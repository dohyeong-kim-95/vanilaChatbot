"""Fixed monthly spending report (Loop 14). The model only supplies numbers (computed by code in the sandbox);
the layout is ours, so every month looks the same and each section can be captured as an image for a document.

The page has no scripts and no external resources: the chart is SVG drawn here, all text is escaped.
Each section is self-contained (own heading, fixed width) so a capture of it reads on its own.
"""
import html
import json

from .views import FONT, PAD, PRESETS, SIZES, TOKENS

SECTIONS = {"summary": "요약", "categories": "카테고리별 지출", "top": "큰 거래", "notes": "메모"}
WIDTH = 720
MAX_ROWS = 20

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "e.g. 2026년 9월 카드A 지출"},
        "period": {"type": "string", "description": "e.g. 2026-09"},
        "total": {"type": "number"},
        "count": {"type": "integer", "description": "number of transactions"},
        "prev_total": {"type": "number", "description": "previous period total, if known"},
        "categories": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "amount": {"type": "number"},
            "budget": {"type": "number", "description": "optional monthly budget"}}, "required": ["name", "amount"]}},
        "top": {"type": "array", "description": "largest transactions, biggest first", "items": {
            "type": "object", "properties": {"date": {"type": "string"}, "merchant": {"type": "string"},
                                             "amount": {"type": "number"}, "category": {"type": "string"}},
            "required": ["date", "merchant", "amount"]}},
        "notes": {"type": "array", "items": {"type": "string"}, "description": "short findings, optional"},
        "headlines": {"type": "object", "description": "optional slide titles that state the finding, per section",
                      "properties": {"summary": {"type": "string"}, "categories": {"type": "string"},
                                     "top": {"type": "string"}}},
    },
    "required": ["title", "period", "total", "categories"],
}

CSS = f"""
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 16px; background: #f5f5f4; color: #1c1917;
       font: 15px/1.5 {FONT}; }}
section {{ width: 100%; max-width: {WIDTH}px; background: #fff; border: 1px solid #e2e0dc; border-radius: 8px; padding: 20px 24px;
          margin: 0 0 16px; }}
svg {{ display: block; }}
h2 {{ font-size: 16px; font-weight: 700; margin: 0 0 14px; }}
h2 .period {{ color: #625c57; font-weight: 400; margin-left: 6px; }}
.figures {{ display: flex; gap: 40px; }}
.figure .k {{ color: #625c57; font-size: 13px; }}
.figure .v {{ font-size: 26px; font-weight: 700; font-variant-numeric: tabular-nums; }}
.figure .v.small {{ font-size: 18px; padding-top: 8px; }}
.up {{ color: #b45309; }} .down {{ color: #0e7490; }}
table {{ width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }}
th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #ecebe8; }}
th {{ color: #625c57; font-weight: 500; font-size: 13px; }}
td.num, th.num {{ text-align: right; }}
ul {{ margin: 0; padding-left: 20px; }}
.legend {{ color: #625c57; font-size: 12px; margin-top: 8px; }}
"""


def _num(x, field):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError(f"{field} must be a number, got {x!r}")
    return x


def _str(x, field, limit=200):
    if not isinstance(x, str):
        raise ValueError(f"{field} must be a string")
    return x[:limit]


def _obj(x, field):
    if not isinstance(x, dict):  # Loop 38: dropped quietly, a report of no categories saved and never opened
        raise ValueError(f"{field}: each item must be an object, got {json.dumps(x, ensure_ascii=False)[:60]}")
    return True


