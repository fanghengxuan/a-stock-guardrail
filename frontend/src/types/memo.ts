/** 投资决策备忘录 — 前端类型契约
 *  与后端 DashboardData 结构对齐，但按备忘录六段重新组织消费视角。
 *  后端 parser 产出的 blocks 字典字段不变，前端自行映射到各段。 */

export type BlockStatus = "ok" | "partial" | "raw" | "una"

export interface BlockBase {
  status?: BlockStatus
  raw_md?: string
  error?: string
}

// ---- 后端 blocks 原始结构（parser.py 产出）----

export interface MetaBlock extends BlockBase {
  code: string
  name: string
  analysis_date: string
  model_label?: string | null
  exchange?: string | null
  manual_chip?: string | null
  sector_chip?: string | null
}

export interface QuoteBlock extends BlockBase {
  price?: number | null
  price_text?: string
  change_pct?: number | null
  up?: boolean | null
  pe?: number | null
  pe_text?: string
  pb?: number | null
  pb_text?: string
  dy?: number | null
  dy_text?: string
  dy_status?: string | null
  market_cap_yi?: number | null
  high_52w?: number | null
  low_52w?: number | null
}

export interface MetricsItem {
  k: string
  v: number | null
  unit?: string | null
  status_chip?: string | null
}
export interface MetricsBlock extends BlockBase {
  items: MetricsItem[]
}

export interface StampBlock extends BlockBase {
  en: string
  state_raw?: string | null
  zh: string
  sub?: string | null
  position_cap_pct?: number | null
  confidence?: string | null
  reason?: string | null
}

export interface PipelineGate {
  no: number
  name: string
  verdict: string
  cls: "pass" | "warn" | "final"
  note?: string | null
}
export interface PipelineBlock extends BlockBase {
  gates: PipelineGate[]
}

export interface SignalItem {
  name: string
  en?: string | null
  signal: string
  enum?: string | null
  polarity: "pos" | "neu" | "neg"
  score?: number | null
  delta?: number | null
  basis: string
}
export interface SignalsBlock extends BlockBase {
  items: SignalItem[]
  pos_count?: number | null
}

export interface Faction {
  name: string
  weight_pct?: number | null
  score?: number | null
  stance?: string | null
}
export interface DebateBlock extends BlockBase {
  factions: Faction[]
  composite?: number | null
  composite_text?: string | null
  verdict?: string | null
  consensus?: string | null
  divergence?: string | null
  qualitative?: boolean
}

export interface GateRow {
  item: string
  condition: string
  result: string
  ok: boolean | null
}
export interface ReverseRow {
  item: string
  value: string
  status: string
  ok: boolean | null
}
export interface GatesBlock extends BlockBase {
  hard?: {
    conclusion?: string | null
    rows: GateRow[]
    checklist: { text: string; ok: boolean | null }[]
  } | null
  reverse?: {
    conclusion?: string | null
    downgrades?: number | null
    rows: ReverseRow[]
  } | null
}

export interface TrapRow {
  signal: string
  evidence: string
  verdict: string
  triggered: boolean | null
}
export interface TrapsBlock extends BlockBase {
  rows: TrapRow[]
  triggered_count: number
  conclusion_text?: string | null
  trap_type?: string | null
  grading?: string | null
}

export interface RoeBar { yr: string; v: number }
export interface RoeBlock extends BlockBase {
  bars: RoeBar[]
}

export interface CapitalRow {
  type: string
  movement: string
  judgment: string
}
export interface CapitalBlock extends BlockBase {
  rows: CapitalRow[]
  pairs?: { label: string; text: string }[]
  relation?: string | null
}

export interface FactorRow {
  factor: string
  self: string
  industry: string
  verdict: string
}
export interface FactorBlock extends BlockBase {
  note?: string | null
  rows: FactorRow[]
  weights?: string | null
}

export interface BehaviorRow {
  q: string
  threshold: string
  actual: string
  verdict: string
}
export interface BehaviorBlock extends BlockBase {
  rows: BehaviorRow[]
  conclusion?: string | null
}

export interface StressBlock extends BlockBase {
  identity?: { k: string; v: string }[]
  paths?: { risk: string; prob: string; impact: string }[]
  path_notes?: string[]
  pes_range?: { lo: number; hi: number } | null
  vs_now_text?: string | null
  scenarios?: { name: string; assumption: string; anchor: string; trigger: string; duration: string }[]
  cross_rows?: { anchor: string; price: string; dist: string }[]
  verdict_bullets?: string[]
  final_line?: string | null
}

export interface QuantBlock extends BlockBase {
  dcf_executed: boolean
  dcf_note?: string | null
  alt_rows?: { method: string; result: string }[]
  alt_notes?: string[]
  var_pct?: number | null
  var_amount?: number | null
  conclusion?: string | null
  primary_method?: string | null
  methods?: { method: string; text: string | null }[]
  bond_like?: boolean
}

export interface RulerRow {
  label: string
  lo: number
  hi?: number | null
  kind: "up" | "dn" | "stop" | "now"
  price_text: string
  note?: string | null
  dist_pct?: number | null
}
export interface RulerBlock extends BlockBase {
  now: number
  rows: RulerRow[]
  merged_first?: boolean
}

