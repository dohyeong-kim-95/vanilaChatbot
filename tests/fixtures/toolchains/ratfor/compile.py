"""The compiler adapter of the stand-in language (Loop 40): Ratfor -> Fortran 77 (ratfor) -> program (gfortran).
Prints one JSON line {"ok", "errors": [{"stage", "line", "message"}]}, lines in the Ratfor source.
python compile.py <src.r> <program>

ratfor exits 0 on errors (they are only on stderr), and gfortran's line numbers are those of the generated .f: a
marker comment before every source line ("#@L<n>", kept by -C as "C@L<n>") maps them back. A .f line before the
first marker, or a link error, has no source line (null): a wrong line sent to the codegen agent is worse than none.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RATFOR_ERR = re.compile(r"^error at line (\d+):\s*(.*)$")
FORTRAN_AT = re.compile(r"^[^:\s]+\.f:(\d+):\d+:\s*$")
FORTRAN_MSG = re.compile(r"^(Error|Fatal Error):\s*(.*)$")
MARK = re.compile(r"^C@L(\d+)\s*$")


def main(src, program):
    lines = Path(src).read_text(errors="replace").splitlines()
    errors = []
    with tempfile.TemporaryDirectory() as d:
        marked, f = Path(d) / "m.r", Path(d) / "main.f"
        marked.write_text("".join(f"#@L{i}\n{line}\n" for i, line in enumerate(lines, 1)))
        p = subprocess.run(["ratfor", "-C", str(marked)], capture_output=True, text=True)
        err = p.stderr.splitlines()
        for i, e in enumerate(err):
            m = RATFOR_ERR.match(e.strip())
            # the message is on the line itself or the next one; ratfor does not count comment lines (the markers),
            # so its line is the source's: the line where it noticed the error (an open "(" : the next line)
            if m:
                msg = m[2] or (err[i + 1].strip() if i + 1 < len(err) else "")
                errors.append({"stage": "ratfor", "line": min(max(int(m[1]), 1), len(lines) or 1),
                               "message": msg.rstrip(".")})
        if errors or p.returncode:
            return {"ok": False, "errors": errors[:20] or [{"stage": "ratfor", "line": None,
                                                         "message": (p.stderr.strip() or f"exit {p.returncode}")[-300:]}]}
        f.write_text(p.stdout)
        source_of, cur = {}, None
        for n, line in enumerate(p.stdout.splitlines(), 1):
            m = MARK.match(line)
            cur = int(m[1]) if m else cur
            source_of[n] = cur
        p = subprocess.run(["gfortran", "-ffixed-line-length-none", "-fcheck=all", "-o", program, str(f)],
                           capture_output=True, text=True)
        at = None
        for line in p.stderr.splitlines():
            m = FORTRAN_AT.match(line)
            if m:
                at = int(m[1])
                continue
            m = FORTRAN_MSG.match(line.strip())
            if m:
                errors.append({"stage": "fortran", "line": source_of.get(at), "message": m[2]})
                at = None
            elif "undefined reference" in line or line.startswith("collect2:"):
                errors.append({"stage": "link", "line": None, "message": line.split(": ", 1)[-1].strip()})
        if p.returncode and not errors:
            errors.append({"stage": "fortran", "line": None, "message": p.stderr.strip()[-300:] or f"exit {p.returncode}"})
    return {"ok": not errors, "errors": errors[:20]}  # one line under the 10 KB the loop reads (codegen.MAX_ERRORS)


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2]), ensure_ascii=False))
