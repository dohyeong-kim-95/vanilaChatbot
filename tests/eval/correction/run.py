"""Loop 23.4: the correction classifier (agent.loop.classify_correction) on labeled messages, k runs each.
python tests/eval/correction/run.py [k] [workers]      (gateway on :8100 or LLM_BASE_URL; EFFORT, default low)
Reports accuracy over all calls, pass^k (every run right), the messages it got wrong, and latency.
"""
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from agent.loop import classify_correction, is_correction  # noqa: E402
from cases import ASKED, NO, PREV, YES  # noqa: E402


def main(k=3, workers=8):
    client = OpenAI(base_url=os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8100/v1"), api_key="dummy")
    model = os.environ.get("LLM_MODEL") or client.models.list().data[0].id
    effort = os.environ.get("EFFORT", "low")
    cases = [(t, True) for t in YES] + [(t, False) for t in NO]

    def one(case):
        t0 = time.time()
        got = classify_correction(client, model, PREV, case[0], effort, ASKED)
        return case, got, time.time() - t0
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(one, [c for c in cases for _ in range(k)]))
    by = {}
    for (text, want), got, _ in res:
        by.setdefault((text, want), []).append(got == want)
    acc = sum(ok for v in by.values() for ok in v) / len(res)
    passk = sum(all(v) for v in by.values()) / len(by)
    regex = sum(is_correction(t) == w for t, w in cases) / len(cases)
    lat = [r[2] for r in res]
    print(f"model {model} effort {effort} k={k}: {len(YES)} yes + {len(NO)} no")
    print(f"accuracy {acc:.1%}  pass^{k} {passk:.1%}  (regex fallback alone {regex:.1%})")
    print(f"latency median {statistics.median(lat):.1f}s  p90 {sorted(lat)[int(len(lat) * 0.9)]:.1f}s")
    for (text, want), v in by.items():
        if not all(v):
            print(f"  {'MISS' if want else 'FALSE+'} {sum(not x for x in v)}/{k} {text!r}")


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:3]))
