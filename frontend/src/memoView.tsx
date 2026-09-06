/** 备忘录主视图 — 组装段落，工作台（流式）与历史详情（一次性）共用。 */
import { useRef } from "react"
import { Link } from "react-router-dom"
import type { DashboardBlocks } from "./types/memo"
import { DecisionHead } from "./sections/DecisionHead"
import { Valuation } from "./sections/Valuation"
import { RiskProfile } from "./sections/RiskProfile"
import { Thesis } from "./sections/Thesis"
import { ActionPlan } from "./sections/ActionPlan"
import { DeepDive } from "./sections/DeepDive"
import { useScrollSpy } from "./hooks/useScrollSpy"

interface Props {
  blocks: DashboardBlocks
  streaming: boolean
}

const NAV = [
  { id: "decision", label: "决策头" },
  { id: "valuation", label: "估值" },
  { id: "risk", label: "风险" },
  { id: "thesis", label: "论证" },
  { id: "action", label: "操作" },
  { id: "detail", label: "明细" },
] as const

export function MemoView({ blocks, streaming }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const active = useScrollSpy(ref)

  return (
    <div ref={ref} className={streaming ? "streaming" : undefined}>
      <header className="topbar">
        <div className="topbar-in">
          <div className="brand"><span className="dot" />备忘录<small>· A股投资决策系统</small></div>
          <nav>
            {NAV.map(n => (
              <a key={n.id} href={`#${n.id}`} className={active === n.id ? "on" : undefined}>{n.label}</a>
            ))}
            <Link to="/">工作台</Link>
            <Link to="/reports">历史</Link>
          </nav>
        </div>
      </header>

      <div className="wrap memo">
        <DecisionHead meta={blocks.meta} quote={blocks.quote} stamp={blocks.stamp}
          summary={blocks.summary} streaming={streaming} />
        <Valuation ruler={blocks.ruler} quote={blocks.quote} quant={blocks.quant}
          metrics={blocks.metrics} streaming={streaming} />
        <RiskProfile stress={blocks.stress} quant={blocks.quant} risks={blocks.risks}
          anchors={blocks.anchors} quote={blocks.quote} streaming={streaming} />
        <Thesis gates={blocks.gates} signals={blocks.signals} debate={blocks.debate}
          traps={blocks.traps} roe={blocks.roe} streaming={streaming} />
        <ActionPlan anchors={blocks.anchors} exits={blocks.exits}
          summary={blocks.summary} streaming={streaming} />
        <DeepDive pipeline={blocks.pipeline} position={blocks.position} peer={blocks.peer}
          deviation={blocks.deviation} pq={blocks.pq} health={blocks.health}
          veto={blocks.veto} chips={blocks.chips} guide={blocks.guide}
          streaming={streaming} />

        {!streaming && Object.keys(blocks).length === 0 && (
          <div className="wrap"><p className="empty">暂无备忘录数据。</p></div>
        )}

        <footer className="memo-foot">
          <p>以上内容为个股信息整理与研究分析，不构成投资建议。</p>
        </footer>
      </div>
    </div>
  )
}