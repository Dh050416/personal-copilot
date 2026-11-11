/**
 * 后端交互客户端：所有网络调用集中在这一层。
 * - SSE 解析（/api/chat 与 /api/runs/{id}/resume）
 * - Excel 导入 / 批量分类 / 人工分类确认
 * 组件不直接接触 fetch，后续更换基址、加鉴权或重试只需改这里。
 */
import type { AgentEvent } from "../tools/types"

/**
 * 后端地址。默认与前端同源（空字符串），由 vite.config.ts 的 /api 代理转发到
 * 127.0.0.1:8000 —— 避免跨域预检被后端鉴权拦截，也无需额外配置。
 * 需要直连其他后端（跨域部署）时设置 VITE_API_BASE 为完整地址，
 * 并把该地址加入后端 CORS_ORIGINS。
 */
export const API_BASE: string = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "")

/** 用户令牌（localStorage 持久化）。登录成功后由后端签发，所有请求经 Bearer 携带。 */
const TOKEN_KEY = "copilot_access_token"
const USER_KEY = "copilot_user"

export interface CurrentUser {
  id: number
  username: string
}

export function getAccessToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? ""
  } catch {
    return ""
  }
}

export function setAccessToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* localStorage 不可用时静默降级 */
  }
}

export function getCurrentUser(): CurrentUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as CurrentUser) : null
  } catch {
    return null
  }
}

export function setCurrentUser(user: CurrentUser | null): void {
  try {
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user))
    else localStorage.removeItem(USER_KEY)
  } catch {
    /* ignore */
  }
}

export function isLoggedIn(): boolean {
  return getAccessToken() !== ""
}

/** 登出：清空令牌与用户信息。 */
export function logout(): void {
  setAccessToken("")
  setCurrentUser(null)
}

/** 登录/注册成功后统一写入凭证。 */
function applyAuth(token: string, user: CurrentUser): void {
  setAccessToken(token)
  setCurrentUser(user)
}

