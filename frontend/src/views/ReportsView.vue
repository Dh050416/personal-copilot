<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { getReportSummary, type ReportSummary } from "../services/agentClient"

const summary = ref<ReportSummary | null>(null)
const loading = ref(false)
const error = ref("")

// 日期区间（默认全部；可选过滤）
const range = ref({ start: "", end: "" })

async function load(): Promise<void> {
  loading.value = true
  error.value = ""
  try {
    const params: { start_date?: string; end_date?: string } = {}
    if (range.value.start) params.start_date = range.value.start
    if (range.value.end) params.end_date = range.value.end
    summary.value = await getReportSummary(params)
  } catch (e) {
    error.value = e instanceof Error ? e.message : "报表加载失败"
  } finally {
    loading.value = false
  }
}

function resetRange(): void {
  range.value = { start: "", end: "" }
  void load()
}

// 分类占比：支出总额为分母，算百分比用于条宽
const totalExpenseCents = computed(() => summary.value?.total_expense_cents ?? 0)
const categories = computed<Array<{ category: string; amount: string; amount_cents: number; pct: number }>>(() => {
  const cats = summary.value?.categories ?? []
  if (totalExpenseCents.value <= 0) {
    return cats.map((c) => ({ ...c, pct: 0 }))
  }
  return cats.map((c) => ({
    ...c,
    pct: Math.round((c.amount_cents / totalExpenseCents.value) * 100),
  }))
})

// 月度柱状图：按最大月支出归一化高度
const maxMonthExpense = computed(() =>
  Math.max(1, ...(summary.value?.monthly.map((m) => m.expense_cents) ?? [1])),
)
const monthly = computed(() =>
  (summary.value?.monthly ?? []).map((m) => ({
    ...m,
    height: Math.max(2, Math.round((m.expense_cents / maxMonthExpense.value) * 100)),
  })),
)

// 简单颜色板（按分类稳定映射，避免随机色跳动）
const CAT_COLORS = ["#185FA5", "#1D9E75", "#D85A30", "#7F77DD", "#BA7517", "#D4537E", "#5F5E5A", "#378ADD"]
function catColor(category: string): string {
  let h = 0
  for (const ch of category) h = (h * 31 + ch.charCodeAt(0)) % CAT_COLORS.length
  return CAT_COLORS[h]
}

function fmt(iso: string): string {
  return iso ? iso.replace("T", " ").slice(0, 19) : ""
}

onMounted(load)
</script>

