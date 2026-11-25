<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue"
import PersonalDialog from "./PersonalDialog.vue"
import GoalProgressArt from "./GoalProgressArt.vue"
import { decimalText, errorText, formatTime } from "../composables/personalFormat"
import { PersonalApiError, personalApi, type Goal, type GoalFields, type GoalMode, type GoalStyle, type ProgressChange, type ProgressEntry } from "../services/personalClient"

const props = defineProps<{ mode: "settings" | "record" | "history"; initial: Goal | null }>()
const emit = defineEmits<{ changed: [goal: Goal]; close: [] }>()
const goal = ref(props.initial)
const busy = ref(false)
const error = ref("")
const conflict = ref(false)
const history = ref<ProgressEntry[]>([])
const moreHistory = ref(false)
const styles: { value: GoalStyle; label: string }[] = [
  { value: "water", label: "水位" }, { value: "route", label: "路线" }, { value: "plant", label: "植物" },
]
const entryNames: Record<string, string> = {
  initial: "建立目标", metric: "记录数值", manual: "更新百分比", milestone: "更新里程碑",
  target_change: "调整目标值", mode_change: "调整计算方式",
}
const modeNames: Record<GoalMode, string> = { metric: "量化累计", manual: "手动百分比", milestone: "里程碑" }
function settingsFrom(value: Goal | null) {
  return {
    title: value?.title ?? "",
    description: value?.description ?? "",
    success_criteria: value?.success_criteria ?? "",
    target_date: value?.target_date ?? "",
    progress_mode: value?.progress_mode ?? "metric" as GoalMode,
    progress_style: value?.progress_style ?? "water" as GoalStyle,
    unit: value?.unit ?? "",
    target_value: value?.target_value ? decimalText(value.target_value) : "",
    milestones: value?.milestones.map(item => item.title).join("\n") ?? "",
  }
}
const form = reactive(settingsFrom(goal.value))
const value = ref("")
const note = ref("")
const operation = ref<"add" | "set">("add")
const milestoneId = ref(props.initial?.milestones.find(item => !item.completed_at)?.id ?? props.initial?.milestones[0]?.id ?? null)
const milestoneDone = ref(!props.initial?.milestones.find(item => item.id === milestoneId.value)?.completed_at)
let retry: { key: string; signature: string } | null = null

const progress = computed<ProgressChange | null>(() => {
  if (goal.value?.progress_mode === "milestone") {
    return milestoneId.value ? { kind: "milestone", milestone_id: milestoneId.value, completed: milestoneDone.value } : null
  }
  const n = value.value.trim()
  if (!/^\d{1,14}(\.\d{1,4})?$/.test(n)) return null
  if (goal.value?.progress_mode === "manual") {
    if (!/^\d{1,3}(\.\d{1,2})?$/.test(n) || Number(n) > 100) return null
    return { kind: "manual", percent: n }
  }
  return { kind: "metric", operation: operation.value, value: n }
})

function amountUnits(value: string) {
  const [whole, fraction = ""] = value.split(".")
  return BigInt(whole + fraction.padEnd(4, "0"))
}

function ratioPercent(numerator: bigint, denominator: bigint) {
  const hundredths = numerator * 10000n / denominator
  return hundredths >= 10000n ? 100 : Number(hundredths) / 100
}

const preview = computed(() => {
  const current = goal.value
  const change = progress.value
  if (!current || !change) return null
  let next: number
  let label = ""
  if (change.kind === "metric") {
    const total = (change.operation === "add" ? amountUnits(current.current_value ?? "0") : 0n) + amountUnits(change.value)
    const digits = total.toString().padStart(5, "0")
    next = ratioPercent(total, amountUnits(current.target_value ?? "1"))
    label = decimalText(digits.slice(0, -4) + "." + digits.slice(-4)) + " / " + decimalText(current.target_value) + " " + current.unit
  } else if (change.kind === "milestone") {
    const existing = current.milestones.find(item => item.id === change.milestone_id)
    const completed = current.completed_milestones - Number(Boolean(existing?.completed_at)) + Number(change.completed)
    next = ratioPercent(BigInt(completed), BigInt(current.milestones.length))
    label = completed + " / " + current.milestones.length + " 个里程碑"
  } else next = Number(change.percent)
  return { label, percent: next }
})

function selectMilestone() {
  milestoneDone.value = !goal.value?.milestones.find(item => item.id === milestoneId.value)?.completed_at
}

async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = ""
  conflict.value = false
  try {
    await action()
  } catch (cause) {
    error.value = errorText(cause)
    conflict.value = cause instanceof PersonalApiError && cause.status === 409
  } finally {
    busy.value = false
  }
}

function reloadGoal() {
  const current = goal.value
  if (!current) return
  void run(async () => {
    const updated = await personalApi.goal(current.id)
    goal.value = updated
    Object.assign(form, settingsFrom(updated))
    if (!updated.milestones.some(item => item.id === milestoneId.value)) {
      milestoneId.value = updated.milestones.find(item => !item.completed_at)?.id ?? updated.milestones[0]?.id ?? null
      selectMilestone()
    }
    emit("changed", updated)
  })
}

