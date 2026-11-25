"""Chat tools backed by the same personal-planning services as the REST API."""
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import ROUND_DOWN, Decimal, localcontext
from hashlib import sha256
from typing import Annotated, Literal, TypeVar
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy.dialects.postgresql import insert

from app.data.models import IdempotencyRecord, PersonalGoal
from app.errors import ToolError
from app.personal_schemas import (
    Amount,
    GoalView,
    Input,
    Notes,
    Percent,
    PeriodKind,
    PositiveId,
    ProgressEntryView,
    ProgressResult,
    ProgressUpdate,
    ReviewSave,
    ReviewView,
    TaskCreate,
    TaskUpdate,
    TaskView,
)
from app.services import personal_goals, personal_reviews, personal_tasks
from app.services.personal_common import owned
from app.tools.context import _IDEMPOTENCY_KEY, current_user_id

T = TypeVar('T', bound=BaseModel)
PROGRESS_SCHEMA = TypeAdapter(ProgressUpdate)
PERSONAL_WRITE_ACTIONS = {
    'create_personal_task': 'create_personal_task',
    'update_personal_task': 'update_personal_task',
    'record_goal_progress': 'record_goal_progress',
    'save_review': 'save_personal_review',
}
PERSONAL_TOOL_NAMES = frozenset((
    *PERSONAL_WRITE_ACTIONS.values(), 'find_personal_tasks', 'find_personal_goals', 'get_personal_review_context',
))


class FindPersonalArgs(Input):
    query: Annotated[str, Field(max_length=255)] | None = None
    offset: Annotated[int, Field(ge=0)] = 0


class FindTasksArgs(FindPersonalArgs):
    open_only: bool = True


class PersonalTaskList(BaseModel):
    items: list[TaskView]
    has_more: bool


class PersonalGoalList(BaseModel):
    items: list[GoalView]
    has_more: bool


class UpdatePersonalTaskArgs(TaskUpdate):
    task_id: PositiveId


class RecordPersonalProgressArgs(Input):
    goal_id: PositiveId
    version: Annotated[int, Field(ge=1)]
    kind: Literal['metric', 'manual', 'milestone'] = 'metric'
    operation: Literal['add', 'set'] = 'add'
    value: Amount | None = None
    unit: Annotated[str, Field(max_length=32)] | None = None
    percent: Percent | None = None
    milestone_id: PositiveId | None = None
    completed: bool | None = None
    note: Notes | None = None

    def progress_payload(self, key):
        fields = self.model_dump(exclude_none=True, exclude={'goal_id', 'unit'})
        if self.kind != 'metric':
            fields.pop('operation')
        return PROGRESS_SCHEMA.validate_python({**fields, 'request_key': key})


class PersonalReviewArgs(Input):
    kind: PeriodKind
    day: date = Field(description='要复盘周期内的一天。上周或上月必须换算为该周期内的日期。')
    timezone: Annotated[str, Field(min_length=1, max_length=64)] = personal_reviews.DEFAULT_TIMEZONE


class PersonalReviewContext(BaseModel):
    kind: PeriodKind
    period_start: date
    period_end: date
    timezone: str
    version: int
    completed_tasks: int
    delayed_tasks: int
    tasks: list[dict]
    goals: list[dict]
    truncated: bool


class SavePersonalReviewArgs(PersonalReviewArgs):
    version: Annotated[int, Field(ge=0)]
    final_text: Annotated[str, Field(min_length=1, max_length=10000)]


async def write_personal(
    db, name: str, schema: type[T], operation: Callable[..., Awaitable[tuple[str, T]]],
) -> tuple[str, T]:
    user = current_user_id()
    approval = _IDEMPOTENCY_KEY.get()
    request_key = sha256(approval.encode()).hexdigest() if approval else uuid4().hex
    key = f'personal:{name}:{request_key}'
    try:
        async with db.sessions.begin() as session:
            record = None
            if approval:
                inserted = await session.scalar(
                    insert(IdempotencyRecord).values(user_id=user, key=key, content='', response_json='')
                    .on_conflict_do_nothing(index_elements=['user_id', 'key'])
                    .returning(IdempotencyRecord.key)
                )
                record = await session.get(IdempotencyRecord, (user, key))
                if inserted is None:
                    return record.content, schema.model_validate_json(record.response_json)
            content, result = await operation(session, user, request_key)
            if record is not None:
                record.content = content
                record.response_json = result.model_dump_json()
            return content, result
    except HTTPException as exc:
        codes = {404: 'PERSONAL_NOT_FOUND', 409: 'PERSONAL_CONFLICT', 422: 'PERSONAL_INVALID_ARGUMENTS'}
        raise ToolError(codes.get(exc.status_code, 'PERSONAL_ERROR'), str(exc.detail)) from exc


