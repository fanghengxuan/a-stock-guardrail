/** 流式备忘录状态机：SSE 事件 → 逐段填充；done 权威整量替换。 */
import type { DashboardBlocks, DashboardData } from "../types/memo"
import { PROGRESS, TOTAL_SECTIONS } from "./constants"

export interface DashState {
  status: "idle" | "connecting" | "streaming" | "saving" | "done" | "error"
  blocks: DashboardBlocks
  lit: string[]
  phase: string | null
  progress: { mdBytes: number; reasoningBytes: number; sections: number; elapsed: number }
  resolved: { code: string; name: string } | null
  error: string | null
  validateIssues: DashboardData["validate_issues"]
  parseFallbacks: string[]
  streaming: boolean
  thought: { title: string | null; tail: string; at: number } | null
}

export const initialState: DashState = {
  status: "idle",
  blocks: {},
  lit: [],
  phase: null,
  progress: { mdBytes: 0, reasoningBytes: 0, sections: 0, elapsed: 0 },
  resolved: null,
  error: null,
  validateIssues: [],
  parseFallbacks: [],
  streaming: false,
  thought: null,
}

export type DashAction =
  | { type: "start"; payload?: { query?: string } }
  | { type: "connected"; payload: { stock_code: string; stock_name: string } }
  | { type: "resolved"; payload: { stock_code: string; stock_name: string } }
  | { type: "phase"; payload: { phase: string; elapsed?: number } }
  | { type: "section_done"; payload: { section_id: string; title: string; md_bytes: number; blocks: DashboardBlocks } }
  | { type: "heartbeat"; payload: { elapsed: number; md_bytes?: number; sections?: number } }
  | { type: "thought"; payload: { title: string | null; tail: string } }
  | { type: "report_saved"; payload: { path: string; issues_count: number } }
  | { type: "done"; payload: { dashboard: DashboardData; issues?: unknown[]; parse_fallbacks?: string[] } }
  | { type: "error"; payload: { message: string; code?: string } }
  | { type: "closed" }
  | { type: "reset" }

const SEC = TOTAL_SECTIONS

export function dashReducer(state: DashState, action: DashAction): DashState {
  switch (action.type) {
    case "start":
      // 先把用户输入记作目标（resolve/connected/meta 到位前就能展示标的）
      return { ...initialState, status: "connecting", streaming: true,
        resolved: { code: "", name: action.payload?.query ?? "" } }
    case "reset":
      return initialState
    case "connected":
      return { ...state, status: "connecting",
        resolved: { code: action.payload.stock_code, name: action.payload.stock_name || state.resolved?.name || "" } }
    case "resolved":
      return { ...state,
        resolved: { code: action.payload.stock_code, name: action.payload.stock_name } }
    case "phase":
      return { ...state,
        status: action.payload.phase === "saved" ? "saving" : "streaming",
        phase: action.payload.phase }
    case "section_done": {
      const incoming = action.payload.blocks ?? {}
      const keys = Object.keys(incoming)
      return { ...state, status: "streaming",
        blocks: { ...state.blocks, ...incoming },
        lit: [...state.lit, ...keys.filter(k => !state.lit.includes(k))],
        progress: { ...state.progress,
          mdBytes: action.payload.md_bytes ?? state.progress.mdBytes,
          sections: state.progress.sections + 1 } }
    }
    case "heartbeat":
      return { ...state, progress: {
        mdBytes: action.payload.md_bytes ?? state.progress.mdBytes,
        reasoningBytes: (action.payload as Record<string, number>).reasoning_bytes ?? state.progress.reasoningBytes,
        sections: action.payload.sections ?? state.progress.sections,
        elapsed: action.payload.elapsed ?? state.progress.elapsed } }
    case "thought":
      return { ...state,
        status: state.status === "connecting" ? "streaming" : state.status,
        thought: { title: action.payload.title ?? null, tail: action.payload.tail ?? "", at: Date.now() } }
    case "report_saved":
      return { ...state, status: "saving", phase: "saved" }
    case "done": {
      const d = action.payload.dashboard
      return { ...state, status: "done", streaming: false, thought: null,
        blocks: d.blocks,
        lit: Array.from(new Set([...state.lit, ...Object.keys(d.blocks)])),
        validateIssues: d.validate_issues ?? [],
        parseFallbacks: d.parse_fallbacks ?? [] }
    }
    case "error":
      return { ...state, status: "error", streaming: false,
        error: action.payload.message ?? "分析失败" }
    case "closed":
      if (state.status === "done" || state.status === "error") return state
      return { ...state, status: "error", streaming: false,
        error: state.error ?? "连接中断，备忘录未生成完整（已点亮段落保留）" }
    default: return state
  }
}

export function progressPct(s: DashState): number {
  if (s.status === "done") return 100
  const bySection = Math.min(s.progress.sections / SEC, PROGRESS.sectionCap)
  const byBytes = Math.min(s.progress.mdBytes / PROGRESS.expectedMdBytes, PROGRESS.bytesCap)
  return Math.round(Math.max(bySection, byBytes) * 100)
}
