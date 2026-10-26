"""Loop 2: agent loop end-to-end against the gateway (gateway must be running on :8100)."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.loop import Context, run, run_tool  # noqa: E402
from agent.rag import DocStore  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402
from agent import github  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l2-"))
BUGGY = "def add(a, b):\n    return a - b\n"
TEST = "from calc import add\nassert add(2, 3) == 5\nprint('tests ok')\n"


def sandbox(name):
    return Sandbox(TMP / name)


def t_predefined_tool():
    calls = []
    ans, _ = run("What is the current UTC year? Use a tool.", Context(), tool_names=["current_time"],
                 log=calls.append)
    assert calls and "current_time" in calls[0] and "2026" in ans, (calls, ans)


def t_arg_validation():
    sb = sandbox("argval")
    try:
        ctx = Context(sandbox=sb)
        assert run_tool(ctx, "bash", "{not json").startswith("ERROR: JSONDecodeError")
        assert "missing required" in run_tool(ctx, "write_file", '{"path":"a"}')
        assert "unknown tool" in run_tool(ctx, "rm_rf", "{}")
        assert "escapes workspace" in run_tool(ctx, "read_file", '{"path":"../../etc/passwd"}')
        assert "read_file this path" in run_tool(ctx, "edit_file", '{"path":"x","old":"a","new":"b"}')
    finally:
        sb.close()


def t_secret_redaction():
    from agent.loop import redact
    out = redact("OPENAI_API_KEY=sk-live-SECRET-9931\ntoken ghp_abcdefghijklmnopqrstuvwxyz0123\nok")
    assert "SECRET-9931" not in out and "ghp_" not in out and out.endswith("ok"), out


def t_sandbox_isolation():
    sb = sandbox("iso")
    try:
        net = sb.exec("python -c \"import urllib.request;urllib.request.urlopen('https://example.com',timeout=5)\"")
        assert "exit_code=0" not in net, net
        ro = sb.exec("touch /etc/x")
        assert "exit_code=0" not in ro, ro
        assert "uid=1000" in sb.exec("id")
        assert "timeout" in sb.exec("sleep 5", timeout=1)
        big = sb.exec("python -c \"print('x'*50000)\"")
        assert "truncated" in big and len(big) < 11_000
    finally:
        sb.close()


def t_fix_bug_in_sandbox():
    sb = sandbox("fix")
    (sb.workspace / "calc.py").write_text(BUGGY)
    (sb.workspace / "test_calc.py").write_text(TEST)
    log = []
    try:
        ans, _ = run("python test_calc.py fails. Fix the bug in calc.py and make the test pass.",
                     Context(sandbox=sb), tool_names=["bash", "read_file", "write_file", "edit_file"], log=log.append)
        print("   ", *log, sep="\n    ")
        assert "tests ok" in sb.exec("python test_calc.py"), ans
    finally:
        sb.close()


def t_rag():
    docs = DocStore()
    docs.add_text("hr_policy.md", "# 사내 규정\n원격근무: 주 2회까지 가능.\n" * 3 +
                  "Vacation policy: employees of Zephyrine Corp receive 23 days of paid leave per year, "
                  "plus 4 'quiet days' in December.\n" + "기타 문서 내용. " * 200)
    docs.add_text("menu.md", "Cafeteria menu: Monday kimchi stew, Tuesday bibimbap. " * 30)
    log = []
    ans, _ = run("How many paid leave days do Zephyrine Corp employees get? Cite the source.",
                 Context(docs=docs), tool_names=["search_documents"], log=log.append)
    assert log and "23" in ans and "hr_policy" in ans, (log, ans)


def t_github_flow():
    origin = TMP / "origin.git"
    seed = TMP / "seed"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "calc.py").write_text(BUGGY)
    (seed / "test_calc.py").write_text(TEST)
    for c in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
              ["push", "-q", str(origin), "main"]):
        github.git(seed, *c)
    ws = TMP / "gh_ws"
    github.clone(str(origin), ws)
    sb = Sandbox(ws)
    asked = []
    task = ("Fix the failing test (python test_calc.py), commit on branch fix/add, "
            "then open a pull request for it.")
    try:
        # 1) human denies -> nothing pushed
        ctx = Context(sandbox=sb, remote_url=str(origin), approve=lambda n, a: asked.append((n, a)) or False)
        log = []
        run(task, ctx, log=log.append)
        print("   ", *log, sep="\n    ")
        assert asked and asked[0][0] == "open_pull_request", asked
        assert github.git(origin, "rev-parse", "--verify", "fix/add")[0] != 0, "pushed despite denial"
        # 2) human approves -> branch lands on origin with the fix
        ctx.approve = lambda n, a: True
        ans, _ = run("The user approved now. Open the pull request.", ctx, log=log.append)
        rc, content = github.git(origin, "show", "fix/add:calc.py")
        assert rc == 0 and "a + b" in content, (ans, content)
    finally:
        sb.close()


if __name__ == "__main__":
    only = sys.argv[1:]
    for name, fn in list(globals().items()):
        if name.startswith("t_") and (not only or name in only):
            try:
                fn(); print("PASS", name)
            except Exception as e:
                print("FAIL", name, repr(e)[:600])
    shutil.rmtree(TMP, ignore_errors=True)
