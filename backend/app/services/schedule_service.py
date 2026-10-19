"""日程业务层：创建/完成/改期的持久化与幂等。

幂等：create_event 支持 idempotency_key（approval_id），同键重复调用重放首次结果（invocation_id 唯一约束兜底），
与卡片/批量建卡一致，保证「执行成功但响应超时后重试」不会重复建日程。
"""
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.data.models import ScheduleEvent
from app.data.views import event_view
from app.errors import ToolError
from app.schemas import EventView


async def create_event(db, args, idempotency_key: str | None = None) -> EventView:
    """创建日程并返回对外视图。幂等：invocation_id=key 命中即重放首次结果。"""
    async with db.sessions() as session:
        async with session.begin():
            if idempotency_key is not None:
                existing = await session.scalar(
                    select(ScheduleEvent).where(ScheduleEvent.invocation_id == idempotency_key))
                if existing is not None:
                    return event_view(existing)
            event = ScheduleEvent(
                title=args.title, start_at=args.start_at, end_at=args.end_at,
                location=args.location, note=args.note,
                invocation_id=idempotency_key or uuid4().hex,
            )
            session.add(event)
            try:
                await session.flush()
            except IntegrityError:
                existing = await session.scalar(
                    select(ScheduleEvent).where(ScheduleEvent.invocation_id == idempotency_key))
                if existing is not None:
                    return event_view(existing)
                raise
            return event_view(event)


async def complete_event(db, event_id: int) -> EventView:
    """将日程标记为已完成。幂等：已完成的日程重复操作直接返回当前视图。"""
    async with db.sessions() as session:
        async with session.begin():
            event = await session.get(ScheduleEvent, event_id)
            if event is None:
                raise ToolError('EVENT_NOT_FOUND', '日程不存在，请先查询真实日程 ID',
                                stage='business', retryable=False)
            if event.status != 'done':
                event.status = 'done'
            return event_view(event)


async def update_event(db, args) -> EventView:
    """修改日程（标题/起止时间/地点/备注），至少一个字段。事件不存在抛业务错误。"""
    async with db.sessions() as session:
        async with session.begin():
            event = await session.get(ScheduleEvent, args.event_id)
            if event is None:
                raise ToolError('EVENT_NOT_FOUND', '日程不存在，请先查询真实日程 ID',
                                stage='business', retryable=False)
            if args.title is not None:
                event.title = args.title
            if args.start_at is not None:
                event.start_at = args.start_at
            if args.end_at is not None:
                event.end_at = args.end_at
            if args.location is not None:
                event.location = args.location
            if args.note is not None:
                event.note = args.note
            return event_view(event)
