"""Dashboard over query results (Loop 33): the model names charts and the SQL behind them; the server runs the SQL
in the sandbox (agent/tools.py show_dashboard) and draws them here. Like the report, the page has no scripts: every
chart is SVG drawn here, every text escaped. The data each chart was drawn from is in the page as JSON
(#data, inert), so a chart can be checked against its numbers.

kinds: bar (x = category, y = number), line (x = number or time, y = number, optional series), scatter (x, y
numbers), wafer_map (x, y die coordinates, value = number or category), table.
"""
import html
import math

from .views import FONT, data_json

KINDS = {"bar", "line", "scatter", "wafer_map", "table"}
MAX_ROWS = {"bar": 60, "line": 5000, "scatter": 5000, "wafer_map": 20000, "table": 100}
MAX_CHARTS = 12
W, H = 680, 300
L, R, T, B = 64, 16, 12, 44  # plot margins
PALETTE = ["#2A78D6", "#D4572A", "#2E9E6B", "#8E5CC4", "#C79A00", "#1C9AAE", "#C2457A", "#6B7A8F", "#9A6B3F",
           "#4C8A2E", "#B42318", "#5C5C5C"]
INK, MUTED, GRID = "#1A1A1A", "#767676", "#E6E6E6"
e = html.escape

CSS = f"""
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 16px; background: #f5f5f4; color: {INK}; font: 14px/1.5 {FONT}; }}
section {{ max-width: {W + 48}px; background: #fff; border: 1px solid #e2e0dc; border-radius: 8px; padding: 18px 24px;
          margin: 0 0 16px; }}
h1 {{ font-size: 18px; margin: 0 0 12px; }}
h2 {{ font-size: 15px; margin: 0 0 4px; }}
.note {{ color: {MUTED}; font-size: 12px; margin: 0 0 10px; }}
svg {{ display: block; width: 100%; height: auto; }}
table {{ width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; font-size: 13px; }}
th, td {{ text-align: left; padding: 4px 8px; border-bottom: 1px solid #ecebe8; }}
th {{ color: {MUTED}; font-weight: 500; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: {MUTED}; margin-top: 6px; }}
.legend i {{ display: inline-block; width: 10px; height: 10px; margin-right: 4px; vertical-align: -1px; }}
"""


def num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def fmt(v):
    if not num(v):
        return "" if v is None else str(v)
    if float(v).is_integer() and abs(v) < 1e15:
        return f"{int(v):,}"
    return f"{v:.4g}"


def ticks(lo, hi, n=5):
    if hi <= lo:
        hi = lo + 1
    step = 10 ** math.floor(math.log10((hi - lo) / n))
    for m in (1, 2, 5, 10):
        if (hi - lo) / (step * m) <= n:
            step *= m
            break
    start = math.ceil(lo / step) * step
    return [start + i * step for i in range(int((hi - start) / step) + 1)]


def col(chart, name):
    cols = chart["columns"]
    if name not in cols:
        raise ValueError(f"chart {chart['title']!r}: no column {name!r} in the result (columns: {cols})")
    return cols.index(name)


def axes(xlo, xhi, ylo, yhi, xlabels=None):
    """Grid, ticks and the scale functions of a plot area."""
    pw, ph = W - L - R, H - T - B
    sx = lambda v: L + (v - xlo) / ((xhi - xlo) or 1) * pw  # noqa: E731
    sy = lambda v: T + ph - (v - ylo) / ((yhi - ylo) or 1) * ph  # noqa: E731
    out = []
    for t in ticks(ylo, yhi):
        y = sy(t)
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" stroke="{GRID}"/>'
                   f'<text x="{L - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="{MUTED}">{fmt(t)}</text>')
    if xlabels is None:
        for t in ticks(xlo, xhi):
            x = sx(t)
            out.append(f'<text x="{x:.1f}" y="{H - B + 16}" text-anchor="middle" font-size="11" fill="{MUTED}">{fmt(t)}</text>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{T + ph}" y2="{T + ph}" stroke="#BDBDBD"/>')
    return out, sx, sy


