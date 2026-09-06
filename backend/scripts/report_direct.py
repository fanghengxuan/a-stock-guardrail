# backend/scripts/report_direct.py
"""去卡直出：代码层取数（L1+下钻）→ LLM 按手册直接撰写 MD 报告（无 JSON schema）→
校验章节 → 落盘。绕开决策卡/前端契约，验证「markdown 是 LLM 天然输出形态」路线。

用法：cd backend && .venv/bin/python scripts/report_direct.py [stock_code]
亦可被 report_service 复用：run_report_direct(code, on_phase=…, on_section=…)，
流式消费时把 token 增量喂 SectionScanner，每闭合一节回调 on_section——前端逐区块点亮专用；
校验/落盘只认 final_output（两路解耦，delta 缺失不影响正确性）。
"""
import asyncio
import json
import pathlib
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from agents import Agent, ModelSettings, Runner
from config import LLM_DEEP_THINK, LLM_MODEL
from orchestrator import resolve_stock_query, orchestrator as main_agent
from report_generator import (_strip_fences, _compact_raw, validate_report,
                              report_writer)
from decision_agents.data_fetcher import fetch_raw_data
from decision_agents.drilldown import run_drilldown

DIRECT_RULES = (
    "\n\n【执行手册——本次分析判定规则（报告 L2/L3 各结论必须依此推演）】\n"
    + main_agent.instructions
)

# 深度思考（默认关，前端每单可切 deep_think 覆盖全局 config.LLM_DEEP_THINK）：
#   关 → reasoning.effort=none：无思维链，出字即时、逐节点亮（实时渲染友好）；
#   开 → reasoning.effort=high：深度推理（更缜密但慢）。
# ModelSettings 字段是 reasoning（dict）与 max_tokens（映射到 API 的 max_output_tokens）；
# 传 max_output_tokens=… 会被 pydantic 静默忽略（2026-09-06 实测教训）。
def _build_direct_agent(deep: bool):
    return Agent(name="report_direct", model=LLM_MODEL,
                 instructions=report_writer.instructions + DIRECT_RULES,
                 model_settings=ModelSettings(
                     max_tokens=65536 if deep else 32768,
                     reasoning={"effort": "high"} if deep else {"effort": "none"}))


# 双态预构建（避免每请求重建含大指令串的 Agent），按请求/全局选择。
_AGENT_MODE = {True: _build_direct_agent(True), False: _build_direct_agent(False)}
# 兼容导出：默认态（=全局配置所对应的那个）
DIRECT_AGENT = _AGENT_MODE[LLM_DEEP_THINK]


def pick_direct_agent(deep_think: bool | None):
    """请求级覆盖：None=全局 LLM_DEEP_THINK；否则按传入布尔选择。"""
    mode = LLM_DEEP_THINK if deep_think is None else bool(deep_think)
    return _AGENT_MODE[mode]

THOUGHT_INTERVAL = 2.0  # 思考摘录条最短发送间隔（秒），防 SSE 刷屏


def _tail_excerpt(buf: str) -> str:
    """最近 token 缓冲 → 单行摘录（折叠全部空白，取尾 60 字符）。"""
    s = " ".join(buf.split())
    return s[-60:]


def _prompt(code, name, date, raw) -> str:
    return (
        f"标的：{name}（{code}），分析日期：{date}。\n\n"
        "【真实数据底稿（L1 代码层采集+模型下钻实取，数据以此为准）】\n"
        f"```json\n{json.dumps(_compact_raw(raw), ensure_ascii=False, default=str)}\n```\n\n"
        "【决策卡】本次无预置决策卡——请按执行手册规则自行完成全部 L2/L3 判定"
        "（行业地位与护城河/硬门槛/反向清单/竞品对比/五维信号/资金画像/价值陷阱与利润质量/"
        "偏离度与协议层/量化四法/辩论/压力测试/行为自检/健康度与风险分类/一票否决/状态机），"
        "并将结论写入报告对应章节；"
        "禁止编造底稿中不存在的数值（行业参照/公开常识除外，需注明）。\n\n"
        "请撰写完整分析报告。"
    )


@dataclass
class ReportOutcome:
    md: str
    issues: list = field(default_factory=list)
    dir: Optional[pathlib.Path] = None
    code: str = ""
    name: str = ""


def _stream_delta(ev) -> Optional[str]:
    """从 SDK 流事件取 token 增量。responses 模式（主线）与 chat_completions 回滚双形态。"""
    if getattr(ev, "type", "") != "raw_response_event":
        return None
    d = getattr(ev, "data", None)
    dtype = getattr(d, "type", "")
    if dtype == "response.output_text.delta":
        return getattr(d, "delta", "") or None
    if dtype == "chat.completion.chunk":  # 回滚模式应急形态
        try:
            return d.choices[0].delta.content or None
        except (AttributeError, IndexError):
            return None
    return None