<template>
  <div class="reports-view">
    <div class="page-head card">
      <div>
        <h1 class="page-title">消费报表</h1>
        <p class="muted">基于已导入账单的收支统计；纯本地聚合，不经过模型。</p>
      </div>
      <div class="head-actions">
        <input v-model="range.start" type="date" class="input date-input" title="开始日期" />
        <span class="muted">至</span>
        <input v-model="range.end" type="date" class="input date-input" title="结束日期" />
        <button class="btn" :disabled="loading" @click="load">{{ loading ? "加载中…" : "应用" }}</button>
        <button class="btn" :disabled="loading" @click="resetRange">全部</button>
      </div>
    </div>

    <p v-if="error" class="card error-text">{{ error }}</p>
    <div v-else-if="loading && !summary" class="card empty">加载中…</div>

    <template v-else-if="summary">
      <!-- 汇总卡片 -->
      <div class="metric-grid">
        <div class="card metric">
          <span class="metric-label">总支出</span>
          <span class="metric-num">¥{{ summary.total_expense }}</span>
        </div>
        <div class="card metric">
          <span class="metric-label">总收入</span>
          <span class="metric-num income">¥{{ summary.total_income }}</span>
        </div>
        <div class="card metric">
          <span class="metric-label">净结余</span>
          <span class="metric-num">{{ (summary.total_income_cents - summary.total_expense_cents) >= 0 ? "¥" : "-¥"
            }}{{ ((summary.total_income_cents - summary.total_expense_cents) / 100).toFixed(2).replace("-", "") }}</span>
        </div>
        <div class="card metric">
          <span class="metric-label">账单笔数</span>
          <span class="metric-num">{{ summary.bill_count }}</span>
        </div>
      </div>

      <!-- 分类占比 -->
      <section class="card">
        <h2 class="section-title">支出分类占比</h2>
        <div v-if="categories.length === 0" class="empty">暂无支出数据</div>
        <div v-else class="cat-list">
          <div v-for="c in categories" :key="c.category" class="cat-row">
            <span class="cat-name">{{ c.category }}</span>
            <div class="cat-bar-track">
              <div class="cat-bar" :style="{ width: c.pct + '%', background: catColor(c.category) }" />
            </div>
            <span class="cat-amount">¥{{ c.amount }}</span>
            <span class="cat-pct">{{ c.pct }}%</span>
          </div>
        </div>
      </section>

      <!-- 月度趋势 -->
      <section class="card">
        <h2 class="section-title">月度收支趋势</h2>
        <div v-if="monthly.length === 0" class="empty">暂无月度数据</div>
        <div v-else class="month-chart">
          <div v-for="m in monthly" :key="m.month" class="month-col">
            <div class="month-bars">
              <div class="month-bar expense" :style="{ height: m.height + '%' }" :title="`支出 ¥${m.expense}`" />
            </div>
            <div class="month-label">{{ m.month.slice(5) }}月</div>
            <div class="month-val">¥{{ m.expense }}</div>
          </div>
        </div>
        <div class="legend">
          <span class="legend-item"><span class="swatch expense" />支出</span>
          <span class="legend-item"><span class="swatch income" />收入</span>
        </div>
        <div v-if="monthly.length" class="month-detail">
          <table class="month-table">
            <thead>
              <tr><th>月份</th><th>支出</th><th>收入</th><th>结余</th></tr>
            </thead>
            <tbody>
              <tr v-for="m in monthly" :key="m.month">
                <td>{{ m.month }}</td>
                <td>¥{{ m.expense }}</td>
                <td>¥{{ m.income }}</td>
                <td :class="{ neg: m.income_cents - m.expense_cents < 0 }">
                  {{ (m.income_cents - m.expense_cents) >= 0 ? "¥" : "-¥"
                  }}{{ ((m.income_cents - m.expense_cents) / 100).toFixed(2).replace("-", "") }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.reports-view {
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
  flex-wrap: wrap;
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
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.date-input {
  width: auto;
}
.input {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 6px 8px;
  font-size: 13px;
  background: var(--surface);
  color: var(--text-1);
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
.section-title {
  font-size: 15px;
  margin-bottom: 14px;
  color: var(--text-1);
}

/* 汇总卡片 */
.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}
@media (max-width: 720px) {
  .metric-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
.metric {
  padding: 16px 18px;
}
.metric-label {
  display: block;
  font-size: 13px;
  color: var(--text-2);
}
.metric-num {
  display: block;
  margin-top: 6px;
  font-size: 22px;
  font-weight: 600;
  color: var(--text-1);
}
.metric-num.income {
  color: var(--success);
}

/* 分类占比 */
.cat-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.cat-row {
  display: grid;
  grid-template-columns: 72px 1fr 90px 48px;
  align-items: center;
  gap: 12px;
}
.cat-name {
  font-size: 13px;
  color: var(--text-1);
  text-align: right;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cat-bar-track {
  height: 18px;
  background: var(--surface-soft);
  border-radius: 4px;
  overflow: hidden;
}
.cat-bar {
  height: 100%;
  border-radius: 4px;
  transition: width 0.4s ease;
}
.cat-amount {
  font-size: 13px;
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.cat-pct {
  font-size: 12px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}

/* 月度柱状图 */
.month-chart {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  height: 180px;
  padding-bottom: 4px;
  overflow-x: auto;
}
.month-col {
  flex: 1;
  min-width: 48px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  height: 100%;
  justify-content: flex-end;
}
.month-bars {
  width: 28px;
  height: 130px;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}
.month-bar {
  width: 100%;
  border-radius: 4px 4px 0 0;
}
.month-bar.expense {
  background: var(--primary);
}
.month-label {
  font-size: 12px;
  color: var(--text-2);
}
.month-val {
  font-size: 11px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}
.legend {
  display: flex;
  gap: 16px;
  margin-top: 12px;
  font-size: 12px;
  color: var(--text-2);
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.swatch {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  display: inline-block;
}
.swatch.expense {
  background: var(--primary);
}
.swatch.income {
  background: var(--success);
}

/* 月度明细表 */
.month-detail {
  margin-top: 16px;
  overflow-x: auto;
}
.month-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.month-table th,
.month-table td {
  text-align: right;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  font-variant-numeric: tabular-nums;
}
.month-table th:first-child,
.month-table td:first-child {
  text-align: left;
}
.month-table th {
  color: var(--text-2);
  font-weight: 500;
  background: var(--surface-soft);
}
.month-table td.neg {
  color: var(--danger);
}
</style>
