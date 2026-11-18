"""Database-backed pending ingestion poller; unfinished work survives API restarts."""
import asyncio
import logging

from sqlalchemy import select

from app.data.models import KnowledgeDocument
from app.services.knowledge.ingest import ingest_document

logger = logging.getLogger(__name__)


async def run_ingestion_worker(db, client, chunk_size, chunk_overlap):
    while True:
        try:
            async with db.sessions() as session:
                ids = list(await session.scalars(select(KnowledgeDocument.id).where(
                    KnowledgeDocument.status == 'pending',
                    KnowledgeDocument.source_bytes.is_not(None)
                ).order_by(KnowledgeDocument.id).limit(20)))
            for doc_id in ids:
                async with db.sessions() as session:
                    doc = await session.get(KnowledgeDocument, doc_id)
                    if doc is None or doc.status != 'pending' or doc.source_bytes is None:
                        continue
                    filename, source = doc.filename or 'paste.txt', doc.source_bytes
                await ingest_document(db, doc_id, filename, source, client, chunk_size, chunk_overlap)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('knowledge.worker_failed')
        await asyncio.sleep(2)
