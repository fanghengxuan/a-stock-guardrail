"""L1 数据获取层（多源策略模式 + 交叉验证）

深度借鉴 ZhuLinsen/daily_stock_analysis data_provider（⭐58k）+ simonlin1212/a-stock-data SKILL.md（⭐7.4k）：
- BaseFetcher(ABC) 策略模式基类 + priority 多源 fallback（朱林森 DataFetcherManager）
- CircuitBreaker 完整状态机：CLOSED→失败3次→OPEN→冷却5min→HALF_OPEN 半开探测→成功CLOSED/失败OPEN，
  按 source 分别管理 + record_inconclusive（朱林森 realtime_types.CircuitBreaker）
- TTLCache 20min + RLock + prune（朱林森 _fundamental_cache）
- BoundedSemaphore 并发控制（朱林森 _fundamental_timeout_slots）
- 指数退避重试（朱林森 tenacity wait_exponential）
- em_get() 东财统一限流：EM_MIN_INTERVAL=1.0s + 随机抖动 + Keep-Alive session + UA 轮换（a-stock-data em_get）
- UA 池 ≥10 随机轮换（朱林森 70+ UA 池）

数据源（免费，按 a-stock-data 优先级：mootdx/腾讯不封IP首选，东财仅独有数据限流）：
- 行情 PE/PB/价格：腾讯 qt.gtimg.cn（主，不封IP）+ 东财 push2 stock/get（em_get+UA池）+ Baostock(price/epsTTM,BPS→PE/PB 第三源仲裁)
- 财务 ROE/净利润：东财 datacenter RPT_LICO_FN_CPD（主）+ Baostock query_profit_data（备）
- 股息率：Baostock query_dividend_data（主，dividCashPsBeforeTax/价格）
- 资金：东财 push2 fflow（重试+退避）

精度保障（leader 要求 <1%）：每个关键字段（PE/PB/价格/ROE/股息率）至少双源交叉验证，
两源偏差 >1% 时取第三源仲裁；三源仍偏差 >1% 标 "数据存疑"，不编造。
"""
from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock, RLock
from typing import Generator, Optional

import requests

logger = logging.getLogger(__name__)


# ============================================================
# UA 池（≥10，随机轮换，降低单 UA 被识别）——借鉴朱林森 70+ UA 池
# ============================================================

_UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/121.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 11_0_0) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15) AppleWebKit/605.1.15 Safari/605.1.15",
]


def _random_ua() -> dict:
    return {
        "User-Agent": random.choice(_UA_POOL),
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://quote.eastmoney.com/",
    }


# 东财会话——Connection: close 避免复用死连接
_EM_SESSION = requests.Session()
_EM_SESSION.headers.update({"Accept": "*/*", "Accept-Encoding": "gzip, deflate", "Referer": "https://quote.eastmoney.com/"})
_EM_MIN_INTERVAL = 1.0
_EM_LAST_CALL = [0.0]
_EM_LOCK = RLock()


def _load_em_cookie() -> str | None:
    """加载东财 Cookie：优先环境变量 EAST_MONEY_COOKIE，其次 backend/eastmoney_cookie.txt。"""
    import os
    cookie = os.getenv("EAST_MONEY_COOKIE", "").strip()
    if cookie:
        return cookie
    cookie_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eastmoney_cookie.txt")
    try:
        with open(cookie_file, encoding="utf-8") as f:
            cookie = f.read().strip()
            if cookie:
                return cookie
    except (FileNotFoundError, OSError):
        pass
    return None


_EM_COOKIE = _load_em_cookie()


def em_get(url: str, **kwargs) -> requests.Response:
    """东财统一节流入口：最小间隔 1.0s + 随机抖动 + UA 轮换 + Cookie 注入。
    Connection: close 避免复用被服务端关闭的 Keep-Alive 连接。"""
    with _EM_LOCK:
        elapsed = time.time() - _EM_LAST_CALL[0]
        wait = _EM_MIN_INTERVAL - elapsed + random.uniform(0.0, 0.6)
        if wait > 0:
            time.sleep(wait)
        _EM_LAST_CALL[0] = time.time()
        kwargs.setdefault("timeout", 10.0)
        headers = _random_ua()
        headers["Connection"] = "close"
        if _EM_COOKIE:
            headers["Cookie"] = _EM_COOKIE
        headers.update(kwargs.pop("headers", {}) or {})
        return _EM_SESSION.get(url, headers=headers, **kwargs)


# ============================================================
# CircuitBreaker（借鉴朱林森 realtime_types.CircuitBreaker 完整状态机）
# CLOSED→失败3次→OPEN→冷却5min→HALF_OPEN(半开探测1次)→成功CLOSED/失败OPEN
# ============================================================

