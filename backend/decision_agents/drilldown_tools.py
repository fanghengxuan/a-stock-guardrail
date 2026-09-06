"""下钻数据源（定量五件 + 公告/研报）— 供下钻 Agent 循环（任务 6）按需调用

继承 data_fetcher.BaseFetcher，自动获得缓存/熔断/重试/并发信号量；本模块 fetcher
仅在下钻循环中单线程调用，baostock 会话（KLine52WFetcher._session）全程持任务 3 的 _bs_lock。

统一返回约定（任务 6 依赖）：
- 成功 {"source": "<来源描述>", ...数据字段}
- 失败（基类捕获子类异常）{"source": "<name> (failed)", "note": "UNA", "error": ...}
- 数据缺失 {"status": "UNA"}

7 个源（端点事实来自设计文档 §5，fixture 实测字段名）；带 route 入参的三个支持多路由降级
（route 权重序由 route_registry 决定，P1 为原实现，P2/P3 见 data_routes.md）：
- MarginFetcher              P1 datacenter-web RPTA_WEB_RZRQ_GGMX（filter SCODE/DATE）；P2 akshare 沪深两所明细（子集）
- BankQualityFetcher         银行资产质量：ak.stock_financial_analysis_indicator_em
                             NONPERLOAN/BLDKBBL(拨备覆盖率)/NET_INTEREST_MARGIN/FIRST_ADEQUACY_RATIO
                             （RISK_COVERAGE 恒空勿用）
- NewsFetcher                个股舆情：ak.stock_news_em（近7日≤10条）
- IndustryValuationFetcher   行业估值：push2 ulist f9 + stock/get f127(行业名) + clist 成分股 f9 中位
- KLine52WFetcher            P1 baostock query_history_k_data_plus 近250交易日；P2 东财 push2his kline
- AnnouncementFetcher        P1 np-anotice security/ann（近90日≤50条）；P2 巨潮 cninfo；P3 全市场日更表兜底 + 负面筛选
- ResearchReportFetcher      个股研报：ak.stock_research_report_em（近180日≤20篇）
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
import time
from contextlib import contextmanager
from typing import Generator, Optional

from decision_agents.data_fetcher import BaseFetcher, _baostock_code, _bs_lock, _em_secid, em_get

logger = logging.getLogger(__name__)


def _num(v) -> Optional[float]:
    """宽松转 float；空串/None/NaN/'--' → None。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN → None


# ============================================================
# 纯解析函数（不碰网络，fixture 单测覆盖）
# ============================================================

