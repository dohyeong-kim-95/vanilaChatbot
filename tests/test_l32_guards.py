"""Loop 41: guards against generated code that builds but does the wrong thing (roles/, agent/codegen.py).
A mock codegen makes one weak-model mistake per attempt; each must be caught, and by the right guard.
Docker (sandbox image with ratfor + gfortran); no LLM. The model evaluation is bench/codegen_eval.py.
python tests/test_l32_guards.py [test_name ...]"""
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "agents-binning" / "bin-mock"))
import mock  # noqa: E402
from agent import codegen, delegate  # noqa: E402
from agent.loop import Context  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402
from agent.tools import _schema  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l32-"))
TOOLCHAINS = ROOT / "tests" / "fixtures" / "toolchains"
ROLE = codegen.discover_roles()["binning"]
SPEC = ROLE.validate({"tests": [{"name": "VDD", "lo": 0.9, "hi": 1.1, "bin": 3, "priority": 2},
                                {"name": "IDDQ", "lo": None, "hi": 50, "bin": 5, "priority": 1, "missing": "skip"},
                                {"name": "FMAX", "lo": 1000, "hi": None, "bin": 7, "priority": 3}],
                      "missing_bin": 9, "marginal_pct": 5, "marginal_bin": 2})


def ctx(name):
    agents = TMP / "agents"
    if not agents.exists():
        shutil.copytree(ROOT / "agents", agents)
        shutil.copytree(ROOT / "tests" / "fixtures" / "agents-binning", agents, dirs_exist_ok=True)
    ws = TMP / name
    ws.mkdir()
    sb = Sandbox(ws, mounts={agents: delegate.MOUNT, TOOLCHAINS: codegen.MOUNT})
    return Context(sandbox=sb, agents=delegate.discover(agents), toolchains=codegen.discover(TOOLCHAINS),
                   roles={"binning": ROLE})


def t_oracle():  # the answers everything is checked against: by hand first
    for vals, want in [((1.0, 20, 1200), 1), ((1.15, 80, 1200), 5), ((1.15, 20, 900), 3),  # priority, not order
                       ((1.0, -9999, 1200), 1), ((-9999, 80, 1200), 5), ((-9999, 20, 1200), 9),  # skip / missing
                       ((1.1, 20, 1200), 2), ((1.06, 20, 1200), 2), ((1.04, 20, 1200), 1),  # within 5% of 1.1
                       ((1.0, 48, 1200), 2), ((1.0, 47.4, 1200), 1), ((1.0, 20, 1049), 2), ((1.0, 20, 1051), 1),
                       ((0.8999, 50.001, 999.999), 5)]:
        assert ROLE.bin_of(SPEC, vals) == want, (vals, ROLE.bin_of(SPEC, vals))
    for c in ROLE.cases(SPEC, 5, 10, 25):  # every category in every input; the oracle's own output is judged right
        assert set(c["categories"]) == set(ROLE.CATEGORIES), c["categories"]  # this spec has all of them
        j = ROLE.judge(SPEC, c, ROLE.expected(SPEC, c["input"]))
        assert j == {"format": [], "wrong": [], "total": 25}, j
    c = ROLE.cases(SPEC, 5, 1, 5)[0]
    padded = "".join(f"{x:>12}{y:>12}{b:>12}\n" for x, y, b in ROLE._expect(SPEC, c["input"]))  # Fortran's write
    assert ROLE.judge(SPEC, c, padded)["wrong"] == []
    for bad, msg in [({"tests": []}, "non-empty"), ({**SPEC, "missing_bin": 1}, "differ"),
                     ({**SPEC, "marginal_bin": 9}, "marginal_bin must differ"), ({**SPEC, "marginal_pct": 80}, "50"),
                     ({"tests": [{"name": "a", "lo": 1, "bin": 2, "priority": 1},
                                 {"name": "b", "lo": 1, "bin": 3, "priority": 1}], "missing_bin": 4}, "priorities"),
                     ({"tests": [{"name": "a", "lo": 1, "bin": 2, "missing": "zero"}], "missing_bin": 4}, "skip"),
                     ({"tests": [{"name": "a b", "lo": 1, "bin": 2}], "missing_bin": 3}, "name"),
                     ({"tests": [{"name": "a", "bin": 2}], "missing_bin": 3}, "at least one"),
                     ({"tests": [{"name": "a", "lo": 2, "hi": 1, "bin": 2}], "missing_bin": 3}, "lo > hi")]:
        try:
            ROLE.validate(bad)
            raise AssertionError(bad)
        except ValueError as e:
            assert msg in str(e), e


