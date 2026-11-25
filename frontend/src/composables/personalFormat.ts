export function localDateTime(value: string | Date = new Date()): string {
  const date = typeof value === "string" ? new Date(value) : value
  const pad = (n: number) => String(n).padStart(2, "0")
  return [date.getFullYear(), pad(date.getMonth() + 1), pad(date.getDate())].join("-")
    + "T" + pad(date.getHours()) + ":" + pad(date.getMinutes())
}

export function formatTime(value: string | null): string {
  return value ? new Intl.DateTimeFormat("zh-CN", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(new Date(value)) : "—"
}

export function periodLabel(start: string, end: string): string {
  const lastDay = new Date(end + "T00:00:00Z")
  lastDay.setUTCDate(lastDay.getUTCDate() - 1)
  return start + " 至 " + lastDay.toISOString().slice(0, 10)
}

export function decimalText(value: string | null): string {
  return value?.replace(/(\.\d*?[1-9])0+$/, "$1").replace(/\.0+$/, "") ?? "—"
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请稍后重试"
}
