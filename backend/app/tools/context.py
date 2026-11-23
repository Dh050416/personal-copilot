"""Request-scoped identity and idempotency; shared by every tool domain."""

from contextvars import ContextVar

from app.errors import ToolError

# 写工具幂等键注入通道：run_tools 在写工具分支设置（= approval_id，由 tool_call id 派生、
# checkpoint 稳定），工具内读取并传给服务层做「查重 → 重放/新建」。用 ContextVar 而非参数传递：
# 1) args_schema 均为 extra='forbid'，模型契约不能携带内部字段；
# 2) StructuredTool.arun 会过滤多余 kwargs，额外参数传不进工具函数。
# asyncio 单事件循环下由 Task 隔离，未来读工具并行执行也不互相污染。
_IDEMPOTENCY_KEY: ContextVar[str | None] = ContextVar("idempotency_key", default=None)
# 当前请求用户（多用户数据隔离）：由 chat.py 在发起 run 时经 config 设置，工具层读取
# 后对所有数据读写按 user_id 过滤，确保不同账号之间数据完全隔离。
_USER_ID: ContextVar[int | None] = ContextVar("user_id", default=None)


def current_user_id() -> int:
    """读取当前请求用户 id；未设置（理论上不会发生，鉴权中间件已保证）时抛错。"""
    uid = _USER_ID.get()
    if uid is None:
        raise ToolError("NO_USER", "当前请求缺少用户身份，无法隔离数据", stage="policy", retryable=False)
    return uid
