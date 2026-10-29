"""Loop 18: interactive text-log view (levels over time, masked patterns, no raw lines) + capture.
python tests/test_l13_logview.py [test_name ...]   (deterministic; real Chrome via Playwright)
"""
import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "eval" / "logview"))
import test_l9_artifacts as l9  # noqa: E402
import gen  # noqa: E402
from agent import logview  # noqa: E402
from agent.artifacts import ArtifactStore  # noqa: E402
from agent.loop import Context, run_tool  # noqa: E402
from agent.userstore import UserStore  # noqa: E402

TMP = l9.TMP / "logview"
TMP.mkdir()
LOG = TMP / "order.log"
TRUTH = gen.truth(gen.write(LOG))


def t_core_matches_truth():
    a, summary = logview.build(LOG)
    assert a["levels"] == {k: TRUTH["levels"][k] for k in ("ERROR", "WARN", "INFO")}, a["levels"]
    assert a["lines"] == TRUTH["lines"] and a["continuation_lines"] == 2 * TRUTH["npe_count"]
    pats = {p["pattern"]: p for p in a["patterns"]}
    to = pats["[order-svc] timeout after <n>ms calling payment-api at <ip>"]
    npe = pats["[order-svc] NullPointerException in OrderService.validate line <n>"]
    assert to["count"] == TRUTH["timeout_count"] and to["level"] == "ERROR"
    assert to["first"].startswith(str(TRUTH["timeout_first"])[:19]) and to["last"][:19] == str(TRUTH["timeout_last"])[:19]
    assert npe["count"] == TRUTH["npe_count"] and npe["first"][:19] == str(TRUTH["npe_first"])
    assert [p["level"] for p in a["patterns"][:2]] == ["ERROR", "ERROR"], "errors first"
    assert len(a["patterns"]) == 8, [p["pattern"] for p in a["patterns"]]  # masking groups all variable lines
    assert sum(sum(b.values()) for b in a["buckets"]) == a["lines"]
    assert "most errors: 10-29 03:" in summary, summary
    # other formats: access log (level from status), syslog, ERROR before the time, epoch
    other = TMP / "mixed.log"
    other.write_text('10.0.0.1 - - [29/Oct/2026:10:00:01 +0900] "GET /a/1 HTTP/1.1" 200 12\n'
                     '10.0.0.2 - - [29/Oct/2026:10:00:02 +0900] "GET /a/2 HTTP/1.1" 503 0\n'
                     '10.0.0.3 - - [29/Oct/2026:10:00:03 +0900] "POST /b HTTP/1.1" 404 9\n'
                     "Oct 29 10:00:04 host sshd[123]: Failed password for root from 1.2.3.4 port 22\n"
                     "E 2026-10-29T10:00:05.123Z ERROR worker crashed\n"
                     "1793293205 WARN slow query 2300ms\n")
    a, _ = logview.build(other)
    assert a["levels"] == {"ERROR": 2, "WARN": 2, "INFO": 1, "-": 1}, a["levels"]
    print("    levels:", a["levels"], "| patterns:", [p["pattern"][:40] for p in a["patterns"]])


def t_tool():
    ctx = Context(sandbox=l9.stub_sandbox(TMP), artifacts=ArtifactStore(TMP / "arts"))
    out = run_tool(ctx, "show_logview", json.dumps({"id": "o", "file": "order.log"}))
    assert out.startswith("logview o saved as version 1") and "timeout after" in out, out
    assert "onerror=alert(" in out  # the pattern is text in the summary (masked numbers), never markup in the view
    for bad in ({"id": "x", "file": "../../etc/passwd"}, {"id": "x", "file": "nope.log"}):
        assert run_tool(ctx, "show_logview", json.dumps(bad)).startswith("ERROR"), bad
    empty = TMP / "empty.log"
    empty.write_text("no timestamps here\nat all\n")
    assert "no timestamped lines" in run_tool(ctx, "show_logview", json.dumps({"id": "e", "file": "empty.log"}))
    d = json.loads(ctx.artifacts.get("o")["content"])
    raw = LOG.read_text()
    assert "order 191862 created" in raw and "191862" not in ctx.artifacts.get("o")["content"], "no raw values stored"
    print(f"    artifact {len(ctx.artifacts.get('o')['content']):,} chars for {len(raw):,} chars of log; "
          f"{len(d['patterns'])} patterns, {len(d['buckets'])} buckets")


