<script setup lang="ts">
import { computed } from "vue"
import { useRoute } from "vue-router"
import AppHeader from "./components/AppHeader.vue"

const route = useRoute()
// 登录/注册页是独立全屏页面，不显示主导航侧栏
const isAuthPage = computed(() => route.name === "login")
</script>

<template>
  <div class="app-shell" :class="{ 'is-auth': isAuthPage }">
    <AppHeader v-if="!isAuthPage" />
    <main class="app-main">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  min-height: 100vh;
}

.app-main {
  flex: 1;
  min-width: 0;
  padding: 28px;
  max-width: 1280px;
  width: 100%;
}

/* 登录页铺满视口，不加主区内边距与宽度限制 */
.app-shell.is-auth .app-main {
  padding: 0;
  max-width: none;
}

@media (max-width: 860px) {
  .app-main {
    padding: 16px 14px 32px;
  }

  .app-shell.is-auth .app-main {
    padding: 0;
  }
}
</style>
