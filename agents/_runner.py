"""Runs one connected agent inside the sandbox (mounted read-only at /agents). Standard library only.

Protocol (JSON lines; stdin from the host, stdout to the host):
  host  -> {"task": str, "inputs": {...}}                       first line
  agent -> {"type": "progress", "message": str}
  agent -> {"type": "llm", "id": n, "messages": [...]}           host answers {"id": n, "content": str} or {"id": n, "error": str}
  agent -> {"type": "result", "content": str}                     last line
  agent -> {"type": "error", "message": str}
Anything else the agent prints is passed to the host as ordinary log lines (shown only in failures).
An agent package exposes run(task, inputs, ctx) -> str; ctx.progress(msg), ctx.llm(messages) -> str, ctx.workspace.
"""
import importlib
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
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


def main(name):
    manifest = json.loads((ROOT / name / "agent.json").read_text())
    module, func = manifest["entry"].split(":")
    sys.path.insert(0, str(ROOT / name))
    sys.stdout = sys.stderr  # stray prints from the agent go to the log, never into the protocol stream
    request = json.loads(sys.stdin.readline())
    try:
        result = getattr(importlib.import_module(module), func)(request["task"], request.get("inputs") or {}, Ctx())
        _send({"type": "result", "content": str(result)})
    except Exception as e:
        traceback.print_exc()
        _send({"type": "error", "message": f"{type(e).__name__}: {e}"[:500]})


if __name__ == "__main__":
    main(sys.argv[1])
