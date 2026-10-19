<script setup lang="ts">
import { ref } from "vue"
import { useSessionStore } from "../stores/session"
import { classifyExisting, importBills } from "../services/agentClient"

const store = useSessionStore()
const file = ref<File | null>(null)

async function upload(): Promise<void> {
  if (!file.value || store.busy) return
  store.importMessage = "正在导入并分类，请稍候…"
  store.busy = true
  try {
    const report = await importBills(file.value)
    store.importMessage = JSON.stringify(report, null, 2)
  } catch (error) {
    store.importMessage = String(error)
  } finally {
    store.busy = false
  }
}

async function classify(): Promise<void> {
  if (store.busy) return
  store.importMessage = "正在分类已有账单；人工分类不会被覆盖…"
  store.busy = true
  try {
    const report = await classifyExisting(200)
    store.importMessage = `${JSON.stringify(report, null, 2)}\n分类已处理，请重新查询查看结果。`
  } catch (error) {
    store.importMessage = String(error)
  } finally {
    store.busy = false
  }
}
</script>

<template>
  <section class="card">
    <div class="card-title">
      <span>账单导入</span>
      <span class="hint">.xlsx / .csv</span>
    </div>

    <div class="file-row">
      <label class="file-pick">
        <input
          type="file"
          accept=".xlsx,.csv"
          :disabled="store.busy"
          @change="file = ($event.target as HTMLInputElement).files?.[0] ?? null"
        />
        <span class="btn">{{ file ? file.name : "选择文件" }}</span>
      </label>
      <button class="btn btn-primary" :disabled="store.busy || !file" @click="upload">
        导入并自动分类
      </button>
    </div>

    <button class="btn classify-btn" :disabled="store.busy" @click="classify">
      分类已有未分类账单
    </button>

    <p class="muted note">
      旧账单可重新上传原文件补齐支付方式和交易状态；相同交易不会重复插入。转账、红包等资金流记录默认保持待分类。
    </p>

    <pre v-if="store.importMessage" class="mono report">{{ store.importMessage }}</pre>
  </section>
</template>

<style scoped>
.file-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.file-pick input {
  display: none;
}

.file-pick .btn {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.classify-btn {
  margin-bottom: 10px;
}

.note {
  margin: 0 0 12px;
}

.report {
  margin: 0;
  background: var(--surface-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px;
  font-size: 12px;
  max-height: 300px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
