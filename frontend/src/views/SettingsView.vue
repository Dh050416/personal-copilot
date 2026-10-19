<script setup lang="ts">
import NotBuiltPage from "../components/NotBuiltPage.vue"
</script>

<template>
  <NotBuiltPage
    title="设置与记忆"
    :planned="['模型切换', '分类预算', '每日晨报推送', '本地记忆管理（偏好/会话）']"
  />
</template>
