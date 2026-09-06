"""report_service 流式端点与历史 API 测试：LLM/取数全程打桩，不触网络。

核心不变量：done.dashboard == build_dashboard(同一 MD)（权威重算），
section_done 的增量块累计与最终态在关键块上一致；同标的并发提交去重。
"""
import asyncio
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

import report_service
from dashboard import build_dashboard
from dashboard.scanner import split_sections
from scripts.report_direct import ReportOutcome

FIX = pathlib.Path(__file__).parent / "fixtures"
MD = (FIX / "600036-report_direct.md").read_text(encoding="utf-8")


async def _fake_resolve(query):
    return "600036", "招商银行"


def _fake_run(delay=None):
    async def run(stock_code, *, on_phase=None, on_section=None, on_thought=None, stats=None,
                  force_refresh=False, out_root=None, deep_think=None):
        if on_phase:
            on_phase("fetch")
            on_phase("drilldown")
            on_phase("write")
        if on_thought:
            on_thought("一、行情数据", "…PE(TTM) 6.93 倍，处于近 5 年 23% 分位")
        if delay is not None:
            await delay.wait()
        for sid, sec in split_sections(MD).items():
            if on_section:
                on_section(sid, sec.title, sec.text)
        d = pathlib.Path(out_root) / "600036-招商银行"
        d.mkdir(parents=True, exist_ok=True)
        (d / "report_direct.md").write_text(MD, encoding="utf-8")
        if on_phase:
            on_phase("validate")
            on_phase("saved")
        return ReportOutcome(md=MD, issues=[], dir=d, code="600036", name="招商银行")
    return run


async def _collect(gen):
    return [ev async for ev in gen]


@pytest.fixture()
def patched(monkeypatch, tmp_path):
    monkeypatch.setattr("orchestrator.resolve_stock_query", _fake_resolve)
    monkeypatch.setattr(report_service, "run_report_direct", _fake_run())
    root = tmp_path / "analysis_reports"
    monkeypatch.setattr(report_service, "REPORTS_ROOT", root)
    return root


def test_stream_events(patched):
    events = asyncio.run(_collect(report_service.report_stream_gen("招商银行")))
    names = [e for e, _ in events]
    assert names[0] == "connected"
    assert "section_done" in names and names[-1] == "done"
    assert set(names) >= {"phase", "report_saved"}
    # 思考摘录条按用户拍板恢复（2026-09-06 二次决策，替代静默期观感）
    th = [d for e, d in events if e == "thought"]
    assert th and th[0]["title"] == "一、行情数据" and "PE" in th[0]["tail"] and "md_bytes" in th[0]
    sds = [d for e, d in events if e == "section_done"]
    assert {s["section_id"] for s in sds} >= {"meta", "l0_quote", "l2_state", "l3_summary"}
    # 累计点亮：quote 在 l0_quote 闭节时出现，stamp 在 l2_state 闭节时出现
    lit = {}
    for s in sds:
        lit.update(s["blocks"])
    assert lit["quote"]["price"] == 41.69
    assert lit["stamp"]["en"] == "Watch"
    # done 权威态 == 一次性解析
    done = dict(events)["done"]
    once = build_dashboard(MD)
    assert json.dumps(done["dashboard"]["blocks"], sort_keys=True) == \
        json.dumps(once["blocks"], sort_keys=True)
    assert done["dashboard"]["schema"] == 1
    # dashboard.json 落盘可回读
    dash_file = patched / "600036-招商银行" / "dashboard.json"
    assert json.loads(dash_file.read_text())["blocks"]["quote"]["price"] == 41.69


def test_stream_duplicate_guard(monkeypatch, patched):
    gate = asyncio.Event()
    monkeypatch.setattr("orchestrator.resolve_stock_query", _fake_resolve)
    monkeypatch.setattr(report_service, "run_report_direct", _fake_run(delay=gate))
    gen1 = report_service.report_stream_gen("600036")

    async def both():
        first = await gen1.__anext__()      # connected
        task = asyncio.create_task(_collect(gen1))
        await asyncio.sleep(0.05)           # gen2 在同标的 active 时进入
        dup = await _collect(report_service.report_stream_gen("600036"))
        gate.set()
        ev1 = await task
        return first, dup, ev1
    first, dup, ev1 = asyncio.run(both())
    assert first[0] == "connected"
    assert dup[0][0] == "error" and dup[0][1]["code"] == "duplicate"
    assert ev1[-1][0] == "done"  # 第一个流不受影响
    assert "600036" not in report_service._active  # 双双释放


