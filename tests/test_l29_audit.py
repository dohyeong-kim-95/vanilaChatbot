"""Loop 38: fixes from the whole-repo audit (ponytail-audit). Each test fails on the code before its fix.
Docker (sandbox image) for the sandbox ones and Chrome for the UI one; no LLM.
python tests/test_l29_audit.py [test_name ...]"""
import json
import os
import shutil
import signal
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="l29-"))
os.environ.setdefault("AGENT_ROOT", str(TMP / "data"))  # before server.app is imported
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "tests"))
from agent import report  # noqa: E402
from agent.loop import Context, _error_streak, run_tool  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402
from agent.userstore import UserStore  # noqa: E402


def t_caddy_caps_one_request():  # M1: deploy/vanila.caddy (the 413 itself: tests/test_l19_deploy.py)
    s = (ROOT / "deploy" / "vanila.caddy").read_text()
    app_site = s.split("https://@RENDER_HOST@")[0]
    assert "request_body" in app_site and "max_size 20MB" in app_site, app_site
    from agent.userstore import MAX_UPLOAD, PART_MAX
    assert PART_MAX + 64 * 1024 < 20 * 10**6 and MAX_UPLOAD < PART_MAX  # GB files still go up, in parts


def t_git_remote():  # M2
    from server.app import git_remote
    data = Path(os.environ["AGENT_ROOT"]).resolve()
    assert git_remote("https://github.com/a/b.git") == "https://github.com/a/b.git"
    from fastapi import HTTPException
    from server.app import checked  # review 38.1: rejected when saved, not dropped at push time
    assert checked({"git_remote": "https://github.com/a/b.git"}) and checked({"x": 1}) == {"x": 1}
    try:
        checked({"git_remote": "git@github.com:a/b.git"})
        raise AssertionError("accepted")
    except HTTPException as e:
        assert e.status_code == 400
    assert git_remote(str(TMP / "elsewhere.git")) == str(TMP / "elsewhere.git")  # a local test origin
    for bad in [str(data / "users/bob/projects/q/repo.git"), str(data) + "/x/../users/bob/repo.git", "--upload-pack=x",
                "-c core.x=y", "ssh://h/r", "file:///etc", "https://a b", "", None, 5, "relative/path"]:
        assert git_remote(bad) is None, bad


def t_timeout_kills_the_command():  # M3
    ws = TMP / "to"
    ws.mkdir()
    sb = Sandbox(ws)
    try:
        for cmd in ["sleep 41", "exec python -c 'import time; time.sleep(42)'", "(sleep 43; echo x) | cat",
                    "sh -c 'sleep 44 & wait'"]:
            t = time.time()
            out = sb.exec(cmd, timeout=2)
            assert out.startswith("exit_code=124") and "timeout after 2s" in out and time.time() - t < 10, (cmd, out)
        time.sleep(1)
        left = sb.exec("for p in /proc/[0-9]*; do tr '\\0' ' ' < $p/cmdline; echo; done 2>/dev/null"
                       " | grep -E 'sleep 4[1-4]|time.sleep' | grep -v grep || true")
        assert left.strip() == "exit_code=0", left
        assert sb.exec("echo ok; exit 3", timeout=5) == "exit_code=3\nok"  # other exits as they are
        assert sb.exec("timeout 1 sleep 5", timeout=20).startswith("exit_code=124\n")  # the command's own timeout
        assert "[timeout" not in sb.exec("timeout 1 sleep 5", timeout=20)
        t = time.time()  # review 38.1: timeout(1) reads 0 as no limit
        out = run_tool(Context(sandbox=sb), "bash", json.dumps({"command": "sleep 45", "timeout": 0}))
        assert "timeout after 1s" in out and time.time() - t < 10, out
    finally:
        sb.close()


def t_broken_partial_upload():  # M4
    s = UserStore(TMP / "store")
    s.create_project("a", "p")
    s.create_project("b", "p")
    big = 6 * 2**20
    s.begin_upload("a", "p", "x.log", big)
    part = s.project_dir("a", "p") / "uploads" / "partial"
    meta = next(part.glob("*.json"))
    meta.write_text("")  # the disk filled up while writing it
    b = s.begin_upload("a", "p", "y.log", big)  # others go on
    assert b["upload_id"] and not meta.exists() and not meta.with_suffix(".data").exists()
    s.sweep_uploads()  # what the server runs at start
    assert s.upload_status("a", "p", b["upload_id"])["received"] == 0
    assert not list(part.glob("*.tmp"))


def t_entry_limit_before_the_upload():  # N1 (with the lean merge of the manifest checks)
    import agent.userstore as us
    s = UserStore(TMP / "store-n")
    s.create_project("a", "p")
    old, us.MAX_UPLOAD_ENTRIES = us.MAX_UPLOAD_ENTRIES, 3
    try:
        s.upload("a", "p", "d/x.txt", b"x")  # d and d/x.txt
        try:
            s.begin_upload("a", "p", "e/big.log", 6 * 2**20)  # e and e/big.log: 4 entries
            raise AssertionError("accepted")
        except us.TooLarge as e:
            assert "files and folders" in str(e), e
        s.begin_upload("a", "p", "d/big.log", 6 * 2**20)  # 3 entries: fine
    finally:
        us.MAX_UPLOAD_ENTRIES = old


