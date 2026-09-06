"""report_direct.md 节定位与切分：一次性切分 + 流式增量切分共用同一套 SPEC。

SPEC 与 report_generator.REQUIRED_SECTIONS 同源但收紧两处已证实的歧义命中：
- l2_state 用「状态机映射」（validate 的「状态机」会先命中 L2 第九节的
  「### 第六步：状态机裁定」H3，看板必须命中 L2 十一本体）；
- l2_debate 用「辩论引擎」；l3_card 用「输出展示层」（「决策卡」与正文撞词）。
节定位带级别约束：meta/l3_card=H1，其余=H2。l3_card 正文到下一个任意级标题为止
（否则吞掉「## 执行摘要」）。数据底稿层内未被 SPEC 认领的 H2 → appendix，永不丢内容。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# (id, level, 标题关键词)——任一关键词命中且级别相等即为该节标题
SPEC: list[tuple[str, int, tuple[str, ...]]] = [
    ("meta",          1, ("数据底稿",)),
    ("l0_constraint", 2, ("200字硬约束", "硬约束校验")),
    ("l0_quote",      2, ("行情数据",)),
    ("l0_financial",  2, ("财务数据",)),
    ("l0_dividend",   2, ("分红",)),
    ("l0_reverse",    2, ("反向清单数据",)),
    ("l0_flow",       2, ("资金流向",)),
    ("l0_forecast",   2, ("机构预测", "机构评级")),
    ("l0_chips",      2, ("筹码", "内部人成本")),
    ("l2_position",   2, ("行业地位", "护城河")),
    ("l2_hardgate",   2, ("硬门槛筛选", "硬门槛")),
    ("l2_reverse",    2, ("反向清单筛查",)),
    ("l2_peer",       2, ("竞品",)),
    ("l2_five",       2, ("五维信号",)),
    ("l2_capital",    2, ("资金行为画像",)),
    ("l2_trap",       2, ("价值陷阱",)),
    ("l2_deviation",  2, ("偏离度", "裁量协议")),
    ("l2_factor",     2, ("因子有效性", "因子验证")),
    ("l2_quant",      2, ("量化增强",)),
    ("l2_debate",     2, ("辩论引擎",)),
    ("l2_stress",     2, ("压力测试",)),
    ("l2_behavior",   2, ("行为金融学", "行为自检")),
    ("l2_health",     2, ("财务健康", "风险分类")),
    ("l2_veto",       2, ("一票否决",)),
    ("l2_state",      2, ("状态机映射",)),
    ("l3_card",       1, ("输出展示层",)),
    ("l3_summary",    2, ("执行摘要",)),
]
_META_LIKE = ("数据底稿",)  # appendix 候选只存在于该 H1 层内


def match_heading(lvl: int, title: str) -> str | None:
    """标题 → SPEC 节 id：级别匹配的所有 SPEC 中取命中关键词最长者。

    最长匹配防抢占：如「✅ 二、反向清单筛查：通过…（…分红率…）」同时含
    l0_dividend 的「分红」与 l2_reverse 的「反向清单筛查」，必须归后者。
    """
    best, best_len = None, 0
    for sid, s_lvl, kws in SPEC:
        if lvl == s_lvl:
            for k in kws:
                if k in title and len(k) > best_len:
                    best, best_len = sid, len(k)
    return best


def _is_meta_marker(lvl: int, title: str) -> bool:
    return lvl == 1 and any(k in title for k in _META_LIKE)


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    text: str  # 标题行与下一边界之间的正文（不含标题行）


def split_sections(md: str) -> dict[str, Section]:
    """一次性切分。未知 H2（底稿层内）id 为 appendix:<标题>。重复标题以首次命中为准。"""
    out: dict[str, Section] = {}
    for sec, _ in _split_iter(md.splitlines()):
        out.setdefault(sec.id, sec)
    return out


def _split_iter(lines: list[str]):
    """按行扫描产出 (Section, 结束行号)。闭节规则与 SectionScanner 一致（单一实现）。"""
    # 预扫全部标题
    heads = [(i, len(m.group(1)), m.group(2).strip())
             for i, ln in enumerate(lines) if (m := _HEADING_RE.match(ln))]
    for k, (li, lvl, title) in enumerate(heads):
        sid = match_heading(lvl, title)
        if sid is None and lvl == 2:
            # 底稿 H1 层内的未知 H2 → appendix（层归属：晚于最后一个 meta 标记、
            # 早于下一个任意 H1）
            prev_meta = [h for h in heads[:k] if _is_meta_marker(h[1], h[2])]
            after = [h for h in heads[k + 1:] if h[1] == 1]
            if prev_meta and li > prev_meta[-1][0] and (not after or li < after[0][0]):
                sid = f"appendix:{title}"
        if sid is None:
            continue
        # 正文边界（与 SectionScanner 同一判据）：新标题级别 <= 本节，或新标题本身命中
        # 任一节定义（含 appendix）→ 闭节。l3_card 无需特判：「## 执行摘要」命中 SPEC。
        for li2, lvl2, t2 in heads[k + 1:]:
            if lvl2 <= lvl or match_heading(lvl2, t2) is not None:
                yield Section(sid, title, "\n".join(lines[li + 1:li2])), li2
                break
        else:
            yield Section(sid, title, "\n".join(lines[li + 1:])), len(lines)


class SectionScanner:
    """流式增量切节：feed(chunk) 在检测到下一标题时闭合并返回前一节。

    与 split_sections 等价（tests 以随机块长证明），闭节滞后约一个标题行。
    """

    def __init__(self) -> None:
        self.total_bytes = 0
        self._buf = ""            # 未完成行
        self._cur: tuple[str, str, list[str], int] | None = None  # (id,title,lines,lvl)
        self._meta_open = False   # 当前是否处于数据底稿 H1 层内

    @property
    def current_title(self) -> str | None:
        """正在撰写的节标题（供 thought 摘录条标注"模型写到哪"）。"""
        return self._cur[1] if self._cur else None

    def feed(self, chunk: str) -> list[tuple[str, str, str]]:
        if not chunk:
            return []
        self.total_bytes += len(chunk.encode("utf-8"))
        self._buf += chunk
        closed: list[tuple[str, str, str]] = []
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._consume_line(line, closed)
        return closed

    def finish(self) -> list[tuple[str, str, str]]:
        closed: list[tuple[str, str, str]] = []
        if self._buf:
            self._consume_line(self._buf, closed)
            self._buf = ""
        if self._cur:
            closed.append(self._pop_cur())
        return closed

    # ---- 内部 ----
    def _pop_cur(self) -> tuple[str, str, str]:
        sid, title, lines, _ = self._cur
        self._cur = None
        return sid, title, "\n".join(lines)

    def _consume_line(self, line: str, closed: list) -> None:
        m = _HEADING_RE.match(line)
        if not m:
            if self._cur:
                self._cur[2].append(line)
            return  # 首节前的散行（寒暄/围栏）丢弃
        lvl, title = len(m.group(1)), m.group(2).strip()
        sid = match_heading(lvl, title)
        if sid is None and lvl == 2 and self._meta_open:
            sid = f"appendix:{title}"
        if lvl == 1:
            self._meta_open = _is_meta_marker(lvl, title)
        # 闭节判据与 split_sections 一致：级别 <= 当前节 或 新标题命中节定义；
        # 嵌套未命中标题行（### 等）必须保留进正文——一次性切分本就含它们
        if self._cur:
            if lvl <= self._cur[3] or sid is not None:
                closed.append(self._pop_cur())
            else:
                self._cur[2].append(line)
        if sid:
            self._cur = (sid, title, [], lvl)


SPEC_IDS = frozenset(s[0] for s in SPEC)  # 供测试与服务层校验
