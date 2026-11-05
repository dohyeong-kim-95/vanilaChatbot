"""Hesk, a made-up language (Loop 43): the stand-in no model has seen. Ratfor is real, so a model may know it from
its training; Hesk exists only here and in its one-page reference (bench/agents/haiku-hesk/REFERENCE.md).
python hesk.py check <src.hk> <program>   one JSON line {"ok", "errors": [{"stage", "line", "message"}]}
python hesk.py run <program>              runs it: stdin in, stdout out; a run error on stderr, exit 1

check parses and type-checks the source and writes the program: Python code and the Hesk line of every Python line.
Errors carry the Hesk line where they were noticed. A syntax error stops the parse (what follows it would only be
noise); the errors found before it are kept. run executes the code with bounds-checked arrays, so every run error
names its Hesk line too.

The language (the reference says it for the model):
  -- comment                       to the end of the line
  var x, a[n], g[r, c].            top level only, before use; scalars 0, arrays of 0 (indexes from 0)
  x <- e.   g[i, j] <- e.          assignment
  read x, g[i, j].                 the next integers of stdin
  say e, "text", e.                one line, items separated by one space
  if c then ... elif c then ... else ... done
  while c do ... done
  for i from a below b do ... done     i = a, a+1, ..., b-1 (through b: up to b); bounds read once;
                                       i cannot be assigned in the body; after the loop: the last value it took
  stop.  skip.                     leave the loop / go to its next round
  + - *  div mod (toward zero; mod has the sign of the left side)  = <> < <= > >=  and or not  min max abs
"""
import json
import re
import sys
from pathlib import Path

MAX_ERRORS, MAX_DEPTH, MAX_CELLS = 20, 40, 4_000_000
KEYWORDS = {"var", "read", "say", "if", "then", "elif", "else", "done", "while", "do", "for", "from", "below",
            "through", "stop", "skip", "and", "or", "not", "div", "mod"}
FUNCS = {"min": 2, "max": 2, "abs": 1}
TOKEN = re.compile(r"(?P<ws>[ \t\r]+)|(?P<nl>\n)|(?P<comment>--[^\n]*)|(?P<num>[0-9]+)|"
                   r"(?P<name>[A-Za-z][A-Za-z0-9_]*)|(?P<str>\"[^\"\n]*\")|(?P<op><-|<>|<=|>=|[-+*=<>.,()\[\]])")


class Error(Exception):
    def __init__(self, line, message):
        super().__init__(message)
        self.line = line


class Tok:
    def __init__(self, kind, text, line):
        self.kind, self.text, self.line = kind, text, line

    def show(self):
        return "the end of the file" if self.kind == "eof" else f"'{self.text}'"


def lex(src):
    out, line, i = [], 1, 0
    while i < len(src):
        m = TOKEN.match(src, i)
        if not m:
            ch = src[i]
            raise Error(line, f"unexpected character '{ch}'" if ch.isprintable() else f"unexpected character {ch!r}")
        kind, text = m.lastgroup, m[0]
        if kind == "nl":
            line += 1
        elif kind == "name" and text in KEYWORDS:
            out.append(Tok("kw", text, line))
        elif kind in ("num", "name", "str", "op"):
            out.append(Tok(kind, text, line))
        i = m.end()
    out.append(Tok("eof", "", line))
    return out