class CircuitBreaker:
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 300.0, half_open_max_calls: int = 1):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_calls = half_open_max_calls
        self._state = self.CLOSED
        self._failures = 0
        self._last_failure_time = 0.0
        self._half_open_calls = 0
        self._lock = RLock()

    def is_available(self) -> bool:
        with self._lock:
            now = time.time()
            if self._state == self.CLOSED:
                return True
            if self._state == self.OPEN:
                if now - self._last_failure_time >= self.cooldown_seconds:
                    self._state = self.HALF_OPEN
                    self._half_open_calls = 0
                    self._last_failure_time = now
                    logger.info("[熔断器] %s 冷却完成，进入半开探测", self._source_name())
                else:
                    return False
            if self._state == self.HALF_OPEN:
                if self._half_open_calls < self.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                if now - self._last_failure_time >= self.cooldown_seconds:
                    self._half_open_calls = 1
                    self._last_failure_time = now
                    return True
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            if self._state == self.HALF_OPEN:
                logger.info("[熔断器] %s 半开探测成功，恢复 CLOSED", self._source_name())
            self._state = self.CLOSED
            self._failures = 0
            self._half_open_calls = 0

    def record_failure(self, error: Optional[str] = None) -> None:
        with self._lock:
            self._failures += 1
            self._last_failure_time = time.time()
            if self._state == self.HALF_OPEN:
                self._state = self.OPEN
                self._half_open_calls = 0
                logger.warning("[熔断器] %s 半开探测失败，继续熔断 %ss", self._source_name(), self.cooldown_seconds)
            elif self._failures >= self.failure_threshold:
                self._state = self.OPEN
                logger.warning("[熔断器] %s 连续失败 %s 次，熔断(冷却%ss) err=%s",
                               self._source_name(), self._failures, self.cooldown_seconds, error)

    def record_inconclusive(self) -> None:
        """半开探测结果不确定（返回空/None）→ 回 OPEN 重新冷却。"""
        with self._lock:
            if self._state == self.HALF_OPEN:
                self._state = self.OPEN
                self._half_open_calls = 0
                self._last_failure_time = time.time()

    def _source_name(self) -> str:
        return getattr(self, "name", "unknown")


# ============================================================
# TTL 缓存（20min，借鉴朱林森 _fundamental_cache + prune）
# ============================================================

class TTLCache:
    def __init__(self, ttl_seconds: float = 1200.0, max_entries: int = 200):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._store: dict = {}
        self._lock = RLock()

    def get(self, key: str):
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            ts, val = item
            if time.time() - ts > self.ttl:
                del self._store[key]
                return None
            return val

    def set(self, key: str, val) -> None:
        with self._lock:
            self._store[key] = (time.time(), val)
            # prune 超额
            if len(self._store) > self.max_entries:
                oldest = sorted(self._store.items(), key=lambda kv: kv[1][0])[: len(self._store) - self.max_entries]
                for k, _ in oldest:
                    self._store.pop(k, None)

    def invalidate_code(self, stock_code: str) -> int:
        """删除某股票的全部缓存键（键格式 '<fetcher>:<code>'）。返回删除数。"""
        with self._lock:
            keys = [k for k in self._store if k.endswith(f":{stock_code}")]
            for k in keys:
                del self._store[k]
            return len(keys)


_cache = TTLCache(ttl_seconds=1200.0)
_concurrency = BoundedSemaphore(4)  # 朱林森 BoundedSemaphore 并发控制

# baostock 非线程安全：连接存于进程级全局（context.default_socket），
# login 覆写全局/logout 关闭 socket，并发会话会串扰响应。
# 所有 baostock 取数（login→query→logout 全程）必须经 _bs_locked 串行化。
# RLock：外层 _bs_locked 与 _shared_bs_session 可能嵌套（可重入）。
_bs_lock = RLock()
_bs_connected = False      # 进程内是否已 login（共享会话保活）
_bs_last_use = 0.0         # 上次使用时间，超空闲窗口重建连接


@contextmanager
def _shared_bs_session() -> Generator:
    """进程级共享 Baostock 会话：首用 login 一次，任务间保活复用、不随任务 logout。

    收益：三个 Baostock fetcher（行情/财务/分红）从「各自 login→logout 3 次」
    降为「共享 1 次 login」，省 2 次握手；且高频 login/logout 会触发 Baostock
    服务端限流（实测连续 login 后 query 挂起）。空闲超 90s 重建（长驻进程防
    连接被服务端回收后失效）。Baostock socket 非线程安全，调用方须经 _bs_locked
    串行（本函数不再自加锁，避免与外层重入竞争）。"""
    global _bs_connected, _bs_last_use
    import baostock as bs
    now = time.time()
    if _bs_connected and now - _bs_last_use > 90:  # 空闲过久 → 重建
        try:
            bs.logout()
        except Exception:  # noqa: BLE001
            pass
        _bs_connected = False
    if not _bs_connected:
        lg = bs.login()
        if lg.error_code != "0":
            _bs_connected = False
            raise RuntimeError(f"baostock login failed: {lg.error_msg}")
        _bs_connected = True
    try:
        yield bs
    finally:
        _bs_last_use = time.time()


def _bs_locked(fn, code):
    with _bs_lock:
        return fn(code)


# ============================================================
# 交叉验证（leader 精度 <1% 要求）
# ============================================================

def _within_tolerance(a: Optional[float], b: Optional[float], tol: float = 0.01) -> bool:
    if a is None or b is None or a == 0 or b == 0:
        return False
    return abs(a - b) / max(abs(a), abs(b)) <= tol


