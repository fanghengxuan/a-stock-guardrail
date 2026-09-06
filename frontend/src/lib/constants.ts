/** 备忘录前端调参常量 */

export const DEAD_LINK_MS = 150_000

export const TOTAL_SECTIONS = 26

export const PROGRESS = {
  expectedMdBytes: 45_000,
  sectionCap: 0.92,
  bytesCap: 0.85,
} as const

export const PHASE_LABEL: Record<string, string> = {
  resolve: "解析标的",
  fetch: "L1 取数",
  drilldown: "补齐数据",
  write: "报告直写",
  validate: "章节校验",
  saved: "落盘",
}
