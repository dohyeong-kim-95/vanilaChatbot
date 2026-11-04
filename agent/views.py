"""Shared pieces of our interactive template views (shmoo, logview) and of the slide renders (docs/DESIGN.md).

Capture: the app posts {type: "capture", nonce, preset: "auto"|"L"|"M"|"S", title: bool} to the frame. The page
draws a fresh slide SVG for that preset with renderSlide(preset, {showTitle}) (no UI state such as a selected cell),
rasterizes it at 2x, tags the PNG with 192 dpi (pHYs) and posts {type: "capture", nonce, name, png} back. The frame
has no network and an opaque origin; the app checks the source window, the nonce and the PNG signature
(server/static/app.js frameCapture). A page using SLIDE_JS defines renderSlide(preset, opts), captureName(preset)
and DEFAULT_PRESET().
"""
import json
import struct
import zlib

# Owner's choice (DESIGN.md 4.2): Korean = 맑은 고딕, Latin/digits = Arial, as in Korean PowerPoint. Arial first: it
# has no Hangul, so Hangul falls through to Malgun Gothic. Both are licensed system fonts (not bundled). Fallbacks are
# open (OFL) look-alikes we can install anywhere: Arimo (Arial metrics) and Noto Sans KR; then whatever is there.
FONT = ('Arial, Arimo, "Malgun Gothic", "맑은 고딕", "Noto Sans KR", "Apple SD Gothic Neo", "Noto Sans CJK KR", '
        '"Liberation Sans", sans-serif')
MONO = 'Consolas, "Liberation Mono", ui-monospace, monospace'

# DESIGN.md 3.1 / 4.1 / 4.2. Canvas in logical px (1 px = 0.75 pt on the slide); exported at 2x.
PRESETS = {"L": (1180, 540), "M": (576, 540), "S": (376, 540)}
PAD = {"L": (28, 32, 22, 32), "M": (24, 24, 22, 24), "S": (20, 20, 22, 20)}  # top, right, bottom, left
TOKENS = {"ink": "#1A1A1A", "ink2": "#5C5C5C", "muted": "#767676", "grid": "#E6E6E6", "axis": "#BDBDBD",
          "band": "#F4F2EE", "accent": "#B42318", "pass": "#2A78D6", "fail": "#F2A88A", "untested": "#D0CEC8",
          "over": "#D4572A", "overText": "#A33A14", "neutral": "#C9C7C1"}
SIZES = {"title": 30, "sub": 20, "label": 18, "tick": 16, "source": 14}


def data_json(obj):
    """JSON safe to inline in <script type=application/json>: no `</script>` breakout."""
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")


def with_dpi(png, dpi=192):
    """Insert a pHYs chunk after IHDR so PowerPoint places the 2x image at its logical size."""
    ppm = round(dpi / 0.0254)
    body = b"pHYs" + struct.pack(">IIB", ppm, ppm, 1)
    chunk = struct.pack(">I", 9) + body + struct.pack(">I", zlib.crc32(body))
    return png[:33] + chunk + png[33:]  # 8-byte signature + IHDR (4 + 4 + 13 + 4)


def page_head():
    """JS constants shared by the page scripts (inlined before SLIDE_JS)."""
    return (f"const FONT = {data_json(FONT)}; const TOK = {data_json(TOKENS)}; const SZ = {data_json(SIZES)};"
            f"const PRESETS = {data_json(PRESETS)}; const PAD = {data_json(PAD)};")


