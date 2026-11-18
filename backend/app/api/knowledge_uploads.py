"""Durable chunk uploads; all mutations serialized per upload across replicas."""
import hashlib
import math
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text

from app.data.models import KnowledgeDocument, KnowledgeUpload, KnowledgeUploadPart, now_utc

router = APIRouter(prefix='/uploads')
PART_SIZE = 4 * 1024 * 1024
MAX_SIZE = 256 * 1024 * 1024


class StartUpload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    title: str = Field(default='', max_length=255)
    size: int = Field(gt=0, le=MAX_SIZE)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


async def owned(session, upload_id, user_id):
    upload = await session.get(KnowledgeUpload, upload_id, with_for_update=True)
    if upload is None or upload.user_id != user_id:
        raise HTTPException(404, '上传任务不存在')
    if upload.expires_at <= now_utc():
        raise HTTPException(410, '上传任务已过期，请重新开始')
    return upload


async def snapshot(session, upload):
    parts = list(await session.scalars(select(KnowledgeUploadPart.part).where(
        KnowledgeUploadPart.upload_id == upload.id)))
    return {'upload_id': upload.id, 'chunk_size': PART_SIZE, 'received': sorted(parts),
            'expires_at': upload.expires_at.isoformat(), 'doc_id': upload.document_id}


@router.post('')
async def start_upload(payload: StartUpload, request: Request):
    if payload.filename.rsplit('.', 1)[-1].lower() not in {'txt', 'text', 'md', 'markdown', 'docx', 'pdf'}:
        raise HTTPException(415, '不支持的文件类型')
    uid = request.state.user_id
    async with request.app.state.db.sessions() as session:
        async with session.begin():
            # Cross-instance quota and init dedup serialization, distinct lock namespace.
            await session.execute(text('SELECT pg_advisory_xact_lock(7312, :uid)'), {'uid': uid})
            doc = await session.scalar(select(KnowledgeDocument).where(
                KnowledgeDocument.user_id == uid, KnowledgeDocument.content_hash == payload.sha256))
            if doc is not None and doc.source_bytes is not None:
                return {'doc_id': doc.id, 'status': doc.status, 'existing': True}
            await session.execute(delete(KnowledgeUpload).where(KnowledgeUpload.expires_at <= now_utc()))
            existing = await session.scalar(select(KnowledgeUpload).where(
                KnowledgeUpload.user_id == uid, KnowledgeUpload.content_hash == payload.sha256,
                KnowledgeUpload.size == payload.size, KnowledgeUpload.document_id.is_(None)))
            if existing:
                return await snapshot(session, existing)
            count = await session.scalar(select(func.count()).select_from(KnowledgeUpload).where(
                KnowledgeUpload.user_id == uid, KnowledgeUpload.document_id.is_(None)))
            if count >= 3:
                raise HTTPException(429, '最多同时保留 3 个上传任务，请完成或取消后重试')
            upload = KnowledgeUpload(id=uuid4().hex, user_id=uid, filename=payload.filename,
                title=payload.title or payload.filename, size=payload.size, content_hash=payload.sha256,
                expires_at=now_utc() + timedelta(hours=24))
            session.add(upload)
            await session.flush()
            return await snapshot(session, upload)


@router.get('/{upload_id}')
async def upload_status(upload_id: str, request: Request):
    async with request.app.state.db.sessions() as session:
        async with session.begin():
            upload = await owned(session, upload_id, request.state.user_id)
            return await snapshot(session, upload)


