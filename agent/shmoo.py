"""Interactive shmoo view (Loop 16): fixed page (our JS) + data built server-side from a test log.
The model only names the log file and its columns (show_shmoo); parsing and analysis are skills/shmoo/scripts/shmoo.py,
the same code the agent runs in the sandbox, so the view and the agent's numbers agree.

Page: condition filters, device = one device or all combined (pass rate), click a cell for the devices failing
there, findings and a device table that select what they point at. The parent app asks for a PNG of the current
view by postMessage {type: "capture", nonce}; the page answers with the PNG bytes (it has no network).
All log-derived text is set with textContent; the data is inlined as JSON with `<` escaped.
"""
import html
import importlib.util

from .skills import DIR
from .views import CAPTURE_JS, FONT, MONO, data_json

_spec = importlib.util.spec_from_file_location("shmoo_core", DIR / "shmoo" / "scripts" / "shmoo.py")
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)

MAX_CHARS = 900_000  # artifact content limit is 1 MB


def build(path, x, y, result, device, filters=(), pass_values=None, title=None):
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
            "combos": [list(c) for c in combos], "grids": grids, "findings": core.analyze(g)}
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

function mix(a, b, t) {
  const h = (s) => [1, 3, 5].map((k) => parseInt(s.slice(k, k + 2), 16));
  const [x, y] = [h(a), h(b)];
  return "#" + x.map((v, k) => Math.round(v + (y[k] - v) * t).toString(16).padStart(2, "0")).join("");
}
const rateColor = (r) => r < 0.5 ? mix("#c2410c", "#f3efe9", r / 0.5) : mix("#f3efe9", "#0e7490", (r - 0.5) / 0.5);

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
        fill = n ? rateColor(st.p / n) : "#e7e5e4";
        if (n && st.f && st.p) label = String(st.f);
      } else {
        const v = at(ci, S.device, i, j);
        fill = v === "P" ? "#0e7490" : v === "F" ? "#c2410c" : "#e7e5e4";
      }
      const r = svgEl("rect", { class: "c", x: x + 1, y: y + 1, width: CW - 2, height: CH - 2, rx: 2, fill });
      r.onclick = () => { S.cell = [i, j]; draw(); };
      s.append(r);
      if (label) s.append(svgEl("text", { x: x + CW / 2, y: y + CH / 2 + 4, "text-anchor": "middle", "font-size": 10,
                                         fill: "#1c1917", "pointer-events": "none" }, label));
    }
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
    for (let k = 0; k <= 10; k++)
      s.append(svgEl("rect", { x: ML + k * 12, y: ly - 9, width: 12, height: 10, fill: rateColor(k / 10) }));
    s.append(svgEl("text", { x: ML + 11 * 12 + 6, y: ly, "font-size": 11, fill: "#625c57" },
                   "통과율 0% → 100%   칸 숫자 = 실패한 장치 수"));
  } else {
    [["#0e7490", "Pass"], ["#c2410c", "Fail"], ["#e7e5e4", "미측정"]].forEach(([c, t], k) => {
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
}

const captureName = () => [S.device === ALL ? "all" : D.devices[S.device], ...D.combos[S.combo]].join("_");
draw();
"""


def render(d):
    return (f'<!doctype html><html lang="ko"><meta charset="utf-8"><title>{html.escape(d["title"])}</title>'
            f'<style>{CSS.replace("MONO", MONO).replace("FONT", FONT)}</style><body><h1 id="h"></h1><div class="sub" id="sub"></div>'
            '<div class="controls" id="controls"></div><div class="layout"><div class="card" id="plot"></div>'
            '<div class="side"><div class="card"><h2>선택한 칸</h2><div id="detail"></div></div>'
            '<div class="card"><h2>이상 패턴</h2><ul class="list" id="findings"></ul></div>'
            '<div class="card"><h2>장치별 통과율 (낮은 순)</h2><div class="scroll"><table id="devices"></table></div></div>'
            '</div></div>'
            f'<script id="data" type="application/json">{data_json(d)}</script>'
            '<script>' + f'const FONT = {data_json(FONT)};' + JS + CAPTURE_JS + 'document.getElementById("h").textContent = D.title;'
            'document.getElementById("sub").textContent = `${D.y.name} × ${D.x.name} · 장치 ${D.devices.length}개`'
            ' + (D.filters.length ? " · 조건 " + D.filters.map((f) => f.name).join(", ") : "");</script></body></html>')
