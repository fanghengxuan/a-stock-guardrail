"""去卡直出报告的服务层：流式分析（SSE 逐区块点亮）+ dashboard.json 落盘 + 历史扫描。

事件以 (name, data) 元组产出，由 main.py 的 _sse() 统一格式化。
超时/并发口径与 main._analyze_stream_gen 对齐：总窗 480s、心跳 15s、
流式静默 240s 熔断——判据是 token 流真实字节零增长（stats["md_bytes"]），
**不是队列无事件**：单节（大表格/长文）写作可超 240s，事件只随闭合产生，误用会假杀活流。
DeepSeek 官方端点有积压史（非流式挂死、流式可幸存），本模块所有 LLM 接触面
只有 run_report_direct 一次流式调用，看板字段全部代码解析。
并发去重为进程内 set（start.sh 单实例 uvicorn 成立；多进程部署时需换文件锁）。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
import time
from typing import AsyncIterator

from dashboard.parser import build_blocks, build_dashboard
from dashboard.scanner import Section
from scripts.report_direct import run_report_direct

REPORTS_ROOT = pathlib.Path(__file__).resolve().parent.parent / "analysis_reports"  # 仓库根（与 backend 同级）

TOTAL_TIMEOUT = 480.0     # 取数+下钻+直写总窗（沿用 main.py 已验证值）
STALL_TIMEOUT = 240.0     # 流式静默熔断（连续无事件）
HEARTBEAT = 15.0

_active: set[str] = set()  # 同标的单实例去重


async def report_stream_gen(stock_code: str, force_refresh: bool = False,
                            deep_think: bool | None = None,
                            ) -> AsyncIterator[tuple[str, dict]]:
    """POST /api/report/stream 的事件源：connected → phase → thought×N（写作摘录）→
    section_done×N → report_saved → done；期间 15s 静默发 heartbeat（含进度字节数）。"""
    from orchestrator import resolve_stock_query

    t0 = time.time()
    try:
        code, name = await resolve_stock_query(stock_code)
    except Exception as e:  # noqa: BLE001
        yield ("error", {"message": f"标的解析失败：{e!r}", "code": "fetch"})
        return
    if code in _active:
        yield ("error", {"message": f"{name or code} 正在分析中，请等待完成", "code": "duplicate"})
        return
    _active.add(code)

    q: asyncio.Queue = asyncio.Queue()

    def push(ev: tuple) -> None:
        q.put_nowait(ev)

    stats: dict = {"md_bytes": 0, "phase": "resolve"}  # 真实字节流活动探针（熔断判据）
    task = asyncio.create_task(run_report_direct(
        code, force_refresh=force_refresh, out_root=REPORTS_ROOT, stats=stats,
        deep_think=deep_think,
        on_phase=lambda p: push(("phase", {"phase": p, "elapsed": round(time.time() - t0)})),
        on_section=lambda sid, title, text: push(("section", (sid, title, text))),
        on_thought=lambda title, tail: push(("thought", {"title": title, "tail": tail})),
    ))
    task.add_done_callback(lambda _: push(("finish", None)))

    closed: dict[str, Section] = {}
    prev_blocks: dict = {}
    md_bytes = 0
    last_activity = t0
    last_bytes = 0
    try:
        yield ("connected", {"stock_code": code, "stock_name": name, "ts": t0})
        while True:
            try:
                kind, payload = await asyncio.wait_for(q.get(), timeout=HEARTBEAT)
            except asyncio.TimeoutError:
                now = time.time()
                live_bytes = stats.get("md_bytes", 0)
                live_active = stats.get("last_active", 0)
                if live_bytes != last_bytes:      # 输出 token 在增长
                    last_bytes, last_activity = live_bytes, now
                if live_active and live_active > last_activity:
                    last_activity = live_active   # 推理 token 证明模型活着
                if now - t0 > TOTAL_TIMEOUT:
                    task.cancel()
                    yield ("error", {"message": f"分析超时（{int(TOTAL_TIMEOUT)}s）", "code": "timeout"})
                    return
                if now - last_activity > STALL_TIMEOUT:
                    task.cancel()
                    yield ("error", {"message": "报告流式中断（连续240s无任何token产出）", "code": "llm"})
                    return
                yield ("heartbeat", {"elapsed": int(now - t0),
                                     "md_bytes": max(live_bytes, md_bytes),
                                     "reasoning_bytes": stats.get("reasoning_bytes", 0),
                                     "sections": len(closed), "phase": stats.get("phase")})
                continue
            last_activity = time.time()
            if kind == "phase":
                yield ("phase", payload)
            elif kind == "thought":
                yield ("thought", {**payload, "md_bytes": max(stats.get("md_bytes", 0), md_bytes)})
            elif kind == "section":
                sid, title, text = payload
                closed.setdefault(sid, Section(sid, title, text))
                md_bytes += len(text.encode("utf-8"))
                blocks = build_blocks(closed)
                changed = {k: v for k, v in blocks.items()
                           if json.dumps(v, ensure_ascii=False, sort_keys=True)
                           != json.dumps(prev_blocks.get(k), ensure_ascii=False, sort_keys=True)}
                prev_blocks = blocks
                yield ("section_done", {"section_id": sid, "title": title,
                                        "md_bytes": md_bytes, "blocks": changed})
            elif kind == "finish":
                break
        if task.cancelled():
            yield ("error", {"message": "分析被取消", "code": "llm"})
            return
        try:
            outcome = task.result()
        except Exception as e:  # noqa: BLE001
            yield ("error", {"message": f"分析失败：{e!r}", "code": "llm"})
            return

        dashboard = build_dashboard(outcome.md, issues=outcome.issues,
                                    report_path=f"{outcome.dir.name}/report_direct.md")
        (outcome.dir / "dashboard.json").write_text(
            json.dumps(dashboard, ensure_ascii=False), encoding="utf-8")
        yield ("report_saved", {"path": str(outcome.dir.relative_to(REPORTS_ROOT.parent)),
                                "issues_count": len(outcome.issues)})
        yield ("done", {"dashboard": dashboard, "issues": outcome.issues,
                        "parse_fallbacks": dashboard["parse_fallbacks"]})
    finally:
        if not task.done():
            task.cancel()
        _active.discard(code)


# ---------- 历史 ----------

_DIR_RE = re.compile(r"^(\d{6})(?:-(.*))?$")


def _summary_from(dash: dict, code: str, name: str, mtime: float) -> dict:
    b = dash.get("blocks", {})
    stamp, quote, meta = b.get("stamp", {}), b.get("quote", {}), b.get("meta", {})
    return {"code": meta.get("code") or code, "name": meta.get("name") or name,
            "analysis_date": meta.get("analysis_date"),
            "state": stamp.get("zh"), "state_en": stamp.get("en"),
            "confidence": stamp.get("confidence"), "position_cap_pct": stamp.get("position_cap_pct"),
            "price": quote.get("price"), "change_pct": quote.get("change_pct"),
            "mtime": round(mtime), "has_markdown": True}


def list_reports() -> list[dict]:
    """扫 analysis_reports/ 子目录（判据=存在 dashboard.json，存量旧目录自然隐身）。"""
    out = []
    if not REPORTS_ROOT.is_dir():
        return out
    for d in sorted(REPORTS_ROOT.iterdir()):
        dj = d / "dashboard.json"
        if not d.is_dir() or not dj.is_file():
            continue
        try:
            dash = json.loads(dj.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️ 看板 JSON 损坏，跳过 {d.name}: {e!r}")
            continue
        m = _DIR_RE.match(d.name)
        out.append(_summary_from(dash, m.group(1) if m else d.name,
                                 (m.group(2) if m else "") or "", dj.stat().st_mtime))
    return sorted(out, key=lambda r: -r["mtime"])


def load_dashboard(code: str) -> dict | None:
    """按 6 位代码取最新看板 JSON 全文；无 → None。

    dashboard.json 若缺 8 块（chips/position/peer/deviation/pq/health/veto/guide）而
    report_direct.md 在，用当前 parser 现场重算补齐并落盘，前端历史详情即能显示明细。"""
    if not re.match(r"^\d{6}$", code) or not REPORTS_ROOT.is_dir():
        return None
    cands = [p for p in REPORTS_ROOT.iterdir()
             if p.is_dir() and (p.name == code or p.name.startswith(f"{code}-"))]
    best = None
    for d in sorted(cands, key=lambda p: p.stat().st_mtime, reverse=True):
        dj = d / "dashboard.json"
        if dj.is_file() and (best is None or dj.stat().st_mtime > best[0]):
            best = (dj.stat().st_mtime, dj)
    if not best:
        return None
    dj = best[1]
    try:
        dash = json.loads(dj.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    # 缺块 → 由原文重算（仅当 md 存在且解析能补齐非 una 块）
    blocks = dash.get("blocks", {})
    new_keys = ("chips", "position", "peer", "deviation", "pq", "health", "veto", "guide")
    missing = [k for k in new_keys
               if blocks.get(k, {}).get("status") not in ("ok", "partial", "raw")]
    if missing:
        md_file = dj.parent / "report_direct.md"
        if md_file.is_file():
            try:
                from dashboard.parser import build_dashboard
                rebuilt = build_dashboard(md_file.read_text(encoding="utf-8"))
                rebuilt_missing = [k for k in new_keys
                                   if rebuilt.get("blocks", {}).get(k, {}).get("status")
                                   not in ("ok", "partial", "raw")]
                if len(rebuilt_missing) < len(missing):
                    dash = rebuilt
                    try:
                        dj.write_text(json.dumps(dash, ensure_ascii=False), encoding="utf-8")
                    except OSError:
                        pass
            except Exception as e:  # noqa: BLE001
                print(f"⚠️ 看板重算失败 {dj.parent.name}: {e!r}")
    return dash


def load_markdown(code: str) -> str | None:
    if not re.match(r"^\d{6}$", code) or not REPORTS_ROOT.is_dir():
        return None
    for d in sorted((p for p in REPORTS_ROOT.iterdir()
                     if p.is_dir() and (p.name == code or p.name.startswith(f"{code}-"))),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        f = d / "report_direct.md"
        if f.is_file():
            return f.read_text(encoding="utf-8")
    return None
