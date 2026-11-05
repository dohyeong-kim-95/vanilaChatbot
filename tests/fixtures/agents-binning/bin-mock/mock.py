"""A codegen agent that makes the mistakes weak models make (Loop 41), one per attempt, to test the guards.
It reads the spec from inputs["spec"] and, from the task text, "modes=a,b,c": attempt N writes the program of
mode N (the last one again after that).
  syntax      a missing brace                              -> the compiler
  strict      limits exclusive (a value at a limit fails)  -> hidden (boundary)
  tableorder  tests in table order, not priority order     -> hidden (priority)
  zero        a missing value read as 0                    -> hidden (missing)
  noskip      "skip" tests give the missing bin too        -> hidden (skip)
  nomarginal  no marginal bin                              -> hidden (marginal, boundary)
  hardcode    prints the examples' answers                 -> hidden only
  hang        never ends                                   -> the time limit per input
  right       correct
"""
import re


def program(spec, mode, task=""):
    tests = list(enumerate(spec["tests"], 1))
    if mode != "tableorder":
        tests.sort(key=lambda it: it[1]["priority"])
    lt, gt = ("<=", ">=") if mode == "strict" else ("<", ">")
    if mode == "hang":
        return "integer k\nk = 0\nrepeat {\n  k = k + 1\n}\nend\n"
    if mode == "hardcode":
        outs = re.findall(r"Expected output:\n((?:-?\d+ -?\d+ -?\d+\n)+)", task)
        body = ""  # the example whose first die this is: its answers, as they are
        for o in outs:
            lines = o.splitlines()
            x, y = lines[0].split()[:2]
            prints = "".join(f"  write(6,*) {ln.replace(' ', ', ')}\n" for ln in lines)
            body += f"if (x == {x} & y == {y}) {{\n{prints}}}\n"
        return f"integer n, x, y\nread(5,*) n\nread(5,*) x, y\n{body}end\n"
    checks = []
    for i, t in tests:
        conds = ([f"v({i}) {lt} {t['lo']}d0"] if t["lo"] is not None else []) + \
                ([f"v({i}) {gt} {t['hi']}d0"] if t["hi"] is not None else [])
        skip = t["missing"] == "skip" and mode != "noskip"
        miss = "" if mode == "zero" else \
            f"    if (v({i}) == -9999d0) {{ {'sk = 1' if skip else 'b = %d' % spec['missing_bin']} }}\n"
        checks.append(f"  sk = 0\n  if (b == 0) {{\n{miss}"
                      f"    if (b == 0 & sk == 0 & ({' | '.join(conds)})) b = {t['bin']}\n  }}\n")
    marg = []
    if "marginal_pct" in spec and mode != "nomarginal":
        for i, t in enumerate(spec["tests"], 1):
            for lim in (t["lo"], t["hi"]):
                if lim is not None:
                    m = spec["marginal_pct"] / 100 * abs(lim)
                    marg.append(f"    if (v({i}) != -9999d0 & dabs(v({i}) - ({lim}d0)) <= {m!r}d0) b = "
                                f"{spec['marginal_bin']}\n")
    zero = "  do j = 1, nt { if (v(j) == -9999d0) v(j) = 0d0 }\n" if mode == "zero" else ""
    src = (f"integer n, i, j, x, y, b, nt, sk\ndouble precision v({len(spec['tests'])})\nnt = {len(spec['tests'])}\n"
           f"read(5,*) n\ndo i = 1, n {{\n  read(5,*) x, y, (v(j), j = 1, nt)\n{zero}  b = 0\n"
           + "".join(checks) + (f"  if (b == 0) {{\n{''.join(marg)}  }}\n" if marg else "")
           + f"  if (b == 0) b = {spec['pass_bin']}\n  write(6,*) x, y, b\n}}\nend\n")
    return src.replace("do i = 1, n {", "do i = 1, n", 1) if mode == "syntax" else src


def run(task, inputs, ctx):
    modes = re.search(r"modes=([a-z,]+)", task)[1].split(",")
    n = int(re.search(r"attempt-(\d+)", inputs["out"])[1])
    mode = modes[min(n, len(modes)) - 1]
    out = ctx.workspace / inputs["out"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(program(inputs["spec"], mode, task))
    return f"wrote {mode}"
