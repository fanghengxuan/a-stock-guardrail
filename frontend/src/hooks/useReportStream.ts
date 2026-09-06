/** 流式备忘录 hook：reducer + AbortController + 死链判据。 */
import { useCallback, useEffect, useMemo, useReducer, useRef } from "react"
import { dashReducer, initialState } from "../lib/dashReducer"
import type { DashAction } from "../lib/dashReducer"
import { streamReport } from "../lib/reportStream"
import { DEAD_LINK_MS } from "../lib/constants"

export function useReportStream() {
  const [state, dispatch] = useReducer(dashReducer, initialState)
  const abortRef = useRef<AbortController | null>(null)
  const seqRef = useRef(0)
  const deadRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const armDeadTimer = useCallback((mySeq: number) => {
    if (deadRef.current) clearTimeout(deadRef.current)
    deadRef.current = setTimeout(() => {
      if (seqRef.current === mySeq) {
        dispatch({ type: "error", payload: { message: "150 秒无任何事件，判定链路中断" } })
        abortRef.current?.abort()
      }
    }, DEAD_LINK_MS)
  }, [])

  const start = useCallback((stockCode: string, forceRefresh = false, deepThink = false) => {
    abortRef.current?.abort()
    const mySeq = ++seqRef.current
    const ctrl = new AbortController()
    abortRef.current = ctrl
    dispatch({ type: "start", payload: { query: stockCode } })
    armDeadTimer(mySeq)
    const safe = (e: string, d: Record<string, unknown>): void => {
      if (seqRef.current !== mySeq) return
      armDeadTimer(mySeq)
      dispatch({ type: e, payload: d } as DashAction)
    }
    streamReport(stockCode, { forceRefresh, deepThink, signal: ctrl.signal }, safe)
      .then(() => { if (seqRef.current === mySeq) dispatch({ type: "closed" }) })
      .catch((err: unknown) => {
        if (seqRef.current !== mySeq || (err instanceof Error && err.name === "AbortError")) return
        dispatch({ type: "error", payload: { message: `请求失败：${String((err as Error).message ?? err)}（后端是否已启动？）` } })
      })
      .finally(() => { if (deadRef.current) clearTimeout(deadRef.current) })
  }, [armDeadTimer])

  const reset = useCallback(() => {
    abortRef.current?.abort()
    seqRef.current++
    dispatch({ type: "reset" })
  }, [])

  useEffect(() => () => { if (deadRef.current) clearTimeout(deadRef.current) }, [])

  return useMemo(() => ({ state, start, reset }), [state, start, reset])
}
