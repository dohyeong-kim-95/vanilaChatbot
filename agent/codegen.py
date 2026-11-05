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
  run          optional argv with {bin}: a program that built is run once; a failure there is an error (stage "run")
  timeout      seconds for compile and for run (default 60, at most 300)
"""
import json
import re
import shlex
from pathlib import Path

from . import delegate

DIR = Path(__file__).resolve().parents[1] / "toolchains"
MOUNT = "/toolchains"
LANG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$")
MAX_ATTEMPTS, MAX_ERRORS, MAX_MESSAGE, MAX_SOURCE = 8, 20, 300, 1024 * 1024


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


def _key(errors):
    return sorted((e["stage"], e["line"], e["message"]) for e in errors)


def write_code(ctx, language, task, id, attempts=4):
    if language not in ctx.toolchains or language not in writers(ctx.toolchains, ctx.agents):
        raise ValueError(f"no codegen agent and compiler for {language!r}; have: "
                         f"{sorted(writers(ctx.toolchains, ctx.agents))}")
    if not ID_RE.match(id):
        raise ValueError("id: letters, digits, _ and -, at most 41")
    tc, agent = ctx.toolchains[language], writers(ctx.toolchains, ctx.agents)[language]
    base, attempts = f"outputs/codegen/{id}", max(1, min(int(attempts), MAX_ATTEMPTS))
    # an earlier call with this id: its files would be taken for this agent's (an attempt that writes nothing)
    ctx.sandbox.exec(f"rm -rf -- {shlex.quote(base)}", timeout=30)
    box = ctx.sandbox.fresh()
    try:
        return _attempts(ctx, box, language, tc, agent, task, id, base, attempts)
    finally:
        box.close()


def _attempts(ctx, box, language, tc, agent, task, id, base, attempts):
    history, prev, last, stop = [], None, None, None
    for n in range(1, attempts + 1):
        if ctx.cancel.is_set():
            stop = "cancelled"
            break
        d = f"{base}/attempt-{n}"
        src = f"{d}/main{tc['ext']}"
        inputs = {"out": src, **({"prev": prev, "errors": f"{base}/attempt-{n - 1}/errors.json"} if prev else {})}
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
        ok, errs, bin = compile_source(box, tc, code, f"{id}-{n}")
        output = None
        if ok and tc.get("run"):
            ok, errs, output = run_program(box, tc, bin)
        ctx.sandbox.write_bytes(f"{d}/errors.json", json.dumps(errs, ensure_ascii=False, indent=1).encode())
        history.append({"attempt": n, "source": src, "lines": code.count(b"\n"), "ok": ok, "errors": errs})
        if ok:
            ctx.sandbox.write_bytes(f"{base}/main{tc['ext']}", code)
            return _summary(language, base, tc, history, None, output)
        if last is not None and _key(errs) == last:
            stop = f"attempt {n} gave the same errors as attempt {n - 1}; stopped (the agent is not fixing them)"
            break
        prev, last = src, _key(errs)
    return _summary(language, base, tc, history, stop or f"no build after {attempts} attempts", None)


def _summary(language, base, tc, history, stop, output):
    lines = [f"{'BUILT' if stop is None else 'NOT BUILT'}: {language}, {len(history)} attempt(s); every attempt's "
             f"source and errors.json are under {base}/"]
    for h in history:
        lines.append(f"- attempt {h['attempt']}: {h['source']} ({h['lines']} lines): "
                     + ("ok" if h["ok"] else f"{len(h['errors'])} error(s)"))
        for e in h["errors"][:5]:
            lines.append(f"    [{e['stage']}{'' if e['line'] is None else ' line %d' % e['line']}] {e['message']}")
    if stop is None:
        lines.append(f"final source: {base}/main{tc['ext']}")
        if output is not None:
            lines.append("program output:\n" + output.strip()[:2000])
    else:
        lines.append("stopped: " + stop)
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


def register(toolchains, agents, registry, make_schema):
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
                f"attempts, the final file. Languages: {langs}"),
                {"language": {"type": "string", "enum": have}, "task": {**S, "description": (
                    "what the program must do, complete and precise (inputs, outputs, edge cases); "
                    "the agent sees only this")},
                 "id": {**S, "description": "short name for this program, e.g. sum-1-to-n"},
                 "attempts": {"type": "integer", "description": f"1-{MAX_ATTEMPTS}, default 4"}},
                ["language", "task", "id"])}
    registry["compile_code"] = {
        "fn": compile_code, "approval": False, "ask": False,
        "schema": make_schema("compile_code", (
            "Compile a source file of the workspace with its language's compiler and get the errors as a list "
            f"(stage, line, message). For code the user gave; write_code already compiles what it writes. "
            f"Languages: {langs}"),
            {"language": {"type": "string", "enum": sorted(toolchains)}, "file": {**S, "description": "workspace path"}},
            ["language", "file"])}