async def task_view(session, user, task):
    return (await personal_tasks.task_views(session, user, [task]))[0]


def personal_reference(args, messages, *, kind):
    lookup = {'task': 'find_personal_tasks', 'goal': 'find_personal_goals'}[kind]
    for entry in reversed(messages):
        if not isinstance(entry, ToolMessage) or entry.name != lookup or not isinstance(entry.artifact, dict):
            continue
        for item in entry.artifact.get('items', []):
            if (item['id'], item['version']) == (args.get(f'{kind}_id'), args.get('version')):
                return item
    return None


def progress_confirmation(args, messages):
    goal = personal_reference(args, messages, kind='goal')
    if goal is None:
        return None
    try:
        typed = RecordPersonalProgressArgs.model_validate(args)
        typed.progress_payload('preview')
        with localcontext() as ctx:
            ctx.prec = 42
            if typed.kind == goal['progress_mode'] == 'metric' and typed.unit == goal['unit']:
                total = typed.value
                if typed.operation == 'add':
                    total += Decimal(goal['current_value'])
                if total > personal_goals.MAX_AMOUNT:
                    return None
                new_percent = total * 100 / Decimal(goal['target_value'])
            elif typed.kind == goal['progress_mode'] == 'manual':
                new_percent = typed.percent
            elif typed.kind == goal['progress_mode'] == 'milestone':
                item = next(m for m in goal['milestones'] if m['id'] == typed.milestone_id)
                complete = goal['completed_milestones'] - bool(item['completed_at']) + typed.completed
                new_percent = Decimal(complete) * 100 / len(goal['milestones'])
            else:
                return None
            before = Decimal(goal['progress_percent']).normalize()
            after = min(new_percent, Decimal(100)).quantize(Decimal('.01'), rounding=ROUND_DOWN).normalize()
            return f'「{goal["title"]}」进度将从 {before:f}% 更新到 {after:f}%。请核对后确认。'
    except (ArithmeticError, KeyError, StopIteration, TypeError, ValueError):
        return None


