<script setup lang="ts">
import SessionList from "../components/SessionList.vue"
import ChatPanel from "../components/ChatPanel.vue"
import ApprovalCard from "../components/ApprovalCard.vue"
</script>

<template>
  <div class="chat-view">
    <SessionList class="col-session" />
    <div class="col-chat">
      <ChatPanel />
      <ApprovalCard />
    </div>
  </div>
</template>

<style scoped>
.chat-view {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 16px;
  align-items: stretch;
  /* 对话区域高度恒定：头部约 64px + 页面上下留白，剩余空间给对话区，内部滚动 */
  height: calc(100vh - 150px);
}

.col-session {
  align-self: stretch;
  min-height: 0;
}

.col-chat {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
  min-height: 0;
  height: 100%;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
  padding: 18px 20px;
}

@media (max-width: 960px) {
  .chat-view {
    grid-template-columns: 1fr;
    /* 窄屏同样保持对话区恒定高度（内部滚动），否则内容会撑高整个页面 */
    height: calc(100vh - 150px);
  }

  .col-session {
    display: none;
  }
}
</style>
