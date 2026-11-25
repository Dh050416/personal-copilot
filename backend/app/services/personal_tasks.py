from datetime import UTC, timedelta

from fastapi import HTTPException
from sqlalchemy import select, update

from app.data.models import (
    PersonalGoal,
    PersonalTask,
    PersonalTaskCheckItem,
    PersonalTaskEvent,
    PersonalTaskReminder,
    now_utc,
)
from app.personal_schemas import CheckItemView, ReminderView, TaskView
from app.services.personal_common import group_by, owned, require_version, touch


async def task_views(session, user_id, tasks):
    if not tasks:
        return []
    task_ids = [task.id for task in tasks]
    checks = group_by(await session.scalars(
        select(PersonalTaskCheckItem)
        .where(PersonalTaskCheckItem.user_id == user_id, PersonalTaskCheckItem.task_id.in_(task_ids))
        .order_by(PersonalTaskCheckItem.position, PersonalTaskCheckItem.id)
    ), 'task_id')
    reminders = await session.scalars(
        select(PersonalTaskReminder)
        .where(
            PersonalTaskReminder.user_id == user_id,
            PersonalTaskReminder.task_id.in_(task_ids),
            PersonalTaskReminder.state != 'cancelled',
        )
        .distinct(PersonalTaskReminder.task_id, PersonalTaskReminder.kind)
        .order_by(PersonalTaskReminder.task_id, PersonalTaskReminder.kind, PersonalTaskReminder.id.desc())
    )
    times = {(reminder.task_id, reminder.kind): reminder.fire_at for reminder in reminders}
    now = now_utc()
    views = []
    for task in tasks:
        items = checks.get(task.id, [])
        active = task.archived_at is None and task.board_status != 'done'
        data = {name: getattr(task, name) for name in TaskView.model_fields if hasattr(task, name)}
        views.append(TaskView(
            **data,
            overdue=active and task.due_at is not None and task.due_at < now,
            reminder_at=times.get((task.id, 'start')) if active else None,
            follow_up_at=times.get((task.id, 'follow_up')) if task.is_waiting else None,
            check_items=[CheckItemView.model_validate(item) for item in items],
            checked_count=sum(item.checked_at is not None for item in items),
            check_total=len(items),
        ))
    return views


async def list_tasks(session, user_id, *, query=None, status=None, recent_due=False,
                     archived=False, goal_id=None, open_only=False, limit=100, offset=0):
    filters = [
        PersonalTask.user_id == user_id,
        PersonalTask.archived_at.is_not(None) if archived else PersonalTask.archived_at.is_(None),
    ]
    if query:
        filters.append(PersonalTask.title.icontains(query, autoescape=True))
    if status:
        filters.append(PersonalTask.board_status == status)
    elif open_only:
        filters.append(PersonalTask.board_status != 'done')
    if goal_id is not None:
        filters.append(PersonalTask.goal_id == goal_id)
    if recent_due:
        filters.extend((
            PersonalTask.board_status != 'done',
            PersonalTask.due_at <= now_utc() + timedelta(days=7),
        ))
    tasks = (await session.scalars(
        select(PersonalTask).where(*filters).order_by(PersonalTask.updated_at.desc(), PersonalTask.id.desc())
        .limit(limit).offset(offset),
    )).all()
    return await task_views(session, user_id, tasks)


def task_snapshot(task):
    return {
        'title': task.title,
        'board_status': task.board_status,
        'due_at': task.due_at.astimezone(UTC).isoformat() if task.due_at is not None else None,
        'archived': task.archived_at is not None,
    }


def record_event(session, task, kind, at, before=None):
    session.add(PersonalTaskEvent(
        user_id=task.user_id, task_id=task.id, event_type=kind,
        before_data=before, after_data=task_snapshot(task), occurred_at=at,
    ))


async def set_reminder(session, task, kind, fire_at):
    filters = (
        PersonalTaskReminder.user_id == task.user_id,
        PersonalTaskReminder.task_id == task.id,
        PersonalTaskReminder.kind == kind,
        PersonalTaskReminder.state.in_(('scheduled', 'available')),
    )
    if fire_at is not None:
        previous = await session.scalar(
            select(PersonalTaskReminder).where(*filters).order_by(PersonalTaskReminder.id.desc()).limit(1)
        )
        if previous is not None and previous.fire_at == fire_at:
            return
    await session.execute(update(PersonalTaskReminder).where(*filters).values(state='cancelled'))
    if fire_at is not None:
        session.add(PersonalTaskReminder(
            user_id=task.user_id, task_id=task.id, kind=kind, fire_at=fire_at, state='scheduled',
        ))


async def clear_reminders(session, task, kind=None):
    filters = [
        PersonalTaskReminder.user_id == task.user_id,
        PersonalTaskReminder.task_id == task.id,
        PersonalTaskReminder.state != 'cancelled',
    ]
    if kind is not None:
        filters.append(PersonalTaskReminder.kind == kind)
    await session.execute(update(PersonalTaskReminder).where(*filters).values(state='cancelled'))


async def create_task(session, user_id, payload, *, origin_review_id=None, origin_action_key=None):
    if payload.goal_id is not None:
        await owned(session, PersonalGoal, user_id, payload.goal_id)
    fields = payload.model_dump(exclude={'reminder_at'})
    task = PersonalTask(
        user_id=user_id, **fields, is_waiting=False,
        origin_review_id=origin_review_id, origin_action_key=origin_action_key,
    )
    session.add(task)
    await session.flush()
    record_event(session, task, 'created', now_utc())
    if payload.reminder_at is not None:
        await set_reminder(session, task, 'start', payload.reminder_at)
    return task


