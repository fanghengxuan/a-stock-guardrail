/** 段一：决策头 — 标的+结论+关键指标条 */
import type { MetaBlock, QuoteBlock, StampBlock, SummaryBlock } from "../types/memo"
import { Placeholder } from "../components/Skeletons"

interface Props {
  meta?: MetaBlock; quote?: QuoteBlock; stamp?: StampBlock; summary?: SummaryBlock
  streaming: boolean
}

function fmt(n: number | null | undefined, d = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—"
  return n.toFixed(d).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "")
}
function fmtPct(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—"
  return `${n > 0 ? "+" : ""}${n.toFixed(1).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "")}%`
}

export function DecisionHead({ meta, quote, stamp, summary, streaming }: Props) {
  return (
    <section id="decision" className="memo-section">
      <div className="decision-head">
        {meta ? (
          <>
            <h1>{meta.name}</h1>
            <div className="sub">{meta.code}.{meta.exchange ?? ""} · {meta.analysis_date}</div>
            {stamp ? (
              <div className="verdict-bar">
                <span className="seal-zh">{stamp.zh}</span>
                <span className="seal-meta">
                  {stamp.position_cap_pct != null && <>≤{fmt(stamp.position_cap_pct, 0)}%</>}
                  {stamp.confidence && <>置信度 {stamp.confidence}</>}
                  {stamp.sub && <span style={{ fontSize: 11 }}>{stamp.sub}</span>}
                </span>
              </div>
            ) : streaming ? <Placeholder label="状态机裁定" /> : null}
            {stamp?.reason && <div className="reason">{stamp.reason.replace(/^>\s*/, "")}</div>}
            {quote ? (
              <div className="key-metrics">
                <span><span className="lbl">最新价</span> ¥{fmt(quote.price)}<span className={`chg ${quote.up ? "up" : "dn"}`}>{fmtPct(quote.change_pct)}</span></span>
                <span><span className="lbl">PE</span> {fmt(quote.pe)}</span>
                <span><span className="lbl">PB</span> {fmt(quote.pb)}</span>
                <span><span className="lbl">股息率</span> {fmt(quote.dy)}%</span>
                {quote.market_cap_yi != null && <span><span className="lbl">市值</span> {fmt(quote.market_cap_yi, 0)}亿</span>}
              </div>
            ) : streaming ? <Placeholder label="行情数据" /> : null}
            {summary?.headline && <p style={{ marginTop: 10, fontSize: 13, color: "var(--ink-2)", maxWidth: 680 }}>{summary.headline}</p>}
            {summary?.one_liner && <p style={{ marginTop: 4, fontSize: 12, color: "var(--navy)", fontFamily: "var(--serif)", fontWeight: 600 }}>{summary.one_liner}</p>}
          </>
        ) : streaming ? <Placeholder label="标的解析" /> : null}
      </div>
    </section>
  )
}
