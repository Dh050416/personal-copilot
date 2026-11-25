<script setup lang="ts">
import { ref } from "vue"
import { usePersonalReminders } from "../stores/personalReminders"
import { errorText, formatTime } from "../composables/personalFormat"

const reminders = usePersonalReminders()
const menu = ref<HTMLDetailsElement>()
const updating = ref<number | null>(null)
const error = ref("")

async function markRead(id: number) {
  updating.value = id
  error.value = ""
  try {
    await reminders.read(id)
  } catch (cause) {
    error.value = errorText(cause)
  } finally {
    updating.value = null
  }
}
</script>

<template>
  <div class="personal-area">
    <header class="personal-header">
      <h1>个人规划</h1>
      <details ref="menu" class="personal-reminders" @toggle="menu?.open && reminders.refresh()">
        <summary class="btn" aria-label="任务提醒">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
            <path d="M18 8a6 6 0 0 0-12 0c0 7-2 7-2 9h16c0-2-2-2-2-9M10 21h4" />
          </svg>
          提醒 <span v-if="reminders.items.length" class="badge badge-amber">{{ reminders.items.length }}</span>
        </summary>
        <div class="card personal-reminder-panel">
          <h2>任务提醒</h2>
          <p v-if="error || reminders.error" class="personal-error" role="alert">{{ error || reminders.error }}</p>
          <p v-if="!reminders.items.length" class="personal-muted">暂无未读提醒</p>
          <div v-for="item in reminders.items" :key="item.id" class="personal-reminder-row">
            <div>
              <RouterLink :to="{ name: 'personal-tasks', query: { task: item.task_id } }" @click="menu && (menu.open = false)">
                {{ item.task_title }}
              </RouterLink>
              <small>{{ item.kind === "follow_up" ? "跟进提醒" : "开工提醒" }} · {{ formatTime(item.fire_at) }}</small>
            </div>
            <button class="btn" :disabled="updating !== null" @click="markRead(item.id)">标为已读</button>
          </div>
        </div>
      </details>
    </header>

    <nav class="personal-tabs" aria-label="个人规划">
      <RouterLink :to="{ name: 'personal-tasks' }">个人任务</RouterLink>
      <RouterLink :to="{ name: 'personal-goals' }">长期目标</RouterLink>
      <RouterLink :to="{ name: 'personal-reviews' }">复盘记录</RouterLink>
    </nav>

    <RouterView />
  </div>
</template>

<style src="../styles/personal.css"></style>
