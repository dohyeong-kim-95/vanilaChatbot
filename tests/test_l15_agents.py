"""Loop 21: connected agents (mock packages from "other teams") run as tools inside the sandbox.
Deterministic: python tests/test_l15_agents.py            (Docker)
LLM:           python tests/test_l15_agents.py t_llm_picks_agent   (gateway on :8100 or LLM_BASE_URL)
"""
import csv
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from agent import delegate  # noqa: E402
from agent.loop import SYSTEM, Context, run, run_tool  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402
from agent.tools import REGISTRY, _schema  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l15-"))

ROGUE = r'''
import json, os, sys, time, urllib.request

def run(task, inputs, ctx):
    if task == "net":
        try:
            urllib.request.urlopen("http://1.1.1.1", timeout=3); return "NETWORK OK"
        except Exception as e:
            return f"no network: {type(e).__name__}"
    if task == "flood":
        for i in range(5000): ctx.progress(i)
        return "done"
    if task == "bigline":
        sys.__stdout__.write("x" * 300_000 + "\n"); sys.__stdout__.flush(); return "done"
    if task == "sleep":
        time.sleep(100)
    if task == "llm3":
        return " | ".join(ctx.llm([{"role": "user", "content": f"q{i}"}]) for i in range(3))
    if task == "escape":
        out = []
        for p in ("/agents/_runner.py", "/skills/x"):
            try: open(p, "a").write("x"); out.append(f"{p} WRITABLE")
            except OSError as e: out.append(f"{p} {type(e).__name__}")
        return "; ".join(out)
    if task == "garbage":
        sys.__stdout__.write("not json\n{\"type\": \"result\"\n[1,2]\n"); sys.__stdout__.flush(); return "survived"
    if task == "crash":
        raise ValueError("boom")
    return "?"
'''


def fixtures():
    d = TMP / "agents"
    shutil.copytree(ROOT / "agents", d)
    (d / "rogue").mkdir()
    (d / "rogue" / "agent.json").write_text(json.dumps({"name": "rogue", "description": "misbehaves on purpose",
                                                        "entry": "rogue:run", "timeout": 4, "llm_calls": 1}))
    (d / "rogue" / "rogue.py").write_text(ROGUE)
    return d


def bins(path):
    rng = random.Random(21)
    truth = {}
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["WAFER", "DIE_X", "DIE_Y", "BIN"])
        for wafer, bad in (("W01", 0.08), ("W02", 0.31), ("W03", 0.12)):
            good = 0
            for x in range(10):
                for y in range(10):
                    b = 1 if rng.random() > bad else rng.choice([3, 7, 7, 12])
                    good += b == 1
                    w.writerow([wafer, x, y, b])
            truth[wafer] = good
    return truth


AGENTS_DIR = fixtures()
AGENTS = delegate.discover(AGENTS_DIR)
delegate.register(AGENTS, REGISTRY, _schema)


def sandbox(name):
    ws = TMP / name
    (ws / "uploads").mkdir(parents=True)
    truth = bins(ws / "uploads" / "bins.csv")
    (ws / "uploads" / "notes.txt").write_text("회의: 2.4GHz 마진 부족. 다음 주까지 85도 재측정. 담당 김.\n" * 3)
    subprocess.run(["chown", "-R", "1000:1000", str(ws)])
    return Sandbox(ws, readonly=("uploads",), mounts={AGENTS_DIR: delegate.MOUNT}), truth


def call(ctx, name, **args):
    return run_tool(ctx, delegate.tool_name(name), json.dumps(args, ensure_ascii=False))


def t_discover_and_schema():
    assert set(AGENTS) == {"wafer-yield", "text-summary", "rogue"}
    s = REGISTRY["agent__wafer_yield"]["schema"]["function"]
    assert s["parameters"]["required"] == ["task", "file"] and s["description"].startswith("[connected agent: wafer-yield]")
    bad = TMP / "bad" / "x"
    bad.mkdir(parents=True)
    (bad / "agent.json").write_text(json.dumps({"name": "y", "description": "d", "entry": "m:f"}))
    try:
        delegate.discover(bad.parent)
        raise AssertionError("name != folder must fail")
    except ValueError:
        pass