def t_browser_interact_capture():
    ctx = Context(sandbox=l9.stub_sandbox(TMP),
                  artifacts=ArtifactStore(UserStore(l9.DATA).create_project("alice", "logs") / "artifacts"))
    assert "version 1" in run_tool(ctx, "show_logview", json.dumps({"id": "order", "file": "order.log",
                                                                     "title": "order-svc </script><b>x</b>"}))
    hits, srv, stop = l9.sinks()
    ps = l9.servers()
    pw, b = l9.browser()
    try:
        page = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(l9.APP + "/")
        page.evaluate("() => localStorage.setItem('user', 'alice')")
        page.goto(l9.APP + "/")
        page.wait_for_function("() => document.querySelector('#project option')")
        page.select_option("#project", "logs")
        page.wait_for_selector("#artifacts li")
        page.locator("#artifacts li").first.click()
        frame = None
        for _ in range(50):  # page.frames does not pump events: wait with Playwright, not time.sleep
            frame = next((f for f in page.frames if f.url.startswith(l9.RENDER)), None)
            if frame and frame.query_selector("#plot svg"):
                break
            page.wait_for_timeout(200)
        else:
            raise AssertionError("logview did not render")
        # default: INFO off (errors and warnings visible); chips show counts
        chips = frame.locator(".chip").all_inner_texts()
        assert [c.split("\n")[0] for c in chips] == ["ERROR", "WARN", "INFO"], chips
        assert "off" in frame.locator(".chip").nth(2).get_attribute("class")
        rows0 = frame.locator("#patterns tr.row").count()
        frame.locator("#patterns tr.row", has_text="timeout after").click()
        sub = frame.evaluate("() => document.querySelectorAll('#plot svg text')[1].textContent")
        assert sub.startswith("패턴: [order-svc] timeout after"), sub
        bars = frame.evaluate("() => [...document.querySelectorAll('#plot svg rect')].filter(r => r.getAttribute('fill') === '#B42318' && r.getAttribute('pointer-events') === 'none').length")  # bars, not the legend
        assert bars == 3, f"the 15-minute incident spans 3 five-minute bars, got {bars}"
        png = page.evaluate("""async () => { const r = await frameCapture(document.querySelector("#frame-wrap iframe"));
                                             return [r.name, Array.from(new Uint8Array(await r.blob.arrayBuffer()))]; }""")
        assert png[0].startswith("log_order_pattern0_L_") and bytes(png[1][:8]) == b"\x89PNG\r\n\x1a\n", png[0]
        assert (int.from_bytes(bytes(png[1][16:20]), "big"), bytes(png[1][37:41])) == (2360, b"pHYs")
        (TMP / "capture-pattern.png").write_bytes(bytes(png[1]))
        # bucket click: table re-sorts by that bucket's counts
        frame.locator("#reset").click()
        start = dt.datetime.fromisoformat(json.loads(ctx.artifacts.get("order")["content"])["start"])
        idx = int((dt.datetime(2026, 10, 29, 3, 15) - start).total_seconds() // 300)
        frame.locator("#plot rect.b").nth(idx).click()
        first = frame.locator("#patterns tr.row").first.inner_text()
        assert "timeout after" in first, first
        assert "03:15" in frame.inner_text("#bucket")
        frame.locator("#reset").click()
        frame.locator(".chip").nth(2).click()  # INFO on
        assert frame.locator("#patterns tr.row").count() > rows0
        page.screenshot(path=str(TMP / "ui-logview.png"))
        assert frame.query_selector("img") is None and frame.query_selector("b") is None
        assert "<img src=x onerror=alert(<n>)>" in frame.inner_text("#patterns")
        time.sleep(1)
        assert not [h for h in hits if not h.startswith("udp/")], hits
        assert not errs, errs
        print(f"    capture -> {TMP / 'capture-pattern.png'}\n    ui -> {TMP / 'ui-logview.png'}")
    finally:
        b.close(); pw.stop(); l9.kill(ps); srv.shutdown(); stop.set()


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
                print(f"FAIL {name} ({time.time() - t:.0f}s): {type(e).__name__}: {str(e)[:400]}")
    sys.exit(1 if failed else 0)
