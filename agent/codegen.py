"""Code generation for a language our model does not know (Loop 40): a codegen agent writes, the language's compiler
checks, the errors go back to the agent, until it builds. Ratfor stands in for a fictional language in the tests.

Only two parts know the language, and both come from outside:
  the codegen agent   agents/{name}/agent.json with "language": an ordinary connected agent (delegate.py), Python or
                      an executable ("command"); it gets --task, --out and, after a failed attempt, --prev/--errors
  the compiler        toolchains/{language}/toolchain.json (+ its adapter script), mounted read-only at /toolchains
Everything in this file is the same for every language: a new language is a new toolchain folder and agent, no code.

The source is the codegen agent's, so it is compiled and run like the agent itself (delegate.py): in a fresh
container (read-only workspace, its own /tmp, no network), never in the run's sandbox, where a generated program
could rewrite the project's cache or plant a .pth that every later python of the run imports (Loop 40 review).

toolchain.json:
  language     slug, equal to the folder name
  description  one line for the model (what the language is for)
  ext          source file extension, e.g. ".r"
  compile      argv; {src} the source file (relative to /workspace), {bin} the program to write (under /tmp)
  errors       "json": compile prints one line {"ok": bool, "errors": [{"stage", "line", "message"}]} (under 10 KB:
               at most MAX_ERRORS errors), and exits 0
               or a regex with named groups message (and line, stage): exit code 0 means it built (matches are
               then warnings, not sent back); else the matches are the errors
  run          optional argv with {bin}: a program that built is run once with no input; a failure there is an
               error (stage "run"). With examples (below) it is run on each example's input instead
  timeout      seconds for compile and for run (default 60, at most 300)

Loop 41: a role (roles/{name}/role.py, trusted host code) says what the program must DO and checks that it does:
the guards against code that builds but is wrong. With a role, write_code takes the role's spec; the agent's task
is the role's description of it (with worked examples), and a program that built is run on cases:
  public   a few small inputs; a wrong answer goes back to the agent with the input and the expected output
  hidden   many inputs the agent never sees; it hears only how many items were wrong and of which kind, so it
           cannot fit its program to the answers (a program that prints the examples' answers fails here)
Expected outputs come from the role's oracle, never from the agent or our model. The language does not matter
to a role, nor the role to a language: their only contract is the program's stdin/stdout.
role.py: DESCRIPTION, SPEC_SCHEMA, validate(spec) -> spec, describe(spec, examples) -> task text,
  cases(spec, seed, n, size, example=False) -> [{"id", "input", "categories"}] (example: plain items only, for
  the worked examples; the tricky ones are for the hidden checks), expected(spec, input) -> output text,
  judge(spec, case, output) -> {"format": [problems], "wrong": [{"item", "category", "detail"}], "total": items}

Loop 44: a program that reads input fails the run without a role (it gets no input: a right BFS was NOT BUILT).
Without a role, write_code takes examples [{"input", "output"}] from our model: the program is run on each input
and its output compared with the expected one (trailing spaces and blank lines aside); a difference goes back to
the agent with the input. These are public checks only, and the expected outputs are our model's own, so they
can be wrong: the result says so. No examples: run once with no input, as before.
"""
import importlib.util
import json
import re
import shlex
from pathlib import Path

from . import delegate

DIR = Path(__file__).resolve().parents[1] / "toolchains"
ROLES = Path(__file__).resolve().parents[1] / "roles"
MOUNT = "/toolchains"
LANG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$")
MAX_ATTEMPTS, MAX_ERRORS, MAX_MESSAGE, MAX_SOURCE = 8, 20, 300, 1024 * 1024
GUARDS = ("public", "hidden")  # all of them; the evaluation (bench/codegen_eval.py) turns them off to measure them
PUBLIC, HIDDEN = (1, 2, 5), (2, 20, 25)  # seed, inputs, items per input
CASE_SECONDS, MAX_CASE_OUT = 5, 64 * 1024
MAX_EXAMPLES, MAX_EXAMPLE = 5, 4096  # examples without a role: how many, and characters per input or output
ROLE_API = ("DESCRIPTION", "SPEC_SCHEMA", "validate", "describe", "cases", "expected", "judge")


