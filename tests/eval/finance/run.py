"""eval v0 (docs/PROBLEM.md): monthly spending questions over card/bank exports. Gateway on :8100.
python tests/eval/finance/run.py [k] [workers] [case_id ...]   -> docs/eval_finance_v0.json"""
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
from agent.loop import Context, run  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402
from cases import build, match  # noqa: E402
from tests.eval.run_eval import Meter  # noqa: E402

CODE = ["bash", "read_file", "write_file", "edit_file"]
TASK_PREFIX = "업로드한 파일은 /workspace/uploads 에 있어요. 결과물은 /workspace/outputs 에 저장해 주세요.\n\n"


def one(case, i, fixtures, tmp):
    ws = tmp / f"{case['id']}-{i}"
    (ws / "uploads").mkdir(parents=True)
    (ws / "outputs").mkdir()
    for f in case["files"]:
        shutil.copy(fixtures / f, ws / "uploads" / f)
    os.system(f"chown -R 1000:1000 {ws}")
    sb = Sandbox(ws, readonly=("uploads",))
    m = Meter(OpenAI(base_url=os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8100/v1"),
                     api_key=os.environ.get("LLM_API_KEY", "dummy")))
    tools, t = [], time.time()
    res = {"case": case["id"], "tag": case["tag"], "i": i, "expected": case["expected"]}
    try:
        final, _ = run(TASK_PREFIX + case["prompt"], Context(sandbox=sb), tool_names=CODE, client=m, max_steps=30,
                       log=lambda l: tools.append(l.split("(")[0].split("] ")[1]))
        res.update(final=(final or "")[:300], llm_calls=m.calls, tool_calls=len(tools),
                   prompt_tokens=m.prompt, completion_tokens=m.completion)
        try:
            got = json.loads((ws / "outputs/answer.json").read_text())["answer"]
        except Exception as e:
            res.update(got=None, pass_=False, why=f"no answer.json: {type(e).__name__}")
            return res
        ok = match(case["expected"], got, case["tol"])
        why = None if ok else "wrong answer"
        if case["id"] == "a_report":
            png = ws / "outputs/chart.png"
            has_png = png.exists() and png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
            has_md = (ws / "outputs/report.md").exists()
            if not (has_png and has_md):
                ok, why = False, f"report.md={has_md} chart.png={has_png}"
        res.update(got=got, pass_=ok, why=why)
    except Exception as e:
        res.update(pass_=False, why=f"error: {repr(e)[:200]}")
    finally:
        res["seconds"] = round(time.time() - t)
        res["tools"] = tools
        sb.close()
    return res


def main(k=3, workers=4, only=()):
    tmp = Path(tempfile.mkdtemp(prefix="feval-"))
    cases = [c for c in build(tmp / "fixtures") if not only or c["id"] in only]
    jobs = [(c, i) for c in cases for i in range(k)]
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(lambda j: one(*j, tmp / "fixtures", tmp), jobs))
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
    out = ROOT / "docs" / "eval_finance_v0.json"
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False, default=lambda o: sorted(o) if isinstance(o, set) else str(o)))
    print("->", out)


if __name__ == "__main__":
    args = sys.argv[1:]
    nums = [int(a) for a in args[:2] if a.isdigit()]
    main(*nums, only=[a for a in args if not a.isdigit()])
