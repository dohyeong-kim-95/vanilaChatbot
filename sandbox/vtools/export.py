"""Spotfire bundle (Loop 33, docs/research/R7 §4): SBDF files of query results + what is needed to put them into an
analysis. A .dxp cannot be made without Spotfire (no public format), so the bundle carries the data and the steps:

  /workspace/outputs/spotfire/<name>/
    <table>.sbdf      one per table (Spotfire data file, spotfire-python)
    template.dxp      the user's analysis template, when one was given (copied as is)
    job.xml           an Automation Services job outline: open the template, replace each data table, save
    README.md         how to use it (in Korean)

  python /vtools/export.py <name> --table NAME=SQL [--table ...] [--template uploads/x.dxp] [--max-rows N]
"""
import argparse
import html
import os
import re
import shutil
import signal
import sys

sys.path.insert(0, os.path.dirname(__file__))
OUT = "/workspace/outputs"
NAME = re.compile(r"^[A-Za-z0-9가-힣][A-Za-z0-9가-힣_-]{0,60}$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--table", action="append", required=True, help="NAME=SQL")
    ap.add_argument("--template")
    ap.add_argument("--max-rows", type=int, default=1_000_000)
    ap.add_argument("--budget", type=int, default=None, help="bytes the bundle may take (what is left of outputs)")
    a = ap.parse_args()
    if not NAME.match(a.name):
        raise SystemExit("name: letters, digits, _ and -")
    tables = []
    for t in a.table:
        n, _, sql = t.partition("=")
        if not NAME.match(n) or not sql.strip():
            raise SystemExit(f"--table {t[:40]!r}: NAME=SQL with a plain name")
        tables.append((n, sql))
    if len({n for n, _ in tables}) != len(tables):
        raise SystemExit("table names must differ")
    if a.template and not (a.template.lower().endswith(".dxp") and os.path.isfile(a.template)):
        raise SystemExit(f"template: not a .dxp file: {a.template}")
    import spotfire.sbdf as sbdf
    from query import connect
    con, _ = connect()
    out = os.path.join(OUT, "spotfire", a.name)
    tmp = out + ".building"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # a cancel or timeout: clean up below, not just die
    try:
        build(a, tables, tmp, sbdf, con)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)  # never a half bundle in the outputs (they are saved and offered)
        raise
    shutil.rmtree(out, ignore_errors=True)
    os.replace(tmp, out)


def build(a, tables, tmp, sbdf, con):
    import pandas as pd
    lines, used = [], 0
    for n, sql in tables:
        print(f"PROGRESS {len(lines)} {len(tables)} {n}", flush=True)
        sql = sql.strip().rstrip(";")
        df = con.execute(f"SELECT * FROM (\n{sql}\n) LIMIT {a.max_rows + 1}").df()
        if len(df) > a.max_rows:
            raise SystemExit(f"{n}: more than {a.max_rows:,} rows; aggregate first (a Spotfire table this big is slow "
                             "to open, and SBDF is written from memory)")
        for c in df.columns:
            if df[c].isna().all():  # an all-null column has no type for SBDF: give it one
                df[c] = df[c].astype("string")
            elif str(df[c].dtype) in ("uint64", "UInt64"):  # past int64: as a real number
                df[c] = df[c].astype("float64")
            elif pd.api.types.is_integer_dtype(df[c].dtype) and str(df[c].dtype) not in ("int32", "int64", "Int32", "Int64"):
                # SBDF: Integer/LongInteger only (STDF heads, sites, bins, x/y are smaller); NULLs (a LEFT JOIN) need
                # the nullable type
                df[c] = df[c].astype("Int64" if df[c].isna().any() else "int64")
            elif str(df[c].dtype) == "float32":
                df[c] = df[c].astype("float64")
        sbdf.export_data(df, os.path.join(tmp, f"{n}.sbdf"))
        size = os.path.getsize(os.path.join(tmp, f"{n}.sbdf"))
        used += size
        if a.budget is not None and used > a.budget:  # else saving the run's outputs would fail as a whole
            raise SystemExit(f"the bundle passes {a.budget:,} bytes (what is left for outputs) at table {n}: aggregate "
                             "or select fewer columns")
        lines.append((n, len(df), list(df.columns), size))
    if a.template:
        shutil.copyfile(a.template, os.path.join(tmp, "template.dxp"))
        if a.budget is not None and used + os.path.getsize(a.template) > a.budget:
            raise SystemExit(f"the bundle with the template passes {a.budget:,} bytes (what is left for outputs)")
    tpl = "template.dxp" if a.template else "(분석 템플릿 .dxp)"
    steps = "".join(f"""
    <Task name="ReplaceDataTable" table="{html.escape(n)}" source="/Library/vanila/{html.escape(a.name)}/{html.escape(n)}.sbdf"/>"""
                    for n, *_ in lines)
    with open(os.path.join(tmp, "job.xml"), "w", encoding="utf-8") as f:
        f.write(f"""<?xml version="1.0" encoding="utf-8"?>
<!-- Outline of an Automation Services job (Job Builder: Open Analysis from Library -> Replace Data Table -> Save
     Analysis to Library). Element names here are ours: rebuild it in Job Builder with these steps, or adapt it to the
     job file your Spotfire version saves. Paths are Library paths after uploading the files of this folder. -->
<Job name="{html.escape(a.name)}">
  <Tasks>
    <Task name="OpenAnalysisFromLibrary" path="/Library/vanila/{html.escape(a.name)}/{html.escape(tpl)}"/>{steps}
    <Task name="SaveAnalysisToLibrary" path="/Library/vanila/{html.escape(a.name)}/{html.escape(a.name)}.dxp"/>
  </Tasks>
</Job>
""")
    with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as f:
        f.write(f"# Spotfire 번들: {a.name}\n\n| 표 | 행 | 열 | 크기 |\n|---|---|---|---|\n" + "".join(
            f"| `{n}.sbdf` | {rows:,} | {', '.join(map(str, cols))[:200].replace('|', '/')} | {size / 2**20:.1f} MB |\n"
            for n, rows, cols, size in lines) + f"""
## 쓰는 법
1. **Spotfire Analyst에서 바로**: 파일 > 열기로 `.sbdf`를 열면 데이터 표가 됩니다. 기존 분석(`{tpl}`)이 있으면
   데이터 > 데이터 표 바꾸기로 같은 이름의 표를 이 파일로 바꾸고 저장하세요.
2. **서버에서 자동으로(Automation Services)**: 이 폴더의 파일을 Library(예: `/Library/vanila/{a.name}/`)에 올리고,
   `job.xml`의 순서(템플릿 열기 → 표마다 데이터 표 바꾸기 → 분석 저장)대로 Job Builder에서 job을 만들어 실행하세요.

.dxp는 Spotfire 없이는 만들 수 없어서(형식 비공개) 데이터(SBDF)와 순서만 담았습니다. 템플릿은 사용자가 Spotfire에서
한 번 만든 것을 씁니다.
""")
    print(f"PROGRESS {len(tables)} {len(tables)} done", flush=True)
    print(f"spotfire bundle: outputs/spotfire/{a.name}/ -> " + ", ".join(
        f"{n}.sbdf ({rows:,} rows, {size / 2**20:.1f} MB)" for n, rows, _, size in lines)
        + (", template.dxp" if a.template else "") + ", job.xml, README.md")


if __name__ == "__main__":
    main()
