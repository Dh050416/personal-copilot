<script setup lang="ts">
import { computed, ref, watch } from "vue"
import { useSessionStore } from "../stores/session"

const store = useSessionStore()
const showRaw = ref(false)

/** 常见工具参数的中文字段名；未命中的回退到原始 key，避免裸 JSON 直接甩给用户 */
const FIELD_LABELS: Record<string, string> = {
  title: "标题",
  name: "名称",
  content: "内容",
  note: "备注",
  description: "描述",
  amount: "金额",
  category: "分类",
  direction: "方向",
  counterparty: "对方",
  date: "日期",
  month: "月份",
  year: "年份",
  start_at: "开始时间",
  end_at: "结束时间",
  datetime: "时间",
  location: "地点",
  event_id: "日程 ID",
  card_id: "卡片 ID",
  bill_id: "账单 ID",
  id: "ID",
  status: "状态",
  keyword: "关键词",
  query: "查询条件",
  reason: "原因",
  type: "类型",
  memory_key: "记忆键",
  memory_value: "记忆值",
  edited_args: "修改后参数",
  decision: "决定",
  url: "链接",
}

/** 仅对语义明确的字段做值翻译，避免把任意字符串误改 */
const FIELD_VALUE_LABELS: Record<string, Record<string, string>> = {
  direction: { expense: "支出", income: "收入" },
}

interface ParamRow {
  key: string
  label: string
  value: string
}

function formatValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return "—"
  if (typeof value === "boolean") return value ? "是" : "否"
  if (typeof value === "number") return String(value)
  if (typeof value === "string") {
    if (value === "") return "（空）"
    return FIELD_VALUE_LABELS[key]?.[value] ?? value
  }
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

const rows = computed<ParamRow[]>(() =>
  Object.entries(store.approval?.args ?? {}).map(([key, value]) => ({
    key,
    label: FIELD_LABELS[key] ?? key,
    value: formatValue(key, value),
  })),
)

const rawJson = computed(() => JSON.stringify(store.approval?.args ?? {}, null, 2))

// 新审批（或审批失败被放回）时收起原始参数，避免上一次的展开状态串到下一次
watch(
  () => store.approval?.approvalId,
  () => {
    showRaw.value = false
  },
)

function approve(): void {
  void store.decide("approve")
}

function reject(): void {
  void store.decide("reject")
}
</script>

<template>
  <!-- 浮层只覆盖聊天面板：不占 flex 布局高度，也不遮左侧会话列表 -->
  <div v-if="store.approval" class="approval-layer">
    <section class="approval-dialog" role="dialog" aria-labelledby="approval-heading">
      <header class="approval-head">
        <span class="approval-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"
               stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 3 4.5 6.4v5.1c0 4.6 3 8.3 7.5 9.5 4.5-1.2 7.5-4.9 7.5-9.5V6.4L12 3Z" />
            <path d="M12 8.6v4.1" />
            <path d="M12 16.1h.01" />
          </svg>
        </span>
        <div class="approval-heading">
          <h2 id="approval-heading">需要你确认</h2>
          <p>管家准备执行一项写操作，请核对参数后决定。</p>
        </div>
        <span class="approval-step">待确认</span>
      </header>

      <div class="approval-body">
        <div class="tool-line">
          <span class="field-label">调用工具</span>
          <code class="tool-chip">{{ store.approval.tool }}</code>
        </div>

        <div class="params-head">
          <span class="field-label">参数 · {{ rows.length }}</span>
          <button class="link-btn" type="button" @click="showRaw = !showRaw">
            {{ showRaw ? "表格视图" : "查看原始 JSON" }}
          </button>
        </div>

        <dl v-if="!showRaw" class="param-list">
          <div v-for="row in rows" :key="row.key" class="param-row">
            <dt>{{ row.label }}</dt>
            <dd>{{ row.value }}</dd>
          </div>
          <p v-if="rows.length === 0" class="param-empty">本次调用没有参数。</p>
        </dl>
        <pre v-else class="raw-json">{{ rawJson }}</pre>

        <div class="approval-id">
          <span class="field-label">审批 ID</span>
          <code>{{ store.approval.approvalId }}</code>
        </div>
      </div>

      <footer class="approval-foot">
        <p class="foot-hint">确认后参数会再次校验，与确认内容不一致将被拒绝。</p>
        <div class="approval-actions">
          <button class="btn" :disabled="store.busy" @click="reject">拒绝</button>
          <button class="btn btn-primary" :disabled="store.busy" @click="approve">
            {{ store.busy ? "执行中…" : "确认执行" }}
          </button>
        </div>
      </footer>
    </section>
  </div>
