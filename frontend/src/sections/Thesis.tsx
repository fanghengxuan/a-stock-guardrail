/** 段四：论证链条 — 硬门槛 + 五维信号 + 辩论 + 价值陷阱(ROE轨迹) */
import type { GatesBlock, SignalsBlock, DebateBlock, TrapsBlock, RoeBlock } from "../types/memo"
import { Placeholder } from "../components/Skeletons"
import { fmt } from "../lib/format"

interface Props {
  gates?: GatesBlock; signals?: SignalsBlock; debate?: DebateBlock
  traps?: TrapsBlock; roe?: RoeBlock
  streaming: boolean
}

const SIG_ARR: Record<string, string> = { pos: "↑", neg: "↓", neu: "→" }

export function Thesis({ gates, signals, debate, traps, roe, streaming }: Props) {
  const sigItems = signals?.items ?? []
  const factions = debate?.factions ?? []
  const roeBars = roe?.bars ?? []
  const maxRoe = roeBars.length ? Math.max(...roeBars.map(b => b.v), 1) : 20
  const hardConcl = gates?.hard?.conclusion
  const checklist = gates?.hard?.checklist ?? []
  // 硬门槛只展示核验结论与逐项条件，不展示"模型一~四"这套内部判定编号；
  // 括注里的类型词（价值股/银行类等）保留，它们是有意义的资产类型判断。
  const gatePass = !/(不通过|阻断|未通过)/.test(hardConcl ?? "")
  const cleanModel = (s: string): string =>
    s
      .replace(/(?:主)?适用?模型[一二三四]（([^）]*)）/g, "$1")
      .replace(/模型[一二三四]（([^）]*)）/g, "$1")
      .replace(/模型[一二三四]/g, "")
      .replace(/主(?=[，。；])/g, "")
      .replace(/触发模型[：:]\s*/g, "")
      .replace(/\s+/g, " ")
      .trim()

  return (
    <section id="thesis">
      <div className="sec-h"><span className="no">04</span><h2>论证链条</h2><span className="tag">分析站得住吗？硬门槛过了没有？</span></div>

      {/* 硬门槛 */}
      {hardConcl || checklist.length > 0 ? (
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 4 }}>
            硬门槛{" "}
            <b style={{ color: gatePass ? "var(--teal)" : "var(--seal)" }}>
              {gatePass ? "✓ 通过" : "✗ 未通过"}
            </b>
          </div>
          {checklist.length > 0 && (
            <div className="gate-list">
              {checklist.slice(0, 8).map((c, i) => (
                <div className="gate-row" key={i}>
                  <span className={`gate-ico ${c.ok === true ? "pass" : c.ok === false ? "fail" : "wait"}`}>
                    {c.ok === true ? "✓" : c.ok === false ? "✗" : "—"}
                  </span>
                  <div><div className="item">{cleanModel(c.text)}</div></div>
                </div>
              ))}
            </div>
          )}
          {hardConcl && (
            <div style={{ fontSize: 11, color: "var(--ink-2)", marginTop: 6, borderLeft: "3px solid var(--line-2)", paddingLeft: 8 }}>
              {cleanModel(hardConcl)}
            </div>
          )}
        </div>
      ) : streaming ? <Placeholder label="硬门槛筛选" /> : null}

      {/* 五维信号 */}
      {sigItems.length > 0 ? (
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 4 }}>五维信号</div>
          {sigItems.map((s, i) => (
            <div className="sig-row" key={i}>
              <div className="nm">{s.name}<small>{s.en}</small></div>
              <div className={`arr ${s.polarity}`}>{SIG_ARR[s.polarity] || "→"}</div>
              <div className="bs">{s.basis}</div>
            </div>
          ))}
        </div>
      ) : streaming ? <Placeholder label="五维信号" /> : null}

      {/* 辩论 */}
      {factions.length > 0 ? (
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 2 }}>辩论引擎</div>
          {factions.map((f, i) => (
            <div className="db-pair" key={i}>
              <div className="db-row">
                <div className="who">{f.name}
                  {f.weight_pct != null && <small>权重 {f.weight_pct}%</small>}
                </div>
                <div className="db-bar">
                  <div className={`fill ${f.score != null && f.score < 60 ? "mid" : ""}`}
                    style={{ width: `${Math.min((f.score ?? 0), 100)}%` }} />
                </div>
                <div className="sc">{f.score != null ? fmt(f.score, 1) : "—"}</div>
              </div>
              {f.stance && <div className="db-stance">{f.stance}</div>}
            </div>
          ))}
          {debate?.composite != null && (
            <div className="db-total">
              <div className="lab">加权综合得分
                {debate.composite_text && <small>{debate.composite_text}</small>}
              </div>
              <div className="num">{fmt(debate.composite, 1)}</div>
            </div>
          )}
          {debate?.consensus && <div style={{ fontSize: 11, color: "var(--ink-2)", marginTop: 6 }}><b>共识：</b>{debate.consensus}</div>}
          {debate?.divergence && <div style={{ fontSize: 11, color: "var(--ink-2)", marginTop: 2 }}><b>分歧：</b>{debate.divergence}</div>}
        </div>
      ) : streaming ? <Placeholder label="辩论引擎" /> : null}

      {/* 价值陷阱 + ROE 轨迹 */}
      {traps ? (
        <div>
          <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 4 }}>
            价值陷阱 <b style={{ color: traps.triggered_count > 0 ? "var(--seal)" : "var(--teal)" }}>{traps.triggered_count} 项触发</b>
          </div>
          {roeBars.length > 0 && (
            <div className="roe-bars">
              {roeBars.map((b, i) => (
                <div className="roe-col" key={i}>
                  <span className="val">{fmt(b.v, 2)}</span>
                  <div className="bar" style={{ height: `${(b.v / maxRoe) * 100 * 0.85}%` }} />
                  <span className="yr">{b.yr}</span>
                </div>
              ))}
            </div>
          )}
          {traps.rows.slice(0, 3).map((r, i) => (
            <div className="gate-row" key={i}>
              <span className={`gate-ico ${r.triggered === true ? "fail" : r.triggered === false ? "pass" : "wait"}`}>{r.triggered === true ? "✗" : r.triggered === false ? "✓" : "—"}</span>
              <div><div className="item">{r.signal}</div><div className="cond">{r.evidence}</div></div>
              <span className="val">{r.verdict.slice(0, 20)}</span>
            </div>
          ))}
          {traps.conclusion_text && <div style={{ fontSize: 11, color: "var(--ink-2)", marginTop: 4 }}>{traps.conclusion_text}</div>}
        </div>
      ) : streaming ? <Placeholder label="价值陷阱识别" /> : null}
    </section>
  )
}
