"""Evaluation of the codegen guards (Loops 41-42): how much do they help a weak model write programs that do the job?

The codegen agent is Haiku at low effort (bench/agents/haiku-<language>) writing a role (roles/binning or
roles/wafermap) in a language it does not know well: Ratfor, real but rare, or (Loop 43) Hesk, made up for this repo
and known to the model only from its one-page reference. Each spec runs under each guard setting:
  G0  compile only: the first program that builds is taken
  G1  + examples: the examples are run, a wrong answer goes back with its input and expected output
  G2  + hidden checks: many inputs the agent never sees, only counts and kinds of mistakes go back
Paired (Loop 42): the settings of one spec and repeat share the model's reply to every prompt they have in common
(the first one always; a retry after the same compile errors too), so they differ only where their guards said
something different.
Independent arms (Loop 41) could not tell the guards apart: at a 1-3% first-attempt error rate they mostly never
had anything to catch.
Then the program each setting accepted is judged on an AUDIT set it never saw (other seeds; wafermap: other real
maps, and apart from them the role's probe maps, which G2's hidden checks also use: reported in their own column).
The guards are measured by that, not by what they said:
  correct   accepted and right on every audit item (real maps)
  false ok  accepted but wrong on the audit set: a guard let it through
  not built no program accepted within the attempts
A false ok program is also replayed through G1 and G2 (would they have caught it).

  python bench/codegen_eval.py [--role binning|wafermap] [--language ratfor|hesk] [--specs 8] [--repeats 3]
                               [--guards G0,G1,G2]
                               [--attempts 5] [--jobs 4] [--maps bench/data/wm811k_sample.json.gz]
                               [--out docs/eval_codegen_<role>[_<language>].json]
Needs the gateway (LLM_BASE_URL, default :8100) and Docker with the sandbox image. wafermap needs the WM-811K
sample (bench/data/, not in the repo: a stratified sample of Kaggle "qingyi/wm811k-wafer-map").
"""
import argparse
import gzip
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent import codegen, delegate  # noqa: E402
from agent.loop import Context  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402

GUARDS = {"G0": (), "G1": ("public",), "G2": ("public", "hidden")}
AUDIT = {"binning": (40, 25), "wafermap": (150, None)}  # inputs, items per input
MODEL = os.environ.get("CODEGEN_MODEL", "claude-haiku-5-5")
EFFORT = "low"
TOOLCHAINS = ROOT / "tests" / "fixtures" / "toolchains"


def audit_seed(spec_seed):
    return 300 + 3 * spec_seed  # 0 mod 3: wafermap's audit maps are none of the examples' (1) or hidden checks' (2)


def audit(box, tc, role, spec, code, name, seed, size, probes=False):
    """-> (items right, items, Counter of wrong kinds) of a program on the audit set: the real items, or (probes)
    only the role's probe items."""
    cases = [c for c in role.cases(spec, seed, *size) if bool(c.get("probe")) == probes]
    total = sum(len(c["categories"]) for c in cases)
    if not cases:
        return 0, 0, Counter()
    ok, errs, bin = codegen.compile_source(box, tc, code.encode(), name)
    if not ok:
        return 0, total, Counter({"not built": total})
    try:
        res = codegen.run_cases(box, tc, bin, cases, name)
    except RuntimeError:  # the harness failed on it: the program's fault
        return 0, total, Counter({"crash/format": total})
    right, kinds = 0, Counter()
    for c in cases:
        r = res[c["id"]]
        try:
            j = role.judge(spec, c, r["out"]) if r["rc"] == 0 else None
        except Exception:
            j = None
        if j is None or j["format"]:
            kinds["crash/format"] += len(c["categories"])
            continue
        right += j["total"] - len(j["wrong"])
        kinds.update(w["category"] for w in j["wrong"])
    return right, total, kinds


class FirstAnswers:
    """The model's reply to a first prompt, asked once per (spec, repeat) and shared by every guard setting."""
    def __init__(self):
        self.lock, self.seen = threading.Lock(), {}

    def get(self, key, ask):
        with self.lock:
            f, mine = self.seen.get(key), False
            if f is None:
                f, mine = self.seen.setdefault(key, Future()), True
        if mine:
            try:
                f.set_result(ask())
            except Exception as e:
                f.set_exception(e)
        return f.result()