@router.put('/{upload_id}/parts/{part}')
async def put_part(upload_id: str, part: int, request: Request):
    async with request.app.state.db.sessions() as session:
        async with session.begin():
            upload = await owned(session, upload_id, request.state.user_id)
            if upload.document_id is not None:
                raise HTTPException(409, '上传已经完成')
            # Global cross-replica active receive limit, independent of connection-pool size.
            admitted = False
            for slot in range(8):
                if await session.scalar(text('SELECT pg_try_advisory_xact_lock(7313, :slot)'), {'slot': slot}):
                    admitted = True
                    break
            if not admitted:
                raise HTTPException(429, '上传繁忙，请稍后重试', headers={'Retry-After': '2'})
            if part < 0 or part >= math.ceil(upload.size / PART_SIZE):
                raise HTTPException(400, '分片编号越界')
            expected = min(PART_SIZE, upload.size - part * PART_SIZE)
            content = bytearray()
            import asyncio
            try:
                async with asyncio.timeout(60):
                    async for piece in request.stream():
                        if len(content) + len(piece) > expected:
                            raise HTTPException(413, '分片超过预期大小')
                        content.extend(piece)
            except TimeoutError:
                raise HTTPException(408, '分片上传超时，请重试') from None
            if len(content) != expected:
                raise HTTPException(400, '分片长度不匹配')
            digest = hashlib.sha256(content).hexdigest()
            if digest != request.headers.get('X-Chunk-SHA256'):
                raise HTTPException(400, '分片校验失败')
            existing = await session.get(KnowledgeUploadPart, (upload_id, part))
            if existing is not None:
                if existing.content_hash != digest:
                    raise HTTPException(409, '同一分片内容冲突')
            else:
                session.add(KnowledgeUploadPart(upload_id=upload_id, part=part,
                    content=bytes(content), content_hash=digest))
            return {'part': part, 'sha256': digest}


@router.post('/{upload_id}/complete')
async def complete_upload(upload_id: str, request: Request):
    async with request.app.state.db.sessions() as session:
        async with session.begin():
            upload = await owned(session, upload_id, request.state.user_id)
            if upload.document_id is not None:
                doc = await session.get(KnowledgeDocument, upload.document_id)
                if doc is None:
                    raise HTTPException(410, '文档已删除')
                return {'doc_id': doc.id, 'status': doc.status, 'existing': True}
            # Serialize content-level finalization with init and other upload sessions.
            await session.execute(text('SELECT pg_advisory_xact_lock(7312, :uid)'),
                                  {'uid': upload.user_id})
            admitted = False
            for slot in range(2):
                if await session.scalar(text('SELECT pg_try_advisory_xact_lock(7314, :slot)'), {'slot': slot}):
                    admitted = True
                    break
            if not admitted:
                raise HTTPException(429, '文件合并繁忙，请稍后再次提交', headers={'Retry-After': '2'})
            rows = list(await session.scalars(select(KnowledgeUploadPart).where(
                KnowledgeUploadPart.upload_id == upload_id).order_by(KnowledgeUploadPart.part)))
            if [row.part for row in rows] != list(range(math.ceil(upload.size / PART_SIZE))):
                raise HTTPException(409, '存在缺失分片，请继续上传')
            source = b''.join(row.content for row in rows)
            if len(source) != upload.size or hashlib.sha256(source).hexdigest() != upload.content_hash:
                raise HTTPException(400, '完整文件校验失败，请取消任务并重新上传')
            doc = await session.scalar(select(KnowledgeDocument).where(
                KnowledgeDocument.user_id == upload.user_id,
                KnowledgeDocument.content_hash == upload.content_hash).with_for_update())
            existing = doc is not None
            if doc is None:
                doc = KnowledgeDocument(user_id=upload.user_id, title=upload.title, source_type='file',
                    filename=upload.filename, file_size=upload.size, content_hash=upload.content_hash,
                    source_bytes=source, status='pending', chunk_count=0)
                session.add(doc)
                await session.flush()
            elif doc.source_bytes is None:
                doc.source_bytes, doc.filename, doc.status = source, upload.filename, 'pending'
                doc.error_message = None
            upload.document_id = doc.id
            await session.execute(delete(KnowledgeUploadPart).where(KnowledgeUploadPart.upload_id == upload_id))
            return {'doc_id': doc.id, 'status': doc.status, 'existing': existing}


@router.delete('/{upload_id}')
async def cancel_upload(upload_id: str, request: Request):
    async with request.app.state.db.sessions() as session:
        async with session.begin():
            upload = await owned(session, upload_id, request.state.user_id)
            await session.delete(upload)
    return {'ok': True}
