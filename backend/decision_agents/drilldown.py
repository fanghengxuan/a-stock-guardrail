"""阶段2：下钻 Agent 循环（≤6轮）+ 快照。定性判断留给 LLM，定量取数归工具。

- LLM 基于 build_stage1_summary 的阶段一摘要，按需调用 7 个下钻工具补缺口；
- 快照 analysis_reports/<code>-<name>/tool_trace.json，TTL 内复用不重取；
- 银行/保险标的的资产质量是模型四必需输入，代码层强制取，不依赖 LLM 决策；
- 未调用/请求失败归一为 {"status":"UNA"}（确定性无数据保留原结果，含 source）；
  下钻整体失败/超时不阻断主流程；
- 取数反哺（任务6b）：每次 fetch 按路由记战绩到 data_routes_state.json，多路由工具按权重序
  降级、连败≥3 自动退役；LLM 回报的 new_route 过两道机械闸门后自动入表。

锁序契约（任务4裁决）：SDK 对同步 function_tool 走 asyncio.to_thread、同轮多调用可并发，
故 _call 内持 _tool_lock 串行化全部 fetch，保证 KLine52W 的 _concurrency→_bs_lock 锁序
永不成环（等价于单线程顺序执行）。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
import threading
import time

from agents import Agent, ModelSettings, Runner, WebSearchTool, function_tool
from agents.run_config import RunConfig, ToolExecutionConfig

from config import LLM_API_MODE, LLM_MODEL, LLM_REASONING
from decision_agents.route_registry import (
    ordered_routes, record_success, record_failure, retire_if_dead,
    route_health_summary, try_auto_add_route)
from decision_agents.drilldown_tools import (
    MarginFetcher, BankQualityFetcher, NewsFetcher, IndustryValuationFetcher,
    KLine52WFetcher, AnnouncementFetcher, ResearchReportFetcher,
    filter_negative_announcements)

MAX_ROUNDS = 6
TTL = 1200.0
_REPORT_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "analysis_reports"  # 仓库根

# 工具名 → extra_data 键（任务 7 消费的契约键名）
_TOOL_DATA_KEY = {
    "get_bank_asset_quality": "bank_quality",
    "get_margin_balance": "margin",
    "get_news_sentiment": "news",
    "get_industry_valuation": "industry_valuation",
    "get_kline_52w": "kline_52w",
    "get_announcements": "announcements",
    "get_research_reports": "research_reports",
}
EXTRA_KEYS = tuple(_TOOL_DATA_KEY.values())

# 工具名 → data_routes.md 节名（台账/路由解析键）
_TOOL_ROUTE_CATEGORY = {
    "get_bank_asset_quality": "银行资产质量",
    "get_margin_balance": "融资融券",
    "get_news_sentiment": "新闻舆情",
    "get_industry_valuation": "行业估值",
    "get_kline_52w": "52周高低",
    "get_announcements": "公告",
    "get_research_reports": "研报",
}
# fetch() 带 route 入参、需按 ordered_routes 逐条降级的工具；其余单路由固定记 P1
_MULTI_ROUTE_TOOLS = frozenset({"get_margin_balance", "get_kline_52w", "get_announcements"})

# 串行化全部工具 fetch（见模块 docstring 锁序契约；与 run_config 并发=1 双保险）
_tool_lock = threading.Lock()


def _route_failed(r: dict) -> bool:
    """失败判定三形态（任务4/5返回契约 + 基类熔断打开）：error / status=UNA / note=UNA。"""
    return bool(r.get("error")) or r.get("status") == "UNA" or r.get("note") == "UNA"


def _record_route(category: str, route_id: str, r: dict) -> None:
    """取数结果反哺台账：失败计连败并触发自动退役检查，成功计连胜。"""
    if _route_failed(r):
        record_failure(category, route_id, str(r.get("error") or "UNA"))
        retire_if_dead(category, route_id)
    else:
        record_success(category, route_id)


def _is_spec_route(rt: dict) -> bool:
    """spec 形态路由：path 以 tool= 开头（自动入表的 LLM 侧工具/试用路由，如 web_search）。
    这类由 LLM 经 route_health_summary 感知后自行调用，_call 无法代执行（裁决 A）。"""
    return str(rt.get("path", "")).startswith("tool=")


def _multi_route_fetch(inst, category: str, stock_code: str) -> tuple:
    """按权重序逐条降级尝试真实 fetcher 路由，返回 (result, 最后尝试的 route_id)。
    跳过 spec 形态路由（裁决 A）：web_search 等 LLM 侧工具 _call 不能代跑，
    硬走 fetcher 会命中 _no_route 给幻影路由记连败 → 误退役。"""
    r = None
    rid = "P1"
    for rt in ordered_routes(category):  # 权重序逐条降级
        if _is_spec_route(rt):
            continue
        rid = rt["id"]
        r = inst.fetch(stock_code, route=rid)
        _record_route(category, rid, r)  # 战绩反哺 + 连败≥3 自动退役
        if not _route_failed(r):
            break
    else:  # 全部真实路由失败，或该类目已无真实路由（仅剩 spec）
        r = {"status": "UNA",
             "error": f"全部路由失败: {r if r is not None else f'{category} 无活动路由'}"}
    return r, rid


def _json_out(r: dict) -> str:
    """工具结果 → LLM 可见 JSON：失败/无数据归一为 UNA 提示，截断 8000 字符。"""
    failed = bool(r.get("error")) or r.get("status") == "UNA"
    payload = {"status": "UNA", "note": "取数失败或无数据"} if failed else r
    return json.dumps(payload, ensure_ascii=False, default=str)[:8000]


def is_bank_or_insurance(name: str) -> bool:
    return any(k in (name or "") for k in ("银行", "保险", "人寿", "平安"))


def build_stage1_summary(base_raw: dict) -> str:
    """阶段一原始数据 → 紧凑 JSON 摘要（<2KB），供下钻 Agent 判断还缺什么。只挑关键字段，不全量 dump。"""
    raw = base_raw or {}
    m = raw.get("market") or {}
    rows = (raw.get("financial") or {}).get("data") or []
    latest = rows[0] if rows and isinstance(rows[0], dict) else {}  # 年报行已被 fetch_raw_data 置于首位
    cf = raw.get("capital_flow") or {}
    klines = (((cf.get("raw") or {}).get("data") or {}).get("klines")) or []
    rc = raw.get("reverse_check_raw") or {}
    pledges = [{str(k)[:20]: str(v)[:30] for k, v in list(rec.items())[:8]}
               for rec in (rc.get("pledge_records") or [])[:2]]  # 截断防摘要膨胀
    summary = {
        "price": m.get("latest_price"),
        "pe_ttm": m.get("pe_ttm"),
        "pb": m.get("pb"),
        "dividend_yield": m.get("dividend_yield"),
        "roe_latest": m.get("roe_latest"),
        "latest_annual": {k: latest.get(k) for k in
                          ("REPORT_DATE", "WEIGHTAVG_ROE", "PARENT_NETPROFIT", "TOTAL_OPERATE_INCOME")},
        "capital_flow": {"source": cf.get("source"), "recent": klines[-2:]},
        "pledge_records": pledges,
    }
    return json.dumps(summary, ensure_ascii=False, default=str)[:1900]  # 硬上限，保证 <2KB


def write_trace(path, payload: dict) -> None:
    """快照落盘：补 fetched_at、父目录 mkdir、原子写（临时文件+replace）。"""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"fetched_at": time.time(), **payload}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def read_fresh_trace(path, ttl_seconds: float = TTL) -> dict | None:
    """读快照；不存在/损坏/ fetched_at 超 ttl → None。"""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "extra_data" not in data:  # 消费方直接取 fresh["extra_data"]
        return None
    try:
        age = time.time() - float(data.get("fetched_at") or 0)
    except (TypeError, ValueError):
        return None
    return data if age <= ttl_seconds else None


def _build_agent(recorder: list, results: dict) -> Agent:
    """7 个工具闭包：执行 fetch、把 {tool,args,elapsed_ms} 记入 recorder、结果按数据键存 results。"""
    def _wrap(tool_name: str, fetcher_cls, post=None):
        data_key = _TOOL_DATA_KEY[tool_name]
        category = _TOOL_ROUTE_CATEGORY[tool_name]
        multi_route = tool_name in _MULTI_ROUTE_TOOLS
        inst = fetcher_cls()

        def _call(stock_code: str) -> str:
            if data_key in results:  # 去重：每工具至多取一次，重复调用回放缓存不再触网
                return _json_out(results[data_key])
            t = time.time()
            rid = "P1"
            with _tool_lock:  # 单线程顺序契约：禁止并发 fetch（KLine 锁序）
                if multi_route:
                    r, rid = _multi_route_fetch(inst, category, stock_code)  # 降级跳过 spec 路由（裁决 A）
                else:
                    r = inst.fetch(stock_code)  # 单路由工具固定记 P1 战绩
                    _record_route(category, "P1", r)
            if post:
                r = post(r)
            recorder.append({"tool": tool_name, "route": rid, "args": stock_code,
                             "elapsed_ms": int((time.time() - t) * 1000)})
            results[data_key] = r
            return _json_out(r)

        _call.__name__ = tool_name
        _call.__doc__ = f"获取{tool_name}数据（真实数据源，取不到返回 status=UNA）。"
        return function_tool(_call)

    tools = [
        _wrap("get_bank_asset_quality", BankQualityFetcher),
        _wrap("get_margin_balance", MarginFetcher),
        _wrap("get_news_sentiment", NewsFetcher),
        _wrap("get_industry_valuation", IndustryValuationFetcher),
        _wrap("get_kline_52w", KLine52WFetcher),
        _wrap("get_announcements", AnnouncementFetcher,
              post=lambda r: {**r, "negative": filter_negative_announcements(r.get("items", []))}),
        _wrap("get_research_reports", ResearchReportFetcher),
    ]
    # WebSearchTool 是 Responses API hosted tool：chat_completions 下 SDK 直接抛 UserError
    # （chatcmpl_converter 拒非 function 工具），故仅 responses 模式挂载——应急回滚须连带禁用联网搜索。
    websearch_on = LLM_API_MODE == "responses"
    if websearch_on:
        tools.append(WebSearchTool())
    websearch_hint = ("公告解读/ESG 负面/舆情等定性问题可用内置联网搜索；定量数据必须用专用工具。\n"
                      if websearch_on else "")
    agent = Agent(name="drilldown", model=LLM_MODEL, tools=tools,
                  model_settings=ModelSettings(reasoning=LLM_REASONING),
                  instructions=(
        "你是取数下钻助手。基于阶段一摘要判断还缺哪些数据，调用工具补齐；每个工具最多调一次；"
        "与决策无关的数据不要取。完成后仅输出 JSON：{\"done\": true}。不得编造数据。\n"
        + websearch_hint +
        f"数据路由健康台账：\n{route_health_summary()}\n"
        "优先按路由表的权重序取数；标失效的路由不要走；若你发现新可用路径，在最终输出中附 "
        "{\"new_route\": \"<数据类>:<路径描述>\"}，描述必须写成 tool=<已注册工具id>;variant=<参数> "
        "或 tool=web_search;query=<查询模板> 形式。"))
    return agent


def _extract_proposed_routes(final_output) -> list:
    """LLM 最终输出若含 new_route 键（发现的新可用取数路径），提取留档进快照 proposed_routes。"""
    text = str(final_output or "")
    candidates = [text]
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        candidates.append(m.group(0))
    for c in candidates:
        try:
            j = json.loads(c)
        except ValueError:
            continue
        if isinstance(j, dict) and j.get("new_route"):
            nr = j["new_route"]
            return nr if isinstance(nr, list) else [nr]
    return []


def _normalized_extra(results: dict) -> dict:
    """全部 7 个数据键进 extra_data；未调用/请求失败 → {"status":"UNA"}；
    确定性无数据保留原结果（含 source，便于下游区分"没查到"与"没查"）。"""
    extra = {}
    for key in EXTRA_KEYS:
        r = results.get(key)
        extra[key] = r if isinstance(r, dict) and not r.get("error") else {"status": "UNA"}
    return extra


async def run_drilldown(code: str, name: str, base_raw: dict,
                        force_refresh: bool = False) -> tuple[dict, pathlib.Path]:
    """阶段2下钻：快照复用 → 银行强制取 → LLM 循环补缺 → 写快照。返回 (extra_data, trace_path)。"""
    trace_path = _REPORT_DIR / f"{code}-{name}".rstrip("-") / "tool_trace.json"
    if not force_refresh:
        fresh = read_fresh_trace(trace_path)
        if fresh:
            return fresh["extra_data"], trace_path

    recorder: list = []
    results: dict = {}
    if is_bank_or_insurance(name):  # 银行/保险：资产质量为模型四必需输入，代码强制取（LLM 再调走缓存）
        t = time.time()
        r = await asyncio.to_thread(BankQualityFetcher().fetch, code)
        _record_route(_TOOL_ROUTE_CATEGORY["get_bank_asset_quality"], "P1", r)
        results["bank_quality"] = r
        recorder.append({"tool": "get_bank_asset_quality", "route": "P1", "args": code,
                         "elapsed_ms": int((time.time() - t) * 1000)})

    agent = _build_agent(recorder, results)
    prompt = (f"标的：{code} {name}\n阶段一已取数据摘要：\n{build_stage1_summary(base_raw)}\n"
              f"已额外取：{list(results)}。按需下钻其余数据。")
    proposed: list = []
    # max_function_tool_concurrency=1：SDK 同轮多 tool_call 也串行执行，守住锁序契约
    rc = RunConfig(tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1))
    try:
        res = await asyncio.wait_for(Runner.run(agent, prompt, max_turns=MAX_ROUNDS, run_config=rc),
                                     timeout=90)
        proposed = _extract_proposed_routes(res.final_output)
    except Exception:  # noqa: BLE001  下钻失败不阻断主流程，缺口标 UNA
        pass

    payload = {"rounds": len(recorder), "calls": list(recorder),
               "extra_data": _normalized_extra(results)}
    if proposed:
        payload["proposed_routes"] = _apply_proposed_routes(proposed)
    write_trace(trace_path, payload)
    return payload["extra_data"], trace_path


def _apply_proposed_routes(proposed: list) -> list:
    """LLM 回报的 new_route 逐条过两道机械闸门自动入表（无人工确认）。
    闸门2 的"每次运行 ≤1 条"上限在此把关（≤5 条/类在 try_auto_add_route 内）；被拒条目仍留档。"""
    out: list = []
    added_this_run = False
    for p in proposed:
        p = str(p).strip()
        category, sep, spec = p.partition(":")
        if not sep or not spec.strip():
            out.append({"route": p, "added": False, "reason": "格式非 <数据类>:<路径描述>"})
            continue
        if added_this_run:
            out.append({"route": p, "added": False, "reason": "本次运行自动入表已达 1 条上限"})
            continue
        ok = try_auto_add_route(category.strip(), spec.strip())
        out.append({"route": p, "added": ok,
                    "reason": "试用期入表（权重基线60，首次成功转正）" if ok
                    else "闸门拒绝：工具未注册/描述格式不符或该类活动路由已满"})
        added_this_run = ok
    return out
