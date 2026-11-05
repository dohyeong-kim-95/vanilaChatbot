"""Role: die binning (Loop 41). What a generated program must do, and the checks that it does it.

The program reads die measurements on stdin and prints a bin per die, by a spec of test limits given in the task.
Plain whitespace-separated numbers both ways, so any language can read and write them:
  stdin   N, then N lines "x y v1 ... vT" (values in table order, -9999 = not measured)
  stdout  N lines "x y bin", in input order
Binning (bin_of): the tests in priority order (not table order). A missing value: skip the test if the spec says
"skip", else missing_bin. A value outside [lo, hi] (inclusive; a null limit is no limit): that test's bin. No bin
yet and a marginal rule: any measured value within marginal_pct % of one of its limits gives marginal_bin. Else
pass_bin. The rules a weak model gets wrong (priority vs table order, skip, the margin) are the point.

This file is trusted host code: the oracle, the case generator and the judge. The generated program only ever runs
in a throwaway container (agent/codegen.py); its output is data here.
"""
import math
import random
import re
from decimal import Decimal

DESCRIPTION = "die binning: die measurements on stdin -> a bin per die, by a spec of test limits"
MISSING = -9999
MAX_TESTS, MAX_BIN = 8, 99
SPEC_SCHEMA = {
    "type": "object", "description": "the binning spec",
    "properties": {
        "tests": {"type": "array", "description": "in the order of the input's columns", "items": {
            "type": "object", "properties": {
                "name": {"type": "string"}, "lo": {"type": ["number", "null"]}, "hi": {"type": ["number", "null"]},
                "bin": {"type": "integer", "description": "the bin of a die that fails this test"},
                "priority": {"type": "integer", "description": "1 is checked first; default: table order"},
                "missing": {"type": "string", "enum": ["bin", "skip"],
                            "description": "not measured: missing_bin (default) or ignore this test"}},
            "required": ["name", "bin"]}},
        "pass_bin": {"type": "integer", "description": "default 1"},
        "missing_bin": {"type": "integer", "description": "bin of a die with a value not measured"},
        "marginal_pct": {"type": "number", "description": "optional: a passing die with a value within this % of "
                                                          "one of its limits gets marginal_bin"},
        "marginal_bin": {"type": "integer"}},
    "required": ["tests", "missing_bin"]}


def validate(spec):
    if not isinstance(spec, dict) or not isinstance(spec.get("tests"), list) or not spec["tests"]:
        raise ValueError("spec needs a non-empty list of tests")
    if len(spec["tests"]) > MAX_TESTS:
        raise ValueError(f"at most {MAX_TESTS} tests")
    tests = []
    for i, t in enumerate(spec["tests"], 1):
        if not isinstance(t, dict) or not str(t.get("name", "")).isidentifier():
            raise ValueError(f"each test needs a name (letters, digits, _): {t!r}")
        lo, hi = t.get("lo"), t.get("hi")
        if lo is None and hi is None:
            raise ValueError(f"{t['name']}: at least one of lo, hi")
        for v in (lo, hi):
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                                  or abs(v) >= 1e6):
                raise ValueError(f"{t['name']}: limits are numbers under 1e6")
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"{t['name']}: lo > hi")
        if t.get("missing", "bin") not in ("bin", "skip"):
            raise ValueError(f"{t['name']}: missing is 'bin' or 'skip'")
        tests.append({"name": t["name"], "lo": lo, "hi": hi, "bin": _bin(t.get("bin"), t["name"]),
                      "priority": _int(t.get("priority", i), 1, 99, f"{t['name']}: priority"),
                      "missing": t.get("missing", "bin")})
    if len({t["priority"] for t in tests}) < len(tests):
        raise ValueError("priorities must differ")
    out = {"tests": tests, "pass_bin": _bin(spec.get("pass_bin", 1), "pass_bin"),
           "missing_bin": _bin(spec.get("missing_bin"), "missing_bin")}
    fails = {t["bin"] for t in tests} | {out["missing_bin"]}
    if spec.get("marginal_pct") is not None:
        p = spec["marginal_pct"]
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 < p <= 50:
            raise ValueError("marginal_pct: a number in (0, 50]")
        out["marginal_pct"], out["marginal_bin"] = p, _bin(spec.get("marginal_bin"), "marginal_bin")
        if out["marginal_bin"] in fails | {out["pass_bin"]}:
            raise ValueError("marginal_bin must differ from the other bins")
    if out["pass_bin"] in fails:
        raise ValueError("pass_bin must differ from the fail bins")
    return out