export interface AnchorTier {
  tier: string
  label?: string | null
  lo?: number | null
  hi?: number | null
  mid?: number | null
  action?: string | null
  note?: string | null
  price_text?: string | null
}
export interface AnchorsBlock extends BlockBase {
  tiers: AnchorTier[]
  stop_loss?: { price: number; text: string; note?: string | null } | null
  current_action?: string | null
  cross_rows: { anchor: string; price: string; dist: string }[]
}

export interface RiskItem {
  no: number
  text: string
  hot: boolean
  chip?: string | null
}
export interface RisksBlock extends BlockBase {
  items: RiskItem[]
}

export interface ExitItem { text: string; src?: string | null }
export interface ExitsBlock extends BlockBase {
  clear_list: ExitItem[]
  watch_list: string[]
}

export interface SummaryBlock extends BlockBase {
  headline?: string | null
  core_logic?: string[]
  key_risks?: string[]
  action_md?: string | null
  one_liner?: string | null
}

export interface TraceBlock extends BlockBase {
  main?: { n: number; total: number } | null
  backup?: { n: number; total: number } | null
  una?: { n: number; total: number } | null
  una_detail?: string | null
  completeness?: string | null
  credibility?: string | null
  sources?: string | null
  disclaim: string
}

export interface CardRawBlock extends BlockBase { text?: string }
export interface AppendixBlock extends BlockBase {
  items: { section_id: string; title: string; raw_md: string }[]
}

// ---- 新手册 26 节解析块（parser 产出或 una 降级）----

export interface ChipsBlock extends BlockBase {
  rows?: { field: string; value: string; vs_price?: string; status?: string }[]
  verdict?: string | null
}
export interface PositionBlock extends BlockBase {
  rank?: string | null
  second_discount?: string | null
  moat_rows?: { dim: string; score: number | null; weight?: string; basis?: string }[]
  moat_total?: number | null
  moat_grade?: string | null
  summary?: string | null
}
export interface PeerBlock extends BlockBase {
  rows?: { dim: string; target: string; leader?: string; avg?: string; verdict?: string }[]
  conclusion?: string | null
}
export interface DeviationBlock extends BlockBase {
  applicable?: boolean
  rows?: { item: string; actual?: string; threshold?: string; dev?: string; grade?: string }[]
  conclusion?: string | null
}
export interface PqBlock extends BlockBase {
  rows?: { period: string; parent?: string; deducted?: string; gap?: string; verdict?: string }[]
  trend?: string | null
}
export interface HealthBlock extends BlockBase {
  rows?: { dim: string; score: number | null; basis?: string }[]
  total?: number | null
  grade?: string | null
  risk_rows?: { cls: string; hedge?: string; fact?: string; impact?: string }[]
}
export interface VetoBlock extends BlockBase {
  hard_rows?: { item: string; result: string; triggered: boolean | null }[]
  comp_rows?: { item: string; result: string; triggered: boolean | null }[]
  conclusion?: string | null
  triggered?: boolean | null
}
export interface GuideBlock extends BlockBase {
  stop_loss_new?: number | null
  zone1?: { lo: number; hi: number; dy?: number | null } | null
  zone2?: { lo: number; hi: number; dy?: number | null } | null
  clear_list?: string[]
}

// ---- 聚合 ----

export interface DashboardBlocks {
  meta?: MetaBlock
  quote?: QuoteBlock
  metrics?: MetricsBlock
  stamp?: StampBlock
  pipeline?: PipelineBlock
  signals?: SignalsBlock
  debate?: DebateBlock
  ruler?: RulerBlock
  anchors?: AnchorsBlock
  gates?: GatesBlock
  traps?: TrapsBlock
  roe?: RoeBlock
  capital?: CapitalBlock
  factor?: FactorBlock
  behavior?: BehaviorBlock
  stress?: StressBlock
  quant?: QuantBlock
  risks?: RisksBlock
  exits?: ExitsBlock
  trace?: TraceBlock
  summary?: SummaryBlock
  card_raw?: CardRawBlock
  appendix?: AppendixBlock
  // 新手册 26 节新增 block（parser 产出或 una 降级，前端按段消费）
  chips?: ChipsBlock
  position?: PositionBlock
  peer?: PeerBlock
  deviation?: DeviationBlock
  pq?: PqBlock
  health?: HealthBlock
  veto?: VetoBlock
  guide?: GuideBlock
  [k: string]: BlockBase | undefined
}

export interface DashboardData {
  schema: number
  generated_at: string
  report_path: string
  validate_issues: { id?: string; title?: string; issue?: string }[]
  parse_fallbacks: string[]
  section_count: number
  blocks: DashboardBlocks
}

export interface ReportSummary {
  code: string
  name: string
  analysis_date?: string | null
  state?: string | null
  state_en?: string | null
  confidence?: string | null
  position_cap_pct?: number | null
  price?: number | null
  change_pct?: number | null
  mtime: number
  has_markdown: boolean
}
