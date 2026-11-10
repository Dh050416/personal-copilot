"""RAG 检索服务：query 向量化 → pgvector 余弦召回 → 阈值过滤 → 带元数据返回。

第一版策略（与用户确认）：
- 不做 query 重写（多轮指代靠 prompt 重组时带上下文消解）
- 不做重排（个人知识库量级小，向量召回够用）
- 召回 top-k + 相似度阈值过滤（距离 > 阈值视为不相关，丢弃）
- 按 user_id 隔离

pgvector 余弦距离 <=>：0=完全相同，1=正交（不相关），2=相反。
默认阈值 0.5 = 距离 ≤ 0.5 才返回（相当相关）。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.data.models import KnowledgeChunk
from app.services.knowledge.embedding import EmbeddingClient


@dataclass
class RetrievalResult:
    """一条检索命中：文本 + 溯源元数据 + 相似度距离。"""
    chunk_id: int
    content: str
    heading_path: str | None
    page: int | None
    document_id: int
    distance: float

    @property
    def heading_text(self) -> str:
        return self.heading_path or ''


async def retrieve(db, user_id: int, query: str, embed_client: EmbeddingClient,
                   top_k: int = 5, max_distance: float = 0.5) -> list[RetrievalResult]:
    """向量化 query → pgvector 余弦召回 top-k → 阈值过滤 → 返回带元数据结果。

    无相关知识时返回空列表（不返回不相关的块）。
    """
    query_vec = await embed_client.embed_query(query)
    async with db.sessions() as session:
        stmt = (
            select(
                KnowledgeChunk.id,
                KnowledgeChunk.content,
                KnowledgeChunk.heading_path,
                KnowledgeChunk.page,
                KnowledgeChunk.document_id,
                KnowledgeChunk.embedding.cosine_distance(query_vec).label('distance'),
            )
            .where(KnowledgeChunk.user_id == user_id)
            .order_by('distance')
            .limit(top_k)
        )
        rows = (await session.execute(stmt)).all()
    return [
        RetrievalResult(
            chunk_id=r.id, content=r.content, heading_path=r.heading_path,
            page=r.page, document_id=r.document_id, distance=float(r.distance))
        for r in rows if float(r.distance) <= max_distance
    ]


def format_results_for_model(results: list[RetrievalResult]) -> str:
    """把检索结果拼成给模型看的文本（带章节路径溯源）。"""
    if not results:
        return '知识库中没有找到相关内容。'
    parts = [f'知识库检索到 {len(results)} 条相关内容：']
    for i, r in enumerate(results, 1):
        source = f'【{r.heading_text}】' if r.heading_text else ''
        page = f'（第{r.page}页）' if r.page else ''
        parts.append(f'\n[{i}]{source}{page}\n{r.content}')
    return ''.join(parts)
