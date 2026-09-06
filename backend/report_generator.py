"""分析报告生成模块：raw 数据 + 决策卡 → 三层 Markdown 分析报告。

流程（验证—修复闭环，防死循环）：
  1. report_writer LLM 按参考模板写报告初稿（markdown 是 LLM 自然输出形态，比巨型 JSON 可靠）；
  2. validate_report 对照 26 节必备清单（L0×8 + L2×16 + L3×2）校验：标题存在 + 内容下限；
  3. 不通过 → 针对性修复调用（最多 MAX_REPAIRS=2 轮；修复无改善则提前退出）；
  4. 仍缺失的节由代码层从 raw/card 确定性渲染骨架补入 → 章节永不为空，循环必然终止。

决策卡 JSON 管线（前端契约）不受本模块影响；本模块只产出 analysis_reports/*.md。

CLI：uv run python report_generator.py <报告.md>   # 仅校验报告结构，退出码 0=通过
"""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass
from datetime import date

from agents import Agent, Runner

from config import LLM_MODEL

# ============================================================
# 必备章节清单
# ============================================================

@dataclass(frozen=True)
class SectionSpec:
    id: str
    layer: str          # L0 / L2 / L3（用于兜底骨架插入位置）
    title: str          # 规范标题（兜底骨架使用）
    keywords: tuple     # 标题模糊匹配关键词（任一命中即算存在）


REQUIRED_SECTIONS: list[SectionSpec] = [
    # ── 【数据底稿】L0 ──
    SectionSpec("l0_constraint", "L0", "〇、200字硬约束校验", ("200字硬约束", "硬约束校验")),
    SectionSpec("l0_quote", "L0", "一、行情数据", ("行情数据",)),
    SectionSpec("l0_financial", "L0", "二、财务数据", ("财务数据",)),
    SectionSpec("l0_dividend", "L0", "三、分红数据", ("分红",)),
    SectionSpec("l0_reverse", "L0", "四、反向清单数据", ("反向清单数据",)),
    SectionSpec("l0_flow", "L0", "五、资金流向数据", ("资金流向数据", "资金流向")),
    SectionSpec("l0_forecast", "L0", "六、机构预测与评级", ("机构预测", "机构评级")),
    SectionSpec("l0_chips", "L0", "七、筹码与内部人成本", ("筹码", "内部人成本")),
    # ── 【L2：分析计算层】──
    SectionSpec("l2_position", "L2", "一、行业地位与护城河评估", ("行业地位", "护城河")),
    SectionSpec("l2_hardgate", "L2", "二、硬门槛筛选与校验清单", ("硬门槛筛选", "硬门槛")),
    SectionSpec("l2_reverse", "L2", "三、反向清单筛查", ("反向清单筛查",)),
    SectionSpec("l2_peer", "L2", "四、竞品横向对比", ("竞品",)),
    SectionSpec("l2_five", "L2", "五、五维信号判定", ("五维信号",)),
    SectionSpec("l2_capital", "L2", "六、资金行为画像", ("资金行为画像",)),
    SectionSpec("l2_trap", "L2", "七、价值陷阱识别与利润质量", ("价值陷阱",)),
    SectionSpec("l2_deviation", "L2", "八、偏离度审查与裁量协议层", ("偏离度", "裁量协议")),
    SectionSpec("l2_factor", "L2", "九、因子有效性验证", ("因子有效性", "因子验证")),
    SectionSpec("l2_quant", "L2", "十、量化增强（四法联动）", ("量化增强",)),
    SectionSpec("l2_debate", "L2", "十一、五维视角辩论引擎", ("辩论引擎", "辩论")),
    SectionSpec("l2_stress", "L2", "十二、悲观情景压力测试", ("压力测试",)),
    SectionSpec("l2_behavior", "L2", "十三、行为金融学自检", ("行为金融学自检", "行为自检", "行为金融")),
    SectionSpec("l2_health", "L2", "十四、财务健康度与风险分类", ("财务健康", "风险分类")),
    SectionSpec("l2_veto", "L2", "十五、一票否决Exit检查", ("一票否决",)),
    SectionSpec("l2_state", "L2", "十六、状态机映射 + 止盈检查", ("状态机映射", "状态机")),
    # ── 【L3：输出展示层】──
    SectionSpec("l3_card", "L3", "决策卡", ("输出展示层", "决策卡")),
    SectionSpec("l3_summary", "L3", "执行摘要", ("执行摘要",)),
]

_BY_ID = {s.id: s for s in REQUIRED_SECTIONS}

# ============================================================
# 结构校验
# ============================================================

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")

MIN_TEXT_CHARS = 40   # 节正文有效字符下限
MIN_TABLE_ROWS = 2    # 或表格数据行下限


def _iter_headings(lines: list[str]):
    """生成 (行号, 级别, 标题文本)。"""
    for i, ln in enumerate(lines):
        m = _HEADING_RE.match(ln)
        if m:
            yield i, len(m.group(1)), m.group(2).strip()


def validate_report(md: str) -> list[dict]:
    """对照必备章节清单校验报告。

    返回问题列表：[{"id", "layer", "title", "issue": "missing"|"thin", ...}]
    空列表 = 通过。
    """
    lines = md.splitlines()
    headings = list(_iter_headings(lines))
    issues: list[dict] = []

    for spec in REQUIRED_SECTIONS:
        found = None
        for pos, (li, lvl, title) in enumerate(headings):
            if any(k in title for k in spec.keywords):
                # 正文范围：到下一个同级或更高级标题为止（子标题内容计入正文）
                end = len(lines)
                for li2, lvl2, _ in headings[pos + 1:]:
                    if lvl2 <= lvl:
                        end = li2
                        break
                found = (title, "\n".join(lines[li + 1:end]))
                break
        if found is None:
            issues.append({"id": spec.id, "layer": spec.layer,
                           "title": spec.title, "issue": "missing"})
            continue
        title, body = found
        text_chars = len(re.sub(r"\s", "", body.replace("|", "")))
        table_rows = len([ln for ln in body.splitlines()
                          if ln.strip().startswith("|") and not _TABLE_SEP_RE.match(ln)])
        if text_chars < MIN_TEXT_CHARS and table_rows < MIN_TABLE_ROWS:
            # 结论型小节：结论（通过/存疑/触发）直接写在标题里视为达标
            # （参考范例「## ✅ 二、反向清单筛查：通过 ✅」即此形态，正文为空）
            if re.search(r"通过|不通过|存疑|触发", title):
                continue
            issues.append({"id": spec.id, "layer": spec.layer,
                           "title": spec.title, "issue": "thin",
                           "found_title": title,
                           "chars": text_chars, "rows": table_rows})
    return issues


