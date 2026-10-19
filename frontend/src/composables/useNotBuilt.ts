/**
 * 未开发功能的统一提示：任何尚未实现的功能点
 * 点击后显示一条「未开发」提示，而不是静默无反应。
 */
import { ref } from "vue"

export function useNotBuilt(timeoutMs = 2600) {
  const notice = ref("")
  let timer: number | undefined

  function show(feature: string, message?: string): void {
    notice.value = message ?? `「${feature}」尚未开发，敬请期待。`
    window.clearTimeout(timer)
    timer = window.setTimeout(() => {
      notice.value = ""
    }, timeoutMs)
  }

  return { notice, show }
}
