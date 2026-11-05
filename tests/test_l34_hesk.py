"""Loop 43: Hesk, a made-up language as the stand-in (tests/fixtures/toolchains/hesk). Ratfor is real, so a model may
know it; Hesk exists only in this repo. The checker's errors (stage, line, message), the run time (bounds, div/mod,
the loop variable), a wafer map program written in Hesk against the role's oracle (and fast enough on the largest
maps), and the codegen loop with the evaluation's agent (bench/agents/haiku-hesk) on scripted replies.
Docker (sandbox image); no LLM.  python tests/test_l34_hesk.py [test_name ...]"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent import codegen, delegate  # noqa: E402
from agent.loop import Context  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l34-"))
TOOLCHAINS = ROOT / "tests" / "fixtures" / "toolchains"
TC = codegen.discover(TOOLCHAINS)["hesk"]
ROLE = codegen.discover_roles()["wafermap"]
SPEC = ROLE.validate({"fields": [
    {"name": "dies"}, {"name": "fails"}, {"name": "yield_permille"},
    {"name": "largest_cluster", "connectivity": 4}, {"name": "largest_cluster", "connectivity": 8},
    {"name": "clusters", "connectivity": 4, "min_size": 3}, {"name": "edge_fails", "connectivity": 8},
    {"name": "ring_fails", "lo": 70, "hi": 100}]})


def box():
    sb = Sandbox(TMP / f"ws-{time.monotonic_ns()}", mounts={TOOLCHAINS: codegen.MOUNT})
    return sb, sb.fresh()


def build(b, src, name="p"):
    return codegen.compile_source(b, TC, src.encode(), name)


def run(b, src, stdin=""):
    ok, errs, bin = build(b, src)
    assert ok, errs
    r = codegen.run_cases(b, TC, bin, [{"id": "x", "input": stdin}], "r")["x"]
    return r["rc"], r["out"], r["err"]


def t_checker_errors():  # what the agent hears: the stage, the line it was noticed on, what was expected
    sb, b = box()
    bad = [
        ("var x.\nx = 3.\n", "syntax", 2, "expected '<-' (assignment)"),
        ("var x;\n", "syntax", 1, "unexpected character ';'"),
        ("var x.\nx <- 5 / 2.\n", "syntax", 2, "unexpected character '/'"),
        ("var x.\nwhile x < 3 do\n  x <- x + 1.\n", "syntax", 4, "expected 'done' before the end"),
        ("var x.\nif x > 1 then say x. done.\n", "syntax", 2, "no '.' after 'done'"),
        ("var x.\nx <- 1 < x < 3.\n", "syntax", 2, "do not chain"),
        ("var x.\nfor x from 0 to 3 do done\n", "syntax", 2, "expected 'below' or 'through'"),
        ("var x\nsay x.\n", "syntax", 2, "expected '.' at the end of the statement, found 'say'"),
        ("x <- 1.\n", "check", 1, "'x' is not declared"),
        ("var g[2, 3].\ng[1] <- 3.\n", "check", 2, "'g' has 2 indexes, not 1"),
        ("var a[3].\nsay a.\n", "check", 2, "'a' is an array"),
        ("var x.\nif x then say x. done\n", "check", 2, "expected a condition"),
        ("var x.\nx <- (x > 1) + 1.\n", "check", 2, "'+' needs two numbers"),
        ("var i.\nfor i from 0 below 3 do\n  i <- i + 1.\ndone\n", "check", 3, "cannot be changed"),
        ("var x.\nif x = 1 then\n  var y.\ndone\n", "check", 3, "only at the top level"),
        ("var x.\nstop.\n", "check", 2, "outside a while/for loop"),
        ("var x, x.\n", "check", 1, "declared twice"),
        ("var x.\nx <- min(1).\n", "check", 2, "min takes 2 arguments"),
        ("var done.\n", "syntax", 1, "'done' is a keyword"),
    ]
    try:
        for src, stage, line, msg in bad:
            ok, errs, _ = build(b, src)
            assert not ok and errs[0]["stage"] == stage and errs[0]["line"] == line and msg in errs[0]["message"], \
                (src, errs)
        # mistakes that do not stop the parse are all reported, up to the first syntax error
        ok, errs, _ = build(b, "var a[2].\na <- 1.\ny <- 2.\nstop.\nsay a[0]\n")
        assert [(e["stage"], e["line"]) for e in errs] == [("check", 2), ("check", 3), ("check", 4), ("syntax", 6)], errs
    finally:
        b.close()
        sb.close()


def t_run_time():  # the semantics the reference promises, and run errors with their line
    sb, b = box()
    try:
        src = """-- comment
