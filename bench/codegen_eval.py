"""Evaluation of the codegen guards (Loop 41): how much do they help a weak model write programs that do the job?

The codegen agent is Haiku at low effort (bench/agents/haiku-ratfor) writing the binning role (roles/binning) in
Ratfor, the stand-in for a language it does not know well. The same specs run under each guard setting:
  G0  compile only: the first program that builds is taken
  G1  + examples: the examples are run, a wrong answer goes back with its input and expected output
  G2  + hidden checks: many inputs the agent never sees, only counts and kinds of mistakes go back
Then the program each setting accepted is judged on an AUDIT set it never saw (other seeds, 1,000 dies). The guards
are measured by that, not by what they said:
  correct   accepted and right on every audit die
  false ok  accepted but wrong on the audit set: a guard let it through
  not built no program accepted within the attempts
The prompt is the same in every setting (examples included); only the checks differ.

  python bench/codegen_eval.py [--specs 6] [--repeats 2] [--guards G0,G1,G2] [--attempts 5] [--jobs 3]
                               [--out docs/eval_codegen.json]
Needs the gateway (LLM_BASE_URL, default :8100) and Docker with the sandbox image.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from openai import OpenAI  # noqa: E402

from agent import codegen, delegate  # noqa: E402
from agent.loop import Context  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402

GUARDS = {"G0": (), "G1": ("public",), "G2": ("public", "hidden")}
AUDIT = (100, 40, 25)  # seed base, inputs, dies per input
MODEL = os.environ.get("CODEGEN_MODEL", "claude-haiku-5-5")
EFFORT = "low"
TOOLCHAINS = ROOT / "tests" / "fixtures" / "toolchains"


def audit(box, tc, role, spec, code, name, seed):
    """-> (dies right, dies) of a program on the audit set."""
    ok, errs, bin = codegen.compile_source(box, tc, code.encode(), name)
    cases = role.cases(spec, seed, *AUDIT[1:])
    total = sum(len(c["categories"]) for c in cases)
    if not ok:
        return 0, total, Counter()
    res = codegen.run_cases(box, tc, bin, cases, name)
    right, kinds = 0, Counter()
    for c in cases:
        r = res[c["id"]]
        j = role.judge(spec, c, r["out"]) if r["rc"] == 0 else None
        if j is None or j["format"]:
            kinds["crash/format"] += len(c["categories"])
            continue
        right += j["total"] - len(j["wrong"])
        kinds.update(w["category"] for w in j["wrong"])
    return right, total, kinds


def one(args, client, agents_dir, spec_seed, g, rep, log):
    role = codegen.discover_roles()["binning"]
    spec = role.random_spec(spec_seed)
    ws = Path(tempfile.mkdtemp(prefix=f"cg-{spec_seed}-{g}-{rep}-"))
    sb = Sandbox(ws, mounts={agents_dir: delegate.MOUNT, TOOLCHAINS: codegen.MOUNT})
    calls = []

    def llm(msgs):
        t = time.time()
        text = client.chat.completions.create(model=MODEL, messages=msgs,
                                              extra_body={"reasoning_effort": EFFORT}).choices[0].message.content
        calls.append(time.time() - t)
        return text
    ctx = Context(sandbox=sb, agents=delegate.discover(agents_dir), toolchains=codegen.discover(TOOLCHAINS),
                  roles={"binning": role}, llm=llm)
    t0 = time.time()
    try:
        r = codegen.loop(ctx, "ratfor", "", f"s{spec_seed}", args.attempts, role="binning", spec=spec,
                         guards=GUARDS[g])
        hist = r["history"]
        stages = []
        for h in hist:
            e = h["errors"]
            stages.append("ok" if h["ok"] else e[0]["stage"] if e[0]["stage"] != "test" else
                          ("examples" if e[0]["message"].startswith("example") else "hidden"))
        accepted = r["stop"] is None
        box = sb.fresh()
        try:
            right, total, kinds = audit(box, ctx.toolchains["ratfor"], role, spec, hist[-1]["code"], "audit",
                                        AUDIT[0] + spec_seed) if hist else (0, 1, Counter())
        finally:
            box.close()
        row = {"spec": spec_seed, "guards": g, "rep": rep, "tests": len(spec["tests"]), "accepted": accepted,
               "attempts": len(hist), "stages": stages, "audit_right": right, "audit_total": total,
               "audit_wrong_kinds": dict(kinds), "stop": r["stop"], "llm_s": round(sum(calls), 1),
               "llm_calls": len(calls), "seconds": round(time.time() - t0, 1),
               "final_code": hist[-1]["code"] if hist else None}
    except Exception as e:
        row = {"spec": spec_seed, "guards": g, "rep": rep, "error": f"{type(e).__name__}: {e}"[:500]}
    finally:
        sb.close()
        shutil.rmtree(ws, ignore_errors=True)
    log(row)
    return row


def table(rows):
    out = ["| guards | runs | correct | false ok | not built | audit dies right | attempts (avg) | caught by |",
           "|---|---|---|---|---|---|---|---|"]
    for g in GUARDS:
        rs = [r for r in rows if r["guards"] == g and "error" not in r]
        if not rs:
            continue
        correct = sum(r["accepted"] and r["audit_right"] == r["audit_total"] for r in rs)
        false_ok = sum(r["accepted"] and r["audit_right"] < r["audit_total"] for r in rs)
        acc = [r for r in rs if r["accepted"]]
        dies = sum(r["audit_right"] for r in acc), sum(r["audit_total"] for r in acc)
        caught = Counter(s for r in rs for s in r["stages"] if s != "ok")
        out.append(f"| {g} | {len(rs)} | {correct} | {false_ok} | {len(rs) - len(acc)} | "
                   f"{dies[0] / dies[1]:.1%} of accepted |" if dies[1] else f"| {g} | {len(rs)} | {correct} | "
                   f"{false_ok} | {len(rs) - len(acc)} | - |")
        out[-1] += f" {sum(r['attempts'] for r in rs) / len(rs):.1f} | " + \
            ", ".join(f"{k} {v}" for k, v in caught.most_common()) + " |"
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--specs", type=int, default=6)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--guards", default="G0,G1,G2")
    ap.add_argument("--attempts", type=int, default=5)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--out", default=str(ROOT / "docs" / "eval_codegen.json"))
    args = ap.parse_args()
    client = OpenAI(base_url=os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8100/v1"), api_key="x",
                    timeout=300, max_retries=1)
    agents_dir = Path(tempfile.mkdtemp(prefix="cg-agents-"))
    shutil.copy(ROOT / "agents" / "_runner.py", agents_dir)
    shutil.copytree(ROOT / "bench" / "agents" / "haiku-ratfor", agents_dir / "haiku-ratfor")
    lock, rows = threading.Lock(), []

    def log(row):
        with lock:
            rows.append(row)
            short = {k: row.get(k) for k in ("spec", "guards", "rep", "accepted", "attempts", "stages",
                                              "audit_right", "audit_total", "error")}
            print(json.dumps(short, ensure_ascii=False), flush=True)
    jobs = [(s, g, rep) for s in range(args.specs) for rep in range(args.repeats) for g in args.guards.split(",")]
    with ThreadPoolExecutor(args.jobs) as ex:
        list(ex.map(lambda j: one(args, client, agents_dir, *j, log), jobs))
    rows.sort(key=lambda r: (r["spec"], r["guards"], r["rep"]))
    Path(args.out).write_text(json.dumps({"model": MODEL, "effort": EFFORT, "attempts": args.attempts,
                                          "audit": AUDIT, "rows": rows}, ensure_ascii=False, indent=1))
    print(table(rows))


if __name__ == "__main__":
    main()
