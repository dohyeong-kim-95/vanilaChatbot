"""Shared pieces of our interactive template views (shmoo, logview): data inlining and the capture protocol.

Capture: the app posts {type: "capture", nonce} to the frame; the page renders the SVG inside #plot to a PNG
(canvas, 2x) and posts {type: "capture", nonce, name, png: ArrayBuffer} back. The frame has no network and an opaque
origin; the app checks the source window, the nonce and the PNG signature (server/static/app.js frameCapture).
A page using CAPTURE_JS defines captureName() -> short file-name part for the current view.
"""
import json

# Owner's choice (DESIGN.md 4.2): Korean = 맑은 고딕, Latin/digits = Arial, as in Korean PowerPoint. Arial first: it
# has no Hangul, so Hangul falls through to Malgun Gothic. Both are licensed system fonts (not bundled); where they
# are missing (this Linux capture host) Liberation Sans (Arial-metric compatible) and Noto Sans CJK KR stand in.
FONT = ('Arial, "Liberation Sans", "Malgun Gothic", "맑은 고딕", "Apple SD Gothic Neo", "Noto Sans CJK KR", '
        'sans-serif')
MONO = 'Consolas, "Liberation Mono", ui-monospace, monospace'


def data_json(obj):
    """JSON safe to inline in <script type=application/json>: no `</script>` breakout."""
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")


CAPTURE_JS = r"""
function capturePng() {
  const svg = document.querySelector("#plot svg");
  const w = +svg.getAttribute("width"), h = +svg.getAttribute("height"), scale = 2;
  const src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(svg));
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = w * scale; c.height = h * scale;
      const g = c.getContext("2d"); g.scale(scale, scale); g.drawImage(img, 0, 0);
      c.toBlob((b) => b ? b.arrayBuffer().then(resolve) : reject(new Error("toBlob failed")), "image/png");
    };
    img.onerror = () => reject(new Error("svg load failed"));
    img.src = src;
  });
}
window.addEventListener("message", async (e) => {
  if (e.source !== parent || !e.data || e.data.type !== "capture") return;
  try {
    const png = await capturePng();
    parent.postMessage({ type: "capture", nonce: e.data.nonce, name: captureName(), png }, "*", [png]);
  } catch (err) {
    parent.postMessage({ type: "capture", nonce: e.data.nonce, error: String(err) }, "*");
  }
});
"""