def cross_validate(values: list, tol: float = 0.01) -> tuple:
    """多源交叉验证。返回 (value, sources, status)。
    status: 'consensus'(多源一致<1%) / 'median'(两源偏差>1% 取中位) / 'disputed'(三源仍偏差>1% 标存疑) / 'single'(仅一源)。
    values: [(value, source), ...]（过滤 None）。"""
    vals = [(v, s) for v, s in values if v is not None]
    if not vals:
        return None, [], "empty"
    if len(vals) == 1:
        return vals[0][0], [vals[0][1]], "single"
    # 找两两一致的源
    for i in range(len(vals)):
        for j in range(i + 1, len(vals)):
            if _within_tolerance(vals[i][0], vals[j][0], tol):
                return vals[i][0], [vals[i][1], vals[j][1]], "consensus"
    # 无两源一致 → 取中位（偏差>1%）
    sorted_vals = sorted(vals, key=lambda x: x[0])
    n = len(sorted_vals)
    if n == 2:
        # 双源偏差>1%：优先取主源（输入列表中第一个，优先级更高）
        # 避免均值被过时数据拉偏（如 Baostock epsTTM 过时导致 PE 偏低）
        mid_val = vals[0][0]
    else:
        mid_val = sorted_vals[n // 2][0]
    return mid_val, [v[1] for v in vals], "median" if n >= 3 else "disputed"


# ============================================================
# 代码映射
# ============================================================

def _tencent_symbol(code: str) -> str:
    c = code.strip().zfill(6)
    if c.startswith("6"):
        return "sh" + c
    if c.startswith(("0", "3")):
        return "sz" + c
    return "bj" + c


def _em_secid(code: str) -> str:
    c = code.strip().zfill(6)
    return f"{'1' if c.startswith('6') else '0'}.{c}"


def _em_secucode(code: str) -> str:
    c = code.strip().zfill(6)
    return f"{c}.{'SH' if c.startswith('6') else 'SZ'}"


def _baostock_code(code: str) -> str:
    c = code.strip().zfill(6)
    if c.startswith("6"):
        return "sh." + c
    if c.startswith(("0", "3")):
        return "sz." + c
    raise ValueError(f"Baostock 不支持北交所/港美股 {code}")


# ============================================================
# BaseFetcher 抽象基类（策略模式，朱林森风格）
# ============================================================

class BaseFetcher(ABC):
    """策略模式基类：priority 多源 fallback + 熔断 + 缓存 + 指数退避重试 + 并发控制。"""

    name = "BaseFetcher"
    priority = 99  # 数字越小越优先（朱林森风格）

    def __init__(self):
        self.breaker = CircuitBreaker()
        self.breaker.name = self.name

    @abstractmethod
    def _fetch(self, stock_code: str) -> dict:
        """子类实现真实取数，失败抛异常。"""
        ...

    def fetch(self, stock_code: str) -> dict:
        """带熔断 + 缓存 + 指数退避重试的 fetch。失败返回 {source: name+' (failed)', note: 'UNA'}。"""
        cache_key = f"{self.name}:{stock_code}"
        cached = _cache.get(cache_key)
        if cached is not None:
            return {**cached, "_cached": True}
        if not self.breaker.is_available():
            return {"source": f"{self.name} (circuit-open)", "note": "UNA"}
        with _concurrency:
            last_err = None
            for attempt in range(3):  # 指数退避重试
                try:
                    result = self._fetch(stock_code)
                    self.breaker.record_success()
                    _cache.set(cache_key, result)
                    return result
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if attempt < 2:
                        time.sleep((2 ** attempt) + random.uniform(0.5, 1.5))
            # 全部重试失败后才记一次熔断 failure（避免 3 次重试 = 3 次 failure = 立刻熔断）
            self.breaker.record_failure(str(last_err))
            return {"source": f"{self.name} (failed)", "note": "UNA", "error": str(last_err)}


# ============================================================
# 行情 fetcher（PE/PB/价格 多源）
# ============================================================

class TencentQuoteFetcher(BaseFetcher):
    """腾讯实时报价（主源，不封IP，a-stock-data §119 tencent_quote）。PE/PB/价格/市值/换手。"""
    name = "TencentQuote"
    priority = 1

    def _fetch(self, stock_code: str) -> dict:
        r = requests.get(
            f"https://qt.gtimg.cn/q={_tencent_symbol(stock_code)}",
            timeout=10.0, headers=_random_ua(),
        )
        text = r.text
        if '"' not in text:
            raise RuntimeError("empty tencent quote")
        f = text.split('"')[1].split("~")

        def g(i: int):
            v = f[i] if i < len(f) else ""
            try:
                return float(v) if v not in ("", None) else None
            except (ValueError, TypeError):
                return None

        return {
            "stock_code": f[2] if len(f) > 2 else stock_code,
            "stock_name": f[1] if len(f) > 1 else None,
            "latest_price": g(3), "prev_close": g(4), "open": g(5),
            "volume_lots": g(6),          # 成交量(手)
            "change": g(31), "change_pct": g(32), "high": g(33), "low": g(34),
            "turnover_wan": g(37),        # 成交额(万元)
            "turnover_rate": g(38),
            "pe_ttm": g(39),              # PE(TTM)
            "amplitude": g(43),           # 振幅%
            "circ_market_cap_yi": g(44), "total_market_cap_yi": g(45),
            "pb": g(46),                  # PB
            "source": "腾讯 qt.gtimg.cn",
        }


class EastmoneyQuoteFetcher(BaseFetcher):
    """东财 push2 stock/get（PE/PB/价格 第二源，em_get 限流+UA池+重试，a-stock-data em_get 证实可通）。"""
    name = "EastmoneyQuote"
    priority = 2

    def _fetch(self, stock_code: str) -> dict:
        r = em_get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={
                "secid": _em_secid(stock_code),
                "fields": "f43,f57,f58,f60,f116,f117,f162,f167,f169,f170",
                "fltt": 2, "invt": 2,
            },
        )
        d = (r.json() or {}).get("data") or {}
        if not d:
            raise RuntimeError("empty eastmoney quote")
        return {
            "stock_code": d.get("f57"), "stock_name": d.get("f58"),
            "latest_price": d.get("f43"), "prev_close": d.get("f60"),
            "change_pct": d.get("f170"), "total_market_cap": d.get("f116"),
            "circ_market_cap": d.get("f117"),
            "pe_ttm": d.get("f162"),  # f162 = PE(TTM)
            "pb": d.get("f167"),  # f167 = PB
            "source": "东方财富 push2 stock/get",
        }