def discover_roles(root=ROLES):
    """name -> role module. Trusted code shipped with the server (like skills), loaded on the host."""
    out = {}
    for f in sorted(Path(root).glob("*/role.py")):
        name = f.parent.name
        if not LANG_RE.match(name):
            raise ValueError(f"{f}: role folder {name!r} must be a slug")
        spec = importlib.util.spec_from_file_location(f"vanila_role_{name.replace('-', '_')}", f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        missing = [a for a in ROLE_API if not hasattr(mod, a)]
        if missing:
            raise ValueError(f"{f}: missing {missing}")
        out[name] = mod
    return out


def discover(root=DIR):
    """language -> manifest. Invalid manifests fail loudly: the operator put them there."""
    out = {}
    for f in sorted(Path(root).glob("*/toolchain.json")):
        m = json.loads(f.read_text(encoding="utf-8"))
        lang = m.get("language", "")
        if not LANG_RE.match(lang) or lang != f.parent.name:
            raise ValueError(f"{f}: language {lang!r} must be a slug equal to its folder name")
        if not m.get("description") or not str(m.get("ext", "")).startswith("."):
            raise ValueError(f"{f}: description and ext ('.x') are required")
        for k in ("compile", "run"):
            v = m.get(k)
            if (k == "compile" or v is not None) and not (isinstance(v, list) and v and all(isinstance(a, str) for a in v)):
                raise ValueError(f"{f}: {k} must be a list of strings (argv)")
        if "{src}" not in "".join(m["compile"]):
            raise ValueError(f"{f}: compile must take {{src}}")
        if m.get("errors") != "json":
            try:
                if "message" not in re.compile(m.get("errors") or "").groupindex:
                    raise ValueError
            except (re.error, ValueError):
                raise ValueError(f"{f}: errors must be \"json\" or a regex with a (?P<message>...) group") from None
        m["timeout"] = max(1, min(int(m.get("timeout", 60)), 300))
        out[lang] = m
    return out


def writers(toolchains, agents):
    """language -> the codegen agent for it."""
    return {m["language"]: name for name, m in agents.items() if m.get("language") in toolchains}


def check(toolchains, agents):
    """Fail loudly at start, like discover(): a codegen agent nothing can use, or two for one language."""
    seen = {}
    for name, m in sorted(agents.items()):
        lang = m.get("language")
        if lang is None:
            continue
        if lang not in toolchains:
            raise ValueError(f"agent {name}: no toolchains/{lang}/toolchain.json for its language {lang!r}")
        if lang in seen:
            raise ValueError(f"agents {seen[lang]} and {name} both write {lang!r}; connect one")
        seen[lang] = name


def _argv(argv, **subst):
    return shlex.join(a.format(**subst) for a in argv)


def _errors(found):
    """Our shape, whatever the adapter sent: at most MAX_ERRORS of {stage, line, message}."""
    out = []
    for e in found[:MAX_ERRORS]:
        e = e if isinstance(e, dict) else {"message": str(e)}
        line = e.get("line")
        out.append({"stage": str(e.get("stage") or "compile")[:40],
                    "line": int(line) if str(line).isdigit() and len(str(line)) < 10 else None,
                    "message": " ".join(str(e.get("message", "")).split())[:MAX_MESSAGE]})
    return out


def _parse(tc, out):
    """exec() output ('exit_code=N\\n...') -> (ok, errors)."""
    head, _, text = out.partition("\n")
    rc = int(head.removeprefix("exit_code=")) if head.startswith("exit_code=") else 1
    if "[timeout after" in text:
        return False, [{"stage": "compile", "line": None, "message": text.strip()[:MAX_MESSAGE]}]
    if tc["errors"] == "json":
        for line in reversed(text.splitlines()):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and "ok" in r:
                errs = _errors(r.get("errors") or [])
                if not r["ok"] and not errs:
                    errs = [{"stage": "compile", "line": None, "message": "failed without an error message"}]
                return bool(r["ok"]) and rc == 0 and not errs, errs
        return False, [{"stage": "compile", "line": None,
                        "message": f"the compiler adapter printed no result (exit {rc}): {text.strip()[-200:]}"}]
    if rc == 0:
        return True, []
    errs = _errors([m.groupdict() for m in re.finditer(tc["errors"], text, re.M)])
    return False, errs or [{"stage": "compile", "line": None, "message": f"exit {rc}: {text.strip()[-200:]}"}]


def compile_source(box, tc, code, name):
    """Compile source bytes in `box` (a fresh() container) -> (ok, errors, program path)."""
    src, d = f"outputs/{name}/main{tc['ext']}", f"/tmp/codegen/{name}"  # box's outputs/: its own tmpfs
    box.write_bytes(src, code)
    cmd = _argv(tc["compile"], src=src, bin=f"{d}/main")
    return (*_parse(tc, box.exec(f"mkdir -p {d} && {cmd} 2>&1", timeout=tc["timeout"])), f"{d}/main")


def run_program(box, tc, bin):
    """Run a program that built once -> (ok, errors, output)."""
    out = box.exec(_argv(tc["run"], bin=bin) + " 2>&1", timeout=tc["timeout"])
    head, _, text = out.partition("\n")
    if head == "exit_code=0":
        return True, [], text
    lines = [x.strip() for x in text.strip().splitlines() if x.strip()]
    said = [x for x in lines if "error" in x.lower()][:3] or lines[-8:]  # the error, not the output before it
    return False, [{"stage": "run", "line": None, "message": " | ".join(said)[:MAX_MESSAGE] or head}], text


# Runs a built program on every case inside the box (python -I: our script, the program is the untrusted part),
# each case with its own time limit. The cases come on our stdin, never in a file: the program runs in the same
# container as the same user and would read the hidden inputs from disk (Loop 41 review). Results (only the
# program's own output) go to a file we read and delete.
# The program's stdout and stderr go to files it cannot grow past `cap` (RLIMIT_FSIZE; past it, SIGXFSZ), never
# into our memory: a program that prints for its whole time limit cannot get the harness killed (review 42.1).
_CASES = """import json, resource, signal, subprocess, sys, tempfile
argv, dst, t, cap = json.loads(sys.argv[1]), sys.argv[2], float(sys.argv[3]), int(sys.argv[4])
def limit():
    resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))
res, slow = {}, 0
for c in json.load(sys.stdin):
    if slow >= 2:  # it hangs: the rest would only spend the attempt's time
        res[c["id"]] = {"rc": None, "out": "", "err": "not run: earlier inputs timed out"}
        continue
    with tempfile.TemporaryFile() as o, tempfile.TemporaryFile() as e:
        try:
            rc = subprocess.run(argv, input=c["input"].encode(), stdout=o, stderr=e, timeout=t,
                                preexec_fn=limit).returncode
        except subprocess.TimeoutExpired:
            slow += 1
            res[c["id"]] = {"rc": None, "out": "", "err": f"timed out after {t:g}s"}
            continue
        o.seek(0)
        out = o.read(cap).decode("utf-8", "replace")  # examples: our model's, any text (review 44.1)
        e.seek(max(0, e.seek(0, 2) - 300))
        err = e.read().decode("utf-8", "replace")
        if rc == -signal.SIGXFSZ or o.seek(0, 2) >= cap:  # killed, or (python ignores SIGXFSZ) a failed write
            err = f"printed more than {cap} bytes"
        res[c["id"]] = {"rc": rc, "out": out, "err": err}
json.dump(res, open(dst, "w"), ensure_ascii=False)
"""


def run_cases(box, tc, bin, cases, name):
    """{case id: {"rc", "out", "err"}} of the program on each case's input (rc None: timed out)."""
    if not all(isinstance(c["id"], str) for c in cases):  # JSON keys: an int id would never find its result
        raise ValueError("the role's cases() gave a case id that is not a string")
    if len({c["id"] for c in cases}) != len(cases):  # results are keyed by id: a duplicate would take another's output
        raise ValueError("the role's cases() gave two cases the same id")
    d = f"outputs/{name}"
    t = min(CASE_SECONDS, tc["timeout"])
    argv = json.dumps([a.format(bin=bin) for a in tc["run"]])
    data = json.dumps([{"id": c["id"], "input": c["input"]} for c in cases]).encode()
    out = box.exec(f"mkdir -p {d} && python -I -c {shlex.quote(_CASES)} {shlex.quote(argv)} {d}/results.json {t} "
                   f"{MAX_CASE_OUT}", timeout=min(300, int(len(cases) * t) + 30), input=data)
    try:  # ASCII decoding and JSON escapes: at most 6 bytes per output byte, and the error tail
        got = json.loads(box.read_bytes(f"{d}/results.json", (6 * MAX_CASE_OUT + 2048) * len(cases) + 4096))
        return {c["id"]: got.get(c["id"]) or {"rc": None, "out": "", "err": "no result"} for c in cases}
    except (FileNotFoundError, ValueError):
        # never the harness's output: the program shares its uid and can write the hidden inputs to the harness's
        # stdout (/proc/$PPID/fd/1) and kill it, and this message goes to the agent (review loop 10)
        rc = out.partition("\n")[0]
        raise RuntimeError(f"running the cases failed ({rc}: the harness was killed or ran out of memory)") from None
    finally:
        box.exec(f"rm -rf {d}", timeout=30)


def test_program(box, tc, bin, role, spec, guards, name):
    """The role's checks -> (errors for the agent, {"public": [ok, n], "hidden": [wrong, total]})."""
    public = role.cases(spec, *PUBLIC, example=True) if "public" in guards else []
    hidden = role.cases(spec, *HIDDEN) if "hidden" in guards else []
    if not all(isinstance(c["id"], str) for c in public + hidden):
        raise ValueError("the role's cases() gave a case id that is not a string")
    for c in hidden:
        c["id"] = "h" + c["id"]
    if {c["id"] for c in public} & {c["id"] for c in hidden}:  # one dict below: a hidden result would be judged, and
        raise ValueError("an example id equals a hidden one ('h' + id)")  # reported, as an example's (review loop 13)
    if not public and not hidden:
        return [], {}
    # the examples first and apart: no hidden input is in the box while a program's error text can still reach the
    # agent through them
    res = {**(run_cases(box, tc, bin, public, name + "-p") if public else {}),
           **(run_cases(box, tc, bin, hidden, name + "-h") if hidden else {})}
    errs, score = [], {}

    def judged(c, out):  # a judge that fails on odd output is that output's fault, not the loop's
        try:
            return role.judge(spec, c, out)
        except Exception as e:
            return {"format": [f"output the checker could not read ({type(e).__name__})"], "wrong": [],
                    "total": len(c.get("categories") or []) or 1}

    def failed(r):
        return None if r["rc"] == 0 else (r["err"].strip() or f"exit {r['rc']}") if r["rc"] is not None else r["err"]

    good = 0
    for k, c in enumerate(public, 1):
        r = res[c["id"]]
        why = failed(r)
        j = None if why else judged(c, r["out"])
        if why:
            errs.append(f"example {k}: the program failed: {why}")
        else:
            errs += [f"example {k}: {f}" for f in j["format"]]
            errs += [f"example {k}, {w['item']}: {w['detail']}" for w in j["wrong"][:3]]
        good += not why and not j["format"] and not j["wrong"]
    if public:
        score["public"] = [good, len(public)]
    if hidden:
        wrong, total, kinds, crashed, badfmt, where = 0, 0, {}, 0, 0, []
        for c in hidden:
            r = res[c["id"]]
            j = None if failed(r) else judged(c, r["out"])
            n = len(c.get("categories") or []) or 1
            total += n
            if j is None or j["format"]:
                crashed += j is None
                badfmt += j is not None
                wrong += n
                where.append(c["id"])
                continue
            wrong += len(j["wrong"])
            for w in j["wrong"]:
                kinds[w["category"]] = kinds.get(w["category"], 0) + 1
            where += [f"{c['id']}:{w['item']}" for w in j["wrong"][:1]]
        score["hidden"] = [wrong, total]
        if wrong:
            kind = ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]))
            errs.append(f"hidden checks: {wrong}/{total} wrong" + (f" ({kind})" if kind else "")
                        + (f"; {crashed} inputs failed to run" if crashed else "")
                        + (f"; {badfmt} inputs with output in the wrong format" if badfmt else "")
                        + f"; e.g. {', '.join(where[:3])}. Inputs not shown: fix the rule, not the answers")
    return [{"stage": "test", "line": None, "message": m[:MAX_MESSAGE]} for m in errs], score


