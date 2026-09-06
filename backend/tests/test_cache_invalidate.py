from decision_agents.data_fetcher import TTLCache


def test_invalidate_code_only_removes_matching_suffix():
    c = TTLCache(ttl_seconds=60)
    c.set("tencent_quote:600036", {"p": 1})
    c.set("em_fin:600036", {"r": 2})
    c.set("tencent_quote:000651", {"p": 3})
    n = c.invalidate_code("600036")
    assert n == 2
    assert c.get("tencent_quote:600036") is None
    assert c.get("tencent_quote:000651") == {"p": 3}


def test_invalidate_code_missing_returns_zero():
    c = TTLCache(ttl_seconds=60)
    c.set("a:111111", 1)
    assert c.invalidate_code("999999") == 0
