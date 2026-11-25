from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

TaskStatus = Literal['inbox', 'todo', 'doing', 'done']
GoalMode = Literal['metric', 'milestone', 'manual']
GoalStyle = Literal['water', 'route', 'plant']
PeriodKind = Literal['week', 'month']
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)]
Unit = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
Notes = Annotated[str, StringConstraints(max_length=10000)]
RequestKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Amount = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)]
Percent = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]
PositiveId = Annotated[int, Field(gt=0)]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class View(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Versioned(Input):
    version: Annotated[int, Field(ge=1)]


class TaskCreate(Input):
    title: Title
    board_status: Literal['inbox', 'todo'] = 'inbox'
    goal_id: PositiveId | None = None
    category: ShortText | None = None
    source: Annotated[str, Field(max_length=128)] | None = None
    note: Notes | None = None
    due_at: AwareDatetime | None = None
    reminder_at: AwareDatetime | None = None


class TaskUpdate(Versioned):
    title: Title | None = None
    board_status: TaskStatus | None = None
    goal_id: PositiveId | None = None
    category: ShortText | None = None
    source: Annotated[str, Field(max_length=128)] | None = None
    note: Notes | None = None
    due_at: AwareDatetime | None = None
    is_waiting: bool | None = None
    waiting_note: Notes | None = None
    reminder_at: AwareDatetime | None = None
    follow_up_at: AwareDatetime | None = None


class CheckItemCreate(Versioned):
    label: Title


class CheckItemUpdate(Versioned):
    label: Title | None = None
    checked: bool | None = None


class CheckItemView(View):
    id: int
    label: str
    position: int
    checked_at: datetime | None


class TaskView(View):
    id: int
    title: str
    board_status: TaskStatus
    goal_id: int | None
    category: str | None
    source: str | None
    note: str | None
    due_at: datetime | None
    completed_at: datetime | None
    archived_at: datetime | None
    is_waiting: bool
    waiting_note: str | None
    origin_review_id: int | None
    origin_action_key: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    overdue: bool
    reminder_at: datetime | None
    follow_up_at: datetime | None
    check_items: list[CheckItemView]
    checked_count: int
    check_total: int


class ReminderView(View):
    id: int
    task_id: int
    task_title: str
    kind: Literal['start', 'follow_up']
    fire_at: datetime
    available_at: datetime
    read_at: datetime | None


class GoalCreate(Input):
    title: Title
    progress_mode: GoalMode
    progress_style: GoalStyle = 'water'
    description: Notes | None = None
    success_criteria: Notes | None = None
    target_date: date | None = None
    unit: Unit | None = None
    target_value: Amount | None = None
    milestones: list[Title] | None = Field(default=None, min_length=1, max_length=100)


class GoalUpdate(Versioned):
    title: Title | None = None
    progress_mode: GoalMode | None = None
    progress_style: GoalStyle | None = None
    description: Notes | None = None
    success_criteria: Notes | None = None
    target_date: date | None = None
    unit: Unit | None = None
    target_value: Amount | None = None
    milestones: list[Title] | None = Field(default=None, min_length=1, max_length=100)


class ProgressWrite(Versioned):
    request_key: RequestKey
    note: Notes | None = None


class MetricProgress(ProgressWrite):
    kind: Literal['metric']
    operation: Literal['add', 'set'] = 'add'
    value: Amount


class ManualProgress(ProgressWrite):
    kind: Literal['manual']
    percent: Percent


class MilestoneProgress(ProgressWrite):
    kind: Literal['milestone']
    milestone_id: PositiveId
    completed: bool


ProgressUpdate = Annotated[MetricProgress | ManualProgress | MilestoneProgress, Field(discriminator='kind')]


class MilestoneView(View):
    id: int
    title: str
    position: int
    completed_at: datetime | None


class GoalView(View):
    id: int
    title: str
    progress_mode: GoalMode
    progress_style: GoalStyle
    description: str | None
    success_criteria: str | None
    target_date: date | None
    unit: str | None
    target_value: Decimal | None
    current_value: Decimal | None
    manual_percent: Decimal | None
    progress_percent: Decimal
    milestones: list[MilestoneView]
    completed_milestones: int
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


class ProgressEntryView(View):
    id: int
    goal_id: int
    milestone_id: int | None
    entry_type: str
    before_data: dict | None
    after_data: dict
    note: str | None
    occurred_at: datetime
    request_key: str


class ProgressResult(View):
    goal: GoalView
    entry: ProgressEntryView


class ReviewSave(Input):
    version: Annotated[int, Field(ge=0)]
    user_notes: Notes
    final_text: Notes | None = None
    selected_week_ids: list[PositiveId] | None = Field(default=None, max_length=6)
    refresh_sources: bool = False
    status: Literal['draft', 'saved'] = 'saved'


class ReviewGenerate(Versioned):
    task_ids: list[PositiveId] = Field(default_factory=list, max_length=50)
    goal_ids: list[PositiveId] = Field(default_factory=list, max_length=30)
    week_ids: list[PositiveId] = Field(default_factory=list, max_length=6)
    include_notes: bool = False


class ReviewSuggestion(Input):
    text: Title
    references: list[Annotated[str, StringConstraints(max_length=64)]] = Field(min_length=1, max_length=20)


class ReviewDraft(Input):
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=3000)]
    suggestions: list[ReviewSuggestion] = Field(max_length=2)


class ReviewView(View):
    id: int | None
    period_kind: PeriodKind
    period_start: date
    period_end: date
    timezone: str
    status: Literal['draft', 'saved']
    user_notes: str
    source_snapshot: dict
    ai_draft: dict | None
    final_text: str | None
    saved_at: datetime | None
    version: int