def _key(errors):
    return sorted((e["stage"], e["line"], e["message"]) for e in errors)


def write_code(ctx, language, task, id, attempts=4, role=None, spec=None, examples=None):
    return _summary(**loop(ctx, language, task, id, attempts, role, spec, examples=examples))


def _examples(examples):
    """Our model's examples -> [{"input", "output"}], checked."""
    if not isinstance(examples, list) or not 1 <= len(examples) <= MAX_EXAMPLES:
        raise ValueError(f"examples: a list of 1-{MAX_EXAMPLES} {{input, output}}")
    out = []
    for e in examples:
        if not isinstance(e, dict) or set(e) != {"input", "output"} or not all(
                isinstance(e[k], str) and len(e[k]) <= MAX_EXAMPLE for k in ("input", "output")):
            raise ValueError(f"examples: each one is {{input, output}}, strings of at most {MAX_EXAMPLE} characters")
        out.append({"input": e["input"], "output": e["output"]})
    return out


def _norm(text):
    """Output as compared with an example: spaces at line ends and blank lines at the end do not count."""
    return "\n".join(x.rstrip() for x in text.replace("\r\n", "\n").rstrip().split("\n"))


def _shown(text, n=60):
    return repr(text if len(text) <= n else text[:n] + "...")


def _differ(want, got):
    """Where two normalized outputs first differ, shown from there: two different wrong outputs never read the same
    (the loop stops on the same errors twice, review 44.1)."""
    w, g = want.split("\n"), got.split("\n")
    i = next((i for i, (a, b) in enumerate(zip(w, g)) if a != b), min(len(w), len(g)))
    a, b = (w[i] if i < len(w) else None), (g[i] if i < len(g) else None)
    at = next((j for j, (x, y) in enumerate(zip(a or "", b or "")) if x != y), min(len(a or ""), len(b or "")))
    cut = max(0, at - 20)
    part = lambda s: "no line" if s is None else ("..." if cut else "") + _shown(s[cut:])  # noqa: E731
    return f"line {i + 1}: expected {part(a)}, got {part(b)} ({len(w)} vs {len(g)} lines)"