# ============================================================
# report_writer LLM：模板规范 + 参考范例
# ============================================================

_REF_EXAMPLE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "标的分析结果参考" / "个股分析-招商银行.md"
)

_WRITER_SPEC = """你是 A 股投研报告撰写员。任务：基于真实数据底稿与已完成的投研决策卡，按《执行手册》判定规则撰写一份完整的 Markdown 分析报告。

【报告结构 —— 严格按此章节顺序产出（数据底稿 8 节 + L2 十六节 + L3 两节，共 26 节），每一节都必须存在且有实质内容；数据缺失的字段写 "—" 或 "UNA"，但不得省略任何一节。关键字段可附加手册 §3.2 状态标识（【全量】【更新中】【部分】【估算】【无法获取】），【全量】vs【更新中】跨期对比须标 "⚠️对比基数存在偏差"】

# 【数据底稿】标的：__NAME__ 代码：__CODE__ 取数时间：__DATE__

## 〇、200字硬约束校验
表格（校验项|内容|结果）：① 收入来源拆解（≥3项+占比）② 主要客户类型（B/C端+前五大客户集中度，>50%标"⚠️存疑"）③ 成本结构（前三大）④ 利润驱动因素（区分内生性增长/外生性贡献，外生占比过高标"⚠️利润质量存疑"）⑤ 客户集中度敏感度。
表后给出结论：✅ 通过，进入分析流程 / 理解不足，暂缓分析。

## 一、行情数据
表格（字段|数值|来源|状态）：最新价/涨跌幅/今开/最高/最低/振幅/成交量/成交额/换手率/PE(TTM或动)/PB/PE历史分位(近5年)/PB历史分位/股息率(TTM)/总市值/流通市值/每股收益/每股净资产/52周最高/52周最低。
52周高低点双源采集；表后附「【52周数据校验：✅前三项全部通过 / ⚠️仅第四项不通过 / ⚠️数据异常】」+ 52周涨幅规模=(最高-最低)÷最低 及分级标注（100%/200%/400%三档）。
表后 "> **今日关键信号：**" 一段点评。

## 二、财务数据
表格（字段|数值|来源|状态）：近5年 营业总收入(同比)/归母净利润/扣非净利润/ROE(加权)/毛利率/净利率/资产负债率/经营现金流/商誉÷净资产/应收账款÷营收（有则列，缺标"—"）；最新季报 营收(同比)/净利润(同比)；最近4个季度单季营收/扣非/毛利率（如可得）；流动比率/速动比率/存货周转天数（如可得）。
表后 "> **关键信号：**" 点评。如有分业务数据，单列「分业务数据」小节表格（板块|收入|占比|毛利率|同比增速）。

## 三、分红数据
表格（字段|数值|来源|状态）：最近年度每股分红/分红总额/分红率/股权登记日/除权除息日/连续分红年数/累计分红（如可得）。表后点评。

## 四、反向清单数据
表格（字段|数值|来源|状态）：大股东质押率/ESG负面事件/审计意见/近两年累计净利润/分红率/前五大客户集中度/流动比率/速动比率/存货周转天数/商誉÷净资产。表后结论。

## 五、资金流向数据
表格（字段|数值|来源|状态）：主力资金动向及近5/10/20日净流向/北向持股及本季变动（无则 UNA）/融资余额及近5日变化/机构持仓变化。表后 "> **关键信号：**" 点评。

## 六、机构预测与评级
表格（字段|数值|来源|状态）：机构覆盖家数/2026年EPS预测均值/净利润预测均值/目标价最高/目标价最低/评级分布（买/增/中/减，如可得）/代表机构评级（≥2家，可据训练数据知识补充）。表后点评（含隐含上行空间测算）。

## 七、筹码与内部人成本
表格（字段|数值|较现价|状态）：股东户数(最新vs上期，变化%)/户均持股市值/员工持股成本/股权激励行权价/回购均价/大股东增减持均价/近期大宗交易价及折溢价/股权质押比例。可得则列，缺标 UNA（本节不因缺项省略）。
表后附「【筹码判定：筹码集中/趋向集中/稳定/趋向分散/高度分散】」与内部人成本时效性标注（有效≤3年/参考3-5年/不适用>5年）。

# 【L2：分析计算层】

## ✅ 一、行业地位与护城河评估
### 行业地位判定
表格（步骤|判定|依据）：申万二级行业归属/主要竞对（2-3家）/营收利润排名/CR2/老二折价适用与否；若适用老二折价，写明 PE 门槛放宽至10倍、状态上限 ValueReversion、仓位8折、置信度降至中。
### 护城河结构化评估
表格（维度|权重|得分(1-5)|依据）：技术壁垒25%/全球化能力25%/用户生态25%/品牌矩阵25%；末行「综合得分：__分（≥18极强 / 14-17较强 / 10-13一般 / <10薄弱）→ 仓位影响」。数据不足时按最低档处理并注明。

## ✅ 二、硬门槛筛选与校验清单
模型一~四适用条件与检验结果表 + 【硬门槛校验清单】逐模型条件核验（带实际数值与✅/❌）+ 质量调节器四条件核验（ROE≥15%/股息率≥4%/现金流÷扣非≥80%/负债率≤60%；若PE落入12-13边界区间，给出边界操作指引）+ 成长股额外验证清单（若适用模型二：行业周期位置/客户集中度/利润质量/营收增速边际四项）+ 强周期PEG陷阱检查（如适用）+ 结论（通过/不通过+触发模型+理由说明）。

## ✅ 三、反向清单筛查：通过/存疑 + 置信度降级档数
表格（检查项|阈值|实际值|结果）十项：大股东质押率/ESG负面事件/审计意见/近两年累计净利润/分红率/客户集中度(>50%存疑,>70%高风险)/流动比率(<1.2)/速动比率(<0.8)/存货周转天数(>行业均值×1.2)/商誉÷净资产(>25%)。

## ✅ 四、竞品横向对比
表格（对比维度|标的|行业龙头|行业均值|结论）五行：市盈率/净利率/ROE/营收增速/海外收入占比 + 结论（全维度占优→置信度+1 / 多数占优→中性 / ≥3项低于均值→置信度-1并计入风险提示）。

## ✅ 五、五维信号判定
表格（维度|信号|计分(+1/0/-1)|依据）：趋势(Trend: UP/FLAT/DOWN)/资金(Flow: POSITIVE/NEUTRAL/NEGATIVE)/估值(Value: LOW/FAIR/HIGH)/质量(Quality: OK/CAUTION/POOR)/情绪(Sentiment: 分数+FEAR/GREED)。依据列须引用具体数据；估值维度须写明 PE/PB/股息率历史分位与边界判定依据（PE处于19-21倍或PB>3倍时按手册细化表处理）；质量维度须写明七子维度加权综合得分（ROE20%/ROE趋势15%/营收增速15%/OCF÷扣非15%/毛利率趋势15%/研发强度10%/费用管控10%，≥2.4优秀/1.6-2.4一般/<1.6差）。
表后「五维正向数量：__项」。

## ✅ 六、资金行为画像
表格（资金类型|存量评分|边际评分|综合评分|依据）三行：主力/机构/北向（单项综合=存量×40%+边际×60%）+ 加权总分（主力40%+机构30%+北向30%）+ 合力判定（≥70合力做多/60-70偏多分歧/50-60偏空分歧/≤40合力做空）+ 存量vs边际冲突处理说明（如连续流出/流入≥5日按手册降权）+ 增量信号（如有极端分歧+净流入）+ 主导资金 + 筹码结构判断。

## ✅ 七、价值陷阱识别与利润质量
表格（信号|触发情况|判断）：ROE连续三年下降/扣非净利润连续两年下降/经营现金流连续两年下降/毛利率连续三年下降/应收增速>营收增速/经营现金流÷扣非<50%。
### P-33 利润质量校验
表格（期间|归母净利润|扣非净利润|差值%|判定）三行：最近季报/上季/同比季（判定=优质<10%/存疑10-30%/预警>30%）+ 趋势判定（持续恶化/边际恶化/持续优质）及对质量评分的加减分。
### 恶化程度分级与类型判定
若触发：写明分级（轻度<3pct/中度3-6/重度>6）+ 修正因子 + 「类型：确认价值陷阱 / 潜在价值陷阱（拐点已现）/ 无」+ 最终仓位 = min(状态机仓位, 强制5%或10%, 分级放宽上限, 修正后上限)；价值陷阱结论（是否强制降级）。

## ✅ 八、偏离度审查与裁量协议层
若硬门槛不通过：表格（未通过项|实际值|阈值|偏离度|分级）（偏离度<25%协议层适用/25-50%仓位7折/>50%阻断）+ 协议层判定结论行「协议层：适用/不适用/阻断；五维正向 __ 项 → StrongLong（降级）60%/40% 或 Watch 10%」。
若硬门槛通过：写"硬门槛通过，偏离度审查与裁量协议层不适用"，本节不可省略。

## ✅ 九、因子有效性验证
验证状态说明 + 表格（因子|本标的|行业参考|初步判断）+ 维度权重说明（样本不足则维持默认权重：价值28%/成长25%/质量22%/资金15%/情绪10%）。

## ✅ 十、量化增强（四法联动）
### 估值方法选择
标的类型判定（高股息≥3%/成长增速≥15%/低PE低PB/银行保险/资产型/现金流稳定）+ 主法与辅法选择及理由；类债券资产识别成立时注明（经营现金流÷净利>120%连续3年+股息率≥3%+分红率≥60%+营收波动<15%）。
### 主估值法计算
按所选方法展开：股息率法（可持续分红÷保守/基准/乐观股息率→三档目标价）/ PE参照法（预测净利润×合理PE→合理市值→目标价）/ DCF（FCF→永续增长→WACC折现→每股内在价值）/ PB-ROE（合理PB=ROE×合理比率→目标价）。
### 5日VaR（简化版）
当前价格/20日历史波动率年化（不得用当日振幅）/Z=1.645/5日VaR 数值 + 风险标记。
末行「综合结论：合理价值区间 __-__ 元，现价所处位置」。

## ✅ 十一、五维视角辩论引擎
框线格式（┌├│└），五派（价值28%/成长25%/质量22%/资金15%/情绪10%；市场防御状态触发时切换为 价值35/成长10/质量30/资金15/情绪10 并给出防御触发依据）各含：结论/论据(≥3条带数据)/**回应其他派质疑**一段；
末尾综合裁定：最大共识点/最大分歧点/加权综合得分(计算式)/裁定理由。
**每派必须打分且格式固定**：综合裁定行首按序写出五派 -10~+10 原分与 0-100 映射分，形如「加权综合得分（-10~+10映射0-100）：价值派+2→60、成长派-1→45、质量派+1→55、资金派-3→35、情绪派0→50 → 60×28%+45×25%+55×22%+35×15%+50×10%=50.4/100」，映射分与综合加权需自洽（数据缺失派写明"无评分数据，默认0"）。

## ✅ 十二、悲观情景压力测试（标准流程）
### 第一步：身份识别（估值溢价标签/实际利润来源/身份错位程度 表格；错位时注明额外下调10-20%）
### 第二步：悲观假设（风险路径|发生概率|影响幅度 表格；客户集中度>50%时必含「单一客户流失」标准路径 + 综合下调幅度 + 多条路径同时触发概率）
### 第三步：估值重算（估值方式|基准情景|悲观情景 表格 + 悲观合理估值区间 + 现价所处位置；FCF用备选口径时额外下调10-20%并注明）
### 第四步：时间轴假设（情景类型|假设条件|估值锚点|触发条件|预计持续时间 表格，悲观A(短期)/B(中期)/C(结构性) 三情景）
### 第五步：机构最低目标价交叉验证（锚点类型|价格|较现价溢价/折价 表格 + 交叉验证结论）
### 第六步：状态机裁定（基准/悲观A/悲观B/悲观C 各自状态 + 最终裁定）

## ✅ 十三、行为金融学自检（提示性警告版）
表格（问题|量化触发阈值|实际值|结果）：1.追涨/恐慌 2.机构目标价幻觉 3.行业周期性风险 4.社交媒体/情绪影响 5.机构最低目标价较现价折价>25%（基准情景估值=当年扣非预测×行业近3年PE中位数÷总股本）+ 自检结论。

## ✅ 十四、财务健康度与风险分类
### 财务健康度综合评分
表格（维度|权重|得分(3/1/0)|依据）四行：资产质量25%/负债结构25%/现金流量25%/盈利能力25% + 结论行「综合得分：__｜评级：≥9优秀/6-8良好/3-5一般/<3警惕」+ 与质量维度显著差异时取保守值的说明。
### 风险分类框架
三行（分类|可对冲性|事实|影响程度）：行业竞争（可对冲）/政策·贸易（不可对冲）/地缘·宏观（不可对冲）。

## ✅ 十五、一票否决Exit检查
### 硬Exit逐项核验
表格（序号|条件|结果）七行：①扣非连续两季负增长 ②ROE降至阈值下(制造<8%/银行<10%) ③不良率连续两季上升(金融) ④经营现金流连续两季为负 ⑤股息率持续低于门槛(高股息标的) ⑥审计意见非标准 ⑦近两年累计净利润为负。
### 综合判断权重
表格（指标|权重档|是否触发）六行：机构数量减少>70%(强)/扣非增速<0%单季(中)/PE>20倍(中)/股息率<1.5%(中)/大股东减持>10%(中)/五维负向≥3项(强)。
结论行「一票否决：未触发 / 触发（强__项+中__项 → Exit，凌驾全部仲裁）」。

## ✅ 十六、状态机映射 + 止盈检查
状态机映射表（状态|触发条件|结果）：StrongLong（完整）/StrongLong（降级）/ValueReversion/Range/Watch/Exit 逐条核验 + 边际改善特殊处理检查 + 规则冲突仲裁说明（数据状态>双重验证>增量信号>硬门槛>价值陷阱>偏离度>协议层，一票否决凌驾）+ 【止盈检查】逐项（PE≥12且股息率≤3 / PB>1.2+豁免因子(ROE≥15%+0.3/股息率≥5%+0.1/现金流÷净利≥100%+0.1) / 涨幅50%/100%减持档）+ 置信度结论（高/中/低/极低+降级触发项计数）+ 状态机输出结论（状态/操作/仓位上限/置信度/理由）。
### 持仓管理指引
【技术性止损线（新仓适用）】__元（仅限建仓<3个月）；【价值观察区（老仓适用）】第一观察区 __-__ 元（对应股息率__%+）/第二观察区 __-__ 元（极端低估）；【基本面清仓条件】编号清单（触发任一即清仓，老仓不因价格本身清仓）。

# 【L3：输出展示层——决策卡】
框线决策卡（用 ┌├│└ 边框），含以下全部框格：📊 数据溯源（主源/备源/UNA计数+完整性+可信度+时效性）/ 🏰 护城河评估（四维得分+等级+仓位影响）/ ❌✅ 硬门槛筛选（适用模型+关键数值+偏离度+质量调节器）/ ⚠️ 反向清单筛查（十项+客户集中度+流动/速动比率）/ 📊 五维信号（各维计分+五维正向数量）/ ⚠️ 价值陷阱识别（触发信号+仓位上限+恶化分级+类型）/ ⚠️ 利润质量校验（最新季报差值%+分级+趋势）/ 📈 量化增强（主辅法+合理价值区间+5日VaR）/ 📊 竞品横向对比（五行摘要）/ 📊 财务健康度（四维得分+评级）/ 📊 筹码集中度（户数变化+判定）/ 📊 内部人成本参考（可得锚点+时效性）/ 🚫 一票否决Exit检查（硬Exit触发情况+综合权重结论）/ 🧠 行为自检（5问触发情况）/ 📋 风险分类框架（三类+可对冲性）/ 🎯 状态机输出（状态/操作/仓位上限/置信度/可操作指令）+ 止盈检查结论 / 🔗 锚点操作建议（三档建仓锚点+止损线+悲观情景修正锚点）/ 📋 持仓管理指引（新仓止损线/老仓观察区/基本面清仓条件）/ ⚠️ 风险提示（编号条目）。

## 执行摘要
一段总结判断 + **核心逻辑**（要点列表）+ **主要风险**（要点列表，按风险分类框架标注可对冲性）+ **操作建议**（含仓位节奏/锚点/止损/持仓管理指引/后续跟踪验证点 的完整段落）。
末尾「**超出清单的思考**」三行：**核心矛盾**：该标的驱动价格与估值的首要变量；**清单外关键数据**：不在采集清单但对判断重要的变量（无则写"未发现"）；**路径依赖检查**：是否存在应换的理解框架。

【硬性规则】
1. 判定阈值与规则以《执行手册》为准（请求中如附手册全文则为唯一裁定依据）；所有数据以提供的真实数据底稿 JSON 为准；决策卡 JSON 中**非空**的字段（状态、评分、仓位、结论）须与报告一致；为 null/空/默认值（0/false/空串）的字段视为"未给出"，按真实数据与手册规则自行推演补齐；真实数据中没有的数值不得编造（可基于训练数据知识补充行业参照/机构观点，需注明）。
2. 任何字段数据缺失写 "—" 或 "UNA"（重要字段附【无法获取】标识），绝不允许整节缺失或空白；比率对比【年度】vs【年度】、【单季】vs【单季】，不得交叉。
3. 叙事密度对齐参考范例：数据表后配 "> **关键信号：**" 点评；辩论五派各有论据与回应；压力测试六步俱全。
4. 仅输出 Markdown 报告正文（以 "# 【数据底稿】" 开头），不要输出任何解释、代码块围栏或多余内容。

【风格参照 —— 若附有参考范例文件则模仿其表格样式、框线样式、"> 关键信号" 点评口吻与叙事密度；**章节结构一律以本规范 26 节为准**，数据一律替换为本标的真实数据】
"""


