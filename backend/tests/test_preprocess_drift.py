# backend/tests/test_preprocess_drift.py
"""_preprocess_json 容错扩展的判别测试——样本取自真实端点 fallback 路径 8 项
校验错的原始 JSON 形态。回归守卫：异常形态被正确修复且语义信息不丢。"""
from orchestrator import _preprocess_json
from schemas import StockDecisionCard

REAL_DRIFT = {
    "header": {"stock_name": "招商银行", "stock_code": "600036", "analysis_date": "2026-09-05"},
    "data_trace": {"fcf_history": {"note": "银行不适用DCF", "reason": "不适用（银行）"}},
    "hard_gate": {"applied_model": "模型四（银行/保险）"},
    "five_signals": {"flow": "NEUTRAL", "details": {"trend_detail": "日线走弱", "note": "情绪不参与加权"}},
    "capital_profile": {
        "main_force": "UNA（EastmoneyFlow连败熔断，板块口径非个股口径）",
        "institution": "评级正面（23家机构覆盖），资金净流向无数据",
        "northbound": "数据失效（AKShare北向2026-09-05，不采信）",
    },
}


def test_real_drift_repairs_to_valid_card():
    card = StockDecisionCard.model_validate(_preprocess_json(REAL_DRIFT))
    assert card.data_trace.fcf_history == []          # 说明性 dict→空列表，不造全 None 假条目
    assert card.hard_gate.applied_model == "模型四"    # 括号说明剥离
    assert card.five_signals.flow == "NEU"            # 别名收敛
    assert isinstance(card.five_signals.details, str)  # dict 值转字符串
    assert card.capital_profile.main_force == "平衡"
    assert card.capital_profile.institution == "平衡"
    assert card.capital_profile.northbound == "UNA"   # 有 UNA 合法值的字段优先用之


def test_flow_keyword_and_summary_preservation():
    d = _preprocess_json({"capital_profile": {"main_force": "近5日主力净流出明显（融资同向）"}})
    cp = d["capital_profile"]
    assert cp["main_force"] == "净流出"               # 关键词方向可救→按方向收敛，不吞默认值
    assert "summary" not in cp or "近5日" not in cp["summary"]  # 成功匹配合法值时不追加转述
    d2 = _preprocess_json({"capital_profile": {"institution": "无口径数据"}})
    assert d2["capital_profile"]["institution"] == "平衡"
    assert "无口径数据" in d2["capital_profile"]["summary"]     # 默认兜底时原文进 summary 保信息
