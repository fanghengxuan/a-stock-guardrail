/** 极简 Markdown 渲染器（raw 降级 / 执行摘要用）——只支持报告实际用到的形态。 */
import type { ReactNode } from "react"
import { createElement, Fragment } from "react"

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /\*\*([^*]+)\*\*/g
  let last = 0, m: RegExpExecArray | null, key = 0
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    out.push(createElement("b", { key: key++ }, m[1]))
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

function isTableLine(s: string): boolean {
  return s.trim().startsWith("|") && s.trim().endsWith("|")
}

export function mdLite(src: string): ReactNode {
  const lines = (src || "").split("\n")
  const nodes: ReactNode[] = []
  let i = 0, key = 0
  const flushList = (items: string[]) => {
    if (items.length) nodes.push(
      createElement("ul", { key: key++ },
        items.map((it, j) => createElement("li", { key: j }, ...inline(it)))))
  }
  while (i < lines.length) {
    const line = lines[i], t = line.trim()
    if (!t) { i++; continue }
    if (isTableLine(t)) {
      const rows: string[][] = []
      while (i < lines.length && isTableLine(lines[i])) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim())
        if (!cells.every(c => /^[-: ]*$/.test(c))) rows.push(cells)
        i++
      }
      const [head, ...body] = rows
      nodes.push(createElement("table", { key: key++ },
        head && createElement("thead", null,
          createElement("tr", null, ...head.map((h, j) => createElement("th", { key: j }, ...inline(h))))),
        createElement("tbody", null,
          ...body.map((r, j) => createElement("tr", { key: j },
            ...r.map((c, k2) => createElement("td", { key: k2 }, ...inline(c))))))))
      continue
    }
    const hm = t.match(/^(#{2,4})\s+(.*)$/)
    if (hm) { nodes.push(createElement(hm[1].length >= 4 ? "h5" : "h4", { key: key++ }, ...inline(hm[2]))); i++; continue }
    if (t.startsWith(">")) {
      const q: string[] = []
      while (i < lines.length && lines[i].trim().startsWith(">")) { q.push(lines[i].trim().replace(/^>\s?/, "")); i++ }
      nodes.push(createElement("blockquote", { key: key++ }, ...inline(q.join(" ")))); continue
    }
    if (/^[-*]\s+/.test(t)) {
      const items: string[] = []
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) { items.push(lines[i].trim().replace(/^[-*]\s+/, "")); i++ }
      flushList(items); continue
    }
    if (/^\d+[.、]\s*/.test(t)) {
      const items: string[] = []
      while (i < lines.length && /^\d+[.、]\s*/.test(lines[i].trim())) { items.push(lines[i].trim().replace(/^\d+[.、]\s*/, "")); i++ }
      nodes.push(createElement("ul", { key: key++, style: { listStyle: "none" } },
        items.map((it, j) => createElement("li", { key: j }, createElement("b", null, `${j + 1}　`), ...inline(it))))); continue
    }
    const para: string[] = [t]; i++
    while (i < lines.length && lines[i].trim() && !isTableLine(lines[i])
      && !/^(#{2,4}\s|>|[-*]\s|\d+[.、]\s)/.test(lines[i].trim())) { para.push(lines[i].trim()); i++ }
    nodes.push(createElement(Fragment, { key: key++ }, createElement("p", null, ...inline(para.join(" ")))))
  }
  return nodes
}
