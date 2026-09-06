"""report_direct.md → DashboardData（看板变量）确定性解析器。

原则：数字只来自正则捕获组（100% 忠实报告原文），唯一派生=距现价%。
降级三层：extractor 异常/关键槽位缺 → {status:"raw", raw_md 节原文}；
整节缺 → {status:"una"}（前端删面板）；跨节块一级缺 → 二级回退链 → 子项删行
{status:"partial"}。永不空屏、永不造数。

字段口径与《执行手册》输出模板一致；视觉与颜色语义由前端样式承载
（styles/memo.css），本模块只产数据。
"""
from __future__ import annotations

import datetime
import json as _json
import re

from report_generator import _TABLE_SEP_RE, _strip_fences

from dashboard.scanner import Section, split_sections

SCHEMA_VERSION = 1

DISCLAIMER = "以上内容为个股信息整理与研究分析，不构成投资建议。"

STATE_ZH = {"StrongLong": "强势做多", "ValueReversion": "价值回归", "Range": "区间震荡",
            "Watch": "持有观察", "Exit": "回避退出"}
# 信号枚举 → 极性（pipeline G4 计数与五维卡配色用）
POLARITY = {"UP": "pos", "POS": "pos", "POSITIVE": "pos", "LOW": "pos", "OK": "pos",
            "DOWN": "neg", "NEG": "neg", "NEGATIVE": "neg", "HIGH": "neg", "POOR": "neg",
            "FLAT": "neu", "NEU": "neu", "NEUTRAL": "neu", "FAIR": "neu",
            "CAU": "neu", "CAUTION": "neu", "GREED": "neu"}
_ENUM_RE = re.compile(r"(POSITIVE|NEGATIVE|CAUTION|NEUTRAL|UP|DOWN|FLAT|POS|NEG|NEU|LOW|FAIR|HIGH|OK|POOR|CAU|GREED)")
_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_YEAR_RE = re.compile(r"(20\d{2})\s*年")


# ---------- 通用小工具 ----------

def strip_bold(s: str) -> str:
    return s.replace("**", "").strip()


