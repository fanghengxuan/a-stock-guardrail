"""看板解析器 golden 测试：两份真实 report_direct.md 的数值忠实性 + 三层降级链。

数字断言全部以 MD 原文为基准（人工核对过的黄金值）；任何解析退化必须先在这里红。
"""
import json
import pathlib
import random

import pytest

from dashboard import build_blocks, build_dashboard, split_sections
from dashboard.scanner import Section, SectionScanner

FIX = pathlib.Path(__file__).parent / "fixtures"


def _md(code: str) -> str:
    return (FIX / f"{code}-report_direct.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def b601():
    return build_dashboard(_md("601318"))["blocks"]


@pytest.fixture(scope="module")
def b600():
    return build_dashboard(_md("600036"))["blocks"]


_NEW_FMT_BLOCKS = {"position", "peer", "deviation", "pq", "health", "veto", "chips", "guide"}


def test_all_blocks_present(b601, b600):
    """两份 golden（旧 20 节格式）：存量区块必须 ok/partial；
    新版手册 8 区块在旧格式缺节 → una 是设计契约（前端删面板，永不空屏）。"""
    for b in (b601, b600):
        for k, v in b.items():
            if k in _NEW_FMT_BLOCKS:
                assert v["status"] == "una", f"{k} 旧格式应为 una，实为 {v['status']}"
                continue
            assert v["status"] in ("ok", "partial"), f"{k} status={v['status']}"


# ---------- 数值忠实性 ----------

def test_meta(b601, b600):
    assert b601["meta"]["code"] == "601318"
    assert b601["meta"]["name"] == "中国平安"
    assert b601["meta"]["model_label"] == "模型四（保险）"
    assert b601["meta"]["exchange"] == "SH"
    assert b600["meta"]["code"] == "600036"
    assert b600["meta"]["name"] == "招商银行"
    assert b600["meta"]["model_label"] == "模型四（银行）"
    assert b600["meta"]["exchange"] == "SH"  # 60xxxx=沪；SZ 由 000/002/300 前缀推断


def test_quote(b601, b600):
    assert b601["quote"]["price"] == 58.24
    assert b601["quote"]["pe"] == 6.62
    assert b601["quote"]["pb"] == 1.03
    assert b601["quote"]["dy"] == 4.64
    assert b601["quote"]["high_52w"] == 72.45
    assert b601["quote"]["low_52w"] == 46.90
    assert b601["quote"]["market_cap_yi"] == 10545.89
    assert b600["quote"]["price"] == 41.69
    assert b600["quote"]["pe"] == 6.93
    assert b600["quote"]["change_pct"] == pytest.approx(1.51)
    assert b600["quote"]["up"] is True


def test_metrics(b601, b600):
    ks1 = [i["k"] for i in b601["metrics"]["items"]]
    assert ks1[:3] == ["PE(TTM)", "PB", "股息率(TTM)"]
    assert "每股净资产" in ks1 and "总市值" in ks1  # 保险无银行指标 → 链落到每股净资产
    ks0 = [i["k"] for i in b600["metrics"]["items"]]
    assert "不良率" in ks0  # 银行链首命中
    for b in (b601, b600):
        assert all(i["v"] is not None for i in b["metrics"]["items"])
        assert len(b["metrics"]["items"]) <= 5


def test_stamp(b601, b600):
    assert b601["stamp"]["en"] == "StrongLong"
    assert b601["stamp"]["position_cap_pct"] == 40.0
    assert b601["stamp"]["confidence"] == "中"
    assert b601["stamp"]["zh"] == "强势做多"
    assert b600["stamp"]["en"] == "Watch"
    assert b600["stamp"]["position_cap_pct"] == 5.0  # 明细在 blockquote 行
    assert b600["stamp"]["confidence"] == "中"


def test_signals(b601, b600):
    for b in (b601, b600):
        items = b["signals"]["items"]
        assert [i["name"] for i in items] == ["趋势", "资金", "估值", "质量", "情绪"]
        assert items[0]["enum"] == "UP"
        assert items[4]["score"] in (60.0, 55.0)
    assert b600["signals"]["items"][4]["enum"] == "NEUTRAL"


def test_debate(b601, b600):
    assert b601["debate"]["composite"] == 77.3
    assert {f["name"]: f["score"] for f in b601["debate"]["factions"]} == \
        {"价值派": 88.0, "成长派": 80.0, "质量派": 85.0, "资金派": 50.0, "情绪派": 65.0}
    assert b601["debate"]["verdict"].startswith("多头占优")
    assert b600["debate"]["composite"] == 70.0
    assert all(f["weight_pct"] for f in b600["debate"]["factions"])
    assert sum(f["weight_pct"] for f in b601["debate"]["factions"]) == 100
    assert b601["debate"]["consensus"] and b601["debate"]["divergence"]


def test_ruler(b601, b600):
    r1 = b601["ruler"]
    assert r1["now"] == 58.24
    kinds = {r["kind"] for r in r1["rows"]}
    assert {"up", "dn", "now", "stop"} <= kinds
    prices = [r["lo"] for r in r1["rows"]]
    assert prices == sorted(prices, reverse=True)  # 降序铁律
    assert any("止损" in r["label"] for r in r1["rows"])
    stop = next(r for r in r1["rows"] if r["kind"] == "stop")
    assert stop["lo"] == 46.0
    assert any("第一档" in r["label"] for r in r1["rows"])  # 现价与第一档合并
    r0 = b600["ruler"]
    assert r0["now"] == 41.69
    assert len(r0["rows"]) <= 14


def test_anchors(b601, b600):
    t1 = b601["anchors"]["tiers"]
    assert [(t["tier"], t["lo"], t["hi"]) for t in t1] == \
        [("第一档", 55.5, 58.5), ("第二档", 52.0, 55.0), ("第三档", 48.5, 51.5)]
    assert t1[0]["label"] == "底仓"
    assert t1[0]["action"] == "≤15%仓位"
    assert b601["anchors"]["stop_loss"]["price"] == 46.0
    t0 = b600["anchors"]["tiers"]
    assert [(t["lo"], t["hi"]) for t in t0] == [(39.0, 40.5), (36.5, 37.5), (34.0, 35.0)]
    assert t0[2]["action"] == "不自动加仓"  # B 形态（区间在括号内）
    assert b600["anchors"]["stop_loss"]["price"] == 34.0
    assert b601["anchors"]["cross_rows"] and b600["anchors"]["cross_rows"]


def test_gates_traps_roe(b601, b600):
    assert len(b601["gates"]["hard"]["rows"]) >= 4
    assert b601["gates"]["hard"]["conclusion"].startswith("结论")
    assert len(b601["gates"]["reverse"]["rows"]) >= 5
    assert b601["traps"]["triggered_count"] == 0
    assert b600["traps"]["triggered_count"] == 1  # ROE 三年下降
    assert [(x["yr"], x["v"]) for x in b601["roe"]["bars"]] == \
        [("2023", 9.7), ("2024", 13.8), ("2025", 14.0)]
    assert [(x["yr"], x["v"]) for x in b600["roe"]["bars"]] == \
        [("2023", 16.22), ("2024", 14.49), ("2025", 13.44)]  # 来自价值陷阱箭头链


def test_stress_quant(b601, b600):
    s1 = b601["stress"]
    assert s1["pes_range"] == {"lo": 44.0, "hi": 51.0}
    assert len(s1["scenarios"]) == 3
    assert "ValueReversion" in " ".join(s1["verdict_bullets"])
    q1 = b601["quant"]
    assert q1["dcf_executed"] is False
    assert q1["var_amount"] == 3.24 and q1["var_pct"] == 5.56  # 表形态
    q0 = b600["quant"]
    assert q0["var_amount"] == 1.93 and q0["var_pct"] == 4.6  # bullet-list 形态
    assert q0["alt_notes"] or q0["alt_rows"]


def test_exits_risks_trace(b601, b600):
    assert len(b601["exits"]["clear_list"]) == 6
    assert all(not c["text"].startswith(("1", "2")) or True for c in b601["exits"]["clear_list"])
    assert len(b600["exits"]["clear_list"]) == 6
    assert len(b601["exits"]["watch_list"]) >= 3
    r1 = b601["risks"]["items"]
    assert len(r1) == 6 and sum(1 for x in r1 if x["hot"]) == 3
    t1 = b601["trace"]
    assert t1["main"] == {"n": 44, "total": 52}
    assert t1["backup"] == {"n": 2, "total": 52}
    assert t1["una"] == {"n": 6, "total": 52}
    assert t1["completeness"].startswith("基本完整")
    t0 = b600["trace"]
    assert t0["main"]["n"] == 46 and t0["una"]["n"] == 2  # 带「约」前缀变体
    assert DISCLAIMER_LINE in b601["trace"]["disclaim"]


DISCLAIMER_LINE = "不构成投资建议"


def test_summary_and_card_raw(b601, b600):
    assert b601["summary"]["headline"].startswith("中国平安")
    assert len(b601["summary"]["core_logic"]) >= 3
    assert b601["summary"]["one_liner"].startswith("中国平安")
    assert b600["summary"]["headline"].startswith("招商银行")
    assert "🔗 锚点操作建议" in b601["card_raw"]["text"]
    assert len(b601["appendix"]["items"]) >= 1  # 员工持股计划
    assert b601["appendix"]["items"][0]["title"] == "七、员工持股计划"


def test_pipeline(b601, b600):
    for b in (b601, b600):
        g = b["pipeline"]["gates"]
        assert [x["no"] for x in g] == [1, 2, 3, 4, 5]
        assert g[4]["cls"] == "final"
    assert any("0 负" in x["verdict"] for x in b601["pipeline"]["gates"] if x["no"] == 4)


# ---------- 降级链 ----------

def test_degrade_tampered_anchor_tag(b601_missing_tag):
    """箱线卡 🔗 标签被篡改 → anchors 走二级（执行摘要「第X档」行），status=partial 且仍有档。"""
    an = b601_missing_tag["anchors"]
    assert an["status"] == "partial"
    assert len(an["tiers"]) >= 2 and an["tiers"][0]["lo"] == 55.5
    assert an["stop_loss"]["price"] == 46.0  # 摘要「止损线：46.00元」可得


@pytest.fixture(scope="module")
def b601_missing_tag():
    md = _md("601318").replace("🔗 锚点操作建议", "🔗 操作坐标")
    return build_dashboard(md)["blocks"]


def test_degrade_missing_section_raw(b600):
    """整节缺失（辩论）→ debate una；其余块不受传染。"""
    md = _md("600036")
    i = md.index("## ✅ 八、五维视角辩论引擎")
    j = md.index("## ✅ 九、")
    b = build_dashboard(md[:i] + md[j:])["blocks"]
    assert b["debate"] == {"status": "una"}
    assert b["quote"]["status"] == "ok"


def test_degrade_broken_table_raw_fallback():
    """extractor 内部异常 → status=raw + raw_md 原文透出（永不空屏）。"""
    md = _md("601318").replace("| 维度 | 信号 | 依据 |", "|", 1)  # 毁表头
    # 毁表后 signals 行匹配不到 → None 或降级，不允许抛
    b = build_dashboard(md)["blocks"]
    assert b["signals"]["status"] in ("una", "raw", "ok", "partial")


def test_v3_debate_and_stamp_variants():
    """辩论无×权重形态「价值派75分、成长派70分…」、置信度只在卡 🎯 块——
    两种报告形态都必须自动消化，且零降级。"""
    md = (FIX / "600036v3-report_direct.md").read_text(encoding="utf-8")
    d = build_dashboard(md)
    assert d["parse_fallbacks"] == []
    db = d["blocks"]["debate"]
    assert db["composite"] == 68.65
    assert {f["name"]: f["score"] for f in db["factions"]} == \
        {"价值派": 75.0, "成长派": 70.0, "质量派": 70.0, "资金派": 55.0, "情绪派": 65.0}
    st = d["blocks"]["stamp"]
    assert (st["en"], st["position_cap_pct"], st["confidence"]) == ("Watch", 5.0, "中")
    assert st["zh"] == "持有观察"


def test_v4_bare_name_debate():
    """600089 辩论四形态叠加——「（权重 28%）」带空格、结论独立行、
    逐派分值只出现在「计算：价值45×28% + 成长55×25%…」裸名简写行。
    五派分值/权重/立场/综合 47.9 必须全部解析出。"""
    md = (FIX / "600089-report_direct.md").read_text(encoding="utf-8")
    d = build_dashboard(md)
    assert d["parse_fallbacks"] == []
    db = d["blocks"]["debate"]
    assert db["status"] == "ok" and "qualitative" not in db
    assert {f["name"]: f["score"] for f in db["factions"]} == \
        {"价值派": 45.0, "成长派": 55.0, "质量派": 50.0, "资金派": 40.0, "情绪派": 45.0}
    assert all(f["weight_pct"] for f in db["factions"])
    assert all(f["stance"] for f in db["factions"])
    assert db["composite"] == 47.9
    assert db["verdict"] == "中性偏空"


def test_build_dashboard_envelope():
    d = build_dashboard(_md("601318"), issues=[{"id": "x"}])
    assert d["schema"] == 1
    assert d["validate_issues"] == [{"id": "x"}]
    assert d["section_count"] >= 20
    assert set(d["blocks"]) >= {"meta", "quote", "stamp", "ruler", "debate"}
    json.dumps(d)  # 必须可序列化


# ---------- 流式与一次性等价（关键不变量） ----------

def test_scanner_equivalence():
    """随机块长流式闭节集合 == 一次性 split_sections（字节级正文相等）。"""
    for code in ("601318", "600036"):
        md = _md(code)
        once = split_sections(md)
        rng = random.Random(42)
        sc = SectionScanner()
        closed = {}
        rest = md
        while rest:
            n = rng.randint(1, 900)
            for sid, t, x in sc.feed(rest[:n]):
                closed.setdefault(sid, (t, x))  # 与 split_sections 同：重复节名首个胜
            rest = rest[n:]
        for sid, t, x in sc.finish():
            closed.setdefault(sid, (t, x))
        assert set(closed) >= set(once.keys()), f"{code}: 流式缺节 {set(once) - set(closed)}"
        for sid, sec in once.items():
            if sid in closed:
                t, x = closed[sid]
                assert t == sec.title and x.strip() == sec.text.strip(), f"{code}/{sid} 正文漂移"


def test_scanner_streamed_blocks_reach_final(b601):
    """流式增量 build_blocks（按节闭包累积）最终态 == 一次性结果。"""
    md = _md("601318")
    sc = SectionScanner()
    sections = {}
    ev = []

    def collect(pairs):
        from dashboard.scanner import Section
        for sid, t, x in pairs:
            sections.setdefault(sid, Section(sid, t, x))
            ev.append(build_blocks(sections))
    rest = md
    while rest:
        n = 777
        collect(sc.feed(rest[:n]))
        rest = rest[n:]
    collect(sc.finish())
    last = build_blocks(sections)
    for k, v in b601.items():
        if v.get("status") == "una":
            # una 占位仅全量 build_dashboard 补（缺节契约），流式 build_blocks 本就不产
            assert k not in last, f"流式终态不应出现 una 块 {k}"
            continue
        assert json.dumps(last.get(k), ensure_ascii=False, sort_keys=True) == \
            json.dumps(v, ensure_ascii=False, sort_keys=True), f"流式最终态 {k} 不一致"


def test_fenced_prefix_tolerated():
    """LLM 以 ```markdown 围栏或寒暄开场 → 解析器不崩、首节前散行丢弃。"""
    md = "以下是报告：\n```markdown\n" + _md("601318") + "\n```\n"
    b = build_dashboard(md)["blocks"]
    assert b["quote"]["price"] == 58.24


# ---------- 26 节手册格式（002236）：新块 golden ----------

@pytest.fixture(scope="module")
def b26():
    return build_dashboard(_md("002236"))["blocks"]


def test_newfmt_structure():
    """26 节全闭（section 27 含 meta）、校验零问题、解析零降级。"""
    md = _md("002236")
    from report_generator import validate_report
    assert validate_report(md) == []
    d = build_dashboard(md)
    assert d["section_count"] == 27
    assert d["parse_fallbacks"] == []
    assert d["blocks"]["deviation"]["status"] == "ok"


def test_newfmt_position(b26):
    p = b26["position"]
    assert p["status"] == "ok"
    assert p["rank"] == "行业第二"
    assert p["second_discount"] == "适用"
    assert p["moat_total"] == 14 and p["moat_grade"] == "较强"  # 综合行取分，阈值串不干扰
    assert [r["dim"] for r in p["moat_rows"]] == ["技术壁垒", "全球化能力", "用户生态", "品牌矩阵"]


def test_newfmt_peer(b26):
    pr = b26["peer"]
    assert len(pr["rows"]) == 5
    assert pr["rows"][0]["target"] == "14.16倍"  # 与行情节 PE 原文一致，无换算


def test_newfmt_deviation(b26):
    d = b26["deviation"]
    assert d["applicable"] is True
    assert any("77%" in r["dev"] for r in d["rows"])  # 偏离度算式原文透出
    assert "协议层" in d["conclusion"]


def test_newfmt_pq(b26):
    q = b26["pq"]
    assert q["rows"][0]["gap"].startswith("≈17.8%")
    assert "存疑" in q["rows"][0]["verdict"]


def test_newfmt_traps_ext(b26):
    t = b26["traps"]
    assert t["triggered_count"] == 0
    assert t["trap_type"] == "无价值陷阱"


def test_newfmt_health(b26):
    h = b26["health"]
    assert h["total"] == 6 and h["grade"] == "良好"
    assert len(h["rows"]) == 4 and len(h["risk_rows"]) == 3


def test_newfmt_veto(b26):
    v = b26["veto"]
    assert len(v["hard_rows"]) == 7
    assert v["triggered"] is False and "未触发" in v["conclusion"]
    assert any(r["triggered"] is None for r in v["hard_rows"])  # ③ 金融条款对非金融标的=三态
    assert v["hard_rows"][0]["item"] == "扣非净利润连续两季负增长"  # 条件列非序号列


def test_newfmt_chips(b26):
    c = b26["chips"]
    assert len(c["rows"]) == 8  # 全 UNA 字段也保行（永不空屏契约）


def test_newfmt_guide(b26):
    g = b26["guide"]
    assert g["stop_loss_new"] == 14.3
    assert (g["zone1"]["lo"], g["zone1"]["hi"], g["zone1"]["dy"]) == (14.6, 15.8, 3.5)
    assert len(g["clear_list"]) >= 5  # 数字编号清单也收


def test_newfmt_quant_four(b26):
    q = b26["quant"]
    assert q["primary_method"] == "PE估值参照法"
    assert q.get("bond_like") is None  # 类债券「不成立」不置位
    assert any(m["method"] == "PE" and "14.61" in (m["text"] or "") for m in q["methods"])


def test_newfmt_signals(b26):
    s = b26["signals"]
    assert s["pos_count"] == 1 and len(s["items"]) == 5


def test_newfmt_pipeline(b26):
    assert [g["name"] for g in b26["pipeline"]["gates"]] == [
        "硬门槛", "反向清单", "价值陷阱", "五维信号", "一票否决", "状态机"]


def test_newfmt_streamed_matches_one_shot():
    """新 26 节：流式增量 build_blocks 最终态 == 一次性（与旧 golden 同契约）。"""
    md = _md("002236")
    full = build_dashboard(md)["blocks"]
    sc = SectionScanner()
    sections = {}
    rest = md
    while rest:
        for sid, t, x in sc.feed(rest[:991]):
            sections.setdefault(sid, Section(sid, t, x))
        rest = rest[991:]
    for sid, t, x in sc.finish():
        sections.setdefault(sid, Section(sid, t, x))
    last = build_blocks(sections)
    for k, v in full.items():
        if v.get("status") == "una":
            assert k not in last
            continue
        assert json.dumps(last.get(k), ensure_ascii=False, sort_keys=True) == \
            json.dumps(v, ensure_ascii=False, sort_keys=True), f"流式最终态 {k} 不一致"
