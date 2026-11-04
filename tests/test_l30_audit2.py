"""Loop 39: fixes from the second whole-repo audit. Each test fails on the code before its fix.
Docker (sandbox image) for the ingest/export ones and Chrome for the shmoo one; no LLM.
python tests/test_l30_audit2.py [test_name ...]"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="l30-"))
os.environ.setdefault("AGENT_ROOT", str(TMP / "data"))  # before server.app is imported
os.environ.setdefault("AGENT_AUTH", "header")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "tests"))
from agent import dashboard, report  # noqa: E402
from agent.loop import Context, run  # noqa: E402
from agent.userstore import MAX_OUTPUTS, UserStore, merge_settings  # noqa: E402
from test_l16_effort import DONE, Scripted  # noqa: E402

KEY = "sk-ant-api03-" + "x" * 40


def t_agent_relay_is_redacted():  # M1: a connected agent's text reaches the model through ctx.llm
    ctx = Context()
    c = Scripted([DONE, {"role": "assistant", "content": "summary"}])
    run("hi", ctx, client=c, model="m", log=lambda *_: None)
    assert ctx.llm([{"role": "user", "content": f"notes: {KEY} end"}]) == "summary"
    sent = json.dumps(c.sent[-1])
    assert KEY not in sent and "[REDACTED_SECRET]" in sent, sent


def t_big_output_keeps_the_rest():  # M2
    s = UserStore(TMP / "store")
    s.create_project("a", "p")
    ws, _ = s.checkout("a", "p", TMP / "run")
    (ws / "outputs" / "report.md").write_text("report")
    with open(ws / "outputs" / "errors.log", "wb") as f:
        f.truncate(MAX_OUTPUTS + 1)
    r = s.commit("a", "p", TMP / "run", "run 1")
    out = s.project_dir("a", "p") / "outputs"
    assert (out / "report.md").read_text() == "report" and not (out / "errors.log").exists()
    assert any("errors.log" in x and "limit" in x for x in r["skipped"]), r
    assert "50 MB" in s.instructions("a", "p")  # the model is told


def t_line_points_at_their_time():  # M3
    c = {"kind": "line", "x": "t", "y": "v", "series": "s", "columns": ["t", "v", "s"],
         "rows": [["00:00", 5, "A"], ["01:00", 9, "B"], ["02:00", 7, "A"]]}
    groups, _ = dashboard._series(c)
    assert groups == {"A": [(0, 5), (2, 7)], "B": [(1, 9)]}, groups


def t_settings_merge():  # S2 (lean): a higher layer wins, nothing else
    assert merge_settings({"effort": "high", "git_remote": "u"}, {}, {"effort": "low"}) == {"effort": "low",
                                                                                           "git_remote": "u"}


def t_report_lists():  # S5, S6
    good = {"title": "t", "period": "2026-09", "total": 250,
            "categories": [{"name": f"c{i}", "amount": 10} for i in range(25)]}
    for bad, msg in [(good, "at most 20 categories"), ({**good, "categories": good["categories"][:2], "total": 20,
                                                        "notes": "카드 사용 줄었음"}, "notes must be a list"),
                     ({**good, "categories": good["categories"][:2], "total": 20, "top": "x"}, "top must be a list")]:
        try:
            report.validate(bad)
            raise AssertionError(f"accepted {msg}")
        except ValueError as e:
            assert msg in str(e), e


def t_template_view_rewrite_message():  # N3: an html rewrite of a dashboard says which tool makes it
    from agent.artifacts import ArtifactStore
    from agent.loop import run_tool
    ctx = Context(artifacts=ArtifactStore(TMP / "arts"))
    ctx.artifacts.save("d", "dashboard", "t", "{}")
    out = run_tool(ctx, "artifact", json.dumps({"command": "rewrite", "id": "d", "type": "html", "content": "<p>"}))
    assert "views are made with show_dashboard" in out, out
    out = run_tool(ctx, "artifact", json.dumps({"command": "update", "id": "nope", "old_str": "a", "new_str": "b"}))
    assert "no artifact" in out, out  # review 39.1: not "unhashable type"


def t_mock_agents_not_deployed():  # S3
    from agent import delegate
    assert delegate.discover() == {}, "a mock agent is a tool in production"
    assert (ROOT / "agents" / "_runner.py").exists()
    assert sorted(delegate.discover(ROOT / "tests" / "fixtures" / "agents")) == ["text-summary", "wafer-yield"]


def t_sniff_big_file_estimates_lines():  # S4
    sys.path.insert(0, str(ROOT / "skills" / "log-triage" / "scripts"))
    import sniff
    small = TMP / "s.log"
    small.write_bytes(b"a\nb\nc")
    assert sniff.sniff(small)["lines"] == 3
    big = TMP / "b.log"
    with open(big, "wb") as f:
        line = b"2026-09-01 00:00:00 INFO x\n"
        f.write(line * (3 * sniff.MAX_BYTES // len(line)))
    real = 3 * sniff.MAX_BYTES // len(line)
    old, Path.open = Path.open, None  # nothing may read the file through to the end
    try:
        lines = sniff.sniff(big)["lines"]
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()) as o:
            sniff.main([str(big)])  # the skill's command (review 39.1: the print crashed on "~N")
        assert f"{lines} lines" in o.getvalue(), o.getvalue()
    finally:
        Path.open = old
    assert lines.startswith("~") and abs(int(lines[1:].replace(",", "")) - real) < real * 0.01, (lines, real)


def t_cancelled_build_leaves_nothing():  # S1: SIGTERM (cancel, timeout) and a SIGKILLed build's leftovers
    from stdf_gen import generate
    from test_l25_ingest import box
    d = TMP / "gen"
    generate(d, seed=4, lots=1, wafers=1, radius=4, tests=20)
    f = next(d.glob("*.stdf"))
    sb, cache = box("cancel")
    try:
        (sb.workspace / "uploads" / "t.log").write_bytes(b"A 1\n" * 60_000_000)  # 240 MB: a few seconds
        out = sb.exec("python /vtools/ingest.py text t uploads/t.log --pattern '^(?P<k>[A-Z]) (?P<v>\\d+)$' & "
                      "until [ -d cache/t.building ]; do sleep 0.05; done; sleep 0.3; "  # TERM before the handler proves nothing
                      "kill -TERM %1; wait %1; echo rc=$?", timeout=60)
        assert "rc=143" in out and not list(cache.glob("*.building")), (out, list(cache.iterdir()))
        (cache / "old.building").mkdir()  # a SIGKILLed build of another dataset
        (cache / "old.building" / "x.parquet").write_bytes(b"x" * 1000)
        shutil.copy(f, sb.workspace / "uploads" / f.name)
        out = sb.exec(f"python /vtools/ingest.py stdf w uploads/{f.name}", timeout=120)
        assert out.startswith("exit_code=0") and not (cache / "old.building").exists(), (out, list(cache.iterdir()))
    finally:
        sb.close()


def t_parts_limit_and_timeout():  # S8: per-user parts at once, and a stalled part gives its place back
    import httpx
    import server.app as app
    app.STORE.create_project("u8", "p")
    uid = app.STORE.begin_upload("u8", "p", "big.log", 6 * 2**20)["upload_id"]
    old = app.PART_SECONDS
    app.PART_SECONDS = 1

    async def go():
        release = asyncio.Event()

        async def stalled():
            yield b"x"
            await release.wait()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url="http://t",
                                     headers={"X-User": "u8"}, timeout=30) as c:
            url = f"/projects/p/files/uploads/{uid}?offset=0"
            slow = [asyncio.create_task(c.put(url, content=stalled())) for _ in range(app.PARTS_PER_USER)]
            await asyncio.sleep(0.3)
            assert app._user_parts["u8"] == app.PARTS_PER_USER
            assert (await c.put(url, content=b"y")).status_code == 503
            codes = [(await t).status_code for t in slow]
            release.set()
            assert codes == [408] * app.PARTS_PER_USER and app._user_parts["u8"] == 0, (codes, app._user_parts)
            r = await c.put(url, content=b"z" * 10)
            assert r.status_code == 200, r.text
    try:
        asyncio.run(go())
    finally:
        app.PART_SECONDS = old


def t_shmoo_select_shows_what_is_drawn():  # S7
    from playwright.sync_api import sync_playwright
    from agent import shmoo
    csv = TMP / "sh.csv"
    rows = ["DEV,VDD,FREQ,TEMP,CORNER,RES"]
    for t, k in (("25", "A"), ("85", "B")):  # only (25, A) and (85, B) were measured
        rows += [f"d1,{v},{f},{t},{k},{'P' if f <= v * 100 else 'F'}" for v in (10, 11) for f in (900, 1100)]
    rows.append("d1,10,900,125,A,N/A")  # a value with no pass/fail cell at all (review 39.1)
    csv.write_text("\n".join(rows) + "\n")
    d, _, _ = shmoo.build(csv, "FREQ", "VDD", "RES", ["DEV"], filters=["TEMP", "CORNER"])
    html = shmoo.render(d)
    with sync_playwright() as pw:
        br = pw.chromium.launch(channel="chrome", headless=True)
        page = br.new_page()
        page.set_content(html)
        page.wait_for_selector("#plot svg")
        page.locator("#controls select").first.select_option("85")
        sel = page.evaluate("() => [...document.querySelectorAll('#controls select')].slice(0, 2).map(s => s.value)")
        drawn = page.evaluate("() => document.querySelectorAll('#plot svg text')[1].textContent")
        assert sel == ["85", "B"] and "TEMP=85" in drawn and "CORNER=B" in drawn, (sel, drawn)
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        if page.locator("#controls select option[value='125']").count():
            page.locator("#controls select").first.select_option("125")
            assert page.locator("#controls select").first.input_value() == "85" and not errs, errs
        br.close()


if __name__ == "__main__":
    only, failed = sys.argv[1:], []
    for name, fn in list(globals().items()):
        if name.startswith("t_") and (not only or name in only):
            try:
                t = time.time(); fn(); print(f"PASS {name} ({time.time() - t:.0f}s)")
            except Exception as e:
                failed.append(name)
                print("FAIL", name, repr(e)[:1500])
    sys.exit(1 if failed else 0)