def svg(parts, h=H):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}">{"".join(parts)}</svg>'


def bar(c):
    xi, yi = col(c, c["x"]), col(c, c["y"])
    rows = [(str(r[xi]), r[yi]) for r in c["rows"] if num(r[yi])]
    if not rows:
        return "<p class='note'>(no numeric values)</p>"
    lo, hi = min(0, min(v for _, v in rows)), max(0, max(v for _, v in rows))
    out, _, sy = axes(0, len(rows), lo, hi, xlabels=True)
    bw = (W - L - R) / len(rows)
    for i, (k, v) in enumerate(rows):
        y0, y1 = sy(max(v, 0)), sy(min(v, 0))
        x = L + i * bw
        out.append(f'<rect x="{x + bw * 0.12:.1f}" y="{y0:.1f}" width="{bw * 0.76:.1f}" height="{max(1, y1 - y0):.1f}" '
                   f'fill="{PALETTE[0]}"><title>{e(k)}: {fmt(v)}</title></rect>')
        if len(rows) <= 30:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{H - B + 16}" text-anchor="middle" font-size="11" fill="{MUTED}">'
                       f'{e(k[:10])}</text>')
    return svg(out)


def _series(c):
    xi, yi = col(c, c["x"]), col(c, c["y"])
    si = col(c, c["series"]) if c.get("series") else None
    groups = {}
    for r in c["rows"]:
        x, y = r[xi], r[yi]
        if not num(y):
            continue
        groups.setdefault("" if si is None else str(r[si]), []).append((x, y))
    xs = [x for g in groups.values() for x, _ in g]
    if not xs:
        return None, None
    if all(num(x) for x in xs):
        conv = float
    else:  # times or labels: in the order they come (ORDER BY in the SQL)
        order = {v: i for i, v in enumerate(dict.fromkeys(str(x) for x in xs))}
        conv = lambda v: order[str(v)]  # noqa: E731
    return {k: [(conv(x), y) for x, y in g] for k, g in list(groups.items())[:len(PALETTE)]}, conv is float


def line(c, dots=False):
    groups, numeric = _series(c)
    if not groups:
        return "<p class='note'>(no numeric values)</p>"
    pts = [p for g in groups.values() for p in g]
    xlo, xhi = min(p[0] for p in pts), max(p[0] for p in pts)
    ylo, yhi = min(p[1] for p in pts), max(p[1] for p in pts)
    pad = (yhi - ylo) * 0.05 or 1
    out, sx, sy = axes(xlo, xhi, ylo - pad, yhi + pad, xlabels=None if numeric else True)
    if not numeric:  # first and last label of a time axis
        xi = col(c, c["x"])
        firsts = [str(r[xi]) for r in c["rows"]]
        out.append(f'<text x="{L}" y="{H - B + 16}" font-size="11" fill="{MUTED}">{e(firsts[0][:19])}</text>'
                   f'<text x="{W - R}" y="{H - B + 16}" text-anchor="end" font-size="11" fill="{MUTED}">{e(firsts[-1][:19])}</text>')
    for i, (k, g) in enumerate(groups.items()):
        color = PALETTE[i % len(PALETTE)]
        if dots:
            out += [f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="2" fill="{color}" fill-opacity="0.6"/>' for x, y in g]
        else:
            path = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in g)
            out.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="1.5"/>')
    legend = "" if len(groups) < 2 else '<div class="legend">' + "".join(
        f'<span><i style="background:{PALETTE[i % len(PALETTE)]}"></i>{e(k)}</span>' for i, k in enumerate(groups)) + "</div>"
    return svg(out) + legend


