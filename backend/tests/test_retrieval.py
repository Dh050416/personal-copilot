"""检索服务单测：向量化→pgvector 召回→阈值过滤→带元数据返回。

用可配置的 FakeEmbeddingClient 控制向量方向，验证召回与阈值过滤。
"""
import pytest

from app.data.models import KnowledgeChunk
from app.services.knowledge.embedding import EMBEDDING_DIM
from app.services.knowledge.ingest import compute_content_hash, create_pending_document, ingest_document
from app.services.knowledge.retrieval import RetrievalResult, format_results_for_model, retrieve
from sqlalchemy import select


def _make_vec(first: float, second: float) -> list[float]:
    """构造一个只有前两维非零的向量，便于控制余弦距离。"""
    v = [0.0] * EMBEDDING_DIM
    v[0] = first
    v[1] = second
    return v


class FakeEmbeddingClient:
    """可配置向量方向的 mock embedding client。"""

    def __init__(self, vec: list[float] | None = None):
        self._vec = vec or [0.1] * EMBEDDING_DIM

    async def embed_texts(self, texts):
        return [list(self._vec) for _ in texts]

    async def embed_query(self, text):
        return list(self._vec)


async def _seed_doc(db, text: str, title: str, vec: list[float]) -> int:
    """摄入一篇文档，用指定向量。返回 doc_id。"""
    file_bytes = text.encode('utf-8')
    doc = await create_pending_document(db, 1, title, 'paste', compute_content_hash(file_bytes))
    await ingest_document(db, doc.id, 'paste.md', file_bytes, FakeEmbeddingClient(vec))
    return doc.id


@pytest.mark.asyncio
async def test_retrieve_returns_results_with_metadata(db):
    """摄入文档后检索，返回块带 heading_path/page 元数据。"""
    text = '# 医保政策\n\n门诊报销比例为70%。\n\n## 住院\n\n住院报销比例为85%。'
    await _seed_doc(db, text, '医保', _make_vec(1, 0))

    # query 用同方向向量（距离≈0，命中）
    results = await retrieve(db, 1, '医保报销', FakeEmbeddingClient(_make_vec(1, 0)), top_k=5)
    assert len(results) > 0
    assert all(isinstance(r, RetrievalResult) for r in results)
    # 至少一个块含 heading_path（结构感知分块保留章节路径）
    assert any(r.heading_path for r in results)
    # 内容含关键词
    assert any('报销' in r.content for r in results)


@pytest.mark.asyncio
async def test_retrieve_empty_when_no_documents(db):
    """无文档时检索返回空列表。"""
    results = await retrieve(db, 1, '任意问题', FakeEmbeddingClient(_make_vec(1, 0)), top_k=5)
    assert results == []


@pytest.mark.asyncio
async def test_retrieve_threshold_filters_irrelevant(db):
    """query 向量与文档正交（距离=1 > 阈值0.5），全部被过滤，返回空。"""
    text = '某段文档内容，用于测试阈值过滤。' * 10
    await _seed_doc(db, text, '阈值测试', _make_vec(1, 0))
    # query 用正交向量 → 余弦距离=1 > 0.5 → 全部过滤
    results = await retrieve(db, 1, '完全不相关', FakeEmbeddingClient(_make_vec(0, 1)), top_k=5)
    assert results == []


@pytest.mark.asyncio
async def test_retrieve_respects_top_k(db):
    """top_k 限制返回数量。"""
    # 摄入一篇长文档（多块）
    text = '段落内容。' * 100
    await _seed_doc(db, text, '长文档', _make_vec(1, 0))
    results = await retrieve(db, 1, '段落', FakeEmbeddingClient(_make_vec(1, 0)), top_k=3)
    assert len(results) <= 3


@pytest.mark.asyncio
async def test_retrieve_user_isolation(db):
    """user_id=2 的文档对 user_id=1 不可见。"""
    text = '隔离测试文档内容。' * 5
    file_bytes = text.encode('utf-8')
    # 手动建 user_id=2 的文档+块
    from app.data.models import KnowledgeDocument, KnowledgeChunk, now_utc
    async with db.sessions() as session:
        async with session.begin():
            doc = KnowledgeDocument(user_id=2, title='别人的', source_type='paste',
                                    content_hash=compute_content_hash(file_bytes),
                                    status='ready', chunk_count=1, created_at=now_utc(),
                                    updated_at=now_utc())
            session.add(doc)
            await session.flush()
            session.add(KnowledgeChunk(document_id=doc.id, user_id=2, chunk_index=0,
                                       content=text, token_count=len(text), embedding=_make_vec(1, 0),
                                       created_at=now_utc()))
    results = await retrieve(db, 1, '隔离', FakeEmbeddingClient(_make_vec(1, 0)), top_k=5)
    assert results == []  # user_id=1 看不到 user_id=2 的文档


def test_format_results_empty():
    assert format_results_for_model([]) == '知识库中没有找到相关内容。'


def test_format_results_with_content():
    results = [RetrievalResult(chunk_id=1, content='报销比例70%', heading_path='医保 > 门诊',
                               page=3, document_id=1, distance=0.1)]
    text = format_results_for_model(results)
    assert '1 条' in text
    assert '医保 > 门诊' in text
    assert '第3页' in text
    assert '报销比例70%' in text