def _load_example() -> str:
    try:
        return _REF_EXAMPLE_PATH.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001  范例缺失时仅按规范产出
        return "（参考范例文件缺失，请按结构规范完整产出每一节。）"


_WRITER_INSTRUCTIONS = _WRITER_SPEC + "\n" + _load_example()

report_writer = Agent(
    name="report_writer",
    instructions=_WRITER_INSTRUCTIONS,
    model=LLM_MODEL,
)


def _strip_fences(s: str) -> str:
    """去除 LLM 输出可能包裹的 ```markdown 围栏。"""
    s = (s or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s)
    return s.strip()


def _compact_raw(raw: dict) -> dict:
    """裁剪 raw 数据用于 prompt：去缓存标记、截断超长列表/字符串、去除巨型资金流原始 kline。"""
    def walk(v):
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items() if k != "_cached"}
        if isinstance(v, list):
            items = [walk(x) for x in v[:12]]
            if len(v) > 12:
                items.append(f"...（另 {len(v) - 12} 条省略）")
            return items
        if isinstance(v, str) and len(v) > 600:
            return v[:600] + "...（截断）"
        return v

    out = walk(raw or {})
    cf = out.get("capital_flow")
    if isinstance(cf, dict) and isinstance(cf.get("raw"), dict):
        klines = ((cf["raw"].get("data") or {}).get("klines")) or []
        cf["raw_summary"] = f"{len(klines)} 条资金流 kline（明细省略）"
        cf.pop("raw", None)
    return out


