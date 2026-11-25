<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue"
import PersonalDialog from "./PersonalDialog.vue"
import { errorText, formatTime, localDateTime } from "../composables/personalFormat"
import { PersonalApiError, personalApi, type Goal, type Task, type TaskStatus, type TaskUpdate } from "../services/personalClient"

const props = defineProps<{ initial: Task | null; creationLane: "inbox" | "todo" }>()
const emit = defineEmits<{ close: []; changed: [] }>()
const task = ref(props.initial)
const goals = ref<Goal[]>([])
const goalsError = ref("")
const error = ref("")
const conflict = ref(false)
const busy = ref(false)
const newItem = ref("")
const editItemId = ref<number | null>(null)
const editItemLabel = ref("")

function fromTask(value: Task | null) {
  return {
    title: value?.title ?? "",
    board_status: value?.board_status ?? props.creationLane,
    category: value?.category ?? "",
    source: value?.source ?? "",
    note: value?.note ?? "",
    goal_id: value?.goal_id ?? null,
    due_at: value?.due_at ? localDateTime(value.due_at) : "",
    reminder_at: value?.reminder_at ? localDateTime(value.reminder_at) : "",
    is_waiting: value?.is_waiting ?? false,
    waiting_note: value?.waiting_note ?? "",
    follow_up_at: value?.follow_up_at ? localDateTime(value.follow_up_at) : "",
  }
}

const form = reactive(fromTask(task.value))
const canWait = computed(() => !!task.value && ["todo", "doing"].includes(form.board_status))
const statuses = computed(() => {
  if (!task.value || task.value.board_status === "done") {
    return task.value ? [["done", "已完成"], ["todo", "待做"]] : [["inbox", "收件箱"], ["todo", "待做"]]
  }
  return [["inbox", "收件箱"], ["todo", "待做"], ["doing", "进行中"], ["done", "已完成"]]
})

async function loadGoals() {
  goalsError.value = ""
  try {
    const available: Goal[] = []
    for (let offset = 0; ; offset += 100) {
      const page = await personalApi.goals({ offset, limit: 100 })
      available.push(...page)
      if (page.length < 100) break
    }
    goals.value = available
  } catch (cause) {
    goalsError.value = errorText(cause)
  }
}

onMounted(loadGoals)

function iso(value: string) {
  return value ? new Date(value).toISOString() : null
}

async function act(operation: () => Promise<Task>, close = false) {
  if (busy.value) return false
  busy.value = true
  error.value = ""
  conflict.value = false
  try {
    task.value = await operation()
    emit("changed")
    if (close) emit("close")
    return true
  } catch (cause) {
    error.value = errorText(cause)
    conflict.value = cause instanceof PersonalApiError && cause.status === 409
    return false
  } finally {
    busy.value = false
  }
}

function save(status = form.board_status) {
  const title = form.title.trim()
  if (!title) {
    error.value = "请填写任务标题"
    return
  }
  const current = task.value
  const values = {
    title, category: form.category.trim() || null, source: form.source.trim() || null,
    note: form.note.trim() || null, goal_id: form.goal_id, due_at: iso(form.due_at),
  }
  if (!current) {
    void act(() => personalApi.createTask({
      ...values, board_status: status === "todo" ? "todo" : "inbox", reminder_at: iso(form.reminder_at),
    }), true)
    return
  }
  const before = fromTask(current)
  const waiting = (status === "todo" || status === "doing") && form.is_waiting
  const patch: TaskUpdate = { ...values, version: current.version, board_status: status, is_waiting: waiting }
  if (form.due_at === before.due_at) delete patch.due_at
  if (status !== "done" && form.reminder_at !== before.reminder_at) patch.reminder_at = iso(form.reminder_at)
  if (waiting) {
    patch.waiting_note = form.waiting_note.trim() || null
    if (form.follow_up_at !== before.follow_up_at) patch.follow_up_at = iso(form.follow_up_at)
  }
  void act(() => personalApi.updateTask(current.id, patch), true)
}

async function reloadTask() {
  const current = task.value
  if (!current) return
  if (await act(() => personalApi.task(current.id))) {
    Object.assign(form, fromTask(task.value))
    editItemId.value = null
  }
}

async function addItem() {
  const current = task.value
  const label = newItem.value.trim()
  if (current && label && await act(() => personalApi.addCheckItem(current.id, current.version, label))) newItem.value = ""
}

async function setChecked(item: number, event: Event) {
  const current = task.value
  if (!current) return
  const input = event.target as HTMLInputElement
  const checked = input.checked
  if (!await act(() => personalApi.checkItem(current.id, item, current.version, { checked }))) input.checked = !checked
}

async function renameItem(id: number) {
  const current = task.value
  const label = editItemLabel.value.trim()
  if (current && label && await act(() => personalApi.checkItem(current.id, id, current.version, { label }))) {
    editItemId.value = null
  }
}

function removeItem(id: number) {
  const current = task.value
  if (current) void act(() => personalApi.removeCheckItem(current.id, id, current.version))
}

function toggleArchive() {
  const current = task.value
  if (current) void act(() => personalApi.archiveTask(current.id, current.version, !current.archived_at), true)
}
</script>

