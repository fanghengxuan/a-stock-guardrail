"""数据路由注册表：路由文档（data_routes.md，方法真相）+ 有效性台账（data_routes_state.json，每跑必更）。

- 权重 = 100 + 10×连胜 − 20×连败，钳制 [0,150]；试用期路由（probation）基线 60，首次成功转正；
- 连败 ≥3 自动把条目从活动区移入文档退役区（行尾追加日期+死因，人工可复活）；
- 新增路由走两道机械闸门：①可执行性（必须 tool=<已注册工具>;variant=<参数> 或 tool=web_search;query=<模板>，
  防网页内容借 LLM 之手注入取数层/任意 URL 触发 em_get cookie 注入）②容量（每类活动路由 ≤5；
  每次运行 ≤1 条的上限由调用方 drilldown.run_drilldown 把关）。

md 与 json 的写全部经本模块：锁（RLock）+ 临时文件原子 replace；测试用 monkeypatch 替换
ROUTES_MD/STATE_JSON 路径即可全离线。
"""
from __future__ import annotations

import json
import pathlib
import re
import threading
import time

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
ROUTES_MD = _BACKEND / "data_routes.md"
STATE_JSON = _BACKEND / "data_routes_state.json"

# 闸门1 可执行性：可引用的工具 id = 7 个下钻工具 + web_search（任务 11 接入）。
# 与 drilldown._TOOL_DATA_KEY 的键一致；在此独立定义以避免 import 环（drilldown 依赖本模块）。
REGISTERED_TOOLS = frozenset({
    "get_bank_asset_quality", "get_margin_balance", "get_news_sentiment",
    "get_industry_valuation", "get_kline_52w", "get_announcements",
    "get_research_reports", "web_search",
})
# 值部分不用 `.`：排除全部 C0 控制字符（\x00-\x1f，含 splitlines 认得的 \v\f\x1c-\x1e\r\n）与
# 行分隔符（\u2028/\u2029/\u0085），防其混入使 splitlines 错切（\x0b\x0c\x1c-\x1e 自裁决 B 起拒收）
_SPEC_RE = re.compile(r"^tool=([A-Za-z0-9_]+);(variant|query)=\S[^\x00-\x1f\u2028\u2029\u0085]+$")
_ROUTE_RE = re.compile(r"^- \[(P\d+)\] (.+)$")
_RETIRE_THRESHOLD = 3
_MAX_ACTIVE_PER_CATEGORY = 5

_lock = threading.RLock()


def _read_state() -> dict:
    try:
        s = json.loads(STATE_JSON.read_text(encoding="utf-8"))
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    tmp = STATE_JSON.with_name(STATE_JSON.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_JSON)