def median(vals: list) -> Optional[float]:
    nums = sorted(v for v in vals if isinstance(v, (int, float)) and v == v and not isinstance(v, bool))
    if not nums:
        return None
    n = len(nums)
    return float(nums[n // 2]) if n % 2 else (nums[n // 2 - 1] + nums[n // 2]) / 2


def parse_margin_rows(resp: dict, code: str) -> dict:
    """RPTA_WEB_RZRQ_GGMX 原始响应 → 按日期降序 [{date,rzye,rqye,rzrqhe}] + windows(近N日均余额)。
    实测字段：DATE/RZYE/RQYE/RZRQYE（两融余额=融资+融券）。"""
    rows = ((resp or {}).get("result") or {}).get("data") or []
    items = []
    for r in rows:
        if code and str(r.get("SCODE", "")) != code:
            continue
        date = str(r.get("DATE", ""))[:10]
        rzye = _num(r.get("RZYE"))
        if not date or rzye is None:
            continue
        items.append({"date": date, "rzye": rzye, "rqye": _num(r.get("RQYE")), "rzrqhe": _num(r.get("RZRQYE"))})
    items.sort(key=lambda x: x["date"], reverse=True)

    def _win(n: int) -> Optional[float]:
        vals = [i["rzye"] for i in items[:n] if i["rzye"] is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    return {"data": items, "windows": {"d3": _win(3), "d5": _win(5), "d10": _win(10)}}


def parse_bank_quality(rows: list) -> dict:
    """stock_financial_analysis_indicator_em records → 最新非空报告期四指标。
    NONPERLOAN→npl_ratio BLDKBBL→coverage_ratio NET_INTEREST_MARGIN→nim
    FIRST_ADEQUACY_RATIO→capital_adequacy。"""
    latest = None
    for r in sorted(rows or [], key=lambda x: str(x.get("REPORT_DATE", "")), reverse=True):
        if _num(r.get("NONPERLOAN")) is not None:
            latest = r
            break
    if latest is None:
        return {"npl_ratio": None, "coverage_ratio": None, "nim": None, "capital_adequacy": None, "periods": None}
    return {
        "npl_ratio": _num(latest.get("NONPERLOAN")),
        "coverage_ratio": _num(latest.get("BLDKBBL")),
        "nim": _num(latest.get("NET_INTEREST_MARGIN")),
        "capital_adequacy": _num(latest.get("FIRST_ADEQUACY_RATIO")),
        "periods": str(latest.get("REPORT_DATE", ""))[:10],
    }


def parse_news_items(rows: list, days: int = 7, limit: int = 10) -> list:
    """stock_news_em records → 近 days 日、时间降序前 limit 条 {title,time,source}。"""
    cutoff = _dt.datetime.now() - _dt.timedelta(days=days)
    items = []
    for r in rows or []:
        t = str(r.get("发布时间", ""))
        try:
            dt = _dt.datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if dt >= cutoff:
            items.append({"title": r.get("新闻标题"), "time": t, "source": r.get("文章来源")})
    items.sort(key=lambda x: x["time"], reverse=True)
    return items[:limit]


def compute_52w_highlow(rows: list) -> tuple:
    """[{high,low}...]（字符串或数值均可）→ (max(high), min(low))；无有效值 → (None, None)。"""
    his = [v for v in (_num(r.get("high")) for r in rows or []) if v is not None]
    los = [v for v in (_num(r.get("low")) for r in rows or []) if v is not None]
    if not his or not los:
        return (None, None)
    return (max(his), min(los))


def parse_announcements(resp: dict, days: int = 90, limit: int = 50) -> list:
    """np-anotice security/ann 响应 → 近 days 日、时间降序前 limit 条 {title,date,type_code,type_name,art_code}。
    实测字段：data.list[].notice_date（"YYYY-MM-DD 00:00:00"）、columns 为分类对象数组
    [{column_code,column_name}]（官方分类，可能缺失 → type_* 为 None）。"""
    rows = ((resp or {}).get("data") or {}).get("list") or []
    cutoff = _dt.datetime.now() - _dt.timedelta(days=days)
    items = []
    for r in rows:
        t = str(r.get("notice_date", ""))
        try:
            dt = _dt.datetime.strptime(t[:10], "%Y-%m-%d")
        except ValueError:
            continue
        if dt < cutoff:
            continue
        col = (r.get("columns") or [{}])[0]
        items.append({
            "title": r.get("title"),
            "date": t[:10],
            "type_code": col.get("column_code"),
            "type_name": col.get("column_name"),
            "art_code": r.get("art_code"),
        })
    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:limit]


# 负面公告关键词（spec §6 信号层）：官方类型名或标题命中即保留。
# §6 原集合外补两个动词前置分支：东财主流标题为倒装语序（"关于变更审计机构的公告"），
# 且事务所类标题不含"审计"二字；分支必须带"变更"前缀——裸"会计师事务所"会误伤"续聘…"类中性公告。
_NEG_ANN_RE = re.compile("处罚|问询|立案|质押|审计.*变更|非标|警示|变更.*审计|变更.*事务所")


def filter_negative_announcements(items: list) -> list:
    """parse_announcements 输出 → 只留负面公告（监管处罚/问询/立案/质押/审计机构变更/非标意见/警示）。"""
    return [i for i in items or []
            if _NEG_ANN_RE.search(str(i.get("type_name") or ""))
            or _NEG_ANN_RE.search(str(i.get("title") or ""))]


def parse_research_reports(rows: list, days: int = 180, limit: int = 20) -> list:
    """stock_research_report_em records → 近 days 日、时间降序前 limit 条
    {org,title,rating,rating_change,eps_this,eps_next,date}。
    实测：akshare 已把 reportapi 原始字段（orgName/title/emRatingName/publishDate/
    predictThisYearEps…）重命名为中文列并丢弃 ratingChange 列，故 rating_change 恒 None。"""
    now = _dt.datetime.now()
    cutoff = now - _dt.timedelta(days=days)
    year = now.year
    items = []
    for r in rows or []:
        date = str(r.get("日期", ""))[:10]
        try:
            dt = _dt.datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            continue
        if dt < cutoff:
            continue
        items.append({
            "org": r.get("机构"),
            "title": r.get("报告名称"),
            "rating": r.get("东财评级"),
            "rating_change": None,
            "eps_this": _num(r.get(f"{year}-盈利预测-收益")),
            "eps_next": _num(r.get(f"{year + 1}-盈利预测-收益")),
            "date": date,
        })
    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:limit]


# ============================================================
# Fetcher（继承 BaseFetcher：熔断/缓存/重试/em_get 节流全复用）
# ============================================================

class MultiRouteMixin:
    """多路由 fetcher 混入：fetch(code, route="P1") 按路由切换 _fetch 实现（route 存 self._route）。

    BaseFetcher 缓存键为 '<name>:<code>'，非 P1 路由临时给实例 name 追加路由号——
    否则降级 P2 取回的数据会在 20min TTL 内被当成 P1 结果回放，台账胜负记录错乱。
    P1 键保持不变（与路由改造前兼容）。"""

    _route = "P1"

    def fetch(self, stock_code: str, route: str = "P1") -> dict:  # type: ignore[override]
        cls_name = type(self).name
        self.name = cls_name if route == "P1" else f"{cls_name}@{route}"
        self._route = route
        try:
            return BaseFetcher.fetch(self, stock_code)
        finally:
            self.name = cls_name


def _no_route(route: str) -> dict:
    """未实现路由（如自动入表的试用路由，任务 11 spec 解释器就位前）→ 显式失败而非 fall-through 到 P1，
    防瞬态成功被 record_success 记到幻影路由头上。error+status=UNA 与降级循环失败判定兼容；不 raise。"""
    return {"error": f"route {route} 无实现（待任务11 spec 解释器）", "status": "UNA"}


class MarginFetcher(MultiRouteMixin, BaseFetcher):
    """融资融券个股明细：P1 东财 datacenter-web RPTA_WEB_RZRQ_GGMX（filter 列名 SCODE，非 SECURITY_CODE，
    合计字段 RZRQYE）；P2 akshare 沪深两所明细（子集路由：无融券余额/合计列）。"""
    name = "Margin"
    priority = 1

    def _fetch(self, stock_code: str) -> dict:
        if self._route == "P2":
            return self._fetch_akshare(stock_code)
        if self._route != "P1":
            return _no_route(self._route)
        return self._fetch_em(stock_code)

    def _fetch_em(self, stock_code: str) -> dict:
        code = stock_code.strip().zfill(6)
        r = em_get(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params={
                "reportName": "RPTA_WEB_RZRQ_GGMX", "columns": "ALL",
                "filter": f'(SCODE="{code}")', "sortColumns": "DATE", "sortTypes": -1,
                "pageSize": 60, "source": "WEB", "client": "WEB",
            },
        )
        parsed = parse_margin_rows(r.json(), code)
        src = "东方财富 datacenter 融资融券明细(RPTA_WEB_RZRQ_GGMX)"
        if not parsed["data"]:
            # 响应成功但无记录（非两融标的）=确定性无数据 → UNA；不抛错，避免白烧重试并污染共享熔断器
            return {"source": src, **parsed, "status": "UNA"}
        return {"source": src, **parsed}

    def _fetch_akshare(self, stock_code: str) -> dict:
        """P2 子集路由：交易所两融明细日报按日回扫（最多 16 自然日/攒 10 个交易日），
        只有融资余额列（rqye/rzrqhe 缺失为 None，下游按缺字段容忍）。"""
        import akshare as ak
        code = stock_code.strip().zfill(6)
        is_sh = code.startswith("6")
        fetch = ak.stock_margin_detail_sse if is_sh else ak.stock_margin_detail_szse
        src = f"akshare {'上交所' if is_sh else '深交所'}两融明细（子集路由：仅融资余额列）"
        today = _dt.date.today()
        items: list = []
        seen_dates: set = set()
        for back in range(16):
            if len(items) >= 10:
                break
            day = (today - _dt.timedelta(days=back)).strftime("%Y%m%d")
            try:
                df = fetch(date=day)
            except Exception:  # noqa: BLE001  尾部日期接口抖动不毁掉已攒到的数据
                if items:
                    break
                raise
            rows = [] if df is None or df.empty else df.to_dict(orient="records")
            if not rows:  # 周末/未发布
                continue
            hit = next((r for r in rows
                        if str(r.get("标的证券代码") or r.get("证券代码") or "").zfill(6) == code), None)
            if hit is None:
                # 当日全量表有数据但无此标的 → 非两融标的，与 P1 同判定：确定性无数据
                return {"source": src, "data": [], "windows": {"d3": None, "d5": None, "d10": None},
                        "status": "UNA"}
            rzye = _num(hit.get("融资余额"))
            if rzye is None:
                continue
            raw_date = str(hit.get("信用交易日期") or day)
            ds = raw_date.replace("-", "")[:8]
            date_str = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}" if len(ds) == 8 else raw_date
            if date_str in seen_dates:
                break  # 接口忽略 date 参数恒返同一交易日 → 停止回扫，防同日到 windows 均值
            seen_dates.add(date_str)
            items.append({"date": date_str,
                          "rzye": rzye, "rqye": _num(hit.get("融券余额")), "rzrqhe": _num(hit.get("融资融券余额"))})
        if not items:
            raise RuntimeError("akshare 两融明细：近16日无该标的记录")
        items.sort(key=lambda x: x["date"], reverse=True)

        def _win(n: int):
            vals = [i["rzye"] for i in items[:n] if i["rzye"] is not None]
            return round(sum(vals) / len(vals), 2) if vals else None

        parsed = {"data": items, "windows": {"d3": _win(3), "d5": _win(5), "d10": _win(10)}}
        return {"source": src, **parsed}


