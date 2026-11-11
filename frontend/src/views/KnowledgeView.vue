<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue"
import {
  deleteKnowledgeDocument,
  getKnowledgeDocument,
  ingestKnowledgeText,
  listKnowledgeDocuments,
  uploadKnowledgeFile,
  type KnowledgeDocumentItem,
} from "../services/agentClient"

const docs = ref<KnowledgeDocumentItem[]>([])
const loading = ref(false)
const error = ref("")
const notice = ref("")

// 上传 / 粘贴 两种入口，tab 切换
type Tab = "upload" | "paste"
const tab = ref<Tab>("upload")

// —— 文件上传 ——
const fileInput = ref<HTMLInputElement | null>(null)
const selectedFile = ref<File | null>(null)
const uploadTitle = ref("")
const uploading = ref(false)
const uploadError = ref("")

// —— 粘贴文本 ——
const pasteTitle = ref("")
const pasteText = ref("")
const pasting = ref(false)
const pasteError = ref("")

const deletingId = ref<number | null>(null)

// 是否存在待轮询的 pending 文档
const hasPending = computed(() => docs.value.some((d) => d.status === "pending"))
let pollTimer: number | null = null

async function load(): Promise<void> {
  loading.value = true
  error.value = ""
  try {
    docs.value = await listKnowledgeDocuments()
  } catch (e) {
    error.value = e instanceof Error ? e.message : "文档列表加载失败"
  } finally {
    loading.value = false
  }
}

/** 轮询所有 pending 文档直到全部进入 ready/failed */
async function pollPending(): Promise<void> {
  const pending = docs.value.filter((d) => d.status === "pending")
  if (pending.length === 0) return
  for (const doc of pending) {
    try {
      const fresh = await getKnowledgeDocument(doc.id)
      const idx = docs.value.findIndex((d) => d.id === doc.id)
      if (idx !== -1) docs.value[idx] = fresh
      if (fresh.status === "ready") notice.value = `文档「${fresh.title}」已就绪`
      if (fresh.status === "failed") notice.value = `文档「${fresh.title}」摄入失败：${fresh.error_message ?? "未知错误"}`
    } catch {
      // 轮询失败静默，下一轮再试
    }
  }
  if (hasPending.value) {
    pollTimer = window.setTimeout(pollPending, 1500)
  }
}

function onFileChange(e: Event): void {
  const input = e.target as HTMLInputElement
  selectedFile.value = input.files?.[0] ?? null
  if (selectedFile.value && !uploadTitle.value) {
    uploadTitle.value = selectedFile.value.name.replace(/\.[^.]+$/, "")
  }
}

function resetUpload(): void {
  selectedFile.value = null
  uploadTitle.value = ""
  uploadError.value = ""
  if (fileInput.value) fileInput.value.value = ""
}

async function submitUpload(): Promise<void> {
  uploadError.value = ""
  if (!selectedFile.value) {
    uploadError.value = "请先选择要上传的文件"
    return
  }
  uploading.value = true
  try {
    const res = await uploadKnowledgeFile(selectedFile.value, uploadTitle.value.trim() || undefined)
    notice.value = `已受理，正在摄入（文档 #${res.doc_id}）`
    resetUpload()
    await load()
    startPolling()
  } catch (e) {
    uploadError.value = e instanceof Error ? e.message : "上传失败"
  } finally {
    uploading.value = false
  }
}

async function submitPaste(): Promise<void> {
  pasteError.value = ""
  const title = pasteTitle.value.trim()
  const text = pasteText.value.trim()
  if (!title) {
    pasteError.value = "请填写标题"
    return
  }
  if (!text) {
    pasteError.value = "请粘贴要入库的文本内容"
    return
  }
  pasting.value = true
  try {
    const res = await ingestKnowledgeText(title, text)
    notice.value = `已受理，正在摄入（文档 #${res.doc_id}）`
    pasteTitle.value = ""
    pasteText.value = ""
    await load()
    startPolling()
  } catch (e) {
    pasteError.value = e instanceof Error ? e.message : "入库失败"
  } finally {
    pasting.value = false
  }
}

async function remove(doc: KnowledgeDocumentItem): Promise<void> {
  if (!window.confirm(`确定删除文档「${doc.title}」吗？删除后其所有分块与向量将一并清除，不可恢复。`)) return
  deletingId.value = doc.id
  try {
    await deleteKnowledgeDocument(doc.id)
    notice.value = `已删除文档「${doc.title}」`
    await load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : "删除失败"
  } finally {
    deletingId.value = null
  }
}

function startPolling(): void {
  if (pollTimer) return
  pollPending()
}

function fmtTime(iso: string): string {
  if (!iso) return ""
  return iso.replace("T", " ").slice(0, 19)
}

