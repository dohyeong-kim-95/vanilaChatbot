"""Benchmark T1 (docs/research/R7 §6): mock STDF wafer-sort files -> lot yield, worst wafer, top failing test.
The agent runs with the server's sandbox setup (uploads read-only, the project's cache, /vtools) and writes
/workspace/outputs/answers.json; the checker compares it with truth.json, which never enters the sandbox.

  python bench/t1.py [--k 3] [--seed 7] [--radius 36] [--tools ours|plain] [--effort low] [--keep]
     ours:  bash, files, plan, run_long, ingest_log, log_query (what the server offers for this)
     plain: bash, files, plan (the same image: python, polars, duckdb are there; no tools of ours)
Needs the gateway (LLM_BASE_URL, default :8100) and Docker with the sandbox image (agent/sandbox.py IMAGE).
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
from openai import OpenAI  # noqa: E402

from agent.loop import Context, run  # noqa: E402
from agent.sandbox import VTOOLS, Sandbox  # noqa: E402
from agent.userstore import UserStore  # noqa: E402
from stdf_gen import generate  # noqa: E402

TASK = ("uploads/lots 폴더에 웨이퍼 소트 STDF 파일들이 있어요. lot별 수율(hard bin 1인 die의 비율, %, 소수 셋째 자리까지), "
        "수율이 가장 낮은 웨이퍼(lot/wafer 형식, 예: L001A/W03), 가장 많은 die를 fail시킨 test 번호를 구해서 "
        "/workspace/outputs/answers.json에 {\"lot_yield\": {\"<lot>\": <pct>}, \"worst_wafer\": \"<lot>/<wafer>\", "
        "\"top_fail_test\": <test_num>} 형식으로 저장해 주세요.")
TOOLS = {"ours": ["bash", "read_file", "write_file", "edit_file", "save_note", "update_plan", "run_long", "ingest_log",
                  "log_query"],
         "plain": ["bash", "read_file", "write_file", "edit_file", "save_note", "update_plan"]}


class Meter:
    """Counts model calls and tokens around an OpenAI client."""
    def __init__(self, client):
        self.c, self.calls, self.inp, self.out = client, 0, 0, 0
        self.chat, self.models = self, client.models

    @property
    def completions(self):
        return self

    def create(self, **kw):
        r = self.c.chat.completions.create(**kw)
        self.calls += 1
        if r.usage:
            self.inp += r.usage.prompt_tokens or 0
            self.out += r.usage.completion_tokens or 0
        return r


def check(answers, truth):
    got = {}
    ly = answers.get("lot_yield") if isinstance(answers, dict) else None
    got["lot_yield"] = isinstance(ly, dict) and set(ly) == set(truth["lot_yield"]) and all(
        isinstance(ly[k], (int, float)) and abs(ly[k] - v) <= 0.1 for k, v in truth["lot_yield"].items())
    got["worst_wafer"] = isinstance(answers, dict) and str(answers.get("worst_wafer", "")).strip() == truth["worst_wafer"]
    try:
        got["top_fail_test"] = int(answers.get("top_fail_test")) == truth["top_fail_test"]
    except (TypeError, ValueError, AttributeError):
        got["top_fail_test"] = False
    return got


def once(i, a, client, base):
    root = base / f"run{i}"
    data = base / "data"
    store = UserStore(root / "store")
    store.create_project("bench", "t1")
    for f in sorted(data.glob("*.stdf")):  # as the UI would: in parts
        b = store.begin_upload("bench", "t1", f"lots/{f.name}", f.stat().st_size)
        with open(f, "rb") as fh:
            off = 0
            while chunk := fh.read(16 << 20):
                store.upload_part("bench", "t1", b["upload_id"], off, chunk)
                off += len(chunk)
        store.finish_upload("bench", "t1", b["upload_id"])
    ws, _ = store.checkout("bench", "t1", root / "run")
    sb = Sandbox(ws, readonly=("uploads",), mounts={VTOOLS: "/vtools"},
                 rw_mounts={store.cache_dir("bench", "t1"): "/workspace/cache"})
    m, events, t = Meter(client), [], time.time()
    try:
        final, msgs = run(TASK, Context(sandbox=sb), tool_names=TOOLS[a.tools], client=m, emit=events.append,
                          log=lambda *_: None, max_steps=40, effort=a.effort or None)
    except Exception as e:
        final, msgs = f"ERROR {type(e).__name__}: {e}", []
    finally:
        wall = time.time() - t
        sb.close()
    try:
        answers = json.loads((ws / "outputs" / "answers.json").read_text())
    except (OSError, ValueError):
        answers = None
    got = check(answers, json.loads((base / "data.truth.json").read_text()))
    calls = Counter(tc["function"]["name"] for mm in msgs if mm.get("role") == "assistant" for tc in mm.get("tool_calls") or [])
    r = {"run": i + 1, "pass": all(got.values()), "items": got, "wall_s": round(wall), "model_calls": m.calls,
         "tokens_in": m.inp, "tokens_out": m.out, "tool_calls": dict(calls), "final": str(final)[:200]}
    print(json.dumps(r, ensure_ascii=False), flush=True)
    if not a.keep:
        shutil.rmtree(root, ignore_errors=True)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--radius", type=int, default=36)
    ap.add_argument("--tools", choices=list(TOOLS), default="ours")
    ap.add_argument("--effort", default="low", help="as the server (AGENT_EFFORT, deploy default low); '' = unset")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    base = Path(tempfile.mkdtemp(prefix="t1-"))
    truth = generate(base / "data", seed=a.seed, radius=a.radius)
    (base / "data.truth.json").write_text(json.dumps(truth))
    size = sum(f.stat().st_size for f in (base / "data").glob("*.stdf"))
    print(f"T1 seed={a.seed} tools={a.tools} effort={a.effort or '-'}: {len(truth['files'])} files, {size / 2**20:.0f} MB, {truth['parts']} parts",
          flush=True)
    client = OpenAI(base_url=os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8100/v1"),
                    api_key=os.environ.get("LLM_API_KEY", "dummy"), max_retries=6)
    rs = [once(i, a, client, base) for i in range(a.k)]
    print(f"T1 {a.tools} effort={a.effort or '-'}: pass {sum(r['pass'] for r in rs)}/{len(rs)}, median wall "
          f"{sorted(r['wall_s'] for r in rs)[len(rs) // 2]} s")
    if not a.keep:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