class BankQualityFetcher(BaseFetcher):
    """银行资产质量：东财 F10 关键指标（不良率/拨备覆盖率/净息差/核心一级资本充足率）。"""
    name = "BankQuality"
    priority = 1

    def _fetch(self, stock_code: str) -> dict:
        import akshare as ak
        code = stock_code.strip().zfill(6)
        symbol = f"{code}.SH" if code.startswith("6") else f"{code}.SZ"
        df = ak.stock_financial_analysis_indicator_em(symbol=symbol)
        parsed = parse_bank_quality([] if df is None else df.to_dict(orient="records"))
        src = "东方财富 F10 关键指标 stock_financial_analysis_indicator_em"
        if parsed["npl_ratio"] is None:
            # 响应成功但无不良率字段（非银行标的/未披露）=确定性无数据 → UNA，不触发重试/熔断
            return {"source": src, **parsed, "status": "UNA"}
        return {"source": src, **parsed}


class NewsFetcher(BaseFetcher):
    """个股舆情：ak.stock_news_em（东财个股新闻），近 7 日 ≤10 条。"""
    name = "News"
    priority = 1

    def _fetch(self, stock_code: str) -> dict:
        import akshare as ak
        code = stock_code.strip().zfill(6)
        df = ak.stock_news_em(symbol=code)
        if df is None or df.empty:
            raise RuntimeError("empty news response")
        items = parse_news_items(df.to_dict(orient="records"))
        result = {"source": "东方财富 个股新闻 stock_news_em", "items": items}
        if not items:
            result["status"] = "UNA"  # 近7日无新闻
        return result


