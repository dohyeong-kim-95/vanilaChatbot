"""Fixed monthly spending report (Loop 14). The model only supplies numbers (computed by code in the sandbox);
the layout is ours, so every month looks the same and each section can be captured as an image for a document.

The page has no scripts and no external resources: the chart is SVG drawn here, all text is escaped.
Each section is self-contained (own heading, fixed width) so a capture of it reads on its own.
"""
import html

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
    },
    "required": ["title", "period", "total", "categories"],
}

CSS = f"""
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 16px; background: #f5f5f4; color: #1c1917;
       font: 15px/1.5 "Noto Sans KR", "Noto Sans CJK KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif; }}
section {{ width: 100%; max-width: {WIDTH}px; background: #fff; border: 1px solid #e2e0dc; border-radius: 8px; padding: 20px 24px;
          margin: 0 0 16px; }}
svg {{ display: block; }}
h2 {{ font-size: 16px; font-weight: 600; margin: 0 0 14px; }}
h2 .period {{ color: #625c57; font-weight: 400; margin-left: 6px; }}
.figures {{ display: flex; gap: 40px; }}
.figure .k {{ color: #625c57; font-size: 13px; }}
.figure .v {{ font-size: 26px; font-weight: 600; font-variant-numeric: tabular-nums; }}
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
    out["categories"] = [{"name": _str(c.get("name"), "categories.name", 40),
                          "amount": _num(c.get("amount"), "categories.amount"),
                          **({"budget": _num(c["budget"], "categories.budget")} if c.get("budget") is not None else {})}
                         for c in cats[:MAX_ROWS] if isinstance(c, dict)]
    out["top"] = [{"date": _str(t.get("date"), "top.date", 20), "merchant": _str(t.get("merchant"), "top.merchant", 60),
                   "amount": _num(t.get("amount"), "top.amount"), "category": _str(t.get("category", ""), "top.category", 40)}
                  for t in (d.get("top") or [])[:MAX_ROWS] if isinstance(t, dict)]
    out["notes"] = [_str(n, "notes", 300) for n in (d.get("notes") or [])[:10]]
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
    body = "".join(f'<section id="sec-{k}">{out[k]}</section>' for k in sections(d))
    return f'<!doctype html><html lang="ko"><meta charset="utf-8"><title>{e(d["title"])}</title><style>{CSS}</style>' \
           f'<body>{body}</body></html>'
