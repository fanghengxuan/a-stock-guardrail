# backend/tests/test_route_registry.py
import json
from decision_agents import route_registry as rr

_MD = """# 数据路由文档

## 融资融券
- [P1] 东财RPTA（主）
- [P2] akshare SSE（子集路由）

## 公告
- [P1] 东财 security/ann

---
## 退役区（自动移入，人工可复活）
"""


def _patch(tmp_path, monkeypatch):
    md, st = tmp_path / "r.md", tmp_path / "s.json"
    md.write_text(_MD)
    monkeypatch.setattr(rr, "ROUTES_MD", md)
    monkeypatch.setattr(rr, "STATE_JSON", st)
    return md, st


def test_load_multi_routes(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch)
    routes = rr.load_routes()
    assert [r["id"] for r in routes["融资融券"]] == ["P1", "P2"]
    assert "东财RPTA" in routes["融资融券"][0]["path"]


def test_weight_ordering(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch)
    rr.record_failure("融资融券", "P1", "timeout")          # P1 权重 80
    rr.record_success("融资融券", "P2")
    rr.record_success("融资融券", "P2")                      # P2 权重 120
    order = rr.ordered_routes("融资融券")
    assert order[0]["id"] == "P2" and order[1]["id"] == "P1"


def test_streak_reset_and_retire(tmp_path, monkeypatch):
    md, st = _patch(tmp_path, monkeypatch)
    rr.record_failure("公告", "P1", "http 500")
    rr.record_success("公告", "P1")
    s = json.loads(st.read_text())
    assert s["公告:P1"]["fail_streak"] == 0 and s["公告:P1"]["ok_streak"] == 1
    for _ in range(3):
        rr.record_failure("公告", "P1", "http 500")
    assert rr.retire_if_dead("公告", "P1") is True
    text = md.read_text()
    assert "东财 security/ann" in text.split("退役区")[1]     # 移入退役区且带死因
    assert rr.load_routes()["公告"] == []


def test_try_auto_add_route_rejects_unknown_tool(tmp_path, monkeypatch):
    md, _ = _patch(tmp_path, monkeypatch)
    before = md.read_text()
    assert rr.try_auto_add_route("公告", "tool=ghost_tool;variant=x") is False
    assert rr.try_auto_add_route("公告", "tool=web_search;https://evil.example.com") is False
    assert md.read_text() == before                          # 拒收且文档不变


def test_try_auto_add_route_probation_weight(tmp_path, monkeypatch):
    md, _ = _patch(tmp_path, monkeypatch)
    assert rr.try_auto_add_route("公告", "tool=web_search;query=<code> 大宗交易 公告") is True
    added = [r for r in rr.load_routes()["公告"] if r["id"] == "P2"]
    assert added and "[auto·试用" in added[0]["path"]
    weights = {r["id"]: rr.route_weight("公告", r["id"]) for r in rr.load_routes()["公告"]}
    assert weights["P2"] == 60 and weights["P1"] == 100      # 试用期基线低于成熟路由
    rr.record_success("公告", "P2")                          # 首次成功转正
    assert rr.route_weight("公告", "P2") >= 100
