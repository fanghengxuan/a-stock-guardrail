"""决策看板数据层：report_direct.md → DashboardData（确定性解析，无 LLM）。"""
from dashboard.parser import build_dashboard, build_blocks
from dashboard.scanner import SectionScanner, split_sections

__all__ = ["build_dashboard", "build_blocks", "SectionScanner", "split_sections"]