# push2 主站/编号子域对本机 IP 会间歇 RST（WAF 拦截），push2delay 同 API 同 JSON 结构可通
# （行情延迟约 15min，日级决策不受影响）。当前默认走 push2delay。
_PUSH2 = "https://push2delay.eastmoney.com"


class IndustryValuationFetcher(BaseFetcher):
    """行业估值：①ulist f9(动态PE) + stock/get f127(行业名，ulist 的 f127 语义不同勿用)
    → ②clist m:90+t:2 分页找行业 BK 码 → ③clist b:BK 成分股 f9 中位数。"""
    name = "IndustryValuation"
    priority = 1

    def _fetch(self, stock_code: str) -> dict:
        code = stock_code.strip().zfill(6)
        r = em_get(
            f"{_PUSH2}/api/qt/ulist.np/get",
            params={"secids": _em_secid(code), "fields": "f9,f12,f14", "fltt": 2, "invt": 2, "np": 1},
        )
        stock = (((r.json() or {}).get("data") or {}).get("diff") or [{}])[0]
        r = em_get(
            f"{_PUSH2}/api/qt/stock/get",
            params={"secid": _em_secid(code), "fields": "f57,f127", "fltt": 2, "invt": 2},
        )
        industry = str(((r.json() or {}).get("data") or {}).get("f127") or "")
        if not industry:
            raise RuntimeError("no industry name (f127)")
        boards: list = []
        pn = 1
        while True:  # 行业板块 496 个，pz 上限实测 100，分页找齐
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
        # 精确匹配优先；行业名带层级后缀（如"银行Ⅱ"vs板块"银行"）时取最长前缀匹配
        board = next((b for b in boards if str(b.get("f14")) == industry), None)
        if board is None:
            cands = [b for b in boards
                     if industry.startswith(str(b.get("f14") or "\x00")) or str(b.get("f14") or "\x00").startswith(industry)]
            board = max(cands, key=lambda b: len(str(b.get("f14"))), default=None)
        if not board:
            raise RuntimeError(f"industry board not found: {industry}")
        cons: list = []
        pn = 1
        while True:  # 成分股可超单页 100（如软件开发/半导体），必须翻页取全，否则中位数被静默截断
            r = em_get(
                f"{_PUSH2}/api/qt/clist/get",
                params={"pn": pn, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                        "fid": "f12", "fs": f"b:{board['f12']}+f:!50", "fields": "f9,f12,f14"},
            )
            d = (r.json() or {}).get("data") or {}
            page = d.get("diff") or []
            cons += page
            if not page or len(cons) >= int(d.get("total") or 0):
                break
            pn += 1
        # 口径注明：peers_n=全部成分数；中位数只用正 PE 子集（亏损股 f9 为负会拉歪中位数）
        pes = [v for v in (_num(d.get("f9")) for d in cons) if v is not None and v > 0]
        return {
            "source": "东方财富 push2 行业板块估值(clist m:90+t:2)",
            "industry": industry,
            "stock_pe": _num(stock.get("f9")),
            "industry_pe_median": median(pes),
            "peers_n": len(cons),
        }


