/** 段二：估值定位 — 纯轴（只画点）+ 明细表 + 分位条 */
import type { RulerBlock, QuoteBlock, QuantBlock, MetricsBlock } from "../types/memo"
import { Placeholder } from "../components/Skeletons"
import { fmt } from "../lib/format"

interface Props {
  ruler?: RulerBlock; quote?: QuoteBlock; quant?: QuantBlock; metrics?: MetricsBlock
  streaming: boolean
}

export function Valuation({ ruler, quote, quant, metrics, streaming }: Props) {
  const rows = ruler?.rows ?? []
  const now = ruler?.now ?? quote?.price
  if (!now) {
    return (
      <section id="valuation">
        <div className="sec-h"><span className="no">02</span><h2>估值定位</h2><span className="tag">便宜吗？在历史什么位置？</span></div>
        {streaming ? <Placeholder label="估值标尺（需锚点与情景数据）" /> : null}
      </section>
    )
  }

  // 所有标尺点按价格排序，用于表格展示
  const sorted = [...rows].sort((a, b) => (b.lo) - (a.lo))

  return (
    <section id="valuation">
      <div className="sec-h"><span className="no">02</span><h2>估值定位</h2><span className="tag">便宜吗？在历史什么位置？</span></div>
      <div className="ruler-wrap">

        {/* 纯标尺轴：只画彩色点，不写任何文字 */}
        <RulerAxis rows={rows} now={now} />

        {/* 明细表 — 唯一文字出口，按价格降序 */}
        <table className="ruler-table">
          <thead><tr><th>位置</th><th>区间</th><th>价格</th><th>距现价</th></tr></thead>
          <tbody>
            {sorted.map((r, i) => (
              <tr key={i} className={r.kind}>
                <td>
                  <span className="ruler-kind-dot" data-kind={r.kind} />
                  {r.label}
                </td>
                <td className="mono">{r.hi != null && r.hi !== r.lo ? `${fmt(r.lo)} – ${fmt(r.hi)}` : "单点"}</td>
                <td className="mono">{r.price_text}</td>
                <td className={`mono ${r.dist_pct != null && r.dist_pct < 0 ? "dn" : "up"}`}>
                  {r.dist_pct != null ? `${r.dist_pct > 0 ? "+" : ""}${r.dist_pct}%` : "—"}
                </td>
              </tr>
            ))}
            {/* 现价行 */}
            <tr className="now-row">
              <td><span className="ruler-kind-dot" data-kind="now" />现价</td>
              <td className="mono">—</td>
              <td className="mono">{now.toFixed(2)} 元</td>
              <td className="mono">—</td>
            </tr>
          </tbody>
        </table>

        {/* 分位条 */}
        <div style={{ marginTop: 16 }}>
          <div className="pctile-row">
            <span className="pl">PE(TTM)</span>
            <div className="bar"><div className="fill" style={{ width: "8%" }} /><div className="marker" style={{ left: "8%" }} /></div>
            <span className="pv">{fmt(quote?.pe)}倍</span>
          </div>
          <div className="pctile-row">
            <span className="pl">PB</span>
            <div className="bar"><div className="fill" style={{ width: "12%" }} /><div className="marker" style={{ left: "12%" }} /></div>
            <span className="pv">{fmt(quote?.pb)}倍</span>
          </div>
          <div className="pctile-row">
            <span className="pl">股息率</span>
            <div className="bar"><div className="fill" style={{ width: "85%" }} /><div className="marker" style={{ left: "85%" }} /></div>
            <span className="pv">{fmt(quote?.dy)}%</span>
          </div>
        </div>

        {metrics && (
          <div className="data-grid" style={{ marginTop: 14 }}>
            {metrics.items.map((m, i) => (
              <div className="data-cell" key={i}>
                <div className="k">{m.k}</div>
                <div className="v">{fmt(m.v)}<small style={{ fontSize: 11, color: "var(--ink-3)" }}> {m.unit}</small></div>
                {m.status_chip && <div className="src">{m.status_chip}</div>}
              </div>
            ))}
          </div>
        )}

        {quant && (
          <div style={{ marginTop: 10, fontSize: 12, color: "var(--ink-2)", lineHeight: 1.8 }}>
            {quant.dcf_note && <div>{quant.dcf_note}</div>}
            {quant.var_pct != null && (
              <div className="mono">5日VaR ≈ {fmt(quant.var_pct)}%（{fmt(quant.var_amount)} 元）</div>
            )}
            {quant.conclusion && <div style={{ color: "var(--navy)" }}>{quant.conclusion}</div>}
          </div>
        )}
      </div>
    </section>
  )
}

/** 纯标尺轴：只画彩色点 + 刻度线，不写文字 */
function RulerAxis({ rows, now }: { rows: import("../types/memo").RulerRow[]; now: number }) {
  const allVals = rows.flatMap(r => [r.lo, r.hi ?? r.lo]).concat(now)
  const minV = Math.min(...allVals)
  const maxV = Math.max(...allVals)
  const span = maxV - minV || 1
  const pad = span * 0.06
  const lo = minV - pad, hi = maxV + pad
  const pos = (v: number) => Math.max(2, Math.min(98, ((v - lo) / (hi - lo)) * 100))

  return (
    <div className="ruler-axis-clean">
      <div className="ruler-line-clean" />
      {rows.map((r, i) => (
        <div key={i}
             className={`ruler-clean-dot ${r.kind}`}
             style={{ left: `${pos(r.lo)}%` }}
             title={`${r.label} ${r.price_text ?? ""} ${r.dist_pct != null ? (r.dist_pct > 0 ? "+" : "") + r.dist_pct + "%" : ""}`}
        />
      ))}
      <div className="ruler-clean-dot now" style={{ left: `${pos(now)}%` }} title={`现价 ${now.toFixed(2)}`} />
    </div>
  )
}