def _writer_prompt(raw: dict, card_dict: dict, stock_code: str, stock_name: str,
                   analysis_date: str) -> str:
    raw_json = json.dumps(_compact_raw(raw), ensure_ascii=False, default=str)
    card_json = json.dumps(card_dict, ensure_ascii=False, default=str)
    return (
        f"标的：{stock_name}（{stock_code}），分析日期：{analysis_date}。\n\n"
        "【真实数据底稿（L1 代码层采集，数据以此为准，数据底稿层表格须尽量从其中提取字段）】\n"
        f"```json\n{raw_json}\n```\n\n"
        "【已完成的投研决策卡（L2/L3 各节的状态/评分/数值/结论必须与之一致）】\n"
        f"```json\n{card_json}\n```\n\n"
        "请按结构规范与参考范例风格，撰写本标的的完整分析报告。"
    )


async def _write_draft(raw: dict, card_dict: dict, stock_code: str,
                       stock_name: str, analysis_date: str) -> str:
    result = await Runner.run(
        report_writer, _writer_prompt(raw, card_dict, stock_code, stock_name, analysis_date))
    return _strip_fences(str(result.final_output))


def _repair_prompt(md: str, issues: list[dict], raw: dict, card_dict: dict,
                   stock_code: str, stock_name: str, analysis_date: str) -> str:
    lines = []
    for it in issues:
        if it["issue"] == "missing":
            lines.append(f'- 缺失章节："{it["title"]}"（{_BY_ID[it["id"]].layer} 层）——必须按参考范例补齐整节')
        else:
            lines.append(f'- 内容不足："{it.get("found_title") or it["title"]}"（当前仅 {it.get("chars", 0)} 字/'
                         f'{it.get("rows", 0)} 表格行）——须补充实质分析内容（表格+点评）')
    raw_json = json.dumps(_compact_raw(raw), ensure_ascii=False, default=str)
    card_json = json.dumps(card_dict, ensure_ascii=False, default=str)
    return (
        f"你此前为 {stock_name}（{stock_code}，{analysis_date}）撰写的报告未通过结构校验。\n"
        "【问题清单】\n" + "\n".join(lines) + "\n\n"
        "【要求】① 未出问题的章节保持原样；② 按参考范例结构补齐所有缺失/不足章节，"
        "每节须有数据表格与点评等实质内容；③ 数据以真实数据底稿为准、结论与决策卡一致、不得编造；"
        "④ 输出修复后的**完整** Markdown 报告（非 diff），以 \"# 【数据底稿】\" 开头。\n\n"
        f"【真实数据底稿】\n```json\n{raw_json}\n```\n\n"
        f"【决策卡】\n```json\n{card_json}\n```\n\n"
        f"【当前报告全文】\n{md}"
    )


