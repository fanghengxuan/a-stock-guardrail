import json, pathlib
from decision_agents.drilldown_tools import (
    parse_margin_rows, parse_bank_quality, parse_news_items,
    compute_52w_highlow, median,
    parse_announcements, filter_negative_announcements, parse_research_reports,
)
FX = pathlib.Path(__file__).parent / "fixtures"

def _load(name): return json.loads((FX / name).read_text())

def test_parse_margin_rows_desc_and_windows():
    d = parse_margin_rows(_load("margin.json"), "600036")
    assert d["data"][0]["date"] >= d["data"][1]["date"]      # 降序
    assert d["windows"]["d5"] is not None                      # 5日窗口余额
    assert all(r["rzye"] > 0 for r in d["data"][:5])

def test_parse_bank_quality_hits_cmb_fields():
    d = parse_bank_quality(_load("bank_quality.json"))
    assert 0 < d["npl_ratio"] < 5          # 招行 ~0.94
    assert d["coverage_ratio"] > 100       # ~385-394
    assert 0 < d["nim"] < 5                # ~1.83

def test_parse_news_items_recent_only():
    items = parse_news_items(_load("news.json"), days=7, limit=10)
    assert len(items) <= 10
    assert all({"title", "time", "source"} <= set(i) for i in items)

def test_compute_52w_highlow():
    hi, lo = compute_52w_highlow(_load("kline.json"))
    assert lo < hi

def test_median_odd_even():
    assert median([1, 3, 2]) == 2
    assert median([1, 2, 3, 4]) == 2.5

def test_announcements_parsed():
    items = parse_announcements(_load("announcements.json"))
    assert items and all({"title", "date", "type_code"} <= set(i) for i in items)

def test_negative_filter_hits_only_negative_types():
    # x 中性不命中；y 命中"处罚"分支；第3/4条命中动词前置倒装分支（变更…审计/变更…事务所）；
    # 续聘类中性公告带"事务所"但无"变更"，不得误伤
    items = [{"title": "x", "date": "2026-01-01", "type_code": "001002009", "type_name": "董事会决议"},
             {"title": "y", "date": "2026-01-02", "type_code": "001005001", "type_name": "监管处罚"},
             {"title": "关于变更审计机构的公告", "date": "2026-01-03", "type_code": "", "type_name": ""},
             {"title": "变更会计师事务所及审计机构", "date": "2026-01-04", "type_code": "", "type_name": ""},
             {"title": "关于续聘会计师事务所的公告", "date": "2026-01-05", "type_code": "", "type_name": ""}]
    neg = filter_negative_announcements(items)
    assert [i["title"] for i in neg] == ["y", "关于变更审计机构的公告", "变更会计师事务所及审计机构"]

def test_research_reports_parsed():
    items = parse_research_reports(_load("research_reports.json"))
    assert items and all({"org", "rating", "date"} <= set(i) for i in items)

def test_negative_filter_audit_change_word_order():
    # 名词式"审计机构变更"须命中（§6 原分支回归）；不带"变更"前缀的事务所中性公告不误伤
    items = [{"title": "审计机构变更", "date": "2026-01-01", "type_code": "", "type_name": ""},
             {"title": "更换会计师事务所公告", "date": "2026-01-02", "type_code": "", "type_name": ""},
             {"title": "董事会决议公告", "date": "2026-01-03", "type_code": "", "type_name": "董事会决议公告"}]
    neg = filter_negative_announcements(items)
    assert [i["title"] for i in neg] == ["审计机构变更"]
