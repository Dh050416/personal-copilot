<script setup lang="ts">
import AppHeader from "./components/AppHeader.vue"
</script>

<template>
  <div class="app-shell">
    <AppHeader />
    <main class="app-main">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.app-main {
  flex: 1;
  padding: 24px 32px 48px;
  max-width: 1280px;
  width: 100%;
  margin: 0 auto;
}

@media (max-width: 640px) {
  .app-main {
    padding: 16px 14px 32px;
  }
}
</style>
