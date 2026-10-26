"""Loop 11: how to pick the right tool when 23 MCP servers expose ~130 tools.
Scores the first *catalog* tool call per query (name + key args) for several exposure strategies.
python tests/experiments/tool_selection.py [strategy ...]     (LLM_BASE_URL defaults to :8101 = API gateway)"""
import json
import math
import os
import random
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tool_catalog as tc  # noqa: E402

MODE = os.environ.get("CATALOG", "compact")  # compact (round 1) | realistic (round 2)
if MODE == "realistic":
    SERVERS, CATALOG = tc.realistic()
    QUERIES = tc.QUERIES + tc.QUERIES_MULTI
else:
    SERVERS, CATALOG, QUERIES = tc.SERVERS, tc.CATALOG, tc.QUERIES
BY_NAME = {t["function"]["name"]: t for t in CATALOG}

client = OpenAI(base_url=os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8101/v1"), api_key="dummy")
MODEL = os.environ.get("LLM_MODEL", "claude-haiku-5-5")
BASE = ("You are a company assistant connected to many tools (MCP servers). Fulfill the request by calling the most "
        "appropriate tool with correct arguments. Do not ask questions; make reasonable assumptions "
        "(today is 2026-10-26).")


# ---------- BM25 over tool docs (name words + description + server summary + param names) ----------
def toks(s):
    return re.findall(r"[a-z0-9]+|[가-힣]+", s.lower().replace("_", " "))


DOCS = [toks(f"{t['function']['name']} {t['function']['description'][:300]} {SERVERS[t['function']['name'].split('__')[0]][0]} "
             f"{' '.join(t['function']['parameters']['properties'])}") for t in CATALOG]
DF = Counter(w for d in DOCS for w in set(d))
AVG = sum(map(len, DOCS)) / len(DOCS)


def bm25(query, k=5, k1=1.2, b=0.75):
    q = toks(query)
    scores = []
    for t, d in zip(CATALOG, DOCS):
        tf = Counter(d)
        s = sum(math.log(1 + (len(DOCS) - DF[w] + .5) / (DF[w] + .5)) * tf[w] * (k1 + 1)
                / (tf[w] + k1 * (1 - b + b * len(d) / AVG)) for w in q if w in tf)
        scores.append((s, t))
    return [t for s, t in sorted(scores, key=lambda x: -x[0])[:k] if s > 0]


def brief(t):
    return f"{t['function']['name']}: {t['function']['description'].split(chr(10))[0]}"


SEARCH = {"type": "function", "function": {
    "name": "search_tools", "description": "Search the company tool catalog (100+ tools on 20+ servers) by keywords. "
    "Write the query in English. Returns the best matching tools.", "parameters": {
        "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}
CALL = {"type": "function", "function": {
    "name": "call_tool", "description": "Call a catalog tool found with search_tools, by exact name.",
    "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}},
                   "required": ["name", "arguments"]}}}
LOAD = {"type": "function", "function": {
    "name": "load_server", "description": "Load all tools of one server so you can call them.",
    "parameters": {"type": "object", "properties": {"server": {"type": "string"}}, "required": ["server"]}}}
SERVER_LIST = "\n".join(f"- {s}: {d} ({len(ts)} tools)" for s, (d, ts) in SERVERS.items())


# ---------- strategies: (system, initial tools, meta handler) ----------
def strat(name, query, gold):
    if name == "all":
        return BASE, list(CATALOG), None
    if name == "all30":  # gold + 29 random distractors (seeded per query): what if we had fewer tools?
        rnd = random.Random(query)
        return BASE, rnd.sample([t for t in CATALOG if t["function"]["name"] != gold], 29) + [BY_NAME[gold]], None
    if name == "retrieve8":  # retrieve on the raw user message, expose top-8
        return BASE, bm25(query, 8), None
    if name == "search_load":  # meta search; found tools are added to `tools` (tools list changes per step)
        def meta(call, args, tools):
            found = bm25(args.get("query", ""), 5)
            tools += [t for t in found if t not in tools]
            return "Loaded (now callable):\n" + "\n".join(brief(t) for t in found) if found else "No matches."
        return BASE + " Use search_tools to find tools first.", [SEARCH], meta
    if name == "hybrid":  # retrieve top-8 on the raw message + search_tools as fallback for retrieval misses
        def meta(call, args, tools):
            found = bm25(args.get("query", ""), 5)
            tools += [t for t in found if t not in tools]
            return "Loaded (now callable):\n" + "\n".join(brief(t) for t in found) if found else "No matches."
        return (BASE + " If none of the given tools fits, use search_tools to find more.",
                bm25(query, 8) + [SEARCH], meta)
    if name == "search_call":  # meta search returns schemas as text; fixed tools=[search, call] -> stable prefix
        def meta(call, args, tools):
            found = bm25(args.get("query", ""), 5)
            return json.dumps([t["function"] for t in found], ensure_ascii=False) if found else "No matches."
        return BASE + " Use search_tools to find tools, then call_tool with the exact name.", [SEARCH, CALL], meta
    if name == "hierarchical":  # server list in system prompt, load a server's tools on demand
        def meta(call, args, tools):
            s = args.get("server", "")
            if s not in SERVERS:
                return f"Unknown server {s!r}."
            tools += [BY_NAME[f"{s}__{t[0]}"] for t in SERVERS[s][1] if BY_NAME[f"{s}__{t[0]}"] not in tools]
            return f"Loaded {len(SERVERS[s][1])} tools of {s}."
        return BASE + "\nServers (load one with load_server before using its tools):\n" + SERVER_LIST, [LOAD], meta
    raise ValueError(name)


READ = ("get_", "list_", "describe_", "search", "read_", "find_", "query_", "estimate_", "explain_")
SIM = os.environ.get("SIMULATE") == "1"  # round 3: answer read-only catalog calls and let the model continue


def check(name, args, gold, want):
    if name != gold:
        return False, f"wrong tool {name}"
    for k, v in want.items():
        if v.lower() not in str(args.get(k, "")).lower():
            return False, f"bad arg {k}={args.get(k)!r}"
    return True, ""


def one(strategy, q):
    query, gold, want = q
    system, tools, meta = strat(strategy, query, gold)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": query}]
    st = {"strategy": strategy, "query": query, "gold": gold, "steps": 0, "prompt": 0, "cached": 0, "out": 0,
          "n_tools_first": len(tools), "picked": None, "args": None, "ok": False, "why": "no tool call"}
    t0 = time.time()
    try:
        for _ in range(8 if SIM else 5):
            if not tools:
                st["why"] = "retrieval returned nothing"
                break
            r = client.chat.completions.create(model=MODEL, messages=msgs, tools=tools)
            st["steps"] += 1
            u = r.usage
            st["prompt"] += u.prompt_tokens
            st["cached"] += (u.prompt_tokens_details.cached_tokens if u.prompt_tokens_details else 0) or 0
            st["out"] += u.completion_tokens
            m = r.choices[0].message
            if not m.tool_calls:
                break
            msgs.append(m.model_dump(exclude_none=True))
            done = False
            for tc in m.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                name = tc.function.name
                if name == "call_tool":
                    name, args = args.get("name"), args.get("arguments") or {}
                    if isinstance(args, str):
                        args = json.loads(args)
                if name in ("search_tools", "load_server"):
                    out = meta(tc, args, tools)
                elif name in BY_NAME:
                    st["picked"], st["args"], (st["ok"], st["why"]) = name, args, check(name, args, gold, want)
                    action = name.split("__")[1]
                    if SIM and name != gold and action.startswith(READ):  # lookup first is fine: fake a result
                        st["lookups"] = st.get("lookups", []) + [name]
                        out = json.dumps({"simulated": True, "tool": name, "result": "lookup succeeded; the objects "
                                          "the user mentioned exist with exactly the names/ids they used"})
                    else:
                        if SIM and name != gold:
                            st["why"] = f"wrong ACTION {name}"  # a state-changing call on the wrong tool
                        done = True
                        break
                else:
                    out = f"ERROR: unknown tool {name!r}"
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": out})
            if done:
                break
    except Exception as e:
        st["why"] = f"error {type(e).__name__}: {str(e)[:120]}"
    st["sec"] = round(time.time() - t0, 1)
    return st


def main(strategies):
    rows = []
    print(f"catalog={MODE}: {len(CATALOG)} tools, ~{len(json.dumps(CATALOG)) // 4} tokens, {len(QUERIES)} queries")
    for s in strategies:
        with ThreadPoolExecutor(4) as ex:
            res = list(ex.map(lambda q: one(s, q), QUERIES))
        rows += res
        n = len(res)
        avg = lambda k: sum(r[k] for r in res) / n  # noqa: E731
        print(f"{s:13} acc {sum(r['ok'] for r in res):>2}/{n}  tools@1st {avg('n_tools_first'):5.0f}  steps {avg('steps'):.1f}"
              f"  prompt_tok {avg('prompt'):7.0f}  cached {avg('cached'):7.0f}  out_tok {avg('out'):5.0f}  sec {avg('sec'):5.1f}",
              flush=True)
        for r in res:
            if not r["ok"]:
                print(f"    ✗ {r['query'][:60]!r:64} gold={r['gold']} -> {r['why']}", flush=True)
    tag = os.environ.get("TAG", "")
    out = Path(__file__).resolve().parents[2] / "docs" / f"tool_selection_{MODE}{'_sim' if SIM else ''}{tag}.json"
    old = json.loads(out.read_text()) if out.exists() else []
    out.write_text(json.dumps([r for r in old if r["strategy"] not in strategies] + rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:] or ["all", "all30", "retrieve8", "search_load", "search_call", "hierarchical"])
