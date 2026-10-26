"""Loop 11 (integration): the hybrid ToolRouter inside the real agent loop, over the 154-tool realistic catalog.
Needs an OpenAI-compatible gateway (default :8101)."""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/experiments"))
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:8101/v1")
from openai import OpenAI  # noqa: E402

import tool_catalog as tc  # noqa: E402
from agent.loop import Context, run  # noqa: E402
from agent.tool_router import ToolRouter  # noqa: E402
from agent.tools import REGISTRY  # noqa: E402

SERVERS, CATALOG = tc.realistic()
for t in CATALOG:  # register as agent tools; results are simulated
    n = t["function"]["name"]
    REGISTRY[n] = {"schema": t, "approval": False,
                   "fn": lambda ctx, _n=n, **a: json.dumps({"ok": True, "tool": _n, "simulated": True})}
NAMES = [t["function"]["name"] for t in CATALOG]

# retrieval hits (en) and retrieval misses (ko) from the Loop 11 experiment
CASES = [q for q in tc.QUERIES + tc.QUERIES_MULTI if q[1] in (
    "jira__create_issue", "stripe__cancel_subscription", "slack__send_message",
    "gcal__find_free_time", "gdrive__search_files", "util__currency_convert", "slack_community__send_message")]


class Spy:
    def __init__(self):
        self.c = OpenAI(base_url=os.environ["LLM_BASE_URL"], api_key="dummy")
        self.chat, self.completions, self.models, self.sizes, self.prompt = self, self, self.c.models, [], 0

    def create(self, **kw):
        self.sizes.append(len(kw.get("tools") or []))
        r = self.c.chat.completions.create(**kw)
        self.prompt += r.usage.prompt_tokens
        return r


def t_router_in_agent_loop():
    ok = 0
    for query, gold, want in CASES:
        spy, calls = Spy(), []
        ctx = Context(router=ToolRouter(NAMES, k=8))
        run(query, ctx, client=spy, max_steps=6, log=lambda l: calls.append(l.split("] ", 1)[1].split("(")[0]))
        hit = gold in calls
        ok += hit
        print(f"    {'✓' if hit else '✗'} {query[:44]!r:48} calls={calls} tools/req={spy.sizes} prompt={spy.prompt}")
    assert ok == len(CASES), f"{ok}/{len(CASES)}"


if __name__ == "__main__":
    t = time.time()
    try:
        t_router_in_agent_loop(); print(f"PASS t_router_in_agent_loop ({time.time() - t:.0f}s)")
    except Exception as e:
        print("FAIL t_router_in_agent_loop", repr(e)[:400])
