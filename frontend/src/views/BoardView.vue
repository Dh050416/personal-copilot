<script setup lang="ts">
import { computed, onMounted } from "vue"
import { useSessionStore } from "../stores/session"
import type { CardListData, TrackingCard } from "../tools/types"
import { useNotBuilt } from "../composables/useNotBuilt"
import NotBuiltNotice from "../components/NotBuiltNotice.vue"
import TaskBoard from "../components/TaskBoard.vue"

const store = useSessionStore()
const { notice, show } = useNotBuilt()

const cardData = computed<CardListData | undefined>(() => {
  const slot = store.renderSlots["list_tracking_cards"]
  return slot && typeof slot === "object" ? (slot as CardListData) : undefined
})

function refresh(): void {
  void store.refreshCards()
}

function onCardCompleted(card: TrackingCard): void {
  // 仅本地标记完成（store 内 status active→done），不触发全量重查，避免看板销毁闪烁
  store.markCardDone(card.id)
}

function onCardDeleted(card: TrackingCard): void {
  // 已完成卡删除：仅本地移除，不触发全量重查
  store.markCardDeleted(card.id)
}

function createCard(): void {
  show("新建跟踪卡", "请到「对话」工作台输入：创建一张…卡，确认后即生成。")
}

onMounted(() => {
  if (store.cards.length === 0) refresh()
})
</script>

<template>
  <div class="board-view">
    <div class="page-head card">
      <div>
        <h1 class="page-title">事务看板</h1>
        <p class="muted">上方泳道按用途/分类分组；将卡片拖拽到下方「已完成」区即可关闭跟踪。</p>
      </div>
      <div class="head-actions">
        <button class="btn" :disabled="store.busy" @click="refresh">
          {{ store.busy ? "查询中…" : "刷新" }}
        </button>
        <button class="btn btn-primary" @click="createCard">+ 新建跟踪卡</button>
      </div>
    </div>

    <TaskBoard v-if="cardData" :data="cardData" @completed="onCardCompleted" @deleted="onCardDeleted" />
    <section v-else class="card empty-card">
      <p class="muted">正在查询任务数据…</p>
    </section>

    <NotBuiltNotice :text="notice" />
  </div>
</template>

<style scoped>
.board-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.page-title {
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 2px;
}

.head-actions {
  display: flex;
  gap: 10px;
}

.empty-card {
  padding: 40px 22px;
  text-align: center;
}
</style>
