"""Runs one connected agent inside the sandbox (mounted read-only at /agents). Standard library only.

Protocol (JSON lines; stdin from the host, stdout to the host):
  host  -> {"task": str, "inputs": {...}}                       first line
  agent -> {"type": "progress", "message": str}
  agent -> {"type": "llm", "id": n, "messages": [...]}           host answers {"id": n, "content": str} or {"id": n, "error": str}
  agent -> {"type": "result", "content": str}                     last line
  agent -> {"type": "error", "message": str}
Anything else the agent prints is passed to the host as ordinary log lines (shown only in failures).
An agent package exposes run(task, inputs, ctx) -> str; ctx.progress(msg), ctx.llm(messages) -> str, ctx.workspace.

An executable agent (manifest "command": argv, the program relative to the agent's folder; Loop 40) does not speak
this protocol, we do it for it: it runs in /workspace as  command... --task <task> --<input> <value> ...  (lists and
objects as JSON), its stdout is the result (at most MAX_STDOUT bytes), its stderr the log, exit code 0 success.
It cannot call the LLM.
"""
import importlib
import json
import signal
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAX_STDOUT = 200 * 1024
_out = sys.stdout


def _send(obj):
    _out.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _out.flush()


class Ctx:
    workspace = Path("/workspace")

    def __init__(self):
        self._n = 0

    def progress(self, message):
        _send({"type": "progress", "message": str(message)[:300]})

    def llm(self, messages):
        self._n += 1
        _send({"type": "llm", "id": self._n, "messages": messages})
        reply = json.loads(sys.stdin.readline() or "{}")
        if reply.get("id") != self._n or "error" in reply:
            raise RuntimeError(reply.get("error", "no answer from the host"))
        return reply["content"]


def run_command(name, manifest, request):
    argv = [str(ROOT / name / manifest["command"][0]), *manifest["command"][1:], "--task", str(request["task"])]
    for k, v in (request.get("inputs") or {}).items():
        if v is not None:
            argv += [f"--{k}", v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)]
    try:  # stderr: ours, which the host keeps (head + tail) for failures
        p = subprocess.Popen(argv, cwd="/workspace", stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=sys.__stderr__)
    except OSError as e:
        return _send({"type": "error", "message": f"cannot run {manifest['command'][0]}: {e}"[:500]})
    out = p.stdout.read(MAX_STDOUT + 1)
    if len(out) > MAX_STDOUT:
        p.kill()
        p.wait()
        return _send({"type": "error", "message": f"printed more than {MAX_STDOUT:,} bytes; stopped"})
    rc = p.wait()
    text = out.decode(errors="replace")
    if rc == 0:
        return _send({"type": "result", "content": text})
    why = f"killed by {signal.Signals(-rc).name}" if rc < 0 and -rc in signal.valid_signals() else f"exit code {rc}"
    _send({"type": "error", "message": f"{why}; output: {text.strip()[-300:]}"[:500]})


def main(name):
    manifest = json.loads((ROOT / name / "agent.json").read_text())
    request = json.loads(sys.stdin.readline())
    if "command" in manifest:
        return run_command(name, manifest, request)
    module, func = manifest["entry"].split(":")
    sys.path.insert(0, str(ROOT / name))
    sys.stdout = sys.stderr  # stray prints from the agent go to the log, never into the protocol stream
    try:
        result = getattr(importlib.import_module(module), func)(request["task"], request.get("inputs") or {}, Ctx())
        _send({"type": "result", "content": str(result)})
    except Exception as e:
        traceback.print_exc()
        _send({"type": "error", "message": f"{type(e).__name__}: {e}"[:500]})


if __name__ == "__main__":
    main(sys.argv[1])
