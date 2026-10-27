"""Artifact renderer (Loop 14): serves artifact content on its OWN origin, the way Claude serves artifacts from a
separate domain. Run it as a second process:  uvicorn server.render:app --port 8201

Artifact content is untrusted (the model wrote it, maybe steered by an uploaded file). Layers:
1. Separate origin (in production a separate registrable domain): no cookies/storage of the app.
2. The app embeds it in <iframe sandbox="allow-scripts"> (no allow-same-origin -> opaque origin).
3. CSP here: no network (connect-src 'none'), scripts/styles only from cdnjs, images only data:/blob:,
   frame-ancestors = the app only. The app's own CSP (frame-src = this origin) blocks the iframe from
   navigating itself to another site, which would otherwise be a way out.
4. The URL carries a short-lived HMAC token issued by the app for one (user, project, artifact, version);
   this server has no other auth and serves nothing else.
"""
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from agent import report
from agent.artifacts import ArtifactStore
from agent.userstore import UserStore

ROOT = Path(os.environ.get("AGENT_ROOT", "/tmp/agent-sessions"))
APP_ORIGIN = os.environ.get("AGENT_APP_ORIGIN", "http://127.0.0.1:8200")
RENDER_ORIGIN = os.environ.get("AGENT_RENDER_ORIGIN", "http://localhost:8201")
TTL = 600
CDN = "https://cdnjs.cloudflare.com"
CSP = ("default-src 'none'; "
       f"script-src 'unsafe-inline' 'unsafe-eval' {CDN}; style-src 'unsafe-inline' {CDN}; font-src {CDN} data:; "
       "img-src data: blob:; media-src data: blob:; worker-src blob:; connect-src 'none'; frame-src 'none'; "
       f"form-action 'none'; base-uri 'none'; frame-ancestors {APP_ORIGIN}")


def _key():
    if k := os.environ.get("AGENT_RENDER_SECRET"):
        return k.encode()
    f = ROOT / "render.key"  # shared by the app and this process through AGENT_ROOT
    try:
        with open(f, "x") as fh:
            fh.write(secrets.token_hex(32))
        f.chmod(0o600)
    except FileExistsError:
        pass
    return f.read_text().strip().encode()


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign(user, project, aid, version, ttl=TTL):
    body = _b64(json.dumps({"u": user, "p": project, "a": aid, "v": version, "exp": int(time.time()) + ttl}).encode())
    return body + "." + _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())


def verify(token):
    body, _, sig = token.partition(".")
    good = _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())
    if not sig or not hmac.compare_digest(sig, good):
        raise PermissionError("bad signature")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    if claims["exp"] < time.time():
        raise PermissionError("expired")
    return claims


def _data(obj):
    """JSON safe to inline in <script type=application/json>: no `</script>` breakout."""
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")


# CSP has no directive for WebRTC: a peer connection to a STUN/TURN server sends packets anywhere (measured in
# Loop 14). Remove the constructors before any artifact code runs. Fresh copies from a child frame are not
# available: frame-src 'none' blocks about:blank/srcdoc children from scripting (also measured).
PRELUDE = ("<script>for (const k of ['RTCPeerConnection', 'webkitRTCPeerConnection', 'RTCIceTransport', "
           "'RTCDtlsTransport', 'RTCSctpTransport']) { try { Object.defineProperty(window, k, "
           "{value: undefined, writable: false, configurable: false}); } catch (e) {} }</script>")
DOCTYPE = re.compile(r"^\s*<!doctype[^>]*>", re.I)


def page(a):
    t, c = a["type"], a["content"]
    if t == "html":  # keep a leading doctype first (else quirks mode), the prelude right after it
        m = DOCTYPE.match(c)
        return c[:m.end()] + PRELUDE + c[m.end():] if m else PRELUDE + c
    head = f'<!doctype html>{PRELUDE}<meta charset="utf-8"><title>{html.escape(a["title"])}</title>' \
           '<style>body{font:15px/1.6 system-ui,sans-serif;margin:24px;max-width:860px;color:#1f2328}' \
           'pre{overflow:auto}table{border-collapse:collapse}td,th{border:1px solid #d0d7de;padding:4px 8px}</style>'
    if t == "report":  # our template, no scripts; data is escaped there
        return report.render(json.loads(c))
    if t == "svg":
        return head + '<style>body{margin:0;max-width:none}svg{max-width:100%;height:auto}</style>' + c
    if t == "code":
        return head + f'<pre><code>{html.escape(c)}</code></pre>'
    src = {"markdown": f'{CDN}/ajax/libs/marked/12.0.2/marked.min.js',
           "mermaid": f'{CDN}/ajax/libs/mermaid/10.9.1/mermaid.min.js'}[t]
    run = {"markdown": "document.getElementById('out').innerHTML = marked.parse(SRC);",
           "mermaid": "mermaid.initialize({startOnLoad:false}); "
                      "mermaid.render('m', SRC).then(r => document.getElementById('out').innerHTML = r.svg);"}[t]
    return head + f'<div id="out"></div><script id="src" type="application/json">{_data(c)}</script>' \
        f'<script src="{src}"></script><script>const SRC = JSON.parse(document.getElementById("src").textContent);' \
        f'{run}</script>'


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/a/{token}")
def render(token: str):
    try:
        c = verify(token)
        a = ArtifactStore(UserStore(ROOT).project_dir(c["u"], c["p"]) / "artifacts").get(c["a"], c["v"])
    except (PermissionError, ValueError, KeyError):
        raise HTTPException(404)  # one answer for bad, expired and missing
    return Response(page(a), media_type="text/html; charset=utf-8", headers={
        "Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store", "Cross-Origin-Opener-Policy": "same-origin"})
