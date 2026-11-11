"""日程 REST 端点：只读查询 + 日历手动录入的直连写接口。

日历页表单是用户显式填写的确定性输入，参数不需要模型推断，因此写操作直接复用
schedule_service，不再绕 /api/chat 走 Agent + HITL 审批：零模型调用、零延迟、
参数不会被模型改写，也不会占用会话运行锁（避免审批过期把会话锁死）。
HITL 存在的意义是复核「模型从自然语言里猜出来的参数」，手填参数再确认一次
没有信息增益。对话区的自然语言创建日程仍走 Agent 工具 + HITL，那条链路不变。

与 Agent 工具共用同一套 service 与 schema：幂等、用户隔离、时间规范化、
输出契约完全一致，只是少了模型与审批这两层。
"""
import logging
from datetime import date as date_type

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.data.models import ScheduleEvent
from app.data.views import event_view
from app.errors import ToolError
from app.schemas import CreateEventArgs, EventList, EventView, UpdateEventArgs
from app.services.schedule_service import complete_event, create_event, update_event

logger = logging.getLogger(__name__)
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
    filters = [ScheduleEvent.user_id == request.state.user_id]
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


@router.post('', response_model=EventView)
async def create_new_event(payload: CreateEventArgs, request: Request):
    """日历直连新建日程（确定性录入，不走 Agent/审批）。
    与看板 POST /api/cards 同一范式；service 层负责时间规范化与用户隔离。"""
    view = await create_event(request.app.state.db, request.state.user_id, payload)
    # 直连写操作不产生 Langfuse trace，补一条结构化日志留痕（rid 由 RequestIdFilter 自动注入）
    logger.info('event.created', extra={'event': 'event_created', 'event_id': view['id']})
    return view


@router.post('/{event_id}/complete', response_model=EventView)
async def mark_event_done(event_id: int, request: Request):
    """标记日程完成。幂等：重复提交仍返回当前视图，不产生副作用。
    service 层抛 ToolError（工具链路用它做结构化错误），这里转成 REST 的 404。"""
    try:
        view = await complete_event(request.app.state.db, request.state.user_id, event_id)
    except ToolError as exc:
        raise HTTPException(404, exc.message) from exc
    logger.info('event.completed', extra={'event': 'event_completed', 'event_id': event_id})
    return view


@router.patch('/{event_id}', response_model=EventView)
async def edit_event(event_id: int, payload: UpdateEventArgs, request: Request):
    """改期/修改日程。path 与 body 的 event_id 必须一致，避免误改别人的日程；
    service 层还会按 user_id 再隔离一次。"""
    if payload.event_id != event_id:
        raise HTTPException(400, '路径 event_id 与请求体 event_id 不一致')
    try:
        view = await update_event(request.app.state.db, request.state.user_id, payload)
    except ToolError as exc:
        raise HTTPException(404, exc.message) from exc
    logger.info('event.updated', extra={'event': 'event_updated', 'event_id': event_id})
    return view
