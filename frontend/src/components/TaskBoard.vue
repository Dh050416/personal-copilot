<script setup lang="ts">
import { computed, ref } from "vue"
import type { CardListData, TrackingCard } from "../tools/types"
import { completeCard, deleteCard } from "../services/agentClient"

const props = defineProps<{ data: CardListData }>()
const emit = defineEmits<{ completed: [card: TrackingCard]; deleted: [card: TrackingCard] }>()

/** 泳道顺序：未报销 / 订阅 / 餐饮 / 交通 / AA / 其他 */
const LANE_ORDER = ["未报销", "订阅", "餐饮", "交通", "AA", "其他"] as const

const LANE_CLASS: Record<string, string> = {
  未报销: "lane-reimburse",
  订阅: "lane-sub",
  餐饮: "lane-food",
  交通: "lane-transport",
  AA: "lane-aa",
  其他: "lane-other",
}

const PURPOSE_LABELS: Record<string, string> = {
  报销: "报销",
  退款: "退款",
  待退订: "待退订",
  AA: "AA",
  待处理: "待处理",
}

/** 卡片归属泳道：优先按用途（AA/报销），再按消费分类 */
function laneFor(card: TrackingCard): string {
  if (card.purpose === "AA") return "AA"
  if (card.category === "订阅") return "订阅"
  if (card.category === "餐饮") return "餐饮"
  if (card.category === "交通") return "交通"
  if (card.purpose === "报销") return "未报销"
  return "其他"
}

interface Lane {
  name: string
  cards: TrackingCard[]
  laneClass: string
}

const activeCards = computed(() => props.data.items.filter((card) => card.status === "active"))
const doneCards = computed(() => props.data.items.filter((card) => card.status !== "active"))

const lanes = computed<Lane[]>(() =>
  LANE_ORDER.map((name) => ({
    name,
    cards: activeCards.value.filter((card) => laneFor(card) === name),
    laneClass: LANE_CLASS[name] ?? "lane-other",
  })),
)

/* ===== 拖拽：拖入即完成，无需确认 ===== */
const draggingId = ref<number | null>(null)
const dragOverDone = ref(false)
const completing = ref(false)
const errorText = ref("")
const doneTip = ref("")

function onDragStart(card: TrackingCard, event: DragEvent): void {
  draggingId.value = card.id
  event.dataTransfer?.setData("text/plain", String(card.id))
  if (event.dataTransfer) event.dataTransfer.effectAllowed = "move"
}

function onDragEnd(): void {
  draggingId.value = null
  dragOverDone.value = false
}

async function onDrop(event: DragEvent): Promise<void> {
  dragOverDone.value = false
  const id = Number(event.dataTransfer?.getData("text/plain"))
  const card = props.data.items.find((item) => item.id === id)
  if (!card || card.status !== "active" || completing.value) return
  completing.value = true
  errorText.value = ""
  try {
    await completeCard(card.id)
    emit("completed", card)
    doneTip.value = `「${card.category} · ${purposeLabel(card.purpose)}」已标记完成`
    setTimeout(() => (doneTip.value = ""), 2200)
  } catch (error) {
    errorText.value = `标记完成失败：${String(error)}`
  } finally {
    completing.value = false
  }
}

function purposeLabel(purpose: string): string {
  return PURPOSE_LABELS[purpose] ?? purpose
}

/* ===== 删除已完成卡（两段式确认，防误触；不可恢复） ===== */
const confirmingDelete = ref<number | null>(null)
const deleting = ref(false)
let confirmTimer: ReturnType<typeof setTimeout> | undefined

function onDeleteClick(card: TrackingCard): void {
  if (confirmingDelete.value === card.id) {
    void performDelete(card)
  } else {
    confirmingDelete.value = card.id
    if (confirmTimer) clearTimeout(confirmTimer)
    confirmTimer = setTimeout(() => (confirmingDelete.value = null), 3000)
  }
}

