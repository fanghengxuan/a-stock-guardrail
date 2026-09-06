/** MD 原文抽屉 — 点击数字时滑入，显示该节 MD 原文片段。 */
import { useEffect, useRef } from "react"
import type { DashboardBlocks } from "../types/memo"

interface Props {
  sectionId: string | null
  blocks: DashboardBlocks
  onClose: () => void
}

const SECTION_TITLES: Record<string, string> = {
  meta: "数据底稿", l0_quote: "行情数据", l0_financial: "财务数据",
  l0_dividend: "分红数据", l0_reverse: "反向清单数据", l0_flow: "资金流向数据",
  l0_forecast: "机构预测与评级",
  l2_hardgate: "硬门槛筛选", l2_reverse: "反向清单筛查", l2_five: "五维信号判定",
  l2_factor: "因子有效性验证", l2_capital: "资金行为画像", l2_trap: "价值陷阱识别",
  l2_quant: "量化增强模块", l2_debate: "五维视角辩论引擎", l2_stress: "悲观情景压力测试",
  l2_behavior: "行为金融学自检", l2_state: "状态机映射 + 止盈检查",
  l3_card: "决策卡", l3_summary: "执行摘要",
}

export function SourceDrawer({ sectionId, blocks, onClose }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const open = sectionId !== null

  useEffect(() => {
    if (open && ref.current) ref.current.scrollTop = 0
  }, [open, sectionId])

  // 找到该节原文：优先 appendix items，其次 card_raw
  let rawMd = ""
  const title = sectionId ? (SECTION_TITLES[sectionId] || sectionId) : ""
  if (sectionId) {
    const ap = blocks.appendix
    if (ap?.items) {
      const item = ap.items.find(i => i.section_id === sectionId)
      if (item) rawMd = item.raw_md
    }
    if (!rawMd && sectionId === "l3_card" && blocks.card_raw?.text) {
      rawMd = blocks.card_raw.text
    }
  }

  return (
    <>
      <div className={`drawer-overlay ${open ? "open" : ""}`} onClick={onClose} />
      <div ref={ref} className={`drawer ${open ? "open" : ""}`}>
        <button className="close" onClick={onClose} aria-label="关闭">✕</button>
        <h3>{title || "报告原文"}</h3>
        {rawMd ? (
          <pre>{rawMd}</pre>
        ) : sectionId ? (
          <p style={{ fontSize: 12, color: "var(--ink-3)" }}>该节原文将在报告完成后可查看。</p>
        ) : null}
      </div>
    </>
  )
}
