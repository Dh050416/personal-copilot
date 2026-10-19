/**
 * 前端工具注册表：新增一个 Agent 工具时在此注册定义，
 * 会话 store 与结果渲染入口自动获得该工具的展示能力。
 */
import type { ToolDefinition } from "./types"
import BillTable from "../components/BillTable.vue"
import TaskBoard from "../components/TaskBoard.vue"

const registry = new Map<string, ToolDefinition<unknown>>()

export function registerTool(def: ToolDefinition<unknown>): void {
  if (registry.has(def.name)) {
    throw new Error(`工具重复注册：${def.name}`)
  }
  registry.set(def.name, def)
}

export function getTool(name: string): ToolDefinition<unknown> | undefined {
  return registry.get(name)
}

export function listTools(): ToolDefinition<unknown>[] {
  return [...registry.values()]
}

/** 工具注册与初始化（内置工具） */
export function setupTools(): void {
  registerTool({
    name: "query_bill",
    label: "账单查询",
    readOnly: true,
    extract: (data) => (data && typeof data === "object" ? data : null),
    renderer: BillTable,
    fallbackSummary: () => "账单查询完成。",
  })
  registerTool({
    name: "list_tracking_cards",
    label: "任务列表",
    readOnly: true,
    extract: (data) => (data && typeof data === "object" ? data : null),
    renderer: TaskBoard,
    fallbackSummary: () => "任务列表已获取。",
  })
  registerTool({
    name: "create_tracking_card",
    label: "创建任务",
    readOnly: false,
    extract: (data) => (data && typeof data === "object" ? data : null),
    fallbackSummary: (data) => {
      const card = data as {
        category?: string
        purpose?: string
        id?: number
        amount?: string | null
        direction?: string
        counterparty?: string
      }
      const amt = card.amount
        ? `${card.direction === "expense" ? "−" : "+"}¥${card.amount}`
        : ""
      const role = card.direction === "expense" ? "收款人" : "付款人"
      const cpt = card.counterparty ? `${role}：${card.counterparty}` : ""
      const tail = [amt, cpt].filter(Boolean).join("，")
      return `已创建${card.category ?? ""}·${card.purpose ?? ""}任务（卡片 ID：${card.id ?? "—"}${tail ? `，${tail}` : ""}）。`
    },
  })
  registerTool({
    name: "create_cards_from_query",
    label: "批量创建任务",
    readOnly: false,
    extract: (data) => (data && typeof data === "object" ? data : null),
    renderer: TaskBoard,
    fallbackSummary: (data) => {
      const batch = data as {
        total?: number
        card_ids?: number[]
        total_amount?: string
        items?: Array<{ category?: string; purpose?: string }>
      }
      const first = batch?.items?.[0]
      const tail = batch?.total_amount
        ? `，合计 ¥${batch.total_amount}`
        : ""
      return `已批量创建 ${batch?.total ?? 0} 张${first?.category ?? ""}·${first?.purpose ?? ""}待办（卡片 ID：${batch?.card_ids?.[0] ?? "—"}${batch && batch.card_ids && batch.card_ids.length > 1 ? `-${batch.card_ids[batch.card_ids.length - 1]}` : ""}${tail}）。`
    },
  })
  registerTool({
    name: "complete_tracking_card",
    label: "完成卡片",
    readOnly: false,
    extract: (data) => (data && typeof data === "object" ? data : null),
    fallbackSummary: (data) => {
      const card = data as { category?: string; purpose?: string; id?: number }
      return `已将${card?.category ?? ""}·${card?.purpose ?? ""}任务（卡片 ID：${card?.id ?? "—"}）标记为已完成。`
    },
  })
}
