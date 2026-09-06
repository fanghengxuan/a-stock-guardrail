/** 历史备忘录详情：GET /api/reports/{code} 秒渲染。 */
import { useEffect, useState } from "react"
import { useParams } from "react-router-dom"
import { getDashboard } from "../lib/reportStream"
import { MemoView } from "../memoView"
import type { DashboardData } from "../types/memo"

export default function ReportPage() {
  const { code = "" } = useParams()
  const [dash, setDash] = useState<DashboardData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let on = true
    setDash(null)
    setError(null)
    getDashboard(code)
      .then(d => {
        if (!on) return
        setDash(d)
        document.title = `${d.blocks.meta?.name ?? code} ${code} · 投资备忘录`
      })
      .catch((e: Error) => { if (on) setError(`读取失败：${e.message}`) })
    return () => { on = false }
  }, [code])

  if (error) {
    return (
      <div className="wrap">
        <p className="err-banner">{error}</p>
        <p><a href="/" style={{ color: "var(--navy)" }}>← 回工作台重新分析</a></p>
      </div>
    )
  }
  if (!dash) {
    return (
      <div className="wrap" style={{ paddingTop: 20 }}>
        <p className="empty">备忘录加载中…（本地数据，通常百毫秒内）</p>
      </div>
    )
  }
  return (
    <MemoView
      blocks={dash.blocks}
      streaming={false}
    />
  )
}
