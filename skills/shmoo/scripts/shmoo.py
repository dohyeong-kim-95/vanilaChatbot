"""Shmoo plots from ATE-style test logs (one row per device x condition x sweep point, with a pass/fail column).
Standard library only. The server builds the interactive view (show_shmoo) from the same code.

CLI:
  python shmoo.py LOG.csv                       guess columns, print the mapping and a summary per condition
  python shmoo.py LOG.csv --x FREQ_MHZ --y VDD_V --device WAFER,DIE_X,DIE_Y --filters TEMP_C [--ascii DEVICE]
Python:
  import sys; sys.path.insert(0, "/skills/shmoo/scripts"); import shmoo
  g = shmoo.load(path, x="FREQ_MHZ", y="VDD_V", result="RESULT", device=["WAFER", "DIE_X", "DIE_Y"], filters=["TEMP_C"])
  g.cell(device, {"TEMP_C": "85"}, x_value, y_value) -> "P" | "F" | None;  shmoo.analyze(g) -> findings
"""
import csv
import io
import re
import sys
from collections import Counter
from pathlib import Path

PASS = {"P", "PASS", "1", "OK", "TRUE", "GO", "Y"}
FAIL = {"F", "FAIL", "0", "NG", "FALSE", "NOGO", "N", "X"}
MAX_AXIS = 200
MAX_DEVICES = 5000
MAX_ROWS = 3_000_000


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _sort_values(vals):
    nums = [_num(v) for v in vals]
    if all(n is not None for n in nums):
        return [v for _, v in sorted(zip(nums, vals))]
    return sorted(vals)


def read_table(path):
    """-> (header, rows). Skips comment/preamble lines (#, //, ;) and blank lines; header = first line left."""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith(("#", "//", ";"))]
    delim = max(",\t;|", key=lambda d: lines[0].count(d)) if lines else ","
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))
    if not rows:
        raise ValueError("empty log")
    header = [h.strip() for h in rows[0]]
    if len(rows) - 1 > MAX_ROWS:
        raise ValueError(f"log has {len(rows) - 1} rows; limit is {MAX_ROWS}")
    return header, [dict(zip(header, (c.strip() for c in r))) for r in rows[1:] if len(r) >= len(header)]


def guess(header, rows):
    """Best-effort column roles; always print and check them."""
    h = {c.upper(): c for c in header}
    pick = lambda pats: next((h[k] for p in pats for k in h if re.search(p, k)), None)  # noqa: E731
    result = pick([r"^RESULT$", r"PASS_?FAIL", r"^P/?F$", r"RESULT", r"STATUS", r"BIN"])
    y = pick([r"VDD", r"VOLT", r"^V[A-Z_]*$"])
    x = pick([r"FREQ", r"MHZ", r"TCK", r"PERIOD", r"TIMING", r"DELAY", r"^T[A-Z_]*NS$"])
    device = [c for c in header if re.search(r"WAFER|DIE_?X|DIE_?Y|^X$|^Y$|CHIP|DEVICE|DUT|SERIAL|UNIT", c.upper())]
    used = {result, x, y, *device}
    filters = [c for c in header if c not in used and re.search(r"TEMP|TEST|PATTERN|CORNER|MODE", c.upper())
               and 1 < _distinct(rows, c, 51) <= 50]
    return {"x": x, "y": y, "result": result, "device": device, "filters": filters}


def _distinct(rows, col, stop):
    """Distinct values of a column over ALL rows (logs sweep one temperature at a time), counting up to `stop`."""
    seen = set()
    for r in rows:
        seen.add(r[col])
        if len(seen) >= stop:
            break
    return len(seen)


class Grid:
    """cells[(device, filter_key, xi, yi)] = "P" / "F"; filter_key is a tuple of filter values."""

    def __init__(self, x, y, result, device, filters):
        self.x_name, self.y_name, self.result, self.device_cols, self.filter_cols = x, y, result, device, filters
        self.xs, self.ys, self.devices, self.filter_values = [], [], [], {}
        self.cells, self.conflicts, self.unknown = {}, 0, Counter()

    def key(self, filt):
        return tuple(str(filt[c]) for c in self.filter_cols)

    def cell(self, device, filt, xv, yv):
        try:
            return self.cells.get((device, self.key(filt), self.xs.index(str(xv)), self.ys.index(str(yv))))
        except ValueError:
            return None

    def combos(self):
        return sorted({k[1] for k in self.cells})


def _norm_axis(v):
    n = _num(v)
    return (f"{n:.15g}" if n is not None else v)  # :g keeps 6 digits: 2000001000 Hz and 2000002000 Hz merged