async function performDelete(card: TrackingCard): Promise<void> {
  if (deleting.value) return
  deleting.value = true
  errorText.value = ""
  if (confirmTimer) clearTimeout(confirmTimer)
  confirmingDelete.value = null
  try {
    await deleteCard(card.id)
    emit("deleted", card)
    doneTip.value = `已删除卡片 #${card.id}`
    setTimeout(() => (doneTip.value = ""), 2200)
  } catch (error) {
    errorText.value = `删除失败：${String(error)}`
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <div class="board">
    <!-- 上部：泳道 -->
    <div class="board-lanes">
      <section v-for="lane in lanes" :key="lane.name" class="lane" :class="lane.laneClass">
        <header class="lane-head">
          <span class="lane-name">{{ lane.name }}</span>
          <span class="lane-count">{{ lane.cards.length }}</span>
        </header>
        <div class="lane-body">
          <div v-if="lane.cards.length === 0" class="lane-empty muted">暂无卡片</div>
          <article
            v-for="card in lane.cards"
            :key="card.id"
            class="lane-card"
            :class="{ dragging: draggingId === card.id }"
            draggable="true"
            @dragstart="onDragStart(card, $event)"
            @dragend="onDragEnd"
          >
            <div class="card-row">
              <span class="status-chip">激活</span>
              <b class="card-title">{{ card.category }} · {{ purposeLabel(card.purpose) }}</b>
            </div>
            <div v-if="card.amount || card.counterparty || card.transaction_type" class="card-meta">
              <span v-if="card.amount" class="amount" :class="card.direction === 'expense' ? 'amount-out' : 'amount-in'">
                {{ card.direction === "expense" ? "−" : "+" }}¥{{ card.amount }}
              </span>
              <span v-if="card.counterparty" class="muted">
                {{ card.direction === "expense" ? "收款人" : "付款人" }}：{{ card.counterparty }}
              </span>
              <span
                v-if="card.transaction_type && card.transaction_type !== '其他'"
                class="chip-type"
              >{{ card.transaction_type }}</span>
            </div>
            <div v-if="card.occurred_at" class="muted card-time">{{ card.occurred_at }}</div>
            <p v-if="card.note" class="card-note">{{ card.note }}</p>
            <span class="muted mono card-id">#{{ card.id }} · 拖到下方完成</span>
          </article>
        </div>
      </section>
    </div>

    <!-- 下部：已完成区（拖拽目标） -->
    <section
      class="done-zone"
      :class="{ 'drag-over': dragOverDone }"
      @dragover.prevent="dragOverDone = true"
      @dragenter.prevent="dragOverDone = true"
      @dragleave="dragOverDone = false"
      @drop.prevent="onDrop"
    >
      <header class="done-head">
        <span class="done-title">已完成</span>
        <span class="lane-count">{{ doneCards.length }}</span>
        <span class="muted done-hint">将上方泳道卡片拖拽到这里，标记为已完成</span>
      </header>

      <div v-if="doneCards.length === 0" class="done-empty muted">
        <span v-if="dragOverDone">松开即完成该卡片</span>
        <span v-else>暂无已完成卡片</span>
      </div>

      <div v-else class="done-cards">
        <article v-for="card in doneCards" :key="card.id" class="done-card">
          <span class="done-check">✓</span>
          <div class="done-body">
            <b class="done-title-text">{{ card.category }} · {{ purposeLabel(card.purpose) }}</b>
            <div v-if="card.amount || card.counterparty" class="card-meta">
              <span v-if="card.amount" class="amount" :class="card.direction === 'expense' ? 'amount-out' : 'amount-in'">
                {{ card.direction === "expense" ? "−" : "+" }}¥{{ card.amount }}
              </span>
              <span v-if="card.counterparty" class="muted">
                {{ card.direction === "expense" ? "收款人" : "付款人" }}：{{ card.counterparty }}
              </span>
            </div>
            <p v-if="card.note" class="card-note">{{ card.note }}</p>
            <span class="muted mono">#{{ card.id }} · {{ card.status === "done" ? "已完成" : card.status }}</span>
          </div>
          <button
            class="done-del"
            :class="{ armed: confirmingDelete === card.id }"
            :disabled="deleting"
            @click.stop="onDeleteClick(card)"
          >{{ confirmingDelete === card.id ? "确认删除？" : "删除" }}</button>
        </article>
      </div>
    </section>

    <!-- 拖拽结果轻提示 -->
    <Transition name="fade">
      <div v-if="doneTip" class="board-toast">{{ doneTip }}</div>
    </Transition>
    <p v-if="errorText" class="board-error">{{ errorText }}</p>
  </div>
</template>

<style scoped>
.board {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ===== 泳道 ===== */
.board-lanes {
  display: flex;
  gap: 12px;
  overflow-x: auto;
  /* 上半部分总高度固定：泳道内部卡片超限时在泳道内滚动，不再撑高看板 */
  height: 420px;
  flex-shrink: 0;
}

.lane {
  flex: 1;
  min-width: 190px;
  height: 100%;
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-md);
  padding: 10px;
  border: 1px solid var(--border);
  background: var(--surface-soft);
}

.lane-reimburse { background: var(--lane-transport-bg); border-color: var(--lane-transport-border); }
.lane-sub       { background: var(--lane-sub-bg);       border-color: var(--lane-sub-border); }
.lane-food      { background: var(--lane-food-bg);      border-color: var(--lane-food-border); }
.lane-transport { background: var(--lane-transport-bg); border-color: var(--lane-transport-border); }
.lane-aa        { background: var(--lane-shop-bg);      border-color: var(--lane-shop-border); }
.lane-other     { background: var(--lane-other-bg);     border-color: var(--lane-other-border); }

.lane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 2px 2px;
}

.lane-reimburse .lane-name { color: var(--lane-transport-text); }
.lane-sub       .lane-name { color: var(--lane-sub-text); }
.lane-food      .lane-name { color: var(--lane-food-text); }
.lane-transport .lane-name { color: var(--lane-transport-text); }
.lane-aa        .lane-name { color: var(--lane-shop-text); }
.lane-other     .lane-name { color: var(--lane-other-text); }

.lane-name {
  font-size: 12px;
  font-weight: 700;
}

.lane-count {
  font-size: 11px;
  color: var(--text-3);
  background: rgba(255, 255, 255, 0.7);
  border-radius: 999px;
  padding: 0 8px;
}

.lane-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.lane-body::-webkit-scrollbar {
  width: 6px;
}

.lane-body::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 3px;
}

