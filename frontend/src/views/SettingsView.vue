<script setup lang="ts">
import { onMounted, ref } from "vue"
import {
  deleteMemory,
  getMemories,
  upsertMemory,
  type MemoryItem,
} from "../services/agentClient"

const memories = ref<MemoryItem[]>([])
const loading = ref(false)
const error = ref("")
const notice = ref("")

// 新增记忆表单
const showForm = ref(false)
const form = ref({ key: "", value: "" })
const formError = ref("")
const saving = ref(false)
const deletingKey = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  error.value = ""
  try {
    memories.value = await getMemories()
  } catch (e) {
    error.value = e instanceof Error ? e.message : "记忆加载失败"
  } finally {
    loading.value = false
  }
}

function openForm(): void {
  formError.value = ""
  form.value = { key: "", value: "" }
  showForm.value = true
}

function closeForm(): void {
  showForm.value = false
}

async function submit(): Promise<void> {
  formError.value = ""
  const key = form.value.key.trim()
  const value = form.value.value.trim()
  if (!key || !value) {
    formError.value = "记忆键和值都不能为空"
    return
  }
  saving.value = true
  try {
    await upsertMemory(key, value)
    closeForm()
    notice.value = `已保存记忆「${key}」`
    await load()
  } catch (e) {
    formError.value = e instanceof Error ? e.message : "保存失败"
  } finally {
    saving.value = false
  }
}

async function remove(item: MemoryItem): Promise<void> {
  if (!window.confirm(`确定删除记忆「${item.key}」吗？删除后 Agent 不再记得这条偏好。`)) return
  deletingKey.value = item.key
  try {
    await deleteMemory(item.key)
    notice.value = `已删除记忆「${item.key}」`
    await load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : "删除失败"
  } finally {
    deletingKey.value = null
  }
}

function fmtTime(iso: string): string {
  if (!iso) return ""
  return iso.replace("T", " ").slice(0, 19)
}

onMounted(() => {
  load()
})
</script>

<template>
  <div class="settings-view">
    <div class="page-head card">
      <div>
        <h1 class="page-title">设置与记忆</h1>
        <p class="muted">
          Agent 在对话中明确表达过的偏好与事实会沉淀为长期记忆；你可以在这里查看、修正或删除。
        </p>
      </div>
      <div class="head-actions">
        <button class="btn" :disabled="loading" @click="load">{{ loading ? "加载中…" : "刷新" }}</button>
        <button class="btn btn-primary" @click="openForm">+ 新增记忆</button>
      </div>
    </div>

    <div v-if="notice" class="card notice-bar">
      <span>{{ notice }}</span>
      <button class="btn icon-btn" title="关闭" @click="notice = ''">✕</button>
    </div>

    <section class="card">
      <h2 class="section-title">长期记忆（{{ memories.length }} 条）</h2>
      <p v-if="error" class="error-text">{{ error }}</p>
      <div v-else-if="loading && memories.length === 0" class="empty">加载中…</div>
      <div v-else-if="memories.length === 0" class="empty">
        还没有任何长期记忆。在对话里告诉 Agent「以后报销默认用其他分类」这类偏好，它会自动记住。
      </div>
      <ul v-else class="memory-list">
        <li v-for="m in memories" :key="m.key" class="memory-item">
          <div class="memory-key">{{ m.key }}</div>
          <div class="memory-value">{{ m.value }}</div>
          <div class="memory-meta">
            <span class="muted">更新于 {{ fmtTime(m.updated_at) }}</span>
            <button
              class="btn btn-mini btn-danger"
              :disabled="deletingKey === m.key"
              @click="remove(m)"
            >
              {{ deletingKey === m.key ? "删除中…" : "删除" }}
            </button>
          </div>
        </li>
      </ul>
    </section>

    <!-- 新增记忆表单 -->
    <div v-if="showForm" class="modal-mask" @click.self="closeForm">
      <div class="modal card">
        <h3 class="modal-title">新增长期记忆</h3>
        <label class="field">
          <span class="field-label">记忆键（简短短语）*</span>
          <input v-model="form.key" class="input" placeholder="例如：报销默认分类" />
        </label>
        <label class="field">
          <span class="field-label">记忆值 *</span>
          <input v-model="form.value" class="input" placeholder="例如：其他" />
        </label>
        <p class="muted hint">键已存在时会覆盖原值；保存后下一次对话即生效。</p>
        <p v-if="formError" class="error-text">{{ formError }}</p>
        <div class="modal-actions">
          <button class="btn" :disabled="saving" @click="closeForm">取消</button>
          <button class="btn btn-primary" :disabled="saving" @click="submit">
            {{ saving ? "保存中…" : "保存" }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-view {
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
.section-title {
  font-size: 15px;
  margin-bottom: 12px;
  color: var(--text-1);
}
.token-row {
  display: flex;
  gap: 10px;
  margin-top: 10px;
  align-items: center;
}
.token-input {
  flex: 1;
  max-width: 420px;
}
.notice-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 16px;
  font-size: 13px;
  color: var(--success);
  border-color: var(--success);
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
.btn-danger {
  color: var(--danger);
  border-color: var(--danger);
  background: var(--surface);
}
.btn-danger:hover:not(:disabled) {
  background: var(--danger-weak);
}
.icon-btn {
  padding: 4px 10px;
  line-height: 1;
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
.memory-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.memory-item {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-soft);
  padding: 12px 14px;
}
.memory-key {
  font-weight: 600;
  font-size: 14px;
  color: var(--text-1);
}
.memory-value {
  margin-top: 4px;
  font-size: 13px;
  color: var(--text-2);
}
.memory-meta {
  margin-top: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
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
.hint {
  margin: 0 0 4px;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 14px;
}
</style>