TESTED = ("ok", "examples", "hidden", "test")  # stage() of a program that built


def stage(h):
    if h["ok"]:
        return "ok"
    e = h["errors"][0]
    if e["stage"] != "test":
        return e["stage"]
    return "examples" if e["message"].startswith("example") else \
        "hidden" if e["message"].startswith("hidden checks") else "test"


def one(args, client, firsts, agents_dir, role_name, spec_seed, g, rep, log):
    role = codegen.discover_roles()[role_name]
    if role_name == "wafermap":
        role.use_maps(MAPS)
    spec = role.random_spec(spec_seed)
    ws = Path(tempfile.mkdtemp(prefix=f"cg-{spec_seed}-{g}-{rep}-"))
    sb = Sandbox(ws, mounts={agents_dir: delegate.MOUNT, TOOLCHAINS: codegen.MOUNT})
    calls = []

    def llm(msgs):
        def ask():
            t = time.time()
            text = client.chat.completions.create(model=MODEL, messages=msgs,
                                                  extra_body={"reasoning_effort": EFFORT}).choices[0].message.content
            calls.append(time.time() - t)
            return text
        # the same prompt in another setting (the first one; a retry after the same compile errors): its reply
        return firsts.get((role_name, spec_seed, rep, json.dumps(msgs)), ask)
    ctx = Context(sandbox=sb, agents=delegate.discover(agents_dir), toolchains=codegen.discover(TOOLCHAINS),
                  roles={role_name: role}, llm=llm)
    tc, t0, size = ctx.toolchains[args.language], time.time(), AUDIT[role_name]
    try:
        r = codegen.loop(ctx, args.language, "", f"s{spec_seed}", args.attempts, role=role_name, spec=spec,
                         guards=GUARDS[g])
        hist, accepted = r["history"], r["stop"] is None
        box = sb.fresh()
        try:
            right, total, kinds = audit(box, tc, role, spec, hist[-1]["code"], "audit", audit_seed(spec_seed), size) \
                if hist else (0, 1, Counter())
            p_right, p_total, p_kinds = audit(box, tc, role, spec, hist[-1]["code"], "probe", audit_seed(spec_seed),
                                              size, probes=True) if hist else (0, 0, Counter())
            first = audit(box, tc, role, spec, hist[0]["code"], "first", audit_seed(spec_seed), size)[:2] \
                if hist else (0, 1)
            replay = {}
            if accepted and (right < total or p_right < p_total):  # a false ok: would the others have caught it?
                ok, _, bin = codegen.compile_source(box, tc, hist[-1]["code"].encode(), "replay")
                for name, gs in GUARDS.items():
                    if gs:
                        replay[name] = bool(codegen.test_program(box, tc, bin, role, spec, gs, "r" + name)[0])
        finally:
            box.close()
        row = {"role": role_name, "spec": spec_seed, "guards": g, "rep": rep, "accepted": accepted,
               "attempts": len(hist), "stages": [stage(h) for h in hist], "audit_right": right, "audit_total": total,
               "audit_wrong_kinds": dict(kinds), "probe_right": p_right, "probe_total": p_total,
               "probe_wrong_kinds": dict(p_kinds), "first_right": first[0] == first[1], "replay": replay,
               "stop": r["stop"], "llm_s": round(sum(calls), 1), "llm_calls": len(calls),
               "seconds": round(time.time() - t0, 1), "final_code": hist[-1]["code"] if hist else None}
    except Exception as e:  # the agent or the gateway failed: not a guard result, kept apart in the table
        row = {"role": role_name, "spec": spec_seed, "guards": g, "rep": rep, "error": f"{type(e).__name__}: {e}"[:500]}
    finally:
        sb.close()
        shutil.rmtree(ws, ignore_errors=True)
    log(row)
    return row


