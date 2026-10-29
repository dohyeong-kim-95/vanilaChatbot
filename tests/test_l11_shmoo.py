"""Loop 16: interactive shmoo view from a test log + capture of what the user set up.
Deterministic (no LLM): python tests/test_l11_shmoo.py [test_name ...]
LLM: t_llm_* need the gateway on :8100 (K=1 by default).
"""
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "eval" / "shmoo"))
import test_l9_artifacts as l9  # noqa: E402  (sets AGENT_ROOT etc. for the app/renderer it starts)
import gen  # noqa: E402
from agent import shmoo, skills  # noqa: E402
from agent.artifacts import ArtifactStore  # noqa: E402
from agent.loop import SYSTEM, Context, run, run_tool  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402
from agent.userstore import UserStore  # noqa: E402

K = int(os.environ.get("K", 1))
TMP = l9.TMP / "shmoo"
TMP.mkdir()
LOG = TMP / "uploads" / "shmoo_L2610A.csv"
LOG.parent.mkdir()
ROWS = gen.write(LOG)
TRUTH = gen.truth(ROWS)
MAP = dict(x="FREQ_MHZ", y="VDD_V", result="RESULT", device=["WAFER", "DIE_X", "DIE_Y"], filters=["TEMP_C"])


def label(dev_id):  # "W1-X3Y2" -> "1-3-2" (device columns joined)
    w, rest = dev_id[1:].split("-X")
    x, y = rest.split("Y")
    return f"{w}-{x}-{y}"


def t_core_finds_injected_defects():
    core = shmoo.core
    header, rows = core.read_table(LOG)
    assert core.guess(header, rows) == MAP, core.guess(header, rows)
    g = core.load(LOG, **MAP)
    kinds = {(f["device"], f["kind"]) for f in core.analyze(g) if f["kind"] != "low"}
    expected = {(label(d), k) for d, k in TRUTH["defects"].items()}
    assert kinds == expected, (kinds, expected)
    assert int(core.all_pass_max_x(g, ("85",), "1.1")) == TRUTH["all_pass_fmax_85_110"]
    assert int(core.all_pass_max_x(g, ("25",), "1.2")) == TRUTH["all_pass_fmax_25_120"]
    assert round(core.pass_rate(g, combo=("85",)), 1) == TRUTH["pass_rate_85"]
    print("    findings:", sorted(kinds))


def t_tool_and_errors():
    ctx = Context(sandbox=l9.stub_sandbox(TMP), artifacts=ArtifactStore(TMP / "arts"))
    call = lambda **a: run_tool(ctx, "show_shmoo", json.dumps(a, ensure_ascii=False))  # noqa: E731
    out = call(id="lot", file="uploads/shmoo_L2610A.csv", **MAP)
    assert out.startswith("shmoo lot saved as version 1") and "hole" in out and "ceiling" in out, out
    d = json.loads(ctx.artifacts.get("lot")["content"])
    assert len(d["devices"]) == 24 and d["combos"] == [["25"], ["85"]] and len(d["grids"][0][0]) == 17 * 16
    errors = {
        "bad column": call(id="x", file="uploads/shmoo_L2610A.csv", **{**MAP, "x": "NOPE"}),
        "escape": call(id="x", file="../../etc/passwd", **MAP),
        "edit via artifact": run_tool(ctx, "artifact", json.dumps({"command": "update", "id": "lot", "old_str": "a", "new_str": "b"})),
    }
    for k, v in errors.items():
        assert v.startswith("ERROR"), (k, v)
    # result values the default pass/fail sets don't know -> skipped with a warning
    odd = TMP / "odd.csv"
    odd.write_text("DUT,V,F,R\n1,1.0,100,GOOD\n1,1.0,200,BAD\n1,1.1,100,GOOD\n1,1.1,200,GOOD\n")
    out = call(id="odd", file="odd.csv", x="F", y="V", result="R", device=["DUT"])
    assert "WARNING: result values neither pass nor fail were skipped" in out, out
    out = call(id="odd", file="odd.csv", x="F", y="V", result="R", device=["DUT"], pass_values=["GOOD"])
    assert "version 2" in out and "WARNING" not in out, out
    print("    errors:", {k: v[7:70] for k, v in errors.items()})