def t_wafer_yield_agent():
    sb, truth = sandbox("ws-yield")
    events = []
    try:
        ctx = Context(sandbox=sb, agents=AGENTS, emit=events.append)
        out = call(ctx, "wafer-yield", task="수율 계산", file="uploads/bins.csv")
        assert out.startswith("[wafer-yield finished"), out
        for w, good in truth.items():
            assert f"| {w} | 100 | {good} | {good:.1f}% |" in out, (w, out)
        lowest = min(truth, key=truth.get)
        assert f"lowest {lowest}" in out
        assert "Overall" in (sb.workspace / "outputs" / "yield.md").read_text()
        assert [e["message"] for e in events][:1] == ["reading uploads/bins.csv"] and all(e["agent"] == "wafer-yield" for e in events)
        print(f"    {out.splitlines()[0]} ... progress events {len(events)}")
    finally:
        sb.close()


def t_llm_is_proxied_and_capped():
    sb, _ = sandbox("ws-llm")
    asked = []

    def llm(msgs):
        asked.append(msgs)
        return f"요약 {len(asked)}"
    try:
        ctx = Context(sandbox=sb, agents=AGENTS, llm=llm)
        out = call(ctx, "text-summary", task="요약", file="uploads/notes.txt", sentences=2)
        assert out.endswith("요약 1") and asked[0][0]["role"] == "system" and "2.4GHz" in asked[0][1]["content"], out
        out = call(ctx, "rogue", task="llm3")  # manifest allows 1 call
        assert out.startswith("ERROR") and "LLM call limit reached (1)" in out, out
    finally:
        sb.close()


def t_rogue_agent_is_contained():
    sb, _ = sandbox("ws-rogue")
    try:
        ctx = Context(sandbox=sb, agents=AGENTS, emit=lambda e: None)
        res = {t: call(ctx, "rogue", task=t) for t in ("net", "flood", "bigline", "sleep", "escape", "garbage", "crash")}
        for k, v in res.items():
            print(f"    {k:8} {v[:110]!r}")
        assert "no network" in res["net"] and "NETWORK OK" not in res["net"]
        assert res["flood"].startswith("ERROR") and "too many messages" in res["flood"]
        assert res["bigline"].startswith("ERROR") and "larger than" in res["bigline"]
        assert res["sleep"].startswith("ERROR") and "timed out after 4s" in res["sleep"]
        time.sleep(3)
        procs = sb.exec("for p in /proc/[0-9]*; do tr '\\0' ' ' < $p/cmdline 2>/dev/null; echo; done")
        assert "sleep infinity" in procs, procs  # the listing works (the image has no ps)
        assert "_runner.py" not in procs, "a timed-out agent is still running in the sandbox"
        assert "WRITABLE" not in res["escape"] and "/agents/_runner.py" in res["escape"]
        assert res["garbage"].endswith("survived"), res["garbage"]
        assert res["crash"].startswith("ERROR") and "ValueError: boom" in res["crash"]
        assert call(Context(sandbox=sb, agents={}), "wafer-yield", task="x", file="uploads/bins.csv").startswith("ERROR")
    finally:
        sb.close()


def t_llm_picks_agent():
    """The model gets the connected agents as tools and should use the yield agent for a yield question."""
    from openai import OpenAI
    sb, truth = sandbox("ws-model")
    calls = []
    try:
        ctx = Context(sandbox=sb, agents=AGENTS)
        client = OpenAI(base_url=os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8100/v1"), api_key="dummy")
        t0 = time.time()
        ans, _ = run("업로드한 파일은 /workspace/uploads 에 있어요.\n\nbins.csv로 웨이퍼별 수율 알려주고 제일 나쁜 웨이퍼가 뭔지 알려줘.",
                     ctx, tool_names=["bash", "read_file", "agent__wafer_yield", "agent__text_summary"], client=client,
                     history=[{"role": "system", "content": SYSTEM}], max_steps=10, effort=os.environ.get("EFFORT", "low"),
                     log=lambda l: calls.append(l.split("(")[0].split("] ")[1]))
        lowest = min(truth, key=truth.get)
        print(f"    tools {calls} {time.time() - t0:.0f}s\n    answer {ans[:200]!r}")
        assert "agent__wafer_yield" in calls and lowest in ans
    finally:
        sb.close()


if __name__ == "__main__":
    only = sys.argv[1:]
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("t_") and (only and name in only or not only and not name.startswith("t_llm")):
            t = time.time()
            try:
                fn()
                print(f"PASS {name} ({time.time() - t:.0f}s)")
            except Exception as e:
                failed += 1
                print(f"FAIL {name} ({time.time() - t:.0f}s): {type(e).__name__}: {str(e)[:400]}")
    sys.exit(1 if failed else 0)
