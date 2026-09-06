"""FastAPI app — A股投资决策系统后端

POST /api/analyze：接受 {stock_code: str}，调用 orchestrator 编排 L1→L2→L3，返回 StockDecisionCard。

自定义 OpenAI 兼容端点与 API 模式配置统一收敛在 config.py（import 即触发全局
set_default_openai_*，含 LLM_API_MODE 开关）。原先在此内联的一份是幂等双写，已删除，
只保留对 config 的 import——端点/模式单一真相源在 config.py。

启动：
  cp .env.example .env  # 填入真实 key
  uv run uvicorn main:app --reload --port 8000
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse

import report_service

# 必须在创建 app / agent 之前触发端点与 API 模式的全局配置（副作用在 config 模块体内）。
from config import LLM_DEEP_THINK, has_api_key  # import config 模块即完成 set_default_openai_client/api + tracing
from orchestrator import run_analysis
from schemas import AnalyzeRequest, StockDecisionCard


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not has_api_key():
        print("⚠️  OPENAI_API_KEY 未设置：POST /api/analyze 将返回 500。请在 backend/.env 配置（OpenAI 兼容端点）。")
    yield


app = FastAPI(title="A股投资决策系统 API", version="0.1.0", lifespan=lifespan)
# 前后端联调用：开发期仅允许 Vite 前端
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def health():
    return {
        "status": "ok",
        "service": "guardrail-backend",
        "openai_key_set": has_api_key(),
    }


@app.get("/api/config")
def app_config():
    """前端运行配置：deep_think_default=后端全局默认深度思考状态（供前端勾选框回显联动）。"""
    return {"deep_think_default": bool(LLM_DEEP_THINK)}


@app.post("/api/analyze", response_model=StockDecisionCard)
async def analyze(req: AnalyzeRequest):
    """分析股票，返回决策卡。"""
    try:
        card = await run_analysis(req.stock_code, force_refresh=req.force_refresh)
        return card
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"分析失败：{e!r}")


# ===== SSE 流式端点：逐区块推送 =====
def _sse(event: str, data) -> str:
    """格式化 SSE 事件块（event + data + 空行）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _analyze_stream_gen(stock_code: str, force_refresh: bool = False):
    """SSE async generator（含边界处理：connected/heartbeat/超时/空数据/断连）。

    阶段1：fetch_raw_data（代码层 ~5s）→ connected + data_trace 预览（前端即时看到 PE/PB/价格/股息率+多源状态）
    阶段2：orchestrator 完整分析 + 报告生成（校验→修复→兜底），期间每 15s 发 heartbeat 心跳防代理超时断连；整体 300s 超时
    阶段3：逐区块 yield 16 区块（含 header）+ done（完整 JSON）

    force_refresh: 清除 TTL 缓存，强制重新取数。
    """
    import asyncio as _aio
    import time as _time
    from decision_agents.data_fetcher import fetch_raw_data

    # 解析股票名称/代码（用户可能输入"中国平安"而非 601318）
    try:
        from orchestrator import resolve_stock_query
        resolved_code, resolved_name = await resolve_stock_query(stock_code)
    except Exception as e:  # noqa: BLE001
        yield _sse("error", {"message": str(e)})
        return

    yield _sse("connected", {
        "stock_code": resolved_code,
        "stock_name": resolved_name,
        "ts": _time.time(),
    })

    # force_refresh: 清除该股票的 TTL 缓存
    if force_refresh:
        from decision_agents.data_fetcher import _cache
        _cache.invalidate_code(resolved_code)

    start = _time.time()  # 整体计时起点（取数 + 分析共用 180s 总超时）

    # 阶段1：代码层取数（asyncio.to_thread 不阻塞事件循环）+ 心跳 + 超时
    fetch_task = _aio.create_task(_aio.to_thread(fetch_raw_data, resolved_code))
    raw = {}
    fetch_error = None
    try:
        while not fetch_task.done():
            elapsed = _time.time() - start
            if elapsed > 300:
                fetch_task.cancel()
                yield _sse("error", {"message": f"整体超时（300s，取数阶段 elapsed={elapsed:.0f}s）"})
                return
            yield _sse("heartbeat", {"ts": _time.time(), "elapsed": int(elapsed), "phase": "fetch"})
            _done, _ = await _aio.wait({fetch_task}, timeout=15)
            if _done:
                break
        raw = fetch_task.result() or {}
    except Exception as e:  # noqa: BLE001
        fetch_error = e
        raw = {}

    # data_trace 预览（空数据/失败降级）
    if fetch_error:
        yield _sse("data_trace", {
            "stock_code": resolved_code, "quote": {},
            "source_summary": {"credibility": "不可用", "completeness": "严重不足"},
            "raw_sources": {"market": f"failed: {fetch_error!r}"},
            "note": "取数异常，UNA",
        })
    m = raw.get("market", {}) if isinstance(raw, dict) else {}
    fin_src = raw.get("financial", {}).get("source") if isinstance(raw, dict) else None
    cf_src = raw.get("capital_flow", {}).get("source") if isinstance(raw, dict) else None
    if not m or m.get("latest_price") is None:
        # 空数据：行情取数失败，data_trace 标 UNA 但继续（orchestrator 据可用数据分析）
        yield _sse("data_trace", {
            "stock_code": resolved_code, "quote": {},
            "source_summary": {"credibility": "不可用", "completeness": "严重不足"},
            "raw_sources": {"market": (m or {}).get("source", "failed"),
                            "financial": fin_src or "failed", "capital_flow": cf_src or "failed"},
            "note": "行情取数失败，UNA；orchestrator 将据可用数据分析",
        })
    else:
        yield _sse("data_trace", {
            "stock_code": resolved_code, "stock_name": m.get("stock_name") or resolved_name,
            "quote": {"latest_price": m.get("latest_price"), "pe_ttm": m.get("pe_ttm"),
                      "pb": m.get("pb"), "dividend_yield": m.get("dividend_yield")},
            "source_summary": {"price_status": m.get("price_status"), "pe_status": m.get("pe_status"),
                               "pb_status": m.get("pb_status"), "dividend_source": m.get("dividend_source"),
                               "credibility": "中", "completeness": "基本完整"},
            "raw_sources": {"market": m.get("source"), "financial": fin_src, "capital_flow": cf_src},
            "note": "代码层预览，完整 DataTrace（含 derived/fcf_history/industry_specific）见 done",
        })

    # 阶段2：orchestrator 完整分析 + heartbeat 心跳（防超时断连）
    # 注：start 已在阶段1设为整体计时起点，180s 为取数+分析总超时
    task = _aio.create_task(run_analysis(resolved_code, force_refresh=force_refresh))
    card = None
    try:
        while not task.done():
            elapsed = _time.time() - start
            if elapsed > 480:  # 300→480：2026-09-05 网关高峰大请求实测 44s→181s→挂死递变，
                task.cancel()  # 给流式+fallback 各留一个完整尝试窗口；低峰回落后不会触顶
                yield _sse("error", {"message": f"分析超时（480s，elapsed={elapsed:.0f}s）"})
                return
            yield _sse("heartbeat", {"ts": _time.time(), "elapsed": int(elapsed)})
            _done, _ = await _aio.wait({task}, timeout=15)
            if _done:
                break
        card = task.result()
    except _aio.CancelledError:
        yield _sse("error", {"message": "分析被取消"})
        return
    except Exception as e:  # noqa: BLE001
        yield _sse("error", {"message": f"分析失败：{e!r}"})
        return

    if card is None:
        yield _sse("error", {"message": "分析未产出结果"})
        return

    # 阶段3：逐区块 yield（含完整 data_trace 覆盖预览）
    d = card.model_dump(mode="json")
    yield _sse("header", d["header"])
    # 重新发送完整 data_trace（含代码层 backfill 的财务/分红/分业务等）
    yield _sse("data_trace", d["data_trace"])
    for block in ["hard_gate", "reverse_check", "five_signals", "value_trap",
                  "quant_enhance", "debate", "factor_validation", "capital_profile",
                  "stress_test", "behavior_check", "final_decision", "anchors"]:
        yield _sse(block, d[block])
    yield _sse("exit_conditions", d["exit_conditions"])
    yield _sse("risks", d["risks"])
    yield _sse("done", d)


