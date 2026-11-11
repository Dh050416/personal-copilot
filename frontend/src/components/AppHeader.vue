<script setup lang="ts">
import { computed } from "vue"
import { useRouter } from "vue-router"
import { getCurrentUser, logout } from "../services/agentClient"

const router = useRouter()

const username = computed(() => getCurrentUser()?.username ?? "")

function handleLogout(): void {
  logout()
  router.replace({ name: "login" })
}

// 分组导航：工作台 / 数据 / 系统
const NAV_GROUPS = [
  {
    section: "工作台",
    items: [
      { to: "/", label: "对话", icon: "chat" },
      { to: "/board", label: "看板", icon: "board" },
      { to: "/calendar", label: "日历", icon: "calendar" },
    ],
  },
  {
    section: "数据",
    items: [
      { to: "/bills", label: "账单", icon: "bills" },
      { to: "/reports", label: "报表", icon: "reports" },
      { to: "/knowledge", label: "知识库", icon: "knowledge" },
    ],
  },
  {
    section: "系统",
    items: [
      { to: "/settings", label: "设置", icon: "settings" },
    ],
  },
] as const

const ICONS: Record<string, string> = {
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  board: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="4" rx="1.5"/><rect x="13" y="10" width="7" height="10" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/>',
  bills: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  reports: '<path d="M4 20V10M10 20V4M16 20v-8"/><path d="M3 20h18"/>',
  calendar: '<rect x="4" y="6" width="16" height="14" rx="2"/><path d="M4 10h16M8 4v4M16 4v4"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/>',
  knowledge: '<path d="M4 4h6l2 2h8v12H4z"/><path d="M8 12h8M8 16h5"/>',
}

// 用户名首字母做头像
const avatarLetter = computed(() => (username.value || "U").charAt(0).toUpperCase())
</script>

<template>
  <aside class="sidebar">
    <div class="logo">
      <div class="logo-mark">A</div>
      <span class="logo-text">Agent</span>
    </div>

    <nav class="nav">
      <template v-for="group in NAV_GROUPS" :key="group.section">
        <div class="nav-section">{{ group.section }}</div>
        <RouterLink
          v-for="item in group.items"
          :key="item.to"
          :to="item.to"
          class="nav-item"
        >
          <svg
            class="nav-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
            v-html="ICONS[item.icon]"
          ></svg>
          <span>{{ item.label }}</span>
        </RouterLink>
      </template>
    </nav>

    <div class="sidebar-footer">
      <div class="avatar" :title="username">{{ avatarLetter }}</div>
      <div class="user-meta">
        <div class="user-name">{{ username || "未登录" }}</div>
        <button class="logout-btn" @click="handleLogout">退出</button>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 220px;
  flex-shrink: 0;
  background: var(--sidebar-bg);
  border-right: 1px solid var(--sidebar-border);
  padding: 20px 12px;
  display: flex;
  flex-direction: column;
  height: 100vh;
  position: sticky;
  top: 0;
}

.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 10px 20px;
  font-weight: 600;
  font-size: 15px;
}

.logo-mark {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  background: var(--accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
}

.logo-text {
  color: var(--text-1);
}

.nav {
  flex: 1;
  overflow-y: auto;
}

.nav-section {
  font-size: 11px;
  color: var(--text-3);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 16px 10px 6px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  color: var(--text-2);
  text-decoration: none;
  font-size: 14px;
  margin-bottom: 2px;
  transition: background 0.12s, color 0.12s;
}

.nav-item:hover {
  background: var(--surface-hover);
  color: var(--text-1);
}

.nav-item.router-link-active {
  background: var(--accent);
  color: #fff;
}

.nav-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.sidebar-footer {
  margin-top: auto;
  padding: 12px 10px;
  border-top: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 10px;
}

.avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: #d4d4d8;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
  flex-shrink: 0;
}

.user-meta {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.user-name {
  font-size: 13px;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logout-btn {
  border: none;
  background: transparent;
  color: var(--text-3);
  font-size: 12px;
  padding: 0;
  cursor: pointer;
  text-align: left;
  font-family: var(--font);
}

.logout-btn:hover {
  color: var(--danger);
}

/* 窄屏：侧栏隐藏，由顶部简易导航兜底（此处仅隐藏，保持各页面可访问） */
@media (max-width: 860px) {
  .sidebar {
    display: none;
  }
}
</style>
