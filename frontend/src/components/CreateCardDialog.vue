<script setup lang="ts">
import { computed, reactive, ref } from "vue"
import { createTrackingCard, type CreateCardInput } from "../services/agentClient"

const emit = defineEmits<{ created: [] }>()

const PURPOSES = [
  { value: "报销", label: "报销" },
  { value: "退款", label: "退款" },
  { value: "待退订", label: "待退订" },
  { value: "AA", label: "AA" },
  { value: "待处理", label: "待处理" },
] as const

const CATEGORIES = ["餐饮", "交通", "购物", "订阅", "医疗", "住房", "教育", "其他"] as const

const open = ref(false)
const saving = ref(false)
const tip = ref("")

const form = reactive({
  purpose: "报销" as CreateCardInput["purpose"],
  category: "其他" as string,
  direction: "expense" as "expense" | "income",
  amount: "",
  counterparty: "",
  note: "",
})

const amountValid = computed(() => {
  if (form.amount.trim() === "") return true // 金额可选
  const n = Number(form.amount)
  return Number.isFinite(n) && n >= 0 && /^\d+(\.\d{1,2})?$/.test(form.amount.trim())
})

function openDialog(): void {
  form.purpose = "报销"
  form.category = "其他"
  form.direction = "expense"
  form.amount = ""
  form.counterparty = ""
  form.note = ""
  tip.value = ""
  open.value = true
}

function close(): void {
  open.value = false
}

async function submit(): Promise<void> {
  if (!amountValid.value || saving.value) return
  saving.value = true
  try {
    await createTrackingCard({
      purpose: form.purpose,
      category: form.category,
      direction: form.direction,
      amount: form.amount.trim() === "" ? undefined : Number(form.amount),
      counterparty: form.counterparty.trim(),
      note: form.note.trim(),
    })
    tip.value = "已创建卡片"
    window.setTimeout(() => {
      open.value = false
      tip.value = ""
      emit("created")
    }, 700)
  } catch (e) {
    tip.value = e instanceof Error ? e.message : "新建卡片失败"
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div>
    <button class="btn btn-primary" @click="openDialog">+ 新建跟踪卡</button>

    <div v-if="open" class="overlay" @click.self="close">
      <div class="dialog card">
        <div class="dialog-head">
          <span class="dialog-title">新建跟踪卡</span>
          <button class="btn-ghost" @click="close">✕</button>
        </div>

        <div class="field">
          <label class="label">用途</label>
          <div class="chips">
            <button
              v-for="p in PURPOSES"
              :key="p.value"
              class="chip"
              :class="{ active: form.purpose === p.value }"
              @click="form.purpose = p.value"
            >
              {{ p.label }}
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
          <label class="label">方向</label>
          <div class="seg">
            <button class="seg-item" :class="{ active: form.direction === 'expense' }" @click="form.direction = 'expense'">支出</button>
            <button class="seg-item" :class="{ active: form.direction === 'income' }" @click="form.direction = 'income'">收入</button>
          </div>
        </div>

        <div class="field">
          <label class="label">金额（可选）</label>
          <input v-model="form.amount" class="input" type="text" inputmode="decimal" placeholder="如 80.00" />
        </div>

        <div class="field">
          <label class="label">对方（可选）</label>
          <input v-model="form.counterparty" class="input" type="text" placeholder="如 张三 / 某某餐厅" />
        </div>

        <div class="field">
          <label class="label">备注（可选）</label>
          <input v-model="form.note" class="input" type="text" placeholder="如 待张三转回 AA 款" />
        </div>

        <div class="dialog-actions">
          <button class="btn" @click="close">取消</button>
          <button class="btn btn-primary" :disabled="!amountValid || saving" @click="submit">
            {{ saving ? "创建中…" : "创建卡片" }}
          </button>
        </div>
        <p class="muted note">{{ tip || "直接写入看板，无需审批。" }}</p>
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
  cursor: pointer;
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
