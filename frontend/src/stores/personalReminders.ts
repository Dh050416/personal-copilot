import { defineStore } from "pinia"
import { ref } from "vue"
import { getAccessToken } from "../services/agentClient"
import { personalApi, type Reminder } from "../services/personalClient"
import { errorText } from "../composables/personalFormat"

export const usePersonalReminders = defineStore("personal-reminders", () => {
  const items = ref<Reminder[]>([])
  const error = ref("")
  let sequence = 0

  async function refresh() {
    const token = getAccessToken()
    const request = ++sequence
    if (!token) return
    try {
      const reminders: Reminder[] = []
      for (let offset = 0; ; offset += 100) {
        const page = await personalApi.reminders(offset)
        reminders.push(...page)
        if (request !== sequence || token !== getAccessToken()) return
        if (page.length < 100) break
      }
      items.value = reminders
      error.value = ""
    } catch (cause) {
      if (request === sequence && token === getAccessToken()) error.value = errorText(cause)
    }
  }

  async function read(id: number) {
    const token = getAccessToken()
    await personalApi.readReminder(id)
    if (token !== getAccessToken()) return
    ++sequence
    items.value = items.value.filter(item => item.id !== id)
  }

  function reset() {
    ++sequence
    items.value = []
    error.value = ""
  }

  return { items, error, refresh, read, reset }
})