async def run_report_direct(stock_code: str, *,
                            on_phase: Optional[Callable[[str], None]] = None,
                            on_section: Optional[Callable[[str, str, str], None]] = None,
                            on_thought: Optional[Callable[[Optional[str], str], None]] = None,
                            stats: Optional[dict] = None,
                            force_refresh: bool = False,
                            out_root: Optional[pathlib.Path] = None,
                            deep_think: Optional[bool] = None) -> ReportOutcome:
    """stats：跨任务共享的活动探针 dict（md_bytes/phase）——供服务端做
    「真实字节流静默」熔断判据（大节写作可超 240s，不能用队列事件当心跳）。
    deep_think：请求级深度思考覆盖（None=用全局 config.LLM_DEEP_THINK）。"""
    """取数 → 下钻 → 流式直写 MD → 校验 → 落盘。CLI 与服务端共用。"""
    from dashboard.scanner import SectionScanner

    def phase(name):
        if stats is not None:
            stats["phase"] = name
        if on_phase:
            on_phase(name)

    resolved_code, resolved_name = await resolve_stock_query(stock_code)
    if force_refresh:
        from decision_agents.data_fetcher import _cache
        _cache.invalidate_code(resolved_code)
    phase("fetch")
    raw = await asyncio.to_thread(fetch_raw_data, resolved_code)
    phase("drilldown")
    extra, _ = await run_drilldown(resolved_code, resolved_name, raw, force_refresh)
    raw = {**raw, "drilldown": extra}
    phase("write")

    scanner = SectionScanner()
    tail_buf = ""
    last_thought = 0.0

    def feed_chunk(chunk):
        for sid, title, text in scanner.feed(chunk):
            if on_section:
                on_section(sid, title, text)

    result = Runner.run_streamed(pick_direct_agent(deep_think), _prompt(
        resolved_code, resolved_name,
        __import__("datetime").date.today().isoformat(), raw))
    async for ev in result.stream_events():
        delta = _stream_delta(ev)
        if delta:
            feed_chunk(delta)
            if stats is not None:
                stats["md_bytes"] = scanner.total_bytes
            if on_thought:
                tail_buf = (tail_buf + delta)[-400:]
                now = time.monotonic()
                if now - last_thought >= THOUGHT_INTERVAL:
                    last_thought = now
                    on_thought(scanner.current_title, _tail_excerpt(tail_buf))
        elif stats is not None and getattr(ev, "type", "") == "raw_response_event":
            # 推理 token（reasoning_text.delta）不算报告字节，但证明模型活着——
            # 不计此活动则 240s 熔断会在长推理阶段误杀活流
            stats["last_active"] = time.time()
            d = getattr(ev, "data", None)
            rdelta = getattr(d, "delta", "") or ""
            if rdelta:
                stats["reasoning_bytes"] = stats.get("reasoning_bytes", 0) + len(rdelta.encode("utf-8"))
    for sid, title, text in scanner.finish():  # 末两节（决策卡/执行摘要）闭节
        if on_section:
            on_section(sid, title, text)

    md = _strip_fences(str(result.final_output))
    phase("validate")
    issues = validate_report(md)
    out_root = pathlib.Path(out_root or pathlib.Path(__file__).resolve().parents[2] / "analysis_reports")
    out = out_root / f"{resolved_code}-{resolved_name}".rstrip("-") / "report_direct.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    phase("saved")
    return ReportOutcome(md=md, issues=issues, dir=out.parent,
                         code=resolved_code, name=resolved_name)


async def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "600036"
    T0 = time.time()

    def on_phase(name):
        print(f"[{time.time()-T0:5.1f}s] {name}", flush=True)
        if name == "write":
            print("进入报告直写（流式，无schema）...", flush=True)

    def on_section(sid, title, text):
        print(f"  ✓ 节闭合 [{sid}] {title[:28]}（{len(text.encode())//1024}KB）", flush=True)

    outcome = await run_report_direct(code, on_phase=on_phase, on_section=on_section)
    print(f"✅ 已存 {outcome.dir}/report_direct.md  ({time.time()-T0:.1f}s)")
    if outcome.issues:
        print(f"⚠️ {len(outcome.issues)} 节缺失/不达标："
              + "；".join(str(i.get('id', i)) for i in outcome.issues))
    else:
        from report_generator import REQUIRED_SECTIONS
        print(f"✅ {len(REQUIRED_SECTIONS)} 节全部达标")


if __name__ == "__main__":
    asyncio.run(main())
