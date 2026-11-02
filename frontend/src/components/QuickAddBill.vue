<script setup lang="ts">
import { computed, reactive, ref } from "vue"
import { useSessionStore } from "../stores/session"
import { addBill } from "../services/agentClient"

const store = useSessionStore()

const CATEGORIES = ["餐饮", "交通", "购物", "订阅", "医疗", "住房", "教育", "其他"] as const

const open = ref(false)
const saving = ref(false)
const savedTip = ref("")
const form = reactive({
  amount: "",
  direction: "expense" as "expense" | "income",
  category: "餐饮" as string,
  counterparty: "",
  note: "",
})

const amountValid = computed(() => {
  const n = Number(form.amount)
  return form.amount.trim() !== "" && Number.isFinite(n) && n >= 0 && /^\d+(\.\d{1,2})?$/.test(form.amount.trim())
})

function openDialog(): void {
  form.amount = ""
  form.direction = "expense"
  form.category = "餐饮"
  form.counterparty = ""
  form.note = ""
  savedTip.value = ""
  open.value = true
}

function close(): void {
  open.value = false
}

/** 随手记：确定性写入，直接 POST /api/bills 落库，不经 Agent/模型。 */
async function submit(): Promise<void> {
  if (!amountValid.value || saving.value) return
  saving.value = true
  try {
    await addBill({
      amount: Number(form.amount),
      direction: form.direction,
      category: form.category,
      merchant: form.counterparty.trim(),
      note: form.note.trim(),
    })
    savedTip.value = "已记入账单"
    // 短暂提示后关闭
    window.setTimeout(() => {
      open.value = false
      savedTip.value = ""
    }, 800)
  } catch (e) {
    savedTip.value = e instanceof Error ? e.message : "手记保存失败"
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div>
    <button class="btn" :disabled="store.busy" @click="openDialog">✍ 即时手记</button>

    <div v-if="open" class="overlay" @click.self="close">
      <div class="dialog card">
        <div class="dialog-head">
          <span class="dialog-title">即时手记</span>
          <button class="btn btn-ghost" @click="close">✕</button>
        </div>

        <div class="field">
          <label class="label">金额（元）</label>
          <input
            v-model="form.amount"
            class="input"
            type="text"
            inputmode="decimal"
            placeholder="如 80.00"
            autofocus
          />
        </div>

        <div class="field">
          <label class="label">方向</label>
          <div class="seg">
            <button
              class="seg-item"
              :class="{ active: form.direction === 'expense' }"
              @click="form.direction = 'expense'"
            >
              支出
            </button>
            <button
              class="seg-item"
              :class="{ active: form.direction === 'income' }"
              @click="form.direction = 'income'"
            >
              收入
            </button>
          </div>
        </div>

        <div class="field">
          <label class="label">分类</label>
          <div class="chips">
            <button
              v-for="c in CATEGORIES"
              :key="c"
              class="chip"
              :class="{ active: form.category === c }"
              @click="form.category = c"
            >
              {{ c }}
            </button>
          </div>
        </div>

        <div class="field">
          <label class="label">对方（可选）</label>
          <input v-model="form.counterparty" class="input" type="text" placeholder="如 张三 / 某某餐厅" />
        </div>

        <div class="field">
          <label class="label">备注（可选）</label>
          <input v-model="form.note" class="input" type="text" placeholder="如 AA 我付的款，还差张三 20" />
        </div>

        <div class="dialog-actions">
          <button class="btn" @click="close">取消</button>
          <button class="btn btn-primary" :disabled="!amountValid || saving" @click="submit">
            {{ saving ? "保存中…" : "记一笔" }}
          </button>
        </div>
        <p class="muted note">{{ savedTip || "直接写入账单，无需模型处理。" }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.dialog {
  width: min(420px, calc(100vw - 32px));
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.dialog-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.dialog-title {
  font-size: 16px;
  font-weight: 700;
}

.btn-ghost {
  border: none;
  background: transparent;
  padding: 4px 8px;
  color: var(--text-2);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-2);
}

.input {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--text-1);
  font-size: 14px;
}

.input:focus {
  outline: none;
  border-color: var(--primary);
}

.seg {
  display: flex;
  gap: 6px;
}

.seg-item {
  flex: 1;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--text-1);
  cursor: pointer;
  font-size: 14px;
}

.seg-item.active {
  border-color: var(--primary);
  background: var(--primary);
  color: #fff;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chip {
  padding: 5px 10px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
  color: var(--text-2);
  cursor: pointer;
  font-size: 13px;
}

.chip.active {
  border-color: var(--primary);
  background: var(--primary);
  color: #fff;
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 2px;
}

.note {
  margin: 0;
  font-size: 12px;
}
</style>
