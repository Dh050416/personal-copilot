<script setup lang="ts">
defineProps<{ text: string }>()
</script>

<template>
  <Transition name="fade">
    <div v-if="text" class="not-built-notice">
      <span class="badge badge-gray">未开发</span>
      <span>{{ text }}</span>
    </div>
  </Transition>
</template>

<style scoped>
.not-built-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding: 9px 12px;
  font-size: 13px;
  color: var(--text-2);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
