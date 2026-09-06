"""捕获下钻数据源真实响应 → tests/fixtures/*.json（截断前 30 条，research_reports 为 20 条）。

7 个接口（均实测端点，设计文档 §5）：
- margin:            东财 datacenter-web RPTA_WEB_RZRQ_GGMX（filter 列 SCODE/DATE）
- bank_quality:      ak.stock_financial_analysis_indicator_em
- news:              ak.stock_news_em
- industry:          push2 ulist f127/f9 + clist 行业列表 + clist 成分股
- kline:             baostock query_history_k_data_plus 近 250 交易日
- announcements:     东财 np-anotice security/ann（注意：非 /api/getAnns，该端点不存在）
- research_reports:  ak.stock_research_report_em
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from decision_agents.data_fetcher import _baostock_code, _em_secid, em_get  # noqa: E402
from decision_agents.drilldown_tools import _PUSH2  # noqa: E402

CODE = "600036"
FX = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures"
FX.mkdir(parents=True, exist_ok=True)


def _save(name: str, obj) -> None:
    path = FX / f"{name}.json"
    path.write_text(json.dumps(obj, ensure_ascii=False, default=str, allow_nan=True), encoding="utf-8")
    print(f"saved {path.name} ({path.stat().st_size} bytes)")


def capture_margin():
    r = em_get(
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        params={
            "reportName": "RPTA_WEB_RZRQ_GGMX", "columns": "ALL",
            "filter": f'(SCODE="{CODE}")', "sortColumns": "DATE", "sortTypes": -1,
            "pageSize": 60, "source": "WEB", "client": "WEB",
        },
    )
    j = r.json()
    rows = ((j.get("result") or {}).get("data")) or []
    j["result"]["data"] = rows[:30]
    _save("margin", j)


def capture_bank_quality():
    import akshare as ak
    symbol = f"{CODE}.SH" if CODE.startswith("6") else f"{CODE}.SZ"
    df = ak.stock_financial_analysis_indicator_em(symbol=symbol)
    _save("bank_quality", df.head(30).to_dict(orient="records"))


def capture_news():
    import akshare as ak
    df = ak.stock_news_em(symbol=CODE)
    _save("news", df.head(30).to_dict(orient="records"))


def capture_industry():
    # ① ulist 动态PE(f9) + stock/get 行业名(f127，ulist 的 f127 是数值语义不同)
    r = em_get(
        f"{_PUSH2}/api/qt/ulist.np/get",
        params={"secids": _em_secid(CODE), "fields": "f9,f12,f14", "fltt": 2, "invt": 2, "np": 1},
    )
    stock = (((r.json() or {}).get("data") or {}).get("diff") or [{}])[0]
    r = em_get(
        f"{_PUSH2}/api/qt/stock/get",
        params={"secid": _em_secid(CODE), "fields": "f57,f127", "fltt": 2, "invt": 2},
    )
    industry = str(((r.json() or {}).get("data") or {}).get("f127") or "")
    # ② 行业板块列表分页，按名找 BK 码
    boards, pn = [], 1
    while True:
        r = em_get(
            f"{_PUSH2}/api/qt/clist/get",
            params={"pn": pn, "pz": 100, "po": 0, "np": 1, "fltt": 2, "invt": 2,
                    "fid": "f12", "fs": "m:90+t:2", "fields": "f12,f14"},
        )
        d = (r.json() or {}).get("data") or {}
        page = d.get("diff") or []
        boards += page
        if not page or len(boards) >= int(d.get("total") or 0):
            break
        pn += 1
    board = next((b for b in boards if str(b.get("f14")) == industry),
                 next((b for b in boards if industry and industry in str(b.get("f14"))), None))
    # ③ 成分股动态PE
    cons = []
    if board:
        r = em_get(
            f"{_PUSH2}/api/qt/clist/get",
            params={"pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                    "fid": "f12", "fs": f"b:{board['f12']}+f:!50", "fields": "f9,f12,f14"},
        )
        cons = (((r.json() or {}).get("data") or {}).get("diff")) or []
    _save("industry", {"stock": stock, "industry": industry, "board": board, "constituents": cons[:30]})


def capture_kline():
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")
    try:
        rs = bs.query_history_k_data_plus(
            code=_baostock_code(CODE), fields="date,close,high,low",
            start_date=time.strftime("%Y-%m-%d", time.localtime(time.time() - 380 * 86400)),
            end_date=time.strftime("%Y-%m-%d"), frequency="d", adjustflag="2",
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(dict(zip(rs.fields, rs.get_row_data())))
    finally:
        bs.logout()
    _save("kline", rows[:30])


def capture_announcements():
    r = em_get(
        "https://np-anotice-stock.eastmoney.com/api/security/ann",
        params={"sr": -1, "page_size": 50, "page_index": 1, "ann_type": "A",
                "client_source": "web", "stock_list": CODE},
        headers={"Referer": f"https://data.eastmoney.com/notices/stock/{CODE}.html"},
    )
    j = r.json()
    rows = ((j.get("data") or {}).get("list")) or []
    j.setdefault("data", {})["list"] = rows[:30]  # 截断前 30 条
    _save("announcements", j)


def capture_research_reports():
    import akshare as ak
    df = ak.stock_research_report_em(symbol=CODE)
    _save("research_reports", df.head(20).to_dict(orient="records"))  # 截断前 20 条


if __name__ == "__main__":
    all_fns = (capture_margin, capture_bank_quality, capture_news, capture_industry,
               capture_kline, capture_announcements, capture_research_reports)
    only = set(sys.argv[1:])  # 可选：只重跑指定 fixture（如 python capture_fixtures.py industry）
    fns = [f for f in all_fns if not only or f.__name__.replace("capture_", "") in only]
    for fn in fns:
        t0 = time.time()
        try:
            fn()
            print(f"  {fn.__name__} OK {(time.time() - t0) * 1000:.0f}ms")
        except Exception as e:  # noqa: BLE001
            print(f"  {fn.__name__} FAIL {(time.time() - t0) * 1000:.0f}ms {type(e).__name__}: {e}")
