"""RAG 知识库文档管理 REST 端点（第二层：在线摄入）。

摄入是「异步」的：上传/粘贴后立即返回 doc_id + status=pending，
切块 + 向量化 + 入库由 BackgroundTasks 在后台执行，前端轮询
GET /api/knowledge/documents/{id} 直到 status 变 ready / failed。

为什么异步：向量化可能很慢（大文档几十块逐批 embedding），同步会撑爆 HTTP 超时。
个人单实例用 FastAPI 自带 BackgroundTasks 即可，不引入 Celery/Redis 队列。
"""
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, UploadFile
from sqlalchemy import select

from app.data.models import KnowledgeDocument
from app.schemas import (
    IngestResponse,
    IngestTextRequest,
    KnowledgeDocumentItem,
    KnowledgeDocumentList,
)
from app.services.knowledge.ingest import (
    DuplicateDocumentError,
    compute_content_hash,
    create_pending_document,
    delete_document,
    ingest_document,
)

router = APIRouter(prefix='/api/knowledge', tags=['knowledge'])

# 单文件大小上限（防止超大文件拖垮摄入与向量化）
MAX_FILE_BYTES = 20 * 1024 * 1024  # 20MB


def _view(d: KnowledgeDocument) -> KnowledgeDocumentItem:
    return KnowledgeDocumentItem(
        id=d.id, title=d.title, source_type=d.source_type, filename=d.filename,
        file_size=d.file_size, status=d.status, error_message=d.error_message,
        chunk_count=d.chunk_count, created_at=d.created_at, updated_at=d.updated_at)


def _schedule_ingest(request: Request, background: BackgroundTasks, doc_id: int,
                     filename: str, file_bytes: bytes):
    """把摄入任务丢给后台：从 app.state 取 db / embedding client / 分块参数。"""
    s = request.app.state.settings
    background.add_task(
        ingest_document, request.app.state.db, doc_id, filename, file_bytes,
        request.app.state.embedding_client, s.chunk_size, s.chunk_overlap)


@router.post('/documents/upload', response_model=IngestResponse, status_code=202)
async def upload_document(file: UploadFile, request: Request, background: BackgroundTasks,
                          title: str | None = None):
    """上传文件摄入（txt / md / docx / pdf）。立即返回 pending，后台异步摄入。"""
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(400, '文件内容为空')
    if len(file_bytes) > MAX_FILE_BYTES:
        raise HTTPException(413, f'文件超过 {MAX_FILE_BYTES // 1024 // 1024}MB 上限')
    filename = file.filename or 'untitled'
    doc_title = (title or '').strip() or filename.rsplit('.', 1)[0]
    content_hash = compute_content_hash(file_bytes)
    try:
        doc = await create_pending_document(
            request.app.state.db, request.state.user_id, doc_title, 'file',
            content_hash, filename=filename, file_size=len(file_bytes))
    except DuplicateDocumentError as exc:
        raise HTTPException(409, str(exc))
    _schedule_ingest(request, background, doc.id, filename, file_bytes)
    return IngestResponse(doc_id=doc.id, status='pending')


@router.post('/documents/text', response_model=IngestResponse, status_code=202)
async def ingest_text(payload: IngestTextRequest, request: Request, background: BackgroundTasks):
    """粘贴文本摄入：直接贴一段文字入库（无需文件）。"""
    file_bytes = payload.text.encode('utf-8')
    content_hash = compute_content_hash(file_bytes)
    try:
        doc = await create_pending_document(
            request.app.state.db, request.state.user_id, payload.title, 'paste',
            content_hash, filename=None, file_size=len(file_bytes))
    except DuplicateDocumentError as exc:
        raise HTTPException(409, str(exc))
    # 粘贴文本统一走 txt 解析器
    _schedule_ingest(request, background, doc.id, 'paste.txt', file_bytes)
    return IngestResponse(doc_id=doc.id, status='pending')


@router.get('/documents', response_model=KnowledgeDocumentList)
async def list_documents(request: Request):
    """文档列表（按创建时间倒序），前端展示 + 轮询状态。"""
    async with request.app.state.db.sessions() as session:
        rows = list(await session.scalars(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.user_id == request.state.user_id)
            .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())))
    return KnowledgeDocumentList(items=[_view(d) for d in rows])


@router.get('/documents/{doc_id}', response_model=KnowledgeDocumentItem)
async def get_document(doc_id: int, request: Request):
    """单个文档详情（前端轮询摄入状态用）。"""
    async with request.app.state.db.sessions() as session:
        doc = await session.get(KnowledgeDocument, doc_id)
    if doc is None or doc.user_id != request.state.user_id:
        raise HTTPException(404, '文档不存在')
    return _view(doc)


@router.delete('/documents/{doc_id}')
async def remove_document(doc_id: int, request: Request):
    """删除文档（块由 ON DELETE CASCADE 级联删除，真删无回收站）。"""
    ok = await delete_document(request.app.state.db, request.state.user_id, doc_id)
    if not ok:
        raise HTTPException(404, '文档不存在')
    return {'ok': True, 'doc_id': doc_id}