async def _repair(md: str, issues: list[dict], raw: dict, card_dict: dict,
                  stock_code: str, stock_name: str, analysis_date: str) -> str:
    result = await Runner.run(
        report_writer, _repair_prompt(md, issues, raw, card_dict, stock_code, stock_name, analysis_date))
    return _strip_fences(str(result.final_output))


# ============================================================
# 兜底骨架：仍缺失的节由代码从 raw/card 确定性渲染（保证章节永不为空）
# ============================================================

def _t(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join(["---"] * len(headers)) + " |"]
    for r in rows:
        out.append("| " + " | ".join("—" if c is None or c == "" else str(c) for c in r) + " |")
    return "\n".join(out)


def _pct(v):
    return f"{v}%" if v is not None else None


def _skeleton(section_id: str, raw: dict, card: dict, name: str, code: str) -> str:
    """按节 id 从 raw/card 渲染最小骨架（标题+表格/要点）。"""
    m = raw.get("market") or {}
    src = m.get("source") or "—"
    dt = card.get("data_trace") or {}
    h = f"## { _BY_ID[section_id].title }"

    if section_id == "l0_constraint":
        bs = raw.get("business_segments") or {}
        main_biz = bs.get("main_business") or "UNA"
        return h + "\n\n" + _t(
            ["校验项", "内容", "结果"],
            [["收入来源拆解", str(main_biz)[:120], "⚠️ 骨架补充"],
             ["主要客户类型", "UNA（骨架补充）", "⚠️"],
             ["成本结构", "UNA（骨架补充）", "⚠️"],
             ["利润驱动因素", "UNA（骨架补充）", "⚠️"]])

    if section_id == "l0_quote":
        return h + "\n\n" + _t(
            ["字段", "数值", "来源", "状态"],
            [["最新价", m.get("latest_price"), src, "主源"],
             ["涨跌幅", _pct(m.get("change_pct")), src, "主源"],
             ["今开", m.get("open"), src, "主源"],
             ["最高", m.get("high"), src, "主源"],
             ["最低", m.get("low"), src, "主源"],
             ["振幅", _pct(m.get("amplitude")), src, "主源"],
             ["成交量(手)", m.get("volume_lots"), src, "主源"],
             ["成交额(万元)", m.get("turnover_wan"), src, "主源"],
             ["换手率", _pct(m.get("turnover_rate")), src, "主源"],
             ["PE(TTM)", m.get("pe_ttm"), m.get("pe_status") or src, "交叉验证"],
             ["PB", m.get("pb"), m.get("pb_status") or src, "交叉验证"],
             ["股息率(TTM)", _pct(m.get("dividend_yield")), m.get("dividend_source") or src, "主源"],
             ["总市值(亿)", m.get("total_market_cap_yi"), src, "主源"],
             ["流通市值(亿)", m.get("circ_market_cap_yi"), src, "主源"]])

    if section_id == "l0_financial":
        rows = []
        for f in (dt.get("financials") or []):
            rows.append([f.get("field"), f.get("latest"), f.get("previous"), f.get("two_periods_ago")])
        if not rows:
            fin = (raw.get("financial") or {}).get("data") or []
            fmap = [("TOTAL_OPERATE_INCOME", "营业总收入(元)"), ("PARENT_NETPROFIT", "归母净利润(元)"),
                    ("WEIGHTAVG_ROE", "ROE(加权)%"), ("BASIC_EPS", "基本EPS"), ("BPS", "每股净资产")]
            for k, label in fmap:
                rows.append([label] + [p.get(k) for p in fin[:3]])
        return h + "\n\n" + _t(["科目", "最新", "上期", "上上期"], rows or [["UNA", None, None, None]])

    if section_id == "l0_dividend":
        rows = [[d.get("event"), d.get("date"), d.get("amount")]
                for d in (dt.get("dividends") or [])[:6]]
        if not rows:
            for d in (raw.get("baostock_dividends") or [])[:5]:
                if isinstance(d, dict):
                    rows.append([f'{d.get("year", "")}年分红', d.get("dividOperateDate", ""),
                                 f'每股{d.get("dividCashPsBeforeTax", "")}元'])
        return h + "\n\n" + _t(["事件", "日期", "金额"], rows or [["UNA", None, None]])

    if section_id == "l0_reverse":
        rc = raw.get("reverse_check_raw") or {}
        pledge = "有质押记录" if rc.get("pledge_records") else "未发现显著质押"
        return h + "\n\n" + _t(
            ["字段", "数值", "来源", "状态"],
            [["大股东质押率", pledge, rc.get("source") or "巨潮", "主源"],
             ["ESG负面事件", "UNA（骨架补充）", "—", "UNA"],
             ["审计意见", "UNA（骨架补充）", "—", "UNA"],
             ["近两年累计净利润", "UNA（骨架补充）", "—", "UNA"],
             ["分红率", "UNA（骨架补充）", "—", "UNA"]])

    if section_id == "l0_flow":
        cf = raw.get("capital_flow") or {}
        nb = cf.get("northbound_akshare") or {}
        return h + "\n\n" + _t(
            ["字段", "数值", "来源", "状态"],
            [["主力资金(近5日)", cf.get("raw_summary") or ("UNA" if cf.get("error") else "东财资金流"),
              cf.get("source") or "—", "主源" if not cf.get("error") else "UNA"],
             ["北向资金", nb.get("note") or ("UNA（非十大活跃股）" if not nb else ""),
              nb.get("source") or "—", "备源" if nb else "UNA"]])

    if section_id == "l0_forecast":
        rows = [[f.get("institution"), f.get("rating"), f.get("target_price")]
                for f in (dt.get("forecasts") or [])]
        if not rows:
            inst = raw.get("institution_forecast") or {}
            for it in (inst.get("data") or [])[:2]:
                if isinstance(it, dict):
                    rows.append([f'{it.get("RATING_ORG_NUM", "—")}家机构覆盖',
                                 f'买入{it.get("RATING_BUY_NUM")}/增持{it.get("RATING_ADD_NUM")}',
                                 f'{it.get("DEC_AIMPRICEMIN")}~{it.get("DEC_AIMPRICEMAX")}元'])
        return h + "\n\n" + _t(["机构", "评级", "目标价"], rows or [["UNA", None, None]])

    # ── L2 骨架：全部取自决策卡 ──
    if section_id == "l2_hardgate":
        hg = card.get("hard_gate") or {}
        kv = hg.get("key_values") or {}
        return h + "\n\n" + _t(
            ["项目", "结果"],
            [["是否通过", "✅ 通过" if hg.get("passed") else "❌ 不通过"],
             ["适用模型", hg.get("applied_model")],
             ["PE / PB / 股息率", f'{kv.get("pe")} / {kv.get("pb")} / {_pct(kv.get("dividend_yield"))}'],
             ["ROE(最新)", _pct(kv.get("roe_latest"))],
             ["不良率 / 拨备覆盖率", f'{kv.get("npl_ratio")} / {kv.get("coverage_ratio")}'],
             ["质量调节器 / 备选池", f'{"触发" if hg.get("quality_regulator_triggered") else "未触发"} / '
              f'{"触发" if hg.get("alt_pool_triggered") else "未触发"}', ],
             ["判定理由", hg.get("reason")]])

    if section_id == "l2_reverse":
        rc = card.get("reverse_check") or {}
        rows = [[it.get("name"), "⚠️ 触发" if it.get("triggered") else "✅ 正常", it.get("detail")]
                for it in (rc.get("items") or [])]
        return h + "\n\n" + _t(["检查项", "状态", "详情"],
                               rows or [["—", "✅ 正常" if rc.get("passed") else "⚠️ 存疑", None]]) + \
            f'\n\n**结论：{"通过" if rc.get("passed") else "存疑"}，置信度降级 {rc.get("confidence_downgrades") or 0} 档。**'

    if section_id == "l2_five":
        fs = card.get("five_signals") or {}
        return h + "\n\n" + _t(
            ["维度", "信号", "依据"],
            [["趋势 Trend", fs.get("trend"), None],
             ["资金 Flow", fs.get("flow"), None],
             ["估值 Value", fs.get("value"), None],
             ["质量 Quality", fs.get("quality"), None],
             ["情绪 Sentiment", fs.get("sentiment_score"), fs.get("details")]])

    if section_id == "l2_factor":
        fv = card.get("factor_validation") or {}
        def vs(v):
            return "UNA" if v is None else ("有效" if v else "失效")
        return h + "\n\n" + _t(
            ["因子", "有效性"],
            [["趋势因子", vs(fv.get("trend_valid"))], ["资金因子", vs(fv.get("flow_valid"))],
             ["估值因子", vs(fv.get("value_valid"))], ["质量因子", vs(fv.get("quality_valid"))],
             ["情绪因子", vs(fv.get("sentiment_valid"))]]) + \
            f'\n\n> 失效因子：{", ".join(fv.get("invalid_factors") or []) or "无"}。{fv.get("note") or ""}'

    if section_id == "l2_capital":
        cp = card.get("capital_profile") or {}
        return h + "\n\n" + _t(
            ["资金类型", "方向/概况"],
            [["主力", cp.get("main_force")], ["机构", cp.get("institution")],
             ["北向", cp.get("northbound")], ["融资", cp.get("margin")]]) + \
            f'\n\n> {cp.get("summary") or "UNA"}'

    if section_id == "l2_trap":
        vt = card.get("value_trap") or {}
        rows = [[s.get("name"), "⚠️ 触发" if s.get("triggered") else "✅ 未触发",
                 _pct(s.get("position_cap"))] for s in (vt.get("signals") or [])]
        return h + "\n\n" + _t(["信号", "触发情况", "仓位上限"],
                               rows or [["—", "✅ 未触发" if not vt.get("triggered") else "⚠️ 触发",
                                         _pct(vt.get("position_cap"))]])

    if section_id == "l2_quant":
        qe = card.get("quant_enhance") or {}
        return h + "\n\n" + _t(
            ["项目", "结果"],
            [["DCF 估值", f'执行：{qe.get("dcf_value")}元' if qe.get("dcf_executed") else "跳过"],
             ["备注", qe.get("note") or "—"]])

    if section_id == "l2_debate":
        db = card.get("debate") or {}
        rows = [[f.get("name"), f.get("stance"), f.get("score"), f.get("weight")]
                for f in (db.get("factions") or [])]
        return h + "\n\n" + _t(["派别", "立场", "评分(-10~+10)", "权重"],
                               rows or [["—", None, None, None]]) + \
            f'\n\n**综合得分：{db.get("composite_score")}｜共识/分歧：{db.get("consensus")}｜胜方：{db.get("winner")}。**\n\n> {db.get("summary") or ""}'

    if section_id == "l2_stress":
        st = card.get("stress_test") or {}
        return h + "\n\n" + _t(
            ["项目", "结果"],
            [["基准情景估值", f'{st.get("base_valuation")}元' if st.get("base_valuation") else None],
             ["悲观情景估值", f'{st.get("pessimistic_valuation")}元' if st.get("pessimistic_valuation") else None],
             ["DCF 估值区间", st.get("dcf_valuation_range")],
             ["下行风险", _pct(st.get("downside_risk"))],
             ["5日VaR", _pct(st.get("var_5d"))],
             ["极端波动声明", "是" if st.get("extreme_volatility") else "否"],
             ["压力情景", st.get("scenario") or "—"]]) + f'\n\n> {st.get("note") or ""}'

    if section_id == "l2_behavior":
        bc = card.get("behavior_check") or {}
        rows = [[item, "⚠️ 触发"] for item in (bc.get("triggered_items") or [])]
        return h + "\n\n" + _t(["自检问题", "结果"],
                               rows or [["四项自检（FOMO/目标价幻觉/周期性/社媒情绪）",
                                         "⚠️ 有提示" if bc.get("has_warning") else "✅ 未触发"]])

    if section_id == "l2_state":
        fd = card.get("final_decision") or {}
        an = card.get("anchors") or {}
        rows = [[a.get("level"), f'{a.get("price")}元', _pct(a.get("position"))]
                for a in (an.get("entry_anchors") or [])]
        return h + "\n\n" + _t(
            ["项目", "结果"],
            [["状态机状态", fd.get("state")], ["操作建议", fd.get("action")],
             ["仓位上限", _pct(fd.get("position_cap"))], ["置信度", fd.get("confidence")],
             ["止盈检查", fd.get("stop_profit_check")],
             ["止损线", f'{an.get("stop_loss_line")}元' if an.get("stop_loss_line") else None]]) + \
            ("\n\n" + _t(["档位", "价格", "仓位"], rows) if rows else "")

    if section_id == "l3_card":
        fd = card.get("final_decision") or {}
        hg = card.get("hard_gate") or {}
        fs = card.get("five_signals") or {}
        return (
            "# 【L3：输出展示层——决策卡】（骨架补充）\n\n"
            "┌─────────────────────────────────────────────────┐\n"
            f"│ **标的：{name}  代码：{code}**\n"
            f"│ ✅ 硬门槛筛选：{'通过' if hg.get('passed') else '不通过'}（{(hg.get('applied_model') or '—')}）\n"
            f"│ 📊 五维信号：趋势 {fs.get('trend')} / 资金 {fs.get('flow')} / 估值 {fs.get('value')} / 质量 {fs.get('quality')}\n"
            f"│ 🎯 状态机输出：{fd.get('state')}  操作：{fd.get('action')}  仓位上限：{_pct(fd.get('position_cap'))}  置信度：{fd.get('confidence')}\n"
            "└─────────────────────────────────────────────────┘"
        )

    if section_id == "l3_summary":
        fd = card.get("final_decision") or {}
        risks = card.get("risks") or []
        risk_lines = "\n".join(f"- {r}" for r in risks[:5]) or "- UNA"
        return (
            f"## 执行摘要（骨架补充）\n\n"
            f"**{name}（{code}）状态机输出：{fd.get('state') or 'UNA'}，操作建议：{fd.get('action') or 'UNA'}，"
            f"仓位上限 {_pct(fd.get('position_cap')) or 'UNA'}，置信度：{fd.get('confidence') or 'UNA'}。**\n\n"
            f"**主要风险：**\n{risk_lines}"
        )

    return h + "\n\nUNA（骨架补充）"


