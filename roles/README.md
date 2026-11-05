# roles/: what a generated program must do, and the checks that it does (Loop 41)

Compiling proves the syntax, not the intent. A role is a kind of program (here: die binning) with a spec format,
an oracle (the right answer, in Python), a case generator and a judge. With `write_code(..., role, spec)` a program
that built is run on the role's cases before it is accepted (agent/codegen.py):

| guard | what the agent hears when it fails | catches |
|---|---|---|
| compile | the compiler's errors (stage, source line) | syntax, types |
| examples (public) | the example input, the expected and the actual output | wrong rule, wrong format |
| hidden | only "k/N wrong (boundary 12, missing 3)" and item ids, never an input or an answer | fitting the program to the examples; mistakes the examples happen to miss |

The expected outputs come only from the role's oracle: never from the codegen agent (it would grade itself) or our
model (it does not know the language and would guess). The role and the language know nothing of each other; their
only contract is the program's stdin and stdout, so either can be swapped alone.

## role.py

Trusted host code, shipped with the server (like skills). One folder per role, `roles/<name>/role.py`:

| name | what |
|---|---|
| `DESCRIPTION` | one line for the model |
| `SPEC_SCHEMA` | JSON Schema of the spec the model passes |
| `validate(spec)` | the spec normalized, or `ValueError` (goes back to the model) |
| `describe(spec, examples)` | the task text the codegen agent gets: I/O format, rules, the spec, worked examples |
| `cases(spec, seed, n, size)` | `n` inputs `{"id", "input", "categories"}`; categories name the kind of mistake each item probes |
| `expected(spec, input)` | the oracle's output |
| `judge(spec, case, output)` | `{"format": [problems], "wrong": [{"item", "category", "detail"}], "total"}`; compare by tokens, a language may pad its numbers |

Test the oracle by hand first (tests/test_l32_guards.py `t_oracle`): every other check trusts it.