var n, i, s, v, a[3].
read n.
for i from 0 below n do read v. if v > 0 and i mod 2 = 0 then s <- s + v. done done
say s, i, "text", -7 div 2, -7 mod 2, 7 mod -2, min(3, 4), max(3, 4), abs(-5), 2 * 3 + 4 - 1.
for i from 5 below 5 do say "never". done
for v from 1 through 3 do a[v - 1] <- v * 10. if v = 2 then stop. done done
say i, v, a[0], a[1], a[2], 10000 * 10000 * 10000.
while 1 = 1 do skip. done
"""
        ok, errs, bin = build(b, src)
        assert ok, errs
        r = codegen.run_cases(b, TC, bin, [{"id": "x", "input": "4\n5 -1\n7 8"}], "r")["x"]
        assert r["rc"] is None and "timed out" in r["err"], r  # the while 1 = 1: the harness's time limit
        assert run(b, src.replace("while 1 = 1 do skip. done", ""), "4\n5 -1\n7 8") == \
            (0, "12 3 text -3 -1 1 3 4 5 9\n3 2 10 20 0 1000000000000\n", ""), "semantics"
        for src, stdin, msg in [("var a[3], i.\nfor i from 0 through 3 do\n  a[i] <- 1.\ndone\n", "", "line 3: index 3"),
                                ("var g[2, 2].\nsay g[0, -1].\n", "", "line 2: index 0, -1 is outside"),
                                ("var x.\n\nsay 1 div x.\n", "", "line 3: division by zero"),
                                ("var x, y.\nread x.\nread y.\n", "5", "line 3: read: no more input"),
                                ("var x.\nread x.\n", "2.5", "is not an integer"),
                                ("var n.\nread n.\nvar a[n].\n", "0", "line 3: array size 0")]:
            rc, out, err = run(b, src, stdin)
            assert rc == 1 and msg in err, (src, rc, err)
    finally:
        b.close()
        sb.close()


def wafermap_program(spec):
    """The wafer map role in Hesk, written apart from the oracle: one flood fill per field (no functions)."""
    head = """-- wafer map measurements (tests/test_l34_hesk.py)
var R, C, r, c, k, a, b, x, y, top, n, best, count, dies, fails, e, d2, D2, hit.
var dr[8], dc[8].
read R, C.
var g[R, C], seen[R, C], qr[R * C], qc[R * C], res[%d].
dr[0] <- -1. dc[0] <- 0.  dr[1] <- 1. dc[1] <- 0.  dr[2] <- 0. dc[2] <- -1.  dr[3] <- 0. dc[3] <- 1.
dr[4] <- -1. dc[4] <- -1. dr[5] <- -1. dc[5] <- 1. dr[6] <- 1. dc[6] <- -1. dr[7] <- 1. dc[7] <- 1.
for r from 0 below R do
  for c from 0 below C do
    read g[r, c].
    if g[r, c] > 0 then dies <- dies + 1. done
    if g[r, c] = 2 then fails <- fails + 1. done
    d2 <- (2 * r - (R - 1)) * (2 * r - (R - 1)) + (2 * c - (C - 1)) * (2 * c - (C - 1)).
    if g[r, c] > 0 and d2 > D2 then D2 <- d2. done
  done
done
""" % len(spec["fields"])
    flood = """for r from 0 below R do for c from 0 below C do seen[r, c] <- 0. done done
best <- 0. count <- 0.
for r from 0 below R do
  for c from 0 below C do
    if g[r, c] = 2 and seen[r, c] = 0 then
      seen[r, c] <- 1. qr[0] <- r. qc[0] <- c. top <- 1. n <- 0.
      while top > 0 do
        top <- top - 1. a <- qr[top]. b <- qc[top]. n <- n + 1.
        for k from 0 below {conn} do
          x <- a + dr[k]. y <- b + dc[k].
          if x >= 0 and x < R and y >= 0 and y < C then
            if g[x, y] = 2 and seen[x, y] = 0 then
              seen[x, y] <- 1. qr[top] <- x. qc[top] <- y. top <- top + 1.
            done
          done
        done
      done
      best <- max(best, n).
      if n >= {min_size} then count <- count + 1. done
    done
  done
done
"""
    body = []
    for i, f in enumerate(spec["fields"]):
        name = f["name"]
        if name in ("largest_cluster", "clusters"):
            body.append(flood.format(conn=f["connectivity"], min_size=f.get("min_size", 1)))
            body.append(f"res[{i}] <- {'best' if name == 'largest_cluster' else 'count'}.\n")
        elif name == "edge_fails":
            body.append(f"""e <- 0.
for r from 0 below R do
  for c from 0 below C do
    if g[r, c] = 2 then
      hit <- 0.
      for k from 0 below {f['connectivity']} do
        x <- r + dr[k]. y <- c + dc[k].
        if x < 0 or x >= R or y < 0 or y >= C then hit <- 1.
        elif g[x, y] = 0 then hit <- 1.
        done
      done
      e <- e + hit.
    done
  done
