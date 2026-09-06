# backend/tests/test_no_prior_fill.py
import pathlib

def test_no_training_data_fill_instructions():
    orch = pathlib.Path("orchestrator.py").read_text()
    assert "利用训练数据知识" not in orch      # 先验补洞指令清零
    assert "无需再调用 fetch_stock_data" not in orch
    assert "禁止用记忆估算" in orch            # 正向断言防替换句被删仍绿（T7 复审 minor②）

def test_dead_registrations_removed():
    orch = pathlib.Path("orchestrator.py").read_text()
    assert "as_tool" not in orch
    assert "mcp_integration" not in orch