class Parser:
    """Recursive descent straight to Python lines: [(indent, code, hesk line)]."""

    def __init__(self, toks):
        self.toks, self.i, self.errors, self.out = toks, 0, [], []
        self.names = {}       # name -> 0 scalar, 1 or 2 array dimensions
        self.loop_vars = []   # for-loop variables in force
        self.loops = 0        # loops around the current statement
        self.depth = 0

    # ---- tokens ----
    @property
    def tok(self):
        return self.toks[self.i]

    def at(self, text):
        return self.tok.kind in ("kw", "op") and self.tok.text == text

    def take(self, text=None):
        t = self.tok
        if text is not None and not self.at(text):
            raise Error(t.line, f"expected '{text}', found {t.show()}")
        if t.kind != "eof":
            self.i += 1
        return t

    def name(self, what):
        t = self.tok
        if t.kind == "kw":
            raise Error(t.line, f"'{t.text}' is a keyword, not a name ({what})")
        if t.kind != "name":
            raise Error(t.line, f"expected a name ({what}), found {t.show()}")
        self.i += 1
        return t

    def semantic(self, line, message):  # a mistake that does not stop the parse
        self.errors.append({"stage": "check", "line": line, "message": message})

    def emit(self, indent, code, line):
        self.out.append((indent, code, line))

    # ---- statements ----
    def program(self):
        while self.tok.kind != "eof":
            if self.at("done") or self.at("elif") or self.at("else"):
                raise Error(self.tok.line, f"{self.tok.show()} without an open if/while/for")
            self.statement(0, top=True)

    def block(self, indent, ends):
        """Statements up to one of `ends` (not taken)."""
        start = len(self.out)
        while not any(self.at(e) for e in ends):
            if self.tok.kind == "eof":
                raise Error(self.tok.line, f"expected {' or '.join(repr(e) for e in ends)} before the end of the file")
            self.statement(indent)
        if len(self.out) == start:
            self.emit(indent, "pass", self.tok.line)

    def statement(self, indent, top=False):
        t = self.tok
        if t.kind == "kw":
            k = t.text
            if k == "var":
                return self.var(indent, top)
            if k in ("read", "say", "if", "while", "for", "stop", "skip"):
                return getattr(self, "s_" + k)(indent)
            raise Error(t.line, f"a statement cannot start with '{k}'")
        if t.kind == "name":
            return self.assign(indent)
        raise Error(t.line, f"a statement cannot start with {t.show()}")

    def end(self):
        t = self.tok
        if not self.at("."):
            raise Error(t.line, f"expected '.' at the end of the statement, found {t.show()}")
        self.i += 1

    def var(self, indent, top):
        line = self.take("var").line
        if not top:
            self.semantic(line, "var only at the top level, not inside if/while/for")
        while True:
            n = self.name("in var")
            dims = []
            if self.at("["):
                self.take("[")
                dims.append(self.expr("int"))
                if self.at(","):
                    self.take(",")
                    dims.append(self.expr("int"))
                self.take("]")
            if n.text in FUNCS:
                self.semantic(n.line, f"'{n.text}' is a built-in function, not a name to declare")
            elif n.text in self.names:
                self.semantic(n.line, f"'{n.text}' is declared twice")
            self.names[n.text] = len(dims)
            if dims:
                self.emit(indent, f"v_{n.text} = _array({', '.join(dims)})", n.line)
            else:
                self.emit(indent, f"v_{n.text} = 0", n.line)
            if not self.at(","):
                break
            self.take(",")
        self.end()

    def target(self, why):
        """A variable or an array cell to write -> Python target."""
        n = self.name(why)
        dims = self.names.get(n.text)
        if dims is None:
            self.semantic(n.line, f"'{n.text}' is not declared (var {n.text}.)")
            dims = 0
        if n.text in self.loop_vars:
            self.semantic(n.line, f"'{n.text}' is the variable of a for loop around this line: it cannot be changed")
        return self.cell(n, dims)

    def cell(self, n, dims):
        if self.at("["):
            self.take("[")
            idx = [self.expr("int")]
            if self.at(","):
                self.take(",")
                idx.append(self.expr("int"))
            self.take("]")
            if dims == 0:
                self.semantic(n.line, f"'{n.text}' is not an array")
                return f"v_{n.text}"
            if len(idx) != dims:
                self.semantic(n.line, f"'{n.text}' has {dims} index{'es' if dims > 1 else ''}, not {len(idx)}")
                return f"v_{n.text}"
            return f"v_{n.text}[_ix{dims}(v_{n.text}, {', '.join(idx)})]"
        if dims:
            self.semantic(n.line, f"'{n.text}' is an array: give its index{'es' if dims > 1 else ''}, "
                                  f"{n.text}[{', '.join('ij'[:dims])}]")
        return f"v_{n.text}"

    def assign(self, indent):
        line = self.tok.line
        tgt = self.target("to assign")
        if self.at("="):
            raise Error(self.tok.line, "expected '<-' (assignment), found '=' (a comparison)")
        self.take("<-")
        e = self.expr("int")
        self.end()
        self.emit(indent, f"{tgt} = {e}", line)

    def s_read(self, indent):
        line = self.take("read").line
        while True:
            self.emit(indent, f"{self.target('to read into')} = _read()", line)
            if not self.at(","):
                break
            self.take(",")
        self.end()

    def s_say(self, indent):
        line = self.take("say").line
        items = []
        while True:
            if self.tok.kind == "str":
                items.append(repr(self.take().text[1:-1]))
            else:
                items.append(f"str({self.expr('int')})")
            if not self.at(","):
                break
            self.take(",")
        self.end()
        self.emit(indent, f"_say({', '.join(items)})", line)

    def nested(self, line):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise Error(line, f"blocks nested more than {MAX_DEPTH} deep")

    def s_if(self, indent):
        line = self.take("if").line
        self.nested(line)
        self.emit(indent, f"if {self.expr('bool')}:", line)
        self.take("then")
        self.block(indent + 1, ("elif", "else", "done"))
        while self.at("elif"):
            line = self.take("elif").line
            self.emit(indent, f"elif {self.expr('bool')}:", line)
            self.take("then")
            self.block(indent + 1, ("elif", "else", "done"))
        if self.at("else"):
            self.emit(indent, "else:", self.take("else").line)
            self.block(indent + 1, ("done",))
        self.take("done")
        self.depth -= 1
        self.end_block()

    def end_block(self):
        if self.at("."):  # 'done.' is the commonest slip; say so rather than "a statement cannot start with '.'"
            raise Error(self.tok.line, "no '.' after 'done' (only simple statements end with '.')")

    def s_while(self, indent):
        line = self.take("while").line
        self.nested(line)
        self.emit(indent, f"while {self.expr('bool')}:", line)
        self.take("do")
        self.loops += 1
        self.block(indent + 1, ("done",))
        self.loops -= 1
        self.take("done")
        self.depth -= 1
        self.end_block()

    def s_for(self, indent):
        line = self.take("for").line
        self.nested(line)
        n = self.name("the loop variable")
        dims = self.names.get(n.text)
        if dims is None:
            self.semantic(n.line, f"'{n.text}' is not declared (var {n.text}.)")
        elif dims:
            self.semantic(n.line, f"'{n.text}' is an array, not a loop variable")
        if n.text in self.loop_vars:
            self.semantic(n.line, f"'{n.text}' is already the variable of a for loop around this one")
        self.take("from")
        a = self.expr("int")
        if not (self.at("below") or self.at("through")):
            raise Error(self.tok.line, f"expected 'below' or 'through', found {self.tok.show()}")
        kind = self.take().text
        b = self.expr("int")
        self.take("do")
        self.emit(indent, f"for v_{n.text} in range({a}, {b}{' + 1' if kind == 'through' else ''}):", line)
        self.loops += 1
        self.loop_vars.append(n.text)
        self.block(indent + 1, ("done",))
        self.loop_vars.pop()
        self.loops -= 1
        self.take("done")
        self.depth -= 1
        self.end_block()

    def s_stop(self, indent):
        return self.jump(indent, "stop", "break")

    def s_skip(self, indent):
        return self.jump(indent, "skip", "continue")

    def jump(self, indent, word, py):
        line = self.take(word).line
        if not self.loops:
            self.semantic(line, f"'{word}' outside a while/for loop")
        self.end()
        self.emit(indent, py, line)

    # ---- expressions: (python text, type) checked against what the caller wants ----
    def expr(self, want):
        line = self.tok.line
        code, typ = self.e_or()
        if typ != want:
            self.semantic(line, "expected a number, found a condition" if want == "int"
                          else "expected a condition (a comparison), found a number")
        return code

    def nest(self):
        self.depth += 1
        if self.depth > 4 * MAX_DEPTH:
            raise Error(self.tok.line, "expression nested too deep")

    def e_or(self):
        a, ta = self.e_and()
        while self.at("or"):
            line = self.take().line
            b, tb = self.e_and()
            self.both_bool(line, "or", ta, tb)
            a, ta = f"({a} or {b})", "bool"
        return a, ta

    def e_and(self):
        a, ta = self.e_not()
        while self.at("and"):
            line = self.take().line
            b, tb = self.e_not()
            self.both_bool(line, "and", ta, tb)
            a, ta = f"({a} and {b})", "bool"
        return a, ta

    def both_bool(self, line, op, ta, tb):
        if ta != "bool" or tb != "bool":
            self.semantic(line, f"'{op}' joins two conditions (comparisons), not numbers")

    def e_not(self):
        if self.at("not"):
            line = self.take().line
            self.nest()
            a, ta = self.e_not()
            self.depth -= 1
            if ta != "bool":
                self.semantic(line, "'not' needs a condition (a comparison), not a number")
            return f"(not {a})", "bool"
        return self.e_cmp()

    CMP = {"=": "==", "<>": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">="}

    def e_cmp(self):
        a, ta = self.e_add()
        if self.tok.kind == "op" and self.tok.text in self.CMP:
            t = self.take()
            b, tb = self.e_add()
            if ta != "int" or tb != "int":
                self.semantic(t.line, f"'{t.text}' compares two numbers")
            if self.tok.kind == "op" and self.tok.text in self.CMP:
                raise Error(self.tok.line, f"comparisons do not chain: join them with 'and' (found {self.tok.show()})")
            return f"({a} {self.CMP[t.text]} {b})", "bool"
        return a, ta

    def e_add(self):
        a, ta = self.e_mul()
        while self.at("+") or self.at("-"):
            t = self.take()
            b, tb = self.e_mul()
            self.both_int(t, ta, tb)
            a, ta = f"({a} {t.text} {b})", "int"
        return a, ta

    def e_mul(self):
        a, ta = self.e_unary()
        while self.at("*") or self.at("div") or self.at("mod"):
            t = self.take()
            b, tb = self.e_unary()
            self.both_int(t, ta, tb)
            a = f"({a} * {b})" if t.text == "*" else f"_{t.text}({a}, {b})"
            ta = "int"
        return a, ta

    def both_int(self, t, ta, tb):
        if ta != "int" or tb != "int":
            self.semantic(t.line, f"'{t.text}' needs two numbers, not a condition")

    def e_unary(self):
        if self.at("-"):
            t = self.take()
            self.nest()
            a, ta = self.e_unary()
            self.depth -= 1
            if ta != "int":
                self.semantic(t.line, "'-' needs a number")
            return f"(-{a})", "int"
        return self.e_primary()

    def e_primary(self):
        t = self.tok
        if t.kind == "num":
            self.i += 1
            return t.text.lstrip("0") or "0", "int"
        if self.at("("):
            self.take("(")
            self.nest()
            a, ta = self.e_or()
            self.depth -= 1
            self.take(")")
            return a, ta
        if t.kind == "name":
            self.i += 1
            if t.text in FUNCS and t.text not in self.names:
                self.take("(")
                args = [self.expr("int")]
                while self.at(","):
                    self.take(",")
                    args.append(self.expr("int"))
                self.take(")")
                if len(args) != FUNCS[t.text]:
                    self.semantic(t.line, f"{t.text} takes {FUNCS[t.text]} argument{'s' * (FUNCS[t.text] > 1)}, "
                                          f"not {len(args)}")
                return f"{t.text}({', '.join(args)})", "int"
            dims = self.names.get(t.text)
            if dims is None:
                self.semantic(t.line, f"'{t.text}' is not declared (var {t.text}.)")
                dims = 0
            return self.cell(t, dims), "int"
        if t.kind == "str":
            raise Error(t.line, "text in quotes only as an item of say")
        raise Error(t.line, f"expected a number, a name or '(', found {t.show()}")


