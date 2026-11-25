from decimal import ROUND_DOWN, Decimal, localcontext
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.data.models import PersonalGoal, PersonalGoalMilestone, PersonalGoalProgressEntry, now_utc
from app.personal_schemas import GoalView, MetricProgress, MilestoneProgress, MilestoneView
from app.services.personal_common import group_by, owned, require_version, touch

MAX_AMOUNT = Decimal('99999999999999.9999')


def progress_snapshot(goal, milestones):
    completed = sum(item.completed_at is not None for item in milestones)
    with localcontext() as ctx:
        ctx.prec = 42
        if goal.progress_mode == 'metric':
            percent = goal.current_value * 100 / goal.target_value
        elif goal.progress_mode == 'milestone':
            percent = Decimal(completed) * 100 / len(milestones)
        else:
            percent = goal.manual_percent
    display = min(Decimal('100'), percent).quantize(Decimal('.01'), rounding=ROUND_DOWN)
    return {
        'title': goal.title,
        'mode': goal.progress_mode,
        'unit': goal.unit,
        'current_value': str(goal.current_value) if goal.current_value is not None else None,
        'target_value': str(goal.target_value) if goal.target_value is not None else None,
        'completed_milestones': completed,
        'total_milestones': len(milestones),
        'raw_percent': str(percent),
        'progress_percent': str(display),
    }


async def milestones_for(session, user_id, goals):
    if not goals:
        return {}
    rows = await session.scalars(
        select(PersonalGoalMilestone)
        .where(PersonalGoalMilestone.user_id == user_id, PersonalGoalMilestone.goal_id.in_([g.id for g in goals]))
        .order_by(PersonalGoalMilestone.position, PersonalGoalMilestone.id),
    )
    return group_by(rows, 'goal_id')


def goal_view(goal, milestones):
    data = {name: getattr(goal, name) for name in GoalView.model_fields if hasattr(goal, name)}
    snapshot = progress_snapshot(goal, milestones)
    return GoalView(
        **data,
        milestones=[MilestoneView.model_validate(item) for item in milestones],
        completed_milestones=snapshot['completed_milestones'],
        progress_percent=Decimal(snapshot['progress_percent']),
    )


async def list_goals(session, user_id, *, archived=False, query=None, limit=100, offset=0):
    filters = [
        PersonalGoal.user_id == user_id,
        PersonalGoal.archived_at.is_not(None) if archived else PersonalGoal.archived_at.is_(None),
    ]
    if query:
        filters.append(PersonalGoal.title.icontains(query, autoescape=True))
    goals = (await session.scalars(
        select(PersonalGoal).where(*filters)
        .order_by(PersonalGoal.updated_at.desc(), PersonalGoal.id.desc()).limit(limit).offset(offset),
    )).all()
    milestones = await milestones_for(session, user_id, goals)
    return [goal_view(goal, milestones.get(goal.id, [])) for goal in goals]


def validate_configuration(mode, unit, target, milestone_titles):
    if mode == 'metric':
        if unit is None or target is None or target <= 0 or milestone_titles is not None:
            raise HTTPException(422, '量化目标需要单位和大于 0 的目标值')
    elif mode == 'milestone':
        if not milestone_titles or unit is not None or target is not None:
            raise HTTPException(422, '里程碑目标至少需要一个阶段')
    elif unit is not None or target is not None or milestone_titles is not None:
        raise HTTPException(422, '手动目标不支持单位、目标值或里程碑')


def set_calculation_fields(goal, mode, unit, target):
    goal.progress_mode = mode
    goal.unit = unit
    goal.target_value = target
    goal.current_value = Decimal(0) if mode == 'metric' else None
    goal.manual_percent = Decimal(0) if mode == 'manual' else None


def add_milestones(session, goal, titles):
    milestones = [
        PersonalGoalMilestone(user_id=goal.user_id, goal_id=goal.id, title=title, position=position)
        for position, title in enumerate(titles or [], start=1)
    ]
    session.add_all(milestones)
    return milestones


def record_progress(session, goal, milestones, kind, *, before=None, note=None, key=None, milestone_id=None):
    # System-generated keys live in their own namespace.
    entry = PersonalGoalProgressEntry(
        user_id=goal.user_id, goal_id=goal.id, milestone_id=milestone_id, entry_type=kind,
        before_data=before, after_data=progress_snapshot(goal, milestones),
        note=note, request_key=key if key is not None else f'system:{uuid4().hex}', occurred_at=now_utc(),
    )
    session.add(entry)
    return entry


async def create_goal(session, user_id, payload):
    validate_configuration(payload.progress_mode, payload.unit, payload.target_value, payload.milestones)
    goal = PersonalGoal(
        user_id=user_id,
        **payload.model_dump(exclude={'milestones', 'progress_mode', 'unit', 'target_value'}),
    )
    set_calculation_fields(goal, payload.progress_mode, payload.unit, payload.target_value)
    session.add(goal)
    await session.flush()
    milestones = add_milestones(session, goal, payload.milestones)
    record_progress(session, goal, milestones, 'initial')
    await session.flush()
    return goal_view(goal, milestones)