def _section_range(lines: list[str], headings: list, spec: SectionSpec,
                   until_any_heading: bool = False):
    """定位 spec 匹配的节：返回 (标题行号, 正文结束行号)，未找到返回 None。

    until_any_heading=True 时正文截至下一个任意级别标题（就地替换用，
    避免吞掉后续小节标题，如 # L3 决策卡 之后的 ## 执行摘要）。
    """
    for pos, (li, lvl, title) in enumerate(headings):
        if any(k in title for k in spec.keywords):
            end = len(lines)
            for li2, lvl2, _ in headings[pos + 1:]:
                if until_any_heading or lvl2 <= lvl:
                    end = li2
                    break
            return li, end
    return None


def _fill_missing(md: str, issues: list[dict], raw: dict, card: dict,
                  name: str, code: str) -> str:
    """补齐缺失节、就地充实内容不足的节（骨架渲染，确定性，必然终止）。

    - thin 节：保留原标题，正文就地替换为骨架内容；
    - missing 节（含 thin 替换后新暴露的）：按层插入（L0→L2 前，L2→L3 前，L3→末尾）。
    """
    lines = md.splitlines()

    # ── 1. thin 节就地替换正文（每次迭代重新定位标题，索引始终有效）──
    thin = [it for it in issues if it["issue"] == "thin"]
    for it in sorted(thin, key=lambda x: x["id"]):
        spec = _BY_ID[it["id"]]
        rng = _section_range(lines, list(_iter_headings(lines)), spec,
                             until_any_heading=True)
        if rng is None:
            continue
        li, end = rng
        sk_lines = _skeleton(it["id"], raw, card, name, code).splitlines()
        lines[li + 1:end] = [""] + sk_lines[1:] + [""]

    # ── 2. 重新校验：剩余 missing（含替换中暴露的）按层插入 ──
    remaining = validate_report("\n".join(lines))
    missing = [it for it in remaining if it["issue"] == "missing"]
    if not missing:
        return "\n".join(lines)

    def heading_idx(pred):
        for i, ln in enumerate(lines):
            if ln.startswith("#") and pred(ln):
                return i
        return None

    idx_l3 = heading_idx(lambda s: "L3" in s or "输出展示层" in s)

    groups: dict[str, list[str]] = {"L0": [], "L2": [], "L3": []}
    for it in missing:
        spec = _BY_ID[it["id"]]
        groups[spec.layer].append(
            _skeleton(it["id"], raw, card, name, code)
            + "\n\n（注：本节由系统骨架补充，仅含结构化数据）")

    def block(key: str) -> list[str]:
        return ["", "\n\n".join(groups[key]), ""] if groups[key] else []

    # 从后向前插入，避免索引漂移
    if groups["L3"]:
        lines.extend(block("L3"))
    if groups["L2"]:
        idx_l3 = heading_idx(lambda s: "L3" in s or "输出展示层" in s)
        if idx_l3 is not None:
            lines[idx_l3:idx_l3] = block("L2")
        else:
            lines.extend(block("L2"))
    if groups["L0"]:
        idx_l2 = heading_idx(lambda s: "L2" in s or "分析计算层" in s)
        if idx_l2 is not None:
            lines[idx_l2:idx_l2] = block("L0")
        else:
            lines.extend(block("L0"))
    return "\n".join(lines)


