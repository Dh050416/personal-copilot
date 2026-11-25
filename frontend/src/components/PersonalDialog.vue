<script setup lang="ts">
import { onMounted, ref } from "vue"

const props = defineProps<{ title: string; drawer?: boolean; busy?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
onMounted(() => dialog.value?.showModal())

function close() {
  if (!props.busy) emit("close")
}
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="personal-dialog" :class="{ drawer }" :aria-label="title"
      @cancel.prevent="close" @click.self="close">
      <div class="personal-dialog-inner">
        <header>
          <h2>{{ title }}</h2>
          <button class="btn close" type="button" aria-label="关闭" :disabled="busy" @click="close">×</button>
        </header>
        <slot />
      </div>
    </dialog>
  </Teleport>
</template>

<style scoped>
dialog {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  color: var(--text-1);
  background: var(--surface);
  width: min(540px, calc(100vw - 26px));
  max-height: calc(100dvh - 28px);
  padding: 0;
  overscroll-behavior: contain;
}
dialog::backdrop { background: #18181b42; }
dialog.drawer {
  margin: 0 0 0 auto;
  height: 100dvh;
  max-height: 100dvh;
  width: min(510px, 100vw);
  max-width: 100vw;
  border-radius: var(--radius-lg) 0 0 var(--radius-lg);
}
.personal-dialog-inner { padding: 20px; }
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 12px;
  margin-bottom: 16px;
  border-bottom: 1px solid var(--border);
}
h2 { font-size: 18px; }
.close { font-size: 20px; line-height: 1; padding: 6px 10px; }
</style>