def validate(d):
    """Strict shape check; returns (clean data, warnings). Warnings go back to the model so it can fix numbers."""
    if not isinstance(d, dict):
        raise ValueError("report data must be an object")
    unknown = set(d) - set(SCHEMA["properties"])
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")
    out = {"title": _str(d.get("title"), "title"), "period": _str(d.get("period"), "period", 40),
           "total": _num(d.get("total"), "total")}
    if "count" in d:
        out["count"] = int(_num(d["count"], "count"))
    if "prev_total" in d and d["prev_total"] is not None:
        out["prev_total"] = _num(d["prev_total"], "prev_total")
    cats = d.get("categories")
    if not isinstance(cats, list) or not cats:
        raise ValueError("categories must be a non-empty list")
    if len(cats) > MAX_ROWS:  # cutting them would make a right total look wrong
        raise ValueError(f"at most {MAX_ROWS} categories: put the small ones together as one '기타'")
    for k in ("top", "notes"):
        if d.get(k) is not None and not isinstance(d[k], list):
            raise ValueError(f"{k} must be a list")
    out["categories"] = [{"name": _str(c.get("name"), "categories.name", 40),
                          "amount": _num(c.get("amount"), "categories.amount"),
                          **({"budget": _num(c["budget"], "categories.budget")} if c.get("budget") is not None else {})}
                         for c in cats if _obj(c, "categories")]
    out["top"] = [{"date": _str(t.get("date"), "top.date", 20), "merchant": _str(t.get("merchant"), "top.merchant", 60),
                   "amount": _num(t.get("amount"), "top.amount"), "category": _str(t.get("category", ""), "top.category", 40)}
                  for t in (d.get("top") or [])[:MAX_ROWS] if _obj(t, "top")]
    out["notes"] = [_str(n, "notes", 300) for n in (d.get("notes") or [])[:10]]
    heads = d.get("headlines") or {}
    if not isinstance(heads, dict) or set(heads) - {"summary", "categories", "top"}:
        raise ValueError("headlines must be an object with summary / categories / top strings")
    out["headlines"] = {k: _str(v, f"headlines.{k}", 120) for k, v in heads.items()}
    warnings = []
    s = sum(c["amount"] for c in out["categories"])
    if abs(s - out["total"]) > 0.5:
        warnings.append(f"categories sum to {fmt(s)} but total is {fmt(out['total'])}")
    amounts = [t["amount"] for t in out["top"]]
    if amounts != sorted(amounts, reverse=True):
        warnings.append("top is not sorted by amount, biggest first")
    return out, warnings


def fmt(x):
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,.2f}"


def sections(d):
    return ["summary", "categories"] + ["top"] * bool(d.get("top")) + ["notes"] * bool(d.get("notes"))


def _bars(cats):
    """Horizontal bars, biggest first; a dark tick marks the budget, over-budget bars are amber."""
    cats = sorted(cats, key=lambda c: -c["amount"])
    label_w, value_w, row, bar_h = 96, 110, 34, 20
    plot_w = WIDTH - 48 - label_w - value_w
    top = max(max(c["amount"] for c in cats), max((c.get("budget", 0) for c in cats), default=0)) or 1
    e = html.escape
    parts = []
    for i, c in enumerate(cats):
        y = i * row
        w = max(1, c["amount"] / top * plot_w)
        over = "budget" in c and c["amount"] > c["budget"]
        parts.append(f'<text x="{label_w - 10}" y="{y + 15}" text-anchor="end" font-size="13" fill="#1c1917">'
                     f'{e(c["name"])}</text>'
                     f'<rect x="{label_w}" y="{y + 1}" width="{w:.1f}" height="{bar_h}" rx="2" '
                     f'fill="{"#d97706" if over else "#0e7490"}"/>'
                     f'<text x="{label_w + plot_w + value_w}" y="{y + 15}" text-anchor="end" font-size="13" '
                     f'fill="#1c1917">{fmt(c["amount"])}</text>')
        if "budget" in c:
            bx = label_w + c["budget"] / top * plot_w
            parts.append(f'<rect x="{bx - 1:.1f}" y="{y - 2}" width="2" height="{bar_h + 6}" fill="#1c1917"/>')
    h = len(cats) * row
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" '
            f'viewBox="0 0 {WIDTH - 48} {h}">{"".join(parts)}</svg>')