function apply(updated: Goal) {
  goal.value = updated
  emit("changed", updated)
  emit("close")
}

function saveSettings() {
  const title = form.title.trim()
  if (!title) {
    error.value = "请填写目标名称"
    return
  }
  const settings: GoalFields = {
    title, description: form.description.trim() || null, success_criteria: form.success_criteria.trim() || null,
    target_date: form.target_date || null, progress_mode: form.progress_mode, progress_style: form.progress_style,
  }
  if (form.progress_mode === "metric") {
    settings.unit = form.unit.trim()
    settings.target_value = form.target_value.trim()
    if (!settings.unit || !/^\d{1,14}(\.\d{1,4})?$/.test(settings.target_value) || Number(settings.target_value) <= 0) {
      error.value = "请填写单位及大于零的目标值（最多四位小数）"
      return
    }
  }
  if (form.progress_mode === "milestone") {
    settings.milestones = form.milestones.split("\n").map(line => line.trim()).filter(Boolean)
    if (!settings.milestones.length || settings.milestones.length > 100 || settings.milestones.some(text => text.length > 255)) {
      error.value = "请填写 1–100 个里程碑，每项最多 255 字"
      return
    }
  }
  const current = goal.value
  if (!current) {
    void run(async () => apply(await personalApi.createGoal(settings)))
    return
  }
  const patch: Partial<GoalFields> = { ...settings }
  if (current.progress_mode === settings.progress_mode) {
    delete patch.progress_mode
    if (current.unit === patch.unit) delete patch.unit
    if (decimalText(current.target_value) === patch.target_value) delete patch.target_value
    if (patch.milestones?.join("\n") === current.milestones.map(item => item.title).join("\n")) delete patch.milestones
  }
  void run(async () => apply(await personalApi.updateGoal(current.id, { ...patch, version: current.version })))
}

function saveProgress() {
  const current = goal.value
  const change = progress.value
  if (!current || !change) return
  const body = { ...change, note: note.value.trim() || null }
  const signature = JSON.stringify(body)
  if (!retry || retry.signature !== signature) {
    const bytes = window.crypto.getRandomValues(new Uint8Array(16))
    const key = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("")
    retry = { signature, key }
  }
  const request_key = retry.key
  void run(async () => {
    const result = await personalApi.progress(current.id, { ...body, request_key, version: current.version })
    apply(result.goal)
  })
}

function toggleArchive() {
  const current = goal.value
  if (current) void run(async () => apply(await personalApi.archiveGoal(current.id, current.version, !current.archived_at)))
}

function loadHistory(append = false) {
  const current = goal.value
  if (!current) return
  void run(async () => {
    const result = await personalApi.history(current.id, append ? history.value.length : 0)
    history.value = append ? [...history.value, ...result] : result
    moreHistory.value = result.length === 30
  })
}

onMounted(() => { if (props.mode === "history") loadHistory() })
</script>

