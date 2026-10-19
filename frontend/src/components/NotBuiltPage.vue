<script setup lang="ts">
defineProps<{ title: string; planned: string[] }>()
</script>

<template>
  <section class="card notbuilt">
    <div class="mark">规划中</div>
    <h1 class="title">{{ title }}</h1>
    <p class="desc muted">该模块尚未开发。</p>
    <p class="planned muted">规划能力：{{ planned.join(" · ") }}</p>
  </section>
</template>

<style scoped>
.notbuilt {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 72px 24px;
  text-align: center;
  gap: 8px;
}

.mark {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: var(--neutral-weak);
  border: 1px dashed var(--border-strong);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
  color: var(--text-3);
  margin-bottom: 6px;
}

.title {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-1);
}

.desc,
.planned {
  margin: 0;
}

.planned {
  max-width: 460px;
  line-height: 1.8;
}
</style>