def render(d):
    d, _ = validate(d)
    e = html.escape
    head = lambda key: f'<h2>{e(d["title"])} · {SECTIONS[key]}<span class="period">{e(d["period"])}</span></h2>'  # noqa: E731
    figs = [("총 지출", fmt(d["total"]) + "원", "")]
    if "count" in d:
        figs.append(("거래 수", f'{d["count"]:,}건', "small"))
    if "prev_total" in d and d["prev_total"]:
        diff = d["total"] - d["prev_total"]
        pct = diff / d["prev_total"] * 100
        figs.append(("전월 대비", f'<span class="{"up" if diff > 0 else "down"}">{"+" if diff > 0 else ""}{fmt(diff)}원 '
                     f'({pct:+.1f}%)</span>', "small"))
    out = {"summary": head("summary") + '<div class="figures">' + "".join(
        f'<div class="figure"><div class="k">{k}</div><div class="v {cls}">{v if k == "전월 대비" else e(v)}</div></div>'
        for k, v, cls in figs) + "</div>"}
    legend = '<div class="legend">검은 눈금 = 예산, 주황 막대 = 예산 초과</div>' if any("budget" in c for c in d["categories"]) else ""
    out["categories"] = head("categories") + _bars(d["categories"]) + legend
    if d["top"]:
        out["top"] = head("top") + '<table><tr><th>날짜</th><th>가맹점</th><th>카테고리</th><th class="num">금액</th></tr>' + \
            "".join(f'<tr><td>{e(t["date"])}</td><td>{e(t["merchant"])}</td><td>{e(t["category"])}</td>'
                    f'<td class="num">{fmt(t["amount"])}</td></tr>' for t in d["top"]) + "</table>"
    if d["notes"]:
        out["notes"] = head("notes") + "<ul>" + "".join(f"<li>{e(n)}</li>" for n in d["notes"]) + "</ul>"
    body = "".join(f'<section id="sec-{k}" data-ref="{k}">{out[k]}</section>' for k in sections(d))
    return f'<!doctype html><html lang="ko"><meta charset="utf-8"><title>{e(d["title"])}</title><style>{CSS}</style>' \
           f'<body>{body}</body></html>'


# ---- slides (docs/DESIGN.md 5.4): one section on a fixed slide canvas, captured server-side --------------------
SLIDE_SECTIONS = {"summary": "S", "categories": "M", "top": "M"}  # default preset; notes are copied as text
MAX_BARS = MAX_TABLE = 7
T = TOKENS


def man(x):
    """Slide money: 만 원 with one decimal (1,605,300 -> 160.5만); under 10,000 as plain won."""
    return f"{x / 10000:,.1f}만" if abs(x) >= 10000 else f"{x:,.0f}"


def _josa(word, with_final="이", without="가"):
    c = word[-1:] or " "
    return with_final if "가" <= c <= "힣" and (ord(c) - 0xAC00) % 28 else without


def _month(period):
    return f"{int(period[5:7])}월" if len(period) >= 7 and period[5:7].isdigit() else period


def headline(d, key):
    if d["headlines"].get(key):
        return d["headlines"][key]
    if key == "summary":
        if d.get("prev_total"):
            pct = (d["total"] - d["prev_total"]) / d["prev_total"] * 100
            return f"{_month(d['period'])} 지출 {abs(pct):.0f}% {'증가' if pct > 0 else '감소'}" if round(pct) else \
                f"{_month(d['period'])} 지출 전월과 비슷"
        return f"{_month(d['period'])} 지출 {man(d['total'])} 원"
    if key == "categories":
        over = [c for c in d["categories"] if "budget" in c and c["amount"] > c["budget"]]
        if over:
            names = "·".join(c["name"] for c in sorted(over, key=lambda c: -c["amount"]))
            excess = sum(c["amount"] - c["budget"] for c in over)
            return f"{names}{_josa(names)} 예산 초과, 합계 {man(excess)} 원 넘음"
        top = max(d["categories"], key=lambda c: c["amount"])
        return f"모든 카테고리 예산 이내, 최대는 {top['name']} {man(top['amount'])} 원"
    t = d["top"][0]
    return f"가장 큰 거래는 {t['merchant']} {man(t['amount'])} 원"


def _tw(s, size=SIZES["label"]):
    """Rough rendered width of s (server-side, no fonts): Hangul/CJK ~1em, other glyphs ~0.56em."""
    return sum(size * (1.0 if ord(ch) >= 0x1100 else 0.3 if ch == " " else 0.56) for ch in s)


def _fit(s, width):
    while s and _tw(s) > width:
        s = s[:-2] + "…" if len(s) > 1 else ""
    return s


