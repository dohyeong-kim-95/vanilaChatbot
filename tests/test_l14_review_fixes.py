"""Loop 19: regressions for the independent review (docs/LOOP_LOG.md Loop 19). Deterministic; needs Docker.
python tests/test_l14_review_fixes.py [test_name ...]
"""
import datetime as dt
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
FAKE_PORT = 8396
os.environ["LLM_BASE_URL"] = f"http://127.0.0.1:{FAKE_PORT}/v1"  # the app's runs talk to the scripted model below
import test_l9_artifacts as l9  # noqa: E402
from agent.loop import Context, run_tool  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402
from agent.userstore import UserStore  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l14-"))


def t_symlink_swap_cannot_reach_host():
    """A process inside the sandbox flips a workspace dir between a real dir and a symlink to a host path while
    host-side tools read and write through it. Nothing on the host may be read or written."""
    ws, host = TMP / "ws", TMP / "host"
    host.mkdir()
    (host / "f.txt").write_text("HOST SECRET\n")
    sb = Sandbox(ws)
    try:
        sb.exec("mkdir -p real && echo inside > real/f.txt")
        # the flipper runs in the container, as the attacker would
        sb.exec(f"nohup sh -c 'while :; do ln -sfn {host} d; rm -f d; ln -sfn real d; rm -f d; mkdir -p d; rmdir d; "
                f"ln -sfn real d; done' >/dev/null 2>&1 &")
        ctx = Context(sandbox=sb)
        reads = [run_tool(ctx, "read_file", '{"path": "d/f.txt"}') for _ in range(200)]
        writes = [run_tool(ctx, "write_file", '{"path": "d/f.txt", "content": "PWNED"}') for _ in range(50)]
        assert not any("HOST SECRET" in r for r in reads), "host file read through the workspace"
        assert (host / "f.txt").read_text() == "HOST SECRET\n", "host file overwritten"
        ok = sum("inside" in r or "PWNED" in r for r in reads)
        print(f"    reads that saw the workspace file: {ok}/200, host hits 0; writes ok {sum('wrote' in w for w in writes)}/50")
    finally:
        sb.close()


def t_git_commit_cannot_reach_host():
    """Same swap against git_commit (host git on the work tree): the sandbox is paused while git runs."""
    ws, host = TMP / "ws-git", TMP / "host-git"
    host.mkdir()
    (host / "leak.txt").write_text("HOST FILE\n")
    sb = Sandbox(ws)
    try:
        sb.exec("mkdir -p repo && echo hi > repo/a.txt")
        gd = TMP / "gitdir"
        import subprocess
        subprocess.run(["git", "init", "-q", f"--separate-git-dir={gd}", str(ws / "repo")], check=True)
        (ws / "repo" / ".git").unlink()
        sb.exec(f"nohup sh -c 'while :; do mv repo real 2>/dev/null; ln -sfn {host} repo; rm -f repo; mv real repo 2>/dev/null; done'"
                " >/dev/null 2>&1 &")
        ctx = Context(sandbox=sb, git_dir=str(gd))
        outs = [run_tool(ctx, "git_commit", json.dumps({"branch": "main", "message": f"c{i}"})) for i in range(20)]
        tracked = subprocess.run(["git", f"--git-dir={gd}", "log", "--all", "--name-only", "--format="],
                                 capture_output=True, text=True).stdout
        assert "leak.txt" not in tracked, tracked
        print(f"    20 commits under a swap loop: host file never committed; results {set(o[:30] for o in outs)}")
    finally:
        sb.close()


def t_big_output_is_capped():
    """300 MB of output: the server keeps only head + tail, memory stays flat; read_file refuses huge files."""
    import resource
    sb = Sandbox(TMP / "ws2")
    try:
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        out = sb.exec("yes xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx | head -c 300000000", timeout=120)
        grown = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) // 1024
        assert out.startswith("exit_code=0") and "bytes truncated" in out and len(out) < 25_000, out[:200]
        assert grown < 100, f"peak RSS grew {grown} MB for 300 MB of output"
        sb.exec("head -c 30000000 /dev/zero > big")
        r = run_tool(Context(sandbox=sb), "read_file", '{"path": "big"}')
        assert "larger than" in r, r[:200]
        print(f"    300 MB of output -> {len(out):,} chars, peak RSS +{grown} MB")
    finally:
        sb.close()


