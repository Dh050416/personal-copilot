import { API_BASE, authedFetch } from "./agentClient"

export type TaskStatus = "inbox" | "todo" | "doing" | "done"
export type GoalMode = "metric" | "milestone" | "manual"
export type GoalStyle = "water" | "route" | "plant"
export type PeriodKind = "week" | "month"

export interface CheckItem {
  id: number
  label: string
  checked_at: string | null
}

export interface TaskFields {
  title: string
  goal_id: number | null
  category: string | null
  source: string | null
  note: string | null
  due_at: string | null
  reminder_at: string | null
}

export interface Task extends TaskFields {
  id: number
  board_status: TaskStatus
  completed_at: string | null
  archived_at: string | null
  is_waiting: boolean
  waiting_note: string | null
  follow_up_at: string | null
  origin_review_id: number | null
  overdue: boolean
  check_items: CheckItem[]
  checked_count: number
  check_total: number
  version: number
}

export type TaskCreate = Partial<TaskFields> & { title: string; board_status?: "inbox" | "todo" }
export type TaskUpdate = Partial<TaskFields & Pick<Task, "board_status" | "is_waiting" | "waiting_note" | "follow_up_at">>
  & { version: number }

export interface Reminder {
  id: number
  task_id: number
  task_title: string
  kind: "start" | "follow_up"
  fire_at: string
}

export interface Milestone {
  id: number
  title: string
  completed_at: string | null
}

export interface GoalFields {
  title: string
  progress_mode: GoalMode
  progress_style: GoalStyle
  description: string | null
  success_criteria: string | null
  target_date: string | null
  unit?: string
  target_value?: string
  milestones?: string[]
}

export interface Goal extends Omit<GoalFields, "unit" | "target_value" | "milestones"> {
  id: number
  unit: string | null
  target_value: string | null
  current_value: string | null
  progress_percent: string
  milestones: Milestone[]
  completed_milestones: number
  archived_at: string | null
  version: number
}

export type ProgressChange =
  | { kind: "metric"; operation: "add" | "set"; value: string }
  | { kind: "manual"; percent: string }
  | { kind: "milestone"; milestone_id: number; completed: boolean }

interface GoalSnapshot {
  title: string
  unit: string | null
  current_value: string | null
  target_value: string | null
  progress_percent: string
}

export interface ProgressEntry {
  id: number
  entry_type: string
  before_data: GoalSnapshot | null
  after_data: GoalSnapshot
  note: string | null
  occurred_at: string
}

export interface ReviewTask {
  id: number
  title: string
  completed: boolean
  delayed: boolean
}

export interface ReviewGoal {
  id: number
  before: GoalSnapshot | null
  after: GoalSnapshot
  target_changes: { id: number; before: string; after: string }[]
}

export interface WeekExcerpt {
  id: number
  period_start: string
  period_end: string
  text: string
}

export interface Suggestion {
  key: string
  text: string
  references: string[]
}

export interface Review {
  id: number | null
  period_kind: PeriodKind
  period_start: string
  period_end: string
  timezone: string
  status: "draft" | "saved"
  user_notes: string
  final_text: string | null
  saved_at: string | null
  version: number
  source_snapshot: {
    completed_tasks: number
    delayed_tasks: number
    tasks: ReviewTask[]
    goals: ReviewGoal[]
    week_excerpts: WeekExcerpt[]
  }
  ai_draft: { summary: string; suggestions: Suggestion[] } | null
}

export interface ReviewPeriod {
  kind: PeriodKind
  day: string
  timezone: string
}

interface ReviewWrite {
  version: number
  user_notes: string
  final_text: string | null
  selected_week_ids?: number[]
  refresh_sources?: boolean
  status: Review["status"]
}

interface ReviewSources {
  version: number
  task_ids: number[]
  goal_ids: number[]
  week_ids: number[]
  include_notes: boolean
}

type Query = Record<string, string | number | boolean | undefined>

function withQuery(path: string, query: Query): string {
  const params = new URLSearchParams()
  for (const [name, value] of Object.entries(query)) {
    if (value !== undefined && value !== "") params.set(name, String(value))
  }
  return params.size ? path + "?" + params.toString() : path
}

export class PersonalApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message)
  }
}

async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await authedFetch(API_BASE + "/api/personal" + path, {
    method,
    ...(body === undefined ? {} : {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  })
  if (!response.ok) {
    const info = await response.json().catch(() => ({}))
    const detail = typeof info.detail === "string" ? info.detail : info.detail?.[0]?.msg
    throw new PersonalApiError(response.status, info.message || detail || "请求失败（" + response.status + "）")
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const personalApi = {
  tasks: (query: Query = {}) => request<Task[]>(withQuery("/tasks", query)),
  task: (id: number) => request<Task>("/tasks/" + id),
  createTask: (body: TaskCreate) => request<Task>("/tasks", "POST", body),
  updateTask: (id: number, body: TaskUpdate) => request<Task>("/tasks/" + id, "PATCH", body),
  archiveTask: (id: number, version: number, archive: boolean) =>
    request<Task>("/tasks/" + id + (archive ? "/archive" : "/restore"), "POST", { version }),
  addCheckItem: (id: number, version: number, label: string) =>
    request<Task>("/tasks/" + id + "/check-items", "POST", { version, label }),
  checkItem: (id: number, item: number, version: number, changes: { checked?: boolean; label?: string }) =>
    request<Task>("/tasks/" + id + "/check-items/" + item, "PATCH", { version, ...changes }),
  removeCheckItem: (id: number, item: number, version: number) =>
    request<Task>(withQuery("/tasks/" + id + "/check-items/" + item, { version }), "DELETE"),
  reminders: (offset = 0) => request<Reminder[]>(withQuery("/reminders", { offset, limit: 100 })),
  readReminder: (id: number) => request<void>("/reminders/" + id + "/read", "POST"),
  goals: (query: Query = {}) => request<Goal[]>(withQuery("/goals", query)),
  goal: (id: number) => request<Goal>("/goals/" + id),
  createGoal: (body: GoalFields) => request<Goal>("/goals", "POST", body),
  updateGoal: (id: number, body: Partial<GoalFields> & { version: number }) =>
    request<Goal>("/goals/" + id, "PATCH", body),
  archiveGoal: (id: number, version: number, archive: boolean) =>
    request<Goal>("/goals/" + id + (archive ? "/archive" : "/restore"), "POST", { version }),
  progress: (id: number, body: ProgressChange & { version: number; request_key: string; note: string | null }) =>
    request<{ goal: Goal; entry: ProgressEntry }>("/goals/" + id + "/progress", "POST", body),
  history: (id: number, offset = 0) =>
    request<ProgressEntry[]>(withQuery("/goals/" + id + "/progress", { offset, limit: 30 })),
  review: (period: ReviewPeriod) => request<Review>(withQuery("/reviews", { ...period })),
  saveReview: (period: ReviewPeriod, body: ReviewWrite) =>
    request<Review>(withQuery("/reviews", { ...period }), "PUT", body),
  weekOptions: (period: ReviewPeriod) => request<WeekExcerpt[]>(
    withQuery("/reviews/week-options", { day: period.day, timezone: period.timezone }),
  ),
  generateReview: (id: number, body: ReviewSources) => request<Review>("/reviews/" + id + "/generate", "POST", body),
  adopt: (id: number, key: string) => request<Task>(
    "/reviews/" + id + "/suggestions/" + encodeURIComponent(key) + "/adopt", "POST",
  ),
}