def _int(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{what}: an integer {lo}-{hi}")
    return v


def _bin(b, what):
    return _int(b, 0, MAX_BIN, what)


def _d(x):
    return Decimal(str(x))


def _order(spec):
    return sorted(range(len(spec["tests"])), key=lambda i: spec["tests"][i]["priority"])


def _margin(spec, limit):
    return _d(spec["marginal_pct"]) / 100 * abs(_d(limit))


def bin_of(spec, values):
    """The oracle. Exact decimal comparison: what the spec says, not what a float says."""
    for i in _order(spec):
        t, v = spec["tests"][i], values[i]
        if v == MISSING:
            if t["missing"] == "skip":
                continue
            return spec["missing_bin"]
        if (t["lo"] is not None and _d(v) < _d(t["lo"])) or (t["hi"] is not None and _d(v) > _d(t["hi"])):
            return t["bin"]
    if "marginal_pct" in spec:
        for t, v in zip(spec["tests"], values):
            if v != MISSING and any(abs(_d(v) - _d(lim)) <= _margin(spec, lim)
                                    for lim in (t["lo"], t["hi"]) if lim is not None):
                return spec["marginal_bin"]
    return spec["pass_bin"]


def describe(spec, examples):
    def lim(x):
        return "-" if x is None else x
    rows = "\n".join(f"| {i} | {t['name']} | {lim(t['lo'])} | {lim(t['hi'])} | {t['bin']} | {t['priority']} | "
                     f"{'skip' if t['missing'] == 'skip' else 'bin %d' % spec['missing_bin']} |"
                     for i, t in enumerate(spec["tests"], 1))
    marginal = (f"If no test gave a bin, the die may still be marginal: when any measured value is within "
                f"{spec['marginal_pct']}% of one of its own limits (|value - limit| <= {spec['marginal_pct']}/100 * "
                f"|limit|; also when it equals the limit), the bin is {spec['marginal_bin']}. Otherwise the bin is "
                f"{spec['pass_bin']}." if "marginal_pct" in spec else
                f"If no test gave a bin, the bin is {spec['pass_bin']}.")
    ex = "\n\n".join(f"Example {k} input:\n{i}Expected output:\n{o}" for k, (i, o) in enumerate(examples, 1))
    return f"""Write a die binning program.

Input (stdin): the first line is N, the number of dies. Then N lines, one per die: x y followed by \
{len(spec['tests'])} measured values, in the order of the table's rows. A value of {MISSING} means "not measured". \
Values have at most 3 decimals.
Output (stdout): N lines "x y bin", one per die, in input order (x, y and bin are integers).

Rules: check the tests in priority order (priority 1 first), which is not the table order. For each test: if its \
value is {MISSING}, then if the last column says "skip" ignore this test and go on, else the die gets the bin in \
that column and you stop. If the value is outside its limits, the die gets that test's fail bin and you stop. \
Limits are inclusive (a value equal to a limit passes); "-" means no limit on that side. {marginal}

| column | test | low limit | high limit | fail bin | priority | if not measured |
|---|---|---|---|---|---|---|
{rows}

{ex}"""


def _inner_ok(spec, t):
    """Whether t has passing values outside its marginal zones (when the zones meet, every pass is marginal)."""
    if "marginal_pct" not in spec or t["lo"] is None or t["hi"] is None:
        return True
    return t["lo"] + float(_margin(spec, t["lo"])) + 0.002 < t["hi"] - float(_margin(spec, t["hi"])) - 0.002


def _range(spec, t, inner):
    """[a, b] of values that pass t; inner: also clear of the marginal zones (the middle when there is no such
    value: categories() then has no "pass")."""
    lo, hi = t["lo"], t["hi"]
    a = lo if lo is not None else hi - max(abs(hi), 1)  # one-sided: a range as wide as the limit
    b = hi if hi is not None else lo + max(abs(lo), 1)
    if inner and "marginal_pct" in spec:
        a2 = a + float(_margin(spec, lo)) + 0.002 if lo is not None else a
        b2 = b - float(_margin(spec, hi)) - 0.002 if hi is not None else b
        a, b = (a2, b2) if a2 < b2 else ((a + b) / 2, (a + b) / 2)
    return a, b


def _value(spec, t, kind, rng):
    lo, hi = t["lo"], t["hi"]
    if kind == "in":
        return round(rng.uniform(*_range(spec, t, True)), 3)
    if kind == "edge":
        return rng.choice([x for x in (lo, hi) if x is not None])
    if kind == "margin":  # inside the limits, within the margin of one whose margin is wide enough
        s = rng.choice([x for x in (lo, hi) if x is not None and float(_margin(spec, x)) >= 0.002])
        m = float(_margin(spec, s))
        # never at |v - limit| == margin, where the exact oracle and a right double program disagree
        u = rng.randint(1, max(1, math.floor((m - 0.0005) * 1000))) / 1000
        return round(s + u if s == lo else s - u, 3)
    a, b = _range(spec, t, False)
    s = rng.choice([x for x in (lo, hi) if x is not None])
    gap = max((b - a) * rng.uniform(0.01, 0.5), 0.001)
    return round((s - gap) if s == lo else (s + gap), 3)


def categories(spec, example=False):
    """The kinds of die the cases hold: each probes one rule. Examples are plain dies only: the rules that are
    easy to get wrong are left to the hidden checks."""
    can_pass = all(_inner_ok(spec, t) for t in spec["tests"])
    if example:
        return ("pass", "fail") if can_pass else ("fail",)
    out = (["pass"] if can_pass else []) + ["fail", "boundary", "priority"]
    if any(t["missing"] == "bin" for t in spec["tests"]):
        out.append("missing")
    if any(t["missing"] == "skip" for t in spec["tests"]):
        out.append("skip")
    if "marginal_pct" in spec and any(float(_margin(spec, x)) >= 0.002 for t in spec["tests"]
                                      for x in (t["lo"], t["hi"]) if x is not None):
        out.append("marginal")
    return tuple(out)


CATEGORIES = ("pass", "fail", "boundary", "priority", "missing", "skip", "marginal")


def _die(spec, cat, rng):
    """Values for one die of a category: what kind of mistake it would catch."""
    tests, n = spec["tests"], len(spec["tests"])
    kinds = ["in"] * n
    if cat == "fail":
        kinds[rng.randrange(n)] = "out"
    elif cat == "boundary":
        for i in rng.sample(range(n), rng.randint(1, n)):
            kinds[i] = "edge"
    elif cat == "priority" and n > 1:
        # two failing tests where the table order and the priority order disagree, when the spec has such a pair
        inv = [(i, j) for i in range(n) for j in range(i + 1, n) if tests[j]["priority"] < tests[i]["priority"]]
        for i in rng.choice(inv) if inv else rng.sample(range(n), 2):
            kinds[i] = "out"
    elif cat == "marginal":
        ok = [i for i, t in enumerate(tests)
              if any(float(_margin(spec, x)) >= 0.002 for x in (t["lo"], t["hi"]) if x is not None)]
        kinds[rng.choice(ok)] = "margin"
    vals = [_value(spec, t, k, rng) for t, k in zip(tests, kinds)]
    if cat in ("missing", "skip"):
        pool = [i for i, t in enumerate(tests) if (t["missing"] == "skip") == (cat == "skip")] or list(range(n))
        for i in rng.sample(pool, min(len(pool), rng.randint(1, 2))):
            vals[i] = MISSING
        if n > 1 and rng.random() < 0.5:  # a failing test before or after the missing one
            j = rng.randrange(n)
            if vals[j] != MISSING:
                vals[j] = _value(spec, tests[j], "out", rng)
    return vals


def cases(spec, seed, n, dies, example=False):
    """n inputs of `dies` dies each; every category of the spec appears in each input when there is room."""
    rng = random.Random(seed)
    cats_of = categories(spec, example)
    out = []
    for k in range(n):
        cats = [cats_of[i % len(cats_of)] for i in range(dies)]
        rng.shuffle(cats)
        coords = rng.sample([(x, y) for x in range(1, 30) for y in range(1, 30)], dies)
        rows = [(x, y, _die(spec, c, rng), c) for (x, y), c in zip(coords, cats)]
        text = f"{dies}\n" + "".join(f"{x} {y} " + " ".join(_fmt(v) for v in vs) + "\n" for x, y, vs, _ in rows)
        out.append({"id": f"{k + 1}", "input": text, "categories": [c for *_, c in rows]})
    return out


def _fmt(v):
    return str(int(v)) if float(v).is_integer() else f"{v:.3f}".rstrip("0")


def expected(spec, text):
    return "".join(f"{x} {y} {b}\n" for x, y, b in _expect(spec, text))


def _expect(spec, text):
    lines = text.split("\n")
    out = []
    for line in lines[1:int(lines[0]) + 1]:
        p = line.split()
        out.append((int(p[0]), int(p[1]), bin_of(spec, [float(v) for v in p[2:]])))
    return out


def judge(spec, case, output):
    """-> {"format": [problems], "wrong": [{"item", "category", "detail"}], "total": dies}. Compared by tokens:
    a language may pad or wrap its numbers."""
    want = _expect(spec, case["input"])
    toks = output.split()
    if len(toks) != 3 * len(want) or not all(re.fullmatch(r"-?[0-9]{1,9}", t) for t in toks):
        return {"format": [f"expected {len(want)} lines of 3 integers (x y bin), got {len(toks)} numbers/words"
                           + (f" starting {' '.join(toks[:6])!r}" if toks else "")], "wrong": [], "total": len(want)}
    got = [tuple(int(t) for t in toks[i:i + 3]) for i in range(0, len(toks), 3)]
    allowed = {spec["pass_bin"], spec["missing_bin"], spec.get("marginal_bin"), *(t["bin"] for t in spec["tests"])}
    fmt = []
    if [g[:2] for g in got] != [w[:2] for w in want]:
        fmt.append("the dies (x y) are not the input's, in input order")
    if any(g[2] not in allowed for g in got):
        fmt.append(f"bins outside the spec: {sorted({g[2] for g in got} - allowed)}")
    lines = case["input"].split("\n")[1:]
    # the item reaches the agent from the hidden checks: an ordinal, never the input's coordinates (review loop 10)
    wrong = [{"item": f"die {i + 1}", "category": c,
              "detail": f"input {lines[i].strip()!r}: expected bin {w[2]}, got {g[2]}"}
             for i, (w, g, c) in enumerate(zip(want, got, case["categories"])) if w[2] != g[2]]
    return {"format": fmt, "wrong": wrong, "total": len(want)}


def random_spec(seed):
    """A spec for the evaluation (bench/codegen_eval.py): 2-5 tests, one-sided limits, odd bins, priorities that
    are not the table order, tests skipped when not measured, often a marginal rule."""
    rng = random.Random(seed)
    names = rng.sample(["VDD", "IDDQ", "FMAX", "VTH", "ILEAK", "TEMP", "RDS", "GAIN"], rng.randint(2, 5))
    tests, bins = [], rng.sample(range(2, 40), len(names) + 2)
    prio = rng.sample(range(1, len(names) + 1), len(names))
    if prio == sorted(prio):
        prio = prio[::-1]
    for name, b, p in zip(names, bins, prio):
        c = round(rng.uniform(0.5, 500), rng.choice([1, 2, 3]))
        w = round(c * rng.uniform(0.05, 0.3), 3) or 0.1
        kind = rng.choice(["both", "both", "lo", "hi"])
        tests.append({"name": name, "lo": None if kind == "hi" else round(c - w, 3),
                      "hi": None if kind == "lo" else round(c + w, 3), "bin": b, "priority": p,
                      "missing": "skip" if rng.random() < 0.3 else "bin"})
    spec = {"tests": tests, "pass_bin": 1, "missing_bin": bins[-2]}
    if rng.random() < 0.7:
        spec.update(marginal_pct=rng.choice([2, 5, 10]), marginal_bin=bins[-1])
    return validate(spec)