def try_examples(box, tc, bin, examples, name):
    """The program on each example -> (errors for the agent, {"public": [right, n]}, its outputs)."""
    res = run_cases(box, tc, bin, [{"id": f"e{k}", "input": e["input"]} for k, e in enumerate(examples, 1)], name)
    errs, good, outs = [], 0, []
    for k, e in enumerate(examples, 1):
        r = res[f"e{k}"]
        outs.append(r["out"])
        if r["rc"] != 0:
            why = (r["err"].strip() or f"exit {r['rc']}") if r["rc"] is not None else r["err"]
            errs.append(f"example {k} (input {_shown(e['input'])}): the program failed: {why}")
        elif _norm(r["out"]) != _norm(e["output"]):
            errs.append(f"example {k}: input {_shown(e['input'])}, {_differ(_norm(e['output']), _norm(r['out']))}")
        else:
            good += 1
    return [{"stage": "test", "line": None, "message": m[:MAX_MESSAGE]} for m in errs], \
        {"public": [good, len(examples)]}, outs


def loop(ctx, language, task, id, attempts=4, role=None, spec=None, guards=GUARDS, examples=None):
    """The whole loop -> a dict (write_code formats it; the evaluation reads it)."""
    if language not in ctx.toolchains or language not in writers(ctx.toolchains, ctx.agents):
        raise ValueError(f"no codegen agent and compiler for {language!r}; have: "
                         f"{sorted(writers(ctx.toolchains, ctx.agents))}")
    if not ID_RE.match(id):
        raise ValueError("id: letters, digits, _ and -, at most 41")
    tc, agent = ctx.toolchains[language], writers(ctx.toolchains, ctx.agents)[language]
    base, attempts = f"outputs/codegen/{id}", max(1, min(int(attempts), MAX_ATTEMPTS))
    r = None
    if role is not None:
        if role not in ctx.roles:
            raise ValueError(f"no role {role!r}; have: {sorted(ctx.roles)}")
        if not tc.get("run"):
            raise ValueError(f"{language} has no run command in its toolchain: its programs cannot be checked")
        r = ctx.roles[role]
        spec = r.validate(spec)
        worked = [(c["input"], r.expected(spec, c["input"])) for c in r.cases(spec, *PUBLIC, example=True)]
        task = r.describe(spec, worked) + (f"\n\nAlso: {task}" if task else "")
    elif spec is not None:
        raise ValueError("spec needs a role")
    if examples is not None:
        if role is not None:
            raise ValueError("examples: a role gives its own; give none with a role")
        if not tc.get("run"):
            raise ValueError(f"{language} has no run command in its toolchain: its programs cannot be checked")
        examples = _examples(examples)
        task += "\n\nExamples: for each input on stdin the program must print exactly the output shown.\n" + \
            "\n".join(f"--- example {k} input ---\n{e['input']}\n--- example {k} output ---\n{e['output']}"
                      for k, e in enumerate(examples, 1))
    # an earlier call with this id: its files would be taken for this agent's (an attempt that writes nothing)
    ctx.sandbox.exec(f"rm -rf -- {shlex.quote(base)}", timeout=30)
    return _attempts(ctx, language, tc, agent, task, id, base, attempts, r, spec, guards, examples)


