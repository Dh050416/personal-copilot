<script setup lang="ts">
import { computed, ref, watch } from "vue"
import { decimalText, errorText, formatTime, localDateTime, periodLabel } from "../composables/personalFormat"
import { personalApi, type PeriodKind, type Review, type Suggestion, type WeekExcerpt } from "../services/personalClient"

const today = localDateTime().slice(0, 10)
const kind = ref<PeriodKind>("week")
const day = ref(today)
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai"
const period = computed(() => ({ kind: kind.value, day: day.value, timezone }))
const review = ref<Review | null>(null)
const loading = ref(false)
const busy = ref<"save" | "refresh" | "generate" | "adopt" | null>(null)
const error = ref("")
const optionsError = ref("")
const notice = ref("")
const notes = ref("")
const finalText = ref("")
const weeks = ref<WeekExcerpt[]>([])
const selectedWeeks = ref<number[]>([])
const taskIds = ref<number[]>([])
const goalIds = ref<number[]>([])
const weekIds = ref<number[]>([])
const includeNotes = ref(false)
const adopted = ref<Record<string, number>>({})
const snapshot = computed(() => review.value?.source_snapshot)
const hasSources = computed(() => !!(taskIds.value.length || goalIds.value.length || weekIds.value.length || (includeNotes.value && notes.value.trim())))
let requestId = 0

async function load() {
  if (!day.value) return
  const request = ++requestId
  const selection = { ...period.value }
  loading.value = true
  error.value = ""
  optionsError.value = ""
  notice.value = ""
  review.value = null
  try {
    const [data, weekOptions] = await Promise.allSettled([
      personalApi.review(selection),
      selection.kind === "month" ? personalApi.weekOptions(selection) : Promise.resolve([]),
    ])
    if (request !== requestId) return
    if (data.status === "rejected") throw data.reason
    review.value = data.value
    notes.value = data.value.user_notes
    finalText.value = data.value.final_text ?? ""
    weeks.value = weekOptions.status === "fulfilled" ? weekOptions.value : []
    if (weekOptions.status === "rejected") optionsError.value = errorText(weekOptions.reason)
    selectedWeeks.value = data.value.source_snapshot.week_excerpts.map(week => week.id)
    taskIds.value = []
    goalIds.value = []
    weekIds.value = []
    includeNotes.value = false
    adopted.value = {}
  } catch (cause) {
    if (request === requestId) error.value = errorText(cause)
  } finally {
    if (request === requestId) loading.value = false
  }
}

watch([day, kind], load, { immediate: true })

function step(direction: -1 | 1) {
  const current = review.value?.period_start ?? day.value
  const date = new Date(current + "T00:00:00Z")
  if (kind.value === "week") date.setUTCDate(date.getUTCDate() + 7 * direction)
  else date.setUTCMonth(date.getUTCMonth() + direction, 1)
  day.value = date.toISOString().slice(0, 10)
}

async function run(action: NonNullable<typeof busy.value>, request: () => Promise<void>) {
  if (busy.value || !review.value) return
  busy.value = action
  error.value = ""
  notice.value = ""
  try {
    await request()
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    busy.value = null
  }
}

async function persist(status: Review["status"], refresh = false) {
  const current = review.value
  if (!current) throw new Error("请先加载复盘")
  const updated = await personalApi.saveReview(period.value, {
    version: current.version, user_notes: notes.value, final_text: finalText.value.trim() || null,
    ...(kind.value === "month" && !optionsError.value ? { selected_week_ids: selectedWeeks.value } : {}),
    refresh_sources: refresh, status,
  })
  review.value = updated
  const data = updated.source_snapshot
  taskIds.value = taskIds.value.filter(id => data.tasks.some(item => item.id === id))
  goalIds.value = goalIds.value.filter(id => data.goals.some(item => item.id === id))
  weekIds.value = weekIds.value.filter(id => data.week_excerpts.some(item => item.id === id))
  return updated
}

function save() {
  void run("save", async () => {
    await persist("saved")
    notice.value = "复盘已保存"
  })
}

function refreshSnapshot() {
  void run("refresh", async () => {
    await persist(review.value?.status ?? "draft", true)
    notice.value = "本期记录已更新，补记已保留"
  })
}

function generate() {
  if (!hasSources.value) return
  void run("generate", async () => {
    const current = await persist(review.value?.status ?? "draft")
    if (!current.id) throw new Error("未能建立复盘草稿")
    const updated = await personalApi.generateReview(current.id, {
      version: current.version, task_ids: taskIds.value, goal_ids: goalIds.value,
      week_ids: weekIds.value, include_notes: includeNotes.value,
    })
    review.value = updated
    notice.value = "AI 草稿已生成"
  })
}

