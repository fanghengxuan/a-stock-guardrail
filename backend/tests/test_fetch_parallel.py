from unittest.mock import patch


def _fake_fetchers():
    """为每个 fetch 调用返回可区分结果。"""
    def fake(self, code):
        # rows must be dicts: fetch_raw_data scans fin rows via row.get("DATEMMDD")
        row = {"DATEMMDD": "年报", "DATAYEAR": "2025"}
        return {"source": type(self).__name__, "data": [row]}
    return fake


def test_fetch_raw_data_returns_all_keys():
    from decision_agents import data_fetcher as df
    with patch.object(df.BaseFetcher, "fetch", _fake_fetchers()), \
         patch.object(df.BaostockDividendFetcher, "fetch_dividends", lambda self, code: []):
        raw = df.fetch_raw_data("600036")
    assert set(raw.keys()) == {
        "market", "financial", "capital_flow", "baostock_financial",
        "baostock_dividends", "business_segments", "institution_forecast",
        "ths_forecast", "buyback", "employee_holding_raw", "reverse_check_raw",
    }
    assert raw["market"]["stock_code"] == "600036"


def test_baostock_calls_never_overlap():
    """baostock 连接为进程级全局 socket，三个 baostock 取数必须串行执行。

    记录三个调用在锁内的执行区间（fake 内 sleep 模拟占用时长），
    断言任意两区间不重叠。若 _bs_locked 缺失/失效，三者同时启动会重叠。
    """
    import time as _time
    from decision_agents import data_fetcher as df

    intervals = {}
    row = {"DATEMMDD": "年报", "DATAYEAR": "2025"}
    BS = {"BaostockQuoteFetcher", "BaostockFinancialFetcher"}

    def fake(self, code):
        name = type(self).__name__
        if name in BS:
            t0 = _time.monotonic()
            _time.sleep(0.05)  # 占住"会话"，无锁时必重叠
            intervals[name] = (t0, _time.monotonic())
        return {"source": name, "data": [dict(row)]}

    def fake_divs(self, code):
        t0 = _time.monotonic()
        _time.sleep(0.05)
        intervals["BaostockDividendFetcher"] = (t0, _time.monotonic())
        return []

    with patch.object(df.BaseFetcher, "fetch", fake), \
         patch.object(df.BaostockDividendFetcher, "fetch_dividends", fake_divs):
        df.fetch_raw_data("600036")

    assert set(intervals) == BS | {"BaostockDividendFetcher"}
    spans = sorted(intervals.values())
    assert spans[0][1] <= spans[1][0], "baostock 会话重叠（socket 全局态会被并发覆写）"
    assert spans[1][1] <= spans[2][0], "baostock 会话重叠（socket 全局态会被并发覆写）"