def _attempts(ctx, language, tc, agent, task, id, base, attempts, role, spec, guards, examples=None):
    history, prev, last, stop = [], None, None, None
    for n in range(1, attempts + 1):
        if ctx.cancel.is_set():
            stop = "cancelled"
            break
        d = f"{base}/attempt-{n}"
        src = f"{d}/main{tc['ext']}"
        inputs = {"out": src, **({"prev": prev, "errors": f"{base}/attempt-{n - 1}/errors.json"} if prev else {}),
                  **({"spec": spec} if role is not None else {})}  # the spec as data, for agents that read it
        ctx.emit({"type": "agent_progress", "agent": agent, "message": f"attempt {n}/{attempts}"})
        try:
            said = delegate.run_agent(ctx, agent, task, inputs)
        except RuntimeError as e:
            stop = f"the codegen agent failed on attempt {n}: {str(e)[:500]}"
            break
        try:
            code = ctx.sandbox.read_bytes(src, MAX_SOURCE)
        except (FileNotFoundError, ValueError) as e:
            stop = f"attempt {n}: the codegen agent wrote no usable {src} ({e}); it said: {said[-300:]}"
            break
        # a box per attempt: an earlier attempt's program could have kept the hidden inputs it was given (in /tmp,
        # or a process of its own) and hand them to this one's examples, whose errors reach the agent (review 42.1)
        box = ctx.sandbox.fresh()
        try:
            ok, errs, bin = compile_source(box, tc, code, f"{id}-{n}")
            output, score = None, {}
            if ok and role is not None:
                try:
                    errs, score = test_program(box, tc, bin, role, spec, guards, f"{id}-{n}")
                except RuntimeError as e:
                    errs = [{"stage": "test", "line": None, "message": str(e)[:MAX_MESSAGE]}]
                ok = not errs
            elif ok and examples:
                try:
                    errs, score, outs = try_examples(box, tc, bin, examples, f"{id}-{n}")
                    output = outs[0]
                except RuntimeError as e:
                    errs = [{"stage": "test", "line": None, "message": str(e)[:MAX_MESSAGE]}]
                ok = not errs
            elif ok and tc.get("run"):
                ok, errs, output = run_program(box, tc, bin)
        finally:
            box.close()
        errs = errs[:MAX_ERRORS]
        ctx.sandbox.write_bytes(f"{d}/errors.json", json.dumps(errs, ensure_ascii=False, indent=1).encode())
        history.append({"attempt": n, "source": src, "lines": code.count(b"\n"), "ok": ok, "errors": errs,
                        "score": score, "code": code.decode(errors="replace")})
        if ok:
            ctx.sandbox.write_bytes(f"{base}/main{tc['ext']}", code)
            # the UI shows a download button for it (outputs/ is saved, and downloadable, when the run ends)
            ctx.emit({"type": "download", "path": f"{base.removeprefix('outputs/')}/main{tc['ext']}"})
            return dict(language=language, base=base, tc=tc, history=history, stop=None, output=output,
                        examples=bool(examples))
        if last is not None and _key(errs) == last:
            stop = f"attempt {n} gave the same errors as attempt {n - 1}; stopped (the agent is not fixing them)"
            break
        prev, last = src, _key(errs)
    return dict(language=language, base=base, tc=tc, history=history,
                stop=stop or f"no {'passing ' if role or examples else ''}build after {attempts} attempts",
                output=None, examples=bool(examples))


