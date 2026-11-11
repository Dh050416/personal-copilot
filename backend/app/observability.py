"""Langfuse 可观测性接线层。

设计原则：**追踪绝不能影响主流程**。
- 未配置凭证 → 整体降级为空操作，不产生任何网络请求；
- 初始化/上报抛异常 → 捕获并记录 warning，主流程照常运行。

Langfuse Python SDK v3 的用法要点（与 v2 差异很大，别照抄旧教程）：
- 凭证不再传给 CallbackHandler，而是传给 Langfuse 客户端；
- Handler **每次请求新建**：用 trace_context={'trace_id': request_id} 把本次
  请求的全部 span 归到一条 trace。复用模块级单例会让并发请求的 span 串台；
- 注入方式仍是把 handler 放进 RunnableConfig 的 callbacks 列表，
  LangGraph 会自动沿调用链下传给每个节点里的 LLM / 工具调用。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 模块级单例只缓存客户端与开关；handler 必须按请求创建（见 langfuse_callbacks）
_client: Any = None
_enabled = False


def init_langfuse(settings) -> bool:
    """应用启动时调用一次。凭证缺失或初始化失败都返回 False（追踪关闭）。"""
    global _client, _enabled

    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        logger.info('Langfuse 未配置凭证，追踪已关闭')
        _enabled = False
        return False

    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        _enabled = True
        logger.info('Langfuse 追踪已启用 -> %s', settings.langfuse_host)
        return True
    except Exception as exc:  # noqa: BLE001 - 追踪失败必须被吞掉
        logger.warning('Langfuse 初始化失败，追踪已关闭：%s', exc)
        _client = None
        _enabled = False
        return False


def client():
    """返回已初始化的 Langfuse 客户端；未启用时返回 None。

    显式缓存自己创建的实例，而不是用 `langfuse.get_client()` 取全局单例——
    评测脚本要在客户端上跑 experiment，拿到的必须是同一个已鉴权的实例。
    """
    return _client if _enabled else None


def langfuse_callbacks(trace_id: str | None = None) -> list[Any]:
    """返回可直接塞进 RunnableConfig['callbacks'] 的列表；未启用时是空列表。

    trace_id 用后端的 request_id：日志、错误响应、Langfuse trace 三者共用同一个
    字符串，用户报一个 id 就能定位完整链路。同一会话的多轮请求靠 config.metadata
    里的 langfuse_session_id（= thread_id）归组，见 api/chat.py。

    每次调用都新建 handler：v3 的 handler 在构造时固化 trace_context，
    复用单例会把并发请求的所有 span 挂到同一条 trace 上。构造异常一律降级，
    追踪绝不能影响主流程。
    """
    if not (_enabled and _client is not None):
        return []
    try:
        from langfuse.langchain import CallbackHandler

        context = {'trace_id': trace_id} if trace_id else None
        return [CallbackHandler(trace_context=context)]
    except Exception as exc:  # noqa: BLE001 - 追踪失败必须被吞掉
        logger.warning('Langfuse handler 创建失败，本次请求不追踪：%s', exc)
        return []


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
