<script setup lang="ts">
import { ref, watch, nextTick, onMounted, onBeforeUnmount } from "vue"
import { marked } from "marked"
import DOMPurify from "dompurify"
import { useSessionStore, type ChatTurn } from "../stores/session"

const store = useSessionStore()
const message = ref("")

/** AI 回答用 Markdown 渲染（消毒防 XSS：AI 输出不可信） */
function mdToHtml(text: string): string {
  return DOMPurify.sanitize(marked.parse(text) as string)
}

/* ===== 智能自动滚动：流式输出时跟随到底部；用户手动上滑则暂停跟随，滑回底部自动恢复 ===== */
const chatStream = ref<HTMLElement | null>(null)
const stickToBottom = ref(true)
const STICK_THRESHOLD = 48 // 距底部小于该值视为"在底部"

function scrollToBottom(): void {
  const el = chatStream.value
  if (!el) return
  el.scrollTop = el.scrollHeight
}

function onScroll(): void {
  const el = chatStream.value
  if (!el) return
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD
  stickToBottom.value = nearBottom
}

/** 内容变化（新消息 / 流式 token / 心跳 / busy 切换）→ nextTick 等 DOM 更新后跟随底部 */
async function followStream(): Promise<void> {
  await nextTick()
  if (stickToBottom.value) scrollToBottom()
}

watch(() => store.chatTurns.length, followStream)
watch(() => store.streamText, followStream)
watch(() => store.progressText, followStream)
watch(() => store.busy, followStream)

onMounted(() => {
  chatStream.value?.addEventListener("scroll", onScroll, { passive: true })
  scrollToBottom()
})

onBeforeUnmount(() => {
  chatStream.value?.removeEventListener("scroll", onScroll)
})

/** 审批恢复（resume）后 answer 更新不会经过 send()，需响应式监听：
 * 只要 store.answer 变为非空新文本就追加一条助手消息。 */
watch(
  () => store.answer,
  (answer) => {
    store.pushTurn("assistant", answer ?? "")
  },
)

async function send(): Promise<void> {
  const text = message.value.trim()
  if (!text) return
  message.value = ""
  store.pushTurn("user", text)
  await store.sendMessage(text)
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault()
    void send()
  }
}

function turns(): ChatTurn[] {
  return store.chatTurns
}
</script>

<template>
  <section class="chat-panel">
    <div class="chat-title">对话</div>

    <div class="chat-stream" ref="chatStream">
      <div v-if="turns().length === 0" class="chat-empty">
        <p class="muted">和管家聊聊，例如：上个月交通费花了多少；创建一张订阅退订卡。</p>
      </div>
      <div
        v-for="(turn, index) in turns()"
        :key="index"
        class="bubble"
        :class="turn.role === 'user' ? 'bubble-user' : 'bubble-assistant'"
      >
        <div v-if="turn.role === 'assistant'" class="markdown" v-html="mdToHtml(turn.text)"></div>
        <template v-else>{{ turn.text }}</template>
      </div>
      <div v-if="store.busy" class="bubble bubble-assistant">
        <div v-if="store.streamText" class="markdown" v-html="mdToHtml(store.streamText)"></div>
        <span v-else class="typing">
          {{ store.queuedCount > 0 ? `已排队 ${store.queuedCount} 条，将在当前任务中追加…` : store.progressText || "管家正在思考…" }}
        </span>
      </div>
    </div>

    <form class="composer" @submit.prevent="send">
      <textarea
        class="textarea"
        v-model="message"
        placeholder="💬 和管家聊聊…（执行中也能发，将排队追加）"
        @keydown="onKeydown"
      ></textarea>
      <div class="composer-foot">
        <span class="muted">Enter 发送 · Shift+Enter 换行</span>
        <button class="btn btn-primary" type="submit" :disabled="!message.trim()">
          {{ store.busy ? (store.queuedCount > 0 ? "排队中…" : "发送（排队）") : "发送" }}
        </button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.chat-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-1);
  margin-bottom: 12px;
}

.chat-stream {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px 2px 12px;
}

.chat-empty {
  padding: 26px 10px;
  text-align: center;
}

.bubble {
  max-width: 76%;
  padding: 9px 14px;
  font-size: 13px;
  line-height: 1.7;
  word-break: break-word;
}

.bubble-user {
  align-self: flex-end;
  background: var(--primary);
  color: #fff;
  border-radius: 12px 12px 3px 12px;
  white-space: pre-wrap;
}

.bubble-assistant {
  align-self: flex-start;
  background: var(--surface-soft);
  border: 1px solid var(--border);
  border-radius: 12px 12px 12px 3px;
  color: var(--text-1);
  max-width: 92%;
}

.typing {
  color: var(--text-3);
}

/* ===== Markdown 渲染样式 ===== */
.markdown {
  overflow-x: auto;
}

.markdown :deep(p) {
  margin: 6px 0;
}

.markdown :deep(p:first-child) {
  margin-top: 0;
}

.markdown :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown :deep(h1),
.markdown :deep(h2),
.markdown :deep(h3),
.markdown :deep(h4) {
  margin: 10px 0 6px;
  font-weight: 700;
  line-height: 1.4;
}

.markdown :deep(h1) { font-size: 17px; }
.markdown :deep(h2) { font-size: 15px; }
.markdown :deep(h3) { font-size: 14px; }
.markdown :deep(h4) { font-size: 13px; }

.markdown :deep(ul),
.markdown :deep(ol) {
  margin: 6px 0;
  padding-left: 20px;
}

.markdown :deep(li) {
  margin: 3px 0;
}

.markdown :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
  font-size: 12px;
}

.markdown :deep(th),
.markdown :deep(td) {
  border: 1px solid var(--border);
  padding: 5px 9px;
  text-align: left;
}

.markdown :deep(th) {
  background: var(--surface-soft);
  font-weight: 600;
  white-space: nowrap;
}

.markdown :deep(code) {
  background: var(--surface-soft);
  border: 1px solid var(--border);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 12px;
}

.markdown :deep(pre) {
  background: var(--surface-soft);
  border: 1px solid var(--border);
  padding: 10px 12px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 8px 0;
}

.markdown :deep(pre code) {
  background: none;
  border: none;
  padding: 0;
}

.markdown :deep(strong) {
  font-weight: 600;
}

.markdown :deep(a) {
  color: var(--primary);
}

.markdown :deep(blockquote) {
  margin: 8px 0;
  padding: 4px 12px;
  border-left: 3px solid var(--primary);
  color: var(--text-2);
  background: var(--surface-soft);
  border-radius: 0 6px 6px 0;
}

.markdown :deep(hr) {
  border: none;
  border-top: 1px solid var(--border);
  margin: 10px 0;
}

.composer {
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-top: 1px solid var(--border);
  padding-top: 12px;
}

.textarea {
  min-height: 64px;
}

.composer-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>