<template>
  <PersonalDialog :title="mode === 'record' ? '记录进度' : mode === 'history' ? '更新历史' : goal ? '目标详情' : '新建目标'"
    :busy="busy" :drawer="mode !== 'record'" @close="emit('close')">
    <div v-if="error" class="personal-error personal-toolbar" role="alert">
      {{ error }} <button v-if="conflict" class="btn" :disabled="busy" @click="reloadGoal">重新加载目标</button>
    </div>
    <div v-if="mode === 'history'" class="personal-form">
      <h3>{{ goal?.title }}</h3>
      <p v-if="busy" class="personal-muted" role="status">正在加载更新历史…</p>
      <p v-if="!history.length && !busy && !error" class="personal-muted">暂无历史记录</p>
      <article v-for="entry in history" :key="entry.id" class="history-row">
        <strong>{{ entryNames[entry.entry_type] ?? "进度更新" }}</strong>
        <span class="personal-muted">{{ formatTime(entry.occurred_at) }}</span>
        <span>
          <template v-if="entry.before_data">{{ decimalText(entry.before_data.progress_percent) }}% → </template>
          {{ decimalText(entry.after_data.progress_percent) }}%
        </span>
        <span v-if="entry.after_data.target_value" class="personal-muted">
          {{ decimalText(entry.after_data.current_value) }} / {{ decimalText(entry.after_data.target_value) }} {{ entry.after_data.unit }}
        </span>
        <p v-if="entry.note">{{ entry.note }}</p>
      </article>
      <button v-if="moreHistory || error" class="btn" :disabled="busy" @click="loadHistory(!!history.length)">{{ error ? "重试" : "加载更多" }}</button>
    </div>

    <form v-else-if="mode === 'record' && goal && !goal.archived_at" class="personal-form" @submit.prevent="saveProgress">
      <h3>{{ goal.title }}</h3>
      <template v-if="goal.progress_mode === 'metric'">
        <label class="personal-field">更新方式
          <select v-model="operation" class="select"><option value="add">新增数值</option><option value="set">更正累计值</option></select>
        </label>
        <label class="personal-field">{{ operation === 'add' ? '本次新增' : '正确累计值' }}（{{ goal.unit }}）
          <input v-model="value" class="input" inputmode="decimal" maxlength="19" required autofocus>
        </label>
      </template>
      <label v-else-if="goal.progress_mode === 'manual'" class="personal-field">新的百分比
        <input v-model="value" class="input" inputmode="decimal" maxlength="6" required autofocus>
      </label>
      <template v-else>
        <label class="personal-field">里程碑
          <select v-model="milestoneId" class="select" @change="selectMilestone">
            <option v-for="item in goal.milestones" :key="item.id" :value="item.id">{{ item.completed_at ? '✓ ' : '○ ' }}{{ item.title }}</option>
          </select>
        </label>
        <label class="personal-toggle"><input v-model="milestoneDone" type="checkbox"> 标记为已完成</label>
      </template>
      <div class="progress-preview" aria-live="polite">
        <div><strong>{{ decimalText(goal.progress_percent) }}%<template v-if="preview"> → {{ preview.percent }}%</template></strong>
          <div v-if="preview?.label" class="personal-muted">{{ preview.label }}</div>
        </div>
        <GoalProgressArt :style-name="goal.progress_style" :percent="preview?.percent ?? Number(goal.progress_percent)" />
      </div>
      <label class="personal-field">备注（可选）<textarea v-model="note" class="textarea" maxlength="10000"></textarea></label>
      <div class="personal-actions">
        <button class="btn btn-primary" :disabled="busy || !progress">保存进度</button>
        <button class="btn" type="button" :disabled="busy" @click="emit('close')">取消</button>
      </div>
    </form>

    <div v-else-if="goal?.archived_at" class="personal-form">
      <h3>{{ goal.title }}</h3>
      <p v-if="goal.description">{{ goal.description }}</p>
      <button class="btn btn-primary" :disabled="busy" @click="toggleArchive">恢复目标</button>
    </div>
    <form v-else class="personal-form" @submit.prevent="saveSettings">
      <label class="personal-field">目标名称<input v-model="form.title" class="input" maxlength="255" required autofocus></label>
      <div class="personal-pair">
        <label class="personal-field">进度方式
          <select v-model="form.progress_mode" class="select">
            <option v-for="(label, key) in modeNames" :key="key" :value="key">{{ label }}</option>
          </select>
        </label>
        <label class="personal-field">目标日期<input v-model="form.target_date" class="input" type="date"></label>
      </div>
      <div v-if="form.progress_mode === 'metric'" class="personal-pair">
        <label class="personal-field">目标数值<input v-model="form.target_value" class="input" inputmode="decimal" maxlength="19" required></label>
        <label class="personal-field">单位<input v-model="form.unit" class="input" placeholder="例如 公里" maxlength="32" required></label>
      </div>
      <label v-if="form.progress_mode === 'milestone'" class="personal-field">里程碑（每行一项）
        <textarea v-model="form.milestones" class="textarea" required></textarea>
      </label>
      <fieldset class="styles">
        <legend>进度样式</legend>
        <label v-for="style in styles" :key="style.value" :class="{ selected: form.progress_style === style.value }">
          <GoalProgressArt :style-name="style.value" :percent="68" />
          <span><input v-model="form.progress_style" type="radio" name="progress-style" :value="style.value"> {{ style.label }}</span>
        </label>
      </fieldset>
      <label class="personal-field">达成条件<textarea v-model="form.success_criteria" class="textarea" maxlength="10000"></textarea></label>
      <label class="personal-field">备注<textarea v-model="form.description" class="textarea" maxlength="10000"></textarea></label>
      <div class="personal-actions">
        <button class="btn btn-primary" :disabled="busy">{{ goal ? "保存目标" : "创建目标" }}</button>
        <button v-if="goal" class="btn" type="button" :disabled="busy" @click="toggleArchive">归档</button>
      </div>
    </form>
  </PersonalDialog>
</template>

<style scoped>
.styles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; border: 0; padding: 0; margin: 0; min-width: 0; }
.styles legend { font-weight: 550; margin-bottom: 5px; }
.styles label { display: grid; padding: 4px 4px 8px; text-align: center; cursor: pointer; border: 1px solid var(--border); border-radius: 8px; }
.styles label.selected { border-color: var(--text-2); background: var(--surface-soft); }
.styles input { accent-color: var(--primary); }
.progress-preview { display: grid; grid-template-columns: 1fr 1fr; align-items: center; gap: 8px; background: var(--surface-soft); padding: 10px; border-radius: 8px; }
.progress-preview strong { font-size: 19px; }
.history-row { display: grid; grid-template-columns: 1fr auto; gap: 4px 10px; border-top: 1px solid var(--border); padding-top: 8px; }
.history-row p { grid-column: 1 / -1; margin: 0; color: var(--text-2); white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
