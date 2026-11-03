"""Loop 32: big logs -> Parquet -> SQL in the sandbox image. The STDF reader against generated data with a known
answer (both byte orders, a cut-off file), the cache reuse, text logs with a regex, and the two tools.
Deterministic (Docker, the sandbox image of agent/sandbox.py):  python tests/test_l25_ingest.py [test_name ...]
The model's part is the benchmark: python bench/t1.py"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
from agent.loop import Context, run_tool  # noqa: E402
from agent.sandbox import VTOOLS, Sandbox  # noqa: E402
from stdf_gen import generate  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l25-"))


def box(name, files=None):
    """A workspace with files under uploads/ (read-only) and an empty cache, like a run."""
    ws, cache = TMP / name / "ws", TMP / name / "cache"
    (ws / "uploads").mkdir(parents=True)
    (ws / "cache").mkdir()
    cache.mkdir()
    for src in files or ():
        shutil.copy(src, ws / "uploads" / Path(src).name)
    return Sandbox(ws, readonly=("uploads",), mounts={VTOOLS: "/vtools"}, rw_mounts={cache: "/workspace/cache"}), cache


def q(sb, sql):
    out = sb.exec(f"python /vtools/query.py {json.dumps(sql)}")
    assert out.startswith("exit_code=0"), out
    lines = out.split("\n[stderr]\n")[0].split("\n")[2:]  # exit_code, header; the row count goes to stderr
    return [ln.split("\t") for ln in lines if ln]


def t_stdf_both_byte_orders():
    for endian in ("<", ">"):
        d = TMP / f"gen{endian == '>'}"
        truth = generate(d, seed=3, lots=2, wafers=3, radius=8, tests=30, endian=endian)
        sb, _ = box(f"stdf{endian == '>'}", sorted(d.glob("*.stdf")))
        try:
            files = " ".join(f"uploads/{f}" for f in truth["files"])
            out = sb.exec(f"python /vtools/ingest.py stdf lots {files}", timeout=300)
            assert "dataset lots" in out and f'"parts": {truth["parts"]}' in out, out
            ly = {r[0]: float(r[1]) for r in q(sb, "SELECT lot, round(100*avg(passed::int), 3) FROM lots.parts GROUP BY 1")}
            assert ly == truth["lot_yield"], (ly, truth["lot_yield"])
            wy = {r[0]: float(r[1]) for r in q(sb, "SELECT lot||'/'||wafer, round(100*avg(passed::int), 3) FROM lots.parts GROUP BY 1")}
            assert wy == truth["wafer_yield"], (wy, truth["wafer_yield"])
            top = q(sb, "SELECT test_num, count(*) n FROM lots.results WHERE failed GROUP BY 1 ORDER BY n DESC, 1 LIMIT 1")
            assert int(top[0][0]) == truth["top_fail_test"], (top, truth["fail_by_test"])
            # results join to parts on (file, part); the failing test of a failed part is its soft bin
            bad = q(sb, "SELECT count(*) FROM lots.parts p JOIN lots.results r USING (file, part) "
                        "WHERE r.failed AND (p.passed OR p.soft_bin <> r.test_num)")
            assert bad == [["0"]], bad
            t = q(sb, "SELECT count(*), min(name), count(lo), count(hi) FROM lots.tests")
            assert t[0][0] == "30" and t[0][1].startswith("T000_") and t[0][2] == t[0][3] == "30", t
        finally:
            sb.close()
    print("    little- and big-endian files give the planted yields, worst wafer and top failing test")


def t_spec_records():
    """Records built by hand from the V4 spec (not with our writer, which could share a misreading): MIR fields,
    PRR flags (invalid pass/fail, retest), PTR TEST_FLG (result not valid) and OPT_FLAG (limit not valid)."""
    import struct
    cn = lambda s: bytes([len(s)]) + s.encode()  # noqa: E731
    rec = lambda t, s, b: struct.pack("<HBB", len(b), t, s) + b  # noqa: E731
    far = rec(0, 10, bytes([2, 4]))
    # MIR: SETUP_T U4, START_T U4, STAT_NUM U1, MODE_COD C1, RTST_COD C1, PROT_COD C1, BURN_TIM U2, CMOD_COD C1,
    #      LOT_ID Cn, PART_TYP Cn, NODE_NAM Cn, TSTR_TYP Cn, JOB_NAM Cn
    mir = rec(1, 10, struct.pack("<IIB", 1, 2, 3) + b"PNN" + struct.pack("<H", 7) + b"C" + cn("LOT42") + cn("PT")
              + cn("N") + cn("T") + cn("J"))
    wir = rec(2, 10, struct.pack("<BBI", 1, 255, 0) + cn("W9"))
    pir = rec(5, 10, bytes([1, 0]))
    # PTR: TEST_NUM U4, HEAD U1, SITE U1, TEST_FLG B1, PARM_FLG B1, RESULT R4, TEST_TXT Cn, ALARM_ID Cn, OPT_FLAG B1,
    #      RES_SCAL I1, LLM_SCAL I1, HLM_SCAL I1, LO_LIMIT R4, HI_LIMIT R4, UNITS Cn
    ptr1 = rec(15, 10, struct.pack("<IBBBBf", 5, 1, 0, 0x02, 0, 9.5) + cn("VDD") + cn("") + struct.pack("<Bbbbff", 0x10, 0, 0, 0, 0.0, 2.0) + cn("V"))
    ptr2 = rec(15, 10, struct.pack("<IBBBBf", 6, 1, 0, 0x80, 0, 3.0) + cn("IDD") + cn("") + struct.pack("<Bbbbff", 0x00, 0, 0, 0, 1.0, 2.0) + cn("A"))
    # PRR: HEAD U1, SITE U1, PART_FLG B1, NUM_TEST U2, HARD_BIN U2, SOFT_BIN U2, X I2, Y I2, TEST_T U4, PART_ID Cn
    prr = lambda flg, x: rec(5, 20, struct.pack("<BBBHHHhhI", 1, 0, flg, 2, 1, 1, x, 2, 0) + cn(f"P{x}"))  # noqa: E731
    f = TMP / "spec.stdf"
    f.write_bytes(far + mir + wir + pir + ptr1 + ptr2 + prr(0x08, 1) + pir + prr(0x01, 1) + pir + prr(0x10, 3))
    sb, _ = box("spec", [f])
    try:
        out = sb.exec("python /vtools/ingest.py stdf spec uploads/spec.stdf", timeout=120)
        assert '"parts": 3' in out, out
        parts = q(sb, "SELECT lot, wafer, x, passed, part_flg FROM spec.parts ORDER BY part")
        assert parts == [["LOT42", "W9", "1", "False", "8"], ["LOT42", "W9", "1", "True", "1"], ["LOT42", "W9", "3", "", "16"]], parts
        res = q(sb, "SELECT test_num, result, failed, test_flg FROM spec.results ORDER BY test_num")
        assert res == [["5", "", "False", "2"], ["6", "3", "True", "128"]], res
        tests = q(sb, "SELECT test_num, name, lo, hi FROM spec.tests ORDER BY 1")
        assert tests == [["5", "VDD", "", "2"], ["6", "IDD", "1", "2"]], tests
    finally:
        sb.close()


def t_cut_file_and_reuse():
    d = TMP / "gencut"
    truth = generate(d, seed=4, lots=1, wafers=2, radius=8, tests=20)
    f = d / truth["files"][0]
    data = f.read_bytes()
    f.write_bytes(data[:-7])  # the last record (MRR) cut short
    sb, cache = box("cut", [f])
    try:
        out = sb.exec(f"python /vtools/ingest.py stdf cut uploads/{f.name}", timeout=300)
        assert out.startswith("exit_code=0") and "cut short" in out and f'"parts": {truth["parts"]}' in out, out
        # the same file again: reused, not rebuilt
        out = sb.exec(f"python /vtools/ingest.py stdf cut uploads/{f.name}", timeout=300)
        assert "already in the cache" in out, out
        built = json.loads((cache / "cut" / "meta.json").read_text())["built"]
        # a file cut inside the parts: the parts before the cut are kept, the open one is noted
        g = d / "half.stdf"
        g.write_bytes(data[:len(data) // 2])
        shutil.copy(g, TMP / "cut" / "ws" / "uploads" / "half.stdf")
        out = sb.exec("python /vtools/ingest.py stdf half uploads/half.stdf", timeout=300)
        assert out.startswith("exit_code=0") and "cut short" in out, out
        n = int(q(sb, "SELECT count(*) FROM half.parts")[0][0])
        assert 0 < n < truth["parts"], n
        assert json.loads((cache / "cut" / "meta.json").read_text())["built"] == built
        # not a dataset name, not a file: refused before anything is written
        out = sb.exec("python /vtools/ingest.py stdf ../x uploads/half.stdf; python /vtools/ingest.py stdf ok uploads/none")
        assert "dataset:" in out and "no such file" in out and not (cache / "ok").exists(), out
    finally:
        sb.close()


def t_text_log():
    lines = []
    for i in range(20000):
        lines.append(f"2026-09-01T00:{i // 600 % 60:02d}:{i // 10 % 60:02d}.{i % 1000:03d} TOOL{i % 3:02d} "
                     f"RESULT={'FAIL' if i % 50 == 0 else 'PASS'} VAL={1 + (i % 7) / 100:.2f}")
    lines.insert(500, "garbage line without the shape")
    f = TMP / "tool.log"
    f.write_text("\n".join(lines) + "\n")
    sb, _ = box("text", [f])
    try:
        pat = r"^(?P<ts>\S+) (?P<tool>TOOL\d+) RESULT=(?P<result>\w+) VAL=(?P<val>\S+)"
        out = sb.exec(f"python /vtools/ingest.py text tools uploads/tool.log --pattern {json.dumps(pat)} "
                      "--time ts --time-format '%Y-%m-%dT%H:%M:%S%.3f'", timeout=300)
        assert '"rows": 20000' in out and '"unmatched_lines": 1' in out, out
        r = q(sb, "SELECT tool, count(*) FILTER (WHERE result='FAIL'), typeof(min(ts)) FROM tools.rows "
                  "WHERE tool IS NOT NULL GROUP BY 1 ORDER BY 1")
        assert r == [["TOOL00", "134", "TIMESTAMP"], ["TOOL01", "133", "TIMESTAMP"], ["TOOL02", "133", "TIMESTAMP"]], r
        out = sb.exec("python /vtools/ingest.py text t2 uploads/tool.log --pattern '(\\S+) (\\S+)'")
        assert "named groups" in out, out
        # the same files, now with a time column: built again (the options are part of the cache key)
        out = sb.exec(f"python /vtools/ingest.py text tools uploads/tool.log --pattern={json.dumps(pat)}", timeout=300)
        assert "already in the cache" not in out and '"rows": 20000' in out, out
        assert q(sb, "SELECT DISTINCT typeof(ts) FROM tools.rows") == [["VARCHAR"]]
        # one build of a dataset at a time
        out = sb.exec("python -c \"import fcntl, time; f = open('/workspace/cache/.tools.lock', 'w'); "
                      "fcntl.flock(f, fcntl.LOCK_EX); time.sleep(4)\" & sleep 1; "
                      f"python /vtools/ingest.py text tools uploads/tool.log --pattern={json.dumps(pat)} --time ts; wait")
        assert "being built by another job" in out, out
        # a half-built dataset is not a schema
        sb.exec("mkdir -p /workspace/cache/x.building && echo {} > /workspace/cache/x.building/meta.json")
        assert "x.building" not in sb.exec("python /vtools/query.py --tables")
    finally:
        sb.close()


def t_tools():
    d = TMP / "gentools"
    truth = generate(d, seed=5, lots=1, wafers=2, radius=8, tests=20)
    sb, _ = box("tools", sorted(d.glob("*.stdf")))
    events = []
    ctx = Context(sandbox=sb, emit=events.append)
    try:
        r = run_tool(ctx, "ingest_log", json.dumps({"paths": [f"uploads/{truth['files'][0]}"], "format": "stdf",
                                                     "dataset": "w"}))
        assert r.startswith("exit_code=0") and "dataset w" in r, r
        assert any(e["type"] == "job" and e.get("total") for e in events), events
        assert events[0]["label"] == "로그 변환: w"
        r = run_tool(ctx, "log_query", json.dumps({"sql": ""}))
        assert "w.parts (" in r and "w.results (" in r, r
        r = run_tool(ctx, "log_query", json.dumps({"sql": "SELECT part FROM w.parts ORDER BY part", "max_rows": 5}))
        rows = r.split("\n[stderr]\n")[0].split("\n")[2:]
        assert [x for x in rows if x] == ["0", "1", "2", "3", "4"] and "more rows not shown" in r, r
        for bad in ({"paths": ["../../etc/passwd"], "format": "stdf", "dataset": "x"},
                    {"paths": ["uploads/a"], "format": "csv", "dataset": "x"},
                    {"paths": ["uploads/a"], "format": "stdf", "dataset": "X; rm -rf /"},
                    {"paths": [], "format": "stdf", "dataset": "x"}):
            r = run_tool(ctx, "ingest_log", json.dumps(bad))
            assert r.startswith("ERROR"), (bad, r)
        # quoting: a pattern with quotes and $ reaches the script as written
        r = run_tool(ctx, "ingest_log", json.dumps({"paths": [f"uploads/{truth['files'][0]}"], "format": "text",
                                                     "dataset": "q", "pattern": "(?P<a>'\"$(touch /tmp/pwn)\")"}))
        assert sb.exec("test -e /tmp/pwn && echo PWNED || echo clean").endswith("clean"), r
    finally:
        sb.close()
    print("    ingest_log progress events and label, log_query listing and row cap, bad arguments refused, no shell injection")


if __name__ == "__main__":
    only, failed = sys.argv[1:], []
    for name, fn in list(globals().items()):
        if name.startswith("t_") and (not only or name in only):
            try:
                t = time.time(); fn(); print(f"PASS {name} ({time.time() - t:.0f}s)")
            except Exception as e:
                failed.append(name)
                print("FAIL", name, repr(e)[:600])
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if failed else 0)
