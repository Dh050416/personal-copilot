<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue"
import { confirmBillImport, deleteBillImport, downloadImportErrors, editImportRows, getBillImport,
  getImportRows, listBillImports, retryBillImport, startBillImport,
  type ImportDetail, type ImportJob, type ImportRow } from "../services/billImports"

const jobs = ref<ImportJob[]>([]), current = ref<ImportDetail | null>(null)
const account = ref("default"), busy = ref(false), message = ref("")
const onlyPending = ref(true), skipErrors = ref(false), rows = ref<ImportRow[]>([])
const offset = ref(0), errorsOnly = ref(false), selected = ref<number[]>([])
const category = ref("餐饮"), saveRule = ref(false), showRows = ref(false)
const categories = ["餐饮", "交通", "购物", "订阅", "医疗", "住房", "教育", "住宿", "娱乐", "生活服务", "数码服务", "其他"]
const statuses: Record<string, string> = { queued: "等待处理", processing: "正在解析与分类", review: "等待确认入库",
  failed: "处理失败", imported: "已入库" }
const natures: Record<string, string> = { consumption: "消费", transfer: "转账/收款", refund: "退款",
  red_packet: "红包", recharge: "充值", withdrawal: "提现", internal: "内部划转", unknown: "性质待确认" }
const groups = computed(() => (current.value?.groups ?? []).filter(g => !onlyPending.value || g.pending > 0))
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
async function refresh() {
  jobs.value = await listBillImports()
  if (!current.value && jobs.value[0]) current.value = await getBillImport(jobs.value[0].id)
  else if (current.value) current.value = await getBillImport(current.value.id)
}
async function poll() {
  try {
    jobs.value = await listBillImports()
    const latest = jobs.value.find(j => j.id === current.value?.id)
    if (!current.value && jobs.value[0]) current.value = await getBillImport(jobs.value[0].id)
    else if (current.value && latest && (['queued','processing'].includes(current.value.status) || latest.status !== current.value.status)) {
      current.value = await getBillImport(current.value.id)
    }
  } catch (e) { message.value = String(e) }
  if (!disposed) timer = setTimeout(() => { void poll() }, jobs.value.some(j => ['queued','processing'].includes(j.status)) ? 2500 : 10000)
}
async function act(fn: () => Promise<unknown>) {
  if (busy.value) return
  busy.value = true; message.value = ""
  try { await fn(); await refresh() } catch (e) { message.value = String(e) }
  finally { busy.value = false }
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0]; input.value = ""
  if (!file) return
  if (file.size > 20 * 1024 * 1024) { message.value = "账单文件不能超过 20MB"; return }
  await act(async () => {
    const job = await startBillImport(file, account.value)
    current.value = await getBillImport(job.id); selected.value = []; rows.value = []
    message.value = "文件已保存，后台处理期间可以继续其他操作。"
  })
}
async function choose(id: string) {
  await act(async () => { current.value = await getBillImport(id); selected.value = []; rows.value = []; offset.value = 0 })
}
async function loadRows() {
  if (current.value) rows.value = await getImportRows(current.value.id, offset.value, errorsOnly.value)
}
function selectGroup(ids: number[]) { selected.value = [...ids]; message.value = `已选择本组 ${ids.length} 笔，可批量设置分类或排除。` }
async function applyCategory() {
  if (!current.value || !selected.value.length) return
  await act(async () => { await editImportRows(current.value!.id,
    { row_ids: selected.value, category: category.value, save_rule: saveRule.value });
    message.value = "分类已确认"; if (showRows.value) await loadRows() })
}
async function exclude(excluded: boolean) {
  if (!current.value || !selected.value.length) return
  await act(async () => { await editImportRows(current.value!.id, { row_ids: selected.value, excluded }); await loadRows() })
}
async function migrateLegacy() {
  if (!current.value || !selected.value.length) return
  await act(async () => { await editImportRows(current.value!.id, { row_ids: selected.value, adopt_legacy: true }); await loadRows() })
}
async function confirm() {
  if (!current.value) return
  await act(async () => { await confirmBillImport(current.value!.id, skipErrors.value); message.value = "导入完成，可查询账单或查看报表。" })
}
onMounted(() => { void poll() })
onUnmounted(() => { disposed = true; if (timer) clearTimeout(timer) })
</script>