def _score(s):
    return "".join([f", examples {s['public'][0]}/{s['public'][1]} right" if "public" in s else "",
                    f", hidden {s['hidden'][1] - s['hidden'][0]}/{s['hidden'][1]} right" if "hidden" in s else ""])


# server/app.py get_session reads "BUILT" and the "final source: outputs/..." line back for the download button
def _summary(language, base, tc, history, stop, output, examples=False):
    lines = [f"{'BUILT' if stop is None else 'NOT BUILT'}: {language}, {len(history)} attempt(s); every attempt's "
             f"source and errors.json are under {base}/"]
    for h in history:
        lines.append(f"- attempt {h['attempt']}: {h['source']} ({h['lines']} lines): "
                     + ("ok" if h["ok"] else f"{len(h['errors'])} error(s)") + _score(h.get("score") or {}))
        for e in h["errors"][:5]:
            lines.append(f"    [{e['stage']}{'' if e['line'] is None else ' line %d' % e['line']}] {e['message']}")
    if stop is None:
        lines.append(f"final source: {base}/main{tc['ext']}")
        if output is not None:
            lines.append(f"program output{' on example 1' if examples else ''}:\n" + output.strip()[:2000])
    else:
        lines.append("stopped: " + stop)
        if examples and any(e["stage"] == "test" and ": expected " in e["message"]  # a mismatch, not a failed run
                            for h in history for e in h["errors"]):
            lines.append("the expected outputs are your examples: if the program's output looks right, check them")
    return "\n".join(lines)


