"""Role: wafer map measurements (Loop 42). A generated program reads one wafer map and prints the numbers the spec
asks for: counts, yield, fail clusters, edge fails, fails in a ring. Every answer is an exact integer, so a right
program is right on every map, and a wrong one is wrong somewhere: the guards can be measured.

Maps: real ones from WM-811K (public, Kaggle "qingyi/wm811k-wafer-map", via use_maps(); bench/codegen_eval.py loads
a sample) or, without them, synthetic wafers (tests). Real maps are not in the repo.
  stdin   "R C", then R lines of C numbers: 0 = not a die (off the wafer), 1 = good die, 2 = failing die
  stdout  one line: the spec's fields in order, integers separated by spaces
Fields (each one integer):
  dies                   cells that are 1 or 2
  fails                  cells that are 2
  yield_permille         floor(1000 * (dies - fails) / dies)
  largest_cluster  c     size of the largest group of failing dies joined through their c neighbours (4 or 8);
                         0 without fails
  clusters  c, min_size  number of such groups of at least min_size dies
  edge_fails  c          failing dies with at least one of their c neighbours (4 or 8) a 0 or outside the map
  ring_fails  lo, hi     failing dies at a distance from the centre in [lo%, hi%) of the largest die distance
                         (hi = 100 includes the farthest), in integers: with d2 = (2r - (R-1))^2 + (2c - (C-1))^2
                         (0-based row r, column c) and D2 the largest d2 of any die,
                         lo^2 * D2 <= 10000 * d2 and (10000 * d2 < hi^2 * D2 or hi = 100)
Trusted host code: the oracle, the cases and the judge. The generated program only runs in a throwaway container.
"""
import random
import re

DESCRIPTION = ("wafer map measurements: one WM-811K style wafer map (0 off-wafer, 1 good, 2 fail) on stdin -> "
               "the numbers a spec asks for (counts, yield, fail clusters, edge fails, ring fails)")
MAX_SIDE, MAX_FIELDS = 256, 8
FIELDS = {"dies": (), "fails": (), "yield_permille": (), "largest_cluster": ("connectivity",),
          "clusters": ("connectivity", "min_size"), "edge_fails": ("connectivity",), "ring_fails": ("lo", "hi")}
SPEC_SCHEMA = {
    "type": "object", "description": "what to measure, in the order the program prints it",
    "properties": {"fields": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string", "enum": sorted(FIELDS)},
        "connectivity": {"type": "integer", "enum": [4, 8]}, "min_size": {"type": "integer"},
        "lo": {"type": "integer", "description": "ring_fails: % of the largest die distance, 0-99"},
        "hi": {"type": "integer", "description": "ring_fails: 1-100"}}, "required": ["name"]}}},
    "required": ["fields"]}
_POOL = []


def use_maps(maps):
    """Real maps for the cases (lists of rows of 0/1/2). Partitioned by seed % 3, so the examples, the hidden checks
    and an evaluation's audit (seeds = 0 mod 3) never share a map."""
    _POOL[:] = [m for m in maps if 0 < len(m) <= MAX_SIDE and 0 < len(m[0]) <= MAX_SIDE and any(any(r) for r in m)]


def validate(spec):
    fields = spec.get("fields") if isinstance(spec, dict) else None
    if not isinstance(fields, list) or not fields or len(fields) > MAX_FIELDS:
        raise ValueError(f"spec needs 1-{MAX_FIELDS} fields")
    out = []
    for f in fields:
        name = f.get("name") if isinstance(f, dict) else None
        if name not in FIELDS:
            raise ValueError(f"unknown field {name!r}; one of {sorted(FIELDS)}")
        g = {"name": name}
        for p in FIELDS[name]:
            v = f.get(p)
            ok = {"connectivity": v in (4, 8), "min_size": isinstance(v, int) and 1 <= v <= 10000,
                  "lo": isinstance(v, int) and 0 <= v <= 99, "hi": isinstance(v, int) and 1 <= v <= 100}[p]
            if isinstance(v, bool) or not ok:
                raise ValueError(f"{name}: bad {p} {v!r}")
            g[p] = v
        if name == "ring_fails" and g["lo"] >= g["hi"]:
            raise ValueError("ring_fails: lo < hi")
        out.append(g)
    return {"fields": out}


