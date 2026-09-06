/** 导航 active 高亮（scroll-spy） */
import { useEffect, useState } from "react"
import type { RefObject } from "react"

const IDS = ["decision", "valuation", "risk", "thesis", "action", "trace"] as const

export function useScrollSpy(container: RefObject<HTMLElement | null>): string {
  const [active, setActive] = useState<string>("")
  useEffect(() => {
    const root = container.current
    if (!root || typeof IntersectionObserver === "undefined") return
    const sections = IDS.map(id => root.querySelector(`#${id}`)).filter((el): el is Element => !!el)
    if (!sections.length) return
    const visible = new Map<string, number>()
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) visible.set(e.target.id, e.intersectionRatio)
        else visible.delete(e.target.id)
      }
      if (!visible.size) return
      let best = "", bestRatio = -1
      for (const [id, ratio] of visible) { if (ratio > bestRatio) { best = id; bestRatio = ratio } }
      if (best) setActive(best)
    }, { rootMargin: "-15% 0px -70% 0px", threshold: [0, 0.25, 0.5, 1] })
    sections.forEach(s => io.observe(s))
    return () => io.disconnect()
  }, [container])
  return active
}
