"""A股投资决策系统 — Pydantic schemas

对应《A股投资决策系统执行手册》第六章「最终输出格式（决策卡）」全部字段 + 小C 审查补齐的 4 个结构化区块。

结构（顶层 StockDecisionCard）：
  header + data_trace + hard_gate + reverse_check + five_signals + value_trap
  + quant_enhance + debate + factor_validation + capital_profile + stress_test
  + behavior_check + final_decision + anchors + exit_conditions + risks

设计原则：
- 所有可选字段默认 None / 空容器，允许 LLM 在数据 UNA 时省略，避免结构化输出解析失败。
- 字段 description 引手册语义，供 OpenAI Agents SDK 作为 output_type 指导 LLM 填充。
- 顶层字段命名清晰（前端按 key 对齐，见 example_response.json）。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ============================================================
# 请求
# ============================================================

class AnalyzeRequest(BaseModel):
    """POST /api/analyze 请求体。"""
    stock_code: str = Field(description="A股股票代码，如 600036 / 002415 / 000651")
    force_refresh: bool = Field(False, description="强制刷新：清除该股票的 TTL 缓存，重新取数")
    deep_think: bool | None = Field(
        None, description="深度思考（前端每单可切）：None=用全局 LLM_DEEP_THINK；true=effort high（更缜密但慢）；false=effort none（即时出字）")


# ============================================================
# L0 头部
# ============================================================

class StockHeader(BaseModel):
    """决策卡头部。"""
    stock_name: str = Field(description="标的名称")
    stock_code: str = Field(description="股票代码")
    analysis_date: str = Field(description="分析日期（YYYY-MM-DD）")


# ============================================================
# L1 数据溯源 / 数据底稿（决策卡「📊 数据溯源」区块 + 手册 3.9 底稿）
# ============================================================

class SourceSummary(BaseModel):
    """数据溯源摘要。"""
    main_source_success: Optional[int] = Field(None, description="主源成功字段数（X/X）")
    backup_source_downgrade: Optional[int] = Field(None, description="备源降级字段数（X/X）")
    una_count: Optional[int] = Field(None, description="UNA标记字段数（X/X）")
    completeness: Optional[Literal["完整", "基本完整", "部分缺失", "严重不足"]] = Field(
        None, description="数据完整性评级"
    )
    credibility: Optional[Literal["高", "中", "低", "不可用"]] = Field(
        None, description="数据可信度"
    )


class QuoteData(BaseModel):
    """行情数据（子阶段 1.1）。"""
    latest_price: Optional[float] = Field(None, description="最新价（元）")
    change_pct: Optional[float] = Field(None, description="涨跌幅（%）")
    pe_ttm: Optional[float] = Field(None, description="PE(TTM)（倍）")
    pb: Optional[float] = Field(None, description="PB（倍）")
    dividend_yield: Optional[float] = Field(None, description="股息率(TTM)（%）")


class DerivedMetrics(BaseModel):
    """衍生指标计算（子阶段 1.2）。"""
    pe_pct_5y: Optional[float] = Field(None, description="PE历史分位（近5年，%）")
    pe_pct_10y: Optional[float] = Field(None, description="PE历史分位（近10年，%）")
    pb_pct_5y: Optional[float] = Field(None, description="PB历史分位（近5年，%）")
    industry_pe_median_5y: Optional[float] = Field(None, description="行业PE中位数（近5年，倍）")
    fcf: Optional[float] = Field(None, description="自由现金流 FCF（亿元）")
    fcf_caliber: Optional[Literal["首选", "次选", "备选"]] = Field(
        None, description="FCF 计算口径（首选=年报附注资本性支出明细；次选=现金流量表科目差；备选=折旧摊销替代，置信度低）"
    )


class FcfYear(BaseModel):
    """FCF 校验（近 3 年）。"""
    year: Optional[str] = Field(None, description="年度")
    ocf: Optional[float] = Field(None, description="经营活动现金流（亿元）")
    capex: Optional[float] = Field(None, description="资本性支出（按优先级口径，亿元）")
    fcf: Optional[float] = Field(None, description="自由现金流 FCF（亿元）")


class IndustrySpecific(BaseModel):
    """行业专属字段（银行/保险）。"""
    npl_ratio: Optional[float] = Field(None, description="不良贷款率（%，阈值<1.8%）")
    coverage_ratio: Optional[float] = Field(None, description="拨备覆盖率（%，阈值>160%）")
    tier1_capital: Optional[float] = Field(None, description="核心一级资本充足率（%，阈值>8%）")
    nim: Optional[float] = Field(None, description="净息差 NIM（%）")
    nim_trend: Optional[Literal["企稳", "下行", "上行"]] = Field(None, description="NIM 趋势")


class EmployeeHolding(BaseModel):
    """员工持股计划。"""
    avg_price: Optional[float] = Field(None, description="员工持股均价（元，UNA 时标注）")
    premium_discount: Optional[float] = Field(None, description="当前价 vs 员工持股均价（溢价/折价，%）")


class PositionTracking(BaseModel):
    """用户持仓追踪区（用户提供时启用）。"""
    cost: Optional[float] = Field(None, description="持仓成本（元）")
    quantity: Optional[int] = Field(None, description="持仓数量（股）")
    first_entry_date: Optional[str] = Field(None, description="首次建仓日期")
    stop_loss_price: Optional[float] = Field(None, description="计划止损价（元）")


class DataTrace(BaseModel):
    """数据溯源 + 数据底稿（决策卡「📊 数据溯源」区块）。"""
    source_summary: SourceSummary = Field(default_factory=SourceSummary, description="数据溯源摘要")
    quote: QuoteData = Field(default_factory=QuoteData, description="行情数据")
    derived: DerivedMetrics = Field(default_factory=DerivedMetrics, description="衍生指标")
    fcf_history: list[FcfYear] = Field(default_factory=list, description="FCF 校验近3年")
    industry_specific: Optional[IndustrySpecific] = Field(None, description="行业专属字段（银行/保险）")
    employee_holding: Optional[EmployeeHolding] = Field(None, description="员工持股计划")
    position_tracking: Optional[PositionTracking] = Field(None, description="用户持仓追踪")
    # L0 原始数据（8 大类展示用，LLM 在数据 UNA 时填 None / 空，不编造）
    financials: list[Any] = Field(default_factory=list, description="3年财务对比 [{field, value_2025, value_2026_q1}]")
    business_breakdown: list[Any] = Field(default_factory=list, description="分业务收入占比 [{name, value}]")
    dividends: list[Any] = Field(default_factory=list, description="分红记录 [{event, date, amount}]")
    forecasts: list[Any] = Field(default_factory=list, description="机构预测与评级 [{institution, rating, target_price}]")
    northbound: Any = Field(None, description="北向资金明细 {holding, change_pct, value, pct_float, q1_holding, flows:[{name,value}]}")


# ============================================================
# L2 硬门槛（决策卡「✅ 硬门槛筛选」区块 + 手册 4.3）
# ============================================================

class HardGateKeyValues(BaseModel):
    """硬门槛关键数值。"""
    pe: Optional[float] = Field(None, description="PE(TTM)（倍）")
    pb: Optional[float] = Field(None, description="PB（倍）")
    dividend_yield: Optional[float] = Field(None, description="股息率（%）")
    roe_avg_3y: Optional[float] = Field(None, description="近三年平均 ROE（%）")
    roe_latest: Optional[float] = Field(None, description="最近一年 ROE（%）")
    ocf_to_np_avg_3y: Optional[float] = Field(None, description="近三年经营现金流/扣非净利润均值（%）")
    debt_ratio: Optional[float] = Field(None, description="资产负债率（%，非金融企业）")
    npl_ratio: Optional[float] = Field(None, description="不良率（%，银行）")
    coverage_ratio: Optional[float] = Field(None, description="拨备覆盖率（%，银行）")


class HardGateResult(BaseModel):
    """硬门槛筛选（决策卡「✅ 硬门槛筛选」区块）。"""
    passed: bool = Field(False, description="硬门槛是否通过")
    applied_model: Optional[Literal["模型一", "模型二", "模型三", "模型四", "无"]] = Field(
        None, description="适用模型（一=价值股/二=成长股/三=深度价值/四=银行保险）"
    )
    key_values: HardGateKeyValues = Field(default_factory=HardGateKeyValues, description="关键数值")
    quality_regulator_triggered: bool = Field(
        False, description="质量调节器是否触发（PE 门槛放宽至 12 倍，仓位上限 45%→30%，置信度高→中）"
    )
    alt_pool_triggered: bool = Field(
        False, description="模型一备选池是否触发（标的<3只时 PE 放宽至 12 倍，置信度降至中，状态 Watch）"
    )
    reason: str = Field("", description="判定理由（含未通过的具体硬条件）")


# ============================================================
# L2 反向清单（决策卡「✅ 反向清单筛查」区块 + 手册 4.4）
# ============================================================

class ReverseCheckItem(BaseModel):
    """反向清单单项。"""
    name: str = Field(description="检查项名称")
    triggered: bool = Field(False, description="是否触发")
    detail: Optional[str] = Field(None, description="细节（数值/事件）")


class ReverseCheckResult(BaseModel):
    """反向清单筛查（决策卡「✅ 反向清单筛查」区块）。每触发一项降一级置信度。"""
    passed: bool = Field(False, description="通过/存疑（置信度降级）")
    items: list[ReverseCheckItem] = Field(
        default_factory=list,
        description="反向清单各项：大股东质押率>50% / ESG负面事件 / 审计意见非标准 / 近两年累计净利润为负(非周期) / 分红率>100%",
    )
    confidence_downgrades: int = Field(0, description="置信度降级档数（高→中→低→极低，极低再触发强制 Exit）")


# ============================================================
# L2 五维信号（决策卡「📊 五维信号」区块 + 手册 4.5）
# ============================================================

class FiveDimSignals(BaseModel):
    """五维信号 T/F/V/Q/S。"""
    trend: Optional[Literal["UP", "FLAT", "DOWN"]] = Field(
        None, description="趋势 Trend（日线50%+周线50%综合）"
    )
    flow: Optional[Literal["POS", "NEU", "NEG"]] = Field(
        None, description="资金 Flow（主力40%+机构30%+北向30%；非十大活跃股北向降权至10%并入机构）"
    )
    value: Optional[Literal["LOW", "FAIR", "HIGH"]] = Field(
        None, description="估值 Value（基于 PE/PB 历史分位 + 股息率）"
    )
    quality: Optional[Literal["OK", "CAU", "POOR"]] = Field(
        None, description="质量 Quality（利润增速+营收增速+毛利率+FCF）"
    )
    sentiment_score: Optional[float] = Field(
        None, description="情绪 Sentiment 分数（提示性辅助，数据不可得时 0 分，不参与加权）"
    )
    details: Optional[str] = Field(None, description="各维判定依据")


# ============================================================
# L2 价值陷阱（决策卡「⚠️ 价值陷阱识别」区块 + 手册 4.6 严格执行版）
# ============================================================

class ValueTrapSignal(BaseModel):
    """价值陷阱信号。"""
    name: str = Field(description="信号名称")
    triggered: bool = Field(False, description="是否触发")
    position_cap: Optional[float] = Field(None, description="触发后仓位上限（%）")


class ValueTrapResult(BaseModel):
    """价值陷阱识别（严格执行版）。触发任一信号→Watch，两个及以上→Exit。"""
    triggered: bool = Field(False, description="是否触发价值陷阱")
    signals: list[ValueTrapSignal] = Field(
        default_factory=list,
        description="价值陷阱信号：ROE连续三年下降(5%) / 扣非净利润连续两年下降(5%) / 经营现金流连续两年下降(5%) / 毛利率连续三年下降(10%) / 应收账款增速>营收增速连续2年(10%) / 经营现金流/扣非净利润<50%(10%)",
    )
    position_cap: Optional[float] = Field(None, description="触发后仓位上限（%）")


# ============================================================
# L2 量化增强（决策卡「📈 量化增强」区块）
# ============================================================

class QuantEnhanceResult(BaseModel):
    """量化增强（仅供参考）。DCF 估值。"""
    dcf_executed: bool = Field(False, description="DCF 估值执行/跳过")
    dcf_value: Optional[float] = Field(None, description="DCF 估值结果（元）")
    note: str = Field("", description="备注")


# ============================================================
# 五维视角辩论（手册 §4.11/§4.12）——小C 补齐区块
# ============================================================

class DebateFaction(BaseModel):
    """五维辩论单派辩手。"""
    name: Literal["价值派", "成长派", "质量派", "资金派", "情绪派"] = Field(description="辩手派别")
    score: Optional[float] = Field(None, description="评分 -10~+10（映射 0-100）")
    weight: Optional[float] = Field(
        None, description="权重%（默认 价值28/成长25/质量22/资金15/情绪10；防御 价值35/成长10/质量30/资金15/情绪10）"
    )
    stance: Optional[Literal["多", "空", "中"]] = Field(None, description="立场")


class DebateResult(BaseModel):
    """五维视角辩论（手册 §4.11/§4.12）。价值→成长→质量→资金→情绪→裁定。"""
    factions: list[DebateFaction] = Field(default_factory=list, description="五派辩手评分")
    composite_score: Optional[float] = Field(None, description="综合得分（0-100，按权重加权）")
    consensus: Optional[Literal["共识", "分歧"]] = Field(None, description="共识/分歧标记")
    winner: Optional[Literal["多头", "空头", "势均"]] = Field(None, description="胜方")
    defense_mode: bool = Field(False, description="是否防御状态（权重切换：价值/质量权重提升）")
    summary: str = Field("", description="辩论摘要（五派观点与裁定结论的文字概述）")


# ============================================================
# 因子有效性验证 ——小C 补齐区块
# ============================================================

class FactorValidation(BaseModel):
    """因子有效性验证：验证五维信号因子的数据充分性/可靠性，UNA 因子标记失效。"""
    trend_valid: Optional[bool] = Field(None, description="趋势因子有效（日线+周线数据充分）")
    flow_valid: Optional[bool] = Field(None, description="资金因子有效（主力/机构/北向可得）")
    value_valid: Optional[bool] = Field(None, description="估值因子有效（PE/PB 分位可得）")
    quality_valid: Optional[bool] = Field(None, description="质量因子有效（利润/营收/毛利率/FCF 可得）")
    sentiment_valid: Optional[bool] = Field(None, description="情绪因子有效（数据可得，否则失效不参与加权）")
    invalid_factors: list[str] = Field(default_factory=list, description="失效因子列表")
    note: str = Field("", description="因子有效性总结")


# ============================================================
# 资金行为画像 ——小C 补齐区块
# ============================================================

class CapitalProfile(BaseModel):
    """资金行为画像（主力/机构/北向/融资 合力）。"""
    main_force: Optional[Literal["净流入", "净流出", "平衡"]] = Field(None, description="主力资金方向")
    institution: Optional[Literal["净流入", "净流出", "平衡"]] = Field(None, description="机构资金方向")
    northbound: Optional[Literal["净流入", "净流出", "UNA"]] = Field(
        None, description="北向资金方向（非十大活跃股 UNA，按周频降权）"
    )
    margin: Optional[str] = Field(None, description="融资融券概况")
    summary: str = Field("", description="资金合力画像（加权综合）")


# ============================================================
# 悲观情景压力测试（手册认知底座双重估值 + §4.12 防御）——小C 补齐区块
# ============================================================

class StressTestResult(BaseModel):
    """悲观情景压力测试。双重估值原则：基准情景之外附悲观情景估值区间（DCF + 压力情景 + VaR）。"""
    base_valuation: Optional[float] = Field(None, description="基准情景估值（当前盈利×行业平均PE）")
    pessimistic_valuation: Optional[float] = Field(None, description="悲观情景估值")
    dcf_valuation_range: Optional[str] = Field(
        None, description="DCF 估值区间（如 '28-35元'，悲观下限-基准上限）"
    )
    scenario: str = Field("", description="压力情景描述（如 '悲观情景：营收降10%+不良率升+流动性收紧'）")
    downside_risk: Optional[float] = Field(None, description="下行风险（%，相对基准）")
    var_5d: Optional[float] = Field(None, description="5日 VaR（%）")
    extreme_volatility: bool = Field(
        False, description="是否极端波动（全市场波动率历史90%分位以上，须声明低估尾部风险）"
    )
    note: str = Field("", description="压力测试结论")


# ============================================================
# L3 行为自检（决策卡「🧠 行为自检」区块 + 手册 5.1 提示性警告版）
# ============================================================

class BehaviorCheckResult(BaseModel):
    """行为金融学自检（提示性警告，不再强制降级）。"""
    has_warning: bool = Field(False, description="有提示/无提示")
    triggered_items: list[str] = Field(
        default_factory=list,
        description="触发的自检问题：1.FOMO/恐慌(近1月涨幅>20%且PE>历史70%分位) 2.机构目标价幻觉 3.行业周期性风险 4.社交媒体/情绪影响",
    )


# ============================================================
# L3 状态机 / 最终决策（决策卡「🎯 状态机输出 + 止盈检查」区块 + 手册 5.6/5.7）
# ============================================================

class FinalDecision(BaseModel):
    """状态机输出 + 止盈检查 + 置信度。状态优先级：Exit > Watch > StrongLong(降级) > ValueReversion > StrongLong(完整) > Range。"""
    state: Optional[Literal[
        "StrongLong完整", "StrongLong降级", "ValueReversion", "Range", "Watch", "Exit"
    ]] = Field(None, description="状态机状态")
    action: str = Field("", description="操作建议（持仓/加仓/分批建仓/观望/持有观察/清仓回避）")
    position_cap: float = Field(0, description="仓位上限（%）")
    confidence: Optional[Literal["高", "中", "低", "极低"]] = Field(None, description="置信度")
    stop_profit_check: Optional[str] = Field(
        None, description="止盈检查结果（止盈触发优先于状态机维持）"
    )


# ============================================================
# L3 锚点（决策卡「🔗 锚点操作建议」区块）
# ============================================================

class Anchor(BaseModel):
    """建仓锚点。"""
    level: str = Field(description="档位（第一档/第二档/第三档）")
    price: Optional[float] = Field(None, description="价位（元）")
    position: Optional[float] = Field(None, description="仓位（%）")


class AnchorPlan(BaseModel):
    """锚点操作建议：三档建仓锚点 + 止损线 + 悲观情景修正锚点。"""
    entry_anchors: list[Anchor] = Field(default_factory=list, description="三档建仓锚点")
    stop_loss_line: Optional[float] = Field(None, description="止损线（元）")
    pessimistic_correction_anchor: Optional[float] = Field(None, description="悲观情景修正锚点（元）")


# ============================================================
# 顶层决策卡
# ============================================================

class StockDecisionCard(BaseModel):
    """A股投资决策卡 — 最终输出。对应手册第六章决策卡全部字段 + 小C 补齐的 4 区块。"""
    header: StockHeader
    data_trace: DataTrace = Field(default_factory=DataTrace, description="📊 数据溯源")
    hard_gate: HardGateResult = Field(default_factory=HardGateResult, description="✅ 硬门槛筛选")
    reverse_check: ReverseCheckResult = Field(default_factory=ReverseCheckResult, description="✅ 反向清单筛查")
    five_signals: FiveDimSignals = Field(default_factory=FiveDimSignals, description="📊 五维信号")
    value_trap: ValueTrapResult = Field(default_factory=ValueTrapResult, description="⚠️ 价值陷阱识别")
    quant_enhance: QuantEnhanceResult = Field(default_factory=QuantEnhanceResult, description="📈 量化增强")
    debate: "DebateResult" = Field(default_factory=lambda: DebateResult(), description="🗣️ 五维视角辩论")
    factor_validation: "FactorValidation" = Field(default_factory=lambda: FactorValidation(), description="🔬 因子有效性验证")
    capital_profile: "CapitalProfile" = Field(default_factory=lambda: CapitalProfile(), description="💰 资金行为画像")
    stress_test: "StressTestResult" = Field(default_factory=lambda: StressTestResult(), description="📉 悲观情景压力测试")
    behavior_check: BehaviorCheckResult = Field(default_factory=BehaviorCheckResult, description="🧠 行为自检")
    final_decision: FinalDecision = Field(default_factory=FinalDecision, description="🎯 状态机输出+止盈检查")
    anchors: AnchorPlan = Field(default_factory=AnchorPlan, description="🔗 锚点操作建议")
    exit_conditions: list[str] = Field(default_factory=list, description="📋 持有期间清仓条件清单")
    risks: list[str] = Field(default_factory=list, description="⚠️ 风险提示")


# ============================================================
# L2/L3 分析主体（orchestrator 将其 + header + DataTrace 组装为 StockDecisionCard）
# ============================================================

class AnalysisBody(BaseModel):
    """L2/L3 分析主体 = 决策卡除 header 与 data_trace 外的全部区块。"""
    hard_gate: HardGateResult = Field(default_factory=HardGateResult, description="✅ 硬门槛筛选")
    reverse_check: ReverseCheckResult = Field(default_factory=ReverseCheckResult, description="✅ 反向清单筛查")
    five_signals: FiveDimSignals = Field(default_factory=FiveDimSignals, description="📊 五维信号")
    value_trap: ValueTrapResult = Field(default_factory=ValueTrapResult, description="⚠️ 价值陷阱识别")
    quant_enhance: QuantEnhanceResult = Field(default_factory=QuantEnhanceResult, description="📈 量化增强")
    debate: "DebateResult" = Field(default_factory=lambda: DebateResult(), description="🗣️ 五维视角辩论")
    factor_validation: "FactorValidation" = Field(default_factory=lambda: FactorValidation(), description="🔬 因子有效性验证")
    capital_profile: "CapitalProfile" = Field(default_factory=lambda: CapitalProfile(), description="💰 资金行为画像")
    stress_test: "StressTestResult" = Field(default_factory=lambda: StressTestResult(), description="📉 悲观情景压力测试")
    behavior_check: BehaviorCheckResult = Field(default_factory=BehaviorCheckResult, description="🧠 行为自检")
    final_decision: FinalDecision = Field(default_factory=FinalDecision, description="🎯 状态机输出+止盈检查")
    anchors: AnchorPlan = Field(default_factory=AnchorPlan, description="🔗 锚点操作建议")
    exit_conditions: list[str] = Field(default_factory=list, description="📋 持有期间清仓条件清单")
    risks: list[str] = Field(default_factory=list, description="⚠️ 风险提示")


# 前向引用解析（StockDecisionCard/AnalysisBody 用字符串注解引用下方定义的类）
StockDecisionCard.model_rebuild()
AnalysisBody.model_rebuild()
