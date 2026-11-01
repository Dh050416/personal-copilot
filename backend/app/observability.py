"""Langfuse 可观测性接线层。

设计原则：**追踪绝不能影响主流程**。
- 未配置凭证 → 整体降级为空操作，不产生任何网络请求；
- 初始化/上报抛异常 → 捕获并记录 warning，主流程照常运行。

Langfuse Python SDK v3 的用法要点（与 v2 差异很大，别照抄旧教程）：
- 凭证不再传给 CallbackHandler，而是传给 Langfuse 客户端；
- CallbackHandler() 无构造参数，从单例客户端取配置；
- 注入方式仍是把 handler 放进 RunnableConfig 的 callbacks 列表，
  LangGraph 会自动沿调用链下传给每个节点里的 LLM / 工具调用。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 模块级单例：Langfuse v3 的客户端本身就是单例，这里只缓存客户端、handler 与开关
_client: Any = None
_handler: Any = None
_enabled = False


def init_langfuse(settings) -> bool:
    """应用启动时调用一次。凭证缺失或初始化失败都返回 False（追踪关闭）。"""
    global _client, _handler, _enabled

    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        logger.info('Langfuse 未配置凭证，追踪已关闭')
        _enabled = False
        return False

    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        _handler = CallbackHandler()
        _enabled = True
        logger.info('Langfuse 追踪已启用 -> %s', settings.langfuse_host)
        return True
    except Exception as exc:  # noqa: BLE001 - 追踪失败必须被吞掉
        logger.warning('Langfuse 初始化失败，追踪已关闭：%s', exc)
        _client = None
        _handler = None
        _enabled = False
        return False


def client():
    """返回已初始化的 Langfuse 客户端；未启用时返回 None。

    显式缓存自己创建的实例，而不是用 `langfuse.get_client()` 取全局单例——
    评测脚本要在客户端上跑 experiment，拿到的必须是同一个已鉴权的实例。
    """
    return _client if _enabled else None


def langfuse_callbacks() -> list[Any]:
    """返回可直接塞进 RunnableConfig['callbacks'] 的列表；未启用时是空列表。"""
    return [_handler] if (_enabled and _handler is not None) else []


def langfuse_enabled() -> bool:
    return _enabled


def flush() -> None:
    """进程退出前把缓冲里的 trace 冲刷出去，避免最后几条丢失。"""
    if not _enabled or _client is None:
        return
    try:
        _client.flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning('Langfuse flush 失败：%s', exc)
