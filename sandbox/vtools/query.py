"""SQL over the Parquet datasets in /workspace/cache (Loop 32), with DuckDB inside the sandbox's limits.
Every dataset (a folder with meta.json) is a schema, every table in it a view: SELECT ... FROM lots.parts.

  python /vtools/query.py [--max-rows N] [--json FILE] "SQL"     (--json: {"columns", "rows", "truncated"} to FILE)
  python /vtools/query.py --tables          the datasets, their tables, columns and row counts
"""
import argparse
import glob
import os
import sys
import time

import duckdb

CACHE = os.environ.get("VTOOLS_CACHE", "/workspace/cache")


def connect():
    con = duckdb.connect()
    os.makedirs(os.path.join(CACHE, ".tmp"), exist_ok=True)
    # the container has 1 GB and 1 CPU; what does not fit spills to the cache's disk
    con.execute(f"SET memory_limit='600MB'; SET threads=1; SET temp_directory='{os.path.join(CACHE, '.tmp')}'")
    views = []
    for meta in sorted(glob.glob(os.path.join(CACHE, "*", "meta.json"))):
        d = os.path.dirname(meta)
        ds = os.path.basename(d)
        if ds.endswith(".building"):  # an ingest in progress (or one that failed): not a dataset yet
            continue
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{ds}"')
        for p in sorted(os.listdir(d)):
            path = os.path.join(d, p)
            if p.endswith(".parquet"):
                src, table = path, p[:-8]
            elif os.path.isdir(path) and glob.glob(os.path.join(path, "*.parquet")):
                src, table = os.path.join(path, "*.parquet"), p
            else:
                continue
            con.execute(f"""CREATE VIEW "{ds}"."{table}" AS SELECT * FROM read_parquet('{src}')""")
            views.append((ds, table))
    return con, views


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sql", nargs="?")
    ap.add_argument("--max-rows", type=int, default=200)
    ap.add_argument("--tables", action="store_true")
    ap.add_argument("--json", metavar="FILE", help="write {columns, rows, truncated} to FILE (for our views)")
    a = ap.parse_args()
    con, views = connect()
    if a.tables or not a.sql:
        if not views:
            print("no datasets in /workspace/cache yet (make one with ingest_log)")
        for ds, t in views:
            cols = con.execute(f'DESCRIBE "{ds}"."{t}"').fetchall()
            n = con.execute(f'SELECT count(*) FROM "{ds}"."{t}"').fetchone()[0]
            print(f"{ds}.{t} ({n:,} rows): " + ", ".join(f"{c[0]} {c[1]}" for c in cols))
        return
    t = time.time()
    cur = con.execute(a.sql)
    if cur.description is None:
        print(f"ok ({time.time() - t:.2f}s)")
        return
    names = [d[0] for d in cur.description]
    rows = cur.fetchmany(a.max_rows + 1)
    if a.json:
        import decimal
        import json

        def cell(v):
            if isinstance(v, float):
                return v if v == v and abs(v) != float("inf") else None  # NaN/inf are not JSON
            if isinstance(v, decimal.Decimal):  # 100.0*sum(...) is a DECIMAL in DuckDB: a number for charts
                return float(v)
            return v if v is None or isinstance(v, (bool, int, str)) else str(v)  # dates, times
        with open(a.json, "w") as f:
            json.dump({"columns": names, "rows": [[cell(v) for v in r] for r in rows[:a.max_rows]],
                       "truncated": len(rows) > a.max_rows}, f, ensure_ascii=False, allow_nan=False, default=str)
        print(f"{min(len(rows), a.max_rows)} rows -> {a.json}")
        return
    print("\t".join(names))
    for r in rows[:a.max_rows]:
        print("\t".join("" if v is None else (f"{v:.6g}" if isinstance(v, float) else str(v)) for v in r))
    more = "; more rows not shown (aggregate, or raise --max-rows)" if len(rows) > a.max_rows else ""
    print(f"({min(len(rows), a.max_rows)} rows{more}; {time.time() - t:.2f}s)", file=sys.stderr)


if __name__ == "__main__":
    main()