<template>
  <PersonalDialog :title="task ? '任务详情' : '新建任务'" drawer :busy="busy" @close="emit('close')">
    <div v-if="error" class="personal-error personal-toolbar" role="alert">
      {{ error }}
      <button v-if="conflict" class="btn" :disabled="busy" @click="reloadTask">重新加载</button>
    </div>
    <div v-if="task?.archived_at" class="personal-form">
      <h3>{{ task.title }}</h3>
      <span class="personal-muted">归档于 {{ formatTime(task.archived_at) }}</span>
      <p v-if="task.note" class="archived-note">{{ task.note }}</p>
      <div class="personal-actions"><button class="btn btn-primary" :disabled="busy" @click="toggleArchive">恢复任务</button></div>
    </div>
    <template v-else>
      <form class="personal-form" @submit.prevent="save()">
        <label class="personal-field">标题<input v-model="form.title" class="input" maxlength="255" required autofocus></label>
        <div class="personal-pair">
          <label class="personal-field">状态
            <select v-model="form.board_status" class="select">
              <option v-for="[value, label] in statuses" :key="value" :value="value as TaskStatus">{{ label }}</option>
            </select>
          </label>
          <label class="personal-field">分类<input v-model="form.category" class="input" maxlength="64"></label>
        </div>
        <div class="personal-pair">
          <label class="personal-field">截止时间<input v-model="form.due_at" class="input" type="datetime-local"></label>
          <label v-if="form.board_status !== 'done'" class="personal-field">
            开工提醒<input v-model="form.reminder_at" class="input" type="datetime-local">
          </label>
        </div>
        <div class="personal-pair">
          <label class="personal-field">来源<input v-model="form.source" class="input" maxlength="128"></label>
          <label class="personal-field">关联目标
            <select v-model="form.goal_id" class="select">
              <option :value="null">未关联</option>
              <option v-if="form.goal_id && !goals.some(goal => goal.id === form.goal_id)" :value="form.goal_id">
                目标 #{{ form.goal_id }}
              </option>
              <option v-for="goal in goals" :key="goal.id" :value="goal.id">{{ goal.title }}</option>
            </select>
          </label>
        </div>
        <div v-if="goalsError" class="personal-muted">{{ goalsError }} <button type="button" class="btn" @click="loadGoals">重试加载目标</button></div>
        <label class="personal-field">备注<textarea v-model="form.note" class="textarea" maxlength="10000"></textarea></label>
        <template v-if="canWait">
          <label class="personal-toggle"><input v-model="form.is_waiting" type="checkbox"> 等待回复</label>
          <div v-if="form.is_waiting" class="personal-pair">
            <label class="personal-field">等待原因<input v-model="form.waiting_note" class="input" maxlength="10000"></label>
            <label class="personal-field">跟进提醒<input v-model="form.follow_up_at" class="input" type="datetime-local"></label>
          </div>
        </template>
        <div class="personal-actions">
          <button class="btn btn-primary" :disabled="busy" type="submit">{{ task ? "保存任务" : "创建任务" }}</button>
          <button v-if="task && task.board_status !== 'done'" class="btn" :disabled="busy" type="button" @click="save('done')">标记完成</button>
          <button v-if="task" class="btn" :disabled="busy" type="button" @click="toggleArchive">归档</button>
        </div>
      </form>

      <section v-if="task" class="check-section">
        <h3>检查项 <span class="personal-muted">{{ task.checked_count }} / {{ task.check_total }}</span></h3>
        <div v-for="item in task.check_items" :key="item.id" class="check-row">
          <form v-if="editItemId === item.id" class="check-row" @submit.prevent="renameItem(item.id)">
            <input v-model="editItemLabel" class="input" maxlength="255" required aria-label="检查项名称">
            <button class="btn" :disabled="busy">保存</button>
            <button class="btn" :disabled="busy" type="button" @click="editItemId = null">取消</button>
          </form>
          <template v-else>
            <label class="personal-toggle">
              <input type="checkbox" :checked="!!item.checked_at" :disabled="busy"
                @change="setChecked(item.id, $event)">
              <span>{{ item.label }}</span>
            </label>
            <button class="btn" :disabled="busy" @click="editItemId = item.id; editItemLabel = item.label">编辑</button>
            <button class="btn" :disabled="busy" :aria-label="'删除检查项 ' + item.label" @click="removeItem(item.id)">删除</button>
          </template>
        </div>
        <form class="check-row" @submit.prevent="addItem">
          <input v-model="newItem" class="input" aria-label="新检查项" placeholder="添加检查项" maxlength="255" required>
          <button class="btn" :disabled="busy">添加</button>
        </form>
      </section>
    </template>
  </PersonalDialog>
</template>

<style scoped>
.check-section { margin-top: 18px; border-top: 1px solid var(--border); padding-top: 13px; display: grid; gap: 8px; }
.check-section h3 { font-size: 14px; }
.check-row { display: flex; gap: 6px; align-items: center; min-width: 0; }
.check-row > label, .check-row > input, .check-row > form { flex: 1; }
.check-row .btn { flex-shrink: 0; padding-inline: 8px; }
.check-row label span, .archived-note { overflow-wrap: anywhere; }
.archived-note { white-space: pre-wrap; }
</style>