def wafer_map(c):
    xi, yi, vi = col(c, c["x"]), col(c, c["y"]), col(c, c["value"])
    cells = [(r[xi], r[yi], r[vi]) for r in c["rows"] if num(r[xi]) and num(r[yi])]
    if not cells:
        return "<p class='note'>(no dies)</p>"
    xs, ys = [p[0] for p in cells], [p[1] for p in cells]
    nx, ny = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    size = min(W / nx, 420 / ny)
    vals = [v for _, _, v in cells]
    categorical = not all(num(v) for v in vals if v is not None) or len(set(vals)) <= 12
    if categorical:
        keys = sorted({str(v) for v in vals}, key=lambda k: (len(k), k))
        color = {k: PALETTE[i % len(PALETTE)] for i, k in enumerate(keys)}
        paint = lambda v: color[str(v)]  # noqa: E731
    else:
        lo, hi = min(v for v in vals if num(v)), max(v for v in vals if num(v))

        def paint(v):
            if not num(v):
                return "#D0CEC8"
            t = (v - lo) / ((hi - lo) or 1)
            return f"rgb({int(242 - t * 200)},{int(242 - t * 120)},{int(250 - t * 40)})"
    out = [f'<rect x="{(x - min(xs)) * size:.1f}" y="{(max(ys) - y) * size:.1f}" width="{size:.2f}" height="{size:.2f}" '
           f'fill="{paint(v)}"><title>({fmt(x)}, {fmt(y)}): {e(fmt(v))}</title></rect>' for x, y, v in cells]
    legend = '<div class="legend">' + ("".join(f'<span><i style="background:{color[k]}"></i>{e(k)}</span>' for k in keys)
                                       if categorical else f"<span>{fmt(lo)} … {fmt(hi)} (밝음 → 진함)</span>") + "</div>"
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {nx * size:.1f} {ny * size:.1f}" ' \
           f'style="max-width:{min(W, nx * size):.0f}px">{"".join(out)}</svg>' + legend


def table(c):
    return "<table><tr>" + "".join(f"<th>{e(n)}</th>" for n in c["columns"]) + "</tr>" + "".join(
        "<tr>" + "".join(f"<td>{e(fmt(v))}</td>" for v in r) + "</tr>" for r in c["rows"]) + "</table>"


DRAW = {"bar": bar, "line": line, "scatter": lambda c: line(c, dots=True), "wafer_map": wafer_map, "table": table}


def validate_spec(title, charts):
    """What the model asks for (before running the SQL)."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title: a non-empty string")
    if not isinstance(charts, list) or not 1 <= len(charts) <= MAX_CHARTS:
        raise ValueError(f"charts: 1-{MAX_CHARTS}")
    out = []
    for i, ch in enumerate(charts):
        if not isinstance(ch, dict) or ch.get("kind") not in KINDS or not isinstance(ch.get("sql"), str):
            raise ValueError(f"charts[{i}]: needs kind ({sorted(KINDS)}) and sql")
        need = {"bar": ("x", "y"), "line": ("x", "y"), "scatter": ("x", "y"), "wafer_map": ("x", "y", "value"), "table": ()}
        miss = [k for k in need[ch["kind"]] if not isinstance(ch.get(k), str)]
        if miss:
            raise ValueError(f"charts[{i}] ({ch['kind']}): name the result columns {miss}")
        out.append({k: str(ch[k])[:300 if k != "sql" else 4000] for k in
                    ("title", "kind", "sql", "x", "y", "value", "series", "note") if ch.get(k) is not None})
        out[-1].setdefault("title", f"chart {i + 1}")
    return title.strip()[:120], out


def render(d):
    body = [f"<h1>{e(d['title'])}</h1>"]
    for i, c in enumerate(d["charts"]):
        try:
            drawn = DRAW[c["kind"]](c)
        except ValueError as ex:
            drawn = f"<p class='note'>{e(str(ex))}</p>"
        note = (c.get("note") or "") + (" (행이 많아 일부만 그렸어요)" if c.get("truncated") else "")
        body.append(f'<section id="sec-c{i}"><h2>{e(c["title"])}</h2>'
                    + (f'<p class="note">{e(note.strip())}</p>' if note.strip() else "") + drawn + "</section>")
    return (f'<!doctype html><html lang="ko"><meta charset="utf-8"><title>{e(d["title"])}</title><style>{CSS}</style>'
            f'<body>{"".join(body)}<script type="application/json" id="data">{data_json(d)}</script></body></html>')
