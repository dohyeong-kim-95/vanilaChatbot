"""First look at unknown log/data files: what kind each is and which skill/view fits it.
Standard library only. Reads at most the first MAX_BYTES of each file for the guess (counts lines of all of it).

python sniff.py FILE [FILE ...]
"""
import csv
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "shmoo" / "scripts"))
sys.path.insert(0, str(HERE.parents[1] / "card-transactions" / "scripts"))
import shmoo  # noqa: E402
import tx  # noqa: E402

MAX_BYTES = 2_000_000
LEVEL = re.compile(r"\b(FATAL|CRITICAL|ERROR|ERR|WARN(?:ING)?|INFO|DEBUG|TRACE)\b")
STAMP = re.compile(r"\d{4}[-/.]\d{2}[-/.]\d{2}[ T]\d{2}:\d{2}(:\d{2})?|\b[A-Z][a-z]{2} +\d{1,2} \d{2}:\d{2}:\d{2}\b")


def _text(path):
    raw = Path(path).read_bytes()[:MAX_BYTES]
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), "binary/unknown"


def _shape(msg):
    """Message template: numbers, hex ids, quoted strings replaced, so repeats group together."""
    msg = re.sub(r"0x[0-9a-fA-F]+|\b[0-9a-f]{8,}\b", "<id>", msg)
    msg = re.sub(r"\"[^\"]*\"|'[^']*'", "<str>", msg)
    return re.sub(r"\d+(\.\d+)?", "<n>", msg).strip()[:120]


def _table(lines):
    """(delimiter, header) if most lines split into the same number (>= 3) of csv fields; preamble lines allowed."""
    best = None
    for d in ",\t;|":
        counts = [len(r) for r in csv.reader(io.StringIO("\n".join(lines[:200])), delimiter=d)]
        n, freq = Counter(counts).most_common(1)[0]
        if n >= 3 and freq >= 0.8 * len(counts) and (not best or freq > best[0]):
            header = next(r for r in csv.reader(io.StringIO("\n".join(lines[:200])), delimiter=d) if len(r) == n)
            best = (freq, d, header)
    return best[1:] if best else None


def sniff(path):
    p = Path(path)
    out = {"file": p.name, "bytes": p.stat().st_size}
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        rows, infos = tx.load(p, with_info=True)
        ok = [i for i in infos if "error" not in i]
        out.update(kind="spreadsheet", sheets=[i.get("sheet") for i in infos])
        out["fits"] = [("card-transactions", "date + amount columns found")] if ok else []
        return out
    text, enc = _text(p)
    with p.open("rb") as fh:
        out["lines"] = sum(1 for _ in fh)
    lines = [l for l in text.splitlines() if l.strip()]
    out.update(encoding=enc, head=[l[:160] for l in lines[:6]])
    fits = []
    body = [l for l in lines if not l.lstrip().startswith(("#", "//", ";"))]
    if body and body[0].lstrip().startswith("{"):
        try:
            keys = Counter(k for l in body[:500] for k in json.loads(l))
            out.update(kind="json lines", keys=[k for k, _ in keys.most_common(20)])
        except ValueError:
            pass
    if "kind" not in out and body:
        table = _table(body)
        if table:
            out.update(kind="table", delimiter=repr(table[0]), columns=table[1])
    try:  # known formats: ask their own loaders, whatever the table guess said
        h, rows = shmoo.read_table(p)
        m = shmoo.guess(h, rows[:20000])
        vals = {r[m["result"]].strip().upper() for r in rows[:5000]} if m["result"] else set()
        if m["x"] and m["y"] and vals and vals <= shmoo.PASS | shmoo.FAIL:
            fits.append(("shmoo", f"sweep x={m['x']} y={m['y']} pass/fail={m['result']} device={m['device']} "
                                  f"conditions={m['filters']}"))
    except Exception:
        pass
    try:
        rows, infos = tx.load(p, with_info=True)
        if rows and not any("error" in i for i in infos):
            out.setdefault("kind", "table")
            fits.append(("card-transactions", f"date + amount columns, {len(rows)} transactions"))
    except Exception:
        pass
    if "kind" not in out:
        levels = Counter(m.group(1) for l in lines if (m := LEVEL.search(l)))
        stamps = [m.group(0) for l in lines if (m := STAMP.search(l))]
        if levels or len(stamps) > len(lines) / 2:
            out.update(kind="text log", levels=dict(levels.most_common()),
                       time_span=[stamps[0], stamps[-1]] if stamps else None)
            bad = [_shape(LEVEL.split(l, 1)[-1]) for l in lines if (m := LEVEL.search(l)) and
                   m.group(1) in ("FATAL", "CRITICAL", "ERROR", "ERR", "WARN", "WARNING")]
            out["top_error_shapes"] = Counter(bad).most_common(5)
        else:
            out["kind"] = "unknown text"
    out["fits"] = fits
    return out


def main(paths):
    for p in paths:
        s = sniff(p)
        print(f"== {s['file']}  ({s['bytes']:,} bytes" + (f", {s['lines']:,} lines" if "lines" in s else "") + ")")
        for k, v in s.items():
            if k not in ("file", "bytes", "lines", "fits"):
                print(f"  {k}: {v}")
        print("  fits: " + ("; ".join(f"{n} ({why})" for n, why in s["fits"]) or "no specific skill: analyze with "
                            "bash (grep/awk/sort/uniq) or python"))


if __name__ == "__main__":
    main(sys.argv[1:])