function adopt(suggestion: Suggestion) {
  const id = review.value?.id
  if (!id) return
  void run("adopt", async () => {
    const task = await personalApi.adopt(id, suggestion.key)
    adopted.value = { ...adopted.value, [suggestion.key]: task.id }
    notice.value = "建议已加入任务收件箱"
  })
}

function sourceName(reference: string) {
  if (reference === "notes") return "我的补记"
  const [type, id] = reference.split(":")
  const data = snapshot.value
  if (type === "task") return data?.tasks.find(item => item.id === Number(id))?.title ?? "任务记录"
  if (type === "goal") return data?.goals.find(item => item.id === Number(id))?.after.title ?? "目标记录"
  const week = data?.week_excerpts.find(item => item.id === Number(id))
  return week ? week.period_start + " 周复盘" : "周复盘记录"
}

function useSummary() {
  const summary = review.value?.ai_draft?.summary
  if (summary) finalText.value = [finalText.value.trim(), summary].filter(Boolean).join("\n\n").slice(0, 10000)
}
</script>

<template>
  <section class="personal-page" aria-labelledby="personal-reviews-heading">
    <header class="personal-toolbar">
      <h2 id="personal-reviews-heading">复盘记录</h2>
      <div class="personal-actions">
        <button class="btn" :disabled="!!busy || !review" @click="refreshSnapshot">更新本期记录</button>
        <button class="btn btn-primary" :disabled="!!busy || !review" @click="save">{{ busy === "save" ? "正在保存…" : "保存复盘" }}</button>
      </div>
    </header>

    <div class="personal-actions periods">
      <button class="btn" :aria-pressed="kind === 'week'" :disabled="!!busy" @click="kind = 'week'">周复盘</button>
      <button class="btn" :aria-pressed="kind === 'month'" :disabled="!!busy" @click="kind = 'month'">月复盘</button>
      <div class="personal-actions">
        <button class="btn" type="button" aria-label="上一期" :disabled="!!busy || loading" @click="step(-1)">‹</button>
        <input :value="day" class="input" type="date" aria-label="选择复盘日期"
          :disabled="!!busy" @change="day = ($event.target as HTMLInputElement).value || today">
        <button class="btn" type="button" aria-label="下一期" :disabled="!!busy || loading" @click="step(1)">›</button>
      </div>
      <span v-if="review" class="personal-muted">{{ periodLabel(review.period_start, review.period_end) }}</span>
    </div>

    <p v-if="notice" class="personal-success" role="status">{{ notice }}</p>
    <div v-if="error" class="personal-error personal-toolbar" role="alert">
      {{ error }} <button v-if="!review" class="btn" @click="load">重试</button>
    </div>
    <p v-if="loading" class="personal-muted" role="status">正在加载复盘…</p>
    <div v-if="review && snapshot" class="review-layout">
      <div class="review-column">
        <section class="card review-card">
          <div class="personal-toolbar">
            <h3>{{ kind === "week" ? "本周记录" : "本月记录" }}</h3>
            <small v-if="review.saved_at" class="personal-muted">上次保存 {{ formatTime(review.saved_at) }}</small>
          </div>
          <dl class="review-counts">
            <div><dt>完成任务</dt><dd>{{ snapshot.completed_tasks }}</dd></div>
            <div><dt>延期任务</dt><dd>{{ snapshot.delayed_tasks }}</dd></div>
            <div><dt>目标有变化</dt><dd>{{ snapshot.goals.length }}</dd></div>
          </dl>
          <p v-if="!snapshot.tasks.length && !snapshot.goals.length" class="personal-muted">本期暂无任务或目标变化</p>
          <ul v-if="snapshot.tasks.length || snapshot.goals.length" class="source-list">
            <li v-for="item in snapshot.tasks" :key="'task-' + item.id">
              <RouterLink :to="{ name: 'personal-tasks', query: { task: item.id } }" class="personal-link">{{ item.title }}</RouterLink>
              <span v-if="item.completed" class="badge badge-green">已完成</span>
              <span v-if="item.delayed" class="badge badge-amber">延期</span>
            </li>
            <li v-for="item in snapshot.goals" :key="'goal-' + item.id">
              <span>{{ item.after.title }}</span>
              <span class="personal-muted">
                <template v-if="item.before">{{ decimalText(item.before.progress_percent) }}% → </template>
                {{ decimalText(item.after.progress_percent) }}%
              </span>
              <span v-if="item.target_changes.length" class="badge badge-gray">调整目标值</span>
            </li>
          </ul>
        </section>

        <section v-if="kind === 'month'" class="card review-card">
          <h3>引用周复盘</h3>
          <p v-if="optionsError" class="personal-error" role="alert">{{ optionsError }}</p>
          <p v-else-if="!weeks.length" class="personal-muted">本月暂无已保存的周复盘</p>
          <label v-for="week in weeks" :key="week.id" class="week-option personal-toggle">
            <input v-model="selectedWeeks" type="checkbox" :value="week.id" :disabled="!!busy || !!optionsError">
            <span>{{ periodLabel(week.period_start, week.period_end) }}<small v-if="week.text">{{ week.text }}</small></span>
          </label>
        </section>

        <section class="card review-card">
          <label class="personal-field"><span class="editor-title">我的补记</span>
            <textarea v-model="notes" class="textarea" maxlength="10000" placeholder="这段时间还发生了什么？" :disabled="!!busy"></textarea>
          </label>
          <label class="personal-field"><span class="editor-title">复盘正文</span>
            <textarea v-model="finalText" class="textarea" maxlength="10000" placeholder="写下总结与下一步" :disabled="!!busy"></textarea>
          </label>
        </section>
      </div>

      <div class="review-column">
        <section class="card review-card">
          <div class="personal-toolbar">
            <h3>AI 复盘草稿</h3>
            <button class="btn" :disabled="!!busy || !hasSources" @click="generate">
              {{ busy === "generate" ? "正在生成…" : review.ai_draft ? "重新生成" : "生成草稿" }}
            </button>
          </div>
          <details class="source-chooser" open>
            <summary>选择用于生成的记录</summary>
            <label v-for="item in snapshot.tasks" :key="'task-' + item.id" class="personal-toggle">
              <input v-model="taskIds" type="checkbox" :value="item.id" :disabled="!!busy || (taskIds.length >= 50 && !taskIds.includes(item.id))">
              <span>{{ item.title }}</span>
            </label>
            <label v-for="item in snapshot.goals" :key="'goal-' + item.id" class="personal-toggle">
              <input v-model="goalIds" type="checkbox" :value="item.id" :disabled="!!busy || (goalIds.length >= 30 && !goalIds.includes(item.id))">
              <span>{{ item.after.title }}</span>
            </label>
            <label v-for="week in snapshot.week_excerpts.filter(item => selectedWeeks.includes(item.id))" :key="'week-' + week.id" class="personal-toggle">
              <input v-model="weekIds" type="checkbox" :value="week.id" :disabled="!!busy">
              <span>{{ week.period_start }} 周复盘</span>
            </label>
            <label class="personal-toggle">
              <input v-model="includeNotes" type="checkbox" :disabled="!!busy || !notes.trim()"> <span>包含我的补记</span>
            </label>
          </details>

          <template v-if="review.ai_draft">
            <div class="ai-summary">
              <h4>{{ kind === "week" ? "本周总结" : "本月总结" }}</h4>
              <p>{{ review.ai_draft.summary }}</p>
              <button class="btn" :disabled="!!busy" @click="useSummary">加入复盘正文</button>
            </div>
            <article v-for="suggestion in review.ai_draft.suggestions" :key="suggestion.key" class="ai-suggestion">
              <p>{{ suggestion.text }}</p>
              <small class="personal-muted">依据 {{ suggestion.references.map(sourceName).join("、") }}</small>
              <RouterLink v-if="adopted[suggestion.key]" class="personal-link"
                :to="{ name: 'personal-tasks', query: { task: adopted[suggestion.key] } }">查看已创建的任务</RouterLink>
              <button v-else class="btn" :disabled="!!busy" @click="adopt(suggestion)">加入任务收件箱</button>
            </article>
          </template>
        </section>
      </div>
    </div>
  </section>
