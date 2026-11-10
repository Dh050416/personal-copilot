"""工具结果缓存实现。

提供两套实现，接口统一（get / set / clear），由 main.py 按配置选择：
- ToolResultCache（graph.py 内）：进程内内存缓存，单实例场景够用。
- RedisToolResultCache（本模块）：Redis 缓存，跨进程/多 worker 共享。

缓存键复用 graph.py 的 _cache_key（工具名 + 规范化参数的 JSON 串），
值为 (content, artifact) 二元组，JSON 序列化后存 Redis，带 TTL 过期。
"""
from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis


class RedisToolResultCache:
    """Redis 工具结果缓存：接口与 graph.ToolResultCache 对齐（get/set/clear）。

    键：f'copilot:toolcache:{name}:{norm_args}'（含 user 无关信息，只读工具结果本身无租户差异，
    但调用方传入的 key 已含工具名与参数，直接编码即可）。
    """

    _PREFIX = 'copilot:toolcache'

    def __init__(self, client: Redis, ttl: float = 300):
        self._client = client
        self._ttl = int(ttl)

    def _redis_key(self, key: tuple[str, str]) -> str:
        name, norm = key
        return f'{self._PREFIX}:{name}:{norm}'

    async def get(self, key: tuple[str, str]):
        raw = await self._client.get(self._redis_key(key))
        if raw is None:
            return None
        try:
            content, artifact = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return content, artifact

    async def set(self, key: tuple[str, str], content: str, artifact: Any) -> None:
        payload = json.dumps([content, artifact], ensure_ascii=False, default=str)
        if self._ttl > 0:
            await self._client.set(self._redis_key(key), payload, ex=self._ttl)
        else:
            await self._client.set(self._redis_key(key), payload)

    async def clear(self) -> None:
        # 仅清本缓存前缀的键，避免误删其他业务数据。
        keys = [k async for k in self._client.scan_iter(f'{self._PREFIX}:*')]
        if keys:
            await self._client.delete(*keys)