def t_each_mistake_is_caught():  # which guard catches which mistake
    c = ctx("modes")
    tc, box = c.toolchains["ratfor"], c.sandbox.fresh()
    examples = [(x["input"], ROLE.expected(SPEC, x["input"]))
                for x in ROLE.cases(SPEC, *codegen.PUBLIC, example=True)]
    task = ROLE.describe(SPEC, examples)
    try:
        # the examples are plain dies: every mistake passes them, only the hidden checks see it (and name its kind)
        kinds = {"right": None, "strict": "boundary", "tableorder": "priority", "zero": "missing", "noskip": "skip",
                 "nomarginal": "marginal", "hardcode": None}
        for mode, kind in kinds.items():
            ok, errs, bin = codegen.compile_source(box, tc, mock.program(SPEC, mode, task).encode(), mode)
            assert ok, (mode, errs)
            errs, score = codegen.test_program(box, tc, bin, ROLE, SPEC, codegen.GUARDS, mode)
            assert score["public"] == [2, 2], (mode, score)
            assert (score["hidden"][0] == 0) == (mode == "right"), (mode, score)
            assert bool(errs) == (mode != "right")
            if kind:
                assert re.search(rf"[(,] ?{kind} \d+", errs[-1]["message"]), (mode, errs)
            if mode == "hardcode":
                assert score["hidden"] == [500, 500] and "wrong format" in errs[-1]["message"], score
            # without the guards every one of them would be taken as done
            assert codegen.test_program(box, tc, bin, ROLE, SPEC, (), mode) == ([], {})
        ok, errs, _ = codegen.compile_source(box, tc, mock.program(SPEC, "syntax", task).encode(), "syntax")
        assert not ok and errs[0]["stage"] == "ratfor", errs
    finally:
        box.close()
        c.sandbox.close()


def t_hidden_inputs_stay_hidden():  # the agent hears counts and kinds, never a hidden input or answer
    c = ctx("leak")
    tc, box = c.toolchains["ratfor"], c.sandbox.fresh()
    try:
        ok, _, bin = codegen.compile_source(box, tc, mock.program(SPEC, "zero", "").encode(), "z")
        errs, _ = codegen.test_program(box, tc, bin, ROLE, SPEC, ("hidden",), "z")
        hidden = ROLE.cases(SPEC, *codegen.HIDDEN)
        values = {v for h in hidden for line in h["input"].split("\n")[1:] for v in line.split()[2:] if "." in v}
        text = json.dumps(errs)
        assert errs and not [v for v in values if v in text], text
        # review 41.1: the whole message has one shape, so an answer ("expected bin 9") cannot slip in either
        shape = (r"hidden checks: \d+/\d+ wrong( \(([a-z]+ \d+(, )?)+\))?(; \d+ inputs [a-z ]+)*; e\.g\. "
                 r"(h\d+(:die \(\d+,\d+\))?(, )?)+\. Inputs not shown: fix the rule, not the answers")
        assert len(errs) == 1 and re.fullmatch(shape, errs[0]["message"]), errs
    finally:
        box.close()
        c.sandbox.close()