def clean_num(s: str | None) -> float | None:
    """首个数字（全角负号归一、去千分位）。匹配不到 → None。"""
    if s is None:
        return None
    m = _NUM_RE.search(str(s).replace("−", "-").replace("≈", "").replace("约", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_range(s: str | None) -> tuple[float | None, float | None]:
    """'52-56元' / '44-48元（下方极端42元）' / '58.24' → (lo, hi|None)。"""
    if s is None:
        return None, None
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)", str(s))
    if m:
        return float(m.group(1)), float(m.group(2))
    v = clean_num(s)
    return v, v


def md_tables(text: str) -> list[dict]:
    """节内全部管道表 → [{headers(去粗), rows:[{header: cell}], raw}]，分隔行剔除、列数容错。"""
    blocks, cur = [], []
    for ln in text.splitlines():
        if ln.strip().startswith("|"):
            cur.append(ln.strip())
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    out = []
    for t in blocks:
        raw_rows = [[strip_bold(c) for c in r.strip("|").split("|")]
                    for r in t if not _TABLE_SEP_RE.match(r)]
        if len(raw_rows) < 2:
            continue
        headers = raw_rows[0]
        rows = []
        for r in raw_rows[1:]:
            n = max(len(headers), len(r))
            rows.append({(headers[i] if i < len(headers) else f"_c{i}"):
                         (r[i] if i < len(r) else "") for i in range(n)})
        out.append({"headers": headers, "rows": rows, "raw": "\n".join(t)})
    return out


def cell_of(table: dict | None, label_pat: str):
    """表内首列（默认）匹配 label_pat 的首行 → 该行 dict；找不到 None。"""
    if not table:
        return None
    pat = re.compile(label_pat)
    first_key = table["headers"][0]
    for r in table["rows"]:
        if pat.search(r.get(first_key, "")):
            return r
    return None


def cell_value(table: dict | None, label_pat: str, value_col: int = 1) -> str | None:
    row = cell_of(table, label_pat)
    if not row:
        return None
    keys = list(row.keys())
    return row.get(keys[value_col]) if value_col < len(keys) else None


def labeled_pairs(text: str) -> list[dict]:
    """**标签：** 正文 / **标签：整行加粗** 两种粗体标签行 → [{label,text}]。"""
    out = []
    for ln in text.splitlines():
        ln = ln.strip().strip("│").strip()  # 箱线卡内的粗体标签行同样适用（两侧框线）
        m = re.match(r"^\*\*(.+?)[:：]\*\*\s*(.+)$", ln) or \
            re.match(r"^\*\*(.+?)[:：]\s*(.+?)\*\*$", ln)
        if m:
            out.append({"label": strip_bold(m.group(1)), "text": strip_bold(m.group(2))})
    return out


def bullets_after(text: str, label_pat: str) -> list[str]:
    """紧跟某标签行之后、连续的 '-'/数字编号 列表项（去粗，保冒号后语义）。"""
    lines, on = text.splitlines(), False
    out: list[str] = []
    pat = re.compile(label_pat)
    for ln in lines:
        s = ln.strip()
        if not on:
            on = bool(pat.search(s))
        elif s.startswith(("-", "*")) or re.match(r"^\d+[.、]", s):
            out.append(strip_bold(re.sub(r"^(?:[-*]|\d+[.、])\s*", "", s)))
        elif s == "":
            continue
        else:
            break
    return out


def parse_mid(lo: float | None, hi: float | None) -> float | None:
    if lo is None:
        return None
    return (lo + hi) / 2 if (hi is not None and hi != lo) else lo


def sub_h3(text: str) -> dict[str, str]:
    """节内按 H3 子标题切片：{'第一步：身份识别': body,...}。"""
    out: dict[str, str] = {}
    cur_t, buf = None, []
    for ln in text.splitlines():
        m = re.match(r"^###\s+(.*)$", ln)
        if m:
            if cur_t:
                out[cur_t] = "\n".join(buf)
            cur_t, buf = m.group(1).strip(), []
        elif cur_t:
            buf.append(ln)
    if cur_t:
        out[cur_t] = "\n".join(buf)
    return out


# ---------- 箱线卡标签块抽取（唯一箱线工具：标签定位，不做网格重建） ----------

CARD_TAGS = {"trace": "数据溯源", "anchors": "锚点操作建议", "clear": "持有期间清仓条件清单",
             "risks": "风险提示", "hard": "硬门槛筛选", "state": "状态机输出"}


def card_blocks(card_text: str) -> dict[str, list[str]]:
    """按 emoji 标签定位框内区段：{'anchors': [行,...], ...}（行已去框线符与粗体符）。"""
    out: dict[str, list[str]] = {}
    cur: str | None = None
    for ln in card_text.splitlines():
        if not ln.strip().startswith("│"):
            if cur and re.match(r"^\s*$", ln):
                pass  # 框内空行（少见）忽略
            continue
        if re.match(r"^[├└]", ln.strip()) or ln.strip().startswith("┌"):
            cur = None
            continue
        body = strip_bold(ln.split("│", 1)[1].rsplit("│", 1)[0]).strip()
        tag_hit = None
        for k, kw in CARD_TAGS.items():
            if kw in body and (body.startswith(("✅", "❌", "⚠️", "📊", "🔗", "📋", "🎯", "📈", "⚖️", "🧠", "📉")) or body.startswith(kw)):
                tag_hit = k
                break
        if tag_hit:
            # 标签行本身也保留（含结论，如「硬门槛筛选：等效不通过（受限）」）
            cur = tag_hit
            out.setdefault(cur, []).append(body)
        elif cur:
            out[cur].append(body)
    return out


# ---------- 区块 extractors ----------

def _sec(sections: dict[str, Section], sid: str) -> str:
    s = sections.get(sid)
    return s.text if s else ""


def _title(sections: dict[str, Section], sid: str) -> str:
    s = sections.get(sid)
    return s.title if s else ""


def _ex_meta(sections):
    m = re.search(r"标的[：:]\s*(\S+?)\s+代码[：:]\s*(\d{6})\s+取数时间[：:]\s*(\S+)",
                  _title(sections, "meta"))
    if not m:
        return None
    name, code, date = m.group(1), m.group(2), m.group(3)
    model = None
    for t in md_tables(_sec(sections, "l2_hardgate")):
        for row in t["rows"]:
            k = list(row)
            if len(k) > 2 and re.match(r"^模型[一二三四]（", row[k[0]]) \
                    and strip_bold(row[k[2]]).startswith("✅"):
                model = row[k[0]]
                break
        if model:
            break
    if not model:
        cb = card_blocks(_sec(sections, "l3_card"))
        for ln in cb.get("hard", []):
            mm = re.search(r"(模型[一二三四]（[^）]+）)", ln)
            if mm:
                model = mm.group(1)
                break
    chip = None
    if model:
        chip = ("银行板块 = 系统最优适用领域" if "银行" in model
                else f"适用模型 {model}")
    return {"code": code, "name": name, "analysis_date": date,
            "model_label": model, "exchange": "SH" if code[0] in "569" else "SZ",
            "manual_chip": "A股投资决策系统执行手册", "sector_chip": chip}



def _pct_num(s: str | None) -> float | None:
    """百分率数值（股息率等）：clean_num 首数字可能是年份前缀（如「2025年度…1.91%」
    → 2025），而百分率合理域在 0-100；异常值回退取带 % 的数字，防 2025% 型脏值。"""
    n = clean_num(s)
    if n is None:
        return None
    if 0 <= n <= 100:
        return n
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", str(s or ""))
    return float(m.group(1)) if m else None


def _ex_quote(sections):
    t = (md_tables(_sec(sections, "l0_quote")) or [None])[0]
    if not t:
        return None
    def v(pat):
        row = cell_of(t, pat)
        return (row or {}).get(list(row)[1], "") if row else None
    price_s, chg_s = v(r"^最新价$"), v(r"^涨跌幅$")
    if price_s is None:
        return None
    chg = clean_num(chg_s)
    return {"price": clean_num(price_s), "price_text": strip_bold(price_s),
            "change_pct": chg, "up": (chg or 0) >= 0,
            "pe": clean_num(v(r"^PE")), "pe_text": strip_bold(v(r"^PE") or ""),
            "pb": clean_num(v(r"^PB$")), "pb_text": strip_bold(v(r"^PB$") or ""),
            "dy": _pct_num(v(r"^股息率")), "dy_text": strip_bold(v(r"^股息率") or ""),
            "dy_status": strip_bold((cell_of(t, r"^股息率") or {}).get("状态", "")),
            "market_cap_yi": clean_num(v(r"^总市值$")),
            "high_52w": clean_num(v(r"^52周最高")), "low_52w": clean_num(v(r"^52周最低"))}


_METRIC_CHAIN = [(r"^不良贷款率|^不良率$", "不良率", "%"), (r"^拨备覆盖率$", "拨备覆盖率", "%"),
                 (r"^净息差|^NIM", "净息差(NIM)", "%"), (r"^资本充足率$", "资本充足率", "%"),
                 (r"偿付能力充足率", "核心偿付能力", "%"), (r"^每股净资产$", "每股净资产", "元"),
                 (r"^换手率$", "换手率", "%")]


def _ex_metrics(sections):
    qt = (md_tables(_sec(sections, "l0_quote")) or [None])[0]
    ft = md_tables(_sec(sections, "l0_financial"))
    if not qt:
        return None
    items = []
    q = _ex_quote(sections) or {}
    for k, val, txt, unit in (("PE(TTM)", q.get("pe"), q.get("pe_text"), "倍"),
                              ("PB", q.get("pb"), q.get("pb_text"), "倍"),
                              ("股息率(TTM)", q.get("dy"), q.get("dy_text"), "%")):
        if val is None:
            continue
        st = "存疑" if ("存疑" in (txt or "") or "争议" in (txt or "")) else None
        items.append({"k": k, "v": val, "unit": unit, "status_chip": st})
    for pat, name, unit in _METRIC_CHAIN:  # 行业专属链：首个命中即用，缺则删格
        row = cell_of(qt, pat) or next((cell_of(t, pat) for t in ft if t), None)
        if row:
            val = clean_num(row.get(list(row)[1], ""))
            if val is not None:
                items.append({"k": name, "v": val, "unit": unit,
                              "status_chip": "存疑" if "存疑" in str(row) else None})
            break
    if q.get("market_cap_yi") is not None:
        items.append({"k": "总市值", "v": q["market_cap_yi"], "unit": "亿元", "status_chip": None})
    return {"items": items[:5]}


def _ex_stamp(sections):
    txt = _sec(sections, "l2_state")
    if not txt:
        return None
    row = None
    for ln in txt.splitlines():
        if "状态机输出" in ln:
            row = strip_bold(ln)
            break
    if row is None:  # blockquote 回退形态
        m = re.search(r"状态[：:]\s*(\w+)", txt)
        if not m:
            return None
        row = m.group(0)
    m = re.search(r"状态机输出[：:]\s*([A-Za-z]+)(?:（([^）]*)）)?", row)
    if not m:
        m = re.search(r"状态[：:]\s*([A-Za-z]+)", row)
    if not m:  # 「状态机输出结论：」独立成行，状态名在下条 bullet
        m = re.search(r"状态[：:]\s*\*{0,2}([A-Za-z]+)(?:（([^）]*)）)?", txt)
    if not m:  # 表格行形态（600941）：| **状态** | **Watch（偏Range）** |
        for ln in txt.splitlines():
            s = strip_bold(ln).strip()
            m = re.match(r"^\|?\s*状态\s*\|\s*([A-Za-z]+)(?:（([^）]*)）)?\s*\|?$", s)
            if m:
                break
    if not m:
        return None
    en = m.group(1)
    paren = m.group(2) or ""
    # 仓位/置信度可能在输出行，也可能在下方 blockquote 明细行——全节搜
    mm = re.search(r"仓位上限[^\d]*(\d+(?:\.\d+)?)\s*%", txt)
    cap = clean_num(mm.group(1)) if mm else None
    mm = re.search(r"置信度[：:为]?\s*\*{0,2}(极低|[高中低])", txt)
    conf = mm.group(1) if mm else None
    if cap is None or conf is None:  # 三级：卡 🎯 状态块（该形态置信度只在卡内）
        for ln in card_blocks(_sec(sections, "l3_card")).get("state", []):
            if cap is None:
                mm = re.search(r"仓位上限[^\d]*(\d+(?:\.\d+)?)\s*%", ln)
                cap = clean_num(mm.group(1)) if mm else None
            if conf is None:
                mm = re.search(r"置信度[：:为]?\s*(极低|[高中低])", ln)
                conf = mm.group(1) if mm else None
    return {"en": en, "state_raw": f"{en}（{paren}）" if paren else en,
            "zh": STATE_ZH.get(en, en), "sub": paren or None,
            "position_cap_pct": cap, "confidence": conf,
            "reason": next((strip_bold(ln) for ln in txt.splitlines()
                            if ln.strip().startswith(">")), None)}


def _ex_signals(sections):
    t = (md_tables(_sec(sections, "l2_five")) or [None])[0]
    if not t:
        return None
    items = []
    has_score_col = "计分" in t["headers"] or "计分" in "".join(t["headers"])
    for row in t["rows"]:
        keys = list(row)
        name_s, sig_s = row.get(keys[0], ""), row.get(keys[1], "")
        basis = row.get("依据") or row.get(keys[3], "") if has_score_col else row.get(keys[2], "")
        m = re.match(r"^(.+?)（([A-Za-z ]+)）$", name_s)
        name, en = (m.group(1), m.group(2).upper()) if m else (name_s, None)
        enum = None
        mm = _ENUM_RE.search(sig_s)
        if mm:
            enum = mm.group(1)
        score = clean_num(sig_s) if ("分" in sig_s or "/100" in sig_s) else None
        delta = None
        if has_score_col:
            sc_s = row.get("计分", "")
            delta = 1 if "+1" in sc_s else (-1 if "-1" in sc_s else (0 if sc_s.strip() in ("0", "0分") else None))
        items.append({"name": name, "en": en, "signal": sig_s, "enum": enum,
                      "polarity": POLARITY.get(enum or "", "neu"),
                      "score": score, "delta": delta, "basis": basis})
    if len(items) < 3:
        return None
    out: dict = {"items": items}
    m = re.search(r"五维正向数量[：:]\*{0,2}\s*(\d+)", _sec(sections, "l2_five"))
    if m:
        out["pos_count"] = int(m.group(1))
    return out


def _ex_debate(sections):
    txt = _sec(sections, "l2_debate")
    if not txt:
        return None
    factions: dict[str, dict] = {}
    cur = None  # 双行形态：「**价值派（权重28%）**」+「**结论：…**」（600036）
    for ln in txt.splitlines():
        s = strip_bold(ln).strip().strip("│").strip()
        m = re.search(r"(\S{1,3})派（\s*权重\s*(\d+)\s*%\s*）——\s*结论[：:]\s*([^│\n]+)", s)  # 同行形态（601318）
        if m:
            factions[m.group(1)] = {"name": m.group(1) + "派", "weight_pct": int(m.group(2)),
                                    "stance": m.group(3).strip(), "score": None}
            cur = None
            continue
        m = re.match(r"^(\S{1,3})派（\s*权重\s*(\d+)\s*%\s*）$", s)  # 双行形态（600036/600089）
        if m:
            cur = m.group(1)
            factions.setdefault(cur, {"name": cur + "派", "weight_pct": int(m.group(2)),
                                      "stance": None, "score": None})
            continue
        m = re.match(r"^结论[：:]\s*(\S.+)$", s)
        if m and cur and factions[cur]["stance"] is None:
            factions[cur]["stance"] = m.group(1)
            cur = None
            continue
        for mm in re.finditer(r"(\S{1,3})派\s*(\d+(?:\.\d+)?)分\s*×\s*(\d+)%", s):
            f = factions.setdefault(mm.group(1),
                                    {"name": mm.group(1) + "派", "weight_pct": int(mm.group(3)),
                                     "stance": None, "score": None})
            f["score"] = float(mm.group(2))
        # 无逐项×权重形态：「价值派75分、成长派70分…→ 加权=…」
        # 前缀限汉字，防顿号/引号被 \S 吞入键名
        for mm in re.finditer(r"([一-龥]{1,2})派\s*(\d+(?:\.\d+)?)分(?!\s*×)", s):
            f = factions.setdefault(mm.group(1),
                                    {"name": mm.group(1) + "派", "weight_pct": None,
                                     "stance": None, "score": None})
            f["score"] = float(mm.group(2))
        # 裸名简写形态：「计算：价值45×28% + 成长55×25%…」（600089）
        # 名单锁死五派，防误吞其它数字表达式
        for mm in re.finditer(r"(价值|成长|质量|资金|情绪)\s*(\d+(?:\.\d+)?)\s*×\s*(\d+)\s*%", s):
            f = factions.setdefault(mm.group(1),
                                    {"name": mm.group(1) + "派", "weight_pct": int(mm.group(3)),
                                     "stance": None, "score": None})
            f["score"] = float(mm.group(2))
        # 综合裁定映射行形态：「价值派+2→60、成长派-1→45…」（002236）
        # 分数写在裁定行，取 → 后的 0-100 映射值；只覆盖 score 为 None 的派，不覆盖逐项分。
        for mm in re.finditer(r"(价值|成长|质量|资金|情绪)派\s*[+-]?\s*\d+(?:\.\d+)?\s*→\s*(\d+(?:\.\d+)?)", s):
            f = factions.setdefault(mm.group(1),
                                    {"name": mm.group(1) + "派", "weight_pct": None,
                                     "stance": None, "score": None})
            if f["score"] is None:
                f["score"] = float(mm.group(2))
    rows = list(factions.values())
    no_scores = not any(f["score"] is not None for f in factions.values())
    comp_s = None
    # 综合分两种实证形态：公式行尾「= **77.3/100**」或「= **70.0分**」
    for m in re.finditer(r"=\s*\*{0,2}(\d+(?:\.\d+)?)\s*(?:/100|分)", strip_bold(txt)):
        comp_s = float(m.group(1))
    if comp_s is None:
        m = re.search(r"综合[得成]分[：:]\s*\*{0,2}约?(\d+(?:\.\d+)?)(?:\s*/\s*100)", strip_bold(txt))
        if m:
            comp_s = float(m.group(1))
    verdict = consensus = divergence = None
    for d in labeled_pairs(txt):
        lab = d["label"]
        if lab.startswith("综合裁定"):
            verdict = d["text"]
        elif lab.startswith("裁定理由") and verdict is None:
            verdict = d["text"][:40]
        elif lab.startswith("最大共识"):
            consensus = d["text"]
        elif lab.startswith("最大分歧"):
            divergence = d["text"]
    if not rows and verdict is None and consensus is None:
        return None
    out = {"factions": rows, "composite": comp_s,
           "composite_text": (f"{comp_s}/100" if comp_s is not None else None),
           "verdict": verdict, "consensus": consensus, "divergence": divergence}
    if no_scores:  # 定性版辩论（600089：五派无逐派分值）——非解析降级，条形渲染 0/—
        out["qualitative"] = True
    return out


def _ex_gates(sections):
    hard = reverse = None
    htxt = _sec(sections, "l2_hardgate")
    if htxt:
        rows = []
        for t in md_tables(htxt):
            if "模型" not in t["headers"][0] or len(t["headers"]) < 3:
                continue
            for row in t["rows"]:
                k = list(row)
                res = row.get(k[2], "")
                ok = ("✅" in res) if ("✅" in res or "❌" in res) else None
                rows.append({"item": row[k[0]], "condition": row.get(k[1], ""),
                             "result": res, "ok": ok})
            if rows:
                break
        concl = next((strip_bold(ln) for ln in htxt.splitlines()
                      if re.match(r"^\*\*结论", ln.strip())), None)
        checklist = [{"text": b, "ok": ("❌" not in b and "不通过" not in b)}
                     for b in bullets_after(htxt, r"硬门槛校验清单")]
        hard = {"rows": rows, "conclusion": concl, "checklist": checklist}
    rtxt = _sec(sections, "l0_reverse")
    rtitle = _title(sections, "l2_reverse")
    if rtxt or rtitle:
        rows = []
        for t in md_tables(rtxt):
            for row in t["rows"]:
                k = list(row)
                status = row.get(k[-1], "")
                ok = None if "UNA" in status else ("✅" in status or "正常" in status)
                rows.append({"item": row[k[0]], "value": row.get(k[1], ""),
                             "status": status, "ok": ok})
            break
        dg = clean_num((re.search(r"降级\s*(\d+)\s*档", rtitle).group(1)
                        if re.search(r"降级\s*(\d+)\s*档", rtitle) else
                        ("0" if "无置信度降级" in rtitle else None)))
        reverse = {"conclusion": strip_bold(rtitle) or None, "rows": rows, "downgrades": dg}
    if hard is None and reverse is None:
        return None
    out = {"hard": hard, "reverse": reverse}
    out["status"] = "ok" if (hard and reverse) else "partial"
    return out


def _ex_traps(sections):
    txt = _sec(sections, "l2_trap")
    if not txt:
        return None
    rows, trig = [], 0
    for t in md_tables(txt):
        if "信号" not in t["headers"][0] and "陷阱" not in "".join(t["headers"]):
            continue
        for row in t["rows"]:
            k = list(row)
            verdict = row.get(k[-1], "")
            if "未触发" in verdict or "不适用" in verdict:
                ok = None if "不适用" in verdict else False
            elif "触发" in verdict:
                ok = True
                trig += 1
            else:
                ok = None
            rows.append({"signal": row[k[0]], "evidence": row.get(k[1], ""),
                         "verdict": verdict, "triggered": ok})
        break
    concl = None
    m = re.search(r"价值陷阱结论[：:]\*{0,2}\s*([^。\n]+)", txt)
    if m:
        concl = strip_bold(m.group(1))
    if not rows:
        return None
    out: dict = {"rows": rows, "triggered_count": trig, "conclusion_text": concl}
    m = re.search(r"类型[：:]\*{0,2}\s*(确认价值陷阱|潜在价值陷阱（拐点已现）|潜在价值陷阱|无价值陷阱|无)", txt)
    if m:
        out["trap_type"] = m.group(1)
    m = re.search(r"分级[：:]\*{0,2}\s*(轻度|中度|重度)", txt)
    if m:
        out["grading"] = m.group(1)
    return out


def _ex_roe(sections):
    yearly: dict[str, float] = {}
    cur = None
    for t in md_tables(_sec(sections, "l0_financial")):
        for row in t["rows"]:
            k = list(row)
            label = row[k[0]]
            m = _YEAR_RE.search(label)
            others = [row[x] for x in k[1:]]
            if m and not any(others):  # 期间标记行
                if "半年" in label and "年化" not in label:
                    cur = None
                else:
                    cur = m.group(1)
            elif cur and "ROE" in label.upper() and "半年" not in label:
                v = clean_num(row.get(k[1], ""))
                if v is not None:
                    yearly.setdefault(cur, v)
    # 二级：价值陷阱首行箭头链（2023年16.22%→2024年14.49%→...）
    for m in re.finditer(r"(20\d{2})年\s*(\d+(?:\.\d+)?)%", _sec(sections, "l2_trap")):
        yearly.setdefault(m.group(1), float(m.group(2)))
    if not yearly:
        return None
    bars = [{"yr": y, "v": yearly[y]} for y in sorted(yearly)][-6:]
    return {"bars": bars}


def _ex_capital(sections):
    txt = _sec(sections, "l2_capital")
    if not txt:
        return None
    rows = []
    for t in md_tables(txt):
        for row in t["rows"]:
            k = list(row)
            rows.append({"type": row[k[0]], "movement": row.get(k[1], ""),
                         "judgment": row.get(k[2], "")})
        break
    pairs = labeled_pairs(txt)
    rel = next((p["text"] for p in pairs if "合力" in p["label"]), None)
    return {"rows": rows, "pairs": pairs,
            "relation": ("分歧" if rel and "分歧" in rel else "合力") if rel else None}


def _ex_factor(sections):
    txt = _sec(sections, "l2_factor")
    if not txt:
        return None
    rows = []
    for t in md_tables(txt):
        for row in t["rows"]:
            k = list(row)
            rows.append({"factor": row[k[0]], "self": row.get(k[1], ""),
                         "industry": row.get(k[2], ""), "verdict": row.get(k[-1], "")})
        break
    pairs = labeled_pairs(txt)
    return {"note": pairs[0]["text"] if pairs else None, "rows": rows,
            "weights": next((p["text"] for p in pairs if "权重" in p["label"]), None)}


def _ex_behavior(sections):
    txt = _sec(sections, "l2_behavior")
    if not txt:
        return None
    rows = []
    for t in md_tables(txt):
        for row in t["rows"]:
            k = list(row)
            rows.append({"q": row[k[0]], "threshold": row.get(k[1], ""),
                         "actual": row.get(k[2], ""), "verdict": row.get(k[-1], "")})
        break
    concl = next((p["text"] for p in labeled_pairs(txt) if "结论" in p["label"]), None)
    return {"rows": rows, "conclusion": concl}


def _ex_stress(sections):
    txt = _sec(sections, "l2_stress")
    if not txt:
        return None
    subs = sub_h3(txt)
    def sub(*kws):
        return next((v for k, v in subs.items() if all(w in k for w in kws)), "")
    out: dict = {}
    idt = md_tables(sub("身份识别"))
    out["identity"] = [{"k": r[list(r)[0]], "v": r[list(r)[-1]]}
                       for t in idt for r in t["rows"]][:4]
    p2 = sub("悲观假设")
    out["paths"] = [{"risk": r[list(r)[0]], "prob": r.get(list(r)[1], ""),
                     "impact": r.get(list(r)[2], "")}
                    for t in md_tables(p2) for r in t["rows"]]
    out["path_notes"] = [strip_bold(ln) for ln in p2.splitlines()
                         if re.match(r"^\|?\s*\*\*(综合悲观|三条路径)", ln.strip())]
    p3 = sub("估值重算")
    m = re.search(r"悲观情景合理估值区间[：:]\*{0,2}\s*约?(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)\s*元", p3)
    out["pes_range"] = ({"lo": float(m.group(1)), "hi": float(m.group(2))} if m else None)
    out["vs_now_text"] = next((strip_bold(ln) for ln in p3.splitlines()
                               if "现价" in ln and ln.strip().startswith("**")), None)
    out["scenarios"] = [{"name": r[list(r)[0]], "assumption": r.get(list(r)[1], ""),
                         "anchor": r.get(list(r)[2], ""), "trigger": r.get(list(r)[3], ""),
                         "duration": r.get(list(r)[4], "") if len(list(r)) > 4 else ""}
                        for t in md_tables(sub("时间轴假设")) for r in t["rows"]]
    out["cross_rows"] = [{"anchor": r[list(r)[0]], "price": r.get(list(r)[1], ""),
                          "dist": r.get(list(r)[2], "")}
                         for t in md_tables(sub("交叉验证")) for r in t["rows"]]
    p6 = sub("状态机裁定")
    out["verdict_bullets"] = [strip_bold(ln.lstrip("-* ").strip()) for ln in p6.splitlines()
                              if ln.strip().startswith(("-", "*"))]
    out["final_line"] = next((strip_bold(ln) for ln in p6.splitlines()
                              if "最终裁定" in ln), None)
    return out


def _ex_quant(sections):
    txt = _sec(sections, "l2_quant")
    if not txt:
        return None
    subs = sub_h3(txt)
    dcf = next((v for k, v in subs.items() if "DCF" in k.upper()), "")
    var_t = next((md_tables(v) for k, v in subs.items() if "VaR" in k), [])
    alt_rows = [{"method": r[list(r)[0]], "result": r.get(list(r)[-1], "")}
                for t in md_tables(txt) for r in t["rows"]
                if len(list(r)) >= 2 and ("元" in str(r.get(list(r)[-1], "")) or "V=" in str(r.get(list(r)[-1], "")))]
    var_pct = var_amount = None
    var_src = "\n".join(t["raw"] for t in var_t) or \
        next((v for k, v in subs.items() if "VaR" in k), "")  # 表形态或 bullet-list 形态
    vln = next((ln for ln in var_src.splitlines() if re.search(r"5\s*日\s*VaR", ln)), None)
    if vln:
        mm = re.search(r"([\d.]+)\s*元", vln)
        var_amount = float(mm.group(1)) if mm else None
        pcts = re.findall(r"([\d.]+)\s*%", vln)  # 取尾段百分比（≈1.93元（约4.6%））
        var_pct = float(pcts[-1]) if pcts else None
    dcf_skipped = ("跳过" in dcf) or ("❌" in dcf)
    concl = next((p["text"] for p in labeled_pairs(txt)
                  if "综合参照" in p["label"] or "综合结论" in p["label"]), None)
    if concl is None:
        concl = next((strip_bold(ln) for ln in txt.splitlines()
                      if re.match(r"^综合结论[：:]", ln.strip())), None)
    # ── 四法联动：主法选择 + 各法结论行（报告无四法明细时为空）──
    # 实证双形态：「主法：X」行内式 与 选择表「| 主法 | **X**（注） |」表格式
    sel = next((v for k, v in subs.items() if "方法选择" in k), "")
    pm_txt = None
    m = re.search(r"主[法用][：:]\*{0,2}\s*([^\n（(，,。]+)", strip_bold(sel))
    if m:
        pm_txt = m.group(1).strip()
    else:
        st = _first_table(sel)
        srow = cell_of(st, r"^主法|^主估值法|^首选") if st else None
        if srow:
            v = strip_bold(srow[list(srow)[-1]])
            mm = re.search(r"(股息率估值法|股息率法|PE估值参照法|PE参照法|DCF估值法|DCF|PB-ROE估值法|PB-ROE|资产重估法|分部估值)", v)
            pm_txt = mm.group(1) if mm else v.split("（")[0][:14]
    bond_like = False
    srow_b = cell_of(_first_table(sel), "类债券") if sel else None
    if srow_b:
        bv = strip_bold(srow_b[list(srow_b)[-1]])
        bond_like = "成立" in bv and "不成立" not in bv
    methods = []
    for k, v in subs.items():
        if "选择" in k:
            continue
        for name in ("股息率", "PE", "DCF", "PB-ROE"):
            if name.upper() in k.upper():
                cands = [strip_bold(ln) for ln in v.splitlines()
                         if "元" in ln and clean_num(ln) is not None]
                # 结论行优先取目标价/区间类判断行，跳过中间的利润/股本推算行
                line = next((c for c in cands if re.search(r"目标价|合理|内在价值|区间|中枢|对应市值", c)),
                            cands[0] if cands else None)
                methods.append({"method": name, "text": line[:80] if line else None})
                break
    out = {"dcf_executed": not dcf_skipped,
           "dcf_note": next((strip_bold(ln) for ln in dcf.splitlines()
                             if ln.strip().startswith(("❌", "✅"))), None),
           "alt_rows": alt_rows,
           "alt_notes": bullets_after(dcf, r"替代参照|替代估值") if not alt_rows else [],
           "var_pct": var_pct, "var_amount": var_amount,
           "conclusion": concl}
    if pm_txt:
        out["primary_method"] = pm_txt
    if methods:
        out["methods"] = methods
    if bond_like:
        out["bond_like"] = True
    return out


def _ex_ruler(sections):
    q = _ex_quote(sections) or {}
    now = q.get("price")
    if now is None:
        return None
    rows = [{"label": "现价", "lo": now, "hi": None, "kind": "now", "price_text": q["price_text"]}]
    def add(label, lo, hi, kind, text=None, note=None):
        rows.append({"label": label, "lo": lo, "hi": hi, "kind": kind,
                     "price_text": text or f"{lo:.2f}元", "note": note})
    a = _ex_anchors(sections)
    merged_first = False
    if a:
        if a.get("stop_loss") and a["stop_loss"]["price"]:
            sl = a["stop_loss"]
            add("止损线", sl["price"], None, "stop", f"{sl['price']:.2f}元", sl.get("note"))
        for i, t in enumerate(a.get("tiers") or []):
            lo, hi = t.get("lo"), t.get("hi")
            if lo is None:
                continue
            if i == 0 and lo <= now <= (hi or lo):  # 第一档含现价 → 合并
                rows[0]["label"] = f"现价 ≈ {t['tier'] or '第一档'}"
                rows[0]["price_text"] = f"{now:.2f}元（{t['lo']:.2f}-{(t['hi'] or t['lo']):.2f}元）"
                merged_first = True
                continue
            add(t.get("tier") or f"第{'一二三'[i]}档", lo, hi,
                "up" if (t.get("mid") or lo) > now else "dn",
                f"{lo:.2f}-{(hi or lo):.2f}元", t.get("action"))
    ft = (md_tables(_sec(sections, "l0_forecast")) or [{}])[0]
    if ft.get("rows"):
        for pat, lab in ((r"目标价.*最高", "机构目标价上限"), (r"目标价.*最低", "机构目标价下限")):
            row = cell_of(ft, pat)
            if row:
                cell_txt = strip_bold(row[list(row)[1]])
                v = clean_num(cell_txt)
                if v:
                    rm = re.search(r"[\d.]+\s*元", cell_txt)
                    add(lab, v, None, "up" if v > now else "dn", rm.group(0) if rm else f"{v}元")
    st = _ex_stress(sections) or {}
    for sc in st.get("scenarios") or []:
        anchor = strip_bold(sc.get("anchor") or "")
        lo, hi = parse_range(anchor)
        if lo is None:
            continue
        # price_text 只取价格段，尾注（区间解释）挪进 note，避免标尺行混入长括号
        rm = re.search(r"[\d.]+\s*(?:[-~]\s*[\d.]+)?\s*元", anchor)
        price_text = rm.group(0) if rm else anchor
        tail = anchor[rm.end():].strip("（） ") if rm else None
        add(strip_bold(sc["name"]), lo, hi if hi != lo else None, "dn",
            price_text, tail or sc.get("duration"))
    if st.get("pes_range"):
        pr = st["pes_range"]
        add("悲观情景区间", pr["lo"], pr["hi"], "dn", f"{pr['lo']}-{pr['hi']}元")
    if q.get("low_52w"):
        add("52周低点", q["low_52w"], None, "dn" if q["low_52w"] < now else "up", f"{q['low_52w']}元")
    dedup: list[dict] = []
    for r in sorted(rows, key=lambda x: -x["lo"]):
        if dedup and abs(r["lo"] - dedup[-1]["lo"]) < 0.05 and r["kind"] == dedup[-1]["kind"]:
            continue
        mid = (r["hi"] + r["lo"]) / 2 if r.get("hi") and r["hi"] != r["lo"] else r["lo"]
        if r["kind"] not in ("now", "stop"):
            r["kind"] = "up" if mid > now else "dn"
        r["dist_pct"] = round((r["lo"] / now - 1) * 100, 1) if r["lo"] != now else None
        dedup.append(r)
    return {"now": now, "rows": dedup[:14], "merged_first": merged_first}


def _ex_anchors(sections):
    out: dict = {"tiers": [], "stop_loss": None, "current_action": None,
                 "cross_rows": [], "status": "ok"}
    # 卡内 🔗 块。档行两种实证形态：
    #   A「第一档（底仓）：55.50-58.50元 → ≤15%仓位」
    #   B「第一档（观察试仓，2%）：39.00-40.50元」「第三档（34.00-35.00元）：不自动加仓」
    cb = card_blocks(_sec(sections, "l3_card"))
    for ln in cb.get("anchors", []):
        m = re.match(r"^第([一二三])档(.*)$", ln)
        if m:
            idx = "一二三".index(m.group(1))
            rest = m.group(2)
            lo, hi = parse_range(rest)
            pm = re.match(r"^（([^）]*)）", rest)
            label = pm.group(1) if pm and "元" not in pm.group(1) else None
            am = re.search(r"→\s*([^│]+)$", rest)
            if am:
                action = strip_bold(am.group(1)) or None
            else:  # 去括号与价格区间后剩余文字（B 形态「：不自动加仓」）
                tail = re.sub(r"[\d.]+\s*[-~]\s*[\d.]+\s*元", "",
                              re.sub(r"[（(][^）)]*[）)]", "", rest)).strip("：: ")
                action = strip_bold(tail) or None
            out["tiers"].append({"tier": f"第{'一二三'[idx]}档", "label": label,
                                 "lo": lo, "hi": hi, "mid": parse_mid(lo, hi),
                                 "action": action, "note": None, "price_text": None})
            continue
        # 形态 B：「第一建仓锚点：94.0-95.5元（...）」（600941）
        m = re.match(r"^第([一二三])建仓锚点[：:](.*)$", ln)
        if m:
            idx = "一二三".index(m.group(1))
            rest = m.group(2)
            lo, hi = parse_range(rest)
            pm = re.search(r"[（(]([^）)]+)", rest)
            note = strip_bold(pm.group(1)) if pm else None
            out["tiers"].append({"tier": f"第{'一二三'[idx]}建仓锚点",
                                 "label": None, "lo": lo, "hi": hi,
                                 "mid": parse_mid(lo, hi), "action": None,
                                 "note": note, "price_text": None})
            continue
        # 形态 C：「观察位一：17.0-18.0元（...）」（600089）
        m = re.match(r"^观察位([一二三])(?:[：:](.*))?$", ln)
        if m:
            idx = "一二三".index(m.group(1))
            rest = m.group(2) or ""
            lo, hi = parse_range(rest)
            pm = re.search(r"[（(]([^）)]+)", rest)
            note = strip_bold(pm.group(1)) if pm else None
            out["tiers"].append({"tier": f"观察位{'一二三'[idx]}",
                                 "label": None, "lo": lo, "hi": hi,
                                 "mid": parse_mid(lo, hi), "action": None,
                                 "note": note, "price_text": None})
            continue
        # 续行：不以「第/观察位/止损/现价/说明」开头 = 上一 tier 的 note 续行（卡内框线折行）
        if out["tiers"] and out["tiers"][-1].get("note") is not None \
                and not re.match(r"^(第[一二三](?:档|建仓锚点)|观察位[一二三]|止损|现价|说明|或持仓)", ln):
            cont = strip_bold(ln).rstrip("）)│ ")
            out["tiers"][-1]["note"] = (out["tiers"][-1]["note"] or "").rstrip("）)│ ") + " " + cont
            continue
        if out["tiers"] and re.match(r"^（", ln) and out["tiers"][-1]["note"] is None:
            out["tiers"][-1]["note"] = ln.strip("（） ")
            continue
        m = re.match(r"^止损线[：:]\s*([\d.]+)\s*元(?:（([^）]*)）)?", ln)
        if m:
            out["stop_loss"] = {"price": float(m.group(1)), "text": strip_bold(ln),
                                "note": m.group(2)}
            continue
        # 形态 C：止损/清仓参考线：收盘跌破 13.68 元（600089）
        m = re.match(r"^止损[/／]清仓参考线[：:]\s*(.+)", ln)
        if m:
            rest = m.group(1)
            pm = re.search(r"([\d.]+)\s*元", rest)
            if pm:
                out["stop_loss"] = {"price": float(pm.group(1)),
                                    "text": strip_bold(ln), "note": None}
            continue
        # 形态 A/B：「现价 XX元 操作：...」「现价（XX元）操作：...」
        m = re.match(r"^现价\s*[（(]?([\d.]+)\s*元[）)]?\s*操作[：:]\s*(.+)", ln)
        if m:
            out["current_action"] = m.group(2)
            continue
        # 形态 C：「现价 18.88 操作：...」（无「元」字）（600089）
        m = re.match(r"^现价\s*([\d.]+)\s*操作[：:]\s*(.+)", ln)
        if m:
            out["current_action"] = m.group(2)
            continue
        # 形态 B 续：「现价操作：不建仓、不追高...」（无价格数字）（600941）
        m = re.match(r"^现价操作[：:]\s*(.+)", ln)
        if m:
            out["current_action"] = m.group(1)
            continue
        if out.get("current_action") and not re.match(r"^(第|止损|观察|现价)", ln):
            out["current_action"] += ln  # 折行续（「每周减1/3至≤5%观察仓」）
    st = _ex_stress(sections) or {}
    out["cross_rows"] = st.get("cross_rows") or []
    if not out["tiers"] or not out["stop_loss"]:  # 二级：执行摘要「第X档」行（宁缺勿错）
        sm = _sec(sections, "l3_summary")
        if not out["stop_loss"]:
            m = re.search(r"止损线[^\d]*([\d.]+)\s*元", sm)
            if m:
                out["stop_loss"] = {"price": float(m.group(1)),
                                    "text": f"{m.group(1)}元", "note": None}
                out["status"] = "partial"
        if not out["tiers"]:
            for ln in sm.splitlines():
                m = re.search(r"第([一二三])档", ln)
                if not m:
                    m = re.search(r"第([一二三])建仓锚点", ln)
                if not m:
                    m = re.search(r"观察位([一二三])", ln)
                if not m:
                    continue
                lo, hi = parse_range(ln)
                if lo is None:
                    continue
                am = re.match(r"^\s*\d*[\.、]?\s*\*\*([^*（(]+)", ln)
                out["tiers"].append({"tier": f"第{m.group(1)}档", "label": am.group(1).strip() or None,
                                     "lo": lo, "hi": hi, "mid": parse_mid(lo, hi),
                                     "action": None, "note": strip_bold(ln).strip("123.、 "),
                                     "price_text": None})
            out["status"] = "partial"
    if not out["tiers"] and not out["stop_loss"]:
        return None
    return out


def _ex_exits(sections):
    cb = card_blocks(_sec(sections, "l3_card"))
    clear = [{"text": re.sub(r"^\d+[\.、]\s*", "", ln), "src": "决策卡"}
             for ln in cb.get("clear", []) if re.match(r"^\d+[\.、]", ln)]
    if not clear:  # 二级
        st = _ex_stress(sections) or {}
        clear = [{"text": b, "src": "状态机裁定"} for b in st.get("verdict_bullets") or []
                 if "Watch" in b or "Exit" in b]
        if clear:
            clear.extend({"text": strip_bold(ln), "src": "止盈检查"}
                         for ln in _sec(sections, "l2_state").splitlines()
                         if "止盈" in ln and "未触发" not in ln and strip_bold(ln))
    sm = _sec(sections, "l3_summary")
    watch: list[str] = []
    m = re.search(r"跟踪验证点[^\n]+", sm) or re.search(r"验证点[^\n]+", sm)
    if m:
        parts = re.split(r"[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]", m.group(0))[1:]
        watch = [strip_bold(p).strip("；;。 ") for p in parts if strip_bold(p)]
    if not clear and not watch:
        return None
    return {"clear_list": clear, "watch_list": watch}


def _ex_risks(sections):
    items: list[str] = []
    cb = card_blocks(_sec(sections, "l3_card"))
    for ln in cb.get("risks", []):
        m = re.match(r"^\d+[\.、]\s*(.+)", ln)
        if m:
            items.append(m.group(1))
    if not items:  # 二级：执行摘要主要风险
        items = bullets_after(_sec(sections, "l3_summary"), r"主要风险")
    if not items:
        return None
    return {"items": [{"no": i + 1, "text": t, "hot": i < 3,
                       "chip": (re.match(r"^([一-龥A-Za-z]+风险)", t) or [None, None])[1]
                       or None}
                      for i, t in enumerate(items)]}


def _ex_trace(sections):
    main = backup = una = una_detail = completeness = credibility = None
    cb = card_blocks(_sec(sections, "l3_card"))
    for ln in cb.get("trace", []):
        m = re.search(r"主源成功[：:]\s*约?(\d+)\s*/\s*(\d+)", ln)
        if m:
            main = {"n": int(m.group(1)), "total": int(m.group(2))}
        m = re.search(r"备源降级[：:]\s*约?(\d+)\s*/\s*(\d+)", ln)
        if m:
            backup = {"n": int(m.group(1)), "total": int(m.group(2))}
        m = re.search(r"UNA标记[：:]\s*约?(\d+)\s*/\s*(\d+)(?:（([^）]*)）)?", ln)
        if m:
            una, una_detail = {"n": int(m.group(1)), "total": int(m.group(2))}, m.group(3)
        m = re.search(r"完整性[：:]\s*(\S+)", ln)
        if m:
            completeness = strip_bold(m.group(1)).strip("（(* ")
        m = re.search(r"可信度[：:]\s*(\S+)", ln)
        if m:
            credibility = strip_bold(m.group(1)).strip("（(* ")
    sources: list[str] = []
    if not main:  # 二级：L0 表状态列计票
        m = b = u = tot = 0
        for sid in ("l0_quote", "l0_financial", "l0_dividend", "l0_reverse",
                    "l0_flow", "l0_forecast"):
            for t in md_tables(_sec(sections, sid)):
                for row in t["rows"]:
                    cells = " ".join(str(x) for x in row.values())
                    if not cells.strip():
                        continue
                    tot += 1
                    if "UNA" in cells:
                        u += 1
                    elif "备源" in cells or "降级" in cells:
                        b += 1
                    elif "主源" in cells or "多源" in cells or "一致" in cells:
                        m += 1
                    if "来源" in row:
                        for s2 in re.split(r"[/（(]", strip_bold(row["来源"])):
                            if s2 and s2 not in sources and "—" not in s2:
                                sources.append(s2)
        if tot:
            main, backup, una = ({"n": m, "total": tot}, {"n": b, "total": tot},
                                 {"n": u, "total": tot})
    else:
        for sid in ("l0_quote", "l0_financial", "l0_dividend", "l0_reverse",
                    "l0_flow", "l0_forecast"):
            for t in md_tables(_sec(sections, sid)):
                for row in t["rows"]:
                    if "来源" in row:
                        for s2 in re.split(r"[/（(]", strip_bold(row["来源"])):
                            if s2 and s2 not in sources and "—" not in s2:
                                sources.append(s2)
    if not main and not sources:
        return None
    return {"main": main, "backup": backup, "una": una, "una_detail": una_detail,
            "completeness": completeness, "credibility": credibility,
            "sources": "、".join(sources[:10]), "disclaim": DISCLAIMER}


def _ex_summary(sections):
    txt = _sec(sections, "l3_summary")
    if not txt:
        return None
    headline = None
    for ln in txt.splitlines():
        s = ln.strip()
        if s.startswith("**") and s.endswith("**") and len(s) > 8:
            headline = strip_bold(s)
            break
    m = re.search(r"\*\*一句话总结[：:]\s*(.+?)\*\*", txt)
    act_m = re.search(r"\*\*操作建议[^\n]*\n(.+?)(?=\n\*\*|\Z)", txt, re.S)
    return {"headline": headline,
            "core_logic": bullets_after(txt, r"核心逻辑"),
            "key_risks": bullets_after(txt, r"主要风险"),
            "action_md": act_m.group(1).strip() if act_m else None,
            "one_liner": m.group(1) if m else None}


def _ex_card_raw(sections):
    s = sections.get("l3_card")
    return {"text": s.text} if s and s.text.strip() else None


def _ex_appendix(sections):
    items = [{"section_id": sid, "title": s.title, "raw_md": s.text}
             for sid, s in sections.items() if sid.startswith("appendix:")]
    return {"items": items} if items else None


# ---------- 手册模块提取器（报告缺节 → 块 una，前端删面板） ----------

def _first_table(txt: str) -> dict | None:
    return (md_tables(txt) or [None])[0]


def _ex_chips(sections):
    """l0_chips：筹码与内部人成本 kv 表 + 筹码判定。"""
    txt = _sec(sections, "l0_chips")
    if not txt:
        return None
    t = _first_table(txt)
    rows = []
    if t:
        for row in t["rows"]:
            k = list(row)
            rows.append({"field": row[k[0]], "value": row.get(k[1], ""),
                         "vs_price": row.get(k[2], "") if len(k) > 2 else "",
                         "status": row.get(k[-1], "")})
    m = re.search(r"筹码判定[：:]\*{0,2}\s*([^】\n]+)", txt)
    verdict = strip_bold(m.group(1)) if m else None
    if not rows and not verdict:
        return None
    return {"rows": rows, "verdict": verdict}


def _summary_row_total_grade(t: dict | None, grade_pats: str) -> tuple[float | None, str | None]:
    r"""评分表「综合」行：总分为 joined 首个 \d+分（避开阈值说明数字），
    等级只认末列（依据/评级）——防「14分（≥18极强/14-17较强…）」阈值文本抢先命中。"""
    if not t:
        return None, None
    row = cell_of(t, "综合")
    if not row:
        return None, None
    k = list(row)
    joined = " ".join(strip_bold(row[x]) for x in k[1:])
    m = re.search(r"(\d+(?:\.\d+)?)\s*分", joined)
    total = float(m.group(1)) if m else None
    m = re.search(grade_pats, strip_bold(row[k[-1]]))
    return total, (m.group(1) if m else None)


def _ex_position(sections):
    """l2_position：行业地位判定 + 护城河四维评分。"""
    txt = _sec(sections, "l2_position")
    if not txt:
        return None
    subs = sub_h3(txt)
    idt = next((v for k, v in subs.items() if "行业地位" in k), "")
    rank = second = None
    for t in md_tables(idt):
        rrow = cell_of(t, r"排名")
        if rrow and rank is None:
            m = re.search(r"第([一二三四五六七八九十])", " ".join(rrow[x] for x in list(rrow)[1:]))
            if m:
                rank = f"行业第{m.group(1)}"
        orow = cell_of(t, r"老二折价")
        if orow and second is None:
            # 判定可能在任意非首列（实证：「判定」列写 **适用**，「依据」列只讲后果）
            v = " ".join(strip_bold(orow[x]) for x in list(orow)[1:])
            second = "不适用" if "不适用" in v else ("适用" if "适用" in v else None)
        if rank and second:
            break
    moat = next((v for k, v in subs.items() if "护城河" in k), "")
    moat_rows = []
    mt = _first_table(moat)
    if mt:
        for row in mt["rows"]:
            k = list(row)
            label = row[k[0]]
            if "综合" in label or "合计" in label:
                continue
            sc = clean_num(row.get(k[2], "")) if len(k) > 2 else None
            moat_rows.append({"dim": label, "score": sc,
                              "weight": row.get(k[1], "") if len(k) > 1 else "",
                              "basis": row.get(k[-1], "")})
    total, grade = _summary_row_total_grade(mt, r"(极强|较强|一般|薄弱)")
    if total is None:  # 行内形态回退：「综合得分：14分」
        m = re.search(r"综合得分[：:]*\*{0,2}\s*(\d+(?:\.\d+)?)\s*分?", strip_bold(moat or txt))
        total = float(m.group(1)) if m else None
    if not moat_rows and total is None and rank is None:
        return None
    return {"rank": rank, "second_discount": second,
            "moat_rows": moat_rows, "moat_total": total, "moat_grade": grade,
            "summary": next((strip_bold(ln) for ln in (idt or txt).splitlines()
                             if ln.strip().startswith(">")), None)}


def _ex_peer(sections):
    """l2_peer：竞品五维横向对比。"""
    txt = _sec(sections, "l2_peer")
    if not txt:
        return None
    t = _first_table(txt)
    rows = []
    if t:
        for row in t["rows"]:
            k = list(row)
            rows.append({"dim": row[k[0]], "target": row.get(k[1], ""),
                         "leader": row.get(k[2], "") if len(k) > 2 else "",
                         "avg": row.get(k[3], "") if len(k) > 3 else "",
                         "verdict": row.get(k[-1], "")})
    if not rows:
        return None
    concl = next((strip_bold(ln) for ln in txt.splitlines()
                  if re.match(r"^(\*\*)?(对比结论|结论)[：:]", ln.strip())), None)
    return {"rows": rows, "conclusion": concl}


def _ex_deviation(sections):
    """l2_deviation：偏离度分级 + 裁量协议层触发。"""
    txt = _sec(sections, "l2_deviation")
    if not txt:
        return None
    flat = strip_bold(txt)
    applicable = "不适用" not in flat[:120]
    t = _first_table(txt)
    rows = []
    if t:
        for row in t["rows"]:
            k = list(row)
            rows.append({"item": row[k[0]], "actual": row.get(k[1], ""),
                         "threshold": row.get(k[2], "") if len(k) > 2 else "",
                         "dev": row.get("偏离度", "") if "偏离度" in row else (row.get(k[3], "") if len(k) > 3 else ""),
                         "grade": row.get(k[-1], "")})
    concl = next((strip_bold(ln) for ln in txt.splitlines() if "协议层" in ln), None)
    return {"applicable": applicable, "rows": rows, "conclusion": concl}


def _ex_pq(sections):
    """l2_trap 内 H3 切片：P-33 利润质量多期表（缺 H3 → None → una）。"""
    subs = sub_h3(_sec(sections, "l2_trap"))
    pq = next((v for k, v in subs.items() if "利润质量" in k or "P-33" in k.upper()), "")
    if not pq:
        return None
    t = _first_table(pq)
    rows = []
    if t:
        for row in t["rows"]:
            k = list(row)
            rows.append({"period": row[k[0]], "parent": row.get(k[1], ""),
                         "deducted": row.get(k[2], "") if len(k) > 2 else "",
                         "gap": row.get(k[3], "") if len(k) > 3 else "",
                         "verdict": row.get(k[-1], "")})
    if not rows:
        return None
    trend = next((strip_bold(ln) for ln in pq.splitlines()
                  if re.search(r"趋势[：:]", ln) and ln.strip()), None)
    return {"rows": rows, "trend": trend}


def _ex_health(sections):
    """l2_health：财务健康度四维评分 + 风险分类三行。"""
    txt = _sec(sections, "l2_health")
    if not txt:
        return None
    subs = sub_h3(txt)
    hs = next((v for k, v in subs.items() if "财务健康" in k), "")
    rows = []
    t = _first_table(hs)
    if t:
        for row in t["rows"]:
            k = list(row)
            label = row[k[0]]
            if "综合" in label or "评级" in label:
                continue
            rows.append({"dim": label,
                         "score": clean_num(row.get("得分", "")) if "得分" in row
                         else clean_num(row.get(k[2], "") if len(k) > 2 else ""),
                         "basis": row.get(k[-1], "")})
    total, grade = _summary_row_total_grade(t, r"(优秀|良好|一般|警惕)")
    if total is None:  # 行内形态回退：「综合得分：6分」
        m = re.search(r"综合得分[：:]*\*{0,2}\s*(\d+(?:\.\d+)?)", strip_bold(hs or txt))
        total = float(m.group(1)) if m else None
    if grade is None:
        m = re.search(r"评级[：:]*\*{0,2}\s*(优秀|良好|一般|警惕)", strip_bold(hs or txt))
        grade = m.group(1) if m else None
    rk = next((v for k, v in subs.items() if "风险分类" in k), "")
    risk_rows = []
    rt = _first_table(rk)
    if rt:
        for row in rt["rows"]:
            k = list(row)
            risk_rows.append({"cls": row[k[0]], "hedge": row.get(k[1], ""),
                              "fact": row.get(k[2], "") if len(k) > 2 else "",
                              "impact": row.get(k[-1], "")})
    if not rows and not risk_rows and total is None:
        return None
    return {"rows": rows, "total": total, "grade": grade, "risk_rows": risk_rows}


def _ex_veto(sections):
    """l2_veto：硬 Exit 七条 + 综合权重六条 + 总结论（触发即凌驾状态机）。"""
    txt = _sec(sections, "l2_veto")
    if not txt:
        return None
    subs = sub_h3(txt)

    def trows(v):
        t = _first_table(v)
        out = []
        if t:
            for row in t["rows"]:
                k = list(row)
                res = row.get(k[-1], "")
                cells = [strip_bold(row[x]) for x in k[:-1]]
                # 首列常为 ①② 序号，条件正文在次列——取首个长单元格
                item = next((c for c in cells if len(c) > 4), cells[-1] if cells else "")
                na = "不适用" in res
                trig = (not na) and ("❌" in res or
                                     ("触发" in res and "未触发" not in res and "不触发" not in res))
                out.append({"item": item, "result": res, "triggered": None if na else trig})
        return out

    hard_rows = trows(next((v for k, v in subs.items() if "硬Exit" in k or "硬退出" in k or "逐项" in k), ""))
    comp_rows = trows(next((v for k, v in subs.items() if "综合" in k or "权重" in k), ""))
    concl = next((strip_bold(ln) for ln in txt.splitlines()
                  if "一票否决" in ln and ("**" in ln or ln.strip().startswith(">"))), None)
    if not concl:
        concl = next((strip_bold(ln) for ln in txt.splitlines()
                      if re.search(r"结论[：:].*一票否决|一票否决[：:]", ln)), None)
    triggered = None
    if concl:
        # 实证形态「结论：一票否决Exit：未触发（强权重0项+中权重0项）」——锚点后找
        idx = concl.find("一票否决")
        m = re.search(r"(未触发|触发)", concl[idx:]) if idx >= 0 else None
        if m:
            triggered = m.group(1) == "触发"
    if not hard_rows and not comp_rows and not concl:
        return None
    return {"hard_rows": hard_rows, "comp_rows": comp_rows,
            "conclusion": concl, "triggered": triggered}


def _ex_guide(sections):
    """l2_state 内 H3 切片：持仓管理指引（新仓止损/老仓观察区/清仓条件）。"""
    g = next((v for k, v in sub_h3(_sec(sections, "l2_state")).items()
              if "持仓管理" in k), "")
    if not g:
        return None
    flat = strip_bold(g)

    def zone(pat):
        m = re.search(pat + r"[^0-9\n]*(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)", flat)
        d = re.search(pat + r"[^%]*?(\d+(?:\.\d+)?)\s*%", flat)
        return ({"lo": float(m.group(1)), "hi": float(m.group(2)),
                 "dy": float(d.group(1)) if d else None} if m else None)

    m = re.search(r"止损线[^0-9\n]*(\d+(?:\.\d+)?)", flat)
    clear = bullets_after(g, r"基本面清仓条件|清仓条件")
    z1, z2 = zone(r"第一观察区"), zone(r"第二观察区")
    if m is None and z1 is None and z2 is None and not clear:
        return None
    return {"stop_loss_new": float(m.group(1)) if m else None,
            "zone1": z1, "zone2": z2, "clear_list": clear}


_EXTRACTORS = {
    "meta": (_ex_meta, ["meta"]), "quote": (_ex_quote, ["l0_quote"]),
    "metrics": (_ex_metrics, ["l0_quote"]), "stamp": (_ex_stamp, ["l2_state"]),
    "signals": (_ex_signals, ["l2_five"]), "debate": (_ex_debate, ["l2_debate"]),
    "gates": (_ex_gates, ["l2_hardgate", "l0_reverse"]),
    "traps": (_ex_traps, ["l2_trap"]), "roe": (_ex_roe, ["l0_financial"]),
    "capital": (_ex_capital, ["l2_capital"]), "factor": (_ex_factor, ["l2_factor"]),
    "behavior": (_ex_behavior, ["l2_behavior"]), "stress": (_ex_stress, ["l2_stress"]),
    "quant": (_ex_quant, ["l2_quant"]), "ruler": (_ex_ruler, ["l2_stress"]),
    "anchors": (_ex_anchors, ["l2_stress"]), "exits": (_ex_exits, ["l3_summary"]),
    "risks": (_ex_risks, ["l3_summary"]), "trace": (_ex_trace, ["l3_card"]),
    "summary": (_ex_summary, ["l3_summary"]), "card_raw": (_ex_card_raw, ["l3_card"]),
    "appendix": (_ex_appendix, []),
    # ── 新版手册区块（26 节模板落点）──
    "position": (_ex_position, ["l2_position"]), "peer": (_ex_peer, ["l2_peer"]),
    "deviation": (_ex_deviation, ["l2_deviation"]), "pq": (_ex_pq, ["l2_trap"]),
    "health": (_ex_health, ["l2_health"]), "veto": (_ex_veto, ["l2_veto"]),
    "chips": (_ex_chips, ["l0_chips"]), "guide": (_ex_guide, ["l2_state"]),
}


def _ex_pipeline(sections):
    """5 闸门流水线：全部由其他块派生（不独立解析）。"""
    g = _ex_gates(sections)
    r = _ex_traps(sections)
    sg = _ex_signals(sections)
    st = _ex_stamp(sections)
    gates = []
    if g and g.get("hard") and g["hard"]["conclusion"]:
        concl = g["hard"]["conclusion"]
        passed = ("通过" in concl and "不通过" not in concl and "等效" not in concl) or "✅" in concl
        gates.append({"no": 1, "name": "硬门槛", "verdict": concl.replace("结论（严格口径）：", "严格口径：")[:40],
                      "cls": "pass" if passed else "warn"})
    if g and g.get("reverse"):
        rev = g["reverse"]
        d = rev.get("downgrades")
        ok = "不通过" not in (rev["conclusion"] or "") and (d in (0, None))
        concl = rev["conclusion"] or ""
        if "反向清单筛查" in concl:
            concl = concl[concl.index("反向清单筛查"):]
        gates.append({"no": 2, "name": "反向清单",
                      "verdict": f"{concl[:18]}｜{int(d) if d is not None else 0} 项降级",
                      "cls": "pass" if ok else "warn"})
    if r:
        n = r["triggered_count"]
        gates.append({"no": 3, "name": "价值陷阱",
                      "verdict": f"{n} 项触发｜{(r['conclusion_text'] or '')[:18]}",
                      "cls": "pass" if n == 0 else "warn"})
    if sg:
        c = {"pos": 0, "neu": 0, "neg": 0}
        for it in sg["items"]:
            c[it["polarity"]] += 1
        pos_n = sg.get("pos_count", c["pos"])
        gates.append({"no": 4, "name": "五维信号",
                      "verdict": f"{pos_n} 正 · {c['neu']} 平 · {c['neg']} 负",
                      "cls": "pass" if c["pos"] > c["neg"] else "warn"})
    v = _ex_veto(sections)
    if v is not None:
        t = v.get("triggered")
        gates.append({"no": 5, "name": "一票否决",
                      "verdict": (v.get("conclusion") or "")[:26] or ("触发" if t else "未触发"),
                      "cls": "final" if t else "pass"})
    if st:
        gates.append({"no": 6 if v is not None else 5, "name": "状态机",
                      "verdict": f"{st['zh']}｜≤{st['position_cap_pct'] or '?'}%｜{st['confidence'] or '?'}",
                      "cls": "final"})
    return {"gates": gates} if gates else None


_EXTRACTORS["pipeline"] = (_ex_pipeline, [])

_PREREQS = {"pipeline": ("l2_hardgate", "l2_five", "l2_state")}  # 未齐不推终态（会随节到齐自然点亮）


def build_blocks(sections: dict[str, Section]) -> dict[str, dict]:
    """由已闭合节集合构造全部可用区块（流式与一次性共用）。异常→raw，永不抛出。"""
    out: dict[str, dict] = {}
    for key, (fn, fallback_ids) in _EXTRACTORS.items():
        if key in _PREREQS and any(sid not in sections for sid in _PREREQS[key]):
            continue
        try:
            payload = fn(sections)
            if payload is not None:
                payload.setdefault("status", "ok")
                _json.dumps(payload)  # 序列化保险：不可序列化 bug 类同样走降级
        except Exception as e:  # noqa: BLE001  降级是设计特性：该块原文透出
            raw = "\n\n".join(sections[sid].text for sid in fallback_ids if sid in sections)
            payload = {"status": "raw", "raw_md": raw, "error": repr(e)} if raw else None
        if payload is None:
            continue
        payload.setdefault("status", "ok")
        out[key] = payload
    return out


def build_dashboard(md: str, *, issues: list | None = None,
                    report_path: str = "") -> dict:
    """全量入口：完整 MD → DashboardData 信封（含 una 显式占位）。"""
    md = _strip_fences(md)
    sections = split_sections(md)
    blocks = build_blocks(sections)
    fallbacks = [k for k, b in blocks.items() if b.get("status") in ("raw", "partial")]
    for key in _EXTRACTORS:
        blocks.setdefault(key, {"status": "una"})
    return {"schema": SCHEMA_VERSION,
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "report_path": report_path,
            "validate_issues": issues or [],
            "parse_fallbacks": fallbacks,
            "section_count": sum(1 for s in sections if not s.startswith("appendix:")),
            "blocks": blocks}
