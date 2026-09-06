/** 段六：分析明细 — 报告的纵深核验块（护城河/竞品/偏离度/利润质量/财务健康/
 *  一票否决/筹码/持仓指引 + 决策管线门），结构化逐块透出，重要判断依据不藏在原文里。 */
import type {
  PipelineBlock, PositionBlock, PeerBlock, DeviationBlock, PqBlock,
  HealthBlock, VetoBlock, ChipsBlock, GuideBlock,
} from "../types/memo"
import { Placeholder } from "../components/Skeletons"
import { fmt } from "../lib/format"

interface Props {
  pipeline?: PipelineBlock
  position?: PositionBlock
  peer?: PeerBlock
  deviation?: DeviationBlock
  pq?: PqBlock
  health?: HealthBlock
  veto?: VetoBlock
  chips?: ChipsBlock
  guide?: GuideBlock
  streaming: boolean
}

function H({ children }: { children: string }) {
  return <div className="dd-h">{children}</div>
}

export function DeepDive({ pipeline, position, peer, deviation, pq, health, veto, chips, guide, streaming }: Props) {
  const hasAny = pipeline?.gates?.length || position?.rank || position?.moat_rows?.length
    || peer?.rows?.length || deviation?.rows?.length || pq?.rows?.length
    || health?.rows?.length || health?.risk_rows?.length || veto?.hard_rows?.length
    || chips?.rows?.length || chips?.verdict || guide?.stop_loss_new || guide?.clear_list?.length

  if (!hasAny && streaming) return <Placeholder label="分析明细（护城河/竞品/利润质量/一票否决等）" />

  return (
    <section id="detail">
      <div className="sec-h"><span className="no">06</span><h2>分析明细</h2><span className="tag">每项核验的判定依据都摊开看</span></div>

      {/* 决策管线核验 */}
      {pipeline?.gates?.length ? (
        <div className="dd-block">
          <H>决策管线核验</H>
          <div className="pipe-row">
            {pipeline.gates.map(g => (
              <div key={g.no} className={`pipe-chip ${g.cls === "final" ? "fin" : g.cls === "warn" ? "warn" : "pass"}`}>
                <b>{g.no}. {g.name}</b>
                <span>{g.verdict?.slice(0, 26)}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {/* 行业地位与护城河 */}
      {(position?.rank || position?.moat_rows?.length) ? (
        <div className="dd-block">
          <H>行业地位与护城河</H>
          {position.rank && (
            <div className="dd-verdict-line">
              <b className="dd-strong">{position.rank}</b>
              {position.second_discount && (
                <span className={`dd-chip ${position.second_discount === "适用" ? "warn" : ""}`}>老二折价：{position.second_discount}</span>
              )}
              {position.moat_total != null && (
                <span className="dd-chip navy">护城河 {position.moat_total} 分 · {position.moat_grade}</span>
              )}
            </div>
          )}
          {position.moat_rows?.length ? (
            <table className="dd-tbl">
              <thead><tr><th style={{ width: 90 }}>维度</th><th style={{ width: 60 }}>权重</th><th style={{ width: 130 }}>得分</th><th>依据</th></tr></thead>
              <tbody>
                {position.moat_rows.map((r, i) => (
                  <tr key={i}>
                    <td>{r.dim}</td>
                    <td className="mono">{r.weight ?? "—"}</td>
                    <td>
                      <div className="mini-bar"><div className={`mini-fill ${(r.score ?? 0) < 3 ? "mid" : ""}`} style={{ width: `${Math.min((r.score ?? 0) / 5 * 100, 100)}%` }} /></div>
                      <span className="mono mini-num">{r.score != null ? fmt(r.score, 0) : "—"}/5</span>
                    </td>
                    <td className="dd-cell-soft">{r.basis}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </div>
      ) : null}

      {/* 竞品横向对比 */}
      {peer?.rows?.length ? (
        <div className="dd-block">
          <H>竞品横向对比</H>
          <table className="dd-tbl">
            <thead><tr><th>维度</th><th>标的</th><th>行业龙头</th><th>行业中位</th><th>判定</th></tr></thead>
            <tbody>
              {peer.rows.map((r, i) => (
                <tr key={i}>
                  <td>{r.dim}</td>
                  <td className="mono">{r.target}</td>
                  <td className="mono dd-cell-soft">{r.leader ?? "—"}</td>
                  <td className="mono dd-cell-soft">{r.avg ?? "—"}</td>
                  <td>{r.verdict}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {peer.conclusion && <div className="dd-note">{peer.conclusion}</div>}
        </div>
      ) : null}

      {/* 偏离度与裁量协议 */}
      {deviation?.rows?.length ? (
        <div className="dd-block">
          <H>偏离度审查（硬门槛偏离 / 裁量协议）</H>
          <table className="dd-tbl">
            <thead><tr><th>检查项</th><th>实际值</th><th>阈值</th><th>偏离度</th><th>分级</th></tr></thead>
            <tbody>
              {deviation.rows.map((r, i) => {
                const g = r.grade ?? ""
                return (
                  <tr key={i}>
                    <td>{r.item}</td>
                    <td className="mono">{r.actual}</td>
                    <td className="mono">{r.threshold}</td>
                    <td className="mono">{r.dev}</td>
                    <td className={/重度/.test(g) ? "dd-grade-seal" : /中度/.test(g) ? "dd-grade-warn" : undefined}>{g || "—"}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {deviation.conclusion && <div className="dd-note">{deviation.conclusion}</div>}
        </div>
      ) : null}

      {/* 利润质量 P-33 */}
      {pq?.rows?.length ? (
        <div className="dd-block">
          <H>利润质量校验（P-33）</H>
          <table className="dd-tbl">
            <thead><tr><th>期间</th><th>归母净利润</th><th>扣非净利润</th><th>差值%</th><th>判定</th></tr></thead>
            <tbody>
              {pq.rows.map((r, i) => (
                <tr key={i}>
                  <td>{r.period}</td>
                  <td className="mono">{r.parent ?? "—"}</td>
                  <td className="mono">{r.deducted ?? "—"}</td>
                  <td className="mono">{r.gap ?? "—"}</td>
                  <td className={/预警/.test(r.verdict ?? "") ? "dd-grade-seal" : /存疑/.test(r.verdict ?? "") ? "dd-grade-warn" : undefined}>{r.verdict ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {pq.trend && <div className="dd-note">{pq.trend}</div>}
        </div>
      ) : null}

      {/* 财务健康度与风险分类 */}
      {(health?.rows?.length || health?.risk_rows?.length) ? (
        <div className="dd-block">
          <H>财务健康度与风险分类</H>
          {health.rows?.length ? (
            <table className="dd-tbl">
              <thead><tr><th style={{ width: 90 }}>维度</th><th style={{ width: 150 }}>得分</th><th>依据</th></tr></thead>
              <tbody>
                {health.rows.map((r, i) => (
                  <tr key={i}>
                    <td>{r.dim}</td>
                    <td>
                      <div className="mini-bar"><div className={`mini-fill ${(r.score ?? 0) <= 1 ? "mid" : ""}`} style={{ width: `${Math.min((r.score ?? 0) / 3 * 100, 100)}%` }} /></div>
                      <span className="mono mini-num">{r.score != null ? fmt(r.score, 0) : "—"}/3</span>
                    </td>
                    <td className="dd-cell-soft">{r.basis}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {health.total != null && (
            <div className="dd-verdict-line">
              <b className="dd-strong">综合 {fmt(health.total, 0)} 分</b>
              <span className="dd-chip navy">评级：{health.grade ?? "—"}</span>
            </div>
          )}
          {health.risk_rows?.length ? (
            <>
              <div className="dd-sub2">风险分类框架</div>
              <table className="dd-tbl">
                <thead><tr><th>分类</th><th style={{ width: 110 }}>可对冲性</th><th>事实</th><th style={{ width: 60 }}>影响</th></tr></thead>
                <tbody>
                  {health.risk_rows.map((r, i) => (
                    <tr key={i}>
                      <td>{r.cls}</td>
                      <td className={r.hedge?.includes("不可对冲") ? "dd-grade-seal" : "dd-grade-teal"}>{r.hedge}</td>
                      <td className="dd-cell-soft">{r.fact}</td>
                      <td>{r.impact}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
        </div>
      ) : null}

      {/* 一票否决 */}
      {veto?.hard_rows?.length ? (
        <div className="dd-block">
          <H>一票否决 Exit 检查</H>
          {veto.triggered != null && (
            <div className="dd-verdict-line">
              <b className={veto.triggered ? "dd-strong seal" : "dd-strong teal"}>
                {veto.triggered ? "✗ 一票否决：触发 → Exit（凌驾全部仲裁）" : "✓ 一票否决：未触发"}
              </b>
            </div>
          )}
          <div className="dd-sub2">硬 Exit 逐项核验</div>
          <table className="dd-tbl">
            <thead><tr><th style={{ width: 26 }}></th><th style={{ width: 210 }}>条件</th><th>核验结果</th></tr></thead>
            <tbody>
              {veto.hard_rows.map((r, i) => {
                const res = r.result ?? ""
                const pass = /未触发|不适用/.test(res)
                const trig = /触发/.test(res) && !/未触发/.test(res)
                return (
                  <tr key={i}>
                    <td className={trig ? "dd-grade-seal" : pass ? "dd-grade-teal" : undefined}>
                      {trig ? "✗" : pass ? "✓" : "·"}
                    </td>
                    <td>{r.item}</td>
                    <td className={r.triggered === null ? "dd-cell-soft" : undefined}>{res}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {veto.comp_rows?.length ? (
            <>
              <div className="dd-sub2">综合判断权重</div>
              <table className="dd-tbl">
                <tbody>
                  {veto.comp_rows.map((r, i) => (
                    <tr key={i}>
                      <td style={{ width: 220 }}>{r.item}</td>
                      <td>{r.result}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
          {veto.conclusion && <div className="dd-note">{veto.conclusion}</div>}
        </div>
      ) : null}

      {/* 筹码与内部人成本 */}
      {(chips?.rows?.length || chips?.verdict) ? (
        <div className="dd-block">
          <H>筹码与内部人成本</H>
          {chips.rows?.length ? (
            <table className="dd-tbl">
              <thead><tr><th>字段</th><th>数值</th><th style={{ width: 110 }}>状态</th></tr></thead>
              <tbody>
                {chips.rows.map((r, i) => (
                  <tr key={i}>
                    <td>{r.field}</td>
                    <td className="dd-cell-soft">{r.value ?? "—"}</td>
                    <td>{r.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {chips.verdict && <div className="dd-note"><b>筹码判定：</b>{chips.verdict}</div>}
        </div>
      ) : null}

      {/* 持仓管理指引 */}
      {(guide?.stop_loss_new != null || guide?.zone1 || guide?.zone2 || guide?.clear_list?.length) ? (
        <div className="dd-block">
          <H>持仓管理指引</H>
          {(guide.stop_loss_new != null || guide.zone1 || guide.zone2) && (
            <div className="zone-row">
              {guide.stop_loss_new != null && (
                <div className="zone-cell">
                  <span className="zone-k">新仓止损线</span>
                  <span className="zone-v seal">{fmt(guide.stop_loss_new, 2)} 元</span>
                </div>
              )}
              {guide.zone1 && (
                <div className="zone-cell">
                  <span className="zone-k">第一观察区</span>
                  <span className="zone-v">{fmt(guide.zone1.lo, 1)} – {fmt(guide.zone1.hi, 1)} 元{guide.zone1.dy != null ? `（股息率${fmt(guide.zone1.dy, 1)}%）` : ""}</span>
                </div>
              )}
              {guide.zone2 && (
                <div className="zone-cell">
                  <span className="zone-k">第二观察区</span>
                  <span className="zone-v">{fmt(guide.zone2.lo, 1)} – {fmt(guide.zone2.hi, 1)} 元{guide.zone2.dy != null ? `（股息率${fmt(guide.zone2.dy, 1)}%）` : ""}</span>
                </div>
              )}
            </div>
          )}
          {guide.clear_list?.length ? (
            <>
              <div className="dd-sub2">基本面清仓条件</div>
              <ul className="dd-ul">
                {guide.clear_list.map((c, i) => (
                  <li key={i}>{c.replace(/^[-–—、\s]+/, "").replace(/；$/, "")}</li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}