"""Mock STDF V4 wafer-sort data with a known answer (Loop 32, docs/research/R7 §6). Real data is confidential:
everything here is generated from a seed, and the answer (truth.json) is written next to, never into, the data.

One file per lot: FAR, MIR, then per wafer WIR, per die PIR + PTRs (stop on the first failing test, as sort
programs do) + PRR, WRR; MRR. The first PTR of each test carries its name and limits, later ones end after RESULT.
Planted: one wafer whose test `drift_test` shifted (the worst wafer), an edge ring failing `edge_test` on every
wafer, and a background of rare random failures.

  python bench/stdf_gen.py OUT_DIR [--seed N] [--lots 3] [--wafers 5] [--radius 36] [--tests 100]
"""
import argparse
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox" / "vtools"))
import stdf as W  # noqa: E402


def generate(out, seed=1, lots=3, wafers=5, radius=36, tests=100, endian="<"):
    rng = random.Random(seed)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    defs = [(1000 + i, f"T{i:03d}_{rng.choice(['VDD', 'IDDQ', 'VOH', 'VOL', 'FMAX', 'TPD', 'LEAK'])}",
             rng.uniform(0.9, 1.1), rng.uniform(0.02, 0.04)) for i in range(tests)]
    drift_i, edge_i = rng.randrange(10, tests), rng.randrange(10, tests)
    while edge_i == drift_i:
        edge_i = rng.randrange(10, tests)
    lot_ids = [f"L{seed % 1000:03d}{chr(65 + i)}" for i in range(lots)]
    bad_wafer = (rng.choice(lot_ids), f"W{rng.randrange(1, wafers + 1):02d}")
    dies = [(x, y) for x in range(-radius, radius + 1) for y in range(-radius, radius + 1) if x * x + y * y <= radius * radius]
    fail_by_test, lot_yield, wafer_yield, n_parts = Counter(), {}, {}, 0
    for lot in lot_ids:
        good_lot = total_lot = 0
        f = out / f"{lot}.stdf"
        with open(f, "wb") as fh:
            fh.write(W.far(endian) + W.mir(lot, e=endian))
            first = set()
            for w in range(1, wafers + 1):
                wid = f"W{w:02d}"
                fh.write(W.wir(wid, endian))
                bad = (lot, wid) == bad_wafer
                good = 0
                buf = []
                for k, (x, y) in enumerate(dies):
                    site = k % 4
                    buf.append(W.pir(1, site, endian))
                    edge = math.hypot(x, y) > radius - 2
                    failed_at, n_test = None, 0
                    for i, (tn, name, mean, sd) in enumerate(defs):
                        lo, hi = mean - 5 * sd, mean + 5 * sd
                        v = rng.gauss(mean, sd)
                        if i == drift_i and bad:
                            v += sd * rng.uniform(1, 8)  # the bad wafer's drift: past the limit (5 sd) for about half
                        if i == edge_i and edge and rng.random() < 0.35:
                            v = hi + sd * rng.uniform(0.5, 3)
                        if rng.random() < 0.0004:  # rare random failures anywhere
                            v = lo - sd
                        failed = not lo <= v <= hi
                        meta = (name, lo, hi, "V") if tn not in first else None
                        first.add(tn)
                        buf.append(W.ptr(tn, 1, site, v, failed, meta, endian))
                        n_test += 1
                        if failed:
                            failed_at = tn
                            break
                    passed = failed_at is None
                    hb = 1 if passed else 2 + (failed_at - 1000) // 25
                    buf.append(W.prr(1, site, passed, n_test, hb, 1 if passed else failed_at, x, y, f"{wid}-{k}", endian))
                    good += passed
                    if not passed:
                        fail_by_test[failed_at] += 1
                    if len(buf) > 50_000:
                        fh.write(b"".join(buf))
                        buf.clear()
                fh.write(b"".join(buf))
                fh.write(W.wrr(wid, len(dies), good, endian))
                wafer_yield[f"{lot}/{wid}"] = round(100 * good / len(dies), 3)
                good_lot += good
                total_lot += len(dies)
            fh.write(W.mrr(endian))
        lot_yield[lot] = round(100 * good_lot / total_lot, 3)
        n_parts += total_lot
    worst = min(wafer_yield, key=wafer_yield.get)
    truth = {"seed": seed, "files": sorted(p.name for p in out.glob("*.stdf")), "parts": n_parts,
             "lot_yield": lot_yield, "wafer_yield": wafer_yield, "worst_wafer": worst,
             "top_fail_test": fail_by_test.most_common(1)[0][0], "fail_by_test": dict(fail_by_test.most_common(5)),
             "planted": {"bad_wafer": "/".join(bad_wafer), "drift_test": defs[drift_i][0], "edge_test": defs[edge_i][0]}}
    return truth


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--lots", type=int, default=3)
    ap.add_argument("--wafers", type=int, default=5)
    ap.add_argument("--radius", type=int, default=36)
    ap.add_argument("--tests", type=int, default=100)
    ap.add_argument("--truth", help="where to write truth.json (default: next to OUT, not in it)")
    a = ap.parse_args()
    t = generate(a.out, a.seed, a.lots, a.wafers, a.radius, a.tests)
    dest = Path(a.truth or Path(a.out).resolve().parent / f"{Path(a.out).name}.truth.json")
    dest.write_text(json.dumps(t, indent=1))
    print(json.dumps({k: t[k] for k in ("parts", "lot_yield", "worst_wafer", "top_fail_test")}), "->", dest)