class KLine52WFetcher(MultiRouteMixin, BaseFetcher):
    """52 周高低：P1 Baostock 日K（前复权）近 250 交易日；P2 东财 push2his kline（更快，
    ⚠️ 复权基准与 P1 不同，混用须统一）。"""
    name = "KLine52W"
    priority = 1

    def __init__(self):
        super().__init__()
        self._bs = None

    def _get_bs(self):
        if self._bs is None:
            import baostock as bs
            self._bs = bs
        return self._bs

    @contextmanager
    def _session(self) -> Generator:
        bs = self._get_bs()
        # baostock 进程级全局 socket 非线程安全：fetch() 可能经任务6与 L1 fetch_raw_data 并发，
        # login→yield→logout 全程持 _bs_lock。注意 _bs_lock 非重入——本临界区内（含 _fetch 调用方）
        # 禁止再套 _bs_locked / 嵌套第二层 _session。
        with _bs_lock:
            lg = bs.login()
            if lg.error_code != "0":
                raise RuntimeError(f"baostock login failed: {lg.error_msg}")
            try:
                yield bs
            finally:
                try:
                    bs.logout()
                except Exception as e:  # noqa: BLE001
                    logger.warning("baostock logout error: %s", e)

    def _fetch(self, stock_code: str) -> dict:
        if self._route == "P2":
            return self._fetch_em(stock_code)
        if self._route != "P1":
            return _no_route(self._route)
        return self._fetch_baostock(stock_code)

    def _fetch_baostock(self, stock_code: str) -> dict:
        bs_code = _baostock_code(stock_code)
        with self._session() as bs:
            rs = bs.query_history_k_data_plus(
                code=bs_code, fields="date,close,high,low",
                start_date=time.strftime("%Y-%m-%d", time.localtime(time.time() - 380 * 86400)),
                end_date=time.strftime("%Y-%m-%d"),
                frequency="d", adjustflag="2",
            )
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(dict(zip(rs.fields, rs.get_row_data())))
        if not rows:
            raise RuntimeError("baostock empty kline")
        rows = rows[-250:]  # 近250交易日
        hi, lo = compute_52w_highlow(rows)
        if hi is None:
            raise RuntimeError("no valid high/low in kline")
        closes = [_num(r.get("close")) for r in rows]
        return {"source": "Baostock query_history_k_data_plus(前复权)",
                "high_52w": hi, "low_52w": lo, "closes": closes}

    def _fetch_em(self, stock_code: str) -> dict:
        """P2 东财 push2his 日K：快但复权基准与 baostock 不同（东财前复权锚点），结果注明口径。"""
        code = stock_code.strip().zfill(6)
        r = em_get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            params={"secid": _em_secid(code), "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                    "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55",
                    "klt": 101, "fqt": 1, "end": "20500101", "lmt": 250},
            headers={"Referer": "https://quote.eastmoney.com/"},
        )
        klines = (((r.json() or {}).get("data") or {}).get("klines")) or []
        parts = [k.split(",") for k in klines]
        parts = [p for p in parts if len(p) >= 5]  # f51日期,f52开,f53收,f54高,f55低
        hi, lo = compute_52w_highlow([{"high": p[3], "low": p[4]} for p in parts])
        if hi is None:
            raise RuntimeError("push2his empty kline")
        return {"source": "东方财富 push2his 日K(前复权，⚠️复权基准与 baostock 不同勿混用)",
                "high_52w": hi, "low_52w": lo, "closes": [_num(p[2]) for p in parts]}


