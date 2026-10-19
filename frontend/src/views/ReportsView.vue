<script setup lang="ts">
import NotBuiltPage from "../components/NotBuiltPage.vue"
</script>

<template>
  <NotBuiltPage
    title="消费报表"
    :planned="['支出分类占比图', '环比/同比对比', 'Agent 消费洞察', '导出月报 PDF / MD']"
  />
</template>
