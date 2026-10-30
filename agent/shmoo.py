"""Interactive shmoo view (Loop 16): fixed page (our JS) + data built server-side from a test log.
The model only names the log file and its columns (show_shmoo); parsing and analysis are skills/shmoo/scripts/shmoo.py,
the same code the agent runs in the sandbox, so the view and the agent's numbers agree.

Page: condition filters, device = one device or all combined (pass rate), click a cell for the devices failing
there, findings and a device table that select what they point at. Capture (views.SLIDE_JS) redraws the current
view as a slide (docs/DESIGN.md 5.1/5.2): units on the axes, findings annotated, optional spec box and operating
point, a title that states the finding (editable). All log-derived text is set with textContent; the data is
inlined as JSON with `<` escaped.
"""
import html
import importlib.util

from .skills import DIR
from .views import FONT, MONO, SLIDE_JS, data_json, page_head

_spec = importlib.util.spec_from_file_location("shmoo_core", DIR / "shmoo" / "scripts" / "shmoo.py")
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)

MAX_CHARS = 900_000  # artifact content limit is 1 MB


def _range(v, name):
    if not (isinstance(v, list) and len(v) == 2 and all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in v)):
        raise ValueError(f"{name} must be [low, high] numbers")
    return sorted(v)


def build(path, x, y, result, device, filters=(), pass_values=None, title=None, spec=None, op=None, source=None):
    """spec: {"x": [lo, hi], "y": [lo, hi]} and op: {"x": v, "y": v}, in the log's own units (optional)."""
    if spec is not None:
        if not isinstance(spec, dict) or set(spec) != {"x", "y"}:
            raise ValueError('spec must be {"x": [low, high], "y": [low, high]}')
        spec = {"x": _range(spec["x"], "spec.x"), "y": _range(spec["y"], "spec.y")}
    if op is not None:
        if not isinstance(op, dict) or set(op) != {"x", "y"} or not all(
                isinstance(op[k], (int, float)) and not isinstance(op[k], bool) for k in "xy"):
            raise ValueError('op must be {"x": number, "y": number}')
    g = core.load(path, x, y, result, device, filters, pass_values)
    combos = g.combos()
    nx, ny = len(g.xs), len(g.ys)
    if len(combos) * len(g.devices) * nx * ny > MAX_CHARS:
        raise ValueError(f"too large for one view ({len(g.devices)} devices x {len(combos)} conditions x {nx}x{ny}); "
                         "filter the log first (fewer devices or conditions) and show that file")
    grids = [["".join(g.cells.get((d, c, i, j), ".") for j in range(ny) for i in range(nx)) for d in g.devices]
             for c in combos]
    data = {"title": title or f"Shmoo: {g.y_name} x {g.x_name}",
            "x": {"name": x, "values": g.xs}, "y": {"name": y, "values": g.ys},
            "device_cols": list(device), "devices": g.devices,
            "filters": [{"name": c, "values": g.filter_values[c]} for c in filters],
            "combos": [list(c) for c in combos], "grids": grids, "findings": core.analyze(g, members=True),
            "spec": spec, "op": op, "source": source or "", "result": result}
    warnings = []
    if g.unknown:
        warnings.append(f"result values neither pass nor fail were skipped: {dict(g.unknown)} (set pass_values)")
    if g.conflicts:
        warnings.append(f"{g.conflicts} retested cells changed result; the last row wins")
    missing = sum(s.count(".") for gr in grids for s in gr)
    if missing:
        warnings.append(f"{missing} grid cells have no result (not tested)")
    return data, warnings, core.summary(g)