function fmtSize(n: number | null): string {
  if (n == null) return "—"
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function statusBadge(status: string): string {
  if (status === "ready") return "badge-green"
  if (status === "failed") return "badge-red"
  return "badge-amber" // pending
}

function statusLabel(status: string): string {
  if (status === "ready") return "就绪"
  if (status === "failed") return "失败"
  return "摄入中"
}

onMounted(() => {
  load().then(() => {
    if (hasPending.value) startPolling()
  })
})

onBeforeUnmount(() => {
  if (pollTimer) window.clearTimeout(pollTimer)
})
</script>

<template>
  <div class="knowledge-view">
    <div class="page-head card">
      <div>
        <h1 class="page-title">知识库</h1>
        <p class="muted">
          上传文档或粘贴文本，切块并向量化后入库；在对话里提问，Agent 会自动检索这些资料作答。
        </p>
      </div>
      <div class="head-actions">
        <button class="btn" :disabled="loading" @click="load">{{ loading ? "加载中…" : "刷新" }}</button>
      </div>
    </div>

    <div v-if="notice" class="card notice-bar">
      <span>{{ notice }}</span>
      <button class="btn icon-btn" title="关闭" @click="notice = ''">✕</button>
    </div>

    <!-- 摄入入口 -->
    <section class="card">
      <div class="tab-bar">
        <button
          class="tab"
          :class="{ active: tab === 'upload' }"
          @click="tab = 'upload'"
        >上传文件</button>
        <button
          class="tab"
          :class="{ active: tab === 'paste' }"
          @click="tab = 'paste'"
        >粘贴文本</button>
      </div>

      <div v-if="tab === 'upload'" class="ingest-form">
        <div class="field">
          <label class="field-label">选择文件 <span class="muted">支持 txt / md / docx / pdf，单文件 ≤ 20MB</span></label>
          <input
            ref="fileInput"
            type="file"
            class="input file-input"
            accept=".txt,.md,.markdown,.docx,.pdf"
            @change="onFileChange"
          />
        </div>
        <div class="field">
          <label class="field-label">标题（可选，默认取文件名）</label>
          <input
            v-model="uploadTitle"
            type="text"
            class="input"
            placeholder="给这份文档起个展示名"
          />
        </div>
        <div class="form-actions">
          <span class="error-text">{{ uploadError }}</span>
          <button class="btn btn-primary" :disabled="uploading || !selectedFile" @click="submitUpload">
            {{ uploading ? "提交中…" : "上传并入库" }}
          </button>
        </div>
      </div>

      <div v-else class="ingest-form">
        <div class="field">
          <label class="field-label">标题</label>
          <input v-model="pasteTitle" type="text" class="input" placeholder="例如：医保报销政策摘要" />
        </div>
        <div class="field">
          <label class="field-label">内容</label>
          <textarea
            v-model="pasteText"
            class="textarea"
            rows="6"
            placeholder="粘贴要入库的文字内容…"
          ></textarea>
        </div>
        <div class="form-actions">
          <span class="error-text">{{ pasteError }}</span>
          <button class="btn btn-primary" :disabled="pasting" @click="submitPaste">
            {{ pasting ? "提交中…" : "入库" }}
          </button>
        </div>
      </div>
    </section>

    <!-- 文档列表 -->
    <section class="card">
      <h2 class="section-title">文档（{{ docs.length }}）</h2>
      <p v-if="error" class="error-text">{{ error }}</p>
      <div v-else-if="loading && docs.length === 0" class="empty">加载中…</div>
      <div v-else-if="docs.length === 0" class="empty">
        还没有文档。上传文件或粘贴文本，建立你的个人知识库。
      </div>
      <ul v-else class="doc-list">
        <li v-for="d in docs" :key="d.id" class="doc-item">
          <div class="doc-main">
            <div class="doc-title">{{ d.title }}</div>
            <div class="doc-meta muted">
              <span>{{ d.source_type === "file" ? "文件" : "文本" }}</span>
              <span v-if="d.filename">· {{ d.filename }}</span>
              <span>· {{ d.chunk_count }} 块</span>
              <span v-if="d.file_size != null">· {{ fmtSize(d.file_size) }}</span>
              <span>· {{ fmtTime(d.created_at) }}</span>
            </div>
            <div v-if="d.status === 'failed' && d.error_message" class="doc-error">
              失败原因：{{ d.error_message }}
            </div>
          </div>
          <div class="doc-side">
            <span class="badge" :class="statusBadge(d.status)">{{ statusLabel(d.status) }}</span>
            <button
              class="btn btn-danger"
              :disabled="deletingId === d.id"
              @click="remove(d)"
            >{{ deletingId === d.id ? "删除中…" : "删除" }}</button>
          </div>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.knowledge-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.page-title {
  font-size: 20px;
  margin-bottom: 4px;
}

.head-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.notice-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: var(--success-weak);
  border-color: #dcfce7;
  padding: 10px 16px;
  font-size: 13px;
  color: var(--success);
}

.icon-btn {
  padding: 2px 8px;
}

.section-title {
  font-size: 14px;
  margin-bottom: 14px;
}

.tab-bar {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 18px;
}

.tab {
  appearance: none;
  border: none;
  background: transparent;
  padding: 8px 14px;
  font-size: 13px;
  color: var(--text-2);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  font-family: var(--font);
}

.tab:hover {
  color: var(--text-1);
}

.tab.active {
  color: var(--text-1);
  font-weight: 600;
  border-bottom-color: var(--accent);
}

.ingest-form {
  max-width: 640px;
}

.field {
  margin-bottom: 14px;
}

.field-label {
  display: block;
  font-size: 13px;
  color: var(--text-2);
  margin-bottom: 6px;
}

.file-input {
  padding: 8px;
}

.form-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.error-text {
  color: var(--danger);
  font-size: 12px;
}

.empty {
  color: var(--text-3);
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
}

.doc-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}

.doc-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 0;
  border-bottom: 1px solid var(--border);
}

.doc-item:last-child {
  border-bottom: none;
}

.doc-main {
  min-width: 0;
  flex: 1;
}

.doc-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-meta {
  margin-top: 3px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.doc-error {
  margin-top: 4px;
  color: var(--danger);
  font-size: 12px;
}

.doc-side {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}
</style>
