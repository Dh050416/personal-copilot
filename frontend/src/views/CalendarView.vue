<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import {
  completeEvent,
  createEvent,
  getEvents,
  updateEvent,
  type CreateEventInput,
  type EventItem,
} from "../services/agentClient"

// ========== 月历状态（只读部分） ==========
const today = new Date()
const year = ref(today.getFullYear())
const month = ref(today.getMonth()) // 0-based
const selected = ref(formatDate(today))
const events = ref<EventItem[]>([])
const loading = ref(false)
const error = ref("")

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]

function formatDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  return `${y}-${m}-${day}`
}

function pad(n: number): string {
  return String(n).padStart(2, "0")
}

function monthStart(y: number, m: number): Date {
  return new Date(y, m, 1)
}

async function loadMonth(): Promise<void> {
  loading.value = true
  error.value = ""
  try {
    const start = formatDate(monthStart(year.value, month.value))
    const last = new Date(year.value, month.value + 1, 0)
    events.value = await getEvents({ start_date: start, end_date: formatDate(last), status: "all" })
    if (!isInCurrentMonth(selected.value)) {
      selected.value = isInCurrentMonth(formatDate(today))
        ? formatDate(today)
        : start
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : "日程加载失败"
  } finally {
    loading.value = false
  }
}

function isInCurrentMonth(dateStr: string): boolean {
  return dateStr.startsWith(`${year.value}-${pad(month.value + 1)}`)
}

function shiftMonth(delta: number): void {
  const d = new Date(year.value, month.value + delta, 1)
  year.value = d.getFullYear()
  month.value = d.getMonth()
}

function goToday(): void {
  year.value = today.getFullYear()
  month.value = today.getMonth()
  selected.value = formatDate(today)
}

const cells = computed(() => {
  const first = monthStart(year.value, month.value)
  const lead = (first.getDay() + 6) % 7
  const daysInMonth = new Date(year.value, month.value + 1, 0).getDate()
  const list: { dateStr: string; day: number; inMonth: boolean }[] = []
  for (let i = 0; i < lead; i++) {
    const d = new Date(year.value, month.value, i - lead + 1)
    list.push({ dateStr: formatDate(d), day: d.getDate(), inMonth: false })
  }
  for (let day = 1; day <= daysInMonth; day++) {
    list.push({ dateStr: `${year.value}-${pad(month.value + 1)}-${pad(day)}`, day, inMonth: true })
  }
  const tail = 7 - (list.length % 7 === 0 ? 7 : list.length % 7)
  for (let i = 1; i <= tail; i++) {
    const d = new Date(year.value, month.value + 1, i)
    list.push({ dateStr: formatDate(d), day: d.getDate(), inMonth: false })
  }
  return list
})

function eventsOf(dateStr: string): EventItem[] {
  return events.value
    .filter((e) => e.start_at.slice(0, 10) === dateStr)
    .sort((a, b) => a.start_at.localeCompare(b.start_at))
}

const selectedEvents = computed(() => eventsOf(selected.value))

function timeText(e: EventItem): string {
  const t = (s: string) => s.slice(11, 16)
  return e.end_at ? `${t(e.start_at)} – ${t(e.end_at)}` : t(e.start_at)
}

function cellClass(c: { dateStr: string; day: number; inMonth: boolean }): string {
  const list: string[] = []
  if (!c.inMonth) list.push("dim")
  if (c.dateStr === formatDate(today)) list.push("today")
  if (c.dateStr === selected.value) list.push("selected")
  const n = eventsOf(c.dateStr).length
  if (n > 0) list.push("has-events")
  return list.join(" ")
}

function hasDone(dateStr: string): boolean {
  return eventsOf(dateStr).some((e) => e.status === "done")
}

// ========== 手动写操作状态（直连 REST，与对话区 Agent/HITL 完全解耦） ==========
const busy = ref(false)
const opNotice = ref("") // 操作完成/失败提示

// 新建表单
const showForm = ref(false)
const form = ref({ title: "", date: formatDate(today), start: "09:00", end: "", location: "", note: "" })
const formError = ref("")

// 改期表单
const resched = ref<{ event: EventItem; datetime: string } | null>(null)

function openForm(): void {
  formError.value = ""
  form.value.date = selected.value
  showForm.value = true
}

function closeForm(): void {
  showForm.value = false
}

function openResched(e: EventItem): void {
  resched.value = { event: e, datetime: e.start_at.slice(0, 16) }
}

function closeResched(): void {
  resched.value = null
}

/** 统一执行一个写操作：串行化、成功后刷新月历、失败给出可读提示。
 * 手填参数是确定性的，直接打 REST；不经过模型，也不需要 HITL 二次确认。 */
async function runWrite(action: () => Promise<unknown>, successText: string): Promise<void> {
  if (busy.value) return
  busy.value = true
  opNotice.value = ""
  try {
    await action()
    opNotice.value = successText
    await loadMonth()
  } catch (e) {
    opNotice.value = `操作失败：${e instanceof Error ? e.message : String(e)}`
  } finally {
    busy.value = false
  }
}

function submitNew(): void {
  formError.value = ""
  const f = form.value
  if (!f.title.trim()) {
    formError.value = "请填写日程标题"
    return
  }
  if (!f.date || !f.start) {
    formError.value = "请填写日期与开始时间"
    return
  }
  if (f.end && f.end < f.start) {
    formError.value = "结束时间不能早于开始时间"
    return
  }
  const input: CreateEventInput = {
    title: f.title.trim(),
    start_at: `${f.date}T${f.start}`,
    ...(f.end ? { end_at: `${f.date}T${f.end}` } : {}),
    ...(f.location.trim() ? { location: f.location.trim() } : {}),
    ...(f.note.trim() ? { note: f.note.trim() } : {}),
  }
  void runWrite(async () => {
    await createEvent(input)
    closeForm() // 失败时保留表单，用户不用重填
  }, "日程已创建")
}

function submitResched(): void {
  const r = resched.value
  if (!r) return
  const dt = r.datetime.trim()
  if (!dt) return
  void runWrite(async () => {
    await updateEvent(r.event.id, { event_id: r.event.id, start_at: dt })
    closeResched()
  }, "日程已改期")
}

function markDone(e: EventItem): void {
  void runWrite(() => completeEvent(e.id), "已标记完成")
}

onMounted(loadMonth)
watch([year, month], loadMonth)
</script>

<template>
  <div class="calendar-view">
    <div class="page-head card">
      <div>
        <h1 class="page-title">日历行程</h1>
        <p class="muted">新建 / 改期 / 完成在本页直接保存，无需模型确认；对话区用自然语言创建日程仍会经人工确认。</p>
      </div>
      <div class="head-actions">
        <button class="btn" :disabled="loading || busy" @click="loadMonth">
          {{ loading ? "加载中…" : "刷新" }}
        </button>
        <button class="btn btn-primary" :disabled="busy" @click="openForm">+ 新建日程</button>
      </div>
    </div>

    <!-- 操作状态条 -->
    <div v-if="busy || opNotice" class="card op-bar">
      <span v-if="busy" class="op-spinner" />
      <span class="op-text">{{ busy ? "正在保存…" : opNotice }}</span>
      <button v-if="!busy && opNotice" class="btn icon-btn" title="关闭" @click="opNotice = ''">✕</button>
    </div>

    <div class="calendar-grid">
      <section class="card cal-card">
        <div class="cal-head">
          <button class="btn icon-btn" title="上个月" @click="shiftMonth(-1)">‹</button>
          <div class="cal-title">{{ year }}年{{ month + 1 }}月</div>
          <button class="btn icon-btn" title="下个月" @click="shiftMonth(1)">›</button>
          <button class="btn" @click="goToday">今天</button>
        </div>
        <div class="week-row">
          <span v-for="w in WEEKDAYS" :key="w" class="week-cell">{{ w }}</span>
        </div>
        <div class="day-grid">
          <button
            v-for="c in cells"
            :key="c.dateStr"
            class="day-cell"
            :class="cellClass(c)"
            :disabled="!c.inMonth"
            @click="selected = c.dateStr"
          >
            <span class="day-num">{{ c.day }}</span>
            <span v-if="c.inMonth && eventsOf(c.dateStr).length" class="dot-stack">
              <span class="dot" :class="hasDone(c.dateStr) ? 'dot-done' : 'dot-active'" />
            </span>
          </button>
        </div>
        <div class="legend">
          <span class="legend-item"><span class="dot dot-active" />未完成</span>
          <span class="legend-item"><span class="dot dot-done" />已完成</span>
          <span class="legend-item"><span class="dot dot-today" />今天</span>
        </div>
      </section>

      <section class="card day-card">
        <h2 class="day-title">{{ selected.replaceAll("-", " / ") }}</h2>
        <p v-if="error" class="error-text">{{ error }}</p>
        <div v-else-if="loading && selectedEvents.length === 0" class="empty">
          加载中…
        </div>
        <div v-else-if="selectedEvents.length === 0" class="empty">
          这一天没有日程安排
        </div>
        <ul v-else class="event-list">
          <li v-for="e in selectedEvents" :key="e.id" class="event-item" :class="{ done: e.status === 'done' }">
            <div class="event-line">
              <span class="event-time">{{ timeText(e) }}</span>
              <span class="event-title">{{ e.title }}</span>
              <span v-if="e.status === 'done'" class="badge badge-done">已完成</span>
              <span v-else class="badge badge-active">未完成</span>
            </div>
            <div v-if="e.location || e.note" class="event-meta">
              <span v-if="e.location">📍 {{ e.location }}</span>
              <span v-if="e.note">{{ e.note }}</span>
            </div>
            <div v-if="e.status === 'active'" class="event-actions">
              <button class="btn btn-mini" :disabled="busy" @click="openResched(e)">改期</button>
              <button class="btn btn-mini btn-ok" :disabled="busy" @click="markDone(e)">完成</button>
            </div>
          </li>
        </ul>
      </section>
    </div>

    <!-- 新建日程表单 -->
    <div v-if="showForm" class="modal-mask" @click.self="closeForm">
      <div class="modal card">
        <h3 class="modal-title">新建日程</h3>
        <label class="field">
          <span class="field-label">标题 *</span>
          <input v-model="form.title" class="input" placeholder="例如：产品评审会议" />
        </label>
        <div class="field-row">
          <label class="field">
            <span class="field-label">日期 *</span>
            <input v-model="form.date" type="date" class="input" />
          </label>
          <label class="field">
            <span class="field-label">开始 *</span>
            <input v-model="form.start" type="time" class="input" />
          </label>
          <label class="field">
            <span class="field-label">结束</span>
            <input v-model="form.end" type="time" class="input" placeholder="可选" />
          </label>
        </div>
        <label class="field">
          <span class="field-label">地点</span>
          <input v-model="form.location" class="input" placeholder="可选" />
        </label>
        <label class="field">
          <span class="field-label">备注</span>
          <input v-model="form.note" class="input" placeholder="可选" />
        </label>
        <p v-if="formError" class="error-text">{{ formError }}</p>
        <div class="modal-actions">
          <button class="btn" :disabled="busy" @click="closeForm">取消</button>
          <button class="btn btn-primary" :disabled="busy" @click="submitNew">
            保存日程
          </button>
        </div>
      </div>
    </div>

    <!-- 改期表单 -->
    <div v-if="resched" class="modal-mask" @click.self="closeResched">
      <div class="modal card">
        <h3 class="modal-title">改期：{{ resched.event.title }}</h3>
        <label class="field">
          <span class="field-label">新的开始时间</span>
          <input v-model="resched.datetime" type="datetime-local" class="input" />
        </label>
        <div class="modal-actions">
          <button class="btn" :disabled="busy" @click="closeResched">取消</button>
          <button class="btn btn-primary" :disabled="busy" @click="submitResched">
            保存改期
          </button>
        </div>
      </div>
    </div>

  </div>
</template>

<style scoped>
.calendar-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 22px;
}
.page-title {
  font-size: 20px;
}
.muted {
  color: var(--text-2);
  font-size: 13px;
  margin: 4px 0 0;
}
.head-actions {
  display: flex;
  gap: 8px;
}
.btn {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-1);
  border-radius: var(--radius-sm);
  padding: 7px 14px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn:hover:not(:disabled) {
  background: var(--surface-hover);
  border-color: var(--border-strong);
}
.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.btn-primary {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
}
.btn-primary:hover:not(:disabled) {
  opacity: 0.88;
}
.btn-mini {
  padding: 3px 10px;
  font-size: 12px;
}
.btn-ok {
  color: var(--success);
  border-color: var(--success);
}
.btn-ok:hover:not(:disabled) {
  background: var(--success-weak);
}
.icon-btn {
  padding: 7px 12px;
  line-height: 1;
}

