/**
 * 后端交互客户端：所有网络调用集中在这一层。
 * - SSE 解析（/api/chat 与 /api/runs/{id}/resume）
 * - Excel 导入 / 批量分类 / 人工分类确认
 * 组件不直接接触 fetch，后续更换基址、加鉴权或重试只需改这里。
 */
import type { AgentEvent } from "../tools/types"

/**
 * 后端地址。默认直连本机 8000；部署到其他地址时设置 VITE_API_BASE。
 * 设为 **空字符串** 表示与前端同源，此时由 vite.config.ts 的 /api 代理转发
 * （适合容器部署或只想暴露一个端口的场景）。
 * 非空时前端直接请求该地址，后端需把该地址加入 CORS_ORIGINS。
 */
export const API_BASE: string = (import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000").replace(
  /\/$/,
  "",
)

async function consume(response: Response, onEvent: (event: AgentEvent) => void): Promise<void> {
  const reader = response.body?.getReader()
  if (!reader) throw new Error("浏览器不支持流式响应")
  const decoder = new TextDecoder()
  let buffer = ""
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const blocks = buffer.split("\n\n")
    buffer = blocks.pop() || ""
    for (const block of blocks) {
      const line = block.split("\n").find((item) => item.startsWith("data: "))
      if (!line) continue
      const data = line.slice(6)
      if (data === "[DONE]") continue
      let event: AgentEvent
      try {
        event = JSON.parse(data) as AgentEvent
      } catch {
        continue
      }
      onEvent(event)
    }
  }
}

async function postJson(path: string, body: unknown): Promise<Response> {
  return fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
}

/** 发送一条用户消息，逐事件回调；sessionId 用于多轮上下文衔接（缺省=新会话）。
 * 返回 { queued }：true 表示当前会话有执行流在跑，消息已进入后端追加队列
 * （检查点注入，将在当前任务的下一轮生效）；false 表示正常流式返回。 */
export async function postMessage(
  text: string,
  sessionId: string | undefined,
  onEvent: (event: AgentEvent) => void,
): Promise<{ queued: boolean }> {
  const response = await postJson("/api/chat", { message: text, session_id: sessionId })
  if (!response.ok) throw new Error(`请求失败（HTTP ${response.status}）`)
  const contentType = response.headers.get("content-type") || ""
  if (contentType.includes("application/json")) {
    // 会话忙：后端返回 {queued:true}，消息排队等待注入（非 SSE）
    return { queued: true }
  }
  await consume(response, onEvent)
  return { queued: false }
}

/** 恢复一次等待审批的运行 */
export async function resume(
  runId: string,
  approvalId: string,
  decision: "approve" | "reject",
  onEvent: (event: AgentEvent) => void,
): Promise<void> {
  const response = await postJson(`/api/runs/${runId}/resume`, {
    approval_id: approvalId,
    decision,
  })
  if (!response.ok) throw new Error(`审批恢复失败（HTTP ${response.status}）`)
  await consume(response, onEvent)
}

/** 上传 Excel/CSV 并导入分类 */
export async function importBills(file: File): Promise<unknown> {
  const body = new FormData()
  body.append("file", file)
  const response = await fetch(`${API_BASE}/api/bills/import`, { method: "POST", body })
  const report = (await response.json()) as unknown
  if (!response.ok) {
    const detail = (report as { detail?: string })?.detail
    throw new Error(detail || "导入失败")
  }
  return report
}

/** 批量分类已有未分类账单 */
export async function classifyExisting(limit = 200): Promise<unknown> {
  const response = await postJson("/api/bills/classify", { limit })
  const report = (await response.json()) as unknown
  if (!response.ok) {
    const detail = (report as { detail?: string })?.detail
    throw new Error(detail || "分类失败")
  }
  return report
}

/** 人工确认账单分类（后端保证不会被后续导入/模型覆盖） */
export async function confirmCategory(billId: number, category: string): Promise<unknown> {
  const response = await postJson(`/api/bills/${billId}/category`, {
    category,
    confirmed: true,
  })
  const data = (await response.json()) as unknown
  if (!response.ok) {
    const detail = (data as { detail?: string })?.detail
    throw new Error(detail || "分类保存失败")
  }
  return data
}

/** 将跟踪卡标记为已完成（看板拖拽到「已完成」区） */
export async function completeCard(cardId: number): Promise<unknown> {
  const response = await fetch(`${API_BASE}/api/cards/${cardId}/complete`, { method: "POST" })
  const data = (await response.json()) as unknown
  if (!response.ok) {
    const detail = (data as { detail?: string })?.detail
    throw new Error(detail || "卡片完成失败")
  }
  return data
}

/** 删除已完成卡片（仅 done 状态；不可恢复） */
export async function deleteCard(cardId: number): Promise<void> {
  const response = await fetch(`${API_BASE}/api/cards/${cardId}`, { method: "DELETE" })
  const data = (await response.json()) as unknown
  if (!response.ok) {
    const detail = (data as { detail?: string })?.detail
    throw new Error(detail || "删除失败")
  }
}

/** 看板卡片（直读端点 /api/cards 返回，字段与 Agent 工具一致） */
export interface TrackingCardItem {
  id: number
  bill_id: number | null
  category: string
  purpose: string
  note: string
  status: string
  direction: string
  transaction_type: string
  counterparty: string
  amount: string | null
  occurred_at: string
}

/** 直读事务卡片列表（不走 Agent 对话；status 缺省返回全部） */
export async function getCards(params: { status?: "active" | "done" } = {}): Promise<TrackingCardItem[]> {
  const qs = new URLSearchParams()
  if (params.status) qs.set("status", params.status)
  const url = `${API_BASE}/api/cards${qs.toString() ? `?${qs.toString()}` : ""}`
  const response = await fetch(url)
  if (!response.ok) throw new Error(`卡片查询失败（HTTP ${response.status}）`)
  const data = (await response.json()) as { items: TrackingCardItem[] }
  return data.items
}

/** 日程视图：某类型 */
export interface EventItem {
  id: number
  title: string
  start_at: string
  end_at: string
  location: string
  note: string
  status: "active" | "done"
}

/** 查询日程（只读端点 /api/events；status=active|done|all） */
export async function getEvents(params: {
  start_date?: string
  end_date?: string
  status?: "active" | "done" | "all"
} = {}): Promise<EventItem[]> {
  const qs = new URLSearchParams()
  if (params.start_date) qs.set("start_date", params.start_date)
  if (params.end_date) qs.set("end_date", params.end_date)
  if (params.status && params.status !== "all") qs.set("status", params.status)
  const url = `${API_BASE}/api/events${qs.toString() ? `?${qs.toString()}` : ""}`
  const response = await fetch(url)
  if (!response.ok) throw new Error(`日程查询失败（HTTP ${response.status}）`)
  const data = (await response.json()) as { items: EventItem[] }
  return data.items
}
