"""Turn big logs into a Parquet dataset in /workspace/cache/<dataset>/ (Loop 32). Runs in the sandbox, as a long job:
prints `PROGRESS done total note` lines (bytes) and a short summary at the end.

  python /vtools/ingest.py stdf <dataset> FILE...                      STDF V4 -> parts, results, tests
  python /vtools/ingest.py text <dataset> --pattern RE [--time COL --time-format FMT] FILE...
                                                                        text lines -> rows (named groups = columns)

A dataset whose sources (path, size, mtime) are unchanged is not built again. Tables are queried with
/vtools/query.py (DuckDB) as <dataset>.<table>.
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

CACHE = os.environ.get("VTOOLS_CACHE", "/workspace/cache")
VERSION = 2  # of the tables this script makes: a dataset from another version is built again
NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


def say(done, total, note=""):
    print(f"PROGRESS {done} {total} {note}", flush=True)


def sources(files):
    return [{"path": os.path.abspath(f), "size": os.path.getsize(f), "mtime": int(os.path.getmtime(f))} for f in files]


def stdf_rust(files, out):
    """The same tables from sandbox/stdf-rs (about 25x faster); its progress lines pass through. A failure of the
    binary is reported, not retried in Python (VTOOLS_STDF=python chooses the Python reader)."""
    import subprocess
    p = subprocess.Popen(["stdf2parquet", out, *files], stdout=subprocess.PIPE, text=True)
    last = ""
    for line in p.stdout:
        if line.startswith("PROGRESS "):
            print(line, end="", flush=True)
        elif line.strip():
            last = line
    if p.wait() != 0:
        raise SystemExit(f"stdf2parquet failed (exit {p.returncode})")
    r = json.loads(last)
    return {"parts": r["parts"], "results": r["results"], "tests": r["tests"]}, r["notes"], "rust"


def stdf(files, out):
    if os.environ.get("VTOOLS_STDF") != "python" and shutil.which("stdf2parquet"):
        return stdf_rust(files, out)
    import pyarrow as pa
    import pyarrow.parquet as pq
    sys.path.insert(0, os.path.dirname(__file__))
    from stdf import flatten
    schema = {
        "parts": pa.schema([("file", pa.string()), ("lot", pa.string()), ("wafer", pa.string()), ("head", pa.uint8()),
                            ("site", pa.uint8()), ("part", pa.int64()), ("part_id", pa.string()), ("x", pa.int16()),
                            ("y", pa.int16()), ("hard_bin", pa.uint16()), ("soft_bin", pa.uint16()),
                            ("passed", pa.bool_()), ("part_flg", pa.uint8()), ("test_t", pa.uint32())]),
        "results": pa.schema([("file", pa.string()), ("part", pa.int64()), ("test_num", pa.uint32()),
                              ("result", pa.float32()), ("failed", pa.bool_()), ("test_flg", pa.uint8())]),
    }
    writers = {t: pq.ParquetWriter(os.path.join(out, f"{t}.parquet"), s, compression="zstd") for t, s in schema.items()}
    total, base, counts, tests, notes = sum(os.path.getsize(f) for f in files), 0, {"parts": 0, "results": 0}, {}, []
    try:
        for f in files:
            name = os.path.basename(f)

            def sink(table, cols):
                cols = {"file": [name] * len(next(iter(cols.values()))), **cols}
                writers[table].write_table(pa.table(cols, schema=schema[table]))
                counts[table] += len(cols["file"])
            r = flatten(f, sink, progress=lambda done: say(base + done, total, name))
            base += os.path.getsize(f)
            for tn, (tname, lo, hi, units) in r["tests"].items():
                tests.setdefault(tn, (tname, lo, hi, units))
            notes += r["notes"]
    finally:
        for w in writers.values():
            w.close()
    tn = sorted(tests)
    pq.write_table(pa.table({"test_num": pa.array(tn, pa.uint32()), "name": pa.array([tests[t][0] for t in tn], pa.string()),
                             "lo": pa.array([tests[t][1] for t in tn], pa.float32()),
                             "hi": pa.array([tests[t][2] for t in tn], pa.float32()),
                             "units": pa.array([tests[t][3] for t in tn], pa.string())}), os.path.join(out, "tests.parquet"))
    counts["tests"] = len(tn)
    return counts, notes, "python"


def text(files, out, pattern, time_col=None, time_format=None):
    import polars as pl
    rx = re.compile(pattern)
    names = list(rx.groupindex)
    if not names:
        raise SystemExit("the pattern needs named groups, e.g. (?P<ts>\\S+) (?P<tool>\\S+)")
    total, base, rows, unmatched = sum(os.path.getsize(f) for f in files), 0, 0, 0
    os.makedirs(os.path.join(out, "rows"))
    for i, f in enumerate(files):
        say(base, total, os.path.basename(f))
        lf = (pl.scan_csv(f, separator="\x1f", has_header=False, quote_char=None, new_columns=["line"],
                          schema_overrides={"line": pl.Utf8}, encoding="utf8-lossy", truncate_ragged_lines=True)
              .select(pl.col("line").str.extract_groups(pattern).alias("g")).unnest("g"))
        if time_col:
            lf = lf.with_columns(pl.col(time_col).str.to_datetime(time_format, strict=False))
        part = os.path.join(out, "rows", f"{i:04d}.parquet")
        lf.with_columns(pl.lit(os.path.basename(f)).alias("file")).sink_parquet(part, compression="zstd")
        n = pl.scan_parquet(part).select(pl.len()).collect().item()
        bad = pl.scan_parquet(part).filter(pl.col(names[0]).is_null()).select(pl.len()).collect().item()
        rows, unmatched = rows + n - bad, unmatched + bad
        base += os.path.getsize(f)
    say(total, total, "done")
    return {"rows": rows, "unmatched_lines": unmatched}, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("format", choices=["stdf", "text"])
    ap.add_argument("dataset")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--pattern")
    ap.add_argument("--time")
    ap.add_argument("--time-format")
    a = ap.parse_args()
    if not NAME.match(a.dataset):
        raise SystemExit("dataset: lowercase letters, digits and _, starting with a letter")
    missing = [f for f in a.files if not os.path.isfile(f)]
    if missing:
        raise SystemExit(f"no such file: {missing}")
    out = os.path.join(CACHE, a.dataset)
    import fcntl
    lock = open(os.path.join(CACHE, f".{a.dataset}.lock"), "w")
    try:  # one build of a dataset at a time (two would share <name>.building)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(f"{a.dataset} is being built by another job; wait for it, or use another name")
    src = sources(a.files)
    meta_f = os.path.join(out, "meta.json")
    key = {"version": VERSION, "format": a.format, "pattern": a.pattern, "time": a.time, "time_format": a.time_format,
           "sources": src}
    if os.path.exists(meta_f):
        meta = json.load(open(meta_f))
        if {k: meta.get(k) for k in key} == key:
            print(f"already in the cache: {a.dataset} {json.dumps(meta['counts'])} (built {meta['built']})")
            return
    tmp = out + ".building"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    t = time.time()
    if a.format == "stdf":
        counts, notes, reader = stdf(a.files, tmp)
    else:
        if not a.pattern:
            raise SystemExit("text needs --pattern with named groups")
        (counts, notes), reader = text(a.files, tmp, a.pattern, a.time, a.time_format), "polars"
    meta = {**key, "counts": counts, "built": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": round(time.time() - t, 1),
            "reader": reader}
    json.dump(meta, open(os.path.join(tmp, "meta.json"), "w"), indent=1)
    shutil.rmtree(out, ignore_errors=True)
    os.replace(tmp, out)  # a dataset is either the whole new one or the old one
    print(f"dataset {a.dataset}: {json.dumps(counts)} in {meta['seconds']}s ({meta['reader']}); tables: "
          + ", ".join(sorted(f.split('.')[0] for f in os.listdir(out) if f != "meta.json")))
    for n in notes:
        print("note:", n)


if __name__ == "__main__":
    main()
