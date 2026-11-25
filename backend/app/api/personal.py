from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import PersonalGoal, PersonalTask
from app.personal_schemas import (
    CheckItemCreate,
    CheckItemUpdate,
    GoalCreate,
    GoalUpdate,
    GoalView,
    PeriodKind,
    ProgressEntryView,
    ProgressResult,
    ProgressUpdate,
    ReminderView,
    RequestKey,
    ReviewGenerate,
    ReviewSave,
    ReviewView,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
    TaskView,
    Versioned,
)
from app.services import personal_goals, personal_reviews, personal_tasks
from app.services.personal_common import owned

router = APIRouter(prefix='/api/personal', tags=['personal'])
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
TimezoneName = Annotated[str, Query(min_length=1, max_length=64)]


async def db_session(request: Request):
    async with request.app.state.db.sessions() as session:
        yield session


def current_user(request: Request) -> int:
    return request.state.user_id


Session = Annotated[AsyncSession, Depends(db_session)]
UserId = Annotated[int, Depends(current_user)]


async def task_response(session, user_id, task, *, save=False):
    view = (await personal_tasks.task_views(session, user_id, [task]))[0]
    if save:
        await session.commit()
    return view


@router.get('/tasks', response_model=list[TaskView])
async def list_tasks(
    session: Session, user_id: UserId, q: Annotated[str | None, Query(max_length=255)] = None,
    status: TaskStatus | None = None, recent_due: bool = False, archived: bool = False,
    goal_id: int | None = None, limit: Limit = 100, offset: Offset = 0,
):
    return await personal_tasks.list_tasks(
        session, user_id, query=q, status=status, recent_due=recent_due, archived=archived,
        goal_id=goal_id, limit=limit, offset=offset,
    )


@router.post('/tasks', response_model=TaskView, status_code=201)
async def create_task(session: Session, user_id: UserId, payload: TaskCreate):
    task = await personal_tasks.create_task(session, user_id, payload)
    return await task_response(session, user_id, task, save=True)


@router.get('/tasks/{task_id}', response_model=TaskView)
async def get_task(session: Session, user_id: UserId, task_id: int):
    task = await owned(session, PersonalTask, user_id, task_id)
    return await task_response(session, user_id, task)


@router.patch('/tasks/{task_id}', response_model=TaskView)
async def update_task(session: Session, user_id: UserId, task_id: int, payload: TaskUpdate):
    task = await personal_tasks.update_task(session, user_id, task_id, payload)
    return await task_response(session, user_id, task, save=True)


@router.post('/tasks/{task_id}/archive', response_model=TaskView)
async def archive_task(session: Session, user_id: UserId, task_id: int, payload: Versioned):
    task = await personal_tasks.archive_task(session, user_id, task_id, payload.version, archive=True)
    return await task_response(session, user_id, task, save=True)


@router.post('/tasks/{task_id}/restore', response_model=TaskView)
async def restore_task(session: Session, user_id: UserId, task_id: int, payload: Versioned):
    task = await personal_tasks.archive_task(session, user_id, task_id, payload.version, archive=False)
    return await task_response(session, user_id, task, save=True)


@router.post('/tasks/{task_id}/check-items', response_model=TaskView, status_code=201)
async def add_check_item(session: Session, user_id: UserId, task_id: int, payload: CheckItemCreate):
    task = await personal_tasks.edit_check_item(session, user_id, task_id, payload)
    return await task_response(session, user_id, task, save=True)


@router.patch('/tasks/{task_id}/check-items/{item_id}', response_model=TaskView)
async def update_check_item(
    session: Session, user_id: UserId, task_id: int, item_id: int, payload: CheckItemUpdate,
):
    task = await personal_tasks.edit_check_item(session, user_id, task_id, payload, item_id=item_id)
    return await task_response(session, user_id, task, save=True)


@router.delete('/tasks/{task_id}/check-items/{item_id}', response_model=TaskView)
async def delete_check_item(
    session: Session, user_id: UserId, task_id: int, item_id: int, version: Annotated[int, Query(ge=1)],
):
    task = await personal_tasks.edit_check_item(
        session, user_id, task_id, Versioned(version=version), item_id=item_id, remove=True,
    )
    return await task_response(session, user_id, task, save=True)


@router.get('/reminders', response_model=list[ReminderView])
async def list_reminders(
    session: Session, user_id: UserId, unread_only: bool = True, limit: Limit = 100, offset: Offset = 0,
):
    reminders = await personal_tasks.list_reminders(session, user_id, unread_only=unread_only, limit=limit, offset=offset)
    await session.commit()
    return reminders