def _label(f):
    p = ", ".join(f"{k}={f[k]}" for k in FIELDS[f["name"]])
    return f["name"] + (f"({p})" if p else "")


# ---- the oracle ----

def _nbrs(c):
    return [(-1, 0), (1, 0), (0, -1), (0, 1)] + ([(-1, -1), (-1, 1), (1, -1), (1, 1)] if c == 8 else [])


def _clusters(m, c):
    R, C = len(m), len(m[0])
    seen, sizes = [[False] * C for _ in range(R)], []
    for r in range(R):
        for k in range(C):
            if m[r][k] == 2 and not seen[r][k]:
                seen[r][k], stack, n = True, [(r, k)], 0
                while stack:
                    a, b = stack.pop()
                    n += 1
                    for da, db in _nbrs(c):
                        x, y = a + da, b + db
                        if 0 <= x < R and 0 <= y < C and m[x][y] == 2 and not seen[x][y]:
                            seen[x][y] = True
                            stack.append((x, y))
                sizes.append(n)
    return sizes


def measure(spec, m):
    R, C = len(m), len(m[0])
    cells = [(r, k) for r in range(R) for k in range(C) if m[r][k]]
    fails = [(r, k) for r, k in cells if m[r][k] == 2]
    out = []
    for f in spec["fields"]:
        n = f["name"]
        if n == "dies":
            out.append(len(cells))
        elif n == "fails":
            out.append(len(fails))
        elif n == "yield_permille":
            out.append(1000 * (len(cells) - len(fails)) // len(cells))
        elif n == "largest_cluster":
            out.append(max(_clusters(m, f["connectivity"]), default=0))
        elif n == "clusters":
            out.append(sum(s >= f["min_size"] for s in _clusters(m, f["connectivity"])))
        elif n == "edge_fails":
            out.append(sum(any(not (0 <= r + a < R and 0 <= k + b < C) or m[r + a][k + b] == 0
                               for a, b in _nbrs(f["connectivity"])) for r, k in fails))
        elif n == "ring_fails":
            d2 = {(r, k): (2 * r - (R - 1)) ** 2 + (2 * k - (C - 1)) ** 2 for r, k in cells}
            D2 = max(d2.values())
            lo, hi = f["lo"], f["hi"]
            out.append(sum(lo * lo * D2 <= 10000 * d2[p] and (10000 * d2[p] < hi * hi * D2 or hi == 100)
                           for p in fails))
    return out


# ---- what the agent is told ----

def describe(spec, examples):
    rules = {
        "dies": "the number of dies (cells that are 1 or 2)",
        "fails": "the number of failing dies (cells that are 2)",
        "yield_permille": "floor(1000 * (dies - fails) / dies), an integer division",
        "largest_cluster": "the size of the largest cluster of failing dies, where two failing dies are in the same "
                           "cluster when they are joined through neighbours ({connectivity} neighbours: "
                           "{nb}); 0 when there are no failing dies",
        "clusters": "the number of clusters of failing dies ({connectivity} neighbours: {nb}) that have at least "
                    "{min_size} dies",
        "edge_fails": "the number of failing dies that have at least one of their {connectivity} neighbours "
                      "({nb}) equal to 0 or outside the map",
        "ring_fails": "the number of failing dies in the ring from {lo}% to {hi}% of the largest die distance from the "
                      "centre: with d2 = (2*r - (R-1))^2 + (2*c - (C-1))^2 for the cell in row r, column c "
                      "(counting from 0), and D2 the largest d2 of any die (1 or 2), a failing die counts when "
                      "{lo}^2 * D2 <= 10000 * d2 and {cond}. Use 64-bit integers: 10000 * d2 can exceed 2^31",
    }
    nb = {4: "up, down, left, right", 8: "the 8 around it, diagonals included"}
    lines = []
    for i, f in enumerate(spec["fields"], 1):
        cond = "nothing more (the ring goes to 100%: the farthest dies count too)" if f.get("hi") == 100 \
            else f"10000 * d2 < {f.get('hi')}^2 * D2"
        lines.append(f"{i}. {_label(f)}: " + rules[f["name"]].format(**f, nb=nb.get(f.get("connectivity")),
                                                                         cond=cond))
    ex = "\n\n".join(f"Example {k} input:\n{i}Expected output:\n{o}" for k, (i, o) in enumerate(examples, 1))
    return f"""Write a program that measures one wafer map.

Input (stdin): the first line is "R C", the number of rows and columns (each at most {MAX_SIDE}). Then R lines, \
each with C numbers separated by spaces: 0 = no die here (off the wafer), 1 = a good die, 2 = a failing die.
Output (stdout): one line with {len(spec['fields'])} integers separated by spaces, in this order:
{chr(10).join(lines)}

{ex}"""


# ---- cases ----

def _synthetic(rng, small):
    """A round wafer with some of the patterns WM-811K has: scatter, a blob, an edge ring, a scratch."""
    R = rng.randint(6, 12) if small else rng.randint(15, 70)
    C = R + rng.randint(-2, 3) if small else R + rng.randint(-5, 8)
    cy, cx, rad = (R - 1) / 2, (C - 1) / 2, min(R, C) / 2
    m = [[1 if (r - cy) ** 2 + (k - cx) ** 2 <= rad * rad else 0 for k in range(C)] for r in range(R)]
    cells = [(r, k) for r in range(R) for k in range(C) if m[r][k]]
    for r, k in cells:
        if rng.random() < (0.05 if small else rng.choice([0.01, 0.05, 0.15])):
            m[r][k] = 2
    kinds = ["blob"] if small else rng.sample(["blob", "ring", "scratch", "blob", "none"], 2)
    for kind in kinds:
        if kind == "blob":
            br, bk = rng.choice(cells)
            s = rng.uniform(1, 2.5 if small else rad / 3)
            for r, k in cells:
                if (r - br) ** 2 + (k - bk) ** 2 <= s * s:
                    m[r][k] = 2
        elif kind == "ring":
            for r, k in cells:
                if (r - cy) ** 2 + (k - cx) ** 2 >= (rad - 1.5) ** 2 and rng.random() < 0.8:
                    m[r][k] = 2
        elif kind == "scratch":
            r, k = rng.choice(cells)
            dr, dk = rng.choice([(1, 1), (1, -1), (1, 0), (0, 1)])
            for _ in range(rng.randint(5, 3 * R)):
                if 0 <= r < R and 0 <= k < C and m[r][k]:
                    m[r][k] = 2
                r, k = r + dr, k + dk
    return m


def _text(m):
    return f"{len(m)} {len(m[0])}\n" + "".join(" ".join(map(str, row)) + "\n" for row in m)


def _parse(text):
    lines = text.split("\n")
    R, C = map(int, lines[0].split())
    return [[int(x) for x in lines[1 + r].split()] for r in range(R)]


def _probe(spec, rng):
    """A big synthetic wafer whose failing dies sit where a slightly wrong program differs: right at each ring's
    limits (Loop 42: a program whose ring constants Ratfor had turned 900_8 into 9008 passed 20 real maps and was
    wrong on 1 die in 150), diagonal-only neighbours (4 vs 8), dies on the map's border (outside counts as an edge)."""
    R, C = rng.randint(120, 220), rng.randint(120, 220)
    m = _synthetic(rng, False)
    cy, cx, rad = (R - 1) / 2, (C - 1) / 2, min(R, C) / 2 - rng.random()
    m = [[1 if (r - cy) ** 2 + (k - cx) ** 2 <= rad * rad else 0 for k in range(C)] for r in range(R)]
    for k in range(C):  # dies on the top and bottom border rows too
        if rng.random() < 0.3:
            m[0][k] = m[R - 1][k] = 2
    cells = [(r, k) for r in range(R) for k in range(C) if m[r][k]]
    d2 = {p: (2 * p[0] - (R - 1)) ** 2 + (2 * p[1] - (C - 1)) ** 2 for p in cells}
    D2 = max(d2.values())
    for f in spec["fields"]:
        if f["name"] == "ring_fails":
            for pct in (f["lo"], f["hi"]):
                t = pct * pct * D2
                if 0 < t < 10000 * D2:
                    near = sorted(cells, key=lambda p: abs(10000 * d2[p] - t))[:6]  # both sides, closest first
                    for r, k in near:
                        m[r][k] = 2
    for _ in range(rng.randint(5, 15)):  # pairs that touch only at a corner
        r, k = rng.choice(cells)
        if 0 < r < R - 1 and 0 < k < C - 1 and m[r + 1][k + 1]:
            m[r][k] = m[r + 1][k + 1] = 2
    return m


def cases(spec, seed, n, size=None, example=False):
    """n maps. Real maps (use_maps) when there are, from the part seed % 3; examples are the small ones. Without
    real maps, synthetic wafers. Beyond the examples, every fourth map is a probe (_probe)."""
    rng = random.Random(seed)
    pool = [m for i, m in enumerate(_POOL) if i % 3 == seed % 3]
    if example and pool:
        pool = sorted(pool, key=lambda m: len(m) * len(m[0]))[:max(n * 20, 40)]
    out = []
    for k in range(n):
        probe = not example and k % 4 == 3
        m = _probe(spec, rng) if probe else rng.choice(pool) if pool else _synthetic(rng, example)
        out.append({"id": f"{k + 1}", "input": _text(m), "categories": [_label(f) for f in spec["fields"]],
                    "probe": probe})
    return out


def expected(spec, text):
    return " ".join(map(str, measure(spec, _parse(text)))) + "\n"


def judge(spec, case, output):
    want = measure(spec, _parse(case["input"]))
    toks = output.split()
    if len(toks) != len(want) or not all(re.fullmatch(r"-?[0-9]{1,9}", t) for t in toks):
        return {"format": [f"expected one line of {len(want)} integers, got {len(toks)} numbers/words"
                           + (f" starting {' '.join(toks[:6])!r}" if toks else "")], "wrong": [], "total": len(want)}
    R, C = map(int, case["input"].split("\n")[0].split())
    wrong = [{"item": f"{_label(f)}", "category": _label(f),
              "detail": f"map {R}x{C}: {_label(f)} expected {w}, got {g}"}
             for f, w, g in zip(spec["fields"], want, map(int, toks)) if w != g]
    return {"format": [], "wrong": wrong, "total": len(want)}


def random_spec(seed):
    """3-5 fields for the evaluation, with the parameters that are easy to get wrong."""
    rng = random.Random(seed)
    names = rng.sample(["dies", "fails", "yield_permille", "largest_cluster", "clusters", "edge_fails", "ring_fails",
                        "largest_cluster", "edge_fails", "ring_fails"], rng.randint(3, 5))
    fields = []
    for n in names:
        f = {"name": n}
        if n in ("largest_cluster", "clusters", "edge_fails"):
            f["connectivity"] = rng.choice([4, 8])
        if n == "clusters":
            f["min_size"] = rng.choice([2, 3, 5, 10])
        if n == "ring_fails":
            f["lo"], f["hi"] = rng.choice([(0, 30), (30, 70), (70, 100), (50, 100), (0, 100), (80, 100)])
        fields.append(f)
    return validate({"fields": fields})
