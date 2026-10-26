"""Loop 10: minimal task-suite eval -> pass@k, pass^k, steps, tokens, time. Gateway on :8100.
python tests/eval/run_eval.py [k] [workers]"""
import hashlib
import json
import os
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agent.loop import Context, run  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402

CODE = ["bash", "read_file", "write_file", "edit_file"]
SALES = "region,amount\n" + "".join(f"{['west', 'east', 'north'][i % 3]},{(i * 37) % 101 + 0.25}\n" for i in range(300))
WEST = round(sum((i * 37) % 101 + 0.25 for i in range(0, 300, 3)), 2)

TASKS = [
    {"id": "fix_bug", "files": {"calc.py": "def add(a, b):\n    return a - b\n",
                                "test_calc.py": "from calc import add\nassert add(2, 3) == 5\nprint('tests ok')\n"},
     "prompt": "python test_calc.py fails. Fix the bug and make it pass.",
     "check": "python test_calc.py | grep -q 'tests ok' && echo PASS"},
    {"id": "rename", "files": {
        "utils.py": "def fmt_price(x):\n    return f'${x:,.2f}'\n",
        "app.py": "from utils import fmt_price\n\ndef show(items):\n    return [fmt_price(i) for i in items]\n",
        "test_app.py": "from app import show\nassert show([1234.5]) == ['$1,234.50']\nprint('ok')\n"},
     "prompt": "Rename the function fmt_price to format_price everywhere in this project (definition and all "
               "uses, tests included). Keep tests passing (python test_app.py).",
     "check": "! grep -rq fmt_price --include=*.py . && python test_app.py | grep -q ok && "
              "grep -q 'def format_price' utils.py && echo PASS"},
    {"id": "csv_sum", "files": {"sales.csv": SALES},
     "prompt": "What is the total amount for region 'west' in sales.csv? Compute it exactly and write only the "
               "number, rounded to 2 decimals, to answer.txt.",
     "check": f"python -c \"import sys; v=float(open('answer.txt').read().strip()); "
              f"sys.exit(0 if abs(v-{WEST})<0.005 else 1)\" && echo PASS"},
    {"id": "scope", "files": {"README.md": "# Demo\nThis is teh demo project.\n",
                              "main.py": "print('hello')  # teh end\n", "LICENSE": "MIT\n"},
     "prompt": "Fix the typo 'teh' in README.md. Change nothing else.",
     "check": "grep -q 'This is the demo project.' README.md && echo PASS",
     "untouched": ["main.py", "LICENSE"]},
]


class Meter:
    """Counts LLM calls and usage around an OpenAI client (what `run` sees as client.chat.completions)."""
    def __init__(self, c):
        self.c, self.calls, self.prompt, self.completion = c, 0, 0, 0
        self.chat, self.completions, self.models = self, self, c.models

    def create(self, **kw):
        r = self.c.chat.completions.create(**kw)
        self.calls += 1
        if r.usage:
            self.prompt += r.usage.prompt_tokens or 0
            self.completion += r.usage.completion_tokens or 0
        return r


def one(task, i, tmp):
    sb = Sandbox(tmp / f"{task['id']}-{i}")
    for n, body in task["files"].items():
        (sb.workspace / n).write_text(body)
    sb.exec("chown -R 1000:1000 . 2>/dev/null; true")
    before = {n: hashlib.sha256((sb.workspace / n).read_bytes()).hexdigest() for n in task.get("untouched", [])}
    m = Meter(OpenAI(base_url=os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8100/v1"),
                     api_key=os.environ.get("LLM_API_KEY", "dummy")))
    tools, t = [], time.time()
    try:
        ans, msgs = run(task["prompt"], Context(sandbox=sb), tool_names=CODE, client=m, max_steps=25,
                        log=lambda l: tools.append(l.split("(")[0].split("] ")[1]))
        ok = sb.exec(task["check"]).rstrip().endswith("PASS")
        violated = [n for n, h in before.items() if hashlib.sha256((sb.workspace / n).read_bytes()).hexdigest() != h]
        return {"task": task["id"], "i": i, "pass": ok and not violated, "violations": violated,
                "llm_calls": m.calls, "tool_calls": len(tools), "prompt_tokens": m.prompt,
                "completion_tokens": m.completion, "seconds": round(time.time() - t), "error": None}
    except Exception as e:
        return {"task": task["id"], "i": i, "pass": False, "error": repr(e)[:200], "seconds": round(time.time() - t)}
    finally:
        sb.close()


def main(k=3, workers=4):
    tmp = Path(tempfile.mkdtemp(prefix="eval-"))
    jobs = [(t, i) for t in TASKS for i in range(k)]
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(lambda j: one(*j, tmp), jobs))
    print(f"{'task':10} {'pass':>6} {'pass@k':>7} {'pass^k':>7} {'llm':>5} {'tools':>6} {'tok_in':>8} {'tok_out':>8} {'sec':>5}")
    for t in TASKS:
        r = [x for x in res if x["task"] == t["id"]]
        ok = [x["pass"] for x in r]
        med = lambda key: statistics.median(x.get(key) or 0 for x in r)  # noqa: E731
        print(f"{t['id']:10} {sum(ok)}/{len(ok):<4} {int(any(ok)):>7} {int(all(ok)):>7} {med('llm_calls'):>5} "
              f"{med('tool_calls'):>6} {med('prompt_tokens'):>8} {med('completion_tokens'):>8} {med('seconds'):>5}")
    for x in res:
        if not x["pass"]:
            print("  fail:", x)
    out = ROOT / "docs" / "eval_results.json"
    out.write_text(json.dumps(res, indent=1))
    print("pass rate", sum(x["pass"] for x in res), "/", len(res), "->", out)


if __name__ == "__main__":
    main(*map(int, sys.argv[1:]))
