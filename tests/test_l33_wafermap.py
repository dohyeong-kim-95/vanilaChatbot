"""Loop 42: the wafer map role (roles/wafermap), checked apart from any model.
The oracle by hand, then against a second implementation written here (the "right" program below: iterative BFS,
no shared code), and the guards on programs with one wafer-map mistake each. The programs are Python on purpose:
a role does not know the language (tests/fixtures/toolchains/python). Synthetic maps: the real WM-811K maps are not
in the repo (bench/codegen_eval.py --role wafermap uses them).
Docker (sandbox image); no LLM.  python tests/test_l33_wafermap.py [test_name ...]"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent import codegen  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l33-"))
ROLE = codegen.discover_roles()["wafermap"]
TC = codegen.discover(ROOT / "tests" / "fixtures" / "toolchains")["python"]
SPEC = ROLE.validate({"fields": [
    {"name": "dies"}, {"name": "fails"}, {"name": "yield_permille"},
    {"name": "largest_cluster", "connectivity": 4}, {"name": "largest_cluster", "connectivity": 8},
    {"name": "clusters", "connectivity": 4, "min_size": 3}, {"name": "edge_fails", "connectivity": 8},
    {"name": "ring_fails", "lo": 70, "hi": 100}]})

# A second implementation of the role, and the mistakes a weak model makes in it (MISTAKE picks one).
PROGRAM = r'''
import sys
from collections import deque
SPEC, MISTAKE = %s, %r
tok = sys.stdin.read().split()
R, C = int(tok[0]), int(tok[1])
g = [[int(tok[2 + r * C + c]) for c in range(C)] for r in range(R)]
N4 = [(-1, 0), (1, 0), (0, -1), (0, 1)]
N8 = N4 + [(-1, -1), (-1, 1), (1, -1), (1, 1)]

def nb(k):
    return N8 if (k == 8 or MISTAKE == "diagonal") else N4

def sizes(k):
    seen, out = set(), []
    for r in range(R):
        for c in range(C):
            if g[r][c] == 2 and (r, c) not in seen:
                if MISTAKE == "recursion":
                    sys.setrecursionlimit(100)
                    def dfs(a, b):
                        seen.add((a, b))
                        return 1 + sum(dfs(a + x, b + y) for x, y in nb(k)
                                       if 0 <= a + x < R and 0 <= b + y < C and g[a + x][b + y] == 2
                                       and (a + x, b + y) not in seen)
                    out.append(dfs(r, c))
                    continue
                q, n = deque([(r, c)]), 0
                seen.add((r, c))
                while q:
                    a, b = q.popleft()
                    n += 1
                    for x, y in nb(k):
                        p = (a + x, b + y)
                        if 0 <= p[0] < R and 0 <= p[1] < C and g[p[0]][p[1]] == 2 and p not in seen:
                            seen.add(p)
                            q.append(p)
                out.append(n)
    return out

dies = R * C if MISTAKE == "zeros" else sum(v > 0 for row in g for v in row)
fails = sum(v == 2 for row in g for v in row)
res = []
for f in SPEC["fields"]:
    n = f["name"]
    if n == "dies": res.append(dies)
    elif n == "fails": res.append(fails)
    elif n == "yield_permille": res.append((dies - fails) * 1000 // dies)
    elif n == "largest_cluster": res.append(max(sizes(f["connectivity"]) or [0]))
    elif n == "clusters": res.append(len([s for s in sizes(f["connectivity"]) if s >= f["min_size"]]))
    elif n == "edge_fails":
        e = 0
        for r in range(R):
            for c in range(C):
                if g[r][c] == 2:
                    for x, y in nb(f["connectivity"]):
                        a, b = r + x, c + y
                        inside = 0 <= a < R and 0 <= b < C
                        if (not inside and MISTAKE != "inside") or (inside and g[a][b] == 0):
                            e += 1
                            break
        res.append(e)
    elif n == "ring_fails":
        if MISTAKE == "centre":  # the centre at R/2, C/2 instead of (R-1)/2, (C-1)/2
            d2 = lambda r, c: (2 * r - R) ** 2 + (2 * c - C) ** 2
        else:
            d2 = lambda r, c: (2 * r - (R - 1)) ** 2 + (2 * c - (C - 1)) ** 2
        D2 = max(d2(r, c) for r in range(R) for c in range(C) if g[r][c])
        lo, hi = f["lo"], f["hi"]
        K, L, H = 10000, lo * lo, hi * hi
        if MISTAKE == "kind":  # Haiku wrote 10000_8 and 4900_8: Ratfor drops the "_", Fortran reads 100008, 49008
            K, L, H = 100008, int(f"{L}8"), int(f"{H}8")
        res.append(sum(1 for r in range(R) for c in range(C) if g[r][c] == 2 and L * D2 <= K * d2(r, c)
                       and (K * d2(r, c) < H * D2 or hi == 100)))
print(*res)
'''


def program(mistake):
    return (PROGRAM % (json.dumps(SPEC), mistake)).encode()


def t_oracle_by_hand():
    m = [[0, 1, 1, 0],
         [1, 2, 1, 2],
         [1, 1, 2, 1],
         [0, 2, 2, 0]]
    spec = ROLE.validate({"fields": [
        {"name": "dies"}, {"name": "fails"}, {"name": "yield_permille"},
        {"name": "largest_cluster", "connectivity": 4}, {"name": "largest_cluster", "connectivity": 8},
        {"name": "clusters", "connectivity": 8, "min_size": 2}, {"name": "edge_fails", "connectivity": 4},
        {"name": "edge_fails", "connectivity": 8}, {"name": "ring_fails", "lo": 0, "hi": 50},
        {"name": "ring_fails", "lo": 50, "hi": 100}][:8]})
    # 12 dies, 5 fails, yield 583; 4-clusters: (1,1) | (1,3) | (2,2),(3,2),(3,1) -> 3; 8: (1,1),(2,2),(1,3),(3,1),(3,2)
    # -> 5; 8-clusters >= 2: 1. edge 4: (1,3) right is outside, (3,1) left is 0, (3,2) right is 0 / down outside -> 3;
    # edge 8: also (1,1) ((0,0)=0 diagonally) and (2,2) ((3,3)=0 diagonally) -> 5
    assert ROLE.measure(spec, m) == [12, 5, 583, 3, 5, 1, 3, 5], ROLE.measure(spec, m)
    ring = ROLE.validate({"fields": [{"name": "ring_fails", "lo": 0, "hi": 50},
                                     {"name": "ring_fails", "lo": 50, "hi": 100}]})
    # d2 with the centre (1.5, 1.5): (1,1)=2, (1,3)=10, (2,2)=2, (3,1)=10, (3,2)=10; D2 = 10 (the corners are 0)
    # 50%: 10000*d2 < 2500*10 -> d2 < 2.5: (1,1), (2,2) -> 2; the rest 3
    assert ROLE.measure(ring, m) == [2, 3]
    assert ROLE.measure(ROLE.validate({"fields": [{"name": "largest_cluster", "connectivity": 4}]}),
                        [[1, 1], [1, 1]]) == [0]
    for bad, msg in [({"fields": []}, "1-8"), ({"fields": [{"name": "area"}]}, "unknown"),
                     ({"fields": [{"name": "edge_fails", "connectivity": 6}]}, "connectivity"),
                     ({"fields": [{"name": "ring_fails", "lo": 50, "hi": 50}]}, "lo < hi")]:
        try:
            ROLE.validate(bad)
            raise AssertionError(bad)
        except ValueError as e:
            assert msg in str(e), e


def t_second_implementation_agrees():  # the "right" program, written apart, on 200 synthetic wafers
    ws = TMP / "agree"
    sb = Sandbox(ws)
    box = sb.fresh()
    try:
        ok, errs, bin = codegen.compile_source(box, TC, program("none"), "right")
        assert ok, errs
        cases = ROLE.cases(SPEC, 7, 200)
        res = codegen.run_cases(box, TC, bin, cases, "agree")
        bad = [(c["id"], ROLE.judge(SPEC, c, res[c["id"]]["out"])) for c in cases]
        bad = [b for b in bad if b[1]["wrong"] or b[1]["format"]]
        assert not bad, bad[:3]
        sizes = [tuple(map(int, c["input"].split()[:2])) for c in cases]
        assert max(r for r, _ in sizes) >= 60, "the wafers are not all small"
    finally:
        box.close()
        sb.close()


def t_each_mistake_is_caught():
    ws = TMP / "modes"
    sb = Sandbox(ws)
    box = sb.fresh()
    want = {"diagonal": "largest_cluster(connectivity=4)", "zeros": "dies", "inside": "edge_fails(connectivity=8)",
            "centre": "ring_fails(lo=70, hi=100)", "recursion": None, "kind": "ring_fails(lo=70, hi=100)"}
    try:
        for mistake, kind in want.items():
            ok, errs, bin = codegen.compile_source(box, TC, program(mistake), mistake)
            assert ok, errs
            errs, score = codegen.test_program(box, TC, bin, ROLE, SPEC, codegen.GUARDS, mistake)
            assert score["hidden"][0] > 0, (mistake, score)
            msg = errs[-1]["message"]
            assert msg.startswith("hidden checks"), (mistake, errs)
            if kind:
                assert kind in msg, (mistake, msg)
            else:  # a crash on the big blobs: the run failed
                assert "failed to run" in msg, (mistake, msg)
        # the kind mistake is right on every real-looking map: only the probes (dies at a ring's limit) see it
        ok, errs, bin = codegen.compile_source(box, TC, program("kind"), "kind2")
        plain = [c for c in ROLE.cases(SPEC, 2, 40) if not c["probe"]]
        res = codegen.run_cases(box, TC, bin, plain, "plain")
        assert all(not ROLE.judge(SPEC, c, res[c["id"]]["out"])["wrong"] for c in plain[:5]), "not subtle"
        ok, errs, bin = codegen.compile_source(box, TC, b"print(1\n", "syntax")
        assert not ok and errs[0]["line"] == 1 and "SyntaxError" in errs[0]["message"], errs
    finally:
        box.close()
        sb.close()


def t_program_cannot_read_hidden_inputs():  # review 41.1: no hidden input on the program's disk, while it runs
    snoop = rb"""
import glob, os, sys
seen = []
for root in ("/workspace", "/tmp"):
    for p in glob.glob(root + "/**", recursive=True):
        try:
            if os.path.isfile(p):
                seen.append(open(p, errors="replace").read())
        except OSError:
            pass
text = "\\n".join(seen)
i = text.find('"id": "h')  # the hidden cases, if they are anywhere on disk
sys.stderr.write(text[i:i + 300] if i >= 0 else "nothing")
sys.exit(1)
"""
    ws = TMP / "snoop"
    sb = Sandbox(ws)
    box = sb.fresh()
    try:
        ok, errs, bin = codegen.compile_source(box, TC, snoop, "snoop")
        assert ok, errs
        errs, _ = codegen.test_program(box, TC, bin, ROLE, SPEC, codegen.GUARDS, "snoop")
        hidden = {c["input"].split("\n")[1] for c in ROLE.cases(SPEC, *codegen.HIDDEN)}
        text = json.dumps(errs)
        assert errs and not [h for h in hidden if h[:40] in text] and '"id": "h' not in text, text[:600]
        assert "No such file" in box.exec("ls /workspace/outputs/snoop-h /workspace/outputs/snoop-p")
    finally:
        box.close()
        sb.close()


def t_real_maps_are_split():  # examples, hidden checks and an audit never share a map
    maps = [[[1, 2, k % 3, (k // 3) % 3]] for k in range(60)]
    ROLE.use_maps(maps)
    try:
        seen = {s: {c["input"] for c in ROLE.cases(SPEC, s, 50)} for s in (1, 2, 3)}
        assert not (seen[1] & seen[2]) and not (seen[2] & seen[3]) and not (seen[1] & seen[3]), seen
        small = ROLE.cases(SPEC, 1, 2, example=True)
        assert all(c["input"].startswith("1 4") for c in small)
    finally:
        ROLE.use_maps([])
    assert ROLE.cases(SPEC, 1, 1)[0]["input"].split()[0] != "1"  # synthetic again


if __name__ == "__main__":
    only, failed = sys.argv[1:], []
    for name, fn in list(globals().items()):
        if name.startswith("t_") and (not only or name in only):
            try:
                t = time.time(); fn(); print(f"PASS {name} ({time.time() - t:.0f}s)")
            except Exception as e:
                failed.append(name)
                print("FAIL", name, repr(e)[:1500])
    sys.exit(1 if failed else 0)
