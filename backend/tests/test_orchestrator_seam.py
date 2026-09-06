# backend/tests/test_orchestrator_seam.py
"""T7 接线契约（终审重建；2026-09-05 更新：减载单份 + 卡片调用改流式）：run_analysis 必须
①把阶段2下钻数据单份注入独立段（raw_json 不再内嵌 drilldown 重复份——减载）；
②走 output_type 主路径（run_streamed 流式消费后取 final_output），端点失败才降级 fallback 容错解析；
③合并后的 raw（含 drilldown）仍流入 _backfill_data_trace。
全部 LLM/网络/报告副作用打桩，不触真实文件、不发真实请求。"""
import asyncio
import json

import orchestrator as o
from schemas import StockDecisionCard

RAW = {"market": {"latest_price": 41.0}, "financial": {"data": []}}
EXTRA = {"bank_quality": {"npl_ratio": 0.37}, "news": {"status": "OK"}}  # 0.37 刻意避开 few-shot 硬编码的 0.94


def _card():
    return StockDecisionCard(header={"stock_name": "招商银行",
                                     "stock_code": "600036",
                                     "analysis_date": "2026-09-05"})


def _stub_env(monkeypatch, calls, run_impl):
    monkeypatch.setattr(o, "has_api_key", lambda: True)

    async def fake_resolve(q):
        return "600036", "招商银行"
    monkeypatch.setattr(o, "resolve_stock_query", fake_resolve)

    import decision_agents.data_fetcher as df
    monkeypatch.setattr(df, "fetch_raw_data", lambda code: dict(RAW))

    import decision_agents.drilldown as dd

    async def fake_drill(code, name, raw, force_refresh=False):
        return json.loads(json.dumps(EXTRA)), None
    monkeypatch.setattr(dd, "run_drilldown", fake_drill)

    import report_generator

    async def no_report(*a, **k):
        return None
    monkeypatch.setattr(report_generator, "generate_and_save_report", no_report)

    backfills = []
    monkeypatch.setattr(o, "_backfill_data_trace",
                        lambda card, raw: backfills.append(raw))

    def fake_run_streamed(agent, prompt, **kw):
        calls.append((agent, prompt))
        return _FakeStream(run_impl(len(calls), agent, prompt))
    monkeypatch.setattr(o.Runner, "run_streamed", staticmethod(fake_run_streamed))
    return backfills


class _FakeStream:
    """模仿 RunResultStreaming：stream_events() 消费时抛异常或空转，final_output 消费后可读。"""

    def __init__(self, outcome):
        self._outcome = outcome          # 异常实例 → 流中抛；否则视作 final_output

    async def stream_events(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        if False:
            yield

    @property
    def final_output(self):
        return self._outcome


def test_main_path_injects_drilldown_single_copy(monkeypatch):
    calls = []
    card = _card()
    backfills = _stub_env(
        monkeypatch, calls,
        lambda n, agent, prompt: card)

    out = asyncio.run(o.run_analysis("600036"))

    assert len(calls) == 1                       # 主路径成功，不触发降级
    _, prompt = calls[0]
    assert getattr(calls[0][0], "output_type", None) is not None  # response_format 主路径
    assert '"drilldown"' not in prompt           # 减载：raw_json 不含内嵌重复份
    assert "下钻补充数据（drilldown" in prompt     # 独立段标题保留路径可寻性
    assert prompt.count('"npl_ratio": 0.37') == 1  # 单份——重复注入（减载被回退）即见红
    assert "已在 drilldown.bank_quality 提供" in prompt
    assert backfills and "drilldown" in backfills[0]  # 合并 raw 流入回填
    assert out is card


def test_fallback_degrades_to_tolerant_parse(monkeypatch):
    calls = []
    dumped = json.dumps(_card().model_dump(mode="json"), ensure_ascii=False)

    def impl(n, agent, prompt):
        # 第 1 次（主路径）在流消费时抛错；第 2 次（fallback）返回纯 JSON 文本
        if n == 1:
            return RuntimeError("endpoint 400: response_format unavailable")
        return dumped
    _stub_env(monkeypatch, calls, impl)

    out = asyncio.run(o.run_analysis("600036"))   # 不抛异常 = 降级链闭合

    assert len(calls) == 2
    agent2, prompt2 = calls[1]
    assert getattr(agent2, "output_type", None) is None   # 降级移除 response_format
    assert "【关键输出格式】" in prompt2                    # 加显式纯 JSON 指令
    assert out.header.stock_code == "600036"              # 容错解析产出合法卡