/** 统一构造鉴权头：有 token 则附带 Bearer。 */
function authHeaders(): Record<string, string> {
  const token = getAccessToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/** 注册新账号，成功返回并写入凭证。 */
export async function register(username: string, password: string): Promise<CurrentUser> {
  const response = await fetch(`${API_BASE}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  })
  const data = (await response.json()) as { token?: string; user?: CurrentUser; detail?: string }
  if (!response.ok || !data.token || !data.user) {
    throw new Error(data.detail || "注册失败")
  }
  applyAuth(data.token, data.user)
  return data.user
}

/** 登录，成功返回并写入凭证。 */
export async function login(username: string, password: string): Promise<CurrentUser> {
  const response = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  })
  const data = (await response.json()) as { token?: string; user?: CurrentUser; detail?: string }
  if (!response.ok || !data.token || !data.user) {
    throw new Error(data.detail || "登录失败")
  }
  applyAuth(data.token, data.user)
  return data.user
}

/** 401 未登录统一处理：清凭证并跳转登录页（由 router 守卫或调用方触发）。 */
let onUnauthorized: (() => void) | null = null

export function setOnUnauthorized(handler: (() => void) | null): void {
  onUnauthorized = handler
}

function handleUnauthorized(): void {
  logout()
  if (onUnauthorized) onUnauthorized()
}

/** 校验当前令牌是否仍有效，返回用户信息。 */
export async function fetchMe(): Promise<CurrentUser> {
  const response = await fetch(`${API_BASE}/api/auth/me`, { headers: authHeaders() })
  const data = (await response.json()) as CurrentUser & { detail?: string }
  if (!response.ok) throw new Error(data.detail || "登录已失效")
  return data
}

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
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  })
  if (response.status === 401) handleUnauthorized()
  return response
}

/** 带鉴权的通用 fetch：自动附加 Bearer，401 时清凭证并跳登录。 */
async function authedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(url, {
    ...init,
    headers: { ...authHeaders(), ...(init.headers || {}) },
  })
  if (response.status === 401) handleUnauthorized()
  return response
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
  const response = await authedFetch(`${API_BASE}/api/bills/import`, {
    method: "POST",
    body,
  })
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

/** 随手记：直接写入一笔账单（不走 Agent/模型），source=manual */
export interface AddBillInput {
  amount: number
  direction: "expense" | "income"
  category: string
  merchant?: string
  note?: string
}

export async function addBill(input: AddBillInput): Promise<unknown> {
  const response = await postJson("/api/bills", input)
  const data = (await response.json()) as unknown
  if (!response.ok) {
    const detail = (data as { detail?: string })?.detail
    throw new Error(detail || "手记保存失败")
  }
  return data
}

/** 将跟踪卡标记为已完成（看板拖拽到「已完成」区） */
export async function completeCard(cardId: number): Promise<unknown> {
  const response = await authedFetch(`${API_BASE}/api/cards/${cardId}/complete`, {
    method: "POST",
  })
  const data = (await response.json()) as unknown
  if (!response.ok) {
    const detail = (data as { detail?: string })?.detail
    throw new Error(detail || "卡片完成失败")
  }
  return data
}

/** 看板直连新建跟踪卡（确定性录入，不走 Agent/审批） */
export interface CreateCardInput {
  category: string
  purpose: "报销" | "退款" | "待退订" | "AA" | "待处理"
  direction?: "expense" | "income"
  counterparty?: string
  amount?: number
  note?: string
  lane?: string
}

export async function createTrackingCard(input: CreateCardInput): Promise<unknown> {
  const response = await postJson("/api/cards", input)
  const data = (await response.json()) as unknown
  if (!response.ok) {
    const detail = (data as { detail?: string })?.detail
    throw new Error(detail || "新建卡片失败")
  }
  return data
}

/** 删除已完成卡片（仅 done 状态；不可恢复） */
export async function deleteCard(cardId: number): Promise<void> {
  const response = await authedFetch(`${API_BASE}/api/cards/${cardId}`, {
    method: "DELETE",
  })
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
  const response = await authedFetch(url)
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
  const response = await authedFetch(url)
  if (!response.ok) throw new Error(`日程查询失败（HTTP ${response.status}）`)
  const data = (await response.json()) as { items: EventItem[] }
  return data.items
}

/** 长期记忆条目 */
export interface MemoryItem {
  key: string
  value: string
  updated_at: string
}

/** 列出全部长期记忆（设置页） */
export async function getMemories(): Promise<MemoryItem[]> {
  const response = await authedFetch(`${API_BASE}/api/memory`)
  if (!response.ok) throw new Error(`记忆查询失败（HTTP ${response.status}）`)
  const data = (await response.json()) as { items: MemoryItem[] }
  return data.items
}

/** 新增/覆盖一条记忆 */
export async function upsertMemory(key: string, value: string): Promise<MemoryItem> {
  const response = await authedFetch(`${API_BASE}/api/memory`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, value }),
  })
  const data = (await response.json()) as unknown
  if (!response.ok) {
    const detail = (data as { detail?: string })?.detail
    throw new Error(detail || "记忆保存失败")
  }
  return data as MemoryItem
}

/** 删除一条记忆 */
export async function deleteMemory(key: string): Promise<void> {
  const response = await authedFetch(`${API_BASE}/api/memory/${encodeURIComponent(key)}`, {
    method: "DELETE",
  })
  if (!response.ok) {
    const data = (await response.json()) as unknown
    const detail = (data as { detail?: string })?.detail
    throw new Error(detail || "记忆删除失败")
  }
}

/** 消费报表：分类占比条目 */
export interface ReportCategoryItem {
  category: string
  amount: string
  amount_cents: number
}

/** 消费报表：月度收支条目 */
export interface ReportMonthItem {
  month: string
  expense: string
  income: string
  expense_cents: number
  income_cents: number
}

/** 消费报表汇总 */
export interface ReportSummary {
  total_expense: string
  total_income: string
  bill_count: number
  total_expense_cents: number
  total_income_cents: number
  categories: ReportCategoryItem[]
  monthly: ReportMonthItem[]
}

/** 获取消费报表汇总（可选日期区间，闭区间） */
export async function getReportSummary(params: {
  start_date?: string
  end_date?: string
} = {}): Promise<ReportSummary> {
  const qs = new URLSearchParams()
  if (params.start_date) qs.set("start_date", params.start_date)
  if (params.end_date) qs.set("end_date", params.end_date)
  const url = `${API_BASE}/api/reports/summary${qs.toString() ? `?${qs.toString()}` : ""}`
  const response = await authedFetch(url)
  if (!response.ok) throw new Error(`报表查询失败（HTTP ${response.status}）`)
  return (await response.json()) as ReportSummary
}

/* ===== 知识库（RAG）===== */

/** 知识库文档元数据（列表/详情/轮询状态用）。status: pending | ready | failed */
export interface KnowledgeDocumentItem {
  id: number
  title: string
  source_type: string // file | paste
  filename: string | null
  file_size: number | null
  status: string
  error_message: string | null
  chunk_count: number
  created_at: string
  updated_at: string
}

/** 摄入受理结果（异步摄入，立即返回 pending） */
export interface IngestResponse {
  doc_id: number
  status: string
}

/** 列出知识库文档（按创建时间倒序） */
export async function listKnowledgeDocuments(): Promise<KnowledgeDocumentItem[]> {
  const response = await authedFetch(`${API_BASE}/api/knowledge/documents`)
  if (!response.ok) throw new Error(`文档列表查询失败（HTTP ${response.status}）`)
  const data = (await response.json()) as { items: KnowledgeDocumentItem[] }
  return data.items
}

/** 单个文档详情（轮询摄入状态用） */
export async function getKnowledgeDocument(docId: number): Promise<KnowledgeDocumentItem> {
  const response = await authedFetch(`${API_BASE}/api/knowledge/documents/${docId}`)
  if (!response.ok) throw new Error(`文档查询失败（HTTP ${response.status}）`)
  return (await response.json()) as KnowledgeDocumentItem
}

/** 上传文件摄入（txt/md/docx/pdf），立即返回 pending，后台异步向量化 */
export async function uploadKnowledgeFile(file: File, title?: string): Promise<IngestResponse> {
  const body = new FormData()
  body.append("file", file)
  if (title) body.append("title", title)
  const response = await authedFetch(`${API_BASE}/api/knowledge/documents/upload`, {
    method: "POST",
    body,
  })
  const data = (await response.json()) as unknown
  if (!response.ok) {
    const detail = (data as { detail?: string })?.detail
    throw new Error(detail || "文件上传失败")
  }
  return data as IngestResponse
}

/** 粘贴文本摄入，立即返回 pending，后台异步向量化 */
export async function ingestKnowledgeText(title: string, text: string): Promise<IngestResponse> {
  const response = await postJson("/api/knowledge/documents/text", { title, text })
  const data = (await response.json()) as unknown
  if (!response.ok) {
    const detail = (data as { detail?: string })?.detail
    throw new Error(detail || "文本入库失败")
  }
  return data as IngestResponse
}

/** 删除文档（块级联删除，真删不可恢复） */
export async function deleteKnowledgeDocument(docId: number): Promise<void> {
  const response = await authedFetch(`${API_BASE}/api/knowledge/documents/${docId}`, {
    method: "DELETE",
  })
  if (!response.ok) {
    const data = (await response.json()) as unknown
    const detail = (data as { detail?: string })?.detail
    throw new Error(detail || "文档删除失败")
  }
}
