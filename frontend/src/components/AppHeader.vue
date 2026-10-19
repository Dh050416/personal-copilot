<script setup lang="ts">
const NAV_ITEMS = [
  { to: "/", label: "对话", icon: "chat" },
  { to: "/board", label: "看板", icon: "board" },
  { to: "/bills", label: "账单", icon: "bills" },
  { to: "/reports", label: "报表", icon: "reports" },
  { to: "/calendar", label: "日历", icon: "calendar" },
  { to: "/settings", label: "设置", icon: "settings" },
] as const

const ICONS: Record<string, string> = {
  chat: '<path d="M4 5h16v11H10l-6 4V5z"/>',
  board: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="4" rx="1.5"/><rect x="13" y="10" width="7" height="10" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/>',
  bills: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  reports: '<path d="M4 20V10M10 20V4M16 20v-8"/><path d="M3 20h18"/>',
  calendar: '<rect x="4" y="6" width="16" height="14" rx="2"/><path d="M4 10h16M8 4v4M16 4v4"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/>',
}
</script>

<template>
  <header class="app-header">
    <div class="brand">
      <span class="brand-mark" aria-hidden="true"></span>
      <div class="brand-text">
        <span class="brand-name">生活管家</span>
        <span class="brand-sub">Agent 实验台</span>
      </div>
    </div>
    <nav class="nav">
      <RouterLink
        v-for="item in NAV_ITEMS"
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
    </nav>
  </header>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: linear-gradient(135deg, var(--header-start), var(--header-end));
  color: #fff;
  padding: 12px 28px;
  position: sticky;
  top: 0;
  z-index: 10;
  box-shadow: 0 2px 10px rgba(15, 76, 129, 0.22);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-mark {
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.18);
  border: 1px solid rgba(255, 255, 255, 0.35);
}

.brand-text {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.brand-name {
  font-size: 17px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.brand-sub {
  font-size: 12px;
  opacity: 0.85;
}

.nav {
  display: flex;
  gap: 2px;
}

.nav-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: 999px;
  font-size: 14px;
  color: rgba(255, 255, 255, 0.88);
  text-decoration: none;
  transition: background 0.15s, color 0.15s;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.14);
  color: #fff;
}

.nav-item.router-link-active {
  background: #fff;
  color: var(--header-start);
  font-weight: 600;
}

.nav-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

@media (max-width: 860px) {
  .app-header {
    padding: 10px 14px;
    flex-direction: column;
    gap: 8px;
  }

  .nav {
    width: 100%;
    overflow-x: auto;
    justify-content: flex-start;
  }

  .brand-sub {
    display: none;
  }
}
</style>