async def update_goal(session, user_id, goal_id, payload):
    goal = await owned(session, PersonalGoal, user_id, goal_id, lock=True)
    require_version(goal, payload.version)
    if goal.archived_at is not None:
        raise HTTPException(409, '请先恢复归档目标')
    fields = payload.model_dump(exclude_unset=True, exclude={'version'})
    for name in ('title', 'progress_mode', 'progress_style'):
        if name in fields and fields[name] is None:
            raise HTTPException(422, f'{name} 不能为空')
    milestones = (await milestones_for(session, user_id, [goal])).get(goal.id, [])
    before = progress_snapshot(goal, milestones)
    mode = fields.pop('progress_mode', goal.progress_mode)
    changed_mode = mode != goal.progress_mode
    unit = fields.pop('unit', None if changed_mode else goal.unit)
    target = fields.pop('target_value', None if changed_mode else goal.target_value)
    configure_milestones = 'milestones' in fields
    titles = fields.pop('milestones', [item.title for item in milestones] if milestones and not changed_mode else None)
    validate_configuration(mode, unit, target, titles)
    if changed_mode or unit != goal.unit or configure_milestones:
        recorded = await session.scalar(select(PersonalGoalProgressEntry.id).where(
            PersonalGoalProgressEntry.user_id == user_id,
            PersonalGoalProgressEntry.goal_id == goal_id,
            PersonalGoalProgressEntry.entry_type.in_(('metric', 'manual', 'milestone')),
        ).limit(1))
        if recorded is not None:
            raise HTTPException(409, '已录入进度的目标不能更换计算方式、单位或里程碑清单')
    for name, value in fields.items():
        setattr(goal, name, value)
    if changed_mode:
        set_calculation_fields(goal, mode, unit, target)
    if changed_mode or configure_milestones:
        for item in milestones:
            await session.delete(item)
        milestones = add_milestones(session, goal, titles)
    changed_target = mode == 'metric' and not changed_mode and (target != goal.target_value or unit != goal.unit)
    goal.unit = unit
    goal.target_value = target
    if changed_mode or configure_milestones:
        record_progress(session, goal, milestones, 'mode_change', before=before)
    elif changed_target:
        record_progress(session, goal, milestones, 'target_change', before=before)
    touch(goal)
    await session.flush()
    return goal_view(goal, milestones)


async def record_goal_progress(session, user_id, goal_id, payload):
    if payload.request_key.startswith('system:'):
        raise HTTPException(422, 'request_key 使用了保留前缀')
    goal = await owned(session, PersonalGoal, user_id, goal_id, lock=True)
    milestones = (await milestones_for(session, user_id, [goal])).get(goal.id, [])
    existing = await session.scalar(select(PersonalGoalProgressEntry).where(
        PersonalGoalProgressEntry.user_id == user_id,
        PersonalGoalProgressEntry.goal_id == goal_id,
        PersonalGoalProgressEntry.request_key == payload.request_key,
    ))
    if existing is not None:
        return goal_view(goal, milestones), existing
    require_version(goal, payload.version)
    if goal.archived_at is not None:
        raise HTTPException(409, '请先恢复归档目标')
    if goal.progress_mode != payload.kind:
        raise HTTPException(422, '记录类型与目标计算方式不一致')
    before = progress_snapshot(goal, milestones)
    milestone_id = None
    if isinstance(payload, MetricProgress):
        value = payload.value if payload.operation == 'set' else goal.current_value + payload.value
        if value > MAX_AMOUNT:
            raise HTTPException(422, '累计值超过可记录范围')
        goal.current_value = value
    elif isinstance(payload, MilestoneProgress):
        milestone_id = payload.milestone_id
        item = next((item for item in milestones if item.id == milestone_id), None)
        if item is None:
            raise HTTPException(404, '里程碑不存在')
        if (item.completed_at is not None) != payload.completed:
            item.completed_at = now_utc() if payload.completed else None
    else:
        goal.manual_percent = payload.percent
    entry = record_progress(
        session, goal, milestones, payload.kind, before=before, note=payload.note,
        key=payload.request_key, milestone_id=milestone_id,
    )
    touch(goal)
    await session.flush()
    return goal_view(goal, milestones), entry


async def archive_goal(session, user_id, goal_id, version, *, archive):
    goal = await owned(session, PersonalGoal, user_id, goal_id, lock=True)
    require_version(goal, version)
    if (goal.archived_at is not None) != archive:
        now = now_utc()
        goal.archived_at = now if archive else None
        touch(goal, now)
    milestones = (await milestones_for(session, user_id, [goal])).get(goal.id, [])
    return goal_view(goal, milestones)


async def goal_history(session, user_id, goal_id, *, limit=100, offset=0):
    await owned(session, PersonalGoal, user_id, goal_id)
    return (await session.scalars(select(PersonalGoalProgressEntry).where(
        PersonalGoalProgressEntry.user_id == user_id, PersonalGoalProgressEntry.goal_id == goal_id,
    ).order_by(PersonalGoalProgressEntry.occurred_at.desc(), PersonalGoalProgressEntry.id.desc())
        .limit(limit).offset(offset))).all()