done
res[{i}] <- e.
""")
        elif name == "ring_fails":
            upper = "1 = 1" if f["hi"] == 100 else f"10000 * d2 < {f['hi'] * f['hi']} * D2"
            body.append(f"""e <- 0.
for r from 0 below R do
  for c from 0 below C do
    d2 <- (2 * r - (R - 1)) * (2 * r - (R - 1)) + (2 * c - (C - 1)) * (2 * c - (C - 1)).
    if g[r, c] = 2 and {f['lo'] * f['lo']} * D2 <= 10000 * d2 and {upper} then e <- e + 1. done
  done
done
res[{i}] <- e.
""")
        else:
            body.append(f"res[{i}] <- " + {"dies": "dies", "fails": "fails",
                                           "yield_permille": "1000 * (dies - fails) div dies"}[name] + ".\n")
    return head + "".join(body) + "say " + ", ".join(f"res[{i}]" for i in range(len(spec["fields"]))) + ".\n"


def t_wafermap_in_hesk():  # the language can do the role, within the harness's time limit on the largest maps
    sb, b = box()
    try:
        ok, errs, bin = build(b, wafermap_program(SPEC))
        assert ok, errs
        cases = ROLE.cases(SPEC, 7, 60)
        res = codegen.run_cases(b, TC, bin, cases, "agree")
        bad = [(c["id"], res[c["id"]], ROLE.judge(SPEC, c, res[c["id"]]["out"])) for c in cases]
        bad = [x for x in bad if x[1]["rc"] != 0 or x[2]["wrong"] or x[2]["format"]]
        assert not bad, bad[:2]
        side = ROLE.MAX_SIDE  # a full wafer of fails: the biggest flood fill there can be
        big = f"{side} {side}\n" + "\n".join(" ".join(["2"] * side) for _ in range(side)) + "\n"
        t = time.time()
        r = codegen.run_cases(b, TC, bin, [{"id": "big", "input": big}], "big")["big"]
        took = time.time() - t
        assert r["rc"] == 0 and r["out"].split()[:2] == [str(side * side)] * 2, r
        assert took < codegen.CASE_SECONDS * 0.8, f"{took:.1f}s on a {side}x{side} map"
        print(f"  {side}x{side} all fails: {took:.1f}s (limit {codegen.CASE_SECONDS}s)")
    finally:
        b.close()
        sb.close()


def t_loop_with_the_agent():  # the evaluation's agent, on scripted replies: syntax error -> wrong -> right
    agents = TMP / "agents"
    agents.mkdir(exist_ok=True)
    shutil.copy(ROOT / "agents" / "_runner.py", agents / "_runner.py")
    shutil.copytree(ROOT / "bench" / "agents" / "haiku-hesk", agents / "haiku-hesk", dirs_exist_ok=True)
    spec = ROLE.validate({"fields": [{"name": "dies"}, {"name": "fails"}]})
    right = wafermap_program(spec)
    replies = ["```hesk\nvar R, C.\nread R, C\nsay R.\n```",                   # no '.' after read
               "```hesk\nvar R, C.\nread R, C.\nsay R * C, 0.\n```",            # builds, wrong answers
               f"Here it is:\n```hesk\n{right}```"]
    seen = []

    def llm(msgs):
        seen.append(msgs)
        return replies[len(seen) - 1]
    sb = Sandbox(TMP / "loop", mounts={agents: delegate.MOUNT, TOOLCHAINS: codegen.MOUNT})
    try:
        ctx = Context(sandbox=sb, agents=delegate.discover(agents), toolchains=codegen.discover(TOOLCHAINS),
                      roles={"wafermap": ROLE}, llm=llm)
        r = codegen.loop(ctx, "hesk", "", "w1", 4, role="wafermap", spec=spec)
        assert r["stop"] is None and len(r["history"]) == 3, r["stop"]
        assert "Hesk" in seen[0][0]["content"] and "```hesk" in seen[0][0]["content"], "the reference is the prompt"
        assert "[syntax line 3] expected '.' at the end of the statement" in seen[1][-1]["content"], seen[1][-1]
        assert "example 1" in seen[2][-1]["content"] and "expected" in seen[2][-1]["content"], seen[2][-1]
    finally:
        sb.close()


if __name__ == "__main__":
    only, failed = sys.argv[1:], []
    for name, fn in list(globals().items()):
        if name.startswith("t_") and (not only or name in only):
            try:
                t = time.time(); fn(); print(f"PASS {name} ({time.time() - t:.0f}s)")
            except Exception as e:
                failed.append(name)
                print("FAIL", name, repr(e)[:1500])
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if failed else 0)
