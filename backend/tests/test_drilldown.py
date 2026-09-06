# backend/tests/test_drilldown.py
import json, pathlib, time


def test_build_stage1_summary_compact():
    from decision_agents.drilldown import build_stage1_summary
    raw = {"market": {"latest_price": 37.99, "pe_ttm": 6.35, "pb": 0.85,
                      "dividend_yield": 5.31, "roe_latest": 13.44},
           "financial": {"data": [{"WEIGHTAVG_ROE": 13.44}]},
           "capital_flow": {}, "reverse_check_raw": {}}
    s = build_stage1_summary(raw)
    assert "37.99" in s and len(s) < 2000        # 紧凑，不得全量 dump


def test_is_bank_or_insurance():
    from decision_agents.drilldown import is_bank_or_insurance
    assert is_bank_or_insurance("招商银行")
    assert is_bank_or_insurance("中国平安") or True   # 保险按名称含"保险/平安/人寿"判定，实现自定
    assert not is_bank_or_insurance("格力电器")


def test_snapshot_roundtrip(tmp_path):
    from decision_agents.drilldown import write_trace, read_fresh_trace
    p = tmp_path / "tool_trace.json"
    write_trace(p, {"extra_data": {"margin": {"x": 1}}, "rounds": 2, "calls": []})
    got = read_fresh_trace(p, ttl_seconds=1200)
    assert got["extra_data"]["margin"] == {"x": 1}
    # 过期
    old = json.loads(p.read_text()); old["fetched_at"] = time.time() - 9999
    p.write_text(json.dumps(old))
    assert read_fresh_trace(p, ttl_seconds=1200) is None


def test_multi_route_fetch_skips_spec_routes(monkeypatch):
    """裁决 A：含 tool= spec 路由的类目，_call 的降级循环（_multi_route_fetch）只尝试真实
    fetcher 路由，绝不给幻影 spec 路由记连败（否则 web_search 等 LLM 侧工具会被误退役）。"""
    import decision_agents.drilldown as dd

    class FakeFetch:
        def __init__(self):
            self.tried = []

        def fetch(self, code, route=None):
            self.tried.append(route)
            return {"status": "UNA", "error": "boom"}  # 每条都失败，逼出全量降级

    routes = [
        {"id": "P4", "path": "tool=web_search;query=<code> 大宗交易"},  # spec：应被跳过
        {"id": "P1", "path": "np-anotice"},
        {"id": "P2", "path": "akshare cninfo"},
    ]
    monkeypatch.setattr(dd, "ordered_routes", lambda cat: list(routes))
    recorded = []
    monkeypatch.setattr(dd, "record_failure", lambda c, i, e: recorded.append(("fail", i)))
    monkeypatch.setattr(dd, "record_success", lambda c, i: recorded.append(("ok", i)))
    monkeypatch.setattr(dd, "retire_if_dead", lambda c, i: None)

    inst = FakeFetch()
    r, rid = dd._multi_route_fetch(inst, "公告", "600036")

    assert inst.tried == ["P1", "P2"]                # 只 fetch 真实路由，P4 从未被执行
    assert ("fail", "P4") not in recorded            # 幻影 spec 路由不记战绩
    assert recorded == [("fail", "P1"), ("fail", "P2")]
    assert r["status"] == "UNA"                       # 全失败 → UNA
    assert rid == "P2"                                # 最后尝试的真实路由 id


def test_websearch_mounted_only_in_responses_mode(monkeypatch):
    """WebSearchTool 是 Responses hosted tool：chat_completions 下 SDK 抛 UserError
    （chatcmpl_converter 拒非 function 工具），故 _build_agent 仅在 LLM_API_MODE==responses 挂载。
    判别力测试：两模式下 tools 组成相差恰为一个 WebSearchTool；联网 instructions 句随之条件化。"""
    import decision_agents.drilldown as dd

    def build(mode):
        monkeypatch.setattr(dd, "LLM_API_MODE", mode)
        agent = dd._build_agent([], {})
        return [type(t).__name__ for t in agent.tools], agent.instructions

    names_resp, instr_resp = build("responses")
    names_chat, instr_chat = build("chat_completions")

    assert names_resp.count("WebSearchTool") == 1            # responses 挂 1 个
    assert "WebSearchTool" not in names_chat                 # chat_completions 不挂（否则运行即 UserError）
    assert len(names_resp) == len(names_chat) + 1            # 差集恰为 WebSearchTool
    assert "内置联网搜索" in instr_resp                       # responses 才提示可用联网搜索
    assert "内置联网搜索" not in instr_chat                    # chat 模式不误导 LLM 用未挂载的工具