SLIDE_JS = r"""
const SVGNS = "http://www.w3.org/2000/svg";
function S_(tag, attrs = {}, text) {  // an SVG element
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  return n;
}
function el(tag, attrs = {}, text) {  // an HTML element
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  return n;
}
const _mctx = document.createElement("canvas").getContext("2d");
function textW(t, size, weight = 400) { _mctx.font = `${weight} ${size}px ${FONT}`; return _mctx.measureText(String(t)).width; }
function ellipsize(t, size, weight, maxW) {
  t = String(t);
  if (textW(t, size, weight) <= maxW) return t;
  while (t.length > 1 && textW(t + "…", size, weight) > maxW) t = t.slice(0, -1);
  return t + "…";
}
// Wrap at spaces (Korean titles have spaces between words); the last line is ellipsized.
function wrap(t, size, weight, maxW, maxLines) {
  const words = String(t).split(/\s+/), lines = [];
  let cur = "";
  for (const w of words) {
    const next = cur ? cur + " " + w : w;
    if (textW(next, size, weight) <= maxW || !cur) cur = next;
    else { lines.push(cur); cur = w; }
  }
  if (cur) lines.push(cur);
  if (lines.length > maxLines) lines.splice(maxLines - 1, lines.length, lines.slice(maxLines - 1).join(" "));
  lines[lines.length - 1] = ellipsize(lines[lines.length - 1], size, weight, maxW);
  return lines;
}
function txt(svg, x, y, t, size, o = {}) {
  svg.append(S_("text", { x, y, "font-size": size, "font-weight": o.weight || 400, fill: o.fill || TOK.ink,
                          "text-anchor": o.anchor || "start", ...(o.attrs || {}) }, t));
}
// Title (<= 2 lines) + subtitle + source line; returns the plot box (what is left).
function slideFrame(preset, o) {
  const [W, H] = PRESETS[preset], [pt, pr, pb, pl] = PAD[preset];
  const svg = S_("svg", { xmlns: SVGNS, width: W, height: H, viewBox: `0 0 ${W} ${H}`, "font-family": FONT });
  svg.append(S_("rect", { x: 0, y: 0, width: W, height: H, fill: "#FFFFFF" }));
  const inner = W - pl - pr;
  let y = pt;
  if (o.showTitle !== false) {
    for (const line of wrap(o.title || "", SZ.title, 700, inner, 2)) { y += SZ.title; txt(svg, pl, y, line, SZ.title, { weight: 700 }); y += 9; }
    if (o.subtitle) { y += SZ.sub + 2; txt(svg, pl, y, ellipsize(o.subtitle, SZ.sub, 400, inner), SZ.sub, { fill: TOK.ink2 }); }
    y += 24;
  }
  const srcY = H - pb;
  if (o.source) txt(svg, pl, srcY, ellipsize(o.source, SZ.source, 400, inner), SZ.source, { fill: TOK.muted });
  return { svg, W, H, plot: { x: pl, y, w: inner, h: srcY - SZ.source - 14 - y } };
}
// Label every k-th of n positions so labels are >= minGap px apart.
function tickStep(n, span, minGap) {
  for (const k of [1, 2, 3, 4, 5, 10, 20, 25, 50, 100, 200, 500]) if (span / n * k >= minGap) return k;
  return n;
}
// Legend items [{label, fill, stroke?, dash?, kind?}] in rows; svg = null only measures. Returns the height used.
function legendRow(svg, x, y, items, maxW) {
  let cx = x, cy = y;
  for (const it of items) {
    const w = 16 + 8 + textW(it.label, SZ.tick) + 20;
    if (cx + w > x + maxW && cx > x) { cx = x; cy += 26; }
    if (svg) { swatch(svg, cx, cy - 13, it); txt(svg, cx + 24, cy, it.label, SZ.tick, { fill: TOK.ink2 }); }
    cx += w;
  }
  return cy - y + 26;
}
function legendColumn(svg, x, y, items) {
  items.forEach((it, k) => { swatch(svg, x, y + k * 32 - 13, it); txt(svg, x + 24, y + k * 32, it.label, SZ.tick, { fill: TOK.ink2 }); });
}
// Axis from a column name: "VDD_V" -> VDD (V), "FREQ_MHZ" -> 주파수 (GHz) when every value >= 1000 MHz.
const AXIS_NAMES = { VDD: "VDD", VCC: "VCC", FREQ: "주파수", FREQUENCY: "주파수", TEMP: "온도", PERIOD: "주기" };
const UNITS = { V: "V", MV: "mV", MHZ: "MHz", GHZ: "GHz", KHZ: "kHz", HZ: "Hz", NS: "ns", PS: "ps", C: "°C" };
function axisInfo(name, values) {  // unit only after a separator: VCC is not "VC (°C)"
  const m = /^(.*?)[_ -](MV|V|GHZ|MHZ|KHZ|HZ|NS|PS|C)$/i.exec(name) || [null, name, ""];
  const base = AXIS_NAMES[m[1].toUpperCase()] || m[1];
  let unit = UNITS[m[2].toUpperCase()] || "", f = 1;
  const nums = values.map(Number);
  if (unit === "MHz" && nums.every((v) => v >= 1000)) { f = 1e-3; unit = "GHz"; }
  const dec = Math.max(0, ...nums.map((v) => { const s = String(+(v * f).toFixed(6)); return s.includes(".") ? s.split(".")[1].length : 0; }));
  const d = unit === "V" ? Math.max(2, dec) : Math.min(dec, 3);
  const fmt = (v) => isNaN(+v) || v === "" ? String(v) : (+v * f).toFixed(d);
  return { base, unit, label: base + (unit ? ` (${unit})` : ""), fmt, fmtU: (v) => fmt(v) + (unit ? " " + unit : "") };
}
function swatch(svg, x, y, it) {
  if (it.kind === "circle") svg.append(S_("circle", { cx: x + 8, cy: y + 8, r: 6, fill: "#fff", stroke: TOK.ink, "stroke-width": 2.5 }));
  else svg.append(S_("rect", { x: x + 1, y: y + 1, width: 14, height: 14, rx: 2, fill: it.fill || "none",
                               stroke: it.stroke || "none", "stroke-width": it.stroke ? 2 : 0, ...(it.dash ? { "stroke-dasharray": it.dash } : {}) }));
}
function today() { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function stamp() { const d = new Date(), p = (n) => String(n).padStart(2, "0"); return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`; }
function fileSafe(s) { return String(s).replace(/[^\w.-]+/g, "_").slice(0, 80); }

// PNG at 2x with a pHYs chunk (192 dpi), so it lands on the slide at its logical size.
const _crcT = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
function crc32(b) { let c = 0xFFFFFFFF; for (const x of b) c = _crcT[(c ^ x) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; }
function withDpi(buf, dpi = 192) {
  const ppm = Math.round(dpi / 0.0254), body = new Uint8Array(13), dv = new DataView(body.buffer);
  body.set([112, 72, 89, 115]); dv.setUint32(4, ppm); dv.setUint32(8, ppm); body[12] = 1;
  const chunk = new Uint8Array(21), cv = new DataView(chunk.buffer);
  cv.setUint32(0, 9); chunk.set(body, 4); cv.setUint32(17, crc32(body));
  const src = new Uint8Array(buf), out = new Uint8Array(src.length + 21);
  out.set(src.subarray(0, 33)); out.set(chunk, 33); out.set(src.subarray(33), 54);
  return out.buffer;
}
function svgPng(svg) {  // 2x: sharp in a document
  const w = +svg.getAttribute("width"), h = +svg.getAttribute("height");
  const src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(svg));
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = w * 2; c.height = h * 2;
      const g = c.getContext("2d"); g.scale(2, 2); g.drawImage(img, 0, 0);
      c.toBlob((b) => b ? b.arrayBuffer().then((a) => resolve(withDpi(a))) : reject(new Error("toBlob failed")), "image/png");
    };
    img.onerror = () => reject(new Error("svg load failed"));
    img.src = src;
  });
}
window.addEventListener("message", async (e) => {
  if (e.source !== parent || !e.data || e.data.type !== "capture") return;
  try {
    const preset = PRESETS[e.data.preset] ? e.data.preset : DEFAULT_PRESET();
    const png = await svgPng(renderSlide(preset, { showTitle: e.data.title !== false }));
    parent.postMessage({ type: "capture", nonce: e.data.nonce, name: captureName(preset), png }, "*", [png]);
  } catch (err) {
    parent.postMessage({ type: "capture", nonce: e.data.nonce, error: String(err) }, "*");
  }
});
// Editable slide title: prefilled with the automatic one until the user types. key = what the slide shows
// (device, condition, pattern); a typed title is dropped when that changes, so it never labels another figure.
let customTitle = null, titleKey = null;
function titleBox(auto, key = "") {
  if (key !== titleKey) { customTitle = null; titleKey = key; }
  const box = document.getElementById("slide-title");
  if (!box) return;
  if (customTitle === null) box.value = auto;
  box.oninput = () => { customTitle = box.value.trim() || null; };
}
function slideTitle(auto) { return customTitle ?? auto; }
"""
