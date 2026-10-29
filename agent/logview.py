"""Interactive text-log view (Loop 18): counts over time by level, and by message pattern. No raw lines.
Parsing/aggregation is skills/log-triage/scripts/logs.py (the same code the agent runs in the sandbox).

Page: stacked bars per time bucket (levels toggle on/off), pattern table (click = chart shows only that pattern),
bar click = top patterns in that bucket. Capture redraws the chart as a slide (views.SLIDE_JS, docs/DESIGN.md 5.3)
with the incident window found and annotated, and a title that states it. Log-derived text (patterns)
only through textContent.
"""
import html
import importlib.util

from .skills import DIR
from .views import FONT, MONO, SLIDE_JS, data_json, page_head

_spec = importlib.util.spec_from_file_location("logs_core", DIR / "log-triage" / "scripts" / "logs.py")
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


def build(path, title=None, bucket=None, source=None):
    entries, skipped = core.parse(path)
    a = core.aggregate(entries, bucket)
    a["title"] = title or f"로그: {path.name}"
    a["continuation_lines"] = skipped
    a["source"] = source or path.name
    return a, core.summary(a)


CSS = """
* { box-sizing: border-box; }
body { margin: 0; padding: 16px; background: #f5f5f4; color: #1c1917;
       font: 14px/1.5 FONT; }
h1 { font-size: 17px; margin: 0 0 4px; font-weight: 700; }
.sub { color: #625c57; font-size: 13px; margin-bottom: 12px; }
.levels { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.chip { display: inline-flex; gap: 6px; align-items: center; border: 1px solid #e2e0dc; background: #fff; border-radius: 14px;
        padding: 2px 10px; font: inherit; font-size: 13px; cursor: pointer; }
.chip .dot { width: 9px; height: 9px; border-radius: 2px; }
.chip.off { opacity: .45; }
.chip .n { color: #625c57; font-variant-numeric: tabular-nums; }
.card { background: #fff; border: 1px solid #e2e0dc; border-radius: 8px; padding: 12px; margin-bottom: 12px; }
.card h2 { font-size: 13px; margin: 0 0 8px; font-weight: 700; display: flex; justify-content: space-between; gap: 8px; }
.card h2 button { font: inherit; font-size: 12px; border: 1px solid #e2e0dc; background: #fff; border-radius: 6px; padding: 1px 8px; cursor: pointer; }
#plot svg { display: block; width: 100%; height: auto; }
#plot rect.b { cursor: pointer; }
table { width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
td, th { padding: 4px 6px; border-bottom: 1px solid #ecebe8; text-align: left; vertical-align: top; }
th { color: #625c57; font-weight: 500; white-space: nowrap; }
td.num, th.num { text-align: right; white-space: nowrap; }
td.time { white-space: nowrap; color: #625c57; }
td.pat { font: 12px/1.45 MONO; word-break: break-all; }
tr.row { cursor: pointer; } tr.row:hover { background: #f0efed; } tr.on { background: #e6e4e1; }
.lv { display: inline-block; font-size: 11px; padding: 0 6px; border-radius: 9px; color: #fff; }
.scroll { max-height: 360px; overflow: auto; }
#bucket { font-size: 13px; color: #625c57; margin-bottom: 6px; min-height: 20px; }
.titlebox { display: flex; gap: 8px; align-items: center; font-size: 12px; color: #625c57; margin: 0 0 12px; }
.titlebox input { flex: 1; font: inherit; font-size: 13px; color: #1c1917; padding: 4px 8px; border: 1px solid #e2e0dc; border-radius: 6px; }
"""

