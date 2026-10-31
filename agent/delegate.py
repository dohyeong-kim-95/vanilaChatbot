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
    try:
        sb = ctx.sandbox.fresh()
    except subprocess.CalledProcessError as e:  # e.g. outputs/ is a file: say so without the docker command line
        raise RuntimeError(f"could not start agent {name}: {e.stderr.decode(errors='replace').strip()[-300:]}") from None
    try:
        seen, notes = _transfer(ctx.sandbox, sb, lambda rel, digest: True, "the run's outputs/ given to the agent",
                                MAX_FILES + 1)
        try:
            out, err = _talk(ctx, sb, name, m, task, inputs), None
        except Exception as e:  # whatever went wrong (e.g. emit), the agent's files are still copied back
            out, err = None, e
        # nothing of the agent's runs on while we copy (a background writer would race the copy)
        with contextlib.suppress(subprocess.TimeoutExpired):
            subprocess.run(["docker", "exec", sb.name, "sh", "-c", "kill -9 -1"], capture_output=True, timeout=30)
        notes += _transfer(sb, ctx.sandbox, lambda rel, digest: seen.get(rel) != digest, "the agent's outputs/",
                           len(seen) + MAX_FILES + 1, known=seen.__contains__)[1]
    finally:
        sb.close()
    note = "".join(f"\n[note: {n}]" for n in notes)
    if err:
        msg = str(err) if isinstance(err, RuntimeError) else f"agent {name} failed: {type(err).__name__}: {err}"
        raise RuntimeError(_utf8(msg + note)) from err
    return _utf8(out + note)


def _utf8(t):
    """Lone surrogates (a file name tar could not decode, a "\\ud800" in the agent's JSON) would fail the event's
    write to SQLite and the stream to the UI."""
    return t.encode("utf-8", "replace").decode("utf-8")


# Source side (format gnu: _Info refuses pax headers, whatever the image's tar defaults to): regular files of at most MAX_OUTPUTS bytes (find -size), at most $1 of them, so a huge or sparse file
# is never read. timeout kills find/tar inside the container (killing the docker exec client does not).
_SEND = ('cd /workspace/outputs 2>/dev/null || exec tar -cf - -T /dev/null; '
         'n=$(find . -type f -size +$(($2))c | head -n 1000 | wc -l); '
         '[ "$n" -gt 0 ] && echo "$n file(s) over $(($2 / 1048576)) MB not sent" >&2; '
         'find . -type f -size -$(($2 + 1))c -print0 | head -z -n "$1" | '
         'tar --null --format=gnu --no-recursion -T - -cf - --warning=no-file-changed')
# Destination side: Python's "data" filter refuses members that would land outside outputs/ through a link the
# source container made (outputs/ws -> /workspace), absolute paths and ../; outputs/ itself must not be a link.
# An existing file is unlinked first: writing into it would go through a hard link the source made
# (outputs/h.txt = /workspace/keep.py).
_RECV = """import os, sys, tarfile
d = "/workspace/outputs"
if os.path.islink(d):
    sys.exit("outputs/ is a symlink; nothing written")
os.makedirs(d, exist_ok=True)
bad = []
try:
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as t:
        for m in t:
            try:
                m = tarfile.data_filter(m, d)
                p = os.path.join(d, m.name)
                if os.path.lexists(p) and not os.path.isdir(p):
                    os.unlink(p)
                t.extract(m, d, filter="fully_trusted")
            except Exception as e:
                bad.append(f"{m.name}: {type(e).__name__}: {e}")
except tarfile.ReadError as e:
    bad.append(f"ReadError: {e}")
if bad:
    sys.exit("; ".join(bad[:3]) + (f"; ... {len(bad)} files not written" if len(bad) > 3 else ""))
"""


class _Info(tarfile.TarInfo):
    """Header parsing that cannot be made to hold much in memory: a pax or GNU long-name header is read whole before
    any check of ours, pax headers pile up (global ones for the whole stream), and sparse maps become one Python
    object per number. Our tar (format gnu) writes no pax headers, no sparse files and no names near this size, so
    such a header is a forged stream: stop."""
    MAX_HEADER = 16 * 1024

    @classmethod
    def fromtarfile(cls, tf):
        try:
            return super().fromtarfile(tf)
        except (tarfile.InvalidHeaderError, tarfile.TruncatedHeaderError) as e:  # past the first member, tarfile
            raise tarfile.HeaderError(f"bad header: {e}") from None         # takes these as the end: say so

    def _proc_member(self, tf):
        if self.type in (tarfile.GNUTYPE_SPARSE, tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE):
            raise tarfile.HeaderError(f"{'sparse' if self.type == tarfile.GNUTYPE_SPARSE else 'pax'} header")
        if self.type in (tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK) and self.size > self.MAX_HEADER:
            raise tarfile.HeaderError(f"long-name header of {self.size} bytes")
        return super()._proc_member(tf)

    def _proc_gnusparse_00(self, *a):
        raise tarfile.HeaderError("sparse member")

    _proc_gnusparse_01 = _proc_gnusparse_10 = _proc_gnusparse_00


