"""Qwen3-VL 向量化 client（OpenAI 兼容 /embeddings 接口）。

默认指向硅基流动，与 chat 模型配置独立，输出 4096 维向量。

批量：一次 /embeddings 请求可传多个文本（input 为列表），按 embedding_batch_size
分批，减少网络往返；响应按 index 对齐回原始顺序。
"""
from __future__ import annotations

import hashlib
import math

from openai import AsyncOpenAI

# 与 knowledge_chunks.embedding 的存储维度一致。
EMBEDDING_DIM = 4096


class EmbeddingClient:
    @property
    def model_name(self) -> str:
        return self._model

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(f'{self._model}\n{self._revision}\n{EMBEDDING_DIM}'.encode()).hexdigest()

    """OpenAI 兼容 embedding 客户端（本地 ollama / 云端通用）。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 batch_size: int = 16, timeout: float = 120, revision: str = 'v1'):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self._model = model
        self._revision = revision
        if batch_size < 1:
            raise ValueError('embedding batch_size 必须大于 0')
        self._batch_size = batch_size

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量向量化，返回与输入等长、同序的向量列表（每个 4096 维）。"""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i:i + self._batch_size]
            resp = await self._client.embeddings.create(model=self._model, input=batch)
            # 按 index 对齐回原始顺序（API 不保证返回顺序）
            data = sorted(resp.data, key=lambda d: d.index)
            if [d.index for d in data] != list(range(len(batch))):
                raise ValueError('embedding 响应索引缺失、重复或越界')
            for item in data:
                vector = item.embedding
                if len(vector) != EMBEDDING_DIM or not all(math.isfinite(v) for v in vector):
                    raise ValueError('embedding 向量维度错误或包含非有限数值')
                if not any(v != 0 for v in vector):
                    raise ValueError('embedding 返回零向量，无法计算余弦距离')
            vectors.extend([list(d.embedding) for d in data])
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """单条查询向量化（第三层检索用）。"""
        return (await self.embed_texts([text]))[0]
