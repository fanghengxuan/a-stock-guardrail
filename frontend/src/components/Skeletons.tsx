/** 骨架占位 — 流式中缺失段落显示，固定高度防布局跳动。 */
export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skel-block">
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className={`skel skel-line ${i % 3 === 0 ? "w60" : i % 3 === 1 ? "w80" : "w40"}`} />
      ))}
    </div>
  )
}

export function Placeholder({ label }: { label: string }) {
  return (
    <div style={{ padding: "8px 0", fontSize: 12, color: "var(--ink-3)", fontFamily: "var(--mono)" }}>
      ▍{label} 采集中<span style={{ animation: "skel-pulse 1s infinite", display: "inline-block" }}>···</span>
    </div>
  )
}