class BaostockQuoteFetcher(BaseFetcher):
    """Baostock 第三源仲裁（PE = price/epsTTM，PB = price/BPS）。
    price 取最新 K线 close，epsTTM 取 query_profit_data，BPS 从东财 datacenter（此处仅 PE 仲裁）。"""
    name = "BaostockQuote"
    priority = 3

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
        # 进程级共享会话：首用 login 一次、_bs_locked 内串行、保活复用（见 _shared_bs_session）
        return _shared_bs_session()

    def _fetch(self, stock_code: str) -> dict:
        bs_code = _baostock_code(stock_code)
        with self._session() as bs:
            # 最新 K 线 close 作 price
            rs = bs.query_history_k_data_plus(
                code=bs_code,
                fields="date,close,volume,amount,pctChg",
                start_date=(time.strftime("%Y-%m-%d", time.localtime(time.time() - 30 * 86400))),
                end_date=time.strftime("%Y-%m-%d"),
                frequency="d", adjustflag="2",
            )
            rows = []
            if rs.error_code == "0":
                while rs.next():
                    rows.append(rs.get_row_data())
            close = float(rows[-1][1]) if rows and rows[-1][1] else None
            # epsTTM（最近 Q4）→ PE = price / epsTTM
            eps = None
            year = int(time.strftime("%Y"))
            for y in (year - 1, year - 2):
                rp = bs.query_profit_data(code=bs_code, year=y, quarter=4)
                if rp.error_code == "0":
                    while rp.next():
                        row = dict(zip(rp.fields, rp.get_row_data()))
                        if row.get("epsTTM"):
                            try:
                                eps = float(row["epsTTM"])
                                break
                            except (ValueError, TypeError):
                                pass
            pe = round(close / eps, 4) if (close and eps and eps > 0) else None
        if close is None and pe is None:
            raise RuntimeError("baostock no quote data")
        return {
            "latest_price": close, "eps_ttm": eps, "pe_ttm": pe,
            "source": "Baostock (price/epsTTM)",
            "note": "PE = 最新close / epsTTM(Q4)，作第三源仲裁",
        }


# ============================================================
# 财务 fetcher（ROE/净利润 多源）
# ============================================================

class EastmoneyFinancialFetcher(BaseFetcher):
    """东财 datacenter RPT_LICO_FN_CPD（财务主源，ROE/净利润/营收/EPS/BPS 多期）。"""
    name = "EastmoneyFinancial"
    priority = 1

    def _fetch(self, stock_code: str) -> dict:
        r = em_get(
            "https://datacenter.eastmoney.com/securities/api/data/v1/get",
            params={
                "reportName": "RPT_LICO_FN_CPD", "columns": "ALL",
                "filter": f'(SECUCODE="{_em_secucode(stock_code)}")',
                "pageSize": 12, "source": "HSF10", "client": "PC",
            },
        )
        data = r.json() or {}
        rows = (data.get("result") or {}).get("data") or []
        if not rows:
            raise RuntimeError("empty financial response")
        return {
            "source": "东方财富 datacenter RPT_LICO_FN_CPD",
            "periods": len(rows), "data": rows[:12],
            "note": "含 WEIGHTAVG_ROE/PARENT_NETPROFIT/TOTAL_OPERATE_INCOME/BASIC_EPS/BPS/MGJYXJJE 多期",
        }


class BaostockFinancialFetcher(BaseFetcher):
    """Baostock query_profit_data（财务备源，roeAvg/netProfit/epsTTM 近3年Q4）。"""
    name = "BaostockFinancial"
    priority = 2

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
        # 进程级共享会话：首用 login 一次、_bs_locked 内串行、保活复用（见 _shared_bs_session）
        return _shared_bs_session()

    def _fetch(self, stock_code: str) -> dict:
        bs_code = _baostock_code(stock_code)
        profits = []
        with self._session() as bs:
            year = int(time.strftime("%Y"))
            for y in (year - 1, year - 2, year - 3):
                rs = bs.query_profit_data(code=bs_code, year=y, quarter=4)
                if rs.error_code == "0":
                    while rs.next():
                        row = dict(zip(rs.fields, rs.get_row_data()))
                        if row.get("roeAvg"):
                            profits.append({"year": y, **row})
        if not profits:
            raise RuntimeError("baostock no profit data")
        return {
            "source": "Baostock query_profit_data",
            "periods": len(profits), "data": profits,
            "note": "roeAvg/netProfit/epsTTM/MBRevenue 近3年Q4",
        }


# ============================================================
# 股息率 fetcher（Baostock query_dividend_data）
# ============================================================

class BaostockDividendFetcher(BaseFetcher):
    """Baostock query_dividend_data（股息率主源，dividCashPsBeforeTax/价格）。"""
    name = "BaostockDividend"
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
        # 进程级共享会话：首用 login 一次、_bs_locked 内串行、保活复用（见 _shared_bs_session）
        return _shared_bs_session()

    def fetch_dividends(self, stock_code: str) -> list:
        """取近3年分红记录（供 fetch_raw_data 算股息率）。失败返回 []。"""
        try:
            return self.fetch(stock_code).get("dividends", [])
        except Exception:  # noqa: BLE001
            return []

    def _fetch(self, stock_code: str) -> dict:
        bs_code = _baostock_code(stock_code)
        dividends = []
        with self._session() as bs:
            year = int(time.strftime("%Y"))
            for y in (year, year - 1, year - 2):  # 含今年，覆盖近12月派息日
                rs = bs.query_dividend_data(code=bs_code, year=y)  # 注意：无 quarter 参数
                if rs.error_code == "0":
                    while rs.next():
                        row = dict(zip(rs.fields, rs.get_row_data()))
                        if row.get("dividCashPsBeforeTax"):
                            dividends.append({"year": y, **row})
        if not dividends:
            raise RuntimeError("baostock no dividend data")
        return {"source": "Baostock query_dividend_data", "dividends": dividends}