# ---- slide render (DESIGN.md 5.3) ---------------------------------------------------------------------------------
SLIDE = r"""
const hm = (ms) => { const d = new Date(ms); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const errs = (c) => (c.FATAL || 0) + (c.ERROR || 0);
const summarize = (p) => p.replace(/^\[[^\]]*\]\s*/, "").replace(/<[a-z]+>/g, " ").replace(/[^\S\n]+/g, " ").trim();
// Incident windows: runs of buckets with errors >= max(median + 3*MAD, 20% of the peak); one quiet bucket inside a
// run is bridged. At most two, the largest first.
function incidents() {
  const e = D.buckets.map(errs), peak = Math.max(0, ...e);
  if (!peak) return [];
  const sorted = [...e].sort((a, b) => a - b), med = sorted[sorted.length >> 1];
  const mad = [...e.map((v) => Math.abs(v - med))].sort((a, b) => a - b)[e.length >> 1];
  const thr = Math.max(med + 3 * mad, 0.2 * peak, 1);
  const runs = [];
  let cur = null;
  e.forEach((v, i) => {
    if (v >= thr) { if (cur && i - cur.end <= 2) cur.end = i; else runs.push(cur = { start: i, end: i }); }
  });
  for (const r of runs) {
    r.count = e.slice(r.start, r.end + 1).reduce((a, b) => a + b, 0);
    r.peak = Math.max(...e.slice(r.start, r.end + 1));
    const inRun = ([b]) => b >= r.start && b <= r.end;
    let top = null, n = 0;
    for (const p of D.patterns) if (p.level === "ERROR" || p.level === "FATAL") {
      const c = p.series.filter(inRun).reduce((a, [, k]) => a + k, 0);
      if (c > n) { n = c; top = p; }
    }
    r.pattern = top ? summarize(top.pattern) : "";
  }
  // a run that is the whole log is "steady", not an incident
  return runs.filter((r) => r.end - r.start + 1 < e.length * 0.6).sort((a, b) => b.count - a.count).slice(0, 2);
}
const keywords = (t) => t.split(" ").slice(0, 5).join(" ");
function autoTitle(preset) {
  const short = preset !== "L", per = bsize();
  if (S.pattern !== null) {
    const p = D.patterns[S.pattern], s = summarize(p.pattern);
    return short ? `${keywords(s)}: ${p.count.toLocaleString()}건` : `${keywords(s)}: ${p.count.toLocaleString()}건 (${p.first.slice(11, 16)}–${p.last.slice(11, 16)})`;
  }
  const inc = incidents()[0], total = D.buckets.reduce((a, c) => a + errs(c), 0);
  if (inc) {
    const span = `${hm(T0 + inc.start * B)}–${hm(T0 + (inc.end + 1) * B)}`;
    return short ? `${span} ERROR 급증` : `${span} ERROR 급증 (${per}당 최대 ${inc.peak}건)${inc.pattern ? " — " + inc.pattern : ""}`;
  }
  return total ? `${D.start.slice(5, 16)} – ${D.end.slice(11, 16)} ERROR ${total.toLocaleString()}건, 고르게 분포` : "ERROR 없음";
}
function DEFAULT_PRESET() { return "L"; }
function captureName(preset) {
  const what = S.pattern !== null ? `pattern${S.pattern}` : [...S.on].join("-") || "none";
  return fileSafe(["log", D.source.replace(/\.[^.]+$/, ""), what, preset, stamp()].join("_")) + ".png";
}
// Round clock ticks: the smallest of these steps that keeps labels >= 72 px apart.
const STEPS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800].map((s) => s * 1000);
function renderSlide(preset, opts = {}) {
  const data = series(), n = data.length, sel = S.pattern !== null;
  const F = slideFrame(preset, { showTitle: opts.showTitle, title: slideTitle(autoTitle(preset)),
    subtitle: sel ? `패턴: ${summarize(D.patterns[S.pattern].pattern)}`
                  : `${D.source} · ${D.start.slice(0, 16)} – ${D.end.slice(11, 16)} · ${D.lines.toLocaleString()}건 · ${bsize()} 단위`,
    source: `출처: ${D.source} · 레벨 = 로그 레벨 필드 · vanilaChatbot ${today()}` });
  const { svg } = F, P = F.plot;
  const tot = data.map((c) => Object.values(c).reduce((a, b) => a + b, 0)), max = Math.max(1, ...tot);
  const step = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 1e4, 2e4, 5e4, 1e5].find((s) => max / s <= 4) || 1e5;
  const top = Math.ceil(max / step) * step;
  const yw = textW(top.toLocaleString(), SZ.tick) + 12;
  const gx = P.x + yw, gy = P.y + 30, gw = P.w - yw, gh = P.h - 30 - 34, bw = gw / n;
  const Y = (v) => gy + gh - v / top * gh;
  const inc = sel ? [] : incidents();
  for (const r of inc) svg.append(S_("rect", { x: gx + r.start * bw, y: gy, width: (r.end - r.start + 1) * bw, height: gh, fill: TOK.band }));
  for (let v = 0; v <= top; v += step) {
    svg.append(S_("line", { x1: gx, x2: gx + gw, y1: Y(v), y2: Y(v), stroke: v ? TOK.grid : TOK.axis, "stroke-width": 1 }));
    txt(svg, gx - 10, Y(v) + 5, v.toLocaleString(), SZ.tick, { anchor: "end", fill: TOK.ink2 });
  }
  data.forEach((c, i) => {
    let y = gy + gh;
    for (const l of ORDER) {
      if (!c[l]) continue;
      const h = c[l] / top * gh;
      y -= h;
      svg.append(S_("rect", { x: gx + i * bw + (bw > 4 ? 0.75 : 0), y, width: Math.max(bw - (bw > 4 ? 1.5 : 0), 0.6), height: h,
                              fill: sel ? (l === "FATAL" || l === "ERROR" ? TOK.accent : COLORS[l]) : COLORS[l] }));
    }
  });
  const t0 = T0, t1 = T0 + n * B, stepMs = STEPS.find((s) => s / B * bw >= 72) || STEPS[STEPS.length - 1];
  const off = new Date(t0).getTimezoneOffset() * 60000;
  let first = true, lastRight = -Infinity;
  for (let t = Math.ceil((t0 - off) / stepMs) * stepMs + off; t <= t1; t += stepMs) {
    const x = gx + (t - t0) / B * bw, d = new Date(t);
    const label = (first || (d.getHours() === 0 && d.getMinutes() === 0) ? `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` : "") + hm(t);
    const w = textW(label, SZ.tick), left = first ? x : x - w / 2;
    if (left < lastRight + 16) continue;  // the dated first label is wider: skip a tick it would collide with
    txt(svg, x, gy + gh + 24, label, SZ.tick, { anchor: first ? "start" : "middle", fill: TOK.ink2 });
    lastRight = left + w;
    first = false;
  }
  txt(svg, gx + gw, P.y + 16, `${bsize()}당 건수`, SZ.tick, { anchor: "end", fill: TOK.ink2 });
  if (!sel) {
    const levels = ORDER.filter((l) => data.some((c) => c[l]));
    legendRow(svg, gx + 4, P.y + 16, levels.map((l) => ({ label: l === "-" ? "레벨 없음" : l, fill: COLORS[l] })), gw * 0.6);
  }
  for (const r of inc) {
    const x0 = gx + r.start * bw, x1 = gx + (r.end + 1) * bw;
    const l1 = `${hm(T0 + r.start * B)}–${hm(T0 + (r.end + 1) * B)} 장애 구간`;
    const l2 = `ERROR ${r.count.toLocaleString()}건${r.pattern ? " · " + r.pattern.slice(0, 40) : ""}`;
    const w = Math.max(textW(l1, SZ.label, 700), textW(l2, SZ.label));
    const right = x1 + 12 + w <= gx + gw, ax = right ? x1 + 12 : x0 - 12, anchor = right ? "start" : "end";
    const ay = Math.max(Y(r.peak) + 10, gy + 60);
    const l2w = Math.min(textW(l2, SZ.label), gw * 0.6), bx = right ? ax - 4 : ax - Math.max(textW(l1, SZ.label, 700), l2w) - 4;
    svg.append(S_("rect", { x: bx, y: ay - 22, width: Math.max(textW(l1, SZ.label, 700), l2w) + 8, height: 54, fill: "#FFFFFF", "fill-opacity": 0.9 }));
    svg.append(S_("line", { x1: right ? x1 : x0, x2: ax + (right ? -4 : 4), y1: ay - 6, y2: ay - 6, stroke: TOK.ink, "stroke-width": 1.2 }));
    txt(svg, ax, ay, l1, SZ.label, { anchor, weight: 700 });
    txt(svg, ax, ay + 24, ellipsize(l2, SZ.label, 400, gw * 0.6), SZ.label, { anchor, fill: TOK.ink2 });
  }
  return svg;
}
"""

