"""L3 编排 Agent — orchestrator

主 Agent：instructions 引用手册全文，L1 取数 + 阶段2 下钻由代码层（run_analysis）完成，
数据注入 prompt 后 output_type=StockDecisionCard 直接装配最终决策卡：
  header + DataTrace(来自 L1) + AnalysisBody(来自 L2/L3)。
OPENAI_API_KEY 从环境变量读取（openai-agents 默认从 OPENAI_API_KEY 取，此处仅做友好检查）。
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
from datetime import date

from agents import Agent, AgentOutputSchema, Runner
from config import LLM_MODEL, OPENAI_API_KEY, OPENAI_BASE_URL, has_api_key

from schemas import StockDecisionCard

_MANUAL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "A股投资决策系统执行手册.md",
)


def _load_manual() -> str:
    """读取手册全文，作为 orchestrator instructions 的一部分，确保规则完整覆盖。"""
    try:
        with open(_MANUAL_PATH, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""  # 手册缺失时，run_analysis prompt 中仍含核心规则


_MANUAL = _load_manual()

orchestrator = Agent(
    name="trading_goose_orchestrator",
    instructions=(
        "你是 A 股投资决策系统的主编排 Agent。**严格遵循以下执行手册全文**：\n\n"
        "==== 执行手册全文开始 ====\n"
        f"{_MANUAL}\n"
        "==== 执行手册全文结束 ====\n\n"
        "核心承诺：防止本金永久性损失，而非挖掘\"伟大公司\"。所有规则为概率化启发式，不提供确定性预测。\n"
        "系统定位：个人投资者 + AI Agent 协作的辅助决策工具，非自动交易系统。\n\n"
        "你的职责：对用户给定的股票代码，编排 L1→L2→L3 完整流程，产出最终决策卡 StockDecisionCard。\n"
        "1. 组装 StockDecisionCard：header(标的名/代码/今日日期 " + date.today().isoformat() + ") + "
        "data_trace(来自 L1) + hard_gate/reverse_check/five_signals/value_trap/quant_enhance/"
        "debate/factor_validation/capital_profile/stress_test/behavior_check/final_decision/anchors/"
        "exit_conditions/risks(来自 L2/L3)。\n"
        "**必须填充所有结构化区块**（数据 UNA 的字段填 None/空，但区块本身必须产出，不得整体省略）：\n"
        "- debate（§4.11/§4.12 五维辩论）：价值/成长/质量/资金/情绪五派各 score(-10~+10)+weight+stance，"
        "composite_score 加权综合，consensus 共识/分歧，winner 多头/空头/势均，defense_mode 防御状态；\n"
        "- factor_validation：验证 trend/flow/value/quality/sentiment 因子数据有效性，UNA 标 invalid；\n"
        "- capital_profile：主力/机构/北向/融资资金方向 + summary 合力画像；\n"
        "- stress_test（双重估值+§4.13 悲观情景）：base_valuation 基准情景 + pessimistic_valuation 悲观情景 + "
        "downside_risk 下行风险 + var_5d 5日VaR + extreme_volatility 极端波动声明。\n\n"
        "不懂不投原则 + 200 字硬约束：若 L1 取数发现收入来源/客户类型/成本结构/利润驱动任一缺失 → "
        "标记\"理解不足，暂缓分析\"，不进入分析。\n"
        "若 OPENAI_API_KEY 缺失或数据不可得，UNA 字段按默认规则处理，**不得编造数值**。\n\n"
        "**判断校准规则（避免机械判断）**：\n"
        "A. UNA 不构成硬阻断（手册§3.1）：UNA 字段按默认规则处理，不影响核心判断。\n"
        "**模型四（银行/保险）硬门槛 passed 判定**：模型四要求 PE≤10(辅助) + 不良率<1.8% + 拨备覆盖率>160% + NIM 企稳/上行"
        "**同时满足**。若不良率/拨备/NIM 任一 UNA 且 PE≤10 → passed=False + 置信度降一级（高→中），"
        "reason 标注\"不良率/拨备/NIM UNA 无法完全验证\"。不能仅因 PE≤10 就判 passed=True。\n"
        "B. Trend 判定基于日线(50%)+周线(50%)综合，非单日涨跌。\n"
        "C. 银行行业不适用的价值陷阱规则：\"经营现金流/扣非净利润<50%\"不适用于银行。\n"
    ),
    tools=[],
    output_type=AgentOutputSchema(StockDecisionCard, strict_json_schema=False),
    model=LLM_MODEL,
)


def _preprocess_json(data: dict) -> dict:
    """预处理 LLM 输出的 JSON，修复常见格式问题：Literal 多余文本、list 字段误输出 dict、string 误输出 dict 等。"""
    import copy
    d = copy.deepcopy(data)

    # 修复 Literal 字段：去除多余括号说明
    _literal_clean = {
        "trend": ("UP", "FLAT", "DOWN"),
        "flow": ("POS", "POSITIVE", "NEU", "NEUTRAL", "NEG", "NEGATIVE"),
        "value": ("LOW", "FAIR", "HIGH"),
        "quality": ("OK", "CAU", "POOR", "CAUTION"),
        "applied_model": ("模型一", "模型二", "模型三", "模型四", "无"),
        "winner": ("多头", "空头", "势均"),
        "main_force": ("净流入", "净流出", "平衡"),
        "institution": ("净流入", "净流出", "平衡"),
        "northbound": ("净流入", "净流出", "UNA"),
        "state": ("StrongLong完整", "StrongLong降级", "ValueReversion", "Range", "Watch", "Exit"),
        "confidence": ("高", "中", "低", "极低"),
        "consensus": ("共识", "分歧"),
        "nim_trend": ("企稳", "下行", "上行"),
        "completeness": ("完整", "基本完整", "部分缺失", "严重不足"),
        "credibility": ("高", "中", "低", "不可用"),
        "fcf_caliber": ("首选", "次选", "备选"),
    }
    _alias_map = {"POSITIVE": "POS", "NEGATIVE": "NEG", "NEUTRAL": "NEU", "CAUTION": "CAU"}
    for key, valid_values in _literal_clean.items():
        val = d.get(key) if isinstance(d, dict) else None
        if isinstance(val, str):
            for vv in valid_values:
                if vv in val:
                    d[key] = _alias_map.get(vv, vv)
                    break

    # 递归修复嵌套 dict 中的 Literal 字段
    def _walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                full_key = f"{path}.{k}" if path else k
                # 修复 Literal 字段
                if isinstance(v, str):
                    for lk, valid_values in _literal_clean.items():
                        if lk in k.lower() or lk.split("_")[-1] in k.lower():
                            for vv in valid_values:
                                if vv in v:
                                    obj[k] = vv
                                    break
                # 修复 list 字段误输出为 dict（financials, business_breakdown, dividends, forecasts）
                if k in ("financials", "business_breakdown", "dividends", "forecasts") and isinstance(v, dict):
                    obj[k] = [v]
                # 修复 list 字段误输出为 dict（exit_conditions, risks, triggered_items）
                if k in ("exit_conditions", "risks", "triggered_items") and isinstance(v, dict):
                    obj[k] = [str(vv) for vv in v.values()] if v else []
                # 修复 list 字段误输出为 dict（financials, business_breakdown, dividends, forecasts, fcf_history）
                # fcf_history 特判：整段说明性 dict（"银行不适用"类）→ 空列表，包裹成 [v] 会造出全 None 假条目
                if k in ("financials", "business_breakdown", "dividends", "forecasts") and isinstance(v, dict):
                    obj[k] = [v]
                if k == "fcf_history" and isinstance(v, dict):
                    obj[k] = [] if set(v) <= {"note", "reason", "comment", "detail", "summary"} else [v]
                # Literal 兜底：枚举字段值是纯说明文本、不含任何合法值
                # → 收敛到安全默认，原文转入 summary 保信息（northbound 有 UNA 合法值优先用之）
                if k in ("main_force", "institution", "northbound") and isinstance(v, str) and v not in _literal_clean[k]:
                    guess = next((vv for vv in _literal_clean[k] if vv in v), None)
                    if guess is None:
                        if "流出" in v:
                            guess = "净流出"
                        elif "流入" in v:
                            guess = "净流入"
                        else:
                            guess = "UNA" if "UNA" in _literal_clean[k] else "平衡"
                        prof = obj.get("summary")
                        obj["summary"] = (f"{prof}；{k}：{v}" if isinstance(prof, str) and prof else f"{k}：{v}")
                    obj[k] = guess
                # 修复 string 字段误输出为 dict（stop_profit_check, details 等）
                if k in ("stop_profit_check", "details") and isinstance(v, dict):
                    obj[k] = str(v)
                # 修复 标量字段误输出为 dict（five_signals trend/flow/value/quality 等）
                if k in ("trend", "flow", "value", "quality", "sentiment_score") and isinstance(v, dict):
                    obj[k] = v.get("signal") or v.get("score") or v.get("value") or str(v)
                # 修复 bool 字段误输出为 dict/string
                if k in ("alt_pool_triggered", "quality_regulator_triggered", "defense_mode",
                         "extreme_volatility", "has_warning", "dcf_executed"):
                    if isinstance(v, dict):
                        obj[k] = v.get("active") or v.get("triggered") or False
                    elif isinstance(v, str):
                        obj[k] = v.startswith("True") or v == "true" or "UNA" not in v
                # 修复 number 字段误输出为 dict（stress_test 估值字段）
                if k in ("base_valuation", "pessimistic_valuation", "downside_risk", "var_5d",
                         "dcf_value", "fcf", "latest_price", "pe_ttm", "pb", "dividend_yield") and isinstance(v, dict):
                    # 尝试提取数值
                    found = None
                    for vk, vv in v.items():
                        if isinstance(vv, (int, float)):
                            found = float(vv)
                            break
                        if isinstance(vv, str):
                            try:
                                found = float(vv.replace("~", "").replace("元", "").strip())
                                break
                            except ValueError:
                                continue
                    obj[k] = found
                # 修复 Literal 字段：去除多余括号说明
                if isinstance(v, str):
                    for lk, valid_values in _literal_clean.items():
                        if lk in k.lower() or lk in k:
                            for vv in valid_values:
                                if vv in v:
                                    obj[k] = vv
                                    break
                _walk(v, full_key)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item, path)

    _walk(d)
    return d


def _validate_json(data: dict, model_cls: type, errors: list) -> StockDecisionCard:
    """验证 JSON，预处理后重试一次。"""
    try:
        return model_cls.model_validate(data)
    except (ValueError,) as e:
        errors.append(f"full: {e}")
        # 预处理后重试
        cleaned = _preprocess_json(data)
        return model_cls.model_validate(cleaned)


def _extract_json_with_tolerance(raw: str, model_cls: type) -> StockDecisionCard:
    """从 LLM 输出中提取 JSON，容错处理：strip markdown fences / 取首个完整 JSON 对象。
    解析失败抛 RuntimeError（调用方捕获后发 error 事件不崩溃）。"""
    text = raw.strip()
    errors: list[str] = []

    # 1. Strip markdown code fences（```json ... ```）
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?\s*```\s*$", "", text)
        text = text.strip()

    # 2. Try full text as JSON
    try:
        data = json.loads(text)
        return _validate_json(data, model_cls, errors)
    except (json.JSONDecodeError, ValueError) as e:
        errors.append(f"full: {e}")

    # 3. Extract from first { to matching }（处理 LLM 输出 JSON 后有额外文本）
    try:
        start = text.index("{")
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    data = json.loads(candidate)
                    return _validate_json(data, model_cls, errors)
    except (ValueError, json.JSONDecodeError) as e:
        errors.append(f"brace: {e}")

    raise RuntimeError(
        f"LLM 输出解析失败（{'; '.join(errors)}）。"
        f"raw[:300]={raw[:300]}"
    )


def _backfill_data_trace(card: StockDecisionCard, raw: dict) -> None:
    """代码层直接从 fetch_raw_data 结果填充 data_trace，不依赖 LLM。
    只填充 LLM 留空的字段（不覆盖已有值）。"""
    dt = card.data_trace
    mkt = raw.get("market", {})

    # 1. quote ← raw['market']
    q = dt.quote
    for attr, key in [("latest_price","latest_price"),("change_pct","change_pct"),
                       ("pe_ttm","pe_ttm"),("pb","pb"),("dividend_yield","dividend_yield")]:
        if getattr(q, attr) is None and mkt.get(key) is not None:
            setattr(q, attr, mkt[key])

    # 2. financials ← raw['financial']['data']
    fin_data = raw.get("financial", {}).get("data")
    if not dt.financials and fin_data:
        fmap = [("TOTAL_OPERATE_INCOME","营业总收入",1e8),("PARENT_NETPROFIT","归母净利润",1e8),
                ("WEIGHTAVG_ROE","ROE(加权)",None),("BASIC_EPS","基本EPS",None),
                ("BPS","每股净资产",None),("SJLTZ","净利润增速%",None),("YSTZ","营收增速%",None)]
        periods = fin_data[:3]
        for src, label, div in fmap:
            vals = []
            for p in periods:
                v = p.get(src)
                if v is not None and div:
                    v = round(v / div, 2)
                vals.append(v)
            dt.financials.append({"field":label,
                "latest":vals[0] if vals else None,
                "previous":vals[1] if len(vals)>1 else None,
                "two_periods_ago":vals[2] if len(vals)>2 else None})

    # 3. dividends ← raw['baostock_dividends'] + raw['buyback']
    if not dt.dividends:
        for d in (raw.get("baostock_dividends") or [])[:6]:
            if isinstance(d, dict):
                dt.dividends.append({
                    "event": f"{d.get('year','')}年分红 {d.get('dividCashStock','')}",
                    "date": str(d.get("dividOperateDate","") or d.get("dividRegistDate","")),
                    "amount": f"每股{d.get('dividCashPsBeforeTax','')}元" if d.get("dividCashPsBeforeTax") else "—"})
        bb = raw.get("buyback", {})
        if isinstance(bb, dict):
            for d in (bb.get("dividend_history") or [])[:4]:
                if isinstance(d, dict):
                    dt.dividends.append({"event":d.get("plan","分红"),
                        "date":str(d.get("date","")), "amount":d.get("amount","—")})

    # 4. business_breakdown ← raw['business_segments']
    if not dt.business_breakdown:
        bs = raw.get("business_segments", {})
        if isinstance(bs, dict):
            bl = bs.get("business_lines")
            if isinstance(bl, dict):
                # dict 格式: {'营业收入': 86940000000, '净利息收入': 55642000000, ...}
                for name, val in list(bl.items())[:8]:
                    if val is not None and str(val) != "nan":
                        dt.business_breakdown.append({
                            "name": name,
                            "value": round(float(val) / 1e8, 2) if isinstance(val, (int,float)) else val})
            elif isinstance(bl, list):
                for ln in bl[:8]:
                    if isinstance(ln, dict):
                        dt.business_breakdown.append({
                            "name": ln.get("name", ln.get("业务","—")),
                            "value": ln.get("revenue", ln.get("收入", ln.get("value")))})

    # 5. forecasts ← raw['institution_forecast']（聚合评级数据）
    if not dt.forecasts:
        inst = raw.get("institution_forecast", {})
        if isinstance(inst, dict):
            for it in (inst.get("data") or [])[:1]:
                if isinstance(it, dict):
                    org = it.get("RATING_ORG_NUM")
                    buy = it.get("RATING_BUY_NUM")
                    add = it.get("RATING_ADD_NUM")
                    tp_max = it.get("DEC_AIMPRICEMAX")
                    tp_min = it.get("DEC_AIMPRICEMIN")
                    eps1 = it.get("EPS1")
                    yr1 = it.get("YEAR1")
                    dt.forecasts.append({
                        "institution": f"{org or '—'}家机构覆盖",
                        "rating": f"买入{buy}/增持{add}" if buy or add else "—",
                        "target_price": f"{tp_min}~{tp_max}元" if tp_min and tp_max else None})
                    if eps1:
                        dt.forecasts.append({
                            "institution": f"EPS预测({yr1})",
                            "rating": f"{round(float(eps1),2)}元",
                            "target_price": None})

    # 6. northbound ← raw['capital_flow']
    if dt.northbound is None:
        cf = raw.get("capital_flow", {})
        if isinstance(cf, dict) and not cf.get("error"):
            dt.northbound = cf.get("data", cf)

    # 7. industry_specific ← raw['financial'] 银行指标
    if dt.industry_specific is None and fin_data:
        isp = {}
        for row in fin_data:
            npl = row.get("NPLRATIO") or row.get("BLPL")
            cov = row.get("PROVISION_COVERAGE") or row.get("BCBL")
            if npl is not None or cov is not None:
                if npl is not None: isp["npl_ratio"] = float(npl)
                if cov is not None: isp["coverage_ratio"] = float(cov)
                break
        if isp:
            from schemas import IndustrySpecific
            dt.industry_specific = IndustrySpecific(
                **{k:v for k,v in isp.items() if k in IndustrySpecific.model_fields})

    # 8. employee_holding ← raw['employee_holding_raw']
    if dt.employee_holding is None:
        eh = raw.get("employee_holding_raw", {})
        if isinstance(eh, dict) and eh.get("avg_price") is not None:
            from schemas import EmployeeHolding
            dt.employee_holding = EmployeeHolding(
                avg_price=eh.get("avg_price"), premium_discount=eh.get("premium_discount"))

    # 9. derived.fcf ← MGJYXJJE × 总股本
    d = dt.derived
    if d.fcf is None and fin_data:
        price, mv = mkt.get("latest_price"), mkt.get("total_market_cap_yi")
        if price and price > 0 and mv:
            mgj = fin_data[0].get("MGJYXJJE")
            if mgj is not None:
                d.fcf = round(float(mgj) * mv / price, 2)
                d.fcf_caliber = d.fcf_caliber or "首选"

    # 10. fcf_history ← raw['financial']['data']
    if not dt.fcf_history and fin_data:
        price, mv = mkt.get("latest_price"), mkt.get("total_market_cap_yi")
        if price and price > 0 and mv:
            from schemas import FcfYear
            for row in fin_data[:3]:
                yr = str(row.get("REPORT_DATE", row.get("QDATE","")))[:4]
                mgj = row.get("MGJYXJJE")
                if yr and mgj is not None:
                    dt.fcf_history.append(FcfYear(year=yr, ocf=round(float(mgj)*mv/price, 2)))


async def resolve_stock_query(query: str) -> tuple[str, str]:
    """用 LLM 解析股票名称/代码 → (6位代码, 股票名称)。

    若已是6位数字直接返回。否则调用 LLM 快速解析（模型从 config.LLM_MODEL 读取）。
    """
    trimmed = query.strip()
    if re.match(r"^\d{6}$", trimmed):
        return trimmed, ""

    from openai import AsyncOpenAI
    client = AsyncOpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{
                    "role": "user",
                    "content": (
                        f"用户输入了「{trimmed}」，请返回对应的A股6位股票代码和股票名称。"
                        "仅返回一个JSON对象，不要输出其他内容：{\"code\": \"601318\", \"name\": \"中国平安\"}"
                    ),
                }],
                temperature=0,
                max_tokens=300,
            ),
            timeout=15,
        )
        text = resp.choices[0].message.content or ""
        # 提取 JSON
        m = re.search(r"\{[^}]+\}", text)
        if not m:
            raise RuntimeError(f"LLM 返回无法解析: {text[:100]}")
        data = json.loads(m.group())
        code = str(data.get("code", "")).strip()
        name = str(data.get("name", "")).strip()
        if not re.match(r"^\d{6}$", code):
            raise RuntimeError(f"LLM 返回的代码无效: {code}")
        return code, name
    except asyncio.TimeoutError:
        raise RuntimeError(f"解析「{trimmed}」超时（10s），请直接输入6位股票代码")
    except Exception as e:
        raise RuntimeError(f"无法识别「{trimmed}」：{e}。请输入6位股票代码（如 601318）")


async def run_analysis(stock_code: str, force_refresh: bool = False) -> StockDecisionCard:
    """端到端分析：stock_code → StockDecisionCard。

    主流程：代码层 fetch_raw_data 取数（阶段1 L1，并发可靠）+ run_drilldown 下钻实取（阶段2）→
    数据注入 prompt → orchestrator（含手册全文 instructions）直接产出 StockDecisionCard。

    force_refresh: 清除该股票的 TTL 缓存 + 重置熔断器，强制重新取数。
    """
    if not has_api_key():
        raise RuntimeError("OPENAI_API_KEY 未设置：请在 backend/.env 配置（已支持 OpenAI 兼容端点）。")
    from decision_agents.data_fetcher import fetch_raw_data, _cache

    # 解析股票名称/代码（用户可能输入名称如"中国平安"）
    resolved_code, resolved_name = await resolve_stock_query(stock_code)

    if force_refresh:
        _cache.invalidate_code(resolved_code)

    raw = await asyncio.to_thread(fetch_raw_data, resolved_code)          # 阶段1（已并发）
    from decision_agents.drilldown import run_drilldown
    extra, trace_path = await run_drilldown(resolved_code, resolved_name, raw, force_refresh)
    raw = {**raw, "drilldown": extra}                                      # 合并供 backfill/report
    # raw_json 不内嵌 drilldown 重复份，下钻数据单份注入独立段（总量减半，缓解端点弱输出/超时）。
    raw_json = json.dumps({k: v for k, v in raw.items() if k != "drilldown"},
                          ensure_ascii=False, indent=2, default=str)
    name_hint = f"（{resolved_name}）" if resolved_name else ""
    prompt = (
        f"分析 A 股股票，代码：{resolved_code}{name_hint}。\n\n"
        "以下为 L1 代码层采集的真实数据（东方财富公开 API，以此为准，不得编造）：\n"
        f"{raw_json}\n\n"
        "\n下钻补充数据（drilldown，模型按需实取，可信级别同上）：\n"
        + json.dumps(extra, ensure_ascii=False, default=str)
        + "\n\n请严格按下方执行手册全文（L2/L3 全部规则）基于以上真实数据产出完整决策卡 StockDecisionCard，"
        "**必须填充所有 16 个结构化区块**：header、data_trace（含8大类原始数据+溯源摘要+行情+衍生指标+FCF）、"
        "hard_gate、reverse_check、five_signals、value_trap、quant_enhance、"
        "debate（五派评分+权重+综合得分+共识/分歧+胜方+防御状态）、"
        "factor_validation（五维因子有效性，UNA 标失效）、"
        "capital_profile（主力/机构/北向/融资 合力画像）、"
        "stress_test（基准/悲观情景估值+下行风险+5日VaR+极端波动声明）、"
        "behavior_check、final_decision、anchors、exit_conditions、risks。\n\n"
        "**data_trace 中 8 大类原始数据及衍生字段填充规则**:\n"
        "- financials: 从 financial.data(东财财务,含WEIGHTAVG_ROE/PARENT_NETPROFIT/TOTAL_OPERATE_INCOME/"
        "BASIC_EPS/BPS/MGJYXJJE)提取3年财务对比; MGJYXJJE=每股经营现金流,乘以总股本算OCF→fcf_history.ocf; "
        "PARENT_NETPROFIT=归母净利润; TOTAL_OPERATE_INCOME=营业总收入; 有数据时**必须填充**\n"
        "- business_breakdown: 从 business_segments 提取分业务收入占比\n"
        "- dividends: 从 baostock_dividends(近12月派息)和 buyback(巨潮分红历史)提取分红记录[{event,date,amount}]\n"
        "- forecasts: 从 institution_forecast 提取机构评级、目标价、评级分布\n"
        "- northbound: 从 capital_flow 提取北向资金明细(持有/变动/占比/Q1持仓/流向)\n"
        "- 上述字段若 drilldown 数据中无对应内容，一律填 None（UNA），禁止用记忆估算\n"
        "- source_summary 计数: 从 raw 数据统计 main_source_success/backup_source_downgrade/una_count,不得留 None\n\n"
        "**模型四（银行/保险）硬门槛判定规则**：模型四要求 PE≤10(辅助) + 不良率<1.8% + 拨备覆盖率>160% + NIM 企稳/上行"
        "**同时满足**。若不良率/拨备/NIM 任一 UNA 且 PE≤10 → passed=False + 置信度降一级（高→中），"
        "reason 标注\"不良率/拨备/NIM UNA 无法完全验证\"。不能仅因 PE≤10 就判 passed=True。\n"
        "银行/保险的不良率/拨备/NIM 已在 drilldown.bank_quality 提供（实取），直接引用，勿用记忆值。\n\n"
        "数据 UNA 的字段填 None/空，不编造；区块本身必须产出。"
        "\n\n"
        "【输出格式示例（银行股，仅供格式参考，数值必须来自上方真实数据）】\n"
        "```json\n"
        "{\n"
        '  "hard_gate": {"passed": true, "applied_model": "模型四", '
        '"key_values": {"pe": 6.36, "pb": 0.87, "dividend_yield": 5.31, "roe_latest": 13.44, '
        '"npl_ratio": 0.94, "coverage_ratio": 394.0}, '
        '"quality_regulator_triggered": false, "alt_pool_triggered": false, '
        '"reason": "模型四通过：PE=6.36≤10, 不良率0.94%<1.8%, 拨备394%>160%, NIM企稳"},\n'
        '  "reverse_check": {"passed": true, "confidence_downgrades": 0, '
        '"items": [{"name": "大股东质押", "triggered": false, "detail": "未发现显著质押"}, '
        '{"name": "ESG负面", "triggered": false, "detail": "未发现"}, '
        '{"name": "审计意见", "triggered": false, "detail": "标准无保留"}, '
        '{"name": "近两年累计净利润", "triggered": false, "detail": "约2986亿(正)"}, '
        '{"name": "分红率", "triggered": false, "detail": "约35%(<100%)"}]},\n'
        '  "five_signals": {"trend": "DOWN", "flow": "NEG", "value": "LOW", "quality": "OK", '
        '"sentiment_score": 45.0, "details": "趋势：今日跌2.36%...; 资金：北向减持...; '
        '估值：PE6.36处历史低位...; 质量：ROE13.44%,不良0.94%..."},\n'
        '  "value_trap": {"triggered": true, "position_cap": 5.0, '
        '"signals": [{"name": "ROE连续三年下降", "triggered": true, "position_cap": 5.0}, '
        '{"name": "扣非净利润连续下降", "triggered": false, "position_cap": null}, '
        '{"name": "经营现金流连续下降", "triggered": false, "position_cap": null}, '
        '{"name": "毛利率连续下降", "triggered": true, "position_cap": null}]},\n'
        '  "quant_enhance": {"dcf_executed": false, "dcf_value": null, '
        '"note": "银行不适用DCF，PB-ROE法：合理PB 0.9-1.1倍，对应40.4-49.4元"},\n'
        '  "debate": {"factions": [{"name": "价值派", "score": 8.5, "weight": 0.28, "stance": "多"}, '
        '{"name": "成长派", "score": 5.0, "weight": 0.25, "stance": "中"}, '
        '{"name": "质量派", "score": 9.0, "weight": 0.22, "stance": "多"}, '
        '{"name": "资金派", "score": -6.0, "weight": 0.15, "stance": "空"}, '
        '{"name": "情绪派", "score": -2.0, "weight": 0.10, "stance": "空"}], '
        '"composite_score": 67.7, "consensus": "分歧", "winner": "多头", '
        '"defense_mode": false, "summary": "价值+质量看多，资金+情绪看空..."},\n'
        '  "factor_validation": {"trend_valid": true, "flow_valid": true, "value_valid": true, '
        '"quality_valid": true, "sentiment_valid": false, '
        '"invalid_factors": ["sentiment"], "note": "样本不足n=1/50，权重维持默认"},\n'
        '  "capital_profile": {"main_force": "净流出", "institution": "净流出", '
        '"northbound": "净流出", "margin": "融资余额连续7日下降", '
        '"summary": "四路资金合力流出，机构大面积撤退(-58%)是最强负向信号"},\n'
        '  "stress_test": {"base_valuation": 42.5, "pessimistic_valuation": 31.0, '
        '"downside_risk": 18.4, "var_5d": 6.6, "dcf_valuation_range": "40.4-49.4元(PB-ROE)", '
        '"scenario": "NIM收窄20bp+零售信贷低迷+资金持续流出", '
        '"extreme_volatility": false, "note": "悲观净利1310亿，合理区间31-42元"},\n'
        '  "behavior_check": {"has_warning": false, '
        '"triggered_items": []},\n'
        '  "final_decision": {"state": "ValueReversion", "action": "分批建仓，宜慢不宜快", '
        '"position_cap": 45.0, "confidence": "高", '
        '"stop_profit_check": "PE<12, PB<2.0, 涨幅<50% → 均未触发"},\n'
        '  "anchors": {"entry_anchors": [{"level": "第一档(轻仓)", "price": 37.5, "position": 15.0}, '
        '{"level": "第二档(加仓)", "price": 35.0, "position": 20.0}, '
        '{"level": "第三档(加仓)", "price": 33.0, "position": 10.0}], '
        '"stop_loss_line": 31.0, "pessimistic_correction_anchor": 31.0},\n'
        '  "exit_conditions": ["不良率连续两个季度上升", "净利润同比下滑超10%", '
        '"ROE连续两年降至10%以下", "净息差连续四个季度下降"],\n'
        '  "risks": ["四路资金合力流出，机构大面积撤退", "ROE连续三年下降(16.22→14.49→13.44)", '
        '"净息差持续收窄", "宏观经济下行致资产质量恶化", "房地产敞口及地方债务风险"]\n'
        "}\n"
        "```\n"
        "**严格按照上方格式输出每个区块，所有字段都必须填写（UNA 填 null），不得省略任何区块。**"
    )
    # 主路径：output_type=StockDecisionCard（LLM 通过 response_format 产出 JSON）
    # 如 Hundsun 端点不支持 json_schema，降级 output_type=None + 容错解析。
    # 流式调用：非流式大请求会被网关积压直至挂死，SSE 流式有持续字节流可完整回包；
    # 消费全部事件后取 final_output，
    # 对下游仍是"整卡返回"，前端逐区块 SSE 推送节奏不变（阶段3）。
    try:
        result = Runner.run_streamed(orchestrator, prompt)
        async for _ in result.stream_events():
            pass
        card = result.final_output
    except Exception:
        # 降级：移除 output_type，加显式 JSON 指令（同样走流式）
        orc_fallback = dataclasses.replace(orchestrator, output_type=None)
        prompt_json = (
            prompt
            + "\n\n【关键输出格式】你必须且仅输出一个纯JSON对象，以{开头，以}结尾。"
            "整个回复必须是合法JSON，不得包含markdown代码块、解释文字、或额外输出。"
            "可直接被json.loads()解析。"
        )
        result = Runner.run_streamed(orc_fallback, prompt_json)
        async for _ in result.stream_events():
            pass
        raw_output = str(result.final_output)
        card = _extract_json_with_tolerance(raw_output, StockDecisionCard)

    # ── 后处理：代码层直接从 raw 填充 data_trace（不依赖 LLM）──
    _backfill_data_trace(card, raw)

    # ── 后处理：从 raw 数据填充 source_summary 计数 + fcf_history.ocf（MGJYXJJE 计算）──
    try:
        dt = card.data_trace
        # source_summary 计数
        if dt.source_summary.main_source_success is None or \
           dt.source_summary.backup_source_downgrade is None or \
           dt.source_summary.una_count is None:
            m, b, u = 0, 0, 0
            for k in ("market", "financial", "capital_flow", "baostock_financial",
                      "baostock_dividends", "business_segments", "institution_forecast",
                      "buyback", "employee_holding_raw", "reverse_check_raw"):
                v = raw.get(k)
                if v is None or v == {}:
                    u += 1
                elif isinstance(v, dict):
                    src = v.get("source", "")
                    if v.get("error") or "failed" in src:
                        b += 1
                    elif "UNA" in str(v.get("note", "")):
                        u += 1
                    elif v.get("data") or v.get("dividends") or v.get("business_lines") or v.get("dividend_history"):
                        m += 1
                    else:
                        u += 1
                elif isinstance(v, list):
                    m += 1 if v else u  # 非空列表算成功
                else:
                    u += 1
            if dt.source_summary.main_source_success is None:
                dt.source_summary.main_source_success = m
            if dt.source_summary.backup_source_downgrade is None:
                dt.source_summary.backup_source_downgrade = b
            if dt.source_summary.una_count is None:
                dt.source_summary.una_count = u

        # fcf_history.ocf：从 MGJYXJJE（每股经营现金流）× 总股本 计算
        if dt.fcf_history and raw.get("financial", {}).get("data"):
            mkt = raw.get("market", {})
            price = mkt.get("latest_price")
            mv = mkt.get("total_market_cap_yi")  # 总市值(亿元)
            if price and price > 0 and mv:
                shares_yi = mv / price  # 总股本(亿股)
                for row in raw["financial"]["data"]:
                    try:
                        mgj = row.get("MGJYXJJE")
                        if mgj is None:
                            continue
                        ocf = float(mgj) * shares_yi
                        yr = str(row.get("REPORT_DATE", ""))[:4]
                        for fcf in dt.fcf_history:
                            if fcf.year == yr and fcf.ocf is None:
                                fcf.ocf = round(ocf, 2)
                                break
                    except (ValueError, TypeError):
                        continue
    except Exception:
        pass

    # 生成并保存分析报告到 analysis_reports/（三层结构对齐「标的分析结果参考」）
    # 校验→修复（≤2轮）→骨架兜底，章节永不为空
    try:
        from report_generator import generate_and_save_report
        await generate_and_save_report(raw, card, stock_code, resolved_name)
    except Exception as e:
        print(f"⚠️ 保存分析报告失败: {e}")

    return card
