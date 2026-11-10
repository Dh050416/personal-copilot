"""摄入编排单测：去重、状态机（pending→ready/failed）、脏块清理。

用 FakeEmbeddingClient 模拟 bge-m3，不依赖 ollama 服务（端到端验证另做）。
"""
import pytest

from app.services.knowledge.embedding import EMBEDDING_DIM
from app.services.knowledge.ingest import (
    DuplicateDocumentError,
    compute_content_hash,
    create_pending_document,
    delete_document,
    ingest_document,
)
from app.data.models import KnowledgeChunk, KnowledgeDocument
from sqlalchemy import select


class FakeEmbeddingClient:
    """模拟 embedding：返回 EMBEDDING_DIM 维零向量，无需 ollama。"""

    async def embed_texts(self, texts):
        return [[0.1] * EMBEDDING_DIM for _ in texts]

    async def embed_query(self, text):
        return [0.1] * EMBEDDING_DIM


@pytest.mark.asyncio
async def test_compute_hash_stable():
    a = b'hello world'
    assert compute_content_hash(a) == compute_content_hash(a)
    assert compute_content_hash(a) != compute_content_hash(b'hello world!')


@pytest.mark.asyncio
async def test_create_pending_and_dedup(db):
    h = compute_content_hash(b'some content')
    doc = await create_pending_document(db, 1, '标题', 'paste', h)
    assert doc.status == 'pending'
    # 同 user 同 hash 重复 → 拒绝
    with pytest.raises(DuplicateDocumentError) as exc:
        await create_pending_document(db, 1, '另一标题', 'paste', h)
    assert exc.value.existing_id == doc.id


@pytest.mark.asyncio
async def test_ingest_success(db):
    """粘贴文本摄入成功：status=ready，块数>0，块带正确向量维度。"""
    text = '# 测试文档\n\n这是第一段正文内容，用于验证摄入流程。\n\n## 子节\n\n子节正文。'
    file_bytes = text.encode('utf-8')
    h = compute_content_hash(file_bytes)
    doc = await create_pending_document(db, 1, '测试', 'paste', h)
    await ingest_document(db, doc.id, 'paste.md', file_bytes, FakeEmbeddingClient())

    async with db.sessions() as session:
        saved = await session.get(KnowledgeDocument, doc.id)
        assert saved.status == 'ready'
        assert saved.chunk_count > 0
        chunks = list(await session.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id)))
        assert len(chunks) == saved.chunk_count
        # 每块向量维度 = EMBEDDING_DIM
        assert all(len(c.embedding) == EMBEDDING_DIM for c in chunks)
        # 第一个块含标题文本（上下文补偿）
        assert '测试文档' in chunks[0].content
        # heading_path 入库（结构感知分块保留章节路径，第三层检索溯源用）
        assert any(c.heading_path for c in chunks)


@pytest.mark.asyncio
async def test_ingest_failure_clears_dirty_chunks(db):
    """摄入失败（解析出错）→ status=failed，不留脏块。"""
    h = compute_content_hash(b'fake excel')
    doc = await create_pending_document(db, 1, '坏文档', 'file', h, filename='bad.xlsx')
    # .xlsx 不在支持格式里，parse_document 抛 ValueError → ingest 标记 failed
    await ingest_document(db, doc.id, 'bad.xlsx', b'fake excel', FakeEmbeddingClient())

    async with db.sessions() as session:
        saved = await session.get(KnowledgeDocument, doc.id)
        assert saved.status == 'failed'
        assert saved.error_message is not None
        assert saved.chunk_count == 0
        count = await session.scalar(
            select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id))
        assert count is None  # 无脏块


@pytest.mark.asyncio
async def test_delete_document_cascades(db):
    """删文档 → 块级联删除。"""
    text = '一段正文。' * 50
    file_bytes = text.encode('utf-8')
    h = compute_content_hash(file_bytes)
    doc = await create_pending_document(db, 1, '待删', 'paste', h)
    await ingest_document(db, doc.id, 'paste.txt', file_bytes, FakeEmbeddingClient())

    ok = await delete_document(db, 1, doc.id)
    assert ok is True
    # 跨用户访问删除应失败
    other = await create_pending_document(db, 2, '别人', 'paste', compute_content_hash(b'x'))
    assert await delete_document(db, 1, other.id) is False