def check(src):
    """-> (errors, program text or None)."""
    try:
        p = Parser(lex(src))
    except Error as e:
        return [{"stage": "syntax", "line": e.line, "message": str(e)}], None
    try:
        p.program()
    except Error as e:
        p.errors.append({"stage": "syntax", "line": e.line, "message": str(e)})
    if p.errors:
        return p.errors[:MAX_ERRORS], None
    code = "\n".join("    " * ind + c for ind, c, _ in p.out) + "\n"
    return [], json.dumps({"py": code, "lines": [ln for _, _, ln in p.out]})


# ---- run time ----

class RunError(Exception):
    pass


class Array(list):
    __slots__ = ("dims", "n", "m")


def _array(*dims):
    n = 1
    for d in dims:
        if d < 1:
            raise RunError(f"array size {d}: sizes are at least 1")
        n *= d
    if n > MAX_CELLS:
        raise RunError(f"array of {n} cells: at most {MAX_CELLS}")
    a = Array([0] * n)
    a.dims, a.n, a.m = dims, dims[0], dims[-1]
    return a


def _bad(a, idx):
    return RunError(f"index {', '.join(map(str, idx))} is outside the array "
                    f"({', '.join(f'0..{n - 1}' for n in a.dims)})")


def _ix1(a, i):  # one per array access: kept short, it is most of a program's time
    if 0 <= i < a.n:
        return i
    raise _bad(a, (i,))