@app.get("/api/analyze/stream")
async def analyze_stream(stock_code: str):
    """SSE 流式（GET，EventSource 兼容，query 参数 stock_code）。逐区块推送。

    前端 EventSource 示例：
      const es = new EventSource('/api/analyze/stream?stock_code=600036');
      es.addEventListener('data_trace', e => console.log(JSON.parse(e.data)));
      es.addEventListener('done', e => { console.log(JSON.parse(e.data)); es.close(); });
      es.addEventListener('error', e => console.error(e));
    """
    if not has_api_key():
        return StreamingResponse(
            iter([_sse("error", {"message": "OPENAI_API_KEY 未设置"})]),
            media_type="text/event-stream",
        )
    return StreamingResponse(_analyze_stream_gen(stock_code), media_type="text/event-stream")


@app.post("/api/analyze/stream")
async def analyze_stream_post(req: AnalyzeRequest):
    """SSE 流式（POST，fetch ReadableStream，JSON body {stock_code}）。GET 版本保留兼容。

    SSE 事件格式（与 GET 一致）：
      event: connected
      data: {"stock_code":...}
      event: data_trace
      data: {"source_summary":..., "quote":...}
      event: heartbeat
      data: {"ts":..., "elapsed":...}
      event: hard_gate / reverse_check / five_signals / ... / risks
      data: {...}
      event: done
      data: {完整 StockDecisionCard JSON}
      event: error
      data: {"message":...}

    前端 fetch ReadableStream 示例：
      const resp = await fetch('/api/analyze/stream', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({stock_code: '600036'}),
      });
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buf += decoder.decode(value, {stream: true});
        // 按 "\\n\\n" 分割 SSE 事件块，解析 event/data
      }
    """
    if not has_api_key():
        return StreamingResponse(
            iter([_sse("error", {"message": "OPENAI_API_KEY 未设置"})]),
            media_type="text/event-stream",
        )
    return StreamingResponse(
        _analyze_stream_gen(req.stock_code, force_refresh=req.force_refresh),
        media_type="text/event-stream",
    )


