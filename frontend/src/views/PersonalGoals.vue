<script setup lang="ts">
import { ref, watch } from "vue"
import GoalProgressArt from "../components/GoalProgressArt.vue"
import PersonalGoalDialog from "../components/PersonalGoalDialog.vue"
import { decimalText, errorText } from "../composables/personalFormat"
import { personalApi, type Goal } from "../services/personalClient"

const goals = ref<Goal[]>([])
const archived = ref(false)
const hasMore = ref(false)
const loading = ref(false)
const error = ref("")
const selected = ref<Goal | null>(null)
const panel = ref<"settings" | "record" | "history" | null>(null)
let requestId = 0
const pageSize = 100

async function load(append = false) {
  const id = ++requestId
  const offset = append ? goals.value.length : 0
  loading.value = true
  error.value = ""
  try {
    const result = await personalApi.goals({ archived: archived.value, limit: pageSize, offset })
    if (id !== requestId) return
    goals.value = append ? [...goals.value, ...result] : result
    hasMore.value = result.length === pageSize
  } catch (cause) {
    if (id === requestId) error.value = errorText(cause)
  } finally {
    if (id === requestId) loading.value = false
  }
}

watch(archived, () => { goals.value = []; hasMore.value = false; void load() }, { immediate: true })

function open(type: NonNullable<typeof panel.value>, goal: Goal | null = null) {
  selected.value = goal
  panel.value = type
}

function changed(goal: Goal) {
  selected.value = goal
  const known = goals.value.some(item => item.id === goal.id)
  if (Boolean(goal.archived_at) !== archived.value) goals.value = goals.value.filter(item => item.id !== goal.id)
  else if (known) goals.value = goals.value.map(item => item.id === goal.id ? goal : item)
  else goals.value = [goal, ...goals.value]
}
</script>

<template>
  <section class="personal-page" aria-labelledby="personal-goals-heading">
    <header class="personal-toolbar">
      <h2 id="personal-goals-heading">长期目标</h2>
      <div class="personal-actions">
        <button class="btn" :aria-pressed="archived" @click="archived = !archived">{{ archived ? '查看进行中的目标' : '查看已归档' }}</button>
        <button class="btn btn-primary" @click="open('settings')">＋ 新建目标</button>
      </div>
    </header>
    <div v-if="error" class="personal-error personal-toolbar" role="alert">{{ error }} <button class="btn" @click="load()">重试</button></div>
    <p v-if="loading" class="personal-muted" role="status">正在加载目标…</p>
    <div v-if="!loading && !error && !goals.length" class="card personal-empty">{{ archived ? "暂无归档目标" : "还没有长期目标" }}</div>

    <article v-for="goal in goals" :key="goal.id" class="card goal-card">
      <div class="goal-copy">
        <h3>{{ goal.title }}</h3>
        <span v-if="goal.target_date" class="personal-muted">目标日期 {{ goal.target_date }}</span>
        <strong class="goal-percent">{{ decimalText(goal.progress_percent) }}<span>%</span></strong>
        <span v-if="goal.progress_mode === 'metric'" class="goal-detail">
          {{ decimalText(goal.current_value) }} / {{ decimalText(goal.target_value) }} {{ goal.unit }}
        </span>
        <span v-if="goal.progress_mode === 'milestone'" class="goal-detail">
          已完成 {{ goal.completed_milestones }} / {{ goal.milestones.length }} 个里程碑
        </span>
        <p v-if="goal.success_criteria" class="goal-criteria">{{ goal.success_criteria }}</p>
        <div class="personal-actions">
          <button v-if="!goal.archived_at" class="btn btn-primary" @click="open('record', goal)">记录进度</button>
          <button class="btn" @click="open('settings', goal)">{{ goal.archived_at ? "目标详情" : "编辑目标" }}</button>
          <button class="btn" @click="open('history', goal)">更新历史</button>
        </div>
      </div>
      <GoalProgressArt :style-name="goal.progress_style" :percent="Number(goal.progress_percent)" />
    </article>
    <button v-if="hasMore" class="btn load-more" :disabled="loading" @click="load(true)">加载更多目标</button>
    <PersonalGoalDialog v-if="panel" :key="panel + (selected?.id ?? 'new')"
      :mode="panel" :initial="selected" @close="panel = null" @changed="changed" />
  </section>
</template>

<style scoped>
.goal-card { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr); gap: 20px; align-items: center; }
.goal-copy { display: grid; justify-items: start; gap: 7px; min-width: 0; overflow-wrap: anywhere; }
.goal-copy h3 { font-size: 19px; font-weight: 650; }
.goal-percent { font-size: clamp(34px, 5vw, 55px); line-height: 1.08; letter-spacing: -.06em; font-variant-numeric: tabular-nums; }
.goal-percent > span { font-size: .6em; margin-left: .1em; font-weight: 500; }
.goal-detail { font-size: 15px; color: var(--text-2); }
.goal-criteria { margin: 0; white-space: pre-wrap; color: var(--text-2); font-size: 13px; }
.goal-copy .personal-actions { margin-top: 7px; }
.load-more { justify-self: start; }
@media (max-width: 800px) {
  .goal-card { grid-template-columns: minmax(0, 1fr); gap: 8px; padding: 14px; }
}
</style>
