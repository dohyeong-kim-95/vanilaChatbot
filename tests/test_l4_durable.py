"""Loop 7: durable runs. Reconnect with Last-Event-ID, approval survives a server restart, crash mid-tool,
cancel keeps history valid. Needs gateway on :8100; starts its own server on :8200."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent import github  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l4-"))
DATA = TMP / "data"
C = httpx.Client(base_url="http://127.0.0.1:8200", timeout=None, headers={"X-User": "tester"})


def server():
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.app:app", "--port", "8200"], cwd=ROOT,
                         env={**os.environ, "AGENT_ROOT": str(DATA)},
                         stdout=subprocess.DEVNULL, stderr=open(TMP / "server.log", "a"))
    for _ in range(50):
        try:
            C.get("/docs"); return p
        except httpx.TransportError:
            time.sleep(0.2)
    raise RuntimeError("server did not start")


def kill(p):
    p.send_signal(signal.SIGKILL); p.wait()


def events(rid, after=0, stop=lambda ev: False):
    """Read SSE until the server closes it or stop(ev); returns [(seq, ev)]."""
    out = []
    with C.stream("GET", f"/runs/{rid}/events", headers={"Last-Event-ID": str(after)}) as r:
        seq = None
        for line in r.iter_lines():
            if line.startswith("id: "):
                seq = int(line[4:])
            elif line.startswith("data: "):
                out.append((seq, json.loads(line[6:])))
                if stop(out[-1][1]):
                    break
    return out


def new_session(remote=None):
    """Project per test (Loop 12: files live in the user's project, the sandbox exists only during a run)."""
    pid = "p" + os.urandom(4).hex()
    C.post("/projects", json={"project_id": pid})
    if remote:
        C.put(f"/projects/{pid}/settings", json={"settings": {"git_remote": str(remote)}})
    return C.post("/sessions", json={"project_id": pid}).json()["session_id"]


def bare_origin():
    origin = TMP / f"{os.urandom(4).hex()}-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    return origin


def history_valid(sid):
    """Every assistant tool_call has exactly one tool result (else the next LLM call is a 400 on most APIs)."""
    db = __import__("sqlite3").connect(DATA / "agent.db")
    msgs = json.loads(db.execute("SELECT history FROM sessions WHERE id=?", (sid,)).fetchone()[0])
    calls = [tc["id"] for m in msgs if m["role"] == "assistant" for tc in m.get("tool_calls") or []]
    results = [m["tool_call_id"] for m in msgs if m["role"] == "tool"]
    return sorted(calls) == sorted(results)


def t_reconnect_and_approval_across_restart():
    srv = server()
    try:
        origin = bare_origin()
        sid = new_session(origin)
        rid = C.post(f"/sessions/{sid}/messages", json={"content": (
            "Create hello.py that prints 'hi', run it to check, commit it on branch feat/hello, "
            "then open a pull request.")}).json()["run_id"]
        part1 = events(rid, stop=lambda ev: ev["type"] == "tool_result")  # "tab closed" after first result
        part2 = events(rid, after=part1[-1][0])                           # reconnect -> rest up to interrupt
        seqs = [s for s, _ in part1 + part2]
        assert seqs == sorted(set(seqs)) and seqs == list(range(seqs[0], seqs[0] + len(seqs))), seqs  # no gap/dup
        assert part2[-2][1]["type"] == "approval_request" and part2[-1][1]["outcome"] == "interrupt", part2[-2:]
        assert part2[-2][1]["arguments"]["branch"] == "feat/hello", part2[-2]  # approver sees real values
        print("    before restart:", " ".join(e["type"] for _, e in part1 + part2))

        kill(srv); srv = server()                                          # server dies while waiting for a human
        assert C.get(f"/runs/{rid}").json()["status"] == "awaiting_approval"
        assert C.post(f"/runs/{rid}/approval", json={"approved": True}).status_code == 200
        assert C.post(f"/runs/{rid}/approval", json={"approved": True}).status_code == 409  # double click
        part3 = events(rid, after=seqs[-1])
        print("    after restart: ", " ".join(e["type"] for _, e in part3))
        types = [e["type"] for _, e in part3]
        assert types[:2] == ["run_started", "tool_result"], types  # pending call ran first, no LLM re-call before it
        assert part3[-1][1]["outcome"] == "succeeded", part3[-1]
        rc, out = github.git(origin, "show", "feat/hello:hello.py")
        assert rc == 0 and "hi" in out, out
        assert history_valid(sid)
    finally:
        kill(srv)


def t_crash_mid_tool():
    srv = server()
    try:
        sid = new_session()
        rid = C.post(f"/sessions/{sid}/messages", json={"content": (
            "Run exactly this bash command once: `sleep 15 && echo done > marker.txt`. "
            "Then tell me whether marker.txt exists.")}).json()["run_id"]
        evs = events(rid, stop=lambda ev: ev["type"] == "tool_call" and "sleep" in ev["arguments"])
        time.sleep(1)
        kill(srv)                                                          # crash while the tool is running
        time.sleep(16)
        srv = server()                                                     # startup recovery resumes the run
        rest = events(rid, after=evs[-1][0])
        print("    after crash:", " ".join(e["type"] for _, e in rest), "| final:",
              [e["content"] for _, e in rest if e["type"] == "final"][0][:160])
        assert rest[-1][1]["outcome"] == "succeeded", rest[-1]
        reruns = [e for _, e in rest if e["type"] == "tool_call" and e["name"] == "bash"
                  and "echo done > marker.txt" in json.loads(e["arguments"])["command"]]
        assert not reruns, f"non-idempotent tool was blindly re-run: {reruns}"
        assert history_valid(sid)
    finally:
        kill(srv)


def t_cancel_keeps_history_valid():
    srv = server()
    try:
        sid = new_session()
        rid = C.post(f"/sessions/{sid}/messages", json={"content": (
            "Create files a.txt, b.txt, c.txt, d.txt one per turn with write_file, then list them.")}).json()["run_id"]
        evs = events(rid, stop=lambda ev: ev["type"] == "tool_result")
        C.post(f"/runs/{rid}/cancel")
        rest = events(rid, after=evs[-1][0])
        assert rest[-1][1]["outcome"] == "cancelled", rest[-1]
        assert history_valid(sid)
        rid2 = C.post(f"/sessions/{sid}/messages", json={"content": "Which files exist now? Use bash ls."}).json()
        fin = events(rid2["run_id"])
        assert fin[-1][1]["outcome"] == "succeeded", fin[-1]                # the session is still usable
        print("    follow-up:", [e["content"] for _, e in fin if e["type"] == "final"][0][:120])
    finally:
        kill(srv)


if __name__ == "__main__":
    only = sys.argv[1:]
    try:
        for name, fn in list(globals().items()):
            if name.startswith("t_") and (not only or name in only):
                try:
                    t = time.time(); fn(); print(f"PASS {name} ({time.time() - t:.0f}s)")
                except Exception as e:
                    print("FAIL", name, repr(e)[:600])
    finally:
        subprocess.run("docker ps -q --filter name=sbx- | xargs -r docker rm -f", shell=True, capture_output=True)