def _slide_bars(cats, width, height):
    """Horizontal bars sized to the space left (height) so no row is clipped; label column sized to the names."""
    cats = sorted(cats, key=lambda c: -c["amount"])
    if len(cats) > MAX_BARS:  # fold the tail into one "기타" row
        rest = cats[MAX_BARS - 1:]
        cats = cats[:MAX_BARS - 1] + [{"name": f"기타 {len(rest)}개", "amount": sum(c["amount"] for c in rest)}]
    e = html.escape
    row = min(56, (height - 16) / len(cats))
    bar = round(row * 0.6)
    top = max(max(c["amount"] for c in cats), max((c.get("budget", 0) for c in cats), default=0)) or 1

    def label(c, short):  # short: without the "+… 초과" part, for when nothing else fits
        over = "budget" in c and c["amount"] > c["budget"]
        return f'{man(c["amount"])} +{man(c["amount"] - c["budget"])} 초과' if over and not short else man(c["amount"])

    def place(c, scale, short):
        """(bar width, budget tick, label inside?, label end), relative to the plot's left edge. The one rule used
        both to pick the layout and to draw, so a label that fits here is never pushed off the slide."""
        w = max(2, c["amount"] / top * scale)
        tick = c["budget"] / top * scale if "budget" in c else None
        tw = _tw(label(c, short))
        inside = w >= tw + 20 and not (tick is not None and tick < 10 + tw + 4)
        return w, tick, inside, 10 + tw if inside else max(w, tick or 0) + 10 + tw

    # widest bars that fit; then a narrower name column (names get …); then values without "+… 초과"
    names = max(_tw(c["name"]) for c in cats)
    layouts = [(round(min(names, width * f)) + 12, short) for f, short in ((0.35, False), (0.2, False), (0.2, True))]
    for lab, short in layouts:
        plot = width - lab - 8
        scale = next((plot * k / 100 for k in range(90, 4, -2)
                      if all(place(c, plot * k / 100, short)[3] <= plot for c in cats)), None)
        if scale:
            break
    else:
        scale = plot * 0.04  # still too wide: labels are clamped to the right edge below
    parts = []
    for i, c in enumerate(cats):
        y = round(i * row + 8)
        w, tick, inside, end = place(c, scale, short)
        text = label(c, short)
        over = "budget" in c and c["amount"] > c["budget"]
        parts.append(f'<text x="{lab - 12}" y="{y + bar / 2 + 6}" text-anchor="end" font-size="{SIZES["label"]}" '
                     f'fill="{T["ink"]}">{e(_fit(c["name"], lab - 12))}</text><rect x="{lab}" y="{y}" width="{w:.1f}" height="{bar}" '
                     f'fill="{T["over"] if over else T["neutral"]}"/>')
        if tick is not None:
            parts.append(f'<rect x="{lab + tick - 1.25:.1f}" y="{y - 5}" width="2.5" height="{bar + 10}" fill="{T["ink"]}"/>')
        lx = lab + 10 if inside else lab + max(w, tick or 0) + 10 - max(0, end - plot)
        parts.append(f'<text x="{lx:.1f}" y="{y + bar / 2 + 6}" font-size="{SIZES["label"]}" '
                     f'font-weight="{700 if over else 400}" fill="{"#FFFFFF" if inside and over else T["ink"]}">{e(text)}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{len(cats) * row + 16:.0f}">'
            f'{"".join(parts)}</svg>')


