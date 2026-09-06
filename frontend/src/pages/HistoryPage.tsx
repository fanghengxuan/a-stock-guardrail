/** 历史记录：读后端 /api/reports，点开秒渲染备忘录详情。 */
import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { listReports } from "../lib/reportStream"
import { useReportStreamCtx } from "../ReportStreamProvider"
import type { ReportSummary } from "../types/memo"
import { fmt } from "../lib/format"
import { TOTAL_SECTIONS } from "../lib/constants"

export default function HistoryPage() {
  const [reports, setReports] = useState<ReportSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { state } = useReportStreamCtx()

  useEffect(() => {
    let on = true
    listReports()
      .then(r => { if (on) setReports(r.reports) })
      .catch((e: Error) => { if (on) setError(`读取历史失败：${e.message}（后端是否已启动？）`) })
    return () => { on = false }
  }, [])

  return (
    <>
      <header className="topbar">
        <div className="topbar-in">
          <div className="brand"><span className="dot" />备忘录<small>· 历史记录</small></div>
          <nav>
            <Link to="/">工作台</Link>
            {state.streaming && (
              <Link to="/" style={{ color: "var(--seal)", fontWeight: 600 }}>
                ▍{state.resolved?.name || state.resolved?.code || "分析"}进行中 {Math.min(state.progress.sections, TOTAL_SECTIONS)} 节已点亮
              </Link>
            )}
          </nav>
        </div>
      </header>
      <div className="wrap" style={{ paddingTop: 20 }}>
        <div style={{ marginBottom: 16 }}>
          <h2 style={{ fontFamily: "var(--serif)", fontWeight: 700, fontSize: 20 }}>历史分析</h2>
          <p style={{ fontSize: 12, color: "var(--ink-3)" }}>备忘录生成后自动落盘，点开即渲染、不跑模型。</p>
          {error && <p className="err-banner">{error}</p>}
        </div>
        <div className="hist-list">
          {reports === null && !error && <p className="empty">加载中…</p>}
          {reports?.length === 0 && <p className="empty">暂无记录——先去工作台跑一份分析。</p>}
          {reports?.map(r => (
            <Link to={`/reports/${r.code}`} className="hist-card" key={`${r.code}-${r.mtime}`}>
              <div>
                <span className="nm">{r.name || r.code}<small>{r.code}</small></span>
                <div className="info">
                  <span>{r.analysis_date ?? ""} · {new Date(r.mtime * 1000).toLocaleString("zh-CN", { hour12: false })}</span>
                </div>
              </div>
              <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                <span className="price">¥{fmt(r.price)}</span>
                {r.change_pct != null && (
                  <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: r.change_pct >= 0 ? "var(--up)" : "var(--down)" }}>
                    {r.change_pct > 0 ? "+" : ""}{r.change_pct}%
                  </span>
                )}
                {r.state && (
                  <span className="stamp-mini">{r.state}{r.position_cap_pct != null ? ` ·≤${r.position_cap_pct}%` : ""}{r.confidence ? ` ·${r.confidence}` : ""}</span>
                )}
              </div>
            </Link>
          ))}
        </div>
      </div>
    </>
  )
}
