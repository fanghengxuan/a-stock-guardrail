/** SSE 客户端 + 历史 API */
import { readSSEStream } from "./sse"
import type { DashboardData, ReportSummary } from "../types/memo"

const API_BASE: string =
  (import.meta.env?.VITE_API_BASE as string | undefined) ?? "http://localhost:8000"

export const reportMdUrl = (code: string): string =>
  `${API_BASE}/api/reports/${code}/report.md`

export const getConfig = (): Promise<{ deep_think_default: boolean }> =>
  getJson(`${API_BASE}/api/config`)

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return (await res.json()) as T
}

export const listReports = (): Promise<{ reports: ReportSummary[] }> =>
  getJson(`${API_BASE}/api/reports`)

export const getDashboard = (code: string): Promise<DashboardData> =>
  getJson(`${API_BASE}/api/reports/${code}`)

export async function streamReport(
  stockCode: string,
  opts: { forceRefresh?: boolean; deepThink?: boolean; signal?: AbortSignal },
  onEvent: (event: string, data: Record<string, unknown>) => void,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/report/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      stock_code: stockCode,
      force_refresh: !!opts.forceRefresh,
      deep_think: !!opts.deepThink,
    }),
    signal: opts.signal,
  })
  if (!res.ok && res.headers.get("content-type")?.includes("json")) {
    const detail = await res.json().catch(() => ({}))
    onEvent("error", { message: (detail as { detail?: string }).detail ?? `HTTP ${res.status}` })
    return
  }
  await readSSEStream(res, (event, data) => {
    let parsed: Record<string, unknown>
    try { parsed = JSON.parse(data) } catch { return }
    onEvent(event, parsed)
  })
}
