<script setup lang="ts">
import { computed, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"
import PersonalTaskDialog from "../components/PersonalTaskDialog.vue"
import { errorText, formatTime } from "../composables/personalFormat"
import { personalApi, type Task, type TaskStatus } from "../services/personalClient"
import { usePersonalReminders } from "../stores/personalReminders"

const lanes: { id: TaskStatus; label: string }[] = [
  { id: "inbox", label: "收件箱" }, { id: "todo", label: "待做" },
  { id: "doing", label: "进行中" }, { id: "done", label: "已完成" },
]
const route = useRoute()
const router = useRouter()
const reminders = usePersonalReminders()
const tasks = ref<Task[]>([])
const archived = ref(false)
const recent = ref(false)
const search = ref("")
const loading = ref(false)
const hasMore = ref(false)
const error = ref("")
const detailError = ref("")
const opening = ref(false)
const editing = ref<Task | null>(null)
const creating = ref<"inbox" | "todo" | null>(null)
const dragging = ref<Task | null>(null)
const dropLane = ref<TaskStatus | null>(null)
const moving = ref(false)
const pageSize = 100
let requestId = 0

const grouped = computed(() => Object.fromEntries(
  lanes.map(lane => [lane.id, tasks.value.filter(task => task.board_status === lane.id)]),
) as Record<TaskStatus, Task[]>)

async function load(append = false) {
  const id = ++requestId
  const offset = append ? tasks.value.length : 0
  loading.value = true
  error.value = ""
  try {
    const received = await personalApi.tasks({
      q: search.value.trim(), archived: archived.value, recent_due: recent.value,
      limit: pageSize, offset,
    })
    if (id !== requestId) return
    tasks.value = append ? [...tasks.value, ...received] : received
    hasMore.value = received.length === pageSize
  } catch (cause) {
    if (id === requestId) error.value = errorText(cause)
  } finally {
    if (id === requestId) loading.value = false
  }
}

watch([archived, recent, search], (_, __, onCleanup) => {
  ++requestId
  tasks.value = []
  hasMore.value = false
  loading.value = true
  const timer = window.setTimeout(() => void load(), 180)
  onCleanup(() => window.clearTimeout(timer))
})
void load()

watch(() => route.query.task, async (value, _, onCleanup) => {
  editing.value = null
  opening.value = false
  detailError.value = ""
  if (!value) return
  let active = true
  onCleanup(() => { active = false })
  const id = Number(value)
  if (!Number.isSafeInteger(id) || id < 1) {
    detailError.value = "无效的任务编号"
    return
  }
  opening.value = true
  try {
    const task = await personalApi.task(id)
    if (active) editing.value = task
  } catch (cause) {
    if (active) detailError.value = errorText(cause)
  } finally {
    if (active) opening.value = false
  }
}, { immediate: true })

function close() {
  creating.value = null
  if (route.query.task) {
    const { task: _, ...query } = route.query
    void router.replace({ name: "personal-tasks", query })
  }
}

function changed() {
  void load()
  void reminders.refresh()
}

function startDrag(event: DragEvent, task: Task) {
  if (moving.value) return
  dragging.value = task
  event.dataTransfer?.setData("text/plain", String(task.id))
  if (event.dataTransfer) event.dataTransfer.effectAllowed = "move"
}

function hoverLane(event: DragEvent, status: TaskStatus) {
  if (!dragging.value || dragging.value.board_status === status) return
  if (dragging.value.board_status === "done" && status !== "todo") return
  event.preventDefault()
  dropLane.value = status
}

function stopDrag() {
  dragging.value = null
  dropLane.value = null
}

async function move(status: TaskStatus) {
  const task = dragging.value
  const valid = dropLane.value === status
  stopDrag()
  if (!task || !valid || task.board_status === status || moving.value) return
  moving.value = true
  error.value = ""
  try {
    const updated = await personalApi.updateTask(task.id, { version: task.version, board_status: status })
    tasks.value = tasks.value.map(item => item.id === updated.id ? updated : item)
    changed()
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    moving.value = false
  }
}
</script>

<template>
  <section class="personal-page" aria-labelledby="personal-tasks-heading">
    <header class="personal-toolbar">
      <h2 id="personal-tasks-heading">任务泳道</h2>
      <button class="btn btn-primary" @click="creating = 'inbox'">＋ 新建任务</button>
    </header>

    <div class="task-filters personal-actions">
      <input v-model="search" class="input" type="search" aria-label="搜索任务" placeholder="搜索任务" maxlength="255">
      <button class="btn" :aria-pressed="!archived && !recent" @click="archived = false; recent = false">全部任务</button>
      <button class="btn" :aria-pressed="!archived && recent" @click="archived = false; recent = true">近期到期</button>
      <button class="btn" :aria-pressed="archived" @click="archived = true; recent = false">已归档</button>
    </div>

    <div v-if="error" class="personal-error personal-toolbar" role="alert">
      {{ error }} <button class="btn" @click="load()">重试</button>
    </div>
    <p v-if="detailError" class="personal-error" role="alert">{{ detailError }}</p>
    <p v-if="loading || opening" class="personal-muted" role="status">正在加载…</p>

    <div v-if="archived" class="archived-tasks">
      <RouterLink v-for="task in tasks" :key="task.id" class="card archived-card"
        :to="{ name: 'personal-tasks', query: { task: task.id } }">
        <strong>{{ task.title }}</strong><span>归档于 {{ formatTime(task.archived_at) }}</span>
      </RouterLink>
      <div v-if="!loading && !tasks.length" class="card personal-empty">暂无归档任务</div>
    </div>

    <div v-else class="task-lanes">
      <section v-for="lane in lanes" :key="lane.id" class="lane" :class="{ 'drop-target': dropLane === lane.id }"
        @dragover="hoverLane($event, lane.id)" @drop.prevent="move(lane.id)">
        <header>
          <h3>{{ lane.label }}</h3><span>{{ grouped[lane.id].length }}</span>
          <button v-if="lane.id === 'inbox' || lane.id === 'todo'" type="button" class="lane-add"
            :aria-label="'添加到' + lane.label" @click="creating = lane.id">＋</button>
        </header>
        <div class="lane-items">
          <RouterLink v-for="task in grouped[lane.id]" :key="task.id"
            :to="{ name: 'personal-tasks', query: { task: task.id } }"
            class="task-tile" :class="{ dragging: dragging?.id === task.id }" :draggable="!moving"
            @dragstart="startDrag($event, task)" @dragend="stopDrag">
            <strong>{{ task.title }}</strong>
            <template v-if="lane.id === 'done'">
              <span class="task-time"><span class="done-mark" aria-hidden="true">✓</span> 完成于 {{ formatTime(task.completed_at) }}</span>
            </template>
            <template v-else>
              <span v-if="task.due_at" class="task-time">截止 {{ formatTime(task.due_at) }}</span>
              <span v-if="task.reminder_at" class="task-time">提醒 {{ formatTime(task.reminder_at) }}</span>
              <span v-if="task.follow_up_at" class="task-time">跟进 {{ formatTime(task.follow_up_at) }}</span>
              <span v-if="task.overdue || task.is_waiting || task.category || task.check_total" class="task-badges">
                <span v-if="task.overdue" class="badge badge-red">逾期</span>
                <span v-if="task.is_waiting" class="badge badge-amber">等待回复</span>
                <span v-if="task.category" class="badge badge-gray">{{ task.category }}</span>
                <span v-if="task.check_total" class="badge badge-gray" aria-label="检查项进度">
                  {{ task.checked_count }} / {{ task.check_total }}
                </span>
              </span>
            </template>
          </RouterLink>
          <p v-if="!grouped[lane.id].length && !loading" class="personal-muted">暂无任务</p>
        </div>
      </section>
    </div>

    <button v-if="hasMore" class="btn more" :disabled="loading" @click="load(true)">加载更多任务</button>

    <PersonalTaskDialog v-if="creating || editing" :key="editing?.id ?? creating ?? 'new'"
      :initial="editing" :creation-lane="creating ?? 'inbox'" @close="close" @changed="changed" />
  </section>
</template>

<style scoped>
.task-filters { justify-content: flex-start; }
.task-filters input { flex: 1 1 200px; }
.task-filters [aria-pressed="true"] { background: var(--surface-hover); border-color: var(--border-strong); }
.task-lanes { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.lane {
  background: var(--surface-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  min-height: 360px;
  min-width: 0;
  transition: border-color .15s, background .15s;
}
.lane.drop-target { background: var(--success-weak); border-color: #86b99b; }
.lane header { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--border); }
.lane h3 { font-size: 14px; font-weight: 650; }
.lane header span { color: var(--text-2); font-size: 12px; }
.lane-add { margin-left: auto; padding: 2px 4px; background: none; border: 0; color: var(--text-2); cursor: pointer; }
.lane-items { display: grid; gap: 8px; padding: 10px; }
.lane-items p { text-align: center; }
.task-tile {
  display: grid; gap: 6px;
  padding: 10px;
  text-decoration: none;
  color: var(--text-1);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow-wrap: anywhere;
}
.task-tile:hover { border-color: var(--border-strong); }
.task-tile.dragging { opacity: .45; }
.task-time { color: var(--text-2); font-size: 12px; }
.task-badges { display: flex; gap: 4px; flex-wrap: wrap; }
.task-badges .badge { white-space: normal; }
.done-mark { color: #166534; font-weight: 700; }
.archived-tasks { display: grid; gap: 8px; }
.archived-card { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; color: var(--text-1); text-decoration: none; }
.archived-card span { color: var(--text-2); }
.more { justify-self: start; }
@media (max-width: 1120px) { .task-lanes { grid-template-columns: repeat(2, minmax(0, 1fr)); } .lane { min-height: 220px; } }
@media (max-width: 620px) { .task-lanes { gap: 7px; } .lane-items { padding: 6px; } .task-tile { padding: 7px; } }
</style>