def t_labels_match_the_oracle():  # review 41.1: a "pass" die is one the oracle passes, a "marginal" one marginal
    for seed in range(300):
        spec = ROLE.random_spec(seed)
        for c in ROLE.cases(spec, 1000 + seed, 2, 21):
            for line, cat in zip(c["input"].split("\n")[1:], c["categories"]):
                b = ROLE.bin_of(spec, [float(x) for x in line.split()[2:]])
                want = {"pass": spec["pass_bin"], "marginal": spec.get("marginal_bin")}.get(cat, b)
                assert b == want, (seed, cat, line, b)
    # at a margin exactly: never generated (a right double program and the exact oracle would disagree)
    tight = ROLE.validate({"tests": [{"name": "A", "lo": 0, "hi": 0.15, "bin": 2}], "missing_bin": 3,
                           "marginal_pct": 2, "marginal_bin": 4})
    vals = [float(line.split()[2]) for c in ROLE.cases(tight, 3, 20, 21) for line, cat in
            zip(c["input"].split("\n")[1:], c["categories"]) if cat == "marginal"]
    assert vals and all(abs(abs(v - 0.15) - 0.003) > 1e-9 for v in vals), vals


def t_odd_output_is_a_format_error():  # review 41.1: "--3", "²", 5000 digits: not a crash of the loop
    c = {"id": "1", "input": "1\n1 2 1.0 20 1200\n", "categories": ["pass"]}
    for out in ["1 2 --3", "1 2 \u00b2", "1 2 " + "9" * 5000]:
        assert ROLE.judge(SPEC, c, out)["format"], out
    assert ROLE.judge(SPEC, c, "  1\n 2\t1 ")["wrong"] == []
    for bad in [float("nan"), float("inf")]:
        try:
            ROLE.validate({"tests": [{"name": "a", "lo": bad, "bin": 2}], "missing_bin": 3})
            raise AssertionError(bad)
        except ValueError:
            pass


def t_measurement():  # review 41.1: the evaluation's own numbers (bench/codegen_eval.py), without a model
    sys.path.insert(0, str(ROOT / "bench"))
    import codegen_eval as ev
    c = ctx("measure")
    tc, box = c.toolchains["ratfor"], c.sandbox.fresh()
    try:
        right, total, kinds = ev.audit(box, tc, ROLE, SPEC, mock.program(SPEC, "right"), "a1", 300, (8, 25))
        assert right == total == 200 and not kinds, (right, total, kinds)
        right, total, kinds = ev.audit(box, tc, ROLE, SPEC, mock.program(SPEC, "tableorder"), "a2", 300, (8, 25))
        assert right < total and kinds["priority"] > 0, kinds
        right, total, kinds = ev.audit(box, tc, ROLE, SPEC, "nope {", "a3", 300, (8, 25))
        assert right == 0 and kinds == {"not built": total}
    finally:
        box.close()
        c.sandbox.close()
    assert ev.audit_seed(4) % 3 == 0 and codegen.PUBLIC[0] % 3 == 1 and codegen.HIDDEN[0] % 3 == 2
    rows = [{"spec": 0, "rep": 0, "guards": "G0", "accepted": True, "attempts": 1, "stages": ["ok"],
             "audit_right": 90, "audit_total": 100, "first_right": False},
            {"spec": 0, "rep": 0, "guards": "G1", "accepted": True, "attempts": 2, "stages": ["examples", "ok"],
             "audit_right": 100, "audit_total": 100, "first_right": False},
            {"spec": 0, "rep": 0, "guards": "G2", "accepted": False, "attempts": 5, "stages": ["hidden"] * 5,
             "audit_right": 0, "audit_total": 100, "first_right": False},
            {"spec": 1, "rep": 0, "guards": "G0", "error": "gateway"}]
    t = ev.table(rows)
    assert "first answers wrong: 1/1" in t and "errors (agent/gateway): 1" in t, t
    assert "| G0 | 1 | 0 | 1 | 0 | 90.0% | 0 | 1.0 | - |" in t, t
    assert "| G1 | 1 | 1 | 0 | 0 | 100.0% | 0 | 2.0 | examples 1 |" in t, t
    assert "| G2 | 1 | 0 | 0 | 1 | - | 0 | 5.0 | hidden 5 |" in t, t
    seen, calls = ev.FirstAnswers(), []
    assert seen.get("k", lambda: calls.append(1) or "a") == seen.get("k", lambda: calls.append(1) or "b") == "a"
    assert calls == [1]