def table(rows):
    ok_rows = [r for r in rows if "error" not in r]
    pairs = {(r["spec"], r["rep"]): r["first_right"] for r in ok_rows}
    unbuilt = {(r["spec"], r["rep"]): r["stages"][0] not in TESTED for r in ok_rows if r["stages"]}
    out = [f"first answers wrong: {sum(not v for v in pairs.values())}/{len(pairs)}, of them not built: "
           f"{sum(unbuilt.values())} (shared by the settings); errors (agent/gateway): {len(rows) - len(ok_rows)}", "",
           "| guards | runs | correct | false ok | not built | items right (accepted) | wrong on probes only | "
           "attempts (avg) | caught by |", "|---|---|---|---|---|---|---|---|---|"]
    for g in GUARDS:
        rs = [r for r in ok_rows if r["guards"] == g]
        if not rs:
            continue
        acc = [r for r in rs if r["accepted"]]
        correct = sum(r["audit_right"] == r["audit_total"] for r in acc)
        items = sum(r["audit_right"] for r in acc), sum(r["audit_total"] for r in acc)
        caught = Counter(s for r in rs for s in r["stages"] if s != "ok")
        probe_only = sum(r["audit_right"] == r["audit_total"] and r.get("probe_right", 0) < r.get("probe_total", 0)
                         for r in acc)
        out.append(f"| {g} | {len(rs)} | {correct} | {len(acc) - correct} | {len(rs) - len(acc)} | "
                   + (f"{items[0] / items[1]:.1%}" if items[1] else "-")
                   + f" | {probe_only} | {sum(r['attempts'] for r in rs) / len(rs):.1f} | "
                   + (", ".join(f"{k} {v}" for k, v in caught.most_common()) or "-") + " |")
    return "\n".join(out)


MAPS = []


def load_maps(path):
    d = json.load(gzip.open(path, "rt"))
    return [[[int(ch) for ch in row] for row in x["rows"]] for x in d]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default="binning", choices=sorted(AUDIT))
    ap.add_argument("--language", default="ratfor", choices=("ratfor", "hesk"))
    ap.add_argument("--specs", type=int, default=8)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--guards", default="G0,G1,G2")
    ap.add_argument("--attempts", type=int, default=5)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--maps", default=str(ROOT / "bench" / "data" / "wm811k_sample.json.gz"))
    ap.add_argument("--out")
    args = ap.parse_args()
    from openai import OpenAI
    if args.role == "wafermap":
        MAPS[:] = load_maps(args.maps)
    client = OpenAI(base_url=os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8100/v1"), api_key="x",
                    timeout=300, max_retries=1)
    agents_dir = Path(tempfile.mkdtemp(prefix="cg-agents-"))
    shutil.copy(ROOT / "agents" / "_runner.py", agents_dir)
    shutil.copytree(ROOT / "bench" / "agents" / f"haiku-{args.language}", agents_dir / f"haiku-{args.language}")
    lock, rows, firsts = threading.Lock(), [], FirstAnswers()

    def log(row):
        with lock:
            rows.append(row)
            short = {k: row.get(k) for k in ("spec", "guards", "rep", "accepted", "attempts", "stages",
                                              "audit_right", "audit_total", "first_right", "replay", "error")}
            print(json.dumps(short, ensure_ascii=False), flush=True)
    jobs = [(s, g, rep) for s in range(args.specs) for rep in range(args.repeats) for g in args.guards.split(",")]
    with ThreadPoolExecutor(args.jobs) as ex:
        list(ex.map(lambda j: one(args, client, firsts, agents_dir, args.role, *j, log), jobs))
    rows.sort(key=lambda r: (r["spec"], r["guards"], r["rep"]))
    lang = "" if args.language == "ratfor" else "_" + args.language
    out = Path(args.out or ROOT / "docs" / f"eval_codegen_{args.role}{lang}.json")
    out.write_text(json.dumps({"role": args.role, "language": args.language, "model": MODEL, "effort": EFFORT, "attempts": args.attempts,
                               "paired": True, "audit": AUDIT[args.role], "rows": rows}, ensure_ascii=False, indent=1))
    print(table(rows))


if __name__ == "__main__":
    main()