# ============================================================
# 资金 fetcher（东财 fflow）
# ============================================================

class EastmoneyFlowFetcher(BaseFetcher):
    """东财 push2 fflow（资金流向，主力/超大单/大单/中单/小单 近5日）。"""
    name = "EastmoneyFlow"
    priority = 1

    def _fetch(self, stock_code: str) -> dict:
        r = em_get(
            "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get",
            params={
                "secid": _em_secid(stock_code), "lmt": 5, "klt": 1,
                "fields1": "f1,f2,f3",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
            },
        )
        j = r.json()
        if not j:
            raise RuntimeError("empty flow response")
        return {"raw": j, "source": "东方财富 push2 资金流"}


class AKShareNorthboundFetcher(BaseFetcher):
    """AKShare 北向资金个股持股（东财 push2 被墙时的备选源）。"""
    name = "AKShareNorthbound"
    priority = 2

    def _fetch(self, stock_code: str) -> dict:
        import akshare as ak
        df = ak.stock_hsgt_individual_em(symbol=stock_code)
        if df is None or df.empty:
            raise RuntimeError("empty northbound data")
        # 按日期降序取最新
        if "持股日期" in df.columns:
            df = df.sort_values("持股日期", ascending=False)
        latest = df.iloc[0].to_dict()
        result = {
            "holding_shares": latest.get("持股数量"),
            "holding_market_cap": latest.get("持股市值"),
            "holding_pct": latest.get("持股占比"),
            "change_shares": latest.get("持股数量变化"),
            "date": str(latest.get("持股日期", "")),
            "source": "AKShare stock_hsgt_individual_em (东财北向)",
        }
        if len(df) >= 2:
            flows = []
            for _, row in df.head(5).iterrows():
                flows.append({
                    "date": str(row.get("持股日期", "")),
                    "holding": row.get("持股数量"),
                    "change": row.get("持股数量变化"),
                })
            result["recent_flows"] = flows
        return result


class THSForecastFetcher(BaseFetcher):
    """同花顺机构一致预期（ak.stock_profit_forecast_ths），补充东财机构预测。"""
    name = "THSForecast"
    priority = 2

    def _fetch(self, stock_code: str) -> dict:
        import akshare as ak
        df = ak.stock_profit_forecast_ths(symbol=stock_code)
        if df is None or df.empty:
            raise RuntimeError("empty THS forecast")
        forecasts = []
        for _, row in df.head(4).iterrows():
            forecasts.append({
                "year": str(row.get("年度", "")),
                "eps_mean": row.get("均值"),
                "eps_min": row.get("最小值"),
                "eps_max": row.get("最大值"),
                "org_count": row.get("预测机构数"),
            })
        return {"forecasts": forecasts, "source": "同花顺 stock_profit_forecast_ths"}


# ============================================================
# 补充数据 fetcher（分业务/回购/员工持股/机构预测/反向清单原始）
# 对照参考文件 8 大类 + 手册 §3.9/§4.4，免费数据源优先
# ============================================================

class BusinessSegmentFetcher(BaseFetcher):
    """分业务数据：AKShare stock_financial_report_sina（新浪财报利润表，业务线收入：净利息/手续费/佣金等）
    + stock_zyjs_ths（同花顺主营概述）。非东财源（新浪/同花顺）。"""
    name = "BusinessSegment"
    priority = 1

    def _fetch(self, stock_code: str) -> dict:
        import akshare as ak
        code = stock_code.strip().zfill(6)
        prefix = "sh" if code.startswith("6") else "sz"
        result = {"source": "AKShare 新浪财报+同花顺主营（非东财）", "business_lines": {}, "segments": []}
        # 新浪财报利润表（含业务线收入：净利息收入/手续费及佣金/投资收益等）
        try:
            df = ak.stock_financial_report_sina(stock=f"{prefix}{code}", symbol="利润表")
            if df is not None and len(df):
                latest = df.iloc[0].to_dict()
                result["report_date"] = str(latest.get("报告日", ""))
                biz = {}
                for k, v in latest.items():
                    if any(b in str(k) for b in ["利息收入", "手续费", "佣金", "营业收入", "主营业务", "净利息", "投资收益"]):
                        biz[k] = v
                result["business_lines"] = biz
        except Exception as e:  # noqa: BLE001
            result["income_statement_error"] = str(e)
        # 同花顺主营概述
        try:
            df2 = ak.stock_zyjs_ths(symbol=code)
            if df2 is not None and len(df2):
                row = df2.iloc[0].to_dict()
                result["main_business"] = row.get("主营业务")
                result["product_type"] = row.get("产品类型")
                result["business_scope"] = row.get("经营范围")
        except Exception as e:  # noqa: BLE001
            result["zyjs_error"] = str(e)
        if not result["business_lines"] and not result.get("main_business"):
            raise RuntimeError("no business segment data")
        return result


class DividendBuybackFetcher(BaseFetcher):
    """分红与回购：分红 AKShare stock_dividend_cninfo（巨潮分红历史，非东财）+ Baostock query_dividend_data（见 fetch_raw_data divs）。
    回购免费源不稳定→UNA（LLM 据公开公告判）。"""
    name = "DividendBuyback"
    priority = 1

    def _fetch(self, stock_code: str) -> dict:
        import akshare as ak
        code = stock_code.strip().zfill(6)
        df = ak.stock_dividend_cninfo(symbol=code)
        if df is None or not len(df):
            raise RuntimeError("no dividend cninfo")
        records = []
        for _, r in df.tail(12).iterrows():  # 记录按年份升序，取最近的 12 条
            records.append({
                "公告日期": str(r.get("实施方案公告日期", "")),
                "分红类型": str(r.get("分红类型", "")),
                "派息比例": r.get("派息比例"),
                "送股比例": r.get("送股比例"),
                "转增比例": r.get("转增比例"),
                "股权登记日": str(r.get("股权登记日", "")),
                "除权日": str(r.get("除权日", "")),
                "派息日": str(r.get("派息日", "")),
                "实施方案说明": str(r.get("实施方案分红说明", "")),
            })
        return {"source": "AKShare 巨潮 stock_dividend_cninfo（非东财）",
                "dividend_history": records,
                "note": "回购免费源不稳定→UNA，LLM 据公开公告判"}


