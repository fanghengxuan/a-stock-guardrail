/** 数字格式化：只做显示格式化，绝不重算。
 *  去尾零只针对小数部分（10 → "10" 而非 "1"，10.50 → "10.5"）。 */
function strip(n: string): string {
  return n.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "")
}

export function fmt(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—"
  return strip(n.toFixed(digits))
}

export function fmtPct(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined) return "—"
  return `${n > 0 ? "+" : ""}${strip(n.toFixed(digits))}%`
}
