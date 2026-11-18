"""知识库摄入编排：去重 → 解析 → 分块 → 批量向量化 → 入库（状态机）。

流程与事务边界：
- create_pending_document：算 content_hash 去重，命中直接抛 DuplicateDocumentError；
  未命中插入 status=pending 的文档记录并立即返回（HTTP 请求到此结束，后台异步摄入）。
- ingest_document：由 FastAPI BackgroundTasks 调用。耗时操作（解析/分块/向量化）
  在数据库事务外执行，不持有连接；只有「写块 + 更新状态」进事务。
- 状态机：pending → ready（成功）/ failed（失败）。
  失败时删除该文档已写入的块（绝不留下"半个文档"的脏块），并记录 error_message。
"""
from __future__ import annotations

import hashlib
import asyncio
from uuid import uuid4
from contextlib import suppress

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.data.models import KnowledgeChunk, KnowledgeDocument, now_utc
from app.services.knowledge.chunking import chunk_paragraphs
from app.services.knowledge.embedding import EMBEDDING_DIM, EmbeddingClient
from app.services.knowledge.parsers import parse_document


class DuplicateDocumentError(Exception):
    """同一用户重复上传相同内容（content_hash 命中）。"""

    def __init__(self, existing_id: int):
        self.existing_id = existing_id
        super().__init__(f'该文档已上传过（文档 ID：{existing_id}），请勿重复摄入。')


def compute_content_hash(content: bytes) -> str:
    """内容级去重键：SHA-256。同一内容即使文件名不同也会判重（正确行为）。"""
    return hashlib.sha256(content).hexdigest()


async def create_pending_document(db, user_id: int, title: str, source_type: str,
                                  content_hash: str, filename: str | None = None,
                                  file_size: int | None = None,
                                  source_bytes: bytes | None = None) -> KnowledgeDocument:
    """去重检查 + 建 pending 文档记录。返回文档（调用方据此返回 doc_id 给前端）。"""
    async with db.sessions() as session:
        existing = await session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.user_id == user_id,
                KnowledgeDocument.content_hash == content_hash).with_for_update())
        if existing is not None:
            if existing.source_bytes is None and source_bytes is not None:
                existing.source_bytes = source_bytes
                existing.filename = filename
                existing.status = 'pending'
                existing.error_message = None
                await session.commit()
                return existing
            raise DuplicateDocumentError(existing.id)
        doc = KnowledgeDocument(
            user_id=user_id, title=title, source_type=source_type, filename=filename,
            file_size=file_size, content_hash=content_hash, status='pending',
            source_bytes=source_bytes,
            chunk_count=0, created_at=now_utc(), updated_at=now_utc())
        session.add(doc)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            existing = await session.scalar(select(KnowledgeDocument).where(
                KnowledgeDocument.user_id == user_id, KnowledgeDocument.content_hash == content_hash))
            if existing is not None:
                raise DuplicateDocumentError(existing.id) from None
            raise
        await session.refresh(doc)
        return doc


async def ingest_document(db, doc_id: int, filename: str, file_bytes: bytes,
                          embed_client: EmbeddingClient, chunk_size: int = 400,
                          chunk_overlap: int = 50) -> None:
    from app.services.coordination import Coordinator
    lease = await Coordinator(db).acquire(f'knowledge-ingest:{doc_id}')
    if lease is None:
        return
    watcher = None
    owner = uuid4().hex
    try:
        async with db.sessions() as session:
            doc = await session.get(KnowledgeDocument, doc_id, with_for_update=True)
            if doc is None or doc.status == 'ready':
                return
            doc.ingest_owner = owner
            await session.commit()
        execution = asyncio.create_task(_ingest_document(
            db, doc_id, filename, file_bytes, embed_client, chunk_size, chunk_overlap, owner))
        lost = asyncio.Event()

        async def monitor():
            try:
                while True:
                    await asyncio.sleep(1)
                    await lease.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                lost.set()
                execution.cancel()

        watcher = asyncio.create_task(monitor())
        try:
            await execution
        except asyncio.CancelledError:
            if not lost.is_set():
                raise
    finally:
        if watcher is not None:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
        await lease.close()


async def _ingest_document(db, doc_id: int, filename: str, file_bytes: bytes,
                           embed_client: EmbeddingClient, chunk_size: int,
                           chunk_overlap: int, owner: str) -> None:
    """后台摄入主流程：解析 → 分块 → 向量化 → 入库。任何失败都标记 failed 并清理脏块。"""
    try:
        # --- 耗时操作（事务外，不持有数据库连接）---
        paras = await asyncio.to_thread(parse_document, filename, file_bytes)
        chunks = await asyncio.to_thread(chunk_paragraphs, paras, chunk_size, chunk_overlap)
        if not chunks:
            raise ValueError('未能从文档中解析出有效文本内容。')
        vectors = await embed_client.embed_texts([c.content for c in chunks])
        if len(vectors) != len(chunks):
            raise ValueError(f'向量化数量不匹配：{len(vectors)} != {len(chunks)}')
        for v in vectors:
            if len(v) != EMBEDDING_DIM:
                raise ValueError(f'向量维度 {len(v)} 与建表维度 {EMBEDDING_DIM} 不一致，'
                                 f'请确认 EMBEDDING_MODEL_NAME 为 bge-m3。')

        # --- 入库（单事务：清旧块 + 批量插新块 + 置 ready）---
        async with db.sessions() as session:
            async with session.begin():
                doc = await session.get(KnowledgeDocument, doc_id, with_for_update=True)
                if doc is None or doc.ingest_owner != owner or doc.status != 'pending':
                    return
                await session.execute(
                    delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc_id))
                session.add_all([
                    KnowledgeChunk(
                        document_id=doc_id, user_id=doc.user_id, chunk_index=c.chunk_index,
                        content=c.content, token_count=len(c.content),
                        heading_path=' > '.join(c.heading_path) if c.heading_path else None,
                        page=c.page, embedding=v, created_at=now_utc())
                    for c, v in zip(chunks, vectors)
                ])
                doc.status = 'ready'
                doc.chunk_count = len(chunks)
                doc.error_message = None
                doc.embedding_model = embed_client.fingerprint
                doc.pipeline_version = 'structured-v3'
                doc.updated_at = now_utc()
    except Exception as exc:  # noqa: BLE001 —— 摄入失败必须兜底，绝不让后台任务静默崩溃
        await _mark_failed(db, doc_id, str(exc), owner)


async def _mark_failed(db, doc_id: int, error_message: str, owner: str) -> None:
    """失败收尾：删除已写入的块（避免脏数据）+ 标记 failed。"""
    async with db.sessions() as session:
        async with session.begin():
            doc = await session.get(KnowledgeDocument, doc_id, with_for_update=True)
            if doc is None or doc.ingest_owner != owner or doc.status != 'pending':
                return
            await session.execute(
                delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc_id))
            if doc is not None:
                doc.status = 'failed'
                doc.error_message = error_message[:1000]
                doc.chunk_count = 0
                doc.updated_at = now_utc()


async def delete_document(db, user_id: int, doc_id: int) -> bool:
    """删除文档（块由 ON DELETE CASCADE 级联删除）。返回是否删除成功。"""
    async with db.sessions() as session:
        async with session.begin():
            doc = await session.get(KnowledgeDocument, doc_id)
            if doc is None or doc.user_id != user_id:
                return False
            await session.delete(doc)
            return True
