"""Connected agents (Loop 21): agents other teams wrote as Python packages, used as tools.

agents/{name}/agent.json + code, chosen by the operator (like skills). Each becomes a tool `agent__{name}`. The code is
NOT trusted: each call runs in its own fresh container over the run's workspace (read-only but for an in-memory
outputs/, no network, agents dir mounted read-only at /agents; it shares no processes or /tmp with the run's sandbox
or other agents) through agents/_runner.py, and talks to us over JSON lines on stdin/stdout. What it may ask the host for is narrow:
progress messages (shown in the UI) and LLM calls (made by us, counted against the manifest's llm_calls limit).
Timeouts, line size and message count are enforced here, outside the sandbox.
"""
import contextlib
import hashlib
import json
import re
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path

from .sandbox import _Cap

DIR = Path(__file__).resolve().parents[1] / "agents"
MOUNT = "/agents"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
MAX_TIMEOUT, MAX_LLM, MAX_LINE, MAX_MESSAGES = 600, 20, 256 * 1024, 2000
MAX_OUTPUTS, MAX_FILES = 50 * 1024 * 1024, 1000  # each way between the run's outputs/ and an agent's


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
    # A fresh container per call: in the run's shared one, an agent could plant code that every later python there
    # runs (a .pth under /tmp) or write into another process's stdout. -I: no user site, no cwd on sys.path.
    # Its outputs/ is a tmpfs: the run's outputs/ are streamed in before, its new files streamed back after.
    sb = ctx.sandbox.fresh()
    try:
        seen, notes = _transfer(ctx.sandbox, sb, lambda rel, digest: True, "the run's outputs/ given to the agent")
        try:
            out, err = _talk(ctx, sb, name, m, task, inputs), None
        except RuntimeError as e:
            out, err = None, e
        notes += _transfer(sb, ctx.sandbox, lambda rel, digest: seen.get(rel) != digest, "the agent's outputs/")[1]
    finally:
        sb.close()
    note = "".join(f"\n[note: {n}]" for n in notes)
    if err:
        raise RuntimeError(f"{err}{note}")
    return out + note


def _transfer(src, dst, keep, what, timeout=120):
    """Regular files under src's /workspace/outputs into dst's, as tar streams through us: one docker exec each
    side; nothing lands on the host disk but a spool file (> 1 MB, deleted at once). Only regular files with plain relative names (no links, devices, ../), at most
    MAX_FILES / MAX_OUTPUTS, and only those keep(rel, sha256) wants. Returns ({rel: sha256} sent, notes): a bad file,
    the limit or a failing side becomes a note for the result, never an exception that hides the agent's answer."""
    tx = subprocess.Popen(["docker", "exec", src.name, "tar", "-C", "/workspace/outputs", "-cf", "-", "."],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    rx = subprocess.Popen(["docker", "exec", "-i", dst.name, "sh", "-c", "mkdir -p /workspace/outputs && "
                           "tar -C /workspace/outputs -xf - --no-same-owner --no-same-permissions"],
                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    caps = [_Cap(tx.stderr, 600), _Cap(rx.stderr, 600)]
    for c in caps:
        c.start()
    killer = threading.Timer(timeout, lambda: (tx.kill(), rx.kill()))
    killer.start()
    sent, notes, total, skipped = {}, [], 0, 0
    try:
        with tarfile.open(fileobj=tx.stdout, mode="r|") as tin, \
                tarfile.open(fileobj=rx.stdin, mode="w|", format=tarfile.PAX_FORMAT) as tout:
            for mi in tin:
                parts = [x for x in mi.name.split("/") if x not in ("", ".")]
                if not mi.isfile() or not parts or ".." in parts or mi.name.startswith("/"):
                    continue
                rel = "/".join(parts)
                if len(sent) >= MAX_FILES or total + mi.size > MAX_OUTPUTS:
                    skipped += 1
                    continue
                with tempfile.SpooledTemporaryFile(1 << 20) as buf:
                    h = hashlib.sha256()
                    for chunk in iter(lambda f=tin.extractfile(mi): f.read(1 << 16), b""):
                        h.update(chunk)
                        buf.write(chunk)
                    if not keep(rel, h.hexdigest()):
                        continue
                    buf.seek(0)
                    mo = tarfile.TarInfo(rel)
                    mo.size, mo.mtime, mo.mode = mi.size, mi.mtime, 0o644
                    tout.addfile(mo, buf)
                sent[rel] = h.hexdigest()
                total += mi.size
    except (tarfile.TarError, OSError) as e:
        notes.append(f"copying {what} stopped: {type(e).__name__}: {e}"[:300])
    finally:
        with contextlib.suppress(OSError):
            rx.stdin.close()
        tx.kill()
        rx.wait()
        tx.wait()
        killer.cancel()
        for c in caps:
            c.join(5)
    if skipped:
        notes.append(f"{skipped} file(s) of {what} not copied: over the limit of {MAX_FILES} files / "
                     f"{MAX_OUTPUTS // 2**20} MB")
    errs = [c.text().strip() for c in caps if c.text().strip()]
    if rx.returncode or errs:  # tx is killed after we read it, its code says nothing; its stderr does
        notes.append(f"copying {what}: {' | '.join(errs) or f'exit {rx.returncode}'}"[:300])
    return sent, notes


def _talk(ctx, sb, name, m, task, inputs):
    # `timeout` inside the container: killing the docker exec client alone leaves the agent process running
    p = subprocess.Popen(["docker", "exec", "-i", sb.name, "timeout", "-k", "2", str(m["timeout"]),
                          "python", "-I", f"{MOUNT}/_runner.py", name],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    killer = threading.Timer(m["timeout"], p.kill)
    killer.start()
    errs = _Cap(p.stderr, 4000)  # drained to the end (a full pipe would block the agent), keeping head + tail
    errs.start()
    log, result, error, llm_used, messages, t0 = [], None, None, 0, 0, time.time()
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
        errs.join(5)
    took = time.time() - t0
    if result is not None and error is None:
        return f"[{name} finished in {took:.0f}s]\n{result}"
    if error is None:
        error = f"timed out after {m['timeout']}s" if took >= m["timeout"] - 0.5 else "ended without a result"
    tail = "\n".join((log + errs.text().splitlines())[-10:])
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