CSS = """
:root { --bg: #f5f5f4; --card: #fff; --line: #e2e0dc; --text: #1c1917; --muted: #625c57; --pass: #0e7490; --fail: #c2410c;
        --none: #e7e5e4; --sel: #1c1917; }
* { box-sizing: border-box; }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--text);
       font: 14px/1.5 FONT; }
h1 { font-size: 17px; margin: 0 0 4px; font-weight: 700; }
.sub { color: var(--muted); font-size: 13px; margin-bottom: 12px; }
.controls { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: end; margin-bottom: 12px; }
.controls label { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 2px; }
select { font: inherit; padding: 4px 6px; border: 1px solid var(--line); border-radius: 6px; background: #fff; min-width: 120px; }
.layout { display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 12px; align-items: start; }
@media (max-width: 760px) { .layout { grid-template-columns: 1fr; } }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
.card h2 { font-size: 13px; margin: 0 0 8px; font-weight: 700; }
#plot svg { display: block; width: 100%; height: auto; }
#plot rect.c { cursor: pointer; }
#detail { font-size: 13px; min-height: 40px; }
#detail .muted, .muted { color: var(--muted); }
ul.list { list-style: none; margin: 0; padding: 0; font-size: 13px; }
ul.list li { padding: 4px 6px; border-radius: 4px; cursor: pointer; }
ul.list li:hover { background: #f0efed; }
ul.list li.on { background: #e6e4e1; }
.tag { display: inline-block; font-size: 11px; padding: 0 6px; border-radius: 9px; background: #fde7dc; color: #9a3412; margin-right: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
td, th { padding: 3px 6px; border-bottom: 1px solid #ecebe8; text-align: left; }
th { color: var(--muted); font-weight: 500; }
td.num, th.num { text-align: right; }
tr.row { cursor: pointer; } tr.row:hover { background: #f0efed; } tr.on { background: #e6e4e1; }
.side { display: flex; flex-direction: column; gap: 12px; }
.scroll { max-height: 260px; overflow: auto; }
.titlebox { display: flex; gap: 8px; align-items: center; font-size: 12px; color: var(--muted); margin: -4px 0 12px; }
.titlebox input { flex: 1; font: inherit; font-size: 13px; color: var(--text); padding: 4px 8px; border: 1px solid var(--line); border-radius: 6px; }
"""