@router.post('/reminders/{reminder_id}/read', status_code=204)
async def mark_reminder_read(session: Session, user_id: UserId, reminder_id: int):
    await personal_tasks.mark_reminder_read(session, user_id, reminder_id)
    await session.commit()
    return Response(status_code=204)


@router.get('/goals', response_model=list[GoalView])
async def list_goals(session: Session, user_id: UserId, archived: bool = False, limit: Limit = 100, offset: Offset = 0):
    return await personal_goals.list_goals(session, user_id, archived=archived, limit=limit, offset=offset)


@router.post('/goals', response_model=GoalView, status_code=201)
async def create_goal(session: Session, user_id: UserId, payload: GoalCreate):
    view = await personal_goals.create_goal(session, user_id, payload)
    await session.commit()
    return view


@router.get('/goals/{goal_id}', response_model=GoalView)
async def get_goal(session: Session, user_id: UserId, goal_id: int):
    goal = await owned(session, PersonalGoal, user_id, goal_id)
    milestones = await personal_goals.milestones_for(session, user_id, [goal])
    return personal_goals.goal_view(goal, milestones.get(goal.id, []))


@router.patch('/goals/{goal_id}', response_model=GoalView)
async def update_goal(session: Session, user_id: UserId, goal_id: int, payload: GoalUpdate):
    view = await personal_goals.update_goal(session, user_id, goal_id, payload)
    await session.commit()
    return view


@router.post('/goals/{goal_id}/archive', response_model=GoalView)
async def archive_goal(session: Session, user_id: UserId, goal_id: int, payload: Versioned):
    view = await personal_goals.archive_goal(session, user_id, goal_id, payload.version, archive=True)
    await session.commit()
    return view


@router.post('/goals/{goal_id}/restore', response_model=GoalView)
async def restore_goal(session: Session, user_id: UserId, goal_id: int, payload: Versioned):
    view = await personal_goals.archive_goal(session, user_id, goal_id, payload.version, archive=False)
    await session.commit()
    return view


@router.post('/goals/{goal_id}/progress', response_model=ProgressResult)
async def record_goal_progress(session: Session, user_id: UserId, goal_id: int, payload: ProgressUpdate):
    goal, entry = await personal_goals.record_goal_progress(session, user_id, goal_id, payload)
    await session.commit()
    return ProgressResult(goal=goal, entry=ProgressEntryView.model_validate(entry))


@router.get('/goals/{goal_id}/progress', response_model=list[ProgressEntryView])
async def goal_progress_history(session: Session, user_id: UserId, goal_id: int, limit: Limit = 100, offset: Offset = 0):
    return await personal_goals.goal_history(session, user_id, goal_id, limit=limit, offset=offset)


@router.get('/reviews', response_model=ReviewView)
async def get_review(
    session: Session, user_id: UserId, kind: PeriodKind = 'week', day: date | None = None,
    timezone: TimezoneName = personal_reviews.DEFAULT_TIMEZONE,
):
    return await personal_reviews.get_review(session, user_id, kind, day, timezone)


@router.put('/reviews', response_model=ReviewView)
async def save_review(
    session: Session, user_id: UserId, payload: ReviewSave, kind: PeriodKind = 'week', day: date | None = None,
    timezone: TimezoneName = personal_reviews.DEFAULT_TIMEZONE,
):
    view = await personal_reviews.save_review(session, user_id, kind, day, timezone, payload)
    await session.commit()
    return view


@router.get('/reviews/week-options')
async def week_options(
    session: Session, user_id: UserId, day: date | None = None,
    timezone: TimezoneName = personal_reviews.DEFAULT_TIMEZONE,
):
    start, end, _ = personal_reviews.review_period('month', day, timezone)
    return await personal_reviews.available_weeks(session, user_id, start, end)


@router.post('/reviews/{review_id}/generate', response_model=ReviewView)
async def generate_review(session: Session, user_id: UserId, request: Request, review_id: int, payload: ReviewGenerate):
    evidence = await personal_reviews.selected_sources(session, user_id, review_id, payload)
    await session.rollback()
    draft = await personal_reviews.generate_draft(request.app.state.personal_review_model, evidence)
    view = await personal_reviews.save_ai_draft(session, user_id, review_id, payload.version, draft)
    await session.commit()
    return view


@router.post('/reviews/{review_id}/suggestions/{key}/adopt', response_model=TaskView)
async def adopt_suggestion(session: Session, user_id: UserId, review_id: int, key: RequestKey):
    task = await personal_reviews.adopt_suggestion(session, user_id, review_id, key)
    return await task_response(session, user_id, task, save=True)