def _ix2(a, i, j):
    if 0 <= i < a.n and 0 <= j < a.m:
        return i * a.m + j
    raise _bad(a, (i, j))


def _div(a, b):
    if b == 0:
        raise RunError("division by zero")
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def _mod(a, b):
    if b == 0:
        raise RunError("mod by zero")
    return a - b * _div(a, b)


def run(program):
    prog = json.loads(Path(program).read_text())
    words, out = [], []

    def _read():
        if not words:  # stdin is read at the first read: a program that reads nothing does not wait for it
            words.append(iter(sys.stdin.read().split()))
        w = next(words[0], None)
        if w is None:
            raise RunError("read: no more input")
        if not re.fullmatch(r"-?[0-9]{1,30}", w):
            raise RunError(f"read: '{w[:20]}' is not an integer")
        return int(w)

    def _say(*items):
        out.append(" ".join(items) + "\n")
        if len(out) > 512:
            sys.stdout.write("".join(out))
            out.clear()

    env = {"__builtins__": {"range": range, "min": min, "max": max, "abs": abs, "str": str},
           "_array": _array, "_ix1": _ix1, "_ix2": _ix2, "_div": _div, "_mod": _mod, "_read": _read, "_say": _say}
    try:
        exec(compile(prog["py"], "<hesk>", "exec"), env)
    except (RunError, MemoryError, RecursionError) as e:
        sys.stdout.write("".join(out))
        tb, line = e.__traceback__, None
        while tb is not None:
            if tb.tb_frame.f_code.co_filename == "<hesk>":
                line = prog["lines"][tb.tb_lineno - 1]
            tb = tb.tb_next
        why = str(e) if isinstance(e, RunError) else "out of memory"
        sys.stderr.write(f"run error at line {line}: {why}\n" if line else f"run error: {why}\n")
        return 1
    sys.stdout.write("".join(out))
    return 0


def main(argv):
    if len(argv) == 3 and argv[0] == "check":
        errors, prog = check(Path(argv[1]).read_text(errors="replace"))
        if prog is not None:
            Path(argv[2]).write_text(prog)
        print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False))
        return 0
    if len(argv) == 2 and argv[0] == "run":
        return run(argv[1])
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