JS = r"""
const D = JSON.parse(document.getElementById("data").textContent);
const NX = D.x.values.length, NY = D.y.values.length, ALL = -1;
const S = { combo: 0, device: ALL, cell: null };
const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, text) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  return n;
};
const svgEl = (tag, attrs = {}, text) => {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  return n;
};
const comboKey = (vals) => D.combos.findIndex((c) => c.every((v, i) => v === vals[i]));
const condText = (ci) => D.filters.map((f, i) => `${f.name}=${D.combos[ci][i]}`).join(", ");
const devName = (di) => di === ALL ? `전체 ${D.devices.length}개 합산` : `${D.device_cols.join("-")} ${D.devices[di]}`;
const at = (ci, di, i, j) => D.grids[ci][di][j * NX + i];
const cellStats = (ci, i, j) => {
  let p = 0, f = 0;
  const failing = [];
  D.grids[ci].forEach((g, di) => { const v = g[j * NX + i]; if (v === "P") p++; else if (v === "F") { f++; failing.push(di); } });
  return { p, f, failing };
};
const rate = (ci, di) => { const g = D.grids[ci][di]; let p = 0, n = 0; for (const c of g) { if (c !== ".") { n++; if (c === "P") p++; } } return n ? 100 * p / n : null; };

// DESIGN.md 4.1: binned diverging scale; the ends equal the single-device pass/fail colors.
const BINS = [[1, "#2A78D6", "100%"], [0.95, "#6DA7EC", "95–99%"], [0.8, "#B7D3F6", "80–94%"], [0.5, "#ECEAE4", "50–79%"],
              [1e-9, "#F8CDB9", "1–49%"], [0, "#F2A88A", "0%"]];
const rateColor = (r) => BINS.find(([lo]) => r >= lo - 1e-12)[1];
const cellColor = (v) => v === "P" ? TOK.pass : v === "F" ? TOK.fail : "#FFFFFF";
const idx = (vals, v) => { let best = -1, d = Infinity; vals.forEach((x, i) => { const e = Math.abs(+x - v); if (e < d) { d = e; best = i; } }); return best; };
const inRange = (vals, [lo, hi]) => vals.map((x, i) => [+x, i]).filter(([x]) => x >= lo - 1e-9 && x <= hi + 1e-9).map(([, i]) => i);

// ---- controls
function controls() {
  const box = $("controls");
  box.replaceChildren();
  const cur = D.combos[S.combo];
  D.filters.forEach((f, fi) => {
    const s = el("select");
    for (const v of f.values) s.append(new Option(v, v));
    s.value = cur[fi];
    s.onchange = () => {
      const vals = D.combos[S.combo].slice(); vals[fi] = s.value;
      const k = comboKey(vals);
      if (k >= 0) { S.combo = k; S.cell = null; draw(); }
    };
    const l = el("label", {}, f.name); l.append(s); box.append(l);
  });
  const d = el("select");
  d.append(new Option(devName(ALL), ALL));
  D.devices.forEach((n, i) => d.append(new Option(`${n}`, i)));
  d.value = S.device;
  d.onchange = () => { S.device = +d.value; draw(); };
  const l = el("label", {}, `장치 (${D.device_cols.join("-") || "device"})`); l.append(d); box.append(l);
}

// ---- the plot: one self-contained SVG (title, axes, legend) so a capture reads on its own
const CW = 30, CH = 22, ML = 64, MT = 58, MB = 66, MR = 16;
function plot() {
  const w = Math.max(ML + NX * CW + MR, 480), h = MT + NY * CH + MB;  // min width: title and legend fit
  const s = svgEl("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: `0 0 ${w} ${h}`, width: w, height: h,
                           style: `max-width: ${w}px`,
                           "font-family": FONT });
  s.append(svgEl("rect", { x: 0, y: 0, width: w, height: h, fill: "#ffffff" }));
  s.append(svgEl("text", { x: 12, y: 22, "font-size": 15, "font-weight": 700, fill: "#1c1917" }, D.title));
  s.append(svgEl("text", { x: 12, y: 42, "font-size": 12, fill: "#625c57" },
                 `${devName(S.device)}${D.filters.length ? " · " + condText(S.combo) : ""}`));
  const ci = S.combo;
  for (let j = 0; j < NY; j++) {
    for (let i = 0; i < NX; i++) {
      const x = ML + i * CW, y = MT + (NY - 1 - j) * CH;
      let fill, label = null;
      if (S.device === ALL) {
        const st = cellStats(ci, i, j);
        const n = st.p + st.f;
        fill = n ? rateColor(st.p / n) : "#FFFFFF";
        if (n && st.f && st.p) label = String(st.f);
      } else {
        const v = at(ci, S.device, i, j);
        fill = cellColor(v);
      }
      const r = svgEl("rect", { class: "c", x: x + 1, y: y + 1, width: CW - 2, height: CH - 2, rx: 2, fill,
                                stroke: fill === "#FFFFFF" ? TOK.untested : "none" });
      r.onclick = () => { S.cell = [i, j]; draw(); };
      s.append(r);
      if (label) s.append(svgEl("text", { x: x + CW / 2, y: y + CH / 2 + 4, "text-anchor": "middle", "font-size": 10,
                                         fill: "#1c1917", "pointer-events": "none" }, label));
    }
  }
  if (D.spec) {
    const xi = inRange(D.x.values, D.spec.x), yj = inRange(D.y.values, D.spec.y);
    if (xi.length && yj.length)
      s.append(svgEl("rect", { x: ML + xi[0] * CW, y: MT + (NY - 1 - yj[yj.length - 1]) * CH, width: xi.length * CW,
                               height: yj.length * CH, fill: "none", stroke: TOK.ink, "stroke-width": 2, "stroke-dasharray": "6 4", "pointer-events": "none" }));
  }
  if (D.op) {
    const i = idx(D.x.values, D.op.x), j = idx(D.y.values, D.op.y);
    s.append(svgEl("circle", { cx: ML + i * CW + CW / 2, cy: MT + (NY - 1 - j) * CH + CH / 2, r: 6, fill: "#fff", stroke: TOK.ink, "stroke-width": 2.5, "pointer-events": "none" }));
  }
  if (S.cell) {
    const [i, j] = S.cell;
    s.append(svgEl("rect", { x: ML + i * CW, y: MT + (NY - 1 - j) * CH, width: CW, height: CH, fill: "none",
                             stroke: "#1c1917", "stroke-width": 2, "pointer-events": "none" }));
  }
  const step = (n, px) => Math.max(1, Math.ceil(n * 14 / px));
  const ys = step(NY, NY * CH), xs = step(NX, NX * CW / 2.2);
  for (let j = 0; j < NY; j += ys)
    s.append(svgEl("text", { x: ML - 6, y: MT + (NY - 1 - j) * CH + CH / 2 + 4, "text-anchor": "end", "font-size": 11,
                             fill: "#1c1917" }, D.y.values[j]));
  for (let i = 0; i < NX; i += xs)
    s.append(svgEl("text", { x: ML + i * CW + CW / 2, y: MT + NY * CH + 15, "text-anchor": "middle", "font-size": 11,
                             fill: "#1c1917" }, D.x.values[i]));
  s.append(svgEl("text", { x: ML + NX * CW / 2, y: MT + NY * CH + 32, "text-anchor": "middle", "font-size": 12,
                           fill: "#625c57" }, D.x.name));
  s.append(svgEl("text", { x: 14, y: MT + NY * CH / 2, "text-anchor": "middle", "font-size": 12, fill: "#625c57",
                           transform: `rotate(-90 14 ${MT + NY * CH / 2})` }, D.y.name));
  // legend
  const ly = h - 12;
  if (S.device === ALL) {
    BINS.forEach(([, c], k) => s.append(svgEl("rect", { x: ML + k * 14, y: ly - 9, width: 14, height: 10, fill: c })));
    s.append(svgEl("text", { x: ML + 6 * 14 + 6, y: ly, "font-size": 11, fill: "#625c57" },
                   "통과율 100% → 0%   칸 숫자 = 실패한 장치 수"));
  } else {
    [[TOK.pass, "Pass"], [TOK.fail, "Fail"], ["#FFFFFF", "미측정"]].forEach(([c, t], k) => {
      s.append(svgEl("rect", { x: ML + k * 80, y: ly - 9, width: 12, height: 10, fill: c }));
      s.append(svgEl("text", { x: ML + k * 80 + 16, y: ly, "font-size": 11, fill: "#625c57" }, t));
    });
  }
  return s;
}

function detail() {
  const box = $("detail");
  box.replaceChildren();
  if (!S.cell) { box.append(el("span", { class: "muted" }, "칸을 클릭하면 그 조건에서 실패한 장치가 나와요.")); return; }
  const [i, j] = S.cell;
  box.append(el("div", {}, `${D.y.name} ${D.y.values[j]} · ${D.x.name} ${D.x.values[i]}`));
  if (S.device !== ALL) { box.append(el("div", {}, `${D.devices[S.device]}: ${({P: "Pass", F: "Fail"})[at(S.combo, S.device, i, j)] || "미측정"}`)); return; }
  const st = cellStats(S.combo, i, j);
  box.append(el("div", { class: "muted" }, `Pass ${st.p} · Fail ${st.f}`));
  const ul = el("ul", { class: "list" });
  for (const di of st.failing.slice(0, 50)) {
    const li = el("li", {}, D.devices[di]);
    li.onclick = () => { S.device = di; draw(); };
    ul.append(li);
  }
  if (st.failing.length > 50) ul.append(el("li", { class: "muted" }, `… 외 ${st.failing.length - 50}개`));
  box.append(ul);
}

const KIND = { hole: "구멍", wall: "벽", ceiling: "천장", low: "낮은 통과율" };
function findings() {
  const ul = $("findings");
  ul.replaceChildren();
  if (!D.findings.length) { ul.append(el("li", { class: "muted" }, "없음")); return; }
  for (const f of D.findings) {
    const li = el("li");
    li.append(el("span", { class: "tag" }, KIND[f.kind] || f.kind));
    const cond = Object.entries(f.condition).map(([k, v]) => `${k}=${v}`).join(", ");
    let what = f.kind === "hole" ? ` ${f.y[0]}–${f.y[1]} / ${f.x[0]}–${f.x[1]}` :
               f.kind === "wall" ? ` 최대 ${f.max_x_at_top_y} (보통 ${f.median_max_x_at_top_y})` :
               f.kind === "low" ? ` ${f.pass_rate}% (중앙값 ${f.median}%)` : ` ${f.y}에서 실패`;
    li.append(document.createTextNode(`${f.device} · ${cond}${what}`));
    const di = D.devices.indexOf(f.device), ci = comboKey(D.filters.map((x) => String(f.condition[x.name])));
    if (di === S.device && ci === S.combo) li.className = "on";
    li.onclick = () => { S.device = di; if (ci >= 0) S.combo = ci; S.cell = null; draw(); };
    ul.append(li);
  }
}

function devices() {
  const t = $("devices");
  t.replaceChildren();
  const head = el("tr"); head.append(el("th", {}, "장치"), el("th", { class: "num" }, "통과율"));
  t.append(head);
  const rows = D.devices.map((n, di) => ({ n, di, r: rate(S.combo, di) })).sort((a, b) => (a.r ?? 999) - (b.r ?? 999));
  for (const { n, di, r } of rows) {
    const tr = el("tr", { class: "row" + (di === S.device ? " on" : "") });
    tr.append(el("td", {}, n), el("td", { class: "num" }, r === null ? "-" : r.toFixed(1) + "%"));
    tr.onclick = () => { S.device = S.device === di ? ALL : di; draw(); };
    t.append(tr);
  }
}

function draw() {
  controls();
  $("plot").replaceChildren(plot());
  detail(); findings(); devices();
  titleBox(autoTitle("L"), `${S.device}|${S.combo}`);
}
"""