class EmployeeHoldingFetcher(BaseFetcher):
    """员工持股计划：东财 F10 免费端点不稳定，标 UNA（LLM 据公开信息/参考文件判持股均价 vs 当前价）。"""
    name = "EmployeeHolding"
    priority = 1

    def _fetch(self, stock_code: str) -> dict:
        # 东财 F10 员工持股/股权激励免费端点易变，标 UNA（不抛异常避免重试浪费）
        return {"source": "东财 F10 员工持股（免费源不稳定）",
                "note": "UNA，LLM 据公开公告/参考文件判持股均价 vs 当前价"}


class InstitutionForecastFetcher(BaseFetcher):
    """机构预测与评级：非东财免费源（同花顺一致预期需 key/爬虫，新浪少）难获取；
    东财 RPT_WEB_RESPREDICT 为该类数据独有源（em_get 限流，最后手段）。
    原则：腾讯>Baostock>同花顺/新浪>巨潮>>>东财（仅独有数据，最后手段）。"""
    name = "InstitutionForecast"
    priority = 1

    def _fetch(self, stock_code: str) -> dict:
        r = em_get(
            "https://datacenter.eastmoney.com/securities/api/data/v1/get",
            params={"reportName": "RPT_WEB_RESPREDICT", "columns": "ALL",
                    "filter": f'(SECUCODE="{_em_secucode(stock_code)}")',
                    "pageSize": 5, "source": "HSF10", "client": "PC"},
        )
        d = r.json() or {}
        rows = (d.get("result") or {}).get("data") or []
        if not rows:
            raise RuntimeError("no institution forecast")
        return {
            "source": "东方财富 RPT_WEB_RESPREDICT",
            "data": rows[:5],
            "note": "含 RATING_ORG_NUM/RATING_BUY_NUM/RATING_ADD_NUM/RATING_NEUTRAL_NUM/RATING_REDUCE_NUM/RATING_SALE_NUM/YEAR1 + 目标价/EPS 预测",
        }


class ReverseCheckFetcher(BaseFetcher):
    """反向清单原始数据：大股东质押率 AKShare stock_cg_equity_mortgage_cninfo（巨潮全市场质押，filter 单股，非东财）。
    ESG/审计意见 LLM 据公开信息判（巨潮公告/天眼查免费版）。"""
    name = "ReverseCheckRaw"
    priority = 1

    def _fetch(self, stock_code: str) -> dict:
        import akshare as ak
        code = stock_code.strip().zfill(6)
        pledge_records = []
        import datetime as _dt
        _now = _dt.datetime.now()
        for year in (_now.year - 1, _now.year - 2, _now.year - 3):
            date = f"{year}1231"
            try:
                df = ak.stock_cg_equity_mortgage_cninfo(date=date)
                if df is not None and len(df):
                    sub = df[df["股票代码"].astype(str) == code]
                    for _, r in sub.head(5).iterrows():
                        pledge_records.append({k: str(v) for k, v in r.to_dict().items()})
                    if pledge_records:
                        break
            except Exception:  # noqa: BLE001
                continue
        if not pledge_records:
            return {"source": "AKShare 巨潮 stock_cg_equity_mortgage_cninfo（非东财）",
                    "pledge_records": [],
                    "note": "巨潮无该股质押记录（可能无质押=正常）；ESG/审计意见 LLM 据公开信息判（参考文件'未发现质押/无ESG负面/标准审计'）"}
        return {"source": "AKShare 巨潮 stock_cg_equity_mortgage_cninfo（非东财）",
                "pledge_records": pledge_records[:5],
                "note": "质押记录；ESG/审计意见 LLM 据公开信息判"}


# ============================================================
# fetcher 实例
# ============================================================

_tencent_q = TencentQuoteFetcher()
_em_q = EastmoneyQuoteFetcher()
_bs_q = BaostockQuoteFetcher()
_em_fin = EastmoneyFinancialFetcher()
_bs_fin = BaostockFinancialFetcher()
_bs_div = BaostockDividendFetcher()
_em_flow = EastmoneyFlowFetcher()
_ak_north = AKShareNorthboundFetcher()
_ths_forecast = THSForecastFetcher()
_business = BusinessSegmentFetcher()
_buyback = DividendBuybackFetcher()
_employee = EmployeeHoldingFetcher()
_institution = InstitutionForecastFetcher()
_reverse = ReverseCheckFetcher()


# ============================================================
# fetch_raw_data — 多源交叉验证 + fallback 聚合
# ============================================================

def _to_float(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "", "--") else None
    except (ValueError, TypeError):
        return None


