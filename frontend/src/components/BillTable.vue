<script setup lang="ts">
import { ref, watch } from "vue"
import type { BillRow, QueryBillData } from "../tools/types"
import { confirmCategory } from "../services/agentClient"

const props = defineProps<{ data: QueryBillData }>()

const CATEGORIES = ["餐饮", "交通", "购物", "订阅", "医疗", "住房", "教育", "其他"]
const SOURCE_LABELS: Record<string, string> = {
  rule: "规则推断",
  llm: "模型推断",
  user: "已人工确认",
  source: "文件提供",
  legacy: "历史分类",
  auto: "待分类",
  unclassified: "待分类",
}
const DIRECTION_LABELS: Record<string, string> = {
  expense: "支出",
  income: "收入",
  neutral: "不计收支",
}

const editedCategories = ref<Record<number, string>>({})
const savingId = ref<number | null>(null)
const rowMessage = ref("")

watch(
  () => props.data,
  (data) => {
    editedCategories.value = Object.fromEntries(data.items.map((row) => [row.id, row.category]))
    rowMessage.value = ""
  },
  { immediate: true },
)

async function confirm(row: BillRow): Promise<void> {
  if (savingId.value !== null) return
  savingId.value = row.id
  rowMessage.value = ""
  try {
    const updated = (await confirmCategory(row.id, editedCategories.value[row.id])) as Partial<BillRow>
    Object.assign(row, updated)
    rowMessage.value = "分类已确认，人工分类不会被后续导入或模型覆盖。"
  } catch (error) {
    rowMessage.value = `保存失败：${String(error)}`
  } finally {
    savingId.value = null
  }
}
</script>

<template>
  <section class="card">
    <div class="card-title">
      <span>匹配账单</span>
      <span class="hint">共 {{ data.count }} 笔，金额合计 {{ data.total }} 元（按原始账单金额，未做退款抵扣）</span>
    </div>

    <div v-if="data.warnings.length" class="warnings">
      <p v-for="(warning, index) in data.warnings" :key="index" class="warning">{{ warning }}</p>
    </div>
    <p v-if="rowMessage" class="row-message">{{ rowMessage }}</p>

    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>时间 / ID</th>
            <th>对方 / 商品</th>
            <th>金额 / 收支</th>
            <th>交易类型</th>
            <th>支付方式 / 状态</th>
            <th>消费分类</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in data.items" :key="row.id">
            <td>
              <span class="mono">{{ row.tx_time.replace("T", " ") }}</span>
              <br /><span class="muted">#{{ row.id }}</span>
            </td>
            <td>
              <span class="cell-main">{{ row.merchant }}</span>
              <br /><span class="muted">{{ row.product }}</span>
            </td>
            <td>
              <span class="cell-main">{{ row.amount }} 元</span>
              <br /><span class="muted">{{ DIRECTION_LABELS[row.direction] ?? row.direction }}</span>
            </td>
            <td>{{ row.transaction_type }}</td>
            <td>
              {{ row.payment_method }}
              <br /><span class="muted">{{ row.transaction_status }}</span>
            </td>
            <td>
              <span class="badge" :class="row.category_source === 'user' ? 'badge-green' : 'badge-gray'">
                {{ SOURCE_LABELS[row.category_source] ?? row.category_source }}
              </span>
              <div class="category-edit">
                <select
                  class="select"
                  v-model="editedCategories[row.id]"
                  :disabled="savingId !== null"
                >
                  <option v-if="!CATEGORIES.includes(row.category)" :value="row.category">
                    {{ row.category }}
                  </option>
                  <option v-for="category in CATEGORIES" :key="category" :value="category">
                    {{ category }}
                  </option>
                </select>
                <button class="btn" :disabled="savingId !== null" @click="confirm(row)">
                  确认
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <p class="muted foot">
      {{ data.truncated ? `明细已截断，仅显示最近 ${data.returned_count} 条。` : `显示全部 ${data.returned_count} 条明细。` }}
      若本次按分类筛选，确认分类后请重新查询更新统计。
    </p>
  </section>
</template>

<style scoped>
.warnings {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 10px;
}

.warning {
  margin: 0;
  font-size: 12px;
  color: var(--warning);
  background: var(--warning-weak);
  border-radius: var(--radius-sm);
  padding: 6px 10px;
}

.row-message {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--success);
}

.table-scroll {
  overflow-x: auto;
}

table {
  border-collapse: collapse;
  width: 100%;
  font-size: 13px;
}

th,
td {
  padding: 10px 12px;
  text-align: left;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
  min-width: 96px;
}

th {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-3);
  background: var(--surface-soft);
}

tbody tr:hover {
  background: var(--surface-hover);
}

.cell-main {
  color: var(--text-1);
  font-weight: 500;
}

.category-edit {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
}

.category-edit .select {
  width: 110px;
  padding: 6px 8px;
  font-size: 13px;
}

.category-edit .btn {
  padding: 6px 12px;
  font-size: 13px;
}

.foot {
  margin: 12px 0 0;
}
</style>