def _call(cid, name, args, content=None):
    return {"role": "assistant", "content": content, "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}


Q = lambda text: {"question": text, "options": [{"label": "A"}, {"label": "B"}]}  # noqa: E731
SCRIPTS = {  # first user message -> steps; each step answers the latest message
    "two questions": [_call("q1", "ask_user", Q("첫 질문")), _call("q2", "ask_user", Q("두 번째 질문"), "중간 설명")],
    "file across a question": [_call("w", "bash", {"command": "echo kept > /workspace/scratch.txt"}),
                               _call("q", "ask_user", Q("계속할까요?")),
                               _call("r", "bash", {"command": "cat /workspace/scratch.txt"})],
}


def fake_llm():
    import uvicorn
    from fastapi import FastAPI
    app = FastAPI()

    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": [{"id": "fake", "object": "model"}]}

    @app.post("/v1/chat/completions")
    def chat(body: dict):
        msgs = body["messages"]
        steps = SCRIPTS[next(m["content"] for m in msgs if m["role"] == "user")]
        done = sum(m["role"] == "assistant" for m in msgs)
        msg = steps[done] if done < len(steps) else {"role": "assistant", "content": "끝: " + str(msgs[-1]["content"])}
        return {"id": "x", "object": "chat.completion", "created": 0, "model": "fake",
                "choices": [{"index": 0, "finish_reason": "stop", "message": msg}]}
    srv = uvicorn.Server(uvicorn.Config(app, port=FAKE_PORT, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{FAKE_PORT}/v1/models"); break
        except httpx.TransportError:
            time.sleep(0.1)
    return srv


def wait_status(c, rid, statuses, timeout=60):
    for _ in range(timeout * 5):
        r = c.get(f"/runs/{rid}").json()
        if r["status"] in statuses:
            return r
        time.sleep(0.2)
    raise AssertionError(f"run stuck in {r['status']}")


def t_runs_and_ui():
    """Stale cards cannot answer a newer call; reload shows only the waiting card, once; a file made before a
    question is still there after it; a sandbox that cannot start fails the run instead of locking the project."""
    store = UserStore(l9.DATA)
    for p in ("q", "f", "bad"):
        store.create_project("alice", p)
    srv = fake_llm()
    ps = l9.servers()
    pw, b = l9.browser()
    a = l9.client("alice")
    try:
        # 1. two questions, reload while the second waits
        page = b.new_page(viewport={"width": 1300, "height": 800})
        page.goto(l9.APP + "/")
        page.evaluate("() => localStorage.setItem('user', 'alice')")
        page.goto(l9.APP + "/")
        page.wait_for_function("() => document.querySelector('#project option')")
        page.select_option("#project", "q")
        page.fill("#input", "two questions")
        page.click("#send")
        page.wait_for_selector(".question button.option")
        page.locator(".question button.option").first.click()
        page.wait_for_selector("text=두 번째 질문")
        sid = page.evaluate("() => state.session")
        rid = a.get(f"/sessions/{sid}").json()["active_run"]["run_id"]
        wait_status(a, rid, ["awaiting_approval"])
        page.reload()
        page.wait_for_selector(".question")
        time.sleep(0.5)
        cards = page.locator(".question .q").all_inner_texts()
        assert cards == ["두 번째 질문"], cards
        assert page.locator(".msg.assistant").all_inner_texts().count("중간 설명") == 1
        stale = a.post(f"/runs/{rid}/approval", json={"call_id": "q1", "answer": "B"})
        assert stale.status_code == 409, stale.text
        page.locator(".question button.option").nth(1).click()
        page.wait_for_selector("text=끝: The user answered: B")
        # 2. a workspace file outside repo/outputs survives the wait for an answer
        sid = a.post("/sessions", json={"project_id": "f"}).json()["session_id"]
        rid = a.post(f"/sessions/{sid}/messages", json={"content": "file across a question"}).json()["run_id"]
        assert wait_status(a, rid, ["awaiting_approval"])["pending"]["call_id"] == "q"
        assert a.post(f"/runs/{rid}/approval", json={"call_id": "q", "answer": "A"}).status_code == 200
        wait_status(a, rid, ["succeeded", "error"])
        final = [m["content"] for m in a.get(f"/sessions/{sid}").json()["messages"] if m["role"] == "assistant"][-1]
        assert "kept" in final, final
        print(f"    reload: one live card; stale answer 409; file across a question: {final[:40]!r}")
    finally:
        b.close(); pw.stop(); l9.kill(ps)
    # 3. sandbox cannot start (no such image): the run fails visibly and the project is not locked
    os.environ["SANDBOX_IMAGE"] = "vanilachatbot/no-such-image:0"
    ps = l9.servers()
    try:
        sid = a.post("/sessions", json={"project_id": "bad"}).json()["session_id"]
        rid = a.post(f"/sessions/{sid}/messages", json={"content": "two questions"}).json()["run_id"]
        assert wait_status(a, rid, ["error", "succeeded", "awaiting_approval"], 30)["status"] == "error"
        again = a.post(f"/sessions/{sid}/messages", json={"content": "two questions"})
        assert again.status_code == 200, again.text
        print("    sandbox failure -> run error, next message accepted")
    finally:
        del os.environ["SANDBOX_IMAGE"]
        l9.kill(ps); srv.should_exit = True


def t_history_edge_cases():
    """A huge first message is not a compaction crash; an empty assistant turn is not an empty API block."""
    from agent.loop import compact
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "x" * 400_000}]
    assert compact(msgs, client=None, model="m", budget=60_000) == msgs  # nothing to summarize: unchanged, no error
    os.environ.setdefault("ANTHROPIC_API_KEY", "unused")  # the module builds a client at import; no call is made
    from gateway.anthropic_backend import to_anthropic
    _, out = to_anthropic([{"role": "user", "content": "a"}, {"role": "assistant"}, {"role": "user", "content": ""}])
    assert all(m["content"] for m in out), out


def t_failed_run_then_next_turn():
    """A run that died after the model asked for a tool leaves an unanswered call; the next message must still
    produce a valid history (every tool call answered before the new user turn)."""
    from agent.loop import run
    hist = [{"role": "system", "content": "s"}, {"role": "user", "content": "first"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "current_time", "arguments": "{}"}}]}]

    class Done:  # answers once, no tool calls
        chat = completions = None

        def create(self, **kw):
            from openai.types.chat import ChatCompletion
            self.seen = kw["messages"]
            return ChatCompletion.model_validate({"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}]})
    c = Done()
    c.chat = c.completions = c
    run("second", Context(), client=c, model="m", history=hist, log=lambda *_: None)
    roles = [(m["role"], m.get("tool_call_id")) for m in c.seen]
    assert roles[3:5] == [("tool", "c1"), ("user", None)], roles


def t_logview_limits():
    from agent import logview
    two = TMP / "two.log"
    two.write_text("2026-01-01 00:00:00 INFO a\n2026-10-01 00:00:00 INFO b\n")
    try:
        logview.build(two, bucket=1)
        raise AssertionError("1 s buckets over 9 months must be refused")
    except ValueError as e:
        assert "time buckets" in str(e)
    java = TMP / "java.log"
    java.write_text("2026-10-29 10:00:00 ERROR order failed\norder 1712345678 failed at Foo.bar\n"
                    "2026-10-29 10:05:00 INFO ok\n")
    a, _ = logview.build(java)
    assert a["lines"] == 2 and a["start"].startswith("2026-10-29") and a["continuation_lines"] == 1, a["start"]
    sys_log = TMP / "sys.log"
    sys_log.write_text("Dec 31 23:59:58 h app: tick\nJan  1 00:00:01 h app: tick\n")
    a, _ = logview.build(sys_log)
    span = dt.datetime.fromisoformat(a["end"]) - dt.datetime.fromisoformat(a["start"])
    assert span < dt.timedelta(seconds=10), f"year rollover: {a['start']} .. {a['end']}"


def t_concurrent_uploads():
    store = UserStore(TMP / "store")
    store.create_project("alice", "up")
    errs = []

    def up(i):
        try:
            store.upload("alice", "up", f"f{i}.txt", f"data {i}".encode())
        except Exception as e:
            errs.append(e)
    ts = [threading.Thread(target=up, args=(i,)) for i in range(200)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    same = [threading.Thread(target=up, args=("dup",)) for _ in range(8)]
    for t in same:
        t.start()
    for t in same:
        t.join()
    manifest = json.loads((store.project_dir("alice", "up") / "uploads" / "manifest.json").read_text())
    assert len(manifest) == 201 and len(errs) == 7, (len(manifest), len(errs))
    print("    200 parallel uploads: 200 in manifest; same name x8: 1 kept, 7 refused")


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