def build_personal_tools(db, embedding_client=None):
    @tool('find_personal_tasks', args_schema=FindTasksArgs, response_format='content_and_artifact')
    async def find_personal_tasks(**kwargs):
        """查找个人任务候选。query 传标题中的关键词，完成任务前先查询未完成记录。"""
        args = FindTasksArgs.model_validate(kwargs)
        async with db.sessions() as session:
            items = await personal_tasks.list_tasks(
                session, current_user_id(), query=args.query, open_only=args.open_only,
                limit=16, offset=args.offset,
            )
        result = PersonalTaskList(items=items[:15], has_more=len(items) > 15)
        text = '\n'.join(f'#{row.id} {row.title}｜状态 {row.board_status}｜版本 {row.version}' for row in result.items)
        return text or '没有符合条件的个人任务。', result

    @tool('create_personal_task', args_schema=TaskCreate, response_format='content_and_artifact')
    async def create_personal_task(**kwargs):
        """新建个人任务。默认放入收件箱，可附截止及提醒时间，时间须带时区。"""
        args = TaskCreate.model_validate(kwargs)

        async def execute(session, user, _key):
            task = await personal_tasks.create_task(session, user, args)
            result = await task_view(session, user, task)
            return f'已创建个人任务 #{result.id}「{result.title}」。', result

        return await write_personal(db, 'task_create', TaskView, execute)

    @tool('update_personal_task', args_schema=UpdatePersonalTaskArgs, response_format='content_and_artifact')
    async def update_personal_task(**kwargs):
        """更新个人任务的指定字段。先查真实 task_id 与 version。标记完成时 board_status 使用 done。"""
        args = UpdatePersonalTaskArgs.model_validate(kwargs)
        # LangChain 会把省略字段的默认 None 也传给工具。保留实际给出的值，避免清空现有字段。
        changes = TaskUpdate.model_validate(args.model_dump(exclude={'task_id'}, exclude_none=True))

        async def execute(session, user, _key):
            task = await personal_tasks.update_task(session, user, args.task_id, changes)
            result = await task_view(session, user, task)
            label = {'inbox': '收件箱', 'todo': '待做', 'doing': '进行中', 'done': '已完成'}[result.board_status]
            return f'「{result.title}」已更新，当前状态为{label}。', result

        return await write_personal(db, 'task_update', TaskView, execute)

    @tool('find_personal_goals', args_schema=FindPersonalArgs, response_format='content_and_artifact')
    async def find_personal_goals(**kwargs):
        """查找长期目标，返回 ID、当前进度、单位、计算方式、里程碑与版本。"""
        args = FindPersonalArgs.model_validate(kwargs)
        async with db.sessions() as session:
            items = await personal_goals.list_goals(
                session, current_user_id(), query=args.query, limit=16, offset=args.offset,
            )
        result = PersonalGoalList(items=items[:15], has_more=len(items) > 15)
        text = '\n'.join(
            f'#{row.id} {row.title}｜{row.progress_mode}｜{row.progress_percent}%'
            + (f'｜累计 {row.current_value}/{row.target_value} {row.unit}' if row.unit else '')
            + f'｜版本 {row.version}'
            + ('｜阶段 ' + '、'.join(f'#{m.id} {m.title}' for m in row.milestones) if row.milestones else '')
            for row in result.items
        )
        return text or '没有符合条件的长期目标。', result

    @tool('record_goal_progress', args_schema=RecordPersonalProgressArgs, response_format='content_and_artifact')
    async def record_goal_progress(**kwargs):
        """记录长期目标进度。先查询目标。metric 用 value、unit 和 add/set，manual 用 percent，
        milestone 用 milestone_id 和 completed。metric 的 unit 必须与目标单位一致。"""
        args = RecordPersonalProgressArgs.model_validate(kwargs)

        async def execute(session, user, key):
            payload = args.progress_payload(key)
            goal = await owned(session, PersonalGoal, user, args.goal_id, lock=True)
            if args.kind == 'metric' and (not args.unit or args.unit != goal.unit):
                raise HTTPException(422, f'请使用目标单位「{goal.unit or "未设置"}」录入。')
            result, entry = await personal_goals.record_goal_progress(session, user, args.goal_id, payload)
            return f'已记录「{result.title}」的进度，当前为 {result.progress_percent}%。', ProgressResult(
                goal=result, entry=ProgressEntryView.model_validate(entry),
            )

        return await write_personal(db, 'goal_progress', ProgressResult, execute)

    @tool('get_personal_review_context', args_schema=PersonalReviewArgs, response_format='content_and_artifact')
    async def get_personal_review_context(**kwargs):
        """读取周复盘或月复盘的任务和目标事实。返回周期、版本和统计，请基于它生成聊天草稿。"""
        args = PersonalReviewArgs.model_validate(kwargs)
        user = current_user_id()
        try:
            async with db.sessions() as session:
                saved = await personal_reviews.get_review(session, user, args.kind, args.day, args.timezone)
                data = (
                    await personal_reviews.review_snapshot(
                        session, user, saved.period_start, saved.period_end, ZoneInfo(saved.timezone),
                    ) if saved.id is not None else saved.source_snapshot
                )
        except HTTPException as exc:
            raise ToolError('PERSONAL_INVALID_PERIOD', str(exc.detail)) from exc
        result = PersonalReviewContext(
            kind=args.kind, period_start=saved.period_start, period_end=saved.period_end,
            timezone=saved.timezone, version=saved.version, completed_tasks=data['completed_tasks'],
            delayed_tasks=data['delayed_tasks'],
            tasks=[{name: row[name] for name in ('id', 'title', 'completed', 'delayed')} for row in data['tasks'][:30]],
            goals=[{name: row[name] for name in ('id', 'before', 'after')} for row in data['goals'][:20]],
            truncated=len(data['tasks']) > 30 or len(data['goals']) > 20,
        )
        return result.model_dump_json(), result

    @tool('save_personal_review', args_schema=SavePersonalReviewArgs, response_format='content_and_artifact')
    async def save_personal_review(**kwargs):
        """保存明确要求保存的周/月复盘。先读取周期与版本，final_text 为用户认可的正文。"""
        args = SavePersonalReviewArgs.model_validate(kwargs)

        async def execute(session, user, _key):
            prior = await personal_reviews.get_review(session, user, args.kind, args.day, args.timezone)
            result = await personal_reviews.save_review(
                session, user, args.kind, args.day, args.timezone,
                ReviewSave(version=args.version, user_notes=prior.user_notes,
                           final_text=args.final_text, refresh_sources=True),
            )
            return f'已保存 {result.period_start} 开始的{"周" if args.kind == "week" else "月"}复盘。', result

        return await write_personal(db, 'review_save', ReviewView, execute)

    return [
        find_personal_tasks, create_personal_task, update_personal_task, find_personal_goals,
        record_goal_progress, get_personal_review_context, save_personal_review,
    ]