EVIL_DEV = '<img src=http://127.0.0.1:8399/x onerror="fetch(\'http://127.0.0.1:8399/js\')">'


def t_browser_interact_capture():
    """Real UI + renderer + Chrome: pick 85C and the 'hole' finding inside the frame, then capture what is shown.
    A device name from the log carrying markup must stay text (sink must see nothing)."""
    evil = TMP / "uploads" / "evil.csv"
    with evil.open("w", newline="") as fh:
        fh.write("# title </script><script>alert(1)</script>\n")
        w = csv.writer(fh)
        w.writerow(["DEV", "VDD", "FREQ", "RES"])
        w.writerows([d, v, f, "P" if f <= v * 100 else "F"]
                    for d in ("ok", EVIL_DEV) for v in (10, 11, 12) for f in (900, 1000, 1100, 1200))
    ctx = Context(sandbox=l9.stub_sandbox(TMP),
                  artifacts=ArtifactStore(UserStore(l9.DATA).create_project("alice", "shmoo") / "artifacts"))
    assert "version 1" in run_tool(ctx, "show_shmoo", json.dumps({"id": "lot", "file": "uploads/shmoo_L2610A.csv", **MAP}))
    assert "version 1" in run_tool(ctx, "show_shmoo", json.dumps(
        {"id": "evil", "file": "uploads/evil.csv", "x": "FREQ", "y": "VDD", "result": "RES", "device": ["DEV"],
         "title": "</script><b>x</b>"}))
    hits, srv, stop = l9.sinks()
    ps = l9.servers()
    pw, b = l9.browser()
    try:
        page = b.new_page(viewport={"width": 1500, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(l9.APP + "/")
        page.evaluate("""() => { localStorage.setItem("user", "alice"); }""")
        page.goto(l9.APP + "/")
        page.wait_for_function("() => document.querySelector('#project option')")
        page.select_option("#project", "shmoo")
        page.wait_for_selector("#artifacts li")
        page.locator("#artifacts li", has_text="Shmoo").first.click()
        page.wait_for_selector("#frame-wrap iframe")
        frame = None
        for _ in range(50):
            frame = next((f for f in page.frames if f.url.startswith(l9.RENDER)), None)
            if frame and frame.query_selector("#plot svg"):
                break
            time.sleep(0.2)
        frame.locator("#controls select").first.select_option("85")
        frame.locator("#findings li", has_text="구멍").first.click()
        frame.locator("#plot rect.c").nth(17 * 12 + 4).click()  # a cell in the hole region (row 1.24 V, col 2000)
        sub = frame.evaluate("() => document.querySelectorAll('#plot svg text')[1].textContent")
        assert "1-3-2" in sub and "TEMP_C=85" in sub, sub
        detail = frame.inner_text("#detail")
        n = page.evaluate("""async () => { const f = document.querySelector("#frame-wrap iframe");
                                           const r = await frameCapture(f); return [r.blob.size, r.name]; }""")
        assert n[0] > 5000 and n[1] == "1-3-2_85", n
        png = page.evaluate("""async () => { const r = await frameCapture(document.querySelector("#frame-wrap iframe"));
                                             return Array.from(new Uint8Array(await r.blob.arrayBuffer())); }""")
        (TMP / "capture-hole.png").write_bytes(bytes(png))
        page.screenshot(path=str(TMP / "ui-shmoo.png"))
        # a frame that is not the shmoo view (or a forged reply) is not accepted: only frame.contentWindow + nonce
        forged = page.evaluate("""() => new Promise(res => { const f = document.createElement("iframe"); document.body.append(f);
            frameCapture(f).then(() => res("accepted"), e => res("rejected: " + e.message)); })""")
        assert forged.startswith("rejected"), forged
        # the evil log
        page.locator("#artifacts li", has_text="</script>").first.click()
        for _ in range(50):
            ef = next((f for f in page.frames if f.url.startswith(l9.RENDER) and not f.is_detached()), None)
            try:  # the old frame may detach between the lookup and the query
                if ef and ef.query_selector("#devices tr") and "장치 2개" in ef.inner_text("#sub"):
                    break
            except Exception:
                pass
            time.sleep(0.2)
        else:
            page.screenshot(path=str(TMP / "evil-debug.png"))
            raise AssertionError(f"evil view not shown; frames {[f.url[:60] for f in page.frames]}; "
                                 f"list {page.inner_text('#artifacts')!r}")
        assert ef.query_selector("img") is None and ef.query_selector("b") is None
        assert EVIL_DEV in ef.inner_text("#devices"), "device name shown as text"
        time.sleep(1)
        assert not [h for h in hits if not h.startswith("udp/")], hits
        assert not errs, errs
        print(f"    detail: {detail!r}\n    capture {n[0]} bytes -> {TMP / 'capture-hole.png'}\n    ui -> {TMP / 'ui-shmoo.png'}")
    finally:
        b.close(); pw.stop(); l9.kill(ps); srv.shutdown(); stop.set()


# ---------------------------------------------------------------- LLM (gateway on :8100)

def t_llm_show_and_find():
    """'Show me the shmoo and tell me which devices look abnormal': show_shmoo with the right mapping, and the
    three injected defects named (device + kind)."""
    rows = []
    for i in range(K):
        ws = TMP / f"llm-{i}"
        (ws / "uploads").mkdir(parents=True)
        (ws / "uploads" / LOG.name).write_bytes(LOG.read_bytes())
        subprocess.run(["chown", "-R", "1000:1000", str(ws)])
        sk = skills.discover()
        sb = Sandbox(ws, readonly=("uploads",), mounts={skills.DIR: skills.MOUNT})
        ctx = Context(sandbox=sb, skills=sk, artifacts=ArtifactStore(ws.parent / f"arts-{i}"))
        m, calls, t0 = l9.llm(), [], time.time()
        try:
            ans, _ = run(f"업로드한 테스트 로그는 /workspace/uploads/{LOG.name} 예요. shmoo plot 보여주고, 이상한 칩이 "
                         "있으면 어떤 칩이 어떤 조건에서 왜 이상한지 알려줘.", ctx,
                         tool_names=["bash", "read_file", "write_file", "edit_file", "use_skill", "show_shmoo", "artifact"],
                         client=m, max_steps=20, history=[{"role": "system", "content": SYSTEM + "\n\n" + skills.prompt(sk)}],
                         log=lambda l: calls.append(l.split("(")[0].split("] ")[1]))
        finally:
            sb.close()
        arts = [a for a in ctx.artifacts.list() if a["type"] == "shmoo"]
        d = json.loads(ctx.artifacts.get(arts[0]["id"])["content"]) if arts else {}
        mapping_ok = bool(d) and d["x"]["name"] == "FREQ_MHZ" and d["y"]["name"] == "VDD_V" and len(d["devices"]) == 24
        named = {dev: label(dev) in (ans or "") or dev in (ans or "") for dev in TRUTH["defects"]}
        rows.append({"shmoo": bool(arts), "mapping_ok": mapping_ok, "named": named, "tools": calls,
                     "sec": round(time.time() - t0), "out_tokens": m.completion, "answer": (ans or "")[:400]})
        print("   ", json.dumps(rows[-1], ensure_ascii=False))
    ok = sum(r["mapping_ok"] and all(r["named"].values()) for r in rows)
    print(f"    shmoo shown with right mapping + all 3 defects named: {ok}/{K}")
    assert ok == K


if __name__ == "__main__":
    only = sys.argv[1:]
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("t_") and (only and name in only or not only and not name.startswith("t_llm")):
            t = time.time()
            try:
                fn()
                print(f"PASS {name} ({time.time() - t:.0f}s)")
            except Exception as e:
                failed += 1
                print(f"FAIL {name} ({time.time() - t:.0f}s): {type(e).__name__}: {str(e)[:400]}")
    sys.exit(1 if failed else 0)
