<script setup lang="ts">
import { computed } from "vue"
import { useSessionStore } from "../stores/session"

const store = useSessionStore()

/** 会话列表：新的在上 */
const ordered = computed(() =>
  [...store.sessions].sort((a, b) => b.updatedAt - a.updatedAt),
)

function timeLabel(ts: number): string {
  const d = new Date(ts)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  return sameDay
    ? d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : `${d.getMonth() + 1}/${d.getDate()}`
}
</script>

<template>
  <aside class="session-panel">
    <div class="session-title">会话</div>

    <button class="btn session-new" @click="store.newSession()">+ 新会话</button>

    <div class="session-list">
      <button
        v-for="s in ordered"
        :key="s.id"
        class="session-item"
        :class="{ active: s.id === store.sessionId }"
        @click="store.switchSession(s.id)"
      >
        <span class="session-dot" aria-hidden="true"></span>
        <span class="session-item-body">
          <span class="session-item-title">{{ s.title }}</span>
          <span class="muted session-item-time">{{ timeLabel(s.updatedAt) }}</span>
        </span>
      </button>
    </div>

    <p v-if="ordered.length === 0" class="muted session-hint">暂无历史会话</p>
  </aside>
</template>

<style scoped>
.session-panel {
  background: var(--surface-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 14px 12px;
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 100%;
  overflow: hidden;
}

.session-title {
  font-weight: 700;
  color: var(--text-1);
  font-size: 13px;
}

.session-new {
  width: 100%;
  border-style: dashed;
  color: var(--text-3);
}

.session-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  overflow-y: auto;
  min-height: 0;
}

.session-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  text-align: left;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  cursor: pointer;
  color: var(--text-1);
}

.session-item:hover {
  background: var(--surface);
  border-color: var(--border);
}

.session-item.active {
  background: var(--primary-weak);
  border-color: var(--primary);
}

.session-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-3);
  flex-shrink: 0;
}

.session-item.active .session-dot {
  background: var(--primary);
}

.session-item-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.session-item-title {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-item-time {
  font-size: 11px;
}

.session-hint {
  margin: 0;
}
</style>