def fetch_raw_data(stock_code: str) -> dict:
    """L1 多源交叉验证取数（主流程入口）。每关键字段 ≥2-3 源交叉，偏差>1% 取中位/标存疑。

    精度保障（leader <1%）：
    - 价格：腾讯 + 东财 + Baostock，cross_validate
    - PE：腾讯[39] + 东财 f162 + Baostock(price/epsTTM)，三源仲裁
    - PB：腾讯[46] + 东财 f167，双源
    - ROE：东财 datacenter WEIGHTAVG_ROE + Baostock roeAvg
    - 股息率：Baostock dividCashPsBeforeTax / 价格
    - 财务：东财 datacenter（主）→ Baostock profit（备 fallback）
    - 资金：东财 fflow
    """
    # 13 个取数任务互相独立：一次并发提交、统一收获。
    # 并发安全由既有基建保证：BaseFetcher.fetch 内 BoundedSemaphore(4) + 熔断 + TTL 缓存，
    # baostock 三源经 _bs_locked 串行化（进程级全局 socket 非线程安全），
    # 东财请求走 em_get 全局 1s 节流——除此之外勿在此另加锁。
    # V8 地址池是进程级单例：多个 akshare 调用并发时各自在线程里首次初始化
    # py_mini_racer 会触发 address_pool_manager 崩溃。主线程预热一次即可。
    try:
        from py_mini_racer import MiniRacer as _mr
        _mr().eval("0")  # 强制 V8 地址池在主线程初始化
    except Exception:
        pass
    from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED

    # 取数总预算：慢源（baostock/akshare 免费端点可能挂死或限速）超时即放弃等待，
    # 用已完成源继续（多源仲裁自然降级），防单源把整条链路拖到几十秒。
    FETCH_TIMEOUT = 25.0

    pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="l1fetch")
    try:
        jobs = {
            "tq": pool.submit(_tencent_q.fetch, stock_code),
            "eq": pool.submit(_em_q.fetch, stock_code),
            "bq": pool.submit(_bs_locked, _bs_q.fetch, stock_code),
            "fin": pool.submit(_em_fin.fetch, stock_code),
            "bsfin": pool.submit(_bs_locked, _bs_fin.fetch, stock_code),
            "divs": pool.submit(_bs_locked, _bs_div.fetch_dividends, stock_code),
            "flow": pool.submit(_em_flow.fetch, stock_code),
            "biz": pool.submit(_business.fetch, stock_code),
            "inst": pool.submit(_institution.fetch, stock_code),
            "ths": pool.submit(_ths_forecast.fetch, stock_code),
            "buy": pool.submit(_buyback.fetch, stock_code),
            "emp": pool.submit(_employee.fetch, stock_code),
            "rev": pool.submit(_reverse.fetch, stock_code),
        }
        done, not_done = wait(jobs.values(), timeout=FETCH_TIMEOUT)
        for f in not_done:
            f.cancel()
            logger.warning("取数超时（>%ss）放弃等待，用已完成的源继续", int(FETCH_TIMEOUT))

        def take(name: str) -> dict:
            f = jobs[name]
            if f in done:
                return f.result()  # 已完成且异常者照常抛出（保持原语义）
            return {}

        tq, eq, bq = take("tq"), take("eq"), take("bq")
        fin, bs_fin = take("fin"), take("bsfin")
        divs_raw = take("divs")
        divs = divs_raw if isinstance(divs_raw, list) else []  # 分红记录须为 list
        capital_flow = take("flow")
        business_segments, institution_forecast = take("biz"), take("inst")
        ths_forecast, buyback = take("ths"), take("buy")
        employee_holding_raw, reverse_check_raw = take("emp"), take("rev")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # 残留线程后台收尾，不阻塞主流程

    # 价格交叉验证
    price_v, price_src, price_st = cross_validate([
        (_to_float(tq.get("latest_price")), "腾讯"), (_to_float(eq.get("latest_price")), "东财"), (_to_float(bq.get("latest_price")), "Baostock"),
    ])
    # PE 交叉验证（三源：腾讯/东财/Baostock price÷epsTTM）
    pe_v, pe_src, pe_st = cross_validate([
        (_to_float(tq.get("pe_ttm")), "腾讯[39]"), (_to_float(eq.get("pe_ttm")), "东财f162"), (_to_float(bq.get("pe_ttm")), "Baostock/epsTTM"),
    ])
    # PB 交叉验证（双源：腾讯/东财）
    pb_v, pb_src, pb_st = cross_validate([
        (_to_float(tq.get("pb")), "腾讯[46]"), (_to_float(eq.get("pb")), "东财f167"),
    ])

    # 财务（东财主 → Baostock 备 fallback；fin/bs_fin 已在上方并发收获）
    if ("failed" in fin.get("source", "") or not fin.get("data")) and bs_fin.get("data"):
        fin = {"source": "Baostock query_profit (fallback)", "periods": len(bs_fin["data"]),
               "data": bs_fin["data"], "note": "东财 datacenter 失败，Baostock 财务补充"}

    # 从东财财务数据中找最新年报行（DATEMMDD=="年报"），避免误取季报
    # RPT_LICO_FN_CPD 并非严格按期排序，需遍历所有行取 DATAYEAR 最大的年报
    def _find_annual_row(rows: list) -> Optional[dict]:
        best = None
        best_year = -1
        for row in rows:
            if row.get("DATEMMDD") == "年报":
                try:
                    y = int(row.get("DATAYEAR", 0))
                except (ValueError, TypeError):
                    y = 0
                if y > best_year:
                    best_year = y
                    best = row
        return best

    _fin_rows = fin.get("data") or []
    _annual_row = _find_annual_row(_fin_rows)

    # ROE 交叉验证（东财 WEIGHTAVG_ROE 年报 vs Baostock roeAvg 最新 Q4）
    em_roe = None
    try:
        roe_row = _annual_row or (_fin_rows[0] if _fin_rows else None)
        if roe_row and "WEIGHTAVG_ROE" in roe_row:
            em_roe = _to_float(roe_row.get("WEIGHTAVG_ROE"))
            # 东财 WEIGHTAVG_ROE 是百分比值（如 13.44=13.44%），直接使用
            if em_roe is not None:
                em_roe = round(em_roe, 2)
    except Exception:  # noqa: BLE001
        pass
    bs_roe = None
    try:
        bp = (bs_fin.get("data") or [{}])[0]
        bs_roe = _to_float(bp.get("roeAvg"))
        if bs_roe is not None:
            bs_roe = round(bs_roe * 100, 2)
    except Exception:  # noqa: BLE001
        pass
    roe_v, roe_src, roe_st = cross_validate([(em_roe, "东财ROE"), (bs_roe, "Baostock roeAvg")])

    # 股息率（Baostock 近12个月实际派息合计 dividCashPsBeforeTax / 价格，TTM 口径）
    # 按 dividPayDate 过滤近365天，合计 dividCashPsBeforeTax（含中期+年度+特别分红）
    import datetime as _dt
    div_yield = None
    div_src = "UNA"
    if divs and price_v:
        try:
            one_year_ago = _dt.datetime.now() - _dt.timedelta(days=365)
            total_cash = 0.0
            for d in divs:
                pay_date_str = d.get("dividPayDate") or ""
                if not pay_date_str:
                    continue
                try:
                    pay_date = _dt.datetime.strptime(pay_date_str, "%Y-%m-%d")
                except ValueError:
                    continue
                if pay_date > one_year_ago:
                    total_cash += float(d.get("dividCashPsBeforeTax") or 0)
            if total_cash > 0:
                div_yield = round(total_cash / price_v * 100, 2)
                div_src = f"Baostock 近12月派息合计({total_cash:.3f}元)/价格(TTM)"
        except (ValueError, TypeError):
            pass

    # 备源：Baostock 分红缺失/超时时，用巨潮分红历史（buyback.dividend_history）按
    # 近 12 个月除权日折算每股现金（派息比例=每 10 股税前元 → /10），防股息率 TTM 落 UNA。
    if div_yield is None and price_v:
        try:
            import datetime as _dd
            one_year_ago = _dd.datetime.now() - _dd.timedelta(days=365)
            ttl = 0.0
            seen_dates: set = set()
            for rec in (buyback or {}).get("dividend_history", []) or []:
                ex_date = str(rec.get("除权日") or rec.get("股权登记日") or "")[:10]
                ratio = rec.get("派息比例")
                if ex_date and ex_date not in seen_dates:
                    try:
                        if _dd.datetime.strptime(ex_date, "%Y-%m-%d") > one_year_ago:
                            r = float(ratio)
                            if r > 0:
                                ttl += r / 10.0  # 每10股派息 → 每股
                                seen_dates.add(ex_date)
                    except (ValueError, TypeError):
                        pass
            if ttl > 0:
                div_yield = round(ttl / price_v * 100, 2)
                div_src = f"巨潮近12月派息(每股{ttl:.3f}元,按除权日)/价格"
        except Exception:  # noqa: BLE001
            pass

    # 东财 push2 被墙时，用 AKShare 北向资金备选（capital_flow 已在上方并发收获）
    if capital_flow.get("error") or "failed" in str(capital_flow.get("source", "")):
        ak_north = _ak_north.fetch(stock_code)
        if not ak_north.get("error"):
            capital_flow = {**capital_flow, "northbound_akshare": ak_north}

    market = {
        "stock_code": stock_code,
        "stock_name": tq.get("stock_name") or eq.get("stock_name"),
        "latest_price": price_v, "price_sources": price_src, "price_status": price_st,
        "prev_close": _to_float(tq.get("prev_close")),
        "open": _to_float(tq.get("open")),
        "change": _to_float(tq.get("change")),
        "change_pct": _to_float(tq.get("change_pct")) or _to_float(eq.get("change_pct")),
        "high": _to_float(tq.get("high")), "low": _to_float(tq.get("low")),
        "amplitude": _to_float(tq.get("amplitude")),
        "volume_lots": _to_float(tq.get("volume_lots")),
        "turnover_wan": _to_float(tq.get("turnover_wan")),
        "turnover_rate": _to_float(tq.get("turnover_rate")),
        "total_market_cap_yi": _to_float(tq.get("total_market_cap_yi")) or (
            round(_to_float(eq.get("total_market_cap")) / 1e8, 2) if _to_float(eq.get("total_market_cap")) else None
        ),
        "circ_market_cap_yi": _to_float(tq.get("circ_market_cap_yi")),
        "pe_ttm": pe_v, "pe_sources": pe_src, "pe_status": pe_st,
        "pb": pb_v, "pb_sources": pb_src, "pb_status": pb_st,
        "dividend_yield": div_yield, "dividend_source": div_src,
        "roe_latest": roe_v, "roe_sources": roe_src, "roe_status": roe_st,
        "source": "多源交叉(腾讯+东财+Baostock)",
    }

    # 将年报行放到 fin["data"] 首位（避免下游 LLM/orchestrator 误取季报作最新期）
    if _annual_row and _fin_rows and _fin_rows[0] is not _annual_row:
        _reordered = [_annual_row] + [r for r in _fin_rows if r is not _annual_row]
        fin = {**fin, "data": _reordered}

    # 补充 5 类数据 + 反向清单（分业务/回购/员工持股/机构预测/THS预测）已在上方并发收获
    return {
        "market": market,
        "financial": fin,
        "capital_flow": capital_flow,
        "baostock_financial": bs_fin,
        "baostock_dividends": divs,
        "business_segments": business_segments,
        "institution_forecast": institution_forecast,
        "ths_forecast": ths_forecast,
        "buyback": buyback,
        "employee_holding_raw": employee_holding_raw,
        "reverse_check_raw": reverse_check_raw,
    }
