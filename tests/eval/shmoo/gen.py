"""Synthetic ATE shmoo log (Loop 16): DRAM-like devices swept over VDD x frequency at two temperatures.
https://en.wikipedia.org/wiki/Shmoo_plot

Model per device: passes when freq <= fmax(vdd) = slope * (vdd - vth), slower when hot, plus a retention floor
at low VDD when hot and random flips near the boundary. Three injected defects (ground truth for the eval):
  W1 X3 Y2  hole     a failing pocket inside the passing region, 85C only
  W2 X1 Y3  wall     fails every frequency above 2000 MHz at any VDD (a slow path)
  W2 X4 Y1  ceiling  fails at VDD >= 1.26 V, 85C only (over-stress)
python tests/eval/shmoo/gen.py OUT.csv
"""
import random
import sys
from pathlib import Path

SEED = 16
VDD = [round(1.00 + 0.02 * i, 2) for i in range(16)]          # 1.00 .. 1.30 V
FREQ = [1600 + 100 * i for i in range(17)]                     # 1600 .. 3200 MHz
TEMPS = [25, 85]
DIES = [(w, x, y) for w in (1, 2) for x in range(1, 5) for y in range(1, 4)]   # 24 devices
DEFECTS = {(1, 3, 2): "hole", (2, 1, 3): "wall", (2, 4, 1): "ceiling"}


def device_id(w, x, y):
    return f"W{w}-X{x}Y{y}"


def result(rng, die, chip, temp, v, f):
    slope, vth = chip
    fmax = slope * (v - vth) * (0.93 if temp == 85 else 1.0)
    if temp == 85 and v < 1.02:  # retention floor when hot
        return "F"
    kind = DEFECTS.get(die)
    if kind == "hole" and temp == 85 and 1.20 <= v <= 1.26 and 1900 <= f <= 2100:
        return "F"
    if kind == "wall" and f > 2000:
        return "F"
    if kind == "ceiling" and temp == 85 and v >= 1.26:
        return "F"
    p = f <= fmax
    if abs(f - fmax) < 60 and rng.random() < 0.3:  # marginal: flips near the boundary
        p = not p
    return "P" if p else "F"


def generate():
    rng = random.Random(SEED)
    chips = {d: (4667 + rng.gauss(0, 110), 0.636 + rng.gauss(0, 0.012)) for d in DIES}
    rows = []
    for t in TEMPS:
        for site, d in enumerate(DIES):
            for v in VDD:
                for f in FREQ:
                    rows.append({"LOT": "L2610A", "WAFER": d[0], "DIE_X": d[1], "DIE_Y": d[2], "SITE": site % 4,
                                 "TEMP_C": t, "TEST": "MARCH_C", "VDD_V": f"{v:.2f}", "FREQ_MHZ": f,
                                 "RESULT": result(rng, d, chips[d], t, v, f)})
    return rows


def write(path):
    rows = generate()
    cols = list(rows[0])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Tester: T5833-SIM  Program: DDR_SHMOO_v3  Operator: sim\n"
                 "# Lot: L2610A  Start: 2026-10-29 21:04:11\n# Sweep: VDD_V x FREQ_MHZ, test MARCH_C\n")
        fh.write(",".join(cols) + "\n")
        for r in rows:
            fh.write(",".join(str(r[c]) for c in cols) + "\n")
    return rows


def truth(rows):
    """Exact answers from the generated rows (not from parsing the file)."""
    grid = {}
    for r in rows:
        grid[(device_id(r["WAFER"], r["DIE_X"], r["DIE_Y"]), r["TEMP_C"], float(r["VDD_V"]), r["FREQ_MHZ"])] = r["RESULT"]
    devs = [device_id(*d) for d in DIES]

    def all_pass_fmax(temp, v):
        ok = [f for f in FREQ if all(grid[(d, temp, v, f)] == "P" for d in devs)]
        return max(ok) if ok else None

    def rate(temp):
        cells = [grid[(d, temp, v, f)] for d in devs for v in VDD for f in FREQ]
        return round(100 * cells.count("P") / len(cells), 1)

    def vmin(dev, temp, f):  # lowest passing VDD at this frequency
        ok = [v for v in VDD if grid[(dev, temp, v, f)] == "P"]
        return min(ok) if ok else None

    return {"defects": {device_id(*d): k for d, k in DEFECTS.items()},
            "all_pass_fmax_85_110": all_pass_fmax(85, 1.10), "all_pass_fmax_25_120": all_pass_fmax(25, 1.20),
            "pass_rate_25": rate(25), "pass_rate_85": rate(85),
            "vmin_W1-X1Y1_85_2400": vmin("W1-X1Y1", 85, 2400), "devices": len(devs), "cells": len(VDD) * len(FREQ)}


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/shmoo_L2610A.csv")
    rows = write(out)
    print(out, len(rows), "rows")
    print(truth(rows))
