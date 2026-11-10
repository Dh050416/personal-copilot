"""对话摘要服务（递进摘要 / 滑动窗口记忆的持久化层）。

负责「读摘要 + 增量合并 + 写摘要」三件事，接口供 graph.py 在 agent 节点调用。
- get: 读某 thread 的现有摘要。
- update: 把「被挤出窗口的旧消息」增量合并进现有摘要（递进摘要，不重算全量）。

摘要只吸收「普通文本消息」（Human/AI 的纯文本，不含 tool_calls、不含 ToolMessage、
不含 SystemMessage），结构化消息（工具调用/审批/系统事件）不参与摘要——它们涉及
幂等、恢复、HITL 语义，被摘要吞掉会破坏这些机制。

递进摘要的「合并」策略由 summarize_fn 注入：默认用文本拼接（轻量、无额外模型调用），
生产可注入调用独立小模型的实现（LLM 摘要）。这样把「摘要算法」和「存储读写」解耦。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from sqlalchemy import select

from app.data.models import ConversationSummary, now_utc

# summarize_fn 签名：async (existing_summary: str, new_messages: list[str]) -> str
SummarizeFn = Callable[[str, list[str]], Awaitable[str]]


async def _concat_summarize(existing: str, new_messages: list[str]) -> str:
    """默认摘要策略：旧摘要 + 新消息文本拼接（截断到合理长度）。

    不做 LLM 调用，保证递进摘要本身零延迟、零额外成本；适合个人单实例。
    生产可替换为调用小模型生成结构化摘要。
    """
    joined = ' | '.join(m.strip() for m in new_messages if m.strip())
    if existing:
        return f'{existing}\n{joined}'
    return joined


class SummaryManager:
    """对话摘要读写封装：持有数据库 session factory，按 thread_id 隔离。"""

    def __init__(self, db, summarize_fn: SummarizeFn | None = None, max_chars: int = 4000):
        self._db = db
        self._summarize = summarize_fn or _concat_summarize
        self._max_chars = max_chars

    async def get(self, thread_id: str, user_id: int) -> str | None:
        async with self._db.sessions() as session:
            row = await session.get(ConversationSummary, thread_id)
            if row is None or not row.summary_text:
                return None
            return row.summary_text

    async def update(self, thread_id: str, user_id: int, new_messages: list[str]) -> str:
        """增量合并：读旧摘要 → summarize_fn(旧摘要, 新消息) → 写回。返回新摘要。"""
        async with self._db.sessions() as session:
            row = await session.get(ConversationSummary, thread_id)
            existing = row.summary_text if row is not None else ''
            merged = await self._summarize(existing, new_messages)
            if len(merged) > self._max_chars:
                merged = merged[-self._max_chars:]
            if row is None:
                row = ConversationSummary(thread_id=thread_id, user_id=user_id,
                                          summary_text=merged, last_summarized_at=now_utc())
                session.add(row)
            else:
                row.summary_text = merged
                row.last_summarized_at = now_utc()
            await session.commit()
            return merged


def is_summarizable(msg: Any) -> bool:
    """判断一条消息是否可参与摘要：仅普通文本的 Human/AI 消息（无 tool_calls）。"""
    from langchain_core.messages import AIMessage, HumanMessage
    if isinstance(msg, HumanMessage):
        content = msg.content
        return isinstance(content, str) and bool(content.strip())
    if isinstance(msg, AIMessage):
        if getattr(msg, 'tool_calls', None):
            return False
        content = msg.content
        return isinstance(content, str) and bool(content.strip())
    return False
