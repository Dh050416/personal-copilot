<script setup lang="ts">
import { ref } from "vue"
import { useRouter } from "vue-router"
import { login, register } from "../services/agentClient"

const router = useRouter()

const mode = ref<"login" | "register">("login")
const username = ref("")
const password = ref("")
const confirm = ref("")
const error = ref("")
const loading = ref(false)

function switchMode(m: "login" | "register"): void {
  mode.value = m
  error.value = ""
  confirm.value = ""
}

async function submit(): Promise<void> {
  error.value = ""
  const name = username.value.trim()
  if (!name) {
    error.value = "请输入用户名"
    return
  }
  if (!password.value) {
    error.value = "请输入密码"
    return
  }
  if (mode.value === "register") {
    if (password.value.length < 6) {
      error.value = "密码至少 6 位"
      return
    }
    if (password.value !== confirm.value) {
      error.value = "两次输入的密码不一致"
      return
    }
  }
  loading.value = true
  try {
    if (mode.value === "login") {
      await login(name, password.value)
    } else {
      await register(name, password.value)
    }
    router.replace("/")
  } catch (e) {
    error.value = e instanceof Error ? e.message : "操作失败"
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="auth-brand">
        <span class="auth-mark" aria-hidden="true"></span>
        <h1 class="auth-title">生活管家</h1>
        <p class="auth-sub">每个账号都是一位专属的账单管家，数据彼此隔离</p>
      </div>

      <div class="auth-tabs">
        <button
          class="auth-tab"
          :class="{ active: mode === 'login' }"
          @click="switchMode('login')"
        >
          登录
        </button>
        <button
          class="auth-tab"
          :class="{ active: mode === 'register' }"
          @click="switchMode('register')"
        >
          注册
        </button>
      </div>

      <form class="auth-form" @submit.prevent="submit">
        <label class="field">
          <span class="field-label">用户名</span>
          <input
            v-model="username"
            class="input"
            placeholder="给自己起个名字，如 alice"
            autocomplete="username"
          />
        </label>
        <label class="field">
          <span class="field-label">密码</span>
          <input
            v-model="password"
            class="input"
            type="password"
            placeholder="至少 6 位"
            autocomplete="current-password"
          />
        </label>
        <label v-if="mode === 'register'" class="field">
          <span class="field-label">确认密码</span>
          <input
            v-model="confirm"
            class="input"
            type="password"
            placeholder="再输一遍"
            autocomplete="new-password"
          />
        </label>

        <p v-if="error" class="error-text">{{ error }}</p>

        <button class="btn btn-primary auth-submit" type="submit" :disabled="loading">
          {{ loading ? "请稍候…" : mode === "login" ? "登录" : "注册并进入" }}
        </button>
      </form>

      <p class="auth-hint">
        {{ mode === "login" ? "还没有账号？" : "已有账号？" }}
        <a
          class="auth-link"
          @click="switchMode(mode === 'login' ? 'register' : 'login')"
        >
          {{ mode === "login" ? "去注册" : "去登录" }}
        </a>
      </p>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--bg);
}

.auth-card {
  width: 100%;
  max-width: 400px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 32px 28px;
  box-shadow: var(--shadow);
}

.auth-brand {
  text-align: center;
  margin-bottom: 22px;
}

.auth-mark {
  display: inline-block;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: linear-gradient(135deg, var(--header-start), var(--header-end));
  margin-bottom: 12px;
}

.auth-title {
  font-size: 22px;
  color: var(--text-1);
}

.auth-sub {
  font-size: 13px;
  color: var(--text-2);
  margin-top: 6px;
}

.auth-tabs {
  display: flex;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  overflow: hidden;
  margin-bottom: 20px;
}

.auth-tab {
  flex: 1;
  padding: 9px 0;
  border: none;
  background: var(--surface);
  color: var(--text-2);
  font-size: 14px;
  cursor: pointer;
}

.auth-tab.active {
  background: var(--primary);
  color: #fff;
  font-weight: 600;
}

.auth-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.field-label {
  font-size: 12px;
  color: var(--text-2);
}

.input {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 9px 12px;
  font-size: 14px;
  background: var(--surface);
  color: var(--text-1);
  width: 100%;
}

.input:focus {
  outline: 2px solid var(--primary-border);
  border-color: var(--primary);
}

.error-text {
  color: var(--danger);
  font-size: 13px;
}

.btn {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-1);
  border-radius: var(--radius-sm);
  padding: 9px 14px;
  font-size: 14px;
  cursor: pointer;
}

.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-primary {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
}

.auth-submit {
  margin-top: 4px;
  padding: 11px;
  font-size: 15px;
}

.auth-hint {
  text-align: center;
  font-size: 13px;
  color: var(--text-2);
  margin-top: 18px;
}

.auth-link {
  color: var(--primary);
  cursor: pointer;
  text-decoration: none;
}
</style>