def load(path, x, y, result, device, filters=(), pass_values=None, header_rows=None):
    header, rows = header_rows or read_table(path)
    device, filters = list(device), list(filters)
    for c in [x, y, result, *device, *filters]:
        if c not in header:
            raise ValueError(f"column {c!r} not in log; columns are {header}")
    passes = {p.upper() for p in pass_values} if pass_values else PASS
    g = Grid(x, y, result, device, filters)
    xs, ys, devs = set(), set(), {}
    for r in rows:
        xs.add(_norm_axis(r[x]))
        ys.add(_norm_axis(r[y]))
        devs.setdefault("-".join(r[c] for c in device) if device else "all", None)
    g.xs, g.ys = _sort_values(list(xs)), _sort_values(list(ys))
    if len(g.xs) > MAX_AXIS or len(g.ys) > MAX_AXIS:
        raise ValueError(f"axis too long ({len(g.xs)} x {len(g.ys)} values; limit {MAX_AXIS}). Wrong columns?")
    g.devices = list(devs)
    if len(g.devices) > MAX_DEVICES:
        raise ValueError(f"{len(g.devices)} devices; limit {MAX_DEVICES}. Wrong device columns?")
    g.filter_values = {c: _sort_values(list({r[c] for r in rows})) for c in filters}
    xi, yi = {v: i for i, v in enumerate(g.xs)}, {v: i for i, v in enumerate(g.ys)}
    for r in rows:
        res = r[result].strip().upper()
        if res in passes:
            v = "P"
        elif pass_values or res in FAIL:
            v = "F"
        else:
            g.unknown[r[result]] += 1
            continue
        k = ("-".join(r[c] for c in device) if device else "all", tuple(r[c] for c in filters),
             xi[_norm_axis(r[x])], yi[_norm_axis(r[y])])
        if k in g.cells and g.cells[k] != v:
            g.conflicts += 1  # retest: last row wins
        g.cells[k] = v
    return g


# ---- analysis -----------------------------------------------------------------------------------------------
def pass_rate(g, device=None, combo=None):
    vals = [v for (d, c, _, _), v in g.cells.items() if (device is None or d == device) and (combo is None or c == combo)]
    return 100 * vals.count("P") / len(vals) if vals else None


def all_pass_max_x(g, combo, yv):
    """Largest x where every device passes at this y (e.g. fmax at a VDD), or None."""
    yi = g.ys.index(str(yv))
    ok = [i for i in range(len(g.xs)) if all(g.cells.get((d, combo, i, yi)) == "P" for d in g.devices)]
    return g.xs[max(ok)] if ok else None


def _holes(g, d, combo):
    """Fail cells inside the pass region: passing on both sides along x AND along y."""
    out = []
    c = lambda i, j: g.cells.get((d, combo, i, j))  # noqa: E731
    for i in range(len(g.xs)):
        for j in range(len(g.ys)):
            if c(i, j) != "F":
                continue
            along_x = any(c(a, j) == "P" for a in range(i)) and any(c(a, j) == "P" for a in range(i + 1, len(g.xs)))
            along_y = any(c(i, b) == "P" for b in range(j)) and any(c(i, b) == "P" for b in range(j + 1, len(g.ys)))
            if along_x and along_y:
                out.append((i, j))
    return out


def _pockets(cells):
    """Split hole cells into separate pockets (8-connected), largest first: two holes are two findings, never one
    box spanning the passing cells between them."""
    left, out = set(cells), []
    while left:
        stack, comp = [left.pop()], []
        while stack:
            i, j = stack.pop()
            comp.append((i, j))
            for n in [(i + a, j + b) for a in (-1, 0, 1) for b in (-1, 0, 1)]:
                if n in left:
                    left.remove(n)
                    stack.append(n)
        out.append(sorted(comp))
    return sorted(out, key=lambda c: (-len(c), c[0]))


def _ceiling(g, d, combo):
    """Columns where the top y values fail although a lower y passes (over-stress, not a margin limit)."""
    cols = []
    top = len(g.ys) - 1
    for i in range(len(g.xs)):
        if g.cells.get((d, combo, i, top)) == "F" and any(g.cells.get((d, combo, i, j)) == "P" for j in range(top)):
            cols.append(i)
    return cols


def _edge(g, d, combo):
    """Max passing x index per y row (-1 if none): the device's pass boundary."""
    return [max((i for i in range(len(g.xs)) if g.cells.get((d, combo, i, j)) == "P"), default=-1)
            for j in range(len(g.ys))]


