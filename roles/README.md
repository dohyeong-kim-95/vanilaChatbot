# roles/: what a generated program must do, and the checks that it does (Loop 41)

Compiling proves the syntax, not the intent. A role is a kind of program (here: die binning) with a spec format,
an oracle (the right answer, in Python), a case generator and a judge. Roles here: `binning` (die measurements -> bins) and `wafermap` (a WM-811K wafer map -> counts, clusters, edge and ring fails). With `write_code(..., role, spec)` a program
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
| `cases(spec, seed, n, size, example=False)` | `size`: a hint the role defines (items per input), fixed at 5 for examples, 25 for hidden checks, `AUDIT[role][1]` in the evaluation (may be None); ignore it if you like, but accept all three. Ids unique within one call (`k + 1` will do; `run_cases` refuses duplicates). Deterministic: the same arguments give the same list (`random.Random(seed)` and pools in a fixed order only), or the worked examples, the public checks and the evaluation's pairing drift apart. `n` inputs `{"id", "input", "categories"}`; categories name the kind of mistake each item probes; `example`: plain items for the worked examples (the tricky ones are for the hidden checks) |
| `expected(spec, input)` | the oracle's output |
| `judge(spec, case, output)` | `{"format": [problems], "wrong": [{"item", "category", "detail"}], "total"}`; compare by tokens, a language may pad its numbers |

The evaluation (bench/codegen_eval.py) also calls `random_spec(seed)` and reads `case["probe"]` (boundary inputs, counted apart in its audit); wafermap adds `use_maps(maps)` for real data.

Test the oracle by hand first (tests/test_l32_guards.py `t_oracle`): every other check trusts it.

The cases reach the program on stdin only: it runs in the same container as the checker, so a file of cases would let it read the hidden ones.

## Which problems make a role

A guard is only as strong as its oracle. The API stays as it is: `expected` gives one right answer (shown in the
worked examples), `judge` decides.

| problem | oracle and judge | guard |
|---|---|---|
| one exact answer (counts, bins, parsing) | compare tokens with `expected` | strongest: a right program is right on every input |
| numbers within a tolerance | compare with `expected` within the tolerance | strong |
| only properties can be checked (several right answers) | `expected` returns one of them; `judge` checks the property | medium |
| an existing implementation (porting) | call or re-implement it in Python | strong: same input, same output |
| no right answer can be stated (summaries, prose, UI) | none | compile only; a person or an LLM is not a guard |

Programs read stdin and print to stdout, 5 s per case (`codegen.CASE_SECONDS`). A program that reads a file once,
start to end, can be given `/dev/stdin` by a run wrapper in its toolchain (docs/ADAPTING.md); one that must seek
in a file, hold a dialogue, or meet a speed requirement needs the harness (`codegen._CASES`) widened first.
`judge`'s `item` and `category` strings and the case `id` reach the agent from the hidden checks: names and ordinals only, never input values, file names or answers. One case's `categories`, `judge`'s `total` and the number of output items are the same count. How to adapt the whole loop to another
language and problem, with what we measured: [docs/ADAPTING.md](../docs/ADAPTING.md).