.lane-body::-webkit-scrollbar-track {
  background: transparent;
}

.lane-empty {
  text-align: center;
  padding: 10px 0;
  font-size: 12px;
}

.lane-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 9px 11px;
  cursor: grab;
  user-select: none;
  transition: opacity 0.12s, transform 0.12s;
}

.lane-card:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow);
}

.lane-card.dragging {
  opacity: 0.45;
  cursor: grabbing;
}

.lane-reimburse .lane-card { border-color: var(--lane-transport-border); }
.lane-sub       .lane-card { border-color: var(--lane-sub-border); }
.lane-food      .lane-card { border-color: var(--lane-food-border); }
.lane-transport .lane-card { border-color: var(--lane-transport-border); }
.lane-aa        .lane-card { border-color: var(--lane-shop-border); }

.card-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.status-chip {
  font-size: 10px;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--primary-weak);
  color: var(--primary);
  flex-shrink: 0;
}

.card-title {
  font-size: 13px;
  color: var(--text-1);
}

.card-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 5px;
  font-size: 12px;
}

.amount {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.amount-out {
  color: var(--danger);
}

.amount-in {
  color: var(--success);
}

.chip-type {
  font-size: 10px;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--surface-soft);
  border: 1px solid var(--border);
  color: var(--text-2);
}

.card-time {
  margin-top: 4px;
  font-size: 11px;
}

.card-note {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--text-2);
  word-break: break-word;
}

.card-id {
  display: inline-block;
  margin-top: 4px;
  font-size: 11px;
}

/* ===== 已完成区 ===== */
.done-zone {
  background: var(--surface);
  border: 2px dashed var(--border-strong);
  border-radius: var(--radius-lg);
  padding: 14px 16px;
  transition: border-color 0.15s, background 0.15s;
  min-height: 96px;
}

.done-zone.drag-over {
  border-color: var(--primary);
  background: var(--primary-weak);
}

.done-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.done-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-1);
}

.done-hint {
  margin-left: auto;
}

.done-empty {
  padding: 18px 0;
  text-align: center;
  font-size: 13px;
}

.done-cards {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 12px;
}

.done-card {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  background: var(--surface-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 9px 12px;
  min-width: 200px;
}

.done-check {
  color: var(--success);
  font-weight: 700;
  font-size: 14px;
  line-height: 1.4;
}

.done-body {
  min-width: 0;
}

.done-del {
  margin-left: auto;
  flex-shrink: 0;
  align-self: center;
  font-size: 11px;
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-3);
  cursor: pointer;
  transition: all 0.15s;
}

.done-del:hover {
  color: var(--danger);
  border-color: var(--danger);
}

.done-del.armed {
  background: var(--danger);
  border-color: var(--danger);
  color: #fff;
}

.done-del:disabled {
  opacity: 0.5;
  cursor: default;
}

.done-title-text {
  font-size: 13px;
  color: var(--text-2);
}

/* ===== 拖拽轻提示 ===== */
.board-toast {
  position: fixed;
  left: 50%;
  bottom: 36px;
  transform: translateX(-50%);
  z-index: 60;
  background: var(--text-1);
  color: var(--surface);
  font-size: 13px;
  padding: 9px 18px;
  border-radius: 999px;
  box-shadow: var(--shadow);
}

.board-error {
  margin: 0;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--danger-weak);
  color: var(--danger);
  font-size: 12px;
  text-align: center;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.18s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 640px) {
  .done-hint {
    display: none;
  }
}
</style>
