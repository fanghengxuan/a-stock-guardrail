/** 段三：风险画像 — 最大回撤/VaR/止损距离 + 风险排序 */
import type { StressBlock, QuantBlock, RisksBlock, AnchorsBlock, QuoteBlock } from "../types/memo"
import { Placeholder } from "../components/Skeletons"

interface Props {
  stress?: StressBlock; quant?: QuantBlock; risks?: RisksBlock
  anchors?: AnchorsBlock; quote?: QuoteBlock
  streaming: boolean
}

function fmt(n: number | null | undefined, d = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—"
  return n.toFixed(d).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "")
}

export function RiskProfile({ stress, quant, risks, anchors, quote, streaming }: Props) {
  const price = quote?.price
  const stopLoss = anchors?.stop_loss?.price
  const maxDD = stopLoss && price ? round1((stopLoss / price - 1) * 100) : null
  const pesRange = stress?.pes_range
  const pesDist = pesRange && price ? round1((pesRange.lo / price - 1) * 100) : null

  const riskItems = risks?.items ?? []
  const heatCls = (h: boolean) => h ? "h3" : "h2"
  const heatDots = (h: boolean) => h ? "●●●○○" : "●●○○○"

  return (
    <section id="risk">
      <div className="sec-h"><span className="no">03</span><h2>风险画像</h2><span className="tag">能亏多少？最大回撤多少？</span></div>
      {stress || quant || risks ? (
        <>
          {/* 风险量化 */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0 28px", padding: "8px 0", borderBottom: "1px solid var(--line)", marginBottom: 8 }}>
            {maxDD != null && (
              <div><span style={{ fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: ".06em" }}>最大回撤</span>
                <div className="mono" style={{ fontSize: 16, fontWeight: 600, color: maxDD < -15 ? "var(--seal)" : "var(--warn)" }}>{maxDD}%</div>
                <div style={{ fontSize: 10, color: "var(--ink-3)" }}>¥{fmt(stopLoss!)}/股</div>
              </div>
            )}
            {quant?.var_pct != null && (
              <div><span style={{ fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: ".06em" }}>5日VaR</span>
                <div className="mono" style={{ fontSize: 16, fontWeight: 600, color: "var(--warn)" }}>{fmt(quant.var_pct)}%</div>
                <div style={{ fontSize: 10, color: "var(--ink-3)" }}>¥{fmt(quant.var_amount)}元</div>
              </div>
            )}
            {stopLoss != null && price != null && (
              <div><span style={{ fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: ".06em" }}>止损线</span>
                <div className="mono" style={{ fontSize: 16, fontWeight: 600, color: "var(--seal)" }}>¥{fmt(stopLoss)}</div>
                <div style={{ fontSize: 10, color: "var(--ink-3)" }}>距现价 {round1((stopLoss / price - 1) * 100)}%</div>
              </div>
            )}
            {pesDist != null && pesRange && (
              <div><span style={{ fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: ".06em" }}>悲观情景</span>
                <div className="mono" style={{ fontSize: 16, fontWeight: 600, color: "var(--warn)" }}>¥{fmt(pesRange.lo)}-{fmt(pesRange.hi)}</div>
                <div style={{ fontSize: 10, color: "var(--ink-3)" }}>距现价 {pesDist}%</div>
              </div>
            )}
          </div>

          {/* 风险排序 */}
          {riskItems.length > 0 && (
            <div>
              {riskItems.map((r) => (
                <div className="risk-row" key={r.no}>
                  <span className="no">{r.no}</span>
                  <span className="txt">{r.text}{r.chip && <span style={{ marginLeft: 6, fontSize: 10, color: "var(--seal)" }}>[{r.chip}]</span>}</span>
                  <span className={`heat ${heatCls(r.hot)}`}>{heatDots(r.hot)}</span>
                </div>
              ))}
            </div>
          )}

          {/* 压力测试路径 */}
          {stress?.paths && stress.paths.length > 0 && (
            <details style={{ marginTop: 10 }}>
              <summary>悲观情景路径（{stress.paths.length} 条）</summary>
              <table className="act-table" style={{ marginTop: 6 }}>
                <thead><tr><th>风险路径</th><th>概率</th><th>影响</th></tr></thead>
                <tbody>
                  {stress.paths.map((p, i) => (
                    <tr key={i}><td>{p.risk}</td><td className="mono">{p.prob}</td><td style={{ fontSize: 11, color: "var(--ink-2)" }}>{p.impact}</td></tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </>
      ) : streaming ? <Placeholder label="风险画像（需压力测试数据）" /> : null}
    </section>
  )
}

function round1(n: number): number { return Math.round(n * 10) / 10 }
