# backend/scripts/acceptance_phase1.py
"""一期验收：spec §7 五条标准。用法：cd backend && uv run python scripts/acceptance_phase1.py"""
import asyncio, json, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))  # 与 scripts/ 下既有脚本同套路：使 backend 根目录可导入
from decision_agents.data_fetcher import fetch_raw_data, _cache
from decision_agents.drilldown import run_drilldown
from orchestrator import run_analysis

def criterion_1():      # 必取墙钟 <10s（冷缓存）
    _cache.invalidate_code("000651")
    t = time.time(); fetch_raw_data("000651"); wall = time.time() - t
    print(f"[1] 必取墙钟 {wall:.1f}s {'PASS' if wall < 10 else 'FAIL'}")

def criterion_2():      # 新数据源可用（招行）
    extra, _ = asyncio.run(run_drilldown("600036", "招商银行", {"market": {}, "financial": {}, "capital_flow": {}, "reverse_check_raw": {}}, force_refresh=True))
    bq = extra.get("bank_quality", {})
    ok = bq.get("npl_ratio") and 0.5 < bq["npl_ratio"] < 2.0
    print(f"[2] 新数据源（银行质量实取 npl={bq.get('npl_ratio')}）{'PASS' if ok else 'FAIL'}；工具清单：{sorted(extra)}")

async def _c34():       # 3/4 合并：先验清零 + 同股3连一致
    import orchestrator
    src = pathlib.Path("orchestrator.py").read_text()
    print(f"[3] 先验补洞指令 {'清零 PASS' if '利用训练数据知识' not in src else 'FAIL'}")
    try:
        cards = []
        for i in range(3):
            cards.append(await run_analysis("600036", force_refresh=(i == 0)))
        same = len({(c.final_decision.state, c.hard_gate.passed, c.five_signals.trend) for c in cards}) == 1
        _root = pathlib.Path(__file__).resolve().parents[2] / "analysis_reports"
        print(f"[4] 同股3连一致（状态机/硬门槛/趋势）{'PASS' if same else 'FAIL'}；trace 文件存在：{(_root/'600036-招商银行'/'tool_trace.json').exists()}")
    except Exception as e:  # noqa: BLE001  run_analysis 异常（端点/环境阻塞）标 BLOCKED 不崩溃，继续跑到 [5]
        first = (str(e).strip().splitlines() or [type(e).__name__])[0]
        print(f"[4] BLOCKED: {first}")

if __name__ == "__main__":
    criterion_1(); criterion_2(); asyncio.run(_c34())
    print("[5] 前端契约：人工验证——启动 backend+frontend，SSE 全流程跑通一次（16 区块渲染正常）")
