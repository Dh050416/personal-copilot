<script setup lang="ts">
import { useSessionStore } from "../stores/session"

const store = useSessionStore()

function approve(): void {
  void store.decide("approve")
}

function reject(): void {
  void store.decide("reject")
}
</script>

<template>
  <section v-if="store.approval" class="card approval-card">
    <div class="card-title">
      <span class="approval-title">
        <span class="badge badge-amber">人在环确认</span>
        <span>写操作需要你的确认</span>
      </span>
    </div>

    <div class="approval-meta">
      <div class="meta-row">
        <span class="muted">工具</span>
        <span class="mono">{{ store.approval.tool }}</span>
      </div>
      <div class="meta-row">
        <span class="muted">审批 ID</span>
        <span class="mono">{{ store.approval.approvalId }}</span>
      </div>
    </div>

    <pre class="mono args">{{ JSON.stringify(store.approval.args, null, 2) }}</pre>

    <div class="approval-actions">
      <button class="btn btn-approve" :disabled="store.busy" @click="approve">确认执行</button>
      <button class="btn btn-danger" :disabled="store.busy" @click="reject">拒绝</button>
    </div>
    <p class="muted note">确认后参数会再次校验；执行参数与确认参数不一致时会被拒绝。</p>
  </section>
</template>

<style scoped>
.approval-card {
  border-color: #f3d9a4;
  background: #fffdf7;
}

.approval-title {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.approval-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
}

.meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.args {
  margin: 0 0 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px;
  font-size: 12px;
  max-height: 220px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.approval-actions {
  display: flex;
  gap: 10px;
}

.note {
  margin: 10px 0 0;
}
</style>