# ============================================================
# 主入口
# ============================================================

MAX_REPAIRS = 2  # 修复轮数硬上限（防死循环）


async def generate_report(raw: dict, card, stock_code: str,
                          stock_name: str | None = None,
                          max_repairs: int = MAX_REPAIRS) -> tuple[str, list[dict]]:
    """生成完整分析报告。返回 (report_markdown, 残余问题列表)。

    闭环：初稿 → 校验 → 至多 max_repairs 轮针对性修复（无改善提前退出）→ 确定性骨架兜底。
    """
    card_dict = card.model_dump(mode="json") if hasattr(card, "model_dump") else dict(card)
    name = (stock_name
            or (card_dict.get("header") or {}).get("stock_name")
            or (raw.get("market") or {}).get("stock_name")
            or stock_code)
    analysis_date = (card_dict.get("header") or {}).get("analysis_date") or date.today().isoformat()

    draft = await _write_draft(raw, card_dict, stock_code, name, analysis_date)
    issues = validate_report(draft)

    repairs_used = 0
    while issues and repairs_used < max_repairs:
        repairs_used += 1
        print(f"📝 报告校验发现 {len(issues)} 处问题，第 {repairs_used}/{max_repairs} 轮修复…")
        fixed = await _repair(draft, issues, raw, card_dict, stock_code, name, analysis_date)
        new_issues = validate_report(fixed)
        if len(new_issues) >= len(issues):
            # 修复无改善 → 提前退出，交给骨架兜底（避免无效循环）
            print(f"⚠️ 修复未改善（{len(issues)}→{len(new_issues)}），停止修复，骨架兜底")
            break
        draft, issues = fixed, new_issues

    if issues:
        draft = _fill_missing(draft, issues, raw, card_dict, name, stock_code)
        issues = validate_report(draft)
    return draft, issues