def _atomic_write_md(text: str) -> None:
    tmp = ROUTES_MD.with_name(ROUTES_MD.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(ROUTES_MD)


# ============================================================
# 解析
# ============================================================

def load_routes() -> dict[str, list]:
    """解析 data_routes.md → {数据类: [Route]} + {"retired": [str]}。
    Route = {"id","path","cross_check","tolerance","notes"}，按文档书写序。
    `---` 之后（或"## 退役区"节）为退役区；节内 `- 互校验/勘误/…` 行归入该节各 Route 的元信息。"""
    try:
        text = ROUTES_MD.read_text(encoding="utf-8")
    except OSError:
        return {}
    result: dict[str, list] = {}
    retired: list[str] = []
    cat: str | None = None          # 当前活动节名（None=不收集路由条目）
    in_retired = False
    pending: list[dict] = []
    cross = tol = None
    notes: list[str] = []

    def _close():
        if cat is not None and not in_retired:
            for r in pending:
                r["cross_check"], r["tolerance"], r["notes"] = cross, tol, "；".join(notes)
            result[cat] = pending

    for line in text.splitlines():
        s = line.strip()
        if s == "---":
            _close()
            cat, in_retired, pending = None, True, []
            continue
        m = re.match(r"^## (.+)$", s)
        if m:
            _close()
            title = m.group(1).strip()
            in_retired = title.startswith("退役区")
            cat = None if in_retired else title
            pending, cross, tol, notes = [], None, [], []
            continue
        if in_retired:
            if s.startswith("- "):
                retired.append(s[2:])
            continue
        if cat is None:
            continue
        m = _ROUTE_RE.match(s)
        if m:
            pending.append({"id": m.group(1), "path": m.group(2).strip(),
                            "cross_check": None, "tolerance": None, "notes": ""})
        elif s.startswith("- "):
            body = s[2:]
            key, _, val = body.partition("：")
            if key.startswith("互校验"):
                cross = val.strip()
                t = re.search(r"容差[:：]?([0-9.]+%?)", val)
                if t:
                    tol = t.group(1)
            else:  # 单源声明/勘误/备注/主路径等说明行 → 节注记
                notes.append(body)
    _close()
    result["retired"] = retired
    return result


# ============================================================
# 权重与台账
# ============================================================

def route_weight(category: str, route_id: str) -> int:
    """权重 = base + 10×连胜 − 20×连败，钳制 [0,150]；probation 基线 60，否则 100。"""
    e = _read_state().get(f"{category}:{route_id}") or {}
    base = 60 if e.get("probation") else 100
    return max(0, min(150, base + 10 * int(e.get("ok_streak", 0)) - 20 * int(e.get("fail_streak", 0))))


def ordered_routes(category: str) -> list:
    """活动路由按权重降序（同分保持文档序）——即 _call 的降级尝试顺序。"""
    routes = load_routes().get(category) or []
    return sorted(routes, key=lambda r: -route_weight(category, r["id"]))


def record_success(category: str, route_id: str) -> None:
    key = f"{category}:{route_id}"
    with _lock:
        state = _read_state()
        e = state.setdefault(key, {"ok_streak": 0, "fail_streak": 0})
        e["ok_streak"] = int(e.get("ok_streak", 0)) + 1
        e["fail_streak"] = 0
        e["last_ok"] = time.time()
        e.pop("probation", None)  # 试用期首次命中转正（基线回 100）
        _write_state(state)


def record_failure(category: str, route_id: str, error: str) -> None:
    key = f"{category}:{route_id}"
    with _lock:
        state = _read_state()
        e = state.setdefault(key, {"ok_streak": 0, "fail_streak": 0})
        e["ok_streak"] = 0
        e["fail_streak"] = int(e.get("fail_streak", 0)) + 1
        e["last_error"] = str(error)[:200]
        _write_state(state)


def retire_if_dead(category: str, route_id: str) -> bool:
    """fail_streak ≥3 → 把该条改写进 data_routes.md 退役区（行尾追加退役日期+死因截断40字）。"""
    e = _read_state().get(f"{category}:{route_id}") or {}
    if int(e.get("fail_streak", 0)) < _RETIRE_THRESHOLD:
        return False
    with _lock:
        try:
            lines = ROUTES_MD.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        sec_i = path = None
        in_sec = False
        after_div = False
        for i, line in enumerate(lines):
            s = line.strip()
            if s == "---":
                after_div = True
                continue
            if re.match(r"^## ", s):
                in_sec = (not after_div) and s[3:].strip() == category
                continue
            if in_sec:
                m = _ROUTE_RE.match(s)
                if m and m.group(1) == route_id:
                    sec_i, path = i, m.group(2).strip()
                    break
        if sec_i is None:
            return False
        err = str(e.get("last_error") or "连续失败")[:40]
        retired_line = f"- {category}·{path}（退役 {time.strftime('%Y-%m-%d')}：{err}）"
        del lines[sec_i]
        div = next((i for i, l in enumerate(lines) if l.strip() == "---"), None)
        if div is None:
            lines += ["", "---", "## 退役区（自动移入，人工可复活）", retired_line]
        else:
            lines.append(retired_line)
        _atomic_write_md("\n".join(lines) + "\n")
        return True


# ============================================================
# prompt 注入
# ============================================================

def _short_label(path: str) -> str:
    toks = [t for t in re.split(r"[\s（(，,；;：:]+", path) if t]
    label = ""
    for t in toks:
        label += t
        if len(label) >= 6:
            break
    return label[:12]


def route_health_summary() -> str:
    """每数据类一行：权重序+最近验证日期+失效警告，供下钻 Agent instructions 注入。
    只报告有活动路由或有台账历史的类（后者=全部退役，报 ✗ 提示换源）。"""
    routes = load_routes()
    state = _read_state()
    cats = [c for c, rts in routes.items()
            if c != "retired" and (rts or any(k.startswith(f"{c}:") for k in state))]
    lines = []
    for cat in cats:
        rts = ordered_routes(cat)
        if not rts:
            lines.append(f"✗ {cat}：全部路由失效，换源并把新路径写入回报")
            continue
        frags, warn = [], False
        for r in rts:
            e = state.get(f"{cat}:{r['id']}") or {}
            frag = f"{r['id']}{_short_label(r['path'])}(权{route_weight(cat, r['id'])}"
            if e.get("probation"):
                frag += ",试用"
            if e.get("last_ok"):
                frag += f",{time.strftime('%m-%d', time.localtime(e['last_ok']))}验证"
            if int(e.get("fail_streak", 0)) >= 2:
                frag += f"⚠连续{e['fail_streak']}次失败"
                warn = True
            frags.append(frag + ")")
        lines.append(f"{'⚠' if warn else '✓'} {cat}：" + " > ".join(frags))
    return "\n".join(lines)


# ============================================================
# 自动入表（新增路由，无人工闸门）
# ============================================================

def _executability_ok(spec: str) -> bool:
    m = _SPEC_RE.match(spec.strip())
    if not m:
        return False
    tool, key = m.group(1), m.group(2)
    if tool not in REGISTERED_TOOLS:
        return False
    return key == "query" if tool == "web_search" else key == "variant"


def try_auto_add_route(category: str, spec: str) -> bool:
    """LLM 回报的新路径自动入表：闸门1 可执行性 + 闸门2 容量（该类活动路由 ≥5 拒；
    每次运行 ≤1 条由调用方把关）。通过则写入 `- [P<next>] <spec> [auto·试用 <日期>]` 并置 probation。"""
    category = (category or "").strip()
    spec = (spec or "").strip()
    if not category or not _executability_ok(spec):
        return False
    with _lock:
        routes = load_routes()
        if len(routes.get(category) or []) >= _MAX_ACTIVE_PER_CATEGORY:
            return False
        try:
            lines = ROUTES_MD.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = ["# 数据路由文档", ""]
        ids = [int(r["id"][1:]) for r in routes.get(category) or []]
        new_id = f"P{max(ids, default=0) + 1}"
        entry = f"- [{new_id}] {spec} [auto·试用 {time.strftime('%Y-%m-%d')}]"
        sec_i = None
        after_div = False
        for i, line in enumerate(lines):
            s = line.strip()
            if s == "---":
                after_div = True
                continue
            if after_div:
                continue
            m = re.match(r"^## (.+)$", s)
            if m:
                if sec_i is not None:
                    break  # 已到下一节，插入点在上一行
                if m.group(1).strip().startswith(category):
                    sec_i = i
        if sec_i is None:  # 节不存在 → 在 --- 前（或文末）新建
            div = next((i for i, l in enumerate(lines) if l.strip() == "---"), len(lines))
            lines[div:div] = [f"## {category}", entry, ""]
        else:
            j = sec_i + 1
            while j < len(lines) and not re.match(r"^## |^---$", lines[j].strip()):
                j += 1
            while j > sec_i + 1 and not lines[j - 1].strip():
                j -= 1  # 越过节尾空行，贴到该节最后一条目之后
            lines[j:j] = [entry]
        _atomic_write_md("\n".join(lines) + "\n")
        state = _read_state()
        e = state.setdefault(f"{category}:{new_id}", {"ok_streak": 0, "fail_streak": 0})
        e["probation"] = True
        _write_state(state)
        return True