def compile_code(ctx, language, file):
    if language not in ctx.toolchains:
        raise ValueError(f"no compiler for {language!r}; have: {sorted(ctx.toolchains)}")
    code = ctx.sandbox.read_bytes(file, MAX_SOURCE)
    box = ctx.sandbox.fresh()
    try:
        ok, errs, _ = compile_source(box, ctx.toolchains[language], code, "check")
    finally:
        box.close()
    return json.dumps({"ok": ok, "errors": errs}, ensure_ascii=False)


def register(toolchains, agents, registry, make_schema, roles=None):
    """write_code (languages with a codegen agent) and compile_code (every toolchain). Not agent__* tools for the
    codegen agents (delegate.register skips them): the model must not write in a language it does not know."""
    check(toolchains, agents)
    if not toolchains:
        return
    S = {"type": "string"}
    langs = "; ".join(f"{k}: {m['description']}" for k, m in toolchains.items())
    have = sorted(writers(toolchains, agents))
    if have:
        registry["write_code"] = {
            "fn": write_code, "approval": False, "ask": False,
            "schema": make_schema("write_code", (
                f"Write a program in a language you do not know ({', '.join(have)}). Never write that code yourself "
                "(not with write_file, edit_file or bash): this tool asks the language's codegen agent, compiles, "
                "sends the errors back to it and tries again until it builds. Report the result: built or not, "
                "attempts, the final file. Without a role or examples the program is run once with NO input, so "
                f"a program that reads input fails: give examples for it. Languages: {langs}"),
                {"language": {"type": "string", "enum": have}, "task": {**S, "description": (
                    "what the program must do, complete and precise (inputs, outputs, edge cases); "
                    "the agent sees only this")},
                 "id": {**S, "description": "short name for this program, e.g. sum-1-to-n"},
                 "attempts": {"type": "integer", "description": f"1-{MAX_ATTEMPTS}, default 4"},
                 "examples": {"type": "array", "maxItems": MAX_EXAMPLES, "description": (
                     f"without a role: 1-{MAX_EXAMPLES} small inputs (stdin text) with the exact expected output, "
                     "worked out carefully; the program is run on each and must print it. Give them whenever the "
                     "program reads input, and include edge cases"),
                     "minItems": 1, "items": {"type": "object", "properties": {"input": S, "output": S},
                                              "required": ["input", "output"], "additionalProperties": False}},
                 **({"role": {"type": "string", "enum": sorted(roles), "description": (
                     "what kind of program this is, when it is one of these; then give its spec, and the program "
                     "is also run and checked against the role's own answers, not only compiled. "
                     + "; ".join(f"{k}: {m.DESCRIPTION}" for k, m in sorted(roles.items())))},
                     "spec": {"type": "object", "description": "the role's spec (with role): " + "; ".join(
                         f"{k}: {json.dumps(m.SPEC_SCHEMA)}" for k, m in sorted(roles.items()))}} if roles else {})},
                ["language", "task", "id"])}
    registry["compile_code"] = {
        "fn": compile_code, "approval": False, "ask": False,
        "schema": make_schema("compile_code", (
            "Compile a source file of the workspace with its language's compiler and get the errors as a list "
            f"(stage, line, message). For code the user gave; write_code already compiles what it writes. "
            f"Languages: {langs}"),
            {"language": {"type": "string", "enum": sorted(toolchains)}, "file": {**S, "description": "workspace path"}},
            ["language", "file"])}
