"""bge-m3 向量化 client（OpenAI 兼容 /embeddings 接口）。

默认指向本地 ollama（EMBEDDING_BASE_URL，与 chat 模型配置完全独立）。
bge-m3 输出 1024 维向量，与 knowledge_chunks.embedding 的 Vector(1024) 对应。

批量：一次 /embeddings 请求可传多个文本（input 为列表），按 embedding_batch_size
分批，减少网络往返；响应按 index 对齐回原始顺序。
"""
from __future__ import annotations

from openai import AsyncOpenAI

# bge-m3 的向量维度，建表时已固定为 Vector(1024)
EMBEDDING_DIM = 1024


class EmbeddingClient:
    """OpenAI 兼容 embedding 客户端（本地 ollama / 云端通用）。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 batch_size: int = 16, timeout: float = 120):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self._model = model
        self._batch_size = batch_size

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量向量化，返回与输入等长、同序的向量列表（每个 1024 维）。"""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i:i + self._batch_size]
            resp = await self._client.embeddings.create(model=self._model, input=batch)
            # 按 index 对齐回原始顺序（API 不保证返回顺序）
            data = sorted(resp.data, key=lambda d: d.index)
            vectors.extend([list(d.embedding) for d in data])
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """单条查询向量化（第三层检索用）。"""
        return (await self.embed_texts([text]))[0]
