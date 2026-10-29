"""Connected agents (Loop 21): agents other teams wrote as Python packages, used as tools.

agents/{name}/agent.json + code, chosen by the operator (like skills). Each becomes a tool `agent__{name}`. The code is
NOT trusted: it runs inside the run's sandbox (no network, workspace only, agents dir mounted read-only at /agents)
through agents/_runner.py, and talks to us over JSON lines on stdin/stdout. What it may ask the host for is narrow:
progress messages (shown in the UI) and LLM calls (made by us, counted against the manifest's llm_calls limit).
Timeouts, line size and message count are enforced here, outside the sandbox.
"""
import json
import re
import subprocess
import threading
import time
from pathlib import Path

DIR = Path(__file__).resolve().parents[1] / "agents"
MOUNT = "/agents"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
MAX_TIMEOUT, MAX_LLM, MAX_LINE, MAX_MESSAGES, MAX_RESULT = 600, 20, 256 * 1024, 2000, 20_000


def tool_name(name):
    return "agent__" + name.replace("-", "_")


def discover(root=DIR):
    """name -> manifest. Invalid manifests fail loudly: the operator put them there."""
    out = {}
    for f in sorted(Path(root).glob("*/agent.json")):
        m = json.loads(f.read_text(encoding="utf-8"))
        name = m.get("name", "")
        if not NAME_RE.match(name) or name != f.parent.name:
            raise ValueError(f"{f}: name {name!r} must be a slug equal to its folder name")
        if not m.get("description") or ":" not in m.get("entry", ""):
            raise ValueError(f"{f}: description and entry 'module:function' are required")
        m["timeout"] = min(int(m.get("timeout", 120)), MAX_TIMEOUT)
        m["llm_calls"] = min(int(m.get("llm_calls", 0)), MAX_LLM)
        out[name] = m
    return out


def register(agents, registry, make_schema):
    """Add one tool per agent to the tool registry (same shape as built-in tools)."""
    for name, m in agents.items():
        params = {"task": {"type": "string", "description": "what you want this agent to do, in one sentence"},
                  **m.get("inputs", {})}
        registry[tool_name(name)] = {
            "fn": lambda ctx, task, _name=name, **inputs: run_agent(ctx, _name, task, inputs),
            "approval": bool(m.get("approval")), "ask": False,
            "schema": make_schema(tool_name(name), f"[connected agent: {name}] {m['description']}", params,
                                  ["task", *m.get("required", [])])}


def run_agent(ctx, name, task, inputs):
    m = ctx.agents.get(name) if ctx.agents else None
    if not m:
        raise ValueError(f"agent {name!r} is not connected")
    # `timeout` inside the container: killing the docker exec client alone leaves the agent process running
    p = subprocess.Popen(["docker", "exec", "-i", ctx.sandbox.name, "timeout", "-k", "2", str(m["timeout"]),
                          "python", f"{MOUNT}/_runner.py", name],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    killer = threading.Timer(m["timeout"], p.kill)
    killer.start()
    log, result, error, llm_used, messages, t0 = [], None, None, 0, 0, time.time()
    errs = []
    threading.Thread(target=lambda: errs.extend(p.stderr.read(64 * 1024).decode(errors="replace").splitlines()[-20:]),
                     daemon=True).start()
    try:
        p.stdin.write((json.dumps({"task": task, "inputs": inputs}, ensure_ascii=False) + "\n").encode())
        p.stdin.flush()
        while True:
            line = p.stdout.readline(MAX_LINE + 1)
            if not line:
                break
            messages += 1
            if messages > MAX_MESSAGES:
                error = f"too many messages (> {MAX_MESSAGES}); stopped"
                break
            if len(line) > MAX_LINE:
                error = f"a message was larger than {MAX_LINE} bytes; stopped"
                break
            try:
                msg = json.loads(line)
                kind = msg.get("type") if isinstance(msg, dict) else None
            except ValueError:
                kind = None
            if kind == "progress":
                ctx.emit({"type": "agent_progress", "agent": name, "message": str(msg.get("message", ""))[:300]})
            elif kind == "llm":
                llm_used += 1
                if llm_used > m["llm_calls"] or not ctx.llm:
                    reply = {"id": msg.get("id"), "error": f"LLM call limit reached ({m['llm_calls']})"}
                else:
                    try:
                        reply = {"id": msg.get("id"), "content": ctx.llm(_messages(msg.get("messages")))}
                    except Exception as e:
                        reply = {"id": msg.get("id"), "error": f"{type(e).__name__}: {e}"[:300]}
                p.stdin.write((json.dumps(reply, ensure_ascii=False) + "\n").encode())
                p.stdin.flush()
            elif kind == "result":
                result = str(msg.get("content", ""))
            elif kind == "error":
                error = str(msg.get("message", ""))[:500]
            else:
                log.append(line.decode(errors="replace").rstrip()[:200])
    except (BrokenPipeError, OSError):
        pass
    finally:
        killer.cancel()
        p.kill()
        p.wait()
    took = time.time() - t0
    if result is not None and error is None:
        return f"[{name} finished in {took:.0f}s]\n{result[:MAX_RESULT]}"
    if error is None:
        error = f"timed out after {m['timeout']}s" if took >= m["timeout"] - 0.5 else "ended without a result"
    tail = "\n".join((log + errs)[-10:])
    raise RuntimeError(f"agent {name} failed: {error}" + (f"\n{tail}" if tail else ""))


def _messages(msgs):
    """Only plain role/content messages from the agent reach our LLM (no tool definitions, no system override of ours)."""
    if not isinstance(msgs, list) or not msgs:
        raise ValueError("messages must be a non-empty list")
    out = []
    for mm in msgs[:50]:
        if not isinstance(mm, dict) or mm.get("role") not in ("system", "user", "assistant"):
            raise ValueError("each message needs role system/user/assistant")
        out.append({"role": mm["role"], "content": str(mm.get("content", ""))[:50_000]})
    return out