def test_stall_breaker_survives_slow_section(monkeypatch, patched):
    """单节写作超 STALL 窗但 token 流未断（stats.md_bytes 持续增长）→ 不得误杀。
    （2026-09-06 002415 实跑误杀事故回归：判据从队列事件改为真实字节。）"""
    monkeypatch.setattr("orchestrator.resolve_stock_query", _fake_resolve)
    monkeypatch.setattr(report_service, "HEARTBEAT", 0.05)
    monkeypatch.setattr(report_service, "STALL_TIMEOUT", 0.3)
    monkeypatch.setattr(report_service, "TOTAL_TIMEOUT", 30)

    async def slow_run(stock_code, *, on_phase=None, on_section=None, on_thought=None, stats=None,
                       force_refresh=False, out_root=None, deep_think=None):
        on_phase("write")
        for i in range(12):                      # 1.2s 持续滴字节，但首节 0.9s 后才闭合
            stats["md_bytes"] = (i + 1) * 400
            await asyncio.sleep(0.1)
            if i == 9:
                on_section("meta", "数据底稿", _title_only())
        d = pathlib.Path(out_root) / "600036-招商银行"
        d.mkdir(parents=True, exist_ok=True)
        return ReportOutcome(md=_title_only(), issues=[], dir=d,
                             code="600036", name="招商银行")
    monkeypatch.setattr(report_service, "run_report_direct", slow_run)
    events = asyncio.run(_collect(report_service.report_stream_gen("600036")))
    errs = [d for e, d in events if e == "error"]
    assert not errs, f"字节在增长的慢节被误杀：{errs}"
    assert any(e == "heartbeat" for e, _ in events)
    assert events[-1][0] == "done"


def _title_only():
    return "# 【数据底稿】标的：招商银行 代码：600036 取数时间：2026-09-06\n"


def test_stall_breaker_kills_dead_stream(monkeypatch, patched):
    """真·零字节断流 → STALL 熔断照常触发。"""
    monkeypatch.setattr("orchestrator.resolve_stock_query", _fake_resolve)
    monkeypatch.setattr(report_service, "HEARTBEAT", 0.05)
    monkeypatch.setattr(report_service, "STALL_TIMEOUT", 0.2)
    monkeypatch.setattr(report_service, "TOTAL_TIMEOUT", 30)
    gate = asyncio.Event()

    async def dead_run(stock_code, **kw):
        await gate.wait()   # 永不产出、永不返回（stats.md_bytes 保持 0）
        return ReportOutcome(md="", issues=[])
    monkeypatch.setattr(report_service, "run_report_direct", dead_run)

    async def collect_with_release():
        task = asyncio.create_task(_collect(report_service.report_stream_gen("600036")))
        await asyncio.sleep(1.0)
        gate.set()          # 让 fake 任务在熔断 cancel 后可收尾
        return await task
    events = asyncio.run(collect_with_release())
    assert events[-1][0] == "error" and events[-1][1]["code"] == "llm"
    assert "无任何token产出" in events[-1][1]["message"]


def test_stream_timeout_config(monkeypatch, patched):
    async def boom(stock_code, **kw):
        raise RuntimeError("LLM down")
    monkeypatch.setattr("orchestrator.resolve_stock_query", _fake_resolve)
    monkeypatch.setattr(report_service, "run_report_direct", boom)
    events = asyncio.run(_collect(report_service.report_stream_gen("600036")))
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "llm"


def test_history_apis(patched):
    asyncio.run(_collect(report_service.report_stream_gen("600036")))
    import main
    client = TestClient(main.app)
    r = client.get("/api/reports")
    assert r.status_code == 200
    rep = r.json()["reports"][0]
    assert rep["code"] == "600036" and rep["state_en"] == "Watch"
    assert rep["price"] == 41.69
    r = client.get("/api/reports/600036")
    assert r.status_code == 200 and r.json()["blocks"]["stamp"]["en"] == "Watch"
    assert client.get("/api/reports/999999").status_code == 404
    r = client.get("/api/reports/600036/report.md")
    assert r.status_code == 200 and "招商银行" in r.text
    assert client.get("/api/reports/600036x").status_code == 404
    assert client.get("/api/reports/..%2F..%2Fetc").status_code in (404, 422)


def test_list_skips_legacy_dirs_without_dashboard(patched):
    (patched / "600036-招商银行").mkdir(parents=True)   # 只有旧目录，无 dashboard.json
    (patched / "600036-招商银行" / "report_direct.md").write_text("# x")
    assert report_service.list_reports() == []
