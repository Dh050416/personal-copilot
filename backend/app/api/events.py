"""日程只读 REST 端点：供前端月历页直接拉取 schedule_events（不走 Agent 对话，省 token）。
写操作仍走 Agent 工具 + HITL 审批（/api/chat），此处仅只读查询。"""
from datetime import date as date_type

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.data.models import ScheduleEvent
from app.data.views import event_view
from app.schemas import EventList, EventView

router = APIRouter(prefix='/api/events', tags=['events'])


@router.get('', response_model=EventList)
async def list_events(request: Request,
                      start_date: date_type | None = None,
                      end_date: date_type | None = None,
                      status: str = 'active'):
    """查询日程：start_date/end_date 为日期闭区间（按 start_at 过滤），
    status ∈ active|done|all（默认 active，即未完成）。按开始时间升序。"""
    if status not in ('active', 'done', 'all'):
        raise HTTPException(400, 'status 仅支持 active|done|all')
    filters = []
    if start_date is not None:
        filters.append(ScheduleEvent.start_at >= start_date.isoformat())
    if end_date is not None:
        filters.append(ScheduleEvent.start_at <= end_date.isoformat() + 'T23:59:59')
    if status != 'all':
        filters.append(ScheduleEvent.status == status)
    async with request.app.state.db.sessions() as session:
        events = list(await session.scalars(
            select(ScheduleEvent).where(*filters)
            .order_by(ScheduleEvent.start_at.asc(), ScheduleEvent.id.asc())))
    return EventList(items=[EventView(**event_view(e)) for e in events])
