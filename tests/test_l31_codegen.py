"""Loop 40: code generation in a language the model does not know (agent/codegen.py). Ratfor stands in for a
fictional language; Toy shows that a second language needs only its toolchain folder and an agent.
Docker (sandbox image with ratfor + gfortran) and gcc on the host (the C agent is built here); no LLM.
python tests/test_l31_codegen.py [test_name ...]"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent import codegen, delegate  # noqa: E402
from agent.loop import Context  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402
from agent.tools import REGISTRY, _schema, schemas  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l31-"))
TOOLCHAINS = ROOT / "tests" / "fixtures" / "toolchains"


def fixtures():
    d = TMP / "agents"
    if not d.exists():
        shutil.copytree(ROOT / "agents", d)
        shutil.copytree(ROOT / "tests" / "fixtures" / "agents", d, dirs_exist_ok=True)
        subprocess.run(["make", "-s", "-C", str(d / "ratfor-codegen")], check=True, capture_output=True)
        (d / "noisy").mkdir()  # an executable agent that breaks the rules
        (d / "noisy" / "agent.json").write_text(json.dumps({"name": "noisy", "description": "misbehaves",
                                                            "command": ["run.sh"], "timeout": 10}))
        (d / "noisy" / "run.sh").write_text('#!/bin/sh\ncase "$2" in big) head -c 300000 /dev/zero | tr "\\0" x;; '
                                            'fail) echo half done; echo oops >&2; exit 3;; *) echo "args: $*";; esac\n')
        (d / "noisy" / "run.sh").chmod(0o755)
    return d


def ctx(name):
    agents = fixtures()
    ws = TMP / name
    ws.mkdir()
    sb = Sandbox(ws, mounts={agents: delegate.MOUNT, TOOLCHAINS: codegen.MOUNT})
    return Context(sandbox=sb, agents=delegate.discover(agents), toolchains=codegen.discover(TOOLCHAINS))


def t_discover():
    assert sorted(codegen.discover(TOOLCHAINS)) == ["ratfor", "toy"]
    good = {"language": "x", "description": "d", "ext": ".x", "compile": ["c", "{src}"], "errors": "json"}
    for bad, msg in [({**good, "language": "y"}, "folder name"), ({**good, "compile": ["c", "a.x"]}, "{src}"),
                     ({**good, "compile": "c {src}"}, "argv"), ({**good, "errors": "(no group"}, "regex"),
                     ({**good, "errors": "(?P<line>\\d+)"}, "message"), ({**good, "ext": "x"}, "ext")]:
        d = TMP / f"tc-{len(msg)}-{msg[:3]}" / "x"
        d.mkdir(parents=True)
        (d / "toolchain.json").write_text(json.dumps(bad))
        try:
            codegen.discover(d.parent)
            raise AssertionError(f"accepted {bad}")
        except ValueError as e:
            assert msg in str(e), e
    base = {"name": "a", "description": "d"}
    for bad in [base, {**base, "entry": "m:f", "command": ["x"]}, {**base, "command": ["../x"]},
                {**base, "command": ["/bin/sh"]}, {**base, "command": []}]:
        d = TMP / f"ag-{abs(hash(json.dumps(bad)))}" / "a"
        d.mkdir(parents=True)
        (d / "agent.json").write_text(json.dumps(bad))
        try:
            delegate.discover(d.parent)
            raise AssertionError(f"accepted {bad}")
        except ValueError as e:
            assert "entry" in str(e) or "command" in str(e), e
    assert delegate.discover(fixtures())["ratfor-codegen"]["command"] == ["bin/codegen"]


def t_ratfor_errors():  # the adapter: two stages, source lines, ratfor's exit 0 on errors
    c = ctx("adapter")
    box, tc = c.sandbox.fresh(), c.toolchains["ratfor"]
    try:
        cases = {
            "ratfor.r": ("integer s\ns = 0\ns = s + (1\nwrite(6,*) s\nend\n", [("ratfor", None)]),
            # review: a line far from the end (the line is clamped to the file): ratfor notices the open ( a line later
            "long.r": ("integer s\n" + "s = 1\n" * 9 + "s = s + (1\n" + "s = 2\n" * 19 + "end\n", [("ratfor", 0)]),
            "fortran.r": ("integer i\ni = 1\nif (i > ) {\n  i = 2\n}\nend\n", [("fortran", 3)]),
            "loop.r": ("integer i, s\ns = 0\ndo i = 1, 3 {\n  if (i > 1) {\n    s = s +* i\n  }\n}\nend\n",
                       [("fortran", 5)]),
            "link.r": ("x = nosuch(1.0)\nend\n", [("link", None)]),
            "ok.r": ("integer s\ns = 2 + 3\nwrite(6,*) s\nend\n", []),
        }
        for f, (code, want) in cases.items():
            ok, errs, bin = codegen.compile_source(box, tc, code.encode(), f.replace(".", "-"))
            assert ok == (not want), (f, ok, errs)
            got = [(e["stage"], e["line"]) for e in errs][:len(want)]
            if want and want[0][1] == 0:
                assert got[0][0] == "ratfor" and got[0][1] in (11, 12), (f, errs)
            elif want and want[0][1] is None and want[0][0] == "ratfor":  # ratfor reports where it noticed: a line
                assert got[0][0] == "ratfor" and got[0][1] in (3, 4, 5), (f, errs)
            else:
                assert got == want, (f, errs)
        assert box.exec(bin).split() == ["exit_code=0", "5"]
        c.sandbox.write_bytes("src/fortran.r", cases["fortran.r"][0].encode())
        out = json.loads(codegen.compile_code(c, "ratfor", "src/fortran.r"))
        assert out["ok"] is False and out["errors"][0]["line"] == 3, out
        many = "integer i\n" + "i = i +* 1\n" * 400 + "end\n"  # review: one JSON line, not cut by exec's 10 KB
        ok, errs, _ = codegen.compile_source(box, tc, many.encode(), "many")
        assert not ok and len(errs) == 20 and errs[0]["line"] == 2, errs[:2]
    finally:
        box.close()
        c.sandbox.close()


def t_three_attempts_to_a_build():  # ratfor error -> Fortran error -> builds and runs
    c = ctx("sum")
    try:
        out = codegen.write_code(c, "ratfor", "sum of 1..10, print it", "sum")
        assert out.startswith("BUILT: ratfor, 3 attempt(s)") and "55" in out.split("program output:")[1], out
        assert "[ratfor line" in out and "[fortran line 4]" in out, out
        for n in (1, 2, 3):
            assert c.sandbox.read_bytes(f"outputs/codegen/sum/attempt-{n}/main.r", 10000)
            errs = json.loads(c.sandbox.read_bytes(f"outputs/codegen/sum/attempt-{n}/errors.json", 10000))
            assert [e["stage"] for e in errs][:1] == [["ratfor"], ["fortran"], []][n - 1][:1], (n, errs)
        assert c.sandbox.read_bytes("outputs/codegen/sum/main.r", 10000) == \
            c.sandbox.read_bytes("outputs/codegen/sum/attempt-3/main.r", 10000)
        assert c.sandbox.exec("ls /workspace/outputs/codegen/sum").split()[1:] == \
            ["attempt-1", "attempt-2", "attempt-3", "main.r"]  # no program in outputs/
        out = codegen.write_code(c, "ratfor", "nofile", "sum")  # review: the same id again, an agent that writes nothing
        assert out.startswith("NOT BUILT") and "wrote no usable" in out, out
        assert "No such file" in c.sandbox.exec("ls /workspace/outputs/codegen/sum"), "old attempts are not this call's"
    finally:
        c.sandbox.close()


def t_generated_code_runs_apart():  # review Must: the agent's program is compiled and run in a throwaway container
    c = ctx("plant")
    try:
        out = codegen.write_code(c, "ratfor", "plant", "p")
        assert out.startswith("BUILT") and "planted" in out, out
        left = c.sandbox.exec("ls /tmp/planted /workspace/planted /tmp/codegen 2>&1; true")
        assert left.count("No such file") == 3, left
        assert "Permission denied" in c.sandbox.exec("cp /bin/true /tmp/t && /tmp/t")  # the run's /tmp stays noexec
    finally:
        c.sandbox.close()


def t_run_error_goes_back():  # builds, fails when run (array bounds), then right
    c = ctx("bounds")
    try:
        out = codegen.write_code(c, "ratfor", "bounds: sum an array", "b")
        assert out.startswith("BUILT: ratfor, 2 attempt(s)") and "[run]" in out and "upper bound" in out, out
    finally:
        c.sandbox.close()


def t_stops():  # the same errors twice, attempts used up, the agent crashes or writes nothing
    c = ctx("stops")
    try:
        out = codegen.write_code(c, "ratfor", "stuck", "s1")
        assert out.startswith("NOT BUILT: ratfor, 2 attempt(s)") and "same errors as attempt 1" in out, out
        out = codegen.write_code(c, "ratfor", "sum", "s2", attempts=2)
        assert out.startswith("NOT BUILT: ratfor, 2 attempt(s)") and "no build after 2 attempts" in out, out
        out = codegen.write_code(c, "ratfor", "crash", "s3")
        assert "NOT BUILT" in out and "SIGSEGV" in out and "attempt 1" in out, out
        out = codegen.write_code(c, "ratfor", "nofile", "s4")
        assert "NOT BUILT" in out and "wrote no usable outputs/codegen/s4/attempt-1/main.r" in out, out
        c.cancel.set()
        assert "stopped: cancelled" in codegen.write_code(c, "ratfor", "sum", "s5")
    finally:
        c.sandbox.close()


def t_second_language():  # Toy: a regex instead of an adapter, a Python agent instead of C, the same loop
    c = ctx("toy")
    try:
        out = codegen.write_code(c, "toy", "say hello world", "hi")
        assert out.startswith("BUILT: toy, 2 attempt(s)") and "[compile line 2] expected 'print <text>'" in out, out
        assert c.sandbox.read_bytes("outputs/codegen/hi/main.toy", 1000) == b"print hello\nprint world\n"
    finally:
        c.sandbox.close()


def t_parse():  # review: warnings with exit 0 are not errors, an adapter that crashes after "ok" did not build
    toy = codegen.discover(TOOLCHAINS)["toy"]
    assert codegen._parse(toy, "exit_code=0\nmain.toy:3: warning: unused") == (True, [])
    assert codegen._parse(toy, "exit_code=1\nmain.toy:3: bad\nmain.toy:99999999999: x")[1] == [
        {"stage": "compile", "line": 3, "message": "bad"}, {"stage": "compile", "line": None, "message": "x"}]
    assert codegen._parse({"errors": "json"}, 'exit_code=1\n{"ok": true, "errors": []}\nTraceback')[0] is False
    bad = {"x": {"name": "x", "language": "zeta"}}
    for agents, msg in [(bad, "no toolchains/zeta"), ({"a": {"language": "toy"}, "b": {"language": "toy"}}, "both")]:
        try:
            codegen.register(codegen.discover(TOOLCHAINS), agents, {}, _schema)
            raise AssertionError(msg)
        except ValueError as e:
            assert msg in str(e), e


def t_tools():  # what the model sees
    agents, tcs = delegate.discover(fixtures()), codegen.discover(TOOLCHAINS)
    reg = {}
    codegen.register(tcs, {}, reg, _schema)
    assert list(reg) == ["compile_code"]  # a compiler without a writer: no write_code
    codegen.register(tcs, agents, reg, _schema)
    fn = reg["write_code"]["schema"]["function"]
    assert fn["parameters"]["properties"]["language"]["enum"] == ["ratfor", "toy"]
    assert "Never write that code yourself" in fn["description"]
    reg = {}
    delegate.register(agents, reg, _schema)
    assert "agent__noisy" in reg and not [n for n in reg if "codegen" in n]  # codegen agents only via write_code
    assert codegen.discover() == {}  # nothing is deployed: the stand-ins live in tests/fixtures
    assert "write_code" not in REGISTRY and "compile_code" not in [s["function"]["name"] for s in schemas()]
    c = Context(toolchains=tcs, agents=agents)
    for args, msg in [({"language": "cobol", "task": "t", "id": "x"}, "no codegen agent"),
                      ({"language": "toy", "task": "t", "id": "../x"}, "id:")]:
        try:
            codegen.write_code(c, **args)
            raise AssertionError(args)
        except ValueError as e:
            assert msg in str(e), e


def t_executable_agent():  # argv in, stdout out, exit code and size limits
    c = ctx("exec")
    try:
        out = delegate.run_agent(c, "noisy", "hello", {"n": 3, "flags": ["a"], "skip": None})
        assert 'args: --task hello --n 3 --flags ["a"]' in out, out
        for task, msg in [("big", "printed more than 204,800 bytes"), ("fail", "exit code 3; output: half done")]:
            try:
                delegate.run_agent(c, "noisy", task, {})
                raise AssertionError(task)
            except RuntimeError as e:
                assert msg in str(e) and (task != "fail" or "oops" in str(e)), e
    finally:
        c.sandbox.close()


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
