import { API_BASE, authedFetch } from "./agentClient"

export interface ImportJob {
  id: string; filename: string; account: string; status: string; progress: number
  error: string; attempts: number; created_at: string; result: Record<string, unknown>
}
export interface ImportGroup {
  id: string; merchant: string; product: string; nature: string; count: number; total_cents: number
  pending: number; categories: Record<string, number>; row_ids: number[]
  samples: { row_id: number; tx_time: string; amount_cents: number; category: string
    category_status: string; category_reason: string }[]
}
export interface ImportDetail extends ImportJob {
  counts: Record<string, number>; pending: number; groups: ImportGroup[]
}
export interface ImportRow {
  row_id: number; data: Record<string, unknown>; error: string; disposition: string; excluded: boolean
}
async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await authedFetch(`${API_BASE}/api/bills/imports${path}`, init)
  const data = await response.json()
  if (!response.ok) throw new Error(data.detail || "账单导入操作失败")
  return data as T
}
const json = (body: unknown): RequestInit => ({
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
})
export const listBillImports = () => api<ImportJob[]>("")
export const getBillImport = (id: string) => api<ImportDetail>(`/${id}`)
export function startBillImport(file: File, account: string) {
  const body = new FormData(); body.append("file", file); body.append("account", account)
  return api<ImportJob>("", { method: "POST", body })
}
export const getImportRows = (id: string, offset: number, errors = false) =>
  api<ImportRow[]>(`/${id}/rows?offset=${offset}&limit=100&errors_only=${errors}`)
export const editImportRows = (id: string, body: {
  row_ids: number[]; category?: string; excluded?: boolean; save_rule?: boolean; adopt_legacy?: boolean
}) => api(`/${id}/rows`, json(body))
export const confirmBillImport = (id: string, excludeErrors: boolean) =>
  api<ImportJob>(`/${id}/confirm`, json({ exclude_errors: excludeErrors }))
export const retryBillImport = (id: string) => api<ImportJob>(`/${id}/retry`, json({}))
export const deleteBillImport = (id: string) => api(`/${id}`, { method: "DELETE" })
export async function downloadImportErrors(id: string) {
  const response = await authedFetch(`${API_BASE}/api/bills/imports/${id}/errors`)
  if (!response.ok) throw new Error("错误报告下载失败")
  const url = URL.createObjectURL(await response.blob())
  const a = document.createElement("a"); a.href = url; a.download = "账单导入错误.csv"; a.click()
  URL.revokeObjectURL(url)
}
export async function setRefundLink(refundId: number, originalId?: number) {
  const response = await authedFetch(`${API_BASE}/api/bills/${refundId}/refund-link`,
    originalId ? json({ original_bill_id: originalId }) : { method: "DELETE" })
  const data = await response.json()
  if (!response.ok) throw new Error(data.detail || "退款关联失败")
}
