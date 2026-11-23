"""schedule tool adapters; business persistence lives in services."""

from langchain_core.tools import tool
from sqlalchemy import func, select

from app.data.models import ScheduleEvent
from app.data.views import event_view
from app.schemas import (
    CompleteEventArgs,
    CreateEventArgs,
    EventList,
    EventView,
    ListEventsArgs,
    UpdateEventArgs,
)
from app.services.schedule_service import complete_event as service_complete_event
from app.services.schedule_service import create_event as service_create_event
from app.services.schedule_service import update_event as service_update_event
from app.tools.context import _IDEMPOTENCY_KEY, current_user_id


def build_schedule_tools(db, embedding_client=None):
    @tool("list_events", args_schema=ListEventsArgs, response_format="content_and_artifact")
    async def list_events(**kwargs):
        """查询日程，按日期范围与状态筛选（分页，按开始时间倒序）。

        默认只查未完成（active）；查全部（含已完成）用 status=all，只看已完成用 status=done。
        结果较多时用 offset/limit 翻页。
        """
        args = ListEventsArgs.model_validate(kwargs)
        user_id = current_user_id()
        filters = [ScheduleEvent.user_id == user_id]
        if args.start_date:
            filters.append(ScheduleEvent.start_at >= args.start_date.isoformat())
        if args.end_date:
            filters.append(ScheduleEvent.start_at <= args.end_date.isoformat() + "T23:59:59")
        if args.status != "all":
            filters.append(ScheduleEvent.status == args.status)
        async with db.sessions() as session:
            events = list(
                await session.scalars(
                    select(ScheduleEvent)
                    .where(*filters)
                    .order_by(ScheduleEvent.start_at.desc(), ScheduleEvent.id.desc())
                    .offset(args.offset)
                    .limit(args.limit)
                )
            )
            total = await session.scalar(select(func.count(ScheduleEvent.id)).where(*filters))
        views = [event_view(e) for e in events]
        if not views:
            return "目前没有匹配的日程。", EventList(items=[])
        summary = f"日程（本页 {len(views)} 条）：\n"
        for v in views:
            time_text = v["start_at"] + (f" 至 {v['end_at']}" if v["end_at"] else "")
            loc = f"｜{v['location']}" if v["location"] else ""
            note = f"｜{v['note']}" if v["note"] else ""
            summary += f"\n#{v['id']} {time_text} {v['title']}{loc}{note}"
        if total and args.offset + len(views) < total:
            summary += (
                f"\n还有 {total - args.offset - len(views)} 条未显示，"
                f"如需继续请用 offset={args.offset + len(views)} 查询下一页。"
            )
        return summary, EventList(items=[EventView(**v) for v in views])

    @tool("create_event", args_schema=CreateEventArgs, response_format="content_and_artifact")
    async def create_event(**kwargs):
        """新建日程，必须经用户确认。时间支持 YYYY-MM-DD（全天）或 YYYY-MM-DDTHH:MM（精确到分）。"""
        args = CreateEventArgs.model_validate(kwargs)
        view = await service_create_event(db, current_user_id(), args, _IDEMPOTENCY_KEY.get())
        summary = f"已创建日程 #{view['id']}「{view['title']}」"
        summary += f"；{view['start_at']}"
        if view["end_at"]:
            summary += f" 至 {view['end_at']}"
        if view["location"]:
            summary += f"；地点：{view['location']}"
        summary += "。"
        return summary, EventView(**view)

    @tool("complete_event", args_schema=CompleteEventArgs, response_format="content_and_artifact")
    async def complete_event(**kwargs):
        """标记日程为已完成，必须经用户确认。日程 ID 必须来自查询结果。"""
        args = CompleteEventArgs.model_validate(kwargs)
        view = await service_complete_event(db, current_user_id(), args.event_id)
        return f"已标记日程 #{view['id']}「{view['title']}」为完成。", EventView(**view)

    @tool("update_event", args_schema=UpdateEventArgs, response_format="content_and_artifact")
    async def update_event(**kwargs):
        """修改日程（改期/改标题/改地点/改备注），必须经用户确认。日程 ID 必须来自查询结果。"""
        args = UpdateEventArgs.model_validate(kwargs)
        view = await service_update_event(db, current_user_id(), args)
        summary = f"已更新日程 #{view['id']}「{view['title']}」"
        summary += f"；{view['start_at']}"
        if view["end_at"]:
            summary += f" 至 {view['end_at']}"
        if view["location"]:
            summary += f"；地点：{view['location']}"
        summary += "。"
        return summary, EventView(**view)

    return [list_events, create_event, complete_event, update_event]
