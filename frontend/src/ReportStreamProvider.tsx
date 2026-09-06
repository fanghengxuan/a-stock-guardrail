/** 流式分析生命周期提升到路由之上：切页不打断进行中的分析。
 *  深度思考开关状态提升至此：启动时回显后端全局默认（与 .env 联动），
 *  切页不丢失，提交时随请求传每单选择。 */
import { createContext, useCallback, useContext, useEffect, useState } from "react"
import type { ReactNode } from "react"
import { useReportStream } from "./hooks/useReportStream"
import { getConfig } from "./lib/reportStream"

type Ctx = ReturnType<typeof useReportStream> & {
  deepThink: boolean
  setDeepThink: (v: boolean) => void
}

const ReportStreamCtx = createContext<Ctx | null>(null)

export function ReportStreamProvider({ children }: { children: ReactNode }) {
  const hook = useReportStream()
  const [deepThink, setDeepThink] = useState(false)
  useEffect(() => {
    let alive = true
    getConfig()
      .then(c => { if (alive) setDeepThink(!!c.deep_think_default) })
      .catch(() => { /* 后端未启动等：保持 false 默认 */ })
    return () => { alive = false }
  }, [])
  const start = useCallback(
    (stockCode: string, forceRefresh = false, deep?: boolean) =>
      hook.start(stockCode, forceRefresh, deep ?? deepThink),
    [hook, deepThink],
  )
  const value: Ctx = { ...hook, start, deepThink, setDeepThink }
  return <ReportStreamCtx.Provider value={value}>{children}</ReportStreamCtx.Provider>
}

export function useReportStreamCtx(): Ctx {
  const c = useContext(ReportStreamCtx)
  if (!c) throw new Error("useReportStreamCtx 必须在 ReportStreamProvider 内使用")
  return c
}