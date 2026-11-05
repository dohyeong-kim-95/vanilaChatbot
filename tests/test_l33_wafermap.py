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
SPEC2 = ROLE.validate({"fields": [{"name": "fails"}, {"name": "ring_fails", "lo": 0, "hi": 30}]})  # Haiku's 900_8

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


def program(mistake, spec=SPEC):
    return (PROGRAM % (json.dumps(spec), mistake)).encode()


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
        # the kind mistake is right on every plain map: only the probes (dies at a ring's limit) see it, at hi too
        for spec in (SPEC, SPEC2):
            ok, errs, bin = codegen.compile_source(box, TC, program("kind", spec), "kind2")
            cases = ROLE.cases(spec, 2, 40)
            res = codegen.run_cases(box, TC, bin, cases, "plain")
            wrong = {c["probe"] for c in cases if ROLE.judge(spec, c, res[c["id"]]["out"])["wrong"]}
            assert wrong == {True}, (spec, wrong)
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


def t_killed_harness_tells_nothing():  # review loop 10 Must: hidden inputs written to the harness's stdout, then kill
    leak = b"""import os, sys
data = sys.stdin.read()
os.write(os.open("/proc/%d/fd/1" % os.getppid(), os.O_WRONLY), data.encode())
os.kill(os.getppid(), 9)
"""
    ws = TMP / "kill"
    sb = Sandbox(ws)
    box = sb.fresh()
    try:
        ok, errs, bin = codegen.compile_source(box, TC, leak, "kill")
        assert ok, errs
        hidden = [c["input"].split("\n")[1] for c in ROLE.cases(SPEC, *codegen.HIDDEN)]
        try:
            codegen.run_cases(box, TC, bin, ROLE.cases(SPEC, *codegen.HIDDEN), "kill")
            raise AssertionError("the harness survived")
        except RuntimeError as e:
            text = str(e)
        assert "harness was killed" in text and not [h for h in hidden if h[:30] in text], text[:300]
    finally:
        box.close()
        sb.close()


def t_probes_sit_on_every_limit():  # review 42.1: near a limit was not enough (caught 1 in 8 at 70%)
    import random
    for lo, hi in [(0, 30), (30, 70), (70, 100), (50, 100), (80, 100)]:
        spec = ROLE.validate({"fields": [{"name": "ring_fails", "lo": lo, "hi": hi}]})
        for seed in range(6):
            m = ROLE._probe(spec, random.Random(seed))
            R, C = len(m), len(m[0])
            cells = [(r, k) for r in range(R) for k in range(C) if m[r][k]]
            d2 = {p: (2 * p[0] - (R - 1)) ** 2 + (2 * p[1] - (C - 1)) ** 2 for p in cells}
            D2 = max(d2.values())
            for pct in (lo, hi):
                if pct < 100:
                    assert [p for p in cells if m[p[0]][p[1]] == 2 and 10000 * d2[p] == pct * pct * D2], (lo, hi, pct)


def t_harness_limits():  # review 42.1: a program that floods stdout loses its own case, not the batch
    ws = TMP / "flood"
    sb = Sandbox(ws)
    box = sb.fresh()
    try:
        ok, errs, bin = codegen.compile_source(box, TC, b"import sys\nif sys.stdin.read().startswith('1 '):\n"
                                               b"    while True: print('7' * 4096)\nprint(1)\n", "flood")
        cases = [{"id": "a", "input": "1 1\n2\n"}, {"id": "b", "input": "2 1\n1\n1\n"}]
        res = codegen.run_cases(box, TC, bin, cases, "flood")
        assert "printed more than" in res["a"]["err"] and res["b"] == {"rc": 0, "out": "1\n", "err": ""}, res
    finally:
        box.close()
        sb.close()


def t_no_inputs_kept_across_attempts():  # review 42.1 Must: attempt 1 keeps the hidden maps, attempt 2 tells them
    import shutil
    from agent import delegate
    from agent.loop import Context
    agents = TMP / "agents"
    shutil.copytree(ROOT / "agents", agents)
    shutil.copytree(ROOT / "tests" / "fixtures" / "agents-binning" / "py-snoop", agents / "py-snoop")
    sb = Sandbox(TMP / "keep", mounts={agents: delegate.MOUNT, ROOT / "tests" / "fixtures" / "toolchains": codegen.MOUNT})
    ctx = Context(sandbox=sb, agents=delegate.discover(agents), roles={"wafermap": ROLE},
                  toolchains=codegen.discover(ROOT / "tests" / "fixtures" / "toolchains"))
    try:
        r = codegen.loop(ctx, "python", "", "keep", attempts=2, role="wafermap", spec=SPEC)
        said = json.dumps(r["history"][1]["errors"])
        assert "KEPT 0" in said, said[:500]
    finally:
        sb.close()


def t_real_maps_are_split():  # examples, hidden checks and an audit never share a map
    maps = [[[1, 2] + [k // 3 ** i % 3 for i in range(4)]] for k in range(60)]  # 60 different one-row maps
    ROLE.use_maps(maps + maps[::-1])  # review 42.1: the same map twice, at other places, is kept once
    assert len(ROLE._POOL) == 60
    try:
        seen = {s: {c["input"] for c in ROLE.cases(SPEC, s, 50)} for s in (1, 2, 3)}
        assert not (seen[1] & seen[2]) and not (seen[2] & seen[3]) and not (seen[1] & seen[3]), seen
        small = ROLE.cases(SPEC, 1, 2, example=True)
        assert all(c["input"].startswith("1 6") for c in small)
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