async def generate_and_save_report(raw: dict, card, stock_code: str,
                                   stock_name: str | None = None) -> pathlib.Path:
    """生成报告并写入 analysis_reports/{code}-{name}.md（沿用原命名约定）。"""
    md, issues = await generate_report(raw, card, stock_code, stock_name)
    card_dict = card.model_dump(mode="json") if hasattr(card, "model_dump") else dict(card)
    name = (stock_name
            or (card_dict.get("header") or {}).get("stock_name")
            or (raw.get("market") or {}).get("stock_name")
            or stock_code)
    reports_dir = pathlib.Path(__file__).resolve().parent.parent / "analysis_reports"  # 仓库根
    reports_dir.mkdir(exist_ok=True)
    filepath = reports_dir / f"{stock_code}-{name}.md"
    filepath.write_text(md, encoding="utf-8")
    if issues:
        print(f"⚠️ 报告已保存（残余问题 {len(issues)}）: {filepath}")
    else:
        print(f"📄 分析报告已保存（结构校验通过）: {filepath}")
    return filepath


# ============================================================
# CLI：校验现有报告文件
# ============================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: uv run python report_generator.py <报告.md> [更多报告.md ...]")
        sys.exit(2)
    exit_code = 0
    for arg in sys.argv[1:]:
        p = pathlib.Path(arg)
        problems = validate_report(p.read_text(encoding="utf-8"))
        if problems:
            exit_code = 1
            print(f"❌ {p.name}: {len(problems)} 处问题")
            for it in problems:
                print(f'   - [{it["issue"]}] {it["layer"]} {it["title"]}')
        else:
            print(f"✅ {p.name}: 结构校验通过（{len(REQUIRED_SECTIONS)} 节齐全）")
    sys.exit(exit_code)