async def update_task(session, user_id, task_id, payload):
    task = await owned(session, PersonalTask, user_id, task_id, lock=True)
    require_version(task, payload.version)
    if task.archived_at is not None:
        raise HTTPException(409, '请先恢复归档任务')
    fields = payload.model_dump(exclude_unset=True, exclude={'version'})
    for name in ('title', 'board_status', 'is_waiting'):
        if name in fields and fields[name] is None:
            raise HTTPException(422, f'{name} 不能为空')
    if fields.get('goal_id') is not None:
        await owned(session, PersonalGoal, user_id, fields['goal_id'])
    before = task_snapshot(task)
    previous_status = task.board_status
    target = fields.get('board_status', previous_status)
    if previous_status == 'done' and target not in ('done', 'todo'):
        raise HTTPException(422, '已完成任务只能重新打开到待做')
    can_wait = target in ('todo', 'doing')
    waiting = can_wait and fields.get('is_waiting', task.is_waiting)
    follow_up = fields.pop('follow_up_at', None)
    reminder = fields.pop('reminder_at', None)
    if not waiting and (fields.get('is_waiting') or follow_up is not None or fields.get('waiting_note')):
        raise HTTPException(422, '请在待做或进行中的任务上先标记等待，再设置跟进')
    if target == 'done' and reminder is not None:
        raise HTTPException(422, '已完成的任务不能设置提醒')

    for name, value in fields.items():
        setattr(task, name, value)
    task.is_waiting = waiting
    if not waiting:
        task.waiting_note = None
    now = now_utc()
    if target != previous_status:
        task.completed_at = now if target == 'done' else None
        record_event(session, task, 'state_changed', now, before)
    if before['due_at'] != task_snapshot(task)['due_at']:
        record_event(session, task, 'due_changed', now, before)
    if target == 'done':
        await clear_reminders(session, task)
    else:
        if 'reminder_at' in payload.model_fields_set:
            await set_reminder(session, task, 'start', reminder)
        if not waiting:
            await clear_reminders(session, task, 'follow_up')
        elif 'follow_up_at' in payload.model_fields_set:
            await set_reminder(session, task, 'follow_up', follow_up)
    touch(task, now)
    return task


async def archive_task(session, user_id, task_id, version, *, archive):
    task = await owned(session, PersonalTask, user_id, task_id, lock=True)
    require_version(task, version)
    if (task.archived_at is not None) == archive:
        return task
    before = task_snapshot(task)
    now = now_utc()
    task.archived_at = now if archive else None
    if archive:
        task.is_waiting = False
        task.waiting_note = None
        await clear_reminders(session, task)
    record_event(session, task, 'archived' if archive else 'restored', now, before)
    touch(task, now)
    return task


async def edit_check_item(session, user_id, task_id, payload, *, item_id=None, remove=False):
    task = await owned(session, PersonalTask, user_id, task_id, lock=True)
    require_version(task, payload.version)
    if task.archived_at is not None:
        raise HTTPException(409, '请先恢复归档任务')
    now = now_utc()
    if item_id is None:
        last_position = await session.scalar(
            select(PersonalTaskCheckItem.position)
            .where(PersonalTaskCheckItem.user_id == user_id, PersonalTaskCheckItem.task_id == task_id)
            .order_by(PersonalTaskCheckItem.position.desc()).limit(1),
        )
        session.add(PersonalTaskCheckItem(
            user_id=user_id, task_id=task_id, position=(last_position or 0) + 1, label=payload.label,
        ))
    else:
        item = await session.scalar(select(PersonalTaskCheckItem).where(
            PersonalTaskCheckItem.user_id == user_id,
            PersonalTaskCheckItem.task_id == task_id,
            PersonalTaskCheckItem.id == item_id,
        ))
        if item is None:
            raise HTTPException(404, '检查项不存在')
        if remove:
            await session.delete(item)
        else:
            if payload.label is not None:
                item.label = payload.label
            if payload.checked is not None and (item.checked_at is not None) != payload.checked:
                item.checked_at = now if payload.checked else None
    touch(task, now)
    return task


async def list_reminders(session, user_id, *, unread_only=True, limit=100, offset=0):
    now = now_utc()
    await session.execute(
        update(PersonalTaskReminder)
        .where(
            PersonalTaskReminder.user_id == user_id,
            PersonalTaskReminder.state == 'scheduled',
            PersonalTaskReminder.fire_at <= now,
        )
        .values(state='available', available_at=now)
    )
    query = select(PersonalTaskReminder, PersonalTask.title).join(
        PersonalTask, (PersonalTask.id == PersonalTaskReminder.task_id)
        & (PersonalTask.user_id == PersonalTaskReminder.user_id),
    ).where(PersonalTaskReminder.user_id == user_id, PersonalTaskReminder.available_at.is_not(None))
    if unread_only:
        query = query.where(PersonalTaskReminder.state == 'available', PersonalTaskReminder.read_at.is_(None))
    rows = await session.execute(
        query.order_by(PersonalTaskReminder.fire_at.desc(), PersonalTaskReminder.id.desc())
        .limit(limit).offset(offset),
    )
    return [
        ReminderView(
            id=row.id, task_id=row.task_id, task_title=title, kind=row.kind,
            fire_at=row.fire_at, available_at=row.available_at, read_at=row.read_at,
        )
        for row, title in rows
    ]


async def mark_reminder_read(session, user_id, reminder_id):
    reminder = await owned(session, PersonalTaskReminder, user_id, reminder_id, lock=True)
    if reminder.available_at is None:
        raise HTTPException(404, '提醒不存在')
    if reminder.read_at is None:
        reminder.read_at = now_utc()