# ===== 看板直出管线：流式分析 + 历史（decision-card-dashboard 风格前端专用） =====


@app.post("/api/report/stream")
async def report_stream(req: AnalyzeRequest):
    """去卡直出报告 SSE：connected → phase → section_done×N（逐区块点亮看板）→
    heartbeat → report_saved → done（权威 DashboardData）。body {stock_code, force_refresh}。

    与 /api/analyze/stream（决策卡 JSON 管线）并存互不影响；新前端只用本端点。
    """
    if not has_api_key():
        return StreamingResponse(
            iter([_sse("error", {"message": "OPENAI_API_KEY 未设置", "code": "config"})]),
            media_type="text/event-stream",
        )
    return StreamingResponse(
        (_sse(e, d) async for e, d in report_service.report_stream_gen(
            req.stock_code, force_refresh=req.force_refresh, deep_think=req.deep_think)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/reports")
def reports_list():
    """历史报告列表（判据=目录内有 dashboard.json），按更新时间降序。"""
    return {"reports": report_service.list_reports()}


@app.get("/api/reports/{code}")
def reports_detail(code: str):
    """看板 DashboardData 全文（点开秒渲染，不再跑 LLM）。"""
    dash = report_service.load_dashboard(code)
    if dash is None:
        raise HTTPException(status_code=404, detail=f"{code} 无看板数据，可发起新分析")
    return dash


@app.get("/api/reports/{code}/report.md", response_class=PlainTextResponse)
def reports_markdown(code: str):
    """报告 MD 原文（看板数字核验入口，与 dashboard.json 同目录）。"""
    md = report_service.load_markdown(code)
    if md is None:
        raise HTTPException(status_code=404, detail=f"{code} 无报告原文")
    return md


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