</template>

<style scoped>
.periods [aria-pressed="true"] { background: var(--primary); color: white; border-color: var(--primary); }
.review-layout { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); gap: 14px; }
.review-column, .review-card { display: grid; align-content: start; gap: 14px; min-width: 0; }
.review-card { padding: 16px; overflow-wrap: anywhere; }
.review-card h3, .editor-title { font-size: 15px; font-weight: 600; }
.review-card p { margin: 0; }
.review-counts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 0; }
.review-counts div { padding: 8px; background: var(--surface-soft); border-radius: 8px; }
.review-counts dt { font-size: 12px; color: var(--text-2); }
.review-counts dd { margin: 1px 0 0; font-size: 21px; font-weight: 650; }
.source-list { margin: 0; padding: 0; list-style: none; }
.source-list li { border-top: 1px solid var(--border); padding: 7px 0; display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.week-option { align-items: flex-start; }
.week-option small { display: block; color: var(--text-2); max-height: 64px; overflow: auto; white-space: pre-wrap; }
.source-chooser { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; max-height: 235px; overflow: auto; }
.source-chooser summary { cursor: pointer; color: var(--text-2); }
.source-chooser label { display: flex; margin-top: 6px; }
.ai-summary, .ai-suggestion { display: grid; justify-items: start; gap: 6px; border-top: 1px solid var(--border); padding-top: 12px; }
.ai-summary p, .ai-suggestion p { white-space: pre-wrap; }
@media (max-width: 1000px) { .review-layout { grid-template-columns: minmax(0, 1fr); } }
</style>
