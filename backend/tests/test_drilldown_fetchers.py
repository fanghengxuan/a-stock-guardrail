"""下钻 fetcher 行为测试（不碰网络）：无数据→UNA 不触熔断/重试；成分股翻页取全。"""
import pandas as pd
from unittest.mock import patch

import decision_agents.drilldown_tools as dt


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_margin_no_rows_is_una_not_failure():
    """非两融标的：响应成功但行集空 → status=UNA、无 error 键、不重试（em_get 仅 1 次）。"""
    calls = []

    def fake(url, **kw):
        calls.append(url)
        return _Resp({"success": True, "result": {"data": []}})

    with patch.object(dt, "em_get", fake):
        r = dt.MarginFetcher().fetch("688001")
    assert r["status"] == "UNA" and "error" not in r
    assert r["data"] == [] and r["windows"]["d5"] is None
    assert len(calls) == 1  # 无指数退避重试 = 不给共享熔断器记 failure


def test_bank_quality_missing_field_is_una_not_failure():
    """非银行标的（无 NONPERLOAN 字段）→ status=UNA、无 error 键。"""
    df = pd.DataFrame([{"REPORT_DATE": "2026-06-30"}])  # 有响应、无不良率列
    with patch("akshare.stock_financial_analysis_indicator_em", return_value=df):
        r = dt.BankQualityFetcher().fetch("600588")
    assert r["status"] == "UNA" and "error" not in r
    assert r["npl_ratio"] is None


def test_bank_quality_empty_response_is_una():
    # 用不同代码避开 BaseFetcher 进程级 TTL 缓存（同代码第二次 fetch 会命中上一条测试的缓存）
    with patch("akshare.stock_financial_analysis_indicator_em", return_value=None):
        r = dt.BankQualityFetcher().fetch("000651")
    assert r["status"] == "UNA" and "error" not in r


def test_industry_constituents_paginate_beyond_100():
    """成分股 total>100 时必须翻页：150 只全量参与中位数（截断到前 100 会得到 50.5 而非 75.5）。"""

    def fake(url, params=None, **kw):
        if "ulist" in url:
            return _Resp({"data": {"diff": [{"f9": 6.88, "f12": "600570", "f14": "恒生电子"}]}})
        if "stock/get" in url:
            return _Resp({"data": {"f57": "600570", "f127": "软件开发"}})
        if str((params or {}).get("fs", "")).startswith("m:90"):
            return _Resp({"data": {"total": 1, "diff": [{"f12": "BK0737", "f14": "软件开发"}]}})
        pn = (params or {}).get("pn", 1)
        if pn == 1:
            diff = [{"f9": float(i), "f12": f"x{i}"} for i in range(1, 101)]
        elif pn == 2:
            diff = [{"f9": 999.0 + i, "f12": f"y{i}"} for i in range(1, 51)]
        else:
            diff = []
        return _Resp({"data": {"total": 150, "diff": diff}})

    with patch.object(dt, "em_get", fake):
        r = dt.IndustryValuationFetcher().fetch("600570")
    assert "error" not in r
    assert r["peers_n"] == 150
    # 全体 150 个正 PE 排序后中位=(75+76)/2=75.5；若截断在首 100 页则会是 50.5
    assert r["industry_pe_median"] == 75.5


# ---- 任务6b：多路由降级（route 入参），P1 行为不变 ----

def test_margin_p2_subset_route_and_cache_isolation():
    """P2 akshare 子集路由只有融资余额列；缓存键含路由号，P1 不得回放 P2 结果。"""
    df = pd.DataFrame([{"信用交易日期": "2026-09-04", "标的证券代码": "601939", "融资余额": 5000000000.0}])
    with patch("akshare.stock_margin_detail_sse", return_value=df):
        r = dt.MarginFetcher().fetch("601939", route="P2")
    assert "error" not in r and "子集" in r["source"]
    assert r["data"][0]["rzye"] == 5e9 and r["data"][0]["rqye"] is None

    def fake(url, **kw):  # P1 空响应 → UNA（若误回放 P2 缓存会带 data 行）
        return _Resp({"success": True, "result": {"data": []}})
    with patch.object(dt, "em_get", fake):
        r1 = dt.MarginFetcher().fetch("601939")
    assert "RPTA" in r1["source"] and r1.get("status") == "UNA"


def test_announcement_p2_cninfo_no_type_code():
    import datetime as dtm
    d = (dtm.date.today() - dtm.timedelta(days=3)).strftime("%Y-%m-%d")
    df = pd.DataFrame([{"代码": "601939", "公告标题": "重大事项提示", "公告时间": d}])
    with patch("akshare.stock_zh_a_disclosure_report_cninfo", return_value=df):
        r = dt.AnnouncementFetcher().fetch("601939", route="P2")
    assert "error" not in r and "cninfo" in r["source"]
    assert r["items"][0]["type_code"] is None  # 巨潮无类型码列 → 负面筛选降级为标题


def test_announcement_p3_notice_report_local_filter():
    import datetime as dtm
    d = (dtm.date.today() - dtm.timedelta(days=1)).strftime("%Y-%m-%d")
    df = pd.DataFrame([{"代码": "601939", "公告标题": "关于XX的公告", "公告类型": "其他", "公告日期": d},
                       {"代码": "000001", "公告标题": "别家公告", "公告类型": "其他", "公告日期": d}])
    with patch("akshare.stock_notice_report", return_value=df):  # 每日返回同表 → 按标题去重
        r = dt.AnnouncementFetcher().fetch("601939", route="P3")
    assert [i["title"] for i in r["items"]] == ["关于XX的公告"]


def test_unimplemented_route_fails_explicitly_no_fallthrough():
    """自动入表的试用路由（P3+ 等未实现 id）必须显式失败——若 fall-through 到 P1，
    瞬态成功会被记到幻影路由头上（台账归因失真）。哨兵 patch P1/P2 网络入口验证零执行。"""
    def sentinel(*a, **kw):
        raise AssertionError("未实现路由不得执行任何已有实现（网络/会话入口哨兵）")
    # Margin：P1=em_get、P2=akshare SSE 全哨兵；Announcement/KLine 同理按各自实现入口
    with patch.object(dt, "em_get", sentinel), patch("akshare.stock_margin_detail_sse", sentinel):
        r = dt.MarginFetcher().fetch("601949", route="P9")
    assert "无实现" in r["error"] and r["status"] == "UNA"
    with patch.object(dt, "em_get", sentinel), patch("akshare.stock_zh_a_disclosure_report_cninfo", sentinel), \
            patch("akshare.stock_notice_report", sentinel):
        r = dt.AnnouncementFetcher().fetch("601949", route="P4")
    assert "无实现" in r["error"] and r["status"] == "UNA"
    with patch.object(dt.KLine52WFetcher, "_fetch_baostock", sentinel), \
            patch.object(dt.KLine52WFetcher, "_fetch_em", sentinel):
        r2 = dt.KLine52WFetcher().fetch("601949", route="P3")
    assert "无实现" in r2["error"] and r2["status"] == "UNA"


def test_kline_p2_push2his():
    def fake(url, params=None, **kw):
        assert "push2his" in url
        return _Resp({"data": {"klines": ["2026-09-03,1.0,2.0,9.9,1.1,100",
                                          "2026-09-04,2.0,3.0,8.8,0.5,100"]}})
    with patch.object(dt, "em_get", fake):
        r = dt.KLine52WFetcher().fetch("601939", route="P2")
    assert r["high_52w"] == 9.9 and r["low_52w"] == 0.5 and "push2his" in r["source"]