class _Capped:
    """A pipe that fails once more than `cap` bytes were read from it."""
    def __init__(self, f, cap):
        self.f, self.left = f, cap

    def read(self, n=-1):
        b = self.f.read(min(n, self.left + 1) if n >= 0 else self.left + 1)
        self.left -= len(b)
        if self.left < 0:
            raise OSError("stream larger than expected; stopped")
        return b


def _transfer(src, dst, keep, what, scan, timeout=120, known=lambda rel: False):
    """Regular files under src's /workspace/outputs into dst's, as tar streams through us: one docker exec each
    side; nothing lands on the host disk but a spool file (> 1 MB, deleted at once). The source lists at most
    `scan` files of at most MAX_OUTPUTS bytes (one more than we take, to know there were more); we pass on
    plain relative names only, at most MAX_FILES / MAX_OUTPUTS in all (then stop), and only those keep(rel, sha256)
    wants (known(rel): a file keep() might skip, so read even past the limit). Returns ({rel: sha256} sent, notes):
    a bad file, the limit or a failing side becomes a note for the result, never an exception that hides the agent's answer."""
    inner = str(max(timeout - 10, 5))
    # the source container can write its own stream (stop our tar, write to its stdout): a header that claims
    # 1 GB would be read into our memory before any member check (_Info refuses that). Never read more than the
    # files we may take, the unchanged ones keep() skips (copy-out), the one that is over the limit, and headers.
    cap = 3 * MAX_OUTPUTS + scan * 8192
    tx = subprocess.Popen(["docker", "exec", src.name, "timeout", "-k", "2", inner, "sh", "-c", _SEND, "_", str(scan),
                           str(MAX_OUTPUTS)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    rx = subprocess.Popen(["docker", "exec", "-i", dst.name, "timeout", "-k", "2", inner, "python", "-I", "-c", _RECV],
                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    caps = [_Cap(tx.stderr, 600), _Cap(rx.stderr, 600)]
    for c in caps:
        c.start()
    killer = threading.Timer(timeout, lambda: (tx.kill(), rx.kill()))
    killer.start()
    sent, notes, total, skipped = {}, [], 0, 0
    try:
        with tarfile.open(fileobj=_Capped(tx.stdout, cap), mode="r|", tarinfo=_Info) as tin, \
                tarfile.open(fileobj=rx.stdin, mode="w|", format=tarfile.PAX_FORMAT) as tout:
            for mi in tin:
                tin.members.clear()  # a stream tarfile keeps every header it read; we need only this one
                if mi.size > MAX_OUTPUTS:  # grew after find looked: stop, never skip over it (that reads it all)
                    notes.append(f"copying {what} stopped at {mi.name}: larger than {MAX_OUTPUTS // 2**20} MB")
                    break
                parts = [x for x in mi.name.split("/") if x not in ("", ".")]
                if not mi.isfile() or not parts or ".." in parts or mi.name.startswith("/"):
                    continue
                rel = "/".join(parts)
                # over the limit unless keep() skips it: a file the run already has (copy-out) does not count
                over = len(sent) >= MAX_FILES or total + mi.size > MAX_OUTPUTS
                if over and not known(rel):
                    skipped += 1  # stop here: skipping on would still read every later file through us
                    break
                with tempfile.SpooledTemporaryFile(1 << 20) as buf:
                    h = hashlib.sha256()
                    for chunk in iter(lambda f=tin.extractfile(mi): f.read(1 << 16), b""):
                        h.update(chunk)
                        buf.write(chunk)
                    if not keep(rel, h.hexdigest()):
                        continue
                    if over:
                        skipped += 1  # stop here: skipping on would still read every later file through us
                        break
                    buf.seek(0)
                    mo = tarfile.TarInfo(rel)
                    mo.size, mo.mtime, mo.mode = mi.size, min(max(mi.mtime, 0), 2**33 - 1), 0o644  # gnu: an int
                    tout.addfile(mo, buf)
                sent[rel] = h.hexdigest()
                total += mi.size
    except Exception as e:  # TarError, OSError, and what a forged header raises (ValueError, RecursionError, ...)
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
        notes.append(f"some files of {what} not copied: over the limit of {MAX_FILES} files / "
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
                ctx.emit({"type": "agent_progress", "agent": name, "message": _utf8(str(msg.get("message", ""))[:300])})
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
