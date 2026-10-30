"""eval v0 (docs/PROBLEM.md): monthly spending questions over card/bank exports. Gateway on :8100.
python tests/eval/finance/run.py [k] [workers] [--skills] [case_id ...]   -> docs/eval_finance_v0[_skills].json
--skills (Loop 15): skills list in the system prompt, use_skill tool, /skills mounted in the sandbox.
EFFORT=low|...|max: sent per request (reasoning_effort); unset = the gateway default. OUT=name: result file stem.
FOLLOWUP=1 (Loop 22): when the first answer is wrong, the "user" says it looks wrong once (same conversation); the
effort policy raises the effort for that turn. Recorded as pass2 / seconds2 / effort2."""
FOLLOWUP = "답이 틀린 것 같아요. 다시 확인해서 outputs/answer.json을 고쳐 주세요."
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from agent import skills  # noqa: E402
from agent.loop import SYSTEM, Context, run  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402
from cases import build, match  # noqa: E402
from tests.eval.run_eval import Meter  # noqa: E402

CODE = ["bash", "read_file", "write_file", "edit_file"]
TASK_PREFIX = "업로드한 파일은 /workspace/uploads 에 있어요. 결과물은 /workspace/outputs 에 저장해 주세요.\n\n"


def _grade(case, ws):
    try:
        got = json.loads((ws / "outputs/answer.json").read_text())["answer"]
    except Exception as e:
        return None, False, f"no answer.json: {type(e).__name__}"
    ok = match(case["expected"], got, case["tol"])
    why = None if ok else "wrong answer"
    if case["id"] == "a_report":
        png = ws / "outputs/chart.png"
        has_png = png.exists() and png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        has_md = (ws / "outputs/report.md").exists()
        if not (has_png and has_md):
            ok, why = False, f"report.md={has_md} chart.png={has_png}"
    return got, ok, why


def one(case, i, fixtures, tmp, use_skills=False):
    ws = tmp / f"{case['id']}-{i}"
    (ws / "uploads").mkdir(parents=True)
    (ws / "outputs").mkdir()
    for f in case["files"]:
        shutil.copy(fixtures / f, ws / "uploads" / f)
    os.system(f"chown -R 1000:1000 {ws}")
    sk = skills.discover() if use_skills else {}
    sb = Sandbox(ws, readonly=("uploads",), mounts={skills.DIR: skills.MOUNT} if use_skills else None)
    m = Meter(OpenAI(base_url=os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8100/v1"),
                     api_key=os.environ.get("LLM_API_KEY", "dummy")))
    tools, t = [], time.time()
    res = {"case": case["id"], "tag": case["tag"], "i": i, "expected": case["expected"]}
    try:
        system = "\n\n".join(filter(None, [SYSTEM, skills.prompt(sk)]))
        ctx = Context(sandbox=sb, skills=sk)
        kw = dict(tool_names=CODE + ["use_skill"] * use_skills, client=m, max_steps=30, effort=os.environ.get("EFFORT"),
                  log=lambda l: tools.append(l.split("(")[0].split("] ")[1]))
        final, msgs = run(TASK_PREFIX + case["prompt"], ctx, history=[{"role": "system", "content": system}], **kw)
        res.update(final=(final or "")[:300], llm_calls=m.calls, tool_calls=len(tools),
                   prompt_tokens=m.prompt, completion_tokens=m.completion)
        got, ok, why = _grade(case, ws)
        res.update(got=got, pass_=ok, why=why)
        if not ok and os.environ.get("FOLLOWUP"):
            t2 = time.time()
            res.update(pass2=False, effort2=None)  # a follow-up that raises still counts as not fixed
            ctx.effort_used = None  # so a follow-up that fails before its first model call reports no effort
            try:
                run(FOLLOWUP, ctx, history=msgs, **kw)
                got2, ok2, why2 = _grade(case, ws)
                res.update(got2=got2, pass2=ok2, why2=why2)
            except Exception as e:
                res.update(why2=f"error: {repr(e)[:200]}")
            res.update(seconds2=round(time.time() - t2), effort2=ctx.effort_used)
    except Exception as e:
        res.update(pass_=False, why=f"error: {repr(e)[:200]}")
    finally:
        res["seconds"] = round(time.time() - t)
        res["tools"] = tools
        sb.close()
    return res


def main(k=3, workers=4, only=(), use_skills=False):
    tmp = Path(tempfile.mkdtemp(prefix="feval-"))
    cases = [c for c in build(tmp / "fixtures") if not only or c["id"] in only]
    jobs = [(c, i) for c in cases for i in range(k)]
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(lambda j: one(*j, tmp / "fixtures", tmp, use_skills), jobs))
    print(f"{'case':16} {'tag':12} {'pass':>5} {'pass^k':>6} {'llm':>4} {'tools':>5} {'sec':>4}  failures")
    for c in cases:
        r = [x for x in res if x["case"] == c["id"]]
        ok = [x["pass_"] for x in r]
        med = lambda key: statistics.median(x.get(key) or 0 for x in r)  # noqa: E731
        whys = sorted({x["why"] for x in r if x["why"]})
        print(f"{c['id']:16} {c['tag']:12} {sum(ok)}/{len(ok):<3} {int(all(ok)):>6} {med('llm_calls'):>4} "
              f"{med('tool_calls'):>5} {med('seconds'):>4}  {'; '.join(whys)}")
    tags = sorted({c["tag"] for c in cases})
    print("by tag:", {t: f"{sum(x['pass_'] for x in res if x['tag'] == t)}/{sum(x['tag'] == t for x in res)}" for t in tags})
    n = sum(x["pass_"] for x in res)
    passk = sum(all(x["pass_"] for x in res if x["case"] == c["id"]) for c in cases)
    print(f"pass@1 {n}/{len(res)} = {n / len(res):.0%}   pass^{k} {passk}/{len(cases)}")
    fu = [x for x in res if "pass2" in x]
    if fu:
        print(f"follow-up after a wrong answer: fixed {sum(x['pass2'] for x in fu)}/{len(fu)}, efforts "
              f"{sorted({x['effort2'] for x in fu})}, seconds {[x['seconds2'] for x in fu]}")
    secs = sorted(x["seconds"] for x in res)
    print(f"seconds median {statistics.median(secs)}  p90 {secs[int(len(secs) * .9) - 1]}  <=120s "
          f"{sum(s <= 120 for s in secs)}/{len(secs)}   use_skill called {sum('use_skill' in x['tools'] for x in res)}"
          f"/{len(res)}")
    out = ROOT / "docs" / ((os.environ.get("OUT") or ("eval_finance_v0_skills" if use_skills else "eval_finance_v0")) + ".json")
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False, default=lambda o: sorted(o) if isinstance(o, set) else str(o)))
    print("->", out)


if __name__ == "__main__":
    args = sys.argv[1:]
    nums = [int(a) for a in args[:2] if a.isdigit()]
    main(*nums, only=[a for a in args if not a.isdigit() and a != "--skills"], use_skills="--skills" in args)
