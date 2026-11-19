<script setup lang="ts">
import { computed } from "vue"
import { useSessionStore } from "../stores/session"
import type { QueryBillData } from "../tools/types"
import BillImportPanel from "../components/BillImportPanel.vue"
import { useNotBuilt } from "../composables/useNotBuilt"
import NotBuiltNotice from "../components/NotBuiltNotice.vue"
import BillTable from "../components/BillTable.vue"
import QuickAddBill from "../components/QuickAddBill.vue"

const store = useSessionStore()
const { notice, show } = useNotBuilt()
const QUICK_QUERIES = [
  { label: "本月支出", text: "统计这个月支出" },
  { label: "上月支出", text: "查询上个月支出" },
  { label: "本月餐饮", text: "查看这个月餐饮支出" },
  { label: "全部账单", text: "查询账单" },
] as const

const queryData = computed<QueryBillData | undefined>(() => {
  const slot = store.renderSlots["query_bill"]
  return slot && typeof slot === "object" ? (slot as QueryBillData) : undefined
})

function quickQuery(text: string): void {
  void store.run(text)
}

</script>

<template>
  <div class="bills-view">
    <section class="card bills-head">
      <div class="head-row">
        <div>
          <h1 class="page-title">账单管理</h1>
          <p class="muted">导入、查询与修正账单；原始字段保留，交易按单号去重。</p>
        </div>
        <div class="actions">
          <QuickAddBill />
          <button class="btn" @click="show('OCR 截图')">📷 OCR 截图</button>
          <button class="btn" @click="show('搜索')">🔍 搜索</button>

        </div>
      </div>

      <div class="quick-row">
        <span class="quick-label">快捷查询</span>
        <button
          v-for="query in QUICK_QUERIES"
          :key="query.text"
          class="btn"
          :disabled="store.busy"
          @click="quickQuery(query.text)"
        >
          {{ query.label }}
        </button>
        <span class="muted">查询走 Agent 链路，可在「对话」页查看轨迹。</span>
      </div>

    </section>

    <BillImportPanel />
    <BillTable v-if="queryData" :data="queryData" />
    <section v-else class="card empty-card">
      <p class="muted">还没有账单数据。先「上传账单」，或使用快捷查询。</p>
    </section>

    <NotBuiltNotice :text="notice" />
  </div>
</template>

<style scoped>
.bills-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.head-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.page-title {
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 2px;
}

.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.quick-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}

.quick-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-2);
}

.empty-card {
  padding: 40px 22px;
  text-align: center;
}
</style>
