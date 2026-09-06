/** 段五：操作纪律 — 锚点表 + 止损 + 清仓条件 + 执行摘要 */
import type { AnchorsBlock, ExitsBlock, SummaryBlock } from "../types/memo"
import { Placeholder } from "../components/Skeletons"
import { mdLite } from "../lib/md-lite"
import { fmt } from "../lib/format"

interface Props {
  anchors?: AnchorsBlock; exits?: ExitsBlock; summary?: SummaryBlock
  streaming: boolean
}

export function ActionPlan({ anchors, exits, summary, streaming }: Props) {
  const tiers = anchors?.tiers ?? []
  const stop = anchors?.stop_loss
  const clearList = exits?.clear_list ?? []
  const watchList = exits?.watch_list ?? []

  return (
    <section id="action">
      <div className="sec-h"><span className="no">05</span><h2>操作纪律</h2><span className="tag">在哪买？在哪卖？</span></div>

      {anchors ? (
        <>
          {anchors.current_action && (
            <div style={{ fontSize: 12, color: "var(--navy)", marginBottom: 8, fontFamily: "var(--serif)", fontWeight: 600 }}>
              {anchors.current_action}
            </div>
          )}
          <table className="act-table">
            <thead><tr><th>档位</th><th>说明</th><th>价格区间</th><th>动作</th></tr></thead>
            <tbody>
              {tiers.map((t, i) => (
                <tr key={i}>
                  <td><b style={{ fontSize: 11 }}>{t.tier}</b>{t.label && <span style={{ fontSize: 10, color: "var(--ink-3)" }}> {t.label}</span>}</td>
                  <td style={{ fontSize: 11, color: "var(--ink-3)" }}>{t.note ?? "—"}</td>
                  <td className="mono">{t.lo != null ? `${fmt(t.lo)}${t.hi != null && t.hi !== t.lo ? ` – ${fmt(t.hi)}` : ""}` : "—"}</td>
                  <td style={{ fontSize: 11 }}>{t.action ?? "—"}</td>
                </tr>
              ))}
              {stop && (
                <tr className="stop">
                  <td><b style={{ fontSize: 11 }}>止损线</b></td>
                  <td style={{ fontSize: 11, color: "var(--ink-3)" }}>{stop.note ?? "—"}</td>
                  <td className="mono">{fmt(stop.price)} 元</td>
                  <td style={{ fontSize: 11 }}>触发即出</td>
                </tr>
              )}
            </tbody>
          </table>
        </>
      ) : streaming ? <Placeholder label="锚点操作建议" /> : null}

      {/* 清仓条件 */}
      {clearList.length > 0 ? (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 4 }}>清仓条件（{clearList.length} 项 · 任一触发即执行）</div>
          <ul className="check-list">
            {clearList.map((c, i) => (
              <li key={i}><span className="box" /><span>{mdLite(c.text)}</span></li>
            ))}
          </ul>
        </div>
      ) : streaming ? <Placeholder label="清仓条件" /> : null}

      {/* 跟踪验证点 */}
      {watchList.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 4 }}>跟踪验证点（下次分析必查）</div>
          <ul className="check-list">
            {watchList.map((w, i) => (<li key={i}><span className="box" /><span>{w}</span></li>))}
          </ul>
        </div>
      )}

      {/* 执行摘要 */}
      {summary ? (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 4 }}>执行摘要</div>
          {summary.core_logic?.map((l, i) => (
            <div key={i} style={{ fontSize: 12, color: "var(--ink-2)", marginBottom: 4 }}>
              <b style={{ color: "var(--ink)" }}>逻辑{i + 1}：</b>{l}
            </div>
          ))}
          {summary.action_md && <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 6 }}>{mdLite(summary.action_md)}</div>}
        </div>
      ) : streaming ? <Placeholder label="执行摘要" /> : null}
    </section>
  )
}