# ---- slide render (DESIGN.md 5.1 single device, 5.2 all devices) ------------------------------------------------
SLIDE = r"""
const AX = axisInfo(D.x.name, D.x.values), AY = axisInfo(D.y.name, D.y.values);
const condLabel = (ci) => D.filters.map((f, k) => { const a = axisInfo(f.name, [D.combos[ci][k]]); return `${a.base} ${D.combos[ci][k]}${a.unit}`; }).join(" · ");
const devLabel = (di) => D.devices[di];
const sameCond = (f, ci) => D.filters.every((x, k) => String(f.condition[x.name]) === D.combos[ci][k]);
const finds = (kind, di, ci) => D.findings.filter((f) => f.kind === kind && f.device === D.devices[di] && sameCond(f, ci));
const rowEdge = (ci, di, j) => { let m = -1; for (let i = 0; i < NX; i++) if (at(ci, di, i, j) === "P") m = i; return m; };

function specFails(ci, di) {
  if (!D.spec) return 0;
  let n = 0;
  for (const i of inRange(D.x.values, D.spec.x)) for (const j of inRange(D.y.values, D.spec.y)) n += at(ci, di, i, j) === "F";
  return n;
}
// Largest block of 100% cells starting at the lowest x, over any contiguous y range: "all N pass at y0–y1 up to x".
function allPassRect(ci) {
  const run = []; // per row: how many cells from x=0 every device passes
  for (let j = 0; j < NY; j++) { let w = 0; while (w < NX && cellStats(ci, w, j).f === 0 && cellStats(ci, w, j).p > 0) w++; run.push(w); }
  let best = null;
  for (let j0 = 0; j0 < NY; j0++) {
    let width = NX;
    for (let j1 = j0; j1 < NY; j1++) {
      width = Math.min(width, run[j1]);
      if (!width) break;
      const area = width * (j1 - j0 + 1);
      if (!best || area > best.area) best = { area, x: width - 1, y0: j0, y1: j1 };
    }
  }
  return best;
}

function autoTitle(preset) {
  const ci = S.combo, short = preset !== "L";
  if (S.device === ALL) {
    const r = allPassRect(ci), n = D.devices.length;
    if (!r) return short ? "전 장치 공통 통과 영역 없음" : `${n}개 장치가 함께 통과하는 조건 없음`;
    const yr = r.y1 === NY - 1 ? `${AY.fmtU(D.y.values[r.y0])} 이상` : `${AY.fmt(D.y.values[r.y0])}–${AY.fmtU(D.y.values[r.y1])}`;
    return short ? `전 장치 통과: ${yr}, ${AX.fmtU(D.x.values[r.x])}까지` : `${n}개 장치 모두 ${yr}에서 ${AX.fmtU(D.x.values[r.x])}까지 통과`;
  }
  const di = S.device, dev = devLabel(di);
  if (D.op) {
    const i = idx(D.x.values, D.op.x), j = idx(D.y.values, D.op.y);
    if (at(ci, di, i, j) === "F") return short ? `${dev}: 동작점 Fail` : `${dev}: 동작점(${AX.fmtU(D.x.values[i])}, ${AY.fmtU(D.y.values[j])})에서 Fail`;
  }
  const holes = finds("hole", di, ci), hole = holes[0];  // one finding per separate pocket, largest first
  if (hole) {
    const where = `${AY.fmt(hole.y[0])}–${AY.fmtU(hole.y[1])} × ${AX.fmt(hole.x[0])}–${AX.fmtU(hole.x[1])}`;
    if (holes.length > 1) return short ? `${dev}: 실패 구멍 ${holes.length}곳` : `${dev}: 실패 구멍 ${holes.length}곳 (가장 큰 곳 ${where} ${hole.cells}칸)`;
    return short ? `${dev}: 실패 구멍 ${hole.cells}칸` : `${dev}: ${where}에 실패 구멍 ${hole.cells}칸`;
  }
  const sf = specFails(ci, di);
  if (sf) return short ? `${dev}: Spec 안 Fail ${sf}칸` : `${dev}: Spec 영역 안 Fail ${sf}칸`;
  const wall = finds("wall", di, ci)[0];
  if (wall) return short ? `${dev}: ${AX.fmtU(wall.max_x_at_top_y)}에서 막힘` :
    `${dev}: 최고 전압에서도 ${AX.fmtU(wall.max_x_at_top_y)}까지 (다른 장치 중앙값 ${AX.fmtU(wall.median_max_x_at_top_y)})`;
  const ceil = finds("ceiling", di, ci)[0];
  if (ceil) return short ? `${dev}: ${AY.fmtU(ceil.y)}에서 Fail` : `${dev}: 최고 전압 ${AY.fmtU(ceil.y)}에서 Fail (과전압)`;
  const e = rowEdge(ci, di, NY - 1);
  if (e < 0) return `${dev}: 통과 조건 없음`;
  return short ? `${dev}: ${AX.fmtU(D.x.values[e])}까지 통과` : `${dev}: ${AY.fmtU(D.y.values[NY - 1])}에서 ${AX.fmtU(D.x.values[e])}까지 통과`;
}
function DEFAULT_PRESET() { return S.device === ALL ? "M" : "L"; }
function captureName(preset) {
  return fileSafe(["shmoo", S.device === ALL ? "all" : D.devices[S.device], ...D.combos[S.combo], preset, stamp()].join("_")) + ".png";
}

function renderSlide(preset, opts = {}) {
  const ci = S.combo, all = S.device === ALL, di = S.device;
  const cond = condLabel(ci);
  const F = slideFrame(preset, { showTitle: opts.showTitle, title: slideTitle(autoTitle(preset)),
    subtitle: all ? `통과율 · 장치 ${D.devices.length}개${cond ? " · " + cond : ""}`
                  : `${AY.base} × ${AX.base} shmoo · 장치 ${devLabel(di)}${D.device_cols.length ? ` (${D.device_cols.join("-")})` : ""}${cond ? " · " + cond : ""}`,
    source: all ? `출처: ${D.source || "로그"} · 칸 색 = 그 조건에서 통과한 장치 비율`
                : `출처: ${D.source || "로그"} · 판정 = ${D.result} · vanilaChatbot ${today()}` });
  const { svg } = F, P = F.plot;
  // legend
  const holes = all ? [] : finds("hole", di, ci), walls = all ? [] : finds("wall", di, ci), ceils = all ? [] : finds("ceiling", di, ci);
  const hasUntested = all ? D.grids[ci].some((g) => g.includes(".")) : D.grids[ci][di].includes(".");
  const items = all ? BINS.map(([, c, l]) => ({ label: l, fill: c }))
                    : [{ label: "Pass", fill: TOK.pass }, { label: "Fail", fill: TOK.fail }];
  if (hasUntested) items.push({ label: "미측정", fill: "#FFFFFF", stroke: TOK.untested });
  if (holes.length) items.push({ label: "실패 구멍", stroke: TOK.accent });
  if (D.spec && !all) items.push({ label: "Spec 영역", stroke: TOK.ink, dash: "4 3" });
  if (D.op && !all) items.push({ label: "동작점", kind: "circle" });
  const side = preset === "L" && !all;
  const notes = all ? D.findings.filter((f) => (f.kind === "low" || f.kind === "wall") && sameCond(f, ci)) : [];
  const noteH = notes.length ? 26 : 0;
  const legH = side ? 0 : legendRow(null, P.x, 0, items, P.w) + 4;
  // grid box
  const yTickW = Math.max(...D.y.values.map((v) => textW(AY.fmt(v), SZ.tick))) + 12;
  const gx = P.x + yTickW, gy = P.y + 30, gw = P.w - yTickW - (side ? 170 : 0), gh = P.h - 30 - 52 - legH - noteH;
  const cw = gw / NX, ch = gh / NY;
  txt(svg, P.x, P.y + 16, AY.label, SZ.tick, { fill: TOK.ink2 });
  for (let j = 0; j < NY; j++) for (let i = 0; i < NX; i++) {
    let fill;
    if (all) { const st = cellStats(ci, i, j), n = st.p + st.f; fill = n ? rateColor(st.p / n) : "#FFFFFF"; }
    else fill = cellColor(at(ci, di, i, j));
    svg.append(S_("rect", { x: gx + i * cw + 0.75, y: gy + (NY - 1 - j) * ch + 0.75, width: Math.max(cw - 1.5, 0.5),
                            height: Math.max(ch - 1.5, 0.5), fill, stroke: fill === "#FFFFFF" ? TOK.untested : "none", "stroke-width": 1 }));
    if (all && cw >= 28 && ch >= 20) {
      const st = cellStats(ci, i, j);
      if (st.f && st.p) txt(svg, gx + i * cw + cw / 2, gy + (NY - 1 - j) * ch + ch / 2 + 5, st.f, 14, { anchor: "middle", fill: TOK.ink });
    }
  }
  const ys = tickStep(NY, gh, 40), xs = Math.max(tickStep(NX, gw, 72), Math.ceil((Math.max(...D.x.values.map((v) => textW(AX.fmt(v), SZ.tick))) + 16) / cw));
  for (let j = 0; j < NY; j += ys) txt(svg, gx - 10, gy + (NY - 1 - j) * ch + ch / 2 + 6, AY.fmt(D.y.values[j]), SZ.tick, { anchor: "end", fill: TOK.ink2 });
  for (let i = 0; i < NX; i += xs) txt(svg, gx + i * cw + cw / 2, gy + gh + 24, AX.fmt(D.x.values[i]), SZ.tick, { anchor: "middle", fill: TOK.ink2 });
  txt(svg, gx + gw, gy + gh + 48, AX.label + " →", SZ.tick, { anchor: "end", fill: TOK.ink2 });
  // annotations (single device)
  const rowY = (j) => gy + (NY - 1 - j) * ch;
  for (const h of holes) {
    const i0 = D.x.values.indexOf(h.x[0]), i1 = D.x.values.indexOf(h.x[1]), j0 = D.y.values.indexOf(h.y[0]), j1 = D.y.values.indexOf(h.y[1]);
    const box = (x, y, w, hh) => svg.append(S_("rect", { x: x - 2, y: y - 2, width: w + 4, height: hh + 4, rx: 3, fill: "none",
                                                          stroke: TOK.accent, "stroke-width": 3, class: "hole" }));
    if (h.cells === (i1 - i0 + 1) * (j1 - j0 + 1)) box(gx + i0 * cw, rowY(j1), (i1 - i0 + 1) * cw, (j1 - j0 + 1) * ch);
    else for (const [i, j] of h.at) box(gx + i * cw, rowY(j), cw, ch);  // not a rectangle: outline only its own cells
  }
  for (const c of ceils) {
    const j = D.y.values.indexOf(c.y);
    svg.append(S_("line", { x1: gx, x2: gx + gw, y1: rowY(j) - 2, y2: rowY(j) - 2, stroke: TOK.accent, "stroke-width": 1.5 }));
    txt(svg, gx + gw, gy - 8, `천장 ${AY.fmtU(c.y)}`, SZ.tick, { anchor: "end", fill: TOK.accent, weight: 700 });
  }
  for (const w of walls) {
    const i = D.x.values.indexOf(w.max_x_at_top_y), lx = gx + (i + 1) * cw;
    svg.append(S_("line", { x1: lx, x2: lx, y1: gy, y2: gy + gh, stroke: TOK.accent, "stroke-width": 1.5, "stroke-dasharray": "5 4" }));
    const label = `벽 ${AX.fmtU(w.max_x_at_top_y)} (보통 ${AX.fmtU(w.median_max_x_at_top_y)})`;
    const right = lx + 6 + textW(label, SZ.tick, 700) <= gx + gw;
    txt(svg, right ? lx + 6 : lx - 6, gy - 8, label, SZ.tick, { anchor: right ? "start" : "end", fill: TOK.accent, weight: 700 });
  }
  if (D.spec && !all) {
    const xi = inRange(D.x.values, D.spec.x), yj = inRange(D.y.values, D.spec.y);
    if (xi.length && yj.length) {
      const sx = gx + xi[0] * cw, sy = rowY(yj[yj.length - 1]), sw = xi.length * cw, sh = yj.length * ch;
      svg.append(S_("rect", { x: sx, y: sy, width: sw, height: sh, fill: "none", stroke: TOK.ink, "stroke-width": 2, "stroke-dasharray": "6 4" }));
      svg.append(S_("rect", { x: sx + 4, y: sy + sh - 28, width: textW("Spec", SZ.tick, 700) + 12, height: 24, fill: "#FFFFFF" }));
      txt(svg, sx + 10, sy + sh - 10, "Spec", SZ.tick, { weight: 700 });
    }
  }
  if (D.op && !all) {
    const i = idx(D.x.values, D.op.x), j = idx(D.y.values, D.op.y);
    svg.append(S_("circle", { cx: gx + i * cw + cw / 2, cy: rowY(j) + ch / 2, r: 7, fill: "#FFFFFF", stroke: TOK.ink, "stroke-width": 2.5 }));
  }
  if (side) legendColumn(svg, gx + gw + 24, gy + 16, items);
  else legendRow(svg, P.x, gy + gh + 52 + 22, items, P.w);
  if (notes.length) {
    const t = notes.slice(0, 3).map((f) => f.kind === "low" ? `낮은 통과율: ${f.device} (${f.pass_rate}%, 중앙값 ${f.median}%)`
                                                         : `벽: ${f.device} (${AX.fmtU(f.max_x_at_top_y)}까지)`).join(" · ");
    txt(svg, P.x, P.y + P.h - 4, ellipsize(t, SZ.tick, 400, P.w), SZ.tick, { fill: TOK.ink });
  }
  return svg;
}
"""