def slide(d, key, preset=None, show_title=True):
    """HTML of one section on a fixed slide canvas (no scripts, all text escaped)."""
    d, _ = validate(d)
    if key not in SLIDE_SECTIONS or key not in sections(d):
        raise KeyError(key)
    preset = preset if preset in PRESETS else SLIDE_SECTIONS[key]
    (W, H), (pt, pr, pb, pl), e = PRESETS[preset], PAD[preset], html.escape
    inner = W - pl - pr
    sub = {"summary": d["title"], "categories": f"{d['title']} · 카테고리별 지출 (원)",
           "top": f"{d['title']} · 금액 큰 순"}[key]
    has_budget = any("budget" in c for c in d["categories"])
    source = ("| = 예산 · " if key == "categories" and has_budget else "") + f"출처: {d['title']}"
    if key == "summary":
        figs = f'<div class="k">총 지출</div><div class="kpi">{e(man(d["total"]))} 원</div>'
        if d.get("prev_total"):
            diff = d["total"] - d["prev_total"]
            up = diff > 0
            figs += (f'<div class="delta" style="color:{T["overText"] if up else T["ink2"]}">{"▲" if up else "▼"} '
                     f'{e(man(abs(diff)))} 원 ({diff / d["prev_total"] * 100:+.1f}%) 전월 대비</div>')
        if "count" in d:
            figs += f'<hr><div class="k">거래 수</div><div class="kpi2">{d["count"]:,}건</div>'
        body = figs
    elif key == "categories":
        # space below the title block (2 title lines + subtitle), the 24px gap and the source line
        title_h = SIZES["title"] * 1.3 * 2 + SIZES["sub"] * 1.4 + 4 if show_title else 0
        body = _slide_bars(d["categories"], inner, H - pt - pb - title_h - 24 - SIZES["source"] * 1.4 - 2)
    else:
        rows = d["top"][:MAX_TABLE]
        wide = preset == "L"
        head = "<tr><th>날짜</th><th>가맹점</th>" + ("<th>카테고리</th>" if wide else "") + '<th class="num">금액 (원)</th></tr>'
        body = "<table>" + head + "".join(
            f'<tr><td>{e(t["date"][5:] if len(t["date"]) >= 10 else t["date"])}</td><td>{e(t["merchant"])}</td>'
            + (f'<td>{e(t["category"])}</td>' if wide else "") + f'<td class="num">{fmt(t["amount"])}</td></tr>'
            for t in rows) + "</table>"
        if len(d["top"]) > MAX_TABLE:
            body += f'<div class="more">외 {len(d["top"]) - MAX_TABLE}건</div>'
    title = (f'<h1>{e(headline(d, key))}</h1><div class="sub">{e(sub)}</div>') if show_title else ""
    css = f"""
* {{ box-sizing: border-box; margin: 0; }}
html, body {{ width: {W}px; height: {H}px; background: #FFFFFF; overflow: hidden; }}
body {{ font: 400 {SIZES["label"]}px/1.4 {FONT}; color: {T["ink"]}; }}
#slide {{ width: {W}px; height: {H}px; padding: {pt}px {pr}px {pb}px {pl}px; display: flex; flex-direction: column; }}
h1 {{ font-size: {SIZES["title"]}px; font-weight: 700; line-height: 1.3; max-height: 2.6em; overflow: hidden;
      word-break: keep-all; }}
.sub {{ font-size: {SIZES["sub"]}px; color: {T["ink2"]}; margin-top: 4px; white-space: nowrap; overflow: hidden;
        text-overflow: ellipsis; }}
.body {{ flex: 1; margin-top: 24px; overflow: hidden; }}
.src {{ font-size: {SIZES["source"]}px; color: {T["muted"]}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.k {{ color: {T["ink2"]}; }}
.kpi {{ font-size: 56px; font-weight: 700; line-height: 1.15; }}
.kpi2 {{ font-size: 36px; font-weight: 700; }}
.delta {{ font-weight: 700; margin-top: 4px; }}
hr {{ border: 0; border-top: 1px solid {T["grid"]}; margin: 20px 0 16px; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ font-size: {SIZES["tick"]}px; font-weight: 400; color: {T["ink2"]}; text-align: left; padding: 0 8px 8px 0;
      border-bottom: 1px solid {T["axis"]}; }}
td {{ height: 40px; padding: 0 8px 0 0; border-bottom: 1px solid {T["grid"]}; white-space: nowrap; overflow: hidden;
      text-overflow: ellipsis; max-width: 0; }}
td:first-child, th:first-child {{ width: 72px; }}
.num {{ text-align: right; width: 130px; }}
.more {{ color: {T["ink2"]}; margin-top: 8px; font-size: {SIZES["tick"]}px; }}
"""
    return (f'<!doctype html><html lang="ko"><meta charset="utf-8"><title>{e(d["title"])}</title><style>{css}</style>'
            f'<body><div id="slide">{title}<div class="body">{body}</div><div class="src">{e(source)}</div></div>'
            '</body></html>')
