# toolchains/: compilers for code generation (Loop 40)

One folder per language our model does not know. With a codegen agent for the same language (`agents/<name>/agent.json`
with `"language"`), the model gets the `write_code` tool: the agent writes, the compiler checks, the errors go back to
the agent, until it builds (agent/codegen.py). Mounted read-only at `/toolchains` in the sandbox.

Changing the language changes only these two parts; the loop, the tools, the UI and the tests' shape stay.

| part | what changes for a new language | example (tests/fixtures/) |
|---|---|---|
| `toolchains/<language>/toolchain.json` | name, extension, compile/run commands, error format | `toolchains/ratfor`, `toolchains/toy` |
| the adapter (optional) | only when the compiler's errors need work (two stages, line mapping, exit 0 on errors) | `toolchains/ratfor/compile.py` |
| the compiler itself | a binary in this folder, or a package in sandbox/Dockerfile | ratfor + gfortran (Dockerfile) |
| `agents/<name>/` | the codegen agent, Python (`entry`) or an executable (`command`) | `agents/ratfor-codegen` (C), `agents/toy-codegen` (Python) |

## toolchain.json

```json
{
  "language": "ratfor",
  "description": "one line for the model",
  "ext": ".r",
  "compile": ["python", "/toolchains/ratfor/compile.py", "{src}", "{bin}"],
  "errors": "json",
  "run": ["env", "GFORTRAN_ERROR_BACKTRACE=0", "{bin}"],
  "timeout": 60
}
```

- `compile`: argv, `{src}` the source (relative to `/workspace`), `{bin}` the program to write (under /tmp).
- `errors`: `"json"` when the command prints one line `{"ok": bool, "errors": [{"stage", "line", "message"}]}`
  and exits 0 (an adapter; keep the line under 10 KB: at most 20 errors), or a regex with named groups `message`
  (and `line`, `stage`) over the compiler's output. With a regex, exit code 0 means it built (matching lines are
  then warnings and are not sent back); otherwise the matches are the errors. A compiler that already prints
  `file:line: message` needs no adapter (`toy`).
- `run` (optional): a program that built is run once with no input; a failure is an error of stage `run` and goes back to
  the agent. With a role, or with `examples` from the model (Loop 44), it is run on those inputs instead.
- `line` is a line of the source the agent wrote. Unknown: `null`, never a guess.

Where it runs: the generated source is the codegen agent's, so it is compiled and run in a throwaway container like
the agent (never in the run's sandbox): no network, read-only root file system and workspace, a non-root user,
`HOME=/tmp`, a 256 MB `/tmp` (exec), 1 CPU, 1 GB, cwd `/workspace`. This folder is at `/toolchains/<language>/`
(read-only): call a compiler binary shipped here as `/toolchains/<language>/bin/...`; a compiler that needs a cache
must keep it under /tmp. Changes here and in agents/ are read when the server starts.

## The codegen agent

It writes the source to `--out`; after a failed attempt it also gets `--prev <last source>` and
`--errors <errors.json>` (a JSON list of `{stage, line, message}`), paths relative to `/workspace`.
- an executable (`"command": ["bin/codegen"]`): `bin/codegen --task <task> --out <file> [--prev ... --errors ...]`,
  stdout the result, stderr the log, exit 0 (agents/_runner.py). Build it before connecting it (no network).
- a Python package (`"entry": "module:run"`): `run(task, inputs, ctx)` with `inputs["out"]`, `inputs.get("prev")`,
  `inputs.get("errors")`; open them as `ctx.workspace / inputs["out"]` (tests/fixtures/agents/toy-codegen).

One agent per language; an agent whose language has no toolchain stops the server at start (a typo shows at once).

Smoke check after adding both: `python -c 'from agent import tools; print(tools.TOOLCHAINS, "write_code" in tools.REGISTRY)'`.

The loop stops when it builds (and runs), when an attempt gives the same errors as the last one, or after
`attempts` (default 4). Every attempt is kept: `outputs/codegen/<id>/attempt-N/{main<ext>, errors.json}`.