def render(d):
    return (f'<!doctype html><html lang="ko"><meta charset="utf-8"><title>{html.escape(d["title"])}</title>'
            f'<style>{CSS.replace("MONO", MONO).replace("FONT", FONT)}</style><body><h1 id="h"></h1><div class="sub" id="sub"></div>'
            '<div class="controls" id="controls"></div><label class="titlebox">캡처 제목 <input id="slide-title"></label>'
            '<div class="layout"><div class="card" id="plot"></div>'
            '<div class="side"><div class="card"><h2>선택한 칸</h2><div id="detail"></div></div>'
            '<div class="card"><h2>이상 패턴</h2><ul class="list" id="findings"></ul></div>'
            '<div class="card"><h2>장치별 통과율 (낮은 순)</h2><div class="scroll"><table id="devices"></table></div></div>'
            '</div></div>'
            f'<script id="data" type="application/json">{data_json(d)}</script>'
            '<script>' + page_head() + SLIDE_JS + JS + SLIDE + 'draw(); document.getElementById("h").textContent = D.title;'
            'document.getElementById("sub").textContent = `${D.y.name} × ${D.x.name} · 장치 ${D.devices.length}개`'
            ' + (D.filters.length ? " · 조건 " + D.filters.map((f) => f.name).join(", ") : "");</script></body></html>')