JS = r"""
const D = JSON.parse(document.getElementById("data").textContent);
// DESIGN.md 4.1: errors carry the color, normal traffic is grey; stacked with the severe levels on the baseline.
const COLORS = { FATAL: "#7A1A12", ERROR: "#B42318", WARN: "#E8A317", INFO: "#CFCDC7", DEBUG: "#E6E4DF", "-": "#DAD8D2" };
const ORDER = ["FATAL", "ERROR", "WARN", "INFO", "DEBUG", "-"];
const S = { on: new Set(Object.keys(D.levels).filter((l) => l !== "INFO" && l !== "DEBUG")), pattern: null, bucket: null };
if (!S.on.size) Object.keys(D.levels).forEach((l) => S.on.add(l));
const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, text) => { const n = document.createElement(tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (text !== undefined) n.textContent = text; return n; };
const svgEl = (tag, attrs = {}, text) => { const n = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (text !== undefined) n.textContent = text; return n; };
const T0 = new Date(D.start.replace(" ", "T")).getTime(), B = D.bucket_seconds * 1000;
const pad = (n) => String(n).padStart(2, "0");
const tlabel = (i, full) => { const d = new Date(T0 + i * B); return (full || i === 0 ? `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` : "") + `${pad(d.getHours())}:${pad(d.getMinutes())}` + (D.bucket_seconds < 60 ? `:${pad(d.getSeconds())}` : ""); };
const bsize = () => D.bucket_seconds >= 3600 ? `${D.bucket_seconds / 3600}시간` : D.bucket_seconds >= 60 ? `${D.bucket_seconds / 60}분` : `${D.bucket_seconds}초`;

function series() {  // per bucket: {level: count} for what is selected
  if (S.pattern !== null) {
    const p = D.patterns[S.pattern], out = D.buckets.map(() => ({}));
    for (const [b, c] of p.series) out[b][p.level] = c;
    return out;
  }
  return D.buckets.map((c) => Object.fromEntries(Object.entries(c).filter(([l]) => S.on.has(l))));
}

function chips() {
  const box = $("levels"); box.replaceChildren();
  for (const l of ORDER.filter((x) => D.levels[x])) {
    const b = el("button", { class: "chip" + (S.on.has(l) ? "" : " off") });
    const dot = el("span", { class: "dot" }); dot.style.background = COLORS[l];
    b.append(dot, el("span", {}, l === "-" ? "레벨 없음" : l), el("span", { class: "n" }, D.levels[l].toLocaleString()));
    b.onclick = () => { S.on.has(l) ? S.on.delete(l) : S.on.add(l); S.pattern = null; draw(); };
    box.append(b);
  }
}

const W = 720, H = 300, ML = 52, MR = 14, MT = 58, MB = 62;
function plot() {
  const data = series(), n = data.length;
  const tot = data.map((c) => Object.values(c).reduce((a, b) => a + b, 0));
  const max = Math.max(1, ...tot);
  const step = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 1e4, 2e4, 5e4, 1e5, 2e5, 5e5, 1e6].find((s) => max / s <= 5) || 1e6;
  const top = Math.ceil(max / step) * step;
  const pw = W - ML - MR, ph = H - MT - MB, bw = pw / n;
  const s = svgEl("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: `0 0 ${W} ${H}`, width: W, height: H,
    "font-family": FONT });
  s.append(svgEl("rect", { x: 0, y: 0, width: W, height: H, fill: "#ffffff" }));
  s.append(svgEl("text", { x: 12, y: 24, "font-size": 16, "font-weight": 700, fill: "#1c1917" }, D.title));
  const what = S.pattern !== null ? `패턴: ${D.patterns[S.pattern].pattern}` : `레벨: ${[...S.on].join(", ") || "없음"}`;
  s.append(svgEl("text", { x: 12, y: 42, "font-size": 13, fill: "#625c57" }, `${what.length > 90 ? what.slice(0, 89) + "…" : what} · ${bsize()} 단위`));
  for (let v = 0; v <= top; v += step) {
    const y = MT + ph - v / top * ph;
    s.append(svgEl("line", { x1: ML, x2: W - MR, y1: y, y2: y, stroke: "#ecebe8" }));
    s.append(svgEl("text", { x: ML - 6, y: y + 4, "text-anchor": "end", "font-size": 12, fill: "#625c57" }, v.toLocaleString()));
  }
  data.forEach((c, i) => {
    let y = MT + ph;
    for (const l of ORDER) {
      if (!c[l]) continue;
      const h = c[l] / top * ph;
      y -= h;
      s.append(svgEl("rect", { x: ML + i * bw + (bw > 4 ? 0.5 : 0), y, width: Math.max(bw - (bw > 4 ? 1 : 0), 0.5), height: h, fill: COLORS[l], "pointer-events": "none" }));
    }
    const hit = svgEl("rect", { class: "b", x: ML + i * bw, y: MT, width: bw, height: ph, fill: "transparent" });
    hit.onclick = () => { S.bucket = S.bucket === i ? null : i; draw(); };
    s.append(hit);
  });
  if (S.bucket !== null) s.append(svgEl("rect", { x: ML + S.bucket * bw, y: MT, width: bw, height: ph, fill: "none", stroke: "#1c1917", "stroke-width": 1.5, "pointer-events": "none" }));
  const every = Math.max(1, Math.ceil(n / 6));
  for (let i = 0; i < n; i += every)
    s.append(svgEl("text", { x: ML + i * bw, y: MT + ph + 17, "font-size": 12, fill: "#1c1917" }, tlabel(i)));
  let lx = ML;
  for (const l of ORDER.filter((x) => data.some((c) => c[x]))) {
    s.append(svgEl("rect", { x: lx, y: H - 22, width: 10, height: 10, fill: COLORS[l] }));
    const t = svgEl("text", { x: lx + 14, y: H - 13, "font-size": 12, fill: "#625c57" }, l === "-" ? "레벨 없음" : l);
    s.append(t); lx += 26 + 8 * (l === "-" ? 6 : l.length);
  }
  s.append(svgEl("text", { x: W - MR, y: H - 13, "text-anchor": "end", "font-size": 12, fill: "#625c57" }, `건수 / ${bsize()}`));
  return s;
}

function bucketInfo() {
  const box = $("bucket"); box.replaceChildren();
  if (S.bucket === null) { box.textContent = "막대를 클릭하면 그 시간대에 많이 나온 패턴이 위에 표시돼요."; return; }
  box.textContent = `${tlabel(S.bucket, true)}부터 ${bsize()}: 패턴 표를 이 구간 건수로 정렬했어요.`;
}

function table() {
  const t = $("patterns"); t.replaceChildren();
  const head = el("tr");
  head.append(el("th", {}, "레벨"), el("th", { class: "num" }, S.bucket !== null ? "이 구간" : "건수"), el("th", {}, "처음 – 마지막"), el("th", {}, "패턴"));
  t.append(head);
  const inBucket = (p) => (p.series.find(([b]) => b === S.bucket) || [0, 0])[1];
  let rows = D.patterns.map((p, i) => ({ p, i }));
  if (S.bucket !== null) rows = rows.filter(({ p }) => inBucket(p)).sort((a, b) => inBucket(b.p) - inBucket(a.p));
  else rows = rows.filter(({ p, i }) => S.on.has(p.level) || i === S.pattern);
  for (const { p, i } of rows) {
    const tr = el("tr", { class: "row" + (i === S.pattern ? " on" : "") });
    const lv = el("span", { class: "lv" }, p.level === "-" ? "-" : p.level); lv.style.background = COLORS[p.level];
    const td = el("td"); td.append(lv);
    tr.append(td, el("td", { class: "num" }, (S.bucket !== null ? inBucket(p) : p.count).toLocaleString()),
              el("td", { class: "time" }, p.first ? `${p.first.slice(5, 16)} – ${p.last.slice(5, 16)}` : ""),
              el("td", { class: "pat" }, p.pattern));
    tr.onclick = () => { S.pattern = S.pattern === i ? null : i; draw(); };
    t.append(tr);
  }
  $("reset").hidden = S.pattern === null && S.bucket === null;
}

function draw() { chips(); $("plot").replaceChildren(plot()); bucketInfo(); table(); titleBox(autoTitle("L")); }
$("reset").onclick = () => { S.pattern = null; S.bucket = null; draw(); };
document.getElementById("h").textContent = D.title;
document.getElementById("sub").textContent = `${D.start} – ${D.end} · ${D.lines.toLocaleString()}건 · 패턴 ${D.patterns.length}개` +
  (D.continuation_lines ? ` · 시각 없는 줄 ${D.continuation_lines.toLocaleString()}개는 앞 줄에 포함` : "");
"""


def render(d):
    return (f'<!doctype html><html lang="ko"><meta charset="utf-8"><title>{html.escape(d["title"])}</title>'
            f'<style>{CSS.replace("MONO", MONO).replace("FONT", FONT)}</style><body><h1 id="h"></h1><div class="sub" id="sub"></div>'
            '<div class="levels" id="levels"></div><label class="titlebox">캡처 제목 <input id="slide-title"></label>'
            '<div class="card"><div id="plot"></div></div>'
            '<div class="card"><h2><span>패턴 (숫자·ID·IP는 가림, 에러 먼저)</span><button id="reset" hidden>선택 해제</button></h2>'
            '<div id="bucket"></div><div class="scroll"><table id="patterns"></table></div></div>'
            f'<script id="data" type="application/json">{data_json(d)}</script>'
            '<script>' + page_head() + SLIDE_JS + JS + SLIDE + 'draw();</script></body></html>')