def t_report_rejects_non_objects():  # M5
    good = {"title": "t", "period": "2026-09", "total": 3, "categories": [{"name": "a", "amount": 3}]}
    report.validate(good)
    for bad in [{**good, "categories": ["식비", "교통"]}, {**good, "top": ["x"]}]:
        try:
            report.validate(bad)
            raise AssertionError(f"accepted {bad}")
        except ValueError as e:
            assert "must be an object" in str(e), e


def t_events_index():  # S1
    import server.app  # noqa: F401  (creates the tables)
    db = sqlite3.connect(Path(os.environ["AGENT_ROOT"]) / "agent.db")
    plan = " ".join(r[3] for r in db.execute("EXPLAIN QUERY PLAN SELECT seq FROM events WHERE run_id='r' AND seq>0"))
    assert "events_run" in plan, plan


def t_error_streak_needs_an_error():  # S2
    tool = lambda c: {"role": "tool", "content": c}  # noqa: E731
    assert _error_streak([tool("exit_code=1\n"), tool("exit_code=1\n")]) == 0  # grep -c found nothing, twice
    assert _error_streak([tool("exit_code=1\n0")]) == 0
    assert _error_streak([tool("exit_code=2\n[stderr]\ngrep: x: No such file"), tool("ERROR: bad")]) == 2
    assert _error_streak([tool("exit_code=0\nok"), tool("exit_code=127\n[stderr]\nnope: command not found")]) == 1
    assert _error_streak([tool("exit_code=124\n[timeout after 60s]")] * 2) == 2  # review 38.1


def t_big_log_points_to_ingest():  # S3
    class Box:
        def copy_out(self, rel, dest, limit):
            raise ValueError(f"{rel} is larger than {limit:,} bytes")
    out = run_tool(Context(sandbox=Box()), "show_logview", json.dumps({"id": "v", "file": "uploads/big.log"}))
    assert "ingest_log" in out and "larger than" in out, out
    assert "ingest_log" in (ROOT / "skills" / "log-triage" / "SKILL.md").read_text()


def t_ingest_fixes():  # S4, S5, S6
    from stdf_gen import generate
    from test_l25_ingest import box, q
    d = TMP / "gen"
    generate(d, seed=4, lots=1, wafers=1, radius=4, tests=20)
    f = next(d.glob("*.stdf"))
    sb, cache = box("ing")
    ws = sb.workspace
    try:
        for sub in ("a", "b"):  # S4: the same name in two folders
            (ws / "uploads" / sub).mkdir()
            shutil.copy(f, ws / "uploads" / sub / f.name)
        out = sb.exec(f"python /vtools/ingest.py stdf two uploads/a/{f.name} uploads/b/{f.name}", timeout=120)
        assert "exit_code=1" in out and "two files named" in out and not (cache / "two").exists(), out
        out = sb.exec(f"python /vtools/ingest.py stdf one uploads/a/{f.name} uploads/a/{f.name}", timeout=120)
        assert "two files named" in out, out
        # S5: unmatched lines are not rows
        (ws / "uploads" / "t.log").write_text("A 1\nnoise\nB 2\n\nC 3\n")
        out = sb.exec("python /vtools/ingest.py text t uploads/t.log --pattern '^(?P<k>[A-Z]) (?P<v>\\d+)$'", timeout=120)
        assert '"rows": 3' in out and '"unmatched_lines": 2' in out, out
        assert q(sb, "SELECT count(*), count(k) FROM t.rows") == [["3", "3"]]
        # S6: a failed build leaves nothing in the cache
        # a lookahead: Python's re takes it, the Rust regex of Polars fails in the middle of the build
        out = sb.exec("python /vtools/ingest.py text bad uploads/t.log --pattern '(?P<k>[A-Z])(?= )'", timeout=120)
        assert not out.startswith("exit_code=0"), out
        assert not [p.name for p in cache.iterdir() if p.name.startswith("bad")], list(cache.iterdir())
    finally:
        sb.close()


def t_ui_new_conversation():  # S7, S8
    from playwright.sync_api import sync_playwright
    from test_l22_layout import APP, PW, servers
    procs = servers()
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(channel="chrome", headless=True)
            page = br.new_page(viewport={"width": 1400, "height": 800})
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(APP + "/")
            page.fill("#login-user", "alice")
            page.fill("#login-password", PW)
            page.click("#login button")
            page.wait_for_selector("#artifacts li")
            # a run of conversation A is streaming: a new conversation stops following it and can send at once
            page.route("**/runs/*/events**", lambda r: None)  # never answers: the stream stays open
            page.route("**/sessions/*/messages", lambda r: r.fulfill(json={"run_id": "r1"}))
            page.evaluate("() => { follow('r0'); }")  # not awaited: it never ends
            assert page.evaluate("state.streaming") and page.is_disabled("#send")
            page.click("#new-session")
            assert not page.evaluate("state.streaming") and not page.is_disabled("#send")
            assert page.evaluate("state.follow") is None
            # S8: the first message of the new conversation keeps the open artifact and the file tree as they are
            page.click("#artifacts li:has-text('리포트')")
            page.wait_for_selector("#panel:not([hidden])")
            n = page.locator("#sessions li").count()
            page.fill("#input", "안녕")
            page.press("#input", "Enter")
            page.wait_for_function("n => document.querySelectorAll('#sessions li').length === n", arg=n + 1)
            page.wait_for_timeout(300)
            assert page.is_visible("#panel") and page.locator("#art-title").inner_text() == "리포트"
            assert page.locator("#messages .msg.user").last.inner_text() == "안녕"
            assert not errs, errs
            br.close()
    finally:
        for p in procs:
            p.send_signal(signal.SIGKILL); p.wait()


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
