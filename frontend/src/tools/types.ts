/**
 * 与后端 Agent 链路对齐的契约类型。
 * 后端事件与回执结构变化时，只需在此处同步类型，
 * 组件与注册表通过类型获得编译期保护。
 */
import type { Component } from "vue"

/* ===== 后端 SSE 事件契约（对应 graph.py / chat.py） ===== */

export type ErrorStage =
  | "routing"
  | "validation"
  | "policy"
  | "business"
  | "execution"
  | "output"
  | "approval"

export interface ErrorInfo {
  code: string
  message: string
  stage: ErrorStage
  retryable: boolean
  details?: unknown
}

/** 工具统一回执 ToolExecutionEnvelope */
export interface ToolExecutionEnvelope {
  ok: boolean
  tool: string
  invocation_id: string
  attempts: number
  data?: unknown
  error?: ErrorInfo
}

export type AgentEventType =
  | "intent_route"
  | "tool_call_proposal"
  | "plan"
  | "arguments_validated"
  | "validation_error"
  | "approval_created"
  | "approval_error"
  | "tool_result"
  | "clarification"
  | "progress"
  | "stream"
  | "final"
  | "error"

export interface AgentEvent {
  type: AgentEventType
  run_id?: string
  at?: string
  payload: Record<string, unknown>
}

export interface IntentRoutePayload {
  tool?: string | null
  args?: Record<string, unknown>
  layer?: string
  confidence?: number | null
  reason?: string
  clarification?: string | null
  trace?: unknown[]
}

export interface ToolResultPayload {
  tool: string
  result: ToolExecutionEnvelope
}

/* ===== 业务数据形状 ===== */

export interface BillRow {
  id: number
  tx_no: string
  tx_time: string
  merchant: string
  product: string
  amount: string
  direction: string
  transaction_type: string
  payment_method: string
  transaction_status: string
  note: string
  category_source: string
  category_confidence: number
  category: string
  source: string
}

export interface QueryBillData {
  count: number
  total: string
  returned_count: number
  truncated: boolean
  filters: Record<string, unknown>
  items: BillRow[]
  warnings: string[]
}

export interface TrackingCard {
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

export interface CardListData {
  items: TrackingCard[]
}

/* ===== 前端工具注册契约 ===== */

/**
 * 一个 Agent 工具在前端的完整定义。
 * 新增任务/工具时：实现 extract 与可选 renderer，registerTool 注册即可，
 * 会话状态、SSE 解析、结果渲染入口全部复用，无需改动主流程。
 */
export interface ToolDefinition<Data = unknown> {
  /** 与后端 ToolSpec.name 保持一致 */
  name: string
  /** 界面展示名 */
  label: string
  /** 是否只读（只读结果直接展示，写结果会先经过审批卡） */
  readOnly: boolean
  /** 从回执 data 提取渲染数据；失败时返回 null 表示不渲染 */
  extract: (data: unknown) => Data | null
  /** 专用渲染组件（接收 prop: data） */
  renderer?: Component
  /** 无专用组件时的文本摘要 */
  fallbackSummary: (data: Data) => string
}