def analyze(g, min_hole=3, members=False):
    """Findings per device/condition, strongest first. Kinds:
    hole     a pocket of >= min_hole connected fail cells inside the pass region, one finding per pocket
             (single cells near the boundary are noise); members=True adds its cells as "at": [[x index, y index], ...]
    ceiling  top-y row fails in >= 3 columns that pass lower (over-stress)
    wall     boundary at the top y rows is far left of the other devices (a slow path not fixed by more voltage)
    low      pass rate far below the other devices for the same condition"""
    found = []
    passed, tested = Counter(), Counter()  # one pass over the cells (pass_rate per device was O(devices x cells))
    for (d, c, _, _), v in g.cells.items():
        tested[d, c] += 1
        passed[d, c] += v == "P"
    for combo in g.combos():
        rates = {d: 100 * passed[d, combo] / tested[d, combo] if tested[d, combo] else None for d in g.devices}
        known = sorted(r for r in rates.values() if r is not None)
        med = known[len(known) // 2] if known else 0
        edges = {d: _edge(g, d, combo) for d in g.devices}
        top = len(g.ys) - 1
        measured = sorted(edges[d][top] for d in g.devices if rates[d] is not None)  # untested devices are not -1s
        top_med = measured[len(measured) // 2]
        cond = dict(zip(g.filter_cols, combo))
        for d in g.devices:
            if rates[d] is None:
                continue
            for h in _pockets(_holes(g, d, combo)):
                if len(h) >= min_hole:
                    found.append({"device": d, "condition": cond, "kind": "hole", "cells": len(h),
                                  "x": [g.xs[min(i for i, _ in h)], g.xs[max(i for i, _ in h)]],
                                  "y": [g.ys[min(j for _, j in h)], g.ys[max(j for _, j in h)]],
                                  **({"at": [list(c) for c in h]} if members else {})})
            ce = _ceiling(g, d, combo)
            if len(ce) >= 3:
                found.append({"device": d, "condition": cond, "kind": "ceiling", "columns": len(ce),
                              "y": g.ys[top]})
            if edges[d][top] >= 0 and top_med - edges[d][top] >= 3:
                found.append({"device": d, "condition": cond, "kind": "wall", "max_x_at_top_y": g.xs[edges[d][top]],
                              "median_max_x_at_top_y": g.xs[top_med]})
            if rates[d] < med - 15:
                found.append({"device": d, "condition": cond, "kind": "low", "pass_rate": round(rates[d], 1),
                              "median": round(med, 1)})
    order = {"hole": 0, "wall": 1, "ceiling": 2, "low": 3}
    return sorted(found, key=lambda f: (order[f["kind"]], f["device"]))


def ascii_plot(g, device, combo):
    """Classic tester printout: rows = y (high at top), '*' pass, '.' fail, ' ' not tested."""
    w = max(len(v) for v in g.ys)
    lines = [f"{device}  {dict(zip(g.filter_cols, combo))}   y={g.y_name}  x={g.x_name}"]
    for j in reversed(range(len(g.ys))):
        row = "".join({"P": "*", "F": "."}.get(g.cells.get((device, combo, i, j)), " ") for i in range(len(g.xs)))
        lines.append(f"{g.ys[j]:>{w}} |{row}")
    lines.append(" " * w + " +" + "-" * len(g.xs))
    lines.append(" " * w + f"  {g.xs[0]} .. {g.xs[-1]}")
    return "\n".join(lines)


def summary(g):
    lines = [f"x={g.x_name} ({len(g.xs)} values {g.xs[0]}..{g.xs[-1]}), y={g.y_name} ({len(g.ys)} values "
             f"{g.ys[0]}..{g.ys[-1]}), result={g.result}, device={'+'.join(g.device_cols) or '-'} "
             f"({len(g.devices)} devices), filters={g.filter_values}"]
    if g.unknown:
        lines.append(f"WARNING: result values neither pass nor fail (skipped): {dict(g.unknown)}. Pass pass_values.")
    if g.conflicts:
        lines.append(f"note: {g.conflicts} retested cells changed result; the last row wins")
    for combo in g.combos():
        lines.append(f"{dict(zip(g.filter_cols, combo))}: pass rate {pass_rate(g, combo=combo):.1f}%")
    found = analyze(g)
    lines.append(f"findings ({len(found)}):" if found else "findings: none")
    lines += [f"  {f}" for f in found]
    return "\n".join(lines)


def main(argv):
    opts, args = {}, []
    it = iter(argv)
    for a in it:
        if a.startswith("--"):
            opts[a[2:]] = next(it)
        else:
            args.append(a)
    header, rows = read_table(args[0])
    m = guess(header, rows)
    for k in ("x", "y", "result"):
        m[k] = opts.get(k, m[k])
    for k in ("device", "filters"):
        if k in opts:
            m[k] = [c for c in opts[k].split(",") if c]
    print("columns:", header)
    print("mapping:", m)
    g = load(args[0], m["x"], m["y"], m["result"], m["device"], m["filters"],
             pass_values=opts["pass"].split(",") if "pass" in opts else None, header_rows=(header, rows))
    print(summary(g))
    if "ascii" in opts:
        for combo in g.combos():
            print(ascii_plot(g, opts["ascii"], combo))


if __name__ == "__main__":
    main(sys.argv[1:])