<template>
  <section class="card import-panel">
    <h2>账单导入与分类确认</h2>
    <p class="muted">文件先解析预览，确认后入库。个人商户用途不明时保留待确认，不按金额或人名猜测。</p>
    <div class="actions">
      <label>账户标记 <input v-model="account" class="input" maxlength="80" placeholder="同一微信账户请保持一致" /></label>
      <label class="btn">{{ busy ? "处理中…" : "上传 CSV / Excel（≤20MB）" }}
        <input type="file" accept=".xlsx,.csv" :disabled="busy || !account.trim()" hidden @change="upload" />
      </label>
    </div>
    <p class="muted">账户标记用于隔离不同账户，不是银行卡号。上传成功后刷新页面，可从任务列表继续。</p>
    <select v-if="jobs.length" class="select" :value="current?.id" :disabled="busy"
      @change="choose(($event.target as HTMLSelectElement).value)">
      <option v-for="j in jobs" :key="j.id" :value="j.id">{{ j.filename }} · {{ statuses[j.status] }} · {{ j.account }}</option>
    </select>
    <p v-if="message" role="status">{{ message }}</p>
    <template v-if="current">
      <p>{{ statuses[current.status] }} · {{ current.progress }}% <span v-if="current.error">{{ current.error }}</span></p>
      <progress v-if="['queued','processing'].includes(current.status)" :value="current.progress" max="100" />
      <p v-if="current.status === 'imported'">新增 {{ current.result.inserted }} 笔，跳过重复 {{ current.result.skipped }} 笔，排除 {{ current.result.excluded }} 笔。</p>
      <button v-if="['failed','review'].includes(current.status)" class="btn" :disabled="busy"
        @click="act(() => retryBillImport(current!.id))">继续处理待确认记录</button>
      <button v-if="current.status !== 'imported'" class="btn" :disabled="busy"
        @click="act(async () => { await deleteBillImport(current!.id); current = null; rows = [] })">放弃本次导入</button>
      <template v-if="current.status === 'review'">
        <p>新增 {{ current.counts.new }} · 重复 {{ current.counts.duplicate + current.counts.duplicate_file }} · 冲突 {{ current.counts.conflict }} · 无效 {{ current.counts.invalid }} · 已排除 {{ current.counts.excluded }} · 分类待确认 {{ current.pending }}</p>
        <p class="muted">分类不确定仍可入库。转账、红包和资金划转单独保留；明确属于消费的转账可人工设置用途。</p>
        <div class="actions">
          <label><input v-model="onlyPending" type="checkbox" /> 只看分类待确认组</label>
          <button class="btn" :disabled="busy" @click="act(() => downloadImportErrors(current!.id))">下载错误报告</button>
          <button class="btn" @click="act(async () => { showRows = !showRows; await loadRows() })">{{ showRows ? "收起" : "查看" }}全部明细 / 异常</button>
        </div>
        <div class="batch-actions">
          <span>已选 {{ selected.length }} 笔</span>
          <select v-model="category" class="select"><option v-for="c in categories" :key="c">{{ c }}</option></select>
          <label><input v-model="saveRule" type="checkbox" /> 以后同账户、同名商户及相同商品描述使用此分类</label>
          <button class="btn" :disabled="busy || !selected.length" @click="applyCategory">确认所选分类</button>
          <button class="btn" :disabled="busy || !selected.length" @click="exclude(true)">排除所选</button>
          <button class="btn" :disabled="busy || !selected.length" @click="exclude(false)">恢复所选</button>
        </div>
        <p class="muted">只有姓名时可能存在同名商户。请核对样例再保存规则；可展开明细只选择部分交易。</p>
        <div class="group-list">
          <details v-for="g in groups" :key="g.id" class="group">
            <summary>{{ g.merchant || "未提供交易对方" }} · {{ g.product || "无商品描述" }} · {{ natures[g.nature] }} · {{ g.count }} 笔 · ¥{{ (g.total_cents / 100).toFixed(2) }}</summary>
            <p>分类分布：{{ Object.entries(g.categories).map(([k,v]) => `${k} ${v}笔`).join('、') }}</p>
            <button class="btn" @click="selectGroup(g.row_ids)">选择本组 {{ g.count }} 笔</button>
            <p v-for="s in g.samples" :key="s.row_id">#{{ s.row_id }} {{ s.tx_time }} · ¥{{ (s.amount_cents/100).toFixed(2) }} · {{ s.category }} · {{ s.category_reason }}</p>
          </details>
        </div>
        <div v-if="showRows">
          <div class="actions">
            <label><input v-model="errorsOnly" type="checkbox" @change="act(async () => { offset = 0; await loadRows() })" /> 仅异常/冲突</label>
            <button class="btn" :disabled="offset === 0 || busy" @click="act(async () => { offset = Math.max(0, offset-100); await loadRows() })">上一页</button>
            <button class="btn" :disabled="rows.length < 100 || busy" @click="act(async () => { offset += 100; await loadRows() })">下一页</button>
            <button class="btn" :disabled="busy" @click="selected = rows.map(r => r.row_id)">选择本页</button>
            <button class="btn" :disabled="busy || !selected.length" @click="migrateLegacy">已核对：迁移所选旧账单的平台及账户</button>
          </div>
          <div class="row-list"><label v-for="r in rows" :key="r.row_id" class="detail-row">
            <input v-model="selected" type="checkbox" :value="r.row_id" /> #{{ r.row_id }}
            {{ r.data.tx_time }} {{ r.data.merchant }} {{ r.data.product }}
            {{ r.data.amount_cents !== undefined ? `¥${(Number(r.data.amount_cents)/100).toFixed(2)}` : '' }}
            {{ r.excluded ? '已排除' : '' }} {{ r.data.category }} {{ r.data.category_reason }} {{ r.error }}
          </label></div>
        </div>
        <div class="confirm-area">
          <label><input v-model="skipErrors" type="checkbox" /> 确认跳过异常和冲突记录，仅导入有效记录</label>
          <button class="btn btn-primary" :disabled="busy" @click="confirm">确认入库（待确认分类可稍后修正）</button>
        </div>
      </template>
    </template>
  </section>
</template>

<style scoped>
.import-panel { display: grid; gap: 12px; }
h2 { font-size: 17px; }
.actions,.batch-actions,.confirm-area { display:flex; flex-wrap:wrap; align-items:center; gap:10px; }
.batch-actions,.confirm-area { padding:12px; background:var(--surface-soft); border-radius:8px; }
.group-list,.row-list { max-height:420px; overflow:auto; }
.group { border-bottom:1px solid var(--border); padding:10px; }
.group summary { cursor:pointer; overflow-wrap:anywhere; }
.group p { margin:8px 0; }
.detail-row { display:block; padding:8px; border-bottom:1px solid var(--border); overflow-wrap:anywhere; }
.input { max-width:240px; }
progress { width:100%; }
</style>