</template>

<style scoped>
/* 遮罩只做视觉弱化：pointer-events 交给卡片，聊天历史与输入框仍可操作 */
.approval-layer {
  position: absolute;
  inset: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  border-radius: var(--radius-lg);
  background: rgba(250, 250, 250, 0.42);
  backdrop-filter: blur(0.5px);
  pointer-events: none;
}

.approval-dialog {
  pointer-events: auto;
  width: min(480px, 100%);
  max-height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.12);
  overflow: hidden;
  animation: approval-in 0.18s ease-out;
}

.approval-dialog::before {
  content: "";
  display: block;
  height: 2px;
  flex: none;
  background: var(--primary);
}

.approval-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 18px 20px 14px;
}

.approval-mark {
  width: 34px;
  height: 34px;
  flex: none;
  display: grid;
  place-items: center;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-md);
  background: var(--surface-soft);
  color: var(--text-1);
}

.approval-mark svg {
  width: 18px;
  height: 18px;
}

.approval-heading {
  min-width: 0;
}

.approval-heading h2 {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.2px;
}

.approval-heading p {
  margin: 3px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-3);
}

.approval-step {
  margin-left: auto;
  flex: none;
  padding: 2px 9px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--neutral-weak);
  color: var(--text-2);
  font-size: 11px;
  line-height: 1.7;
  white-space: nowrap;
}

.approval-body {
  min-height: 0;
  overflow-y: auto;
  padding: 0 20px;
}

.tool-line {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 0;
  border-top: 1px solid var(--border);
}

.field-label {
  font-size: 11px;
  letter-spacing: 0.3px;
  color: var(--text-3);
  white-space: nowrap;
}

.tool-chip {
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: var(--surface-soft);
  font-size: 12px;
  color: var(--text-1);
}

.params-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 0 7px;
}

.link-btn {
  appearance: none;
  border: none;
  background: none;
  padding: 0;
  font-size: 11px;
  font-family: var(--font);
  color: var(--text-2);
  text-decoration: underline;
  text-underline-offset: 3px;
  cursor: pointer;
}

.link-btn:hover {
  color: var(--text-1);
}

.param-list {
  margin: 0;
  max-height: 196px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}

.param-row {
  display: grid;
  grid-template-columns: 100px minmax(0, 1fr);
  gap: 12px;
  padding: 7px 12px;
  font-size: 12px;
  line-height: 1.6;
}

.param-row:nth-child(odd) {
  background: var(--surface-soft);
}

.param-row + .param-row {
  border-top: 1px solid var(--border);
}

.param-row dt {
  color: var(--text-3);
}

.param-row dd {
  margin: 0;
  color: var(--text-1);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.param-empty {
  margin: 0;
  padding: 14px 12px;
  font-size: 12px;
  color: var(--text-3);
}

.raw-json {
  margin: 0;
  max-height: 196px;
  overflow: auto;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-soft);
  color: var(--text-2);
  font-size: 11.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.approval-id {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 13px 0 14px;
}

.approval-id code {
  font-size: 11px;
  color: var(--text-3);
  overflow-wrap: anywhere;
}

.approval-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 20px 15px;
  border-top: 1px solid var(--border);
  background: var(--surface-soft);
}

.foot-hint {
  margin: 0;
  max-width: 210px;
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-3);
}

.approval-actions {
  display: flex;
  flex: none;
  gap: 8px;
}

.approval-actions .btn {
  min-width: 86px;
}

@keyframes approval-in {
  from {
    opacity: 0;
    transform: translateY(8px) scale(0.99);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .approval-dialog {
    animation: none;
  }
}

@media (max-width: 640px) {
  .approval-layer {
    padding: 12px;
  }

  .approval-head,
  .approval-foot {
    padding-left: 14px;
    padding-right: 14px;
  }

  .approval-body {
    padding-left: 14px;
    padding-right: 14px;
  }

  .approval-foot {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }

  .approval-actions {
    justify-content: flex-end;
  }

  .foot-hint {
    max-width: none;
  }

  .param-row {
    grid-template-columns: 84px minmax(0, 1fr);
  }
}
</style>