def t_loop_until_right():  # syntax -> cheat -> wrong order -> right: four attempts, each stopped by its guard
    c = ctx("loop")
    try:
        r = codegen.loop(c, "ratfor", "modes=syntax,hardcode,tableorder,right", "bin", attempts=5, role="binning",
                         spec=SPEC)
        assert r["stop"] is None and len(r["history"]) == 4, r["stop"]
        first = [h["errors"][0]["stage"] if h["errors"] else "ok" for h in r["history"]]
        assert first == ["ratfor", "test", "test", "ok"], first
        assert r["history"][1]["score"] == {"public": [2, 2], "hidden": [500, 500]}  # the cheat passes the examples
        assert r["history"][2]["score"]["public"] == [2, 2] and r["history"][2]["score"]["hidden"][0] > 0
        out = codegen.write_code(c, "ratfor", "modes=tableorder", "bin2", attempts=3, role="binning", spec=SPEC)
        assert out.startswith("NOT BUILT") and "examples 2/2 right" in out and "same errors" in out, out
        got = json.loads(c.sandbox.read_bytes("outputs/codegen/bin2/attempt-2/errors.json", 10000))
        assert got[0]["stage"] == "test" and got[0]["message"].startswith("hidden checks"), got
        r = codegen.loop(c, "ratfor", "modes=zero", "pub", attempts=1, role="binning", spec=SPEC, guards=("public",))
        assert r["stop"] is None, "the examples alone take a program that reads a missing value as 0"
        r = codegen.loop(c, "ratfor", "modes=hardcode", "g1", attempts=2, role="binning", spec=SPEC,
                         guards=("public",))
        assert r["stop"] is None, "with the examples only, the cheat is taken as done (what the evaluation measures)"
    finally:
        c.sandbox.close()


def t_hang_is_cut():  # a program that never ends: per-input limit, the rest not run
    c = ctx("hang")
    try:
        t = time.time()
        r = codegen.loop(c, "ratfor", "modes=hang", "h", attempts=1, role="binning", spec=SPEC)
        msgs = " ".join(e["message"] for e in r["history"][0]["errors"])
        assert r["stop"] and "timed out after 5s" in msgs and time.time() - t < 60, (msgs, time.time() - t)
    finally:
        c.sandbox.close()


def t_contract():  # what write_code takes and what the model sees
    c = Context(toolchains=codegen.discover(TOOLCHAINS), roles={"binning": ROLE},
                agents=delegate.discover(ROOT / "tests" / "fixtures" / "agents-binning"))
    for kw, msg in [({"role": "nope", "spec": SPEC}, "no role"), ({"spec": SPEC}, "spec needs a role"),
                    ({"role": "binning", "spec": {"tests": []}}, "non-empty")]:
        try:
            codegen.loop(c, "ratfor", "t", "x", **kw)
            raise AssertionError(kw)
        except ValueError as e:
            assert msg in str(e), e
    reg = {}
    codegen.register(c.toolchains, c.agents, reg, _schema, c.roles)
    p = reg["write_code"]["schema"]["function"]["parameters"]["properties"]
    assert p["role"]["enum"] == ["binning"] and "missing_bin" in p["spec"]["description"]
    assert sorted(codegen.discover_roles()) == ["binning", "wafermap"]
    bad = TMP / "roles" / "half"
    bad.mkdir(parents=True)
    (bad / "role.py").write_text("DESCRIPTION = 'x'\n")
    try:
        codegen.discover_roles(bad.parent)
        raise AssertionError("accepted")
    except ValueError as e:
        assert "missing" in str(e), e


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