.op-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  font-size: 13px;
  color: var(--text-2);
}
.op-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--border);
  border-top-color: var(--primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex: none;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.op-text {
  flex: 1;
}

.calendar-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(300px, 1fr);
  gap: 16px;
  align-items: start;
}
@media (max-width: 860px) {
  .calendar-grid {
    grid-template-columns: 1fr;
  }
}

.cal-card {
  padding: 18px 20px;
}
.cal-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}
.cal-title {
  flex: 1;
  font-size: 17px;
  font-weight: 600;
  text-align: center;
}

.week-row {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  margin-bottom: 6px;
}
.week-cell {
  text-align: center;
  font-size: 12px;
  color: var(--text-3);
  padding: 4px 0;
}

.day-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 4px;
}
.day-cell {
  position: relative;
  border: 1px solid transparent;
  background: var(--surface-soft);
  border-radius: var(--radius-sm);
  aspect-ratio: 1.15;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 6px;
  cursor: pointer;
  transition: all 0.12s ease;
}
.day-cell:hover:not(:disabled) {
  border-color: var(--border-strong);
  background: var(--surface-hover);
}
.day-cell.dim {
  opacity: 0.38;
  pointer-events: none;
}
.day-cell.today .day-num {
  color: var(--primary);
  font-weight: 700;
}
.day-cell.selected {
  border-color: var(--primary);
  background: var(--primary-weak);
}
.day-num {
  font-size: 13px;
  color: var(--text-1);
}
.dot-stack {
  position: absolute;
  bottom: 5px;
  left: 0;
  right: 0;
  display: flex;
  justify-content: center;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
}
.dot-active {
  background: var(--primary);
}
.dot-done {
  background: var(--text-3);
}
.dot-today {
  background: var(--primary);
  outline: 1px solid var(--surface);
}
.legend {
  display: flex;
  gap: 16px;
  margin-top: 14px;
  font-size: 12px;
  color: var(--text-2);
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.day-card {
  padding: 18px 20px;
}
.day-title {
  font-size: 15px;
  margin-bottom: 12px;
  color: var(--text-1);
}
.empty {
  color: var(--text-3);
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
}
.error-text {
  color: var(--danger);
  font-size: 13px;
}
.event-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 460px;
  overflow-y: auto;
}
.event-item {
  border: 1px solid var(--border);
  border-left: 3px solid var(--primary);
  border-radius: var(--radius-sm);
  background: var(--surface-soft);
  padding: 10px 12px;
}
.event-item.done {
  border-left-color: var(--text-3);
  opacity: 0.72;
}
.event-line {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.event-time {
  font-size: 12px;
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.event-title {
  font-weight: 600;
  font-size: 14px;
}
.event-meta {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-2);
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.event-actions {
  margin-top: 8px;
  display: flex;
  gap: 8px;
}
.badge {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 999px;
  white-space: nowrap;
}
.badge-active {
  background: var(--primary-weak);
  color: var(--primary);
}
.badge-done {
  background: var(--neutral-weak);
  color: var(--text-2);
}

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
  padding: 20px;
}
.modal {
  width: 100%;
  max-width: 460px;
  padding: 20px 22px;
  max-height: 88vh;
  overflow-y: auto;
}
.modal-title {
  font-size: 16px;
  margin-bottom: 14px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 10px;
  flex: 1;
}
.field-row {
  display: flex;
  gap: 10px;
}
.field-label {
  font-size: 12px;
  color: var(--text-2);
}
.input {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 7px 10px;
  font-size: 13px;
  background: var(--surface);
  color: var(--text-1);
  width: 100%;
}
.input:focus {
  outline: 2px solid var(--primary-border);
  border-color: var(--primary);
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 14px;
}

</style>
