/**
 * 会话状态：一次 Agent 运行的完整可观察状态。
 * 事件流统一从这里分发；工具结果按注册表提取后进入 renderSlots，
 * 组件按工具名取数据渲染——新增工具无需改动本 store 主流程。
 */
import { defineStore } from "pinia"
import { computed, ref } from "vue"
import { getTool } from "../tools/registry"
import type {
  AgentEvent,
  BillRow,
  ToolExecutionEnvelope,
  ToolResultPayload,
} from "../tools/types"
import * as api from "../services/agentClient"

export interface ApprovalState {
  runId: string
  approvalId: string
  tool: string
  args: Record<string, unknown>
}

export interface ChatTurn {
  role: "user" | "assistant"
  text: string
}

export interface SavedSession {
  id: string
  title: string
  updatedAt: number
  turns: ChatTurn[]
}

const SESSIONS_KEY = "copilot_sessions_v1"

export const useSessionStore = defineStore("session", () => {
  const events = ref<AgentEvent[]>([])
  const answer = ref("")
  /** 流式回答增量：模型生成期间逐 token 累积，final/澄清后清空 */
  const streamText = ref("")
  /** 生成期间的心跳提示（后端每 2.5s 推一条 progress） */
  const progressText = ref("")
  const busy = ref(false)
  /** 执行中追加且已排队（后端确认入队）的消息数；注入后随流自然消费 */
  const queuedCount = ref(0)
  const approval = ref<ApprovalState | null>(null)
  const renderSlots = ref<Record<string, unknown>>({})
  const lastResult = ref<ToolExecutionEnvelope | null>(null)
  const importMessage = ref("")
  /** 会话级对话历史：跨路由、跨轮保留（组件卸载不丢）；「新对话」才清空 */
  const chatTurns = ref<ChatTurn[]>([])
  /** 会话列表（含当前会话），localStorage 持久化 */
  const sessions = ref<SavedSession[]>([])

  function newSessionId(): string {
    return typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `s-${Date.now()}`
  }

  function persistSessions(): void {
    try {
      localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions.value))
    } catch {
      /* localStorage 不可用时静默降级为纯内存会话 */
    }
  }

  /** 启动时加载会话列表；无历史会话则创建当前会话 */
  function loadSessions(): void {
    try {
      const raw = localStorage.getItem(SESSIONS_KEY)
      sessions.value = raw ? (JSON.parse(raw) as SavedSession[]) : []
    } catch {
      sessions.value = []
    }
    if (sessions.value.length === 0) {
      sessions.value.push({ id: newSessionId(), title: "新会话", updatedAt: Date.now(), turns: [] })
      persistSessions()
    }
    // 恢复最近一次会话
    const last = sessions.value[sessions.value.length - 1]
    sessionId.value = last.id
    chatTurns.value = last.turns
  }

  /** 当前会话的最新状态回写本地存储 */
  function syncCurrentSession(): void {
    const cur = sessions.value.find((s) => s.id === sessionId.value)
    if (cur) {
      cur.turns = chatTurns.value
      cur.updatedAt = Date.now()
      persistSessions()
    }
  }

  /** 切换会话：先存档当前，再加载目标会话的历史与上下文 */
  function switchSession(id: string): void {
    const target = sessions.value.find((s) => s.id === id)
    if (!target || id === sessionId.value) return
    syncCurrentSession()
    sessionId.value = target.id
    chatTurns.value = target.turns
    events.value = []
    answer.value = ""
    streamText.value = ""
    progressText.value = ""
    renderSlots.value = {}
    approval.value = null
    lastResult.value = null
  }

  /** 新建会话：存档当前，开一个新会话并置顶 */
  function newSession(): void {
    syncCurrentSession()
    const id = newSessionId()
    sessionId.value = id
    chatTurns.value = []
    events.value = []
    answer.value = ""
    streamText.value = ""
    progressText.value = ""
    renderSlots.value = {}
    approval.value = null
    lastResult.value = null
    sessions.value.push({ id, title: "新会话", updatedAt: Date.now(), turns: [] })
    persistSessions()
  }
  /** 会话 ID：同一会话的多轮消息共用，后端据此衔接上下文；「新对话」才重置 */
  const sessionId = ref<string>("")

  // 启动时恢复会话列表与最近会话
  loadSessions()

  const hasApproval = computed(() => approval.value !== null)
  const lastToolResultText = computed(() => {
    const result = lastResult.value
    if (!result) return ""
    if (result.ok) {
      const def = getTool(result.tool)
      return def ? def.fallbackSummary(def.extract(result.data)) : "工具执行完成。"
    }
    return `执行失败（${result.error?.code ?? "UNKNOWN"}）：${result.error?.message ?? "未知错误"}`
  })

  /** 事件统一入口：SSE 回调 -> 状态更新 */
  function pushEvent(event: AgentEvent): void {
    events.value.push(event)
    switch (event.type) {
      case "stream":
        streamText.value += String(event.payload.delta ?? "")
        break
      case "progress":
        progressText.value = String(event.payload.text ?? "")
        break
      case "final":
        answer.value = String(event.payload.text ?? "")
        streamText.value = ""
        progressText.value = ""
        break
      case "clarification":
        answer.value = String(event.payload.text ?? "")
        streamText.value = ""
        progressText.value = ""
        break
      case "approval_created":
        approval.value = {
          runId: event.run_id ?? "",
          approvalId: String(event.payload.approval_id ?? ""),
          tool: String(event.payload.tool ?? ""),
          args: (event.payload.args as Record<string, unknown>) ?? {},
        }
        break
      case "approval_error":
        approval.value = null
        answer.value = readError(event.payload.result)
        break
      case "tool_result":
        handleToolResult(event.payload as unknown as ToolResultPayload)
        break
      case "error":
        answer.value = `运行失败：${String(event.payload.error ?? "")}`
        break
      default:
        break
    }
  }

  function handleToolResult(payload: ToolResultPayload): void {
    const result = payload.result
    if (!result) return
    lastResult.value = result
    if (!result.ok) return
    const def = getTool(payload.tool)
    if (!def) return
    const data = def.extract(result.data)
    if (data !== null) renderSlots.value[payload.tool] = data
    // 完成卡片后同步看板列表槽位：该卡从泳道移入「已完成」区（避免再次全量查询）
    if (payload.tool === "complete_tracking_card" && result.ok) {
      const card = result.data as { id?: number } | undefined
      if (card && typeof card.id === "number") markCardDone(card.id)
    }
  }

  function readError(result: unknown): string {
    const envelope = result as ToolExecutionEnvelope | undefined
    if (!envelope?.error) return "审批处理失败。"
    return `审批失败（${envelope.error.code}）：${envelope.error.message}`
  }

  /** 发送新消息：清空上轮结果，逐事件进入状态 */
  async function run(text: string): Promise<void> {
    busy.value = true
    answer.value = ""
    streamText.value = ""
    progressText.value = ""
    events.value = []
    approval.value = null
    renderSlots.value = {}
    lastResult.value = null
    try {
      await api.postMessage(text, sessionId.value, pushEvent)
    } catch (error) {
      answer.value = `请求失败：${String(error)}`
    } finally {
      busy.value = false
      queuedCount.value = 0
    }
  }

  /** 统一发送入口（检查点注入）：busy 时新消息不再被禁用，而是发送给后端排队，
   * 由当前执行流的 runner 在下一输入检查点注入并继续；排队消息的回复走当前 SSE 流回来。 */
  async function sendMessage(text: string): Promise<void> {
    if (busy.value) {
      try {
        const res = await api.postMessage(text, sessionId.value, () => {})
        if (res.queued) queuedCount.value += 1
      } catch {
        /* 排队失败静默：当前流仍在跑，用户可稍后再试 */
      }
      return
    }
    await run(text)
  }

  /** 追加一条对话轮次；assistant 与末条同文本视为重复，跳过 */
  function pushTurn(role: ChatTurn["role"], text: string): void {
    const clean = String(text ?? "").trim()
    if (!clean) return
    const last = chatTurns.value[chatTurns.value.length - 1]
    if (last && last.role === role && last.text === clean) return
    chatTurns.value.push({ role, text: clean })
    // 会话标题：取第一条用户消息
    const cur = sessions.value.find((s) => s.id === sessionId.value)
    if (cur) {
      if (cur.title === "新会话" && role === "user") {
        cur.title = clean.length > 18 ? `${clean.slice(0, 18)}…` : clean
      }
      cur.turns = chatTurns.value
      cur.updatedAt = Date.now()
      persistSessions()
    }
  }

  /** 开启新会话：重置会话 ID（后端 checkpoint 随之另开一段历史），并清空对话历史 */
  function resetSession(): void {
    sessionId.value = newSessionId()
    chatTurns.value = []
  }

  async function decide(decision: "approve" | "reject"): Promise<void> {
    const pending = approval.value
    if (!pending || busy.value) return
    approval.value = null
    busy.value = true
    try {
      await api.resume(pending.runId, pending.approvalId, decision, pushEvent)
    } catch (error) {
      answer.value = `审批恢复失败：${String(error)}`
    } finally {
      busy.value = false
    }
  }

  /** 看板拖拽完成：仅更新本地那张卡的状态，不触发全量重新查询（避免组件销毁闪烁） */
  function markCardDone(id: number): void {
    const slot = renderSlots.value["list_tracking_cards"]
    if (slot && typeof slot === "object" && Array.isArray((slot as { items?: unknown[] }).items)) {
      const item = (slot as { items: { id?: unknown; status?: string }[] }).items.find(
        (card) => card.id === id,
      )
      if (item) item.status = "done"
    }
  }

  /** 看板已完成卡被删除后，从本地槽位移除（不触发全量重查） */
  function markCardDeleted(id: number): void {
    const slot = renderSlots.value["list_tracking_cards"]
    if (slot && typeof slot === "object" && Array.isArray((slot as { items?: unknown[] }).items)) {
      const items = (slot as { items: { id?: unknown }[] }).items
      const idx = items.findIndex((card) => card.id === id)
      if (idx >= 0) items.splice(idx, 1)
    }
  }

  /** 看板刷新：直读 /api/cards（不走 Agent 对话，省 token 与模型往返延迟）；
   * 结果写入 renderSlots 的 list_tracking_cards 槽位，TaskBoard/BoardView 无需改动 */
  async function refreshCards(): Promise<void> {
    busy.value = true
    answer.value = ""
    try {
      const items = await api.getCards()
      renderSlots.value["list_tracking_cards"] = { items }
    } catch (e) {
      answer.value = `请求失败：${String(e)}`
    } finally {
      busy.value = false
    }
  }

  /** 查询结果的账单行（经注册表 extract） */
  const billRows = computed<BillRow[]>(() => {
    const data = renderSlots.value["query_bill"] as { items?: BillRow[] } | undefined
    return data?.items ?? []
  })

  const cards = computed(() => {
    const data = renderSlots.value["list_tracking_cards"] as { items?: unknown[] } | undefined
    return data?.items ?? []
  })

  return {
    events,
    answer,
    streamText,
    progressText,
    busy,
    queuedCount,
    approval,
    renderSlots,
    lastResult,
    importMessage,
    sessionId,
    chatTurns,
    sessions,
    hasApproval,
    lastToolResultText,
    pushEvent,
    pushTurn,
    run,
    sendMessage,
    decide,
    resetSession,
    switchSession,
    newSession,
    refreshCards,
    markCardDone,
    markCardDeleted,
    billRows,
    cards,
  }
})