class AnnouncementFetcher(MultiRouteMixin, BaseFetcher):
    """个股公告：P1 东财 np-anotice security/ann（近90日≤50条）；
    P2 巨潮 cninfo（无类型码列，负面筛选降级为标题关键词）；
    P3 ak.stock_notice_report 全市场日更表本地过滤（慢，仅兜底，回看近5自然日）。
    ⚠️ /api/getAnns 端点不存在（实测空响应），只有 security/ann 可用。"""
    name = "Announcement"
    priority = 1

    def _fetch(self, stock_code: str) -> dict:
        if self._route == "P2":
            return self._fetch_cninfo(stock_code)
        if self._route == "P3":
            return self._fetch_notice_report(stock_code)
        if self._route != "P1":
            return _no_route(self._route)
        return self._fetch_em(stock_code)

    def _fetch_em(self, stock_code: str) -> dict:
        code = stock_code.strip().zfill(6)
        r = em_get(
            "https://np-anotice-stock.eastmoney.com/api/security/ann",
            params={"sr": -1, "page_size": 50, "page_index": 1, "ann_type": "A",
                    "client_source": "web", "stock_list": code},
            headers={"Referer": f"https://data.eastmoney.com/notices/stock/{code}.html"},
        )
        j = r.json()
        if not j.get("success"):
            raise RuntimeError(f"ann api error: {j.get('error') or 'unknown'}")
        items = parse_announcements(j)
        result = {"source": "东方财富 个股公告 np-anotice security/ann", "items": items}
        if not items:
            result["status"] = "UNA"  # 近90日无公告（含无此代码）=确定性无数据，不触发重试/熔断
        return result

    def _fetch_cninfo(self, stock_code: str) -> dict:
        import akshare as ak
        code = stock_code.strip().zfill(6)
        end = _dt.date.today()
        start = end - _dt.timedelta(days=90)
        df = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=code, market="沪深京", start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
        cutoff = start.strftime("%Y-%m-%d")
        items = []
        for r in [] if df is None or df.empty else df.to_dict(orient="records"):
            d = str(r.get("公告时间", ""))[:10]
            if d < cutoff:
                continue
            # 巨潮无类型码列 → type_* 置 None，负面筛选只走标题关键词
            items.append({"title": r.get("公告标题"), "date": d,
                          "type_code": None, "type_name": None, "art_code": None})
        items.sort(key=lambda x: x["date"], reverse=True)
        result = {"source": "巨潮资讯 stock_zh_a_disclosure_report_cninfo(无类型码列)", "items": items[:50]}
        if not items:
            result["status"] = "UNA"
        return result

    def _fetch_notice_report(self, stock_code: str) -> dict:
        """P3 兜底：全市场日更表按日拉取本地过滤，仅回看近 5 自然日（全 90 日太慢）。"""
        import akshare as ak
        code = stock_code.strip().zfill(6)
        today = _dt.date.today()
        items: list = []
        seen: set = set()
        errs = 0
        for back in range(5):
            d = (today - _dt.timedelta(days=back)).strftime("%Y%m%d")
            try:
                df = ak.stock_notice_report(symbol="全部", date=d)
            except Exception:  # noqa: BLE001
                errs += 1
                continue
            for r in [] if df is None or df.empty else df.to_dict(orient="records"):
                if str(r.get("代码", "")).zfill(6) != code:
                    continue
                key = str(r.get("公告标题"))
                if key in seen:
                    continue
                seen.add(key)
                items.append({"title": r.get("公告标题"), "date": str(r.get("公告日期", ""))[:10],
                              "type_code": None, "type_name": r.get("公告类型"), "art_code": None})
        if errs >= 5 and not items:
            raise RuntimeError("stock_notice_report 全部日期请求失败")
        items.sort(key=lambda x: x["date"], reverse=True)
        result = {"source": "akshare stock_notice_report 全市场日更表过滤(近5日兜底)", "items": items[:50]}
        if not items:
            result["status"] = "UNA"
        return result


class ResearchReportFetcher(BaseFetcher):
    """个股研报：ak.stock_research_report_em（东财 reportapi，近180日≤20篇）。"""
    name = "ResearchReport"
    priority = 1

    def _fetch(self, stock_code: str) -> dict:
        import akshare as ak
        code = stock_code.strip().zfill(6)
        try:
            df = ak.stock_research_report_em(symbol=code)
        except KeyError as e:
            # reportapi 返回空列表（无卖方覆盖/无效代码）时 akshare 内部抛 KeyError('infoCode')，
            # 属确定性无数据而非请求失败 → 转 UNA，避免白烧重试并污染共享熔断器（任务 4 裁决定型）
            if "infoCode" not in str(e):
                raise
            df = None
        records = [] if df is None or df.empty else df.to_dict(orient="records")
        items = parse_research_reports(records)
        result = {"source": "东方财富 个股研报 stock_research_report_em", "items": items}
        if not items:
            result["status"] = "UNA"
        return result
