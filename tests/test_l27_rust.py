"""Loop 34: the Rust STDF reader (sandbox/stdf-rs) gives the same tables as the Python one (sandbox/vtools/stdf.py),
and ingest uses it. Deterministic (Docker, the sandbox image with stdf2parquet):  python tests/test_l27_rust.py"""
import json
import shutil
import struct
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
from agent.sandbox import VTOOLS, Sandbox  # noqa: E402
from stdf_gen import generate  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l27-"))


def spec_file(path):
    """Hand-built records (as in test_l25_ingest.t_spec_records) plus a retest, a site interleave and a cut end."""
    cn = lambda s: bytes([len(s)]) + s.encode("latin-1")  # noqa: E731
    rec = lambda t, s, b: struct.pack("<HBB", len(b), t, s) + b  # noqa: E731
    far = rec(0, 10, bytes([2, 4]))
    mir = rec(1, 10, struct.pack("<IIB", 1, 2, 3) + b"PNN" + struct.pack("<H", 7) + b"C" + cn("LOTé1") + cn("PT")
              + cn("N") + cn("T") + cn("J"))
    wir = rec(2, 10, struct.pack("<BBI", 1, 255, 0) + cn("W9"))
    pir = lambda s: rec(5, 10, bytes([1, s]))  # noqa: E731
    ptr = lambda tn, s, flg, v, first: rec(15, 10, struct.pack("<IBBBBf", tn, 1, s, flg, 0, v) + (  # noqa: E731
        cn(f"T{tn}") + cn("") + struct.pack("<Bbbbff", 0x10 if tn == 5 else 0, 0, 0, 0, 0.0, 2.0) + cn("V") if first else b""))
    prr = lambda s, flg, x: rec(5, 20, struct.pack("<BBBHHHhhI", 1, s, flg, 2, 1, 1, x, 2, 0) + cn(f"P{x}"))  # noqa: E731
    body = far + mir + wir + pir(0) + pir(1) + ptr(5, 0, 0x02, 9.5, True) + ptr(6, 1, 0x80, 3.0, True) \
        + ptr(6, 0, 0, 1.5, False) + prr(1, 0x08, 1) + prr(0, 0, 2) + pir(0) + prr(0, 0x01, 2) + pir(0) + prr(0, 0x10, 3) \
        + rec(2, 20, b"") + pir(0) + ptr(5, 0, 0, 1.0, False)
    path.write_bytes(body + b"\x10\x00\x05")  # an open part at the end and a cut header


def tables(sb, ds):
    out = {}
    for t, order in (("parts", "file, part"), ("results", "file, part, test_num, result"), ("tests", "test_num")):
        r = sb.exec(f"python /vtools/query.py --max-rows 1000000 --json /workspace/cache/{ds}-{t}.json "
                    f"\"SELECT * FROM {ds}.{t} ORDER BY {order}\"", timeout=300)
        assert r.startswith("exit_code=0"), r
        out[t] = json.loads(sb.read_bytes(f"cache/{ds}-{t}.json", 2**30))
    return out


def t_same_tables():
    gen = [generate(TMP / "le", seed=21, lots=2, wafers=2, radius=8, tests=25),
           generate(TMP / "be", seed=22, lots=1, wafers=2, radius=8, tests=25, endian=">")]
    ws, cache = TMP / "ws", TMP / "cache"
    (ws / "uploads").mkdir(parents=True)
    (ws / "cache").mkdir()
    cache.mkdir()
    files = []
    for d in ("le", "be"):
        for f in sorted((TMP / d).glob("*.stdf")):
            shutil.copy(f, ws / "uploads" / f"{d}-{f.name}")
            files.append(f"uploads/{d}-{f.name}")
    spec_file(ws / "uploads" / "spec.stdf")
    files.append("uploads/spec.stdf")
    sb = Sandbox(ws, readonly=("uploads",), mounts={VTOOLS: "/vtools"}, rw_mounts={cache: "/workspace/cache"})
    try:
        assert "/usr/local/bin/stdf2parquet" in sb.exec("command -v stdf2parquet"), "the image has no stdf2parquet"
        fl = " ".join(files)
        py = sb.exec(f"VTOOLS_STDF=python python /vtools/ingest.py stdf py {fl}", timeout=600)
        rs = sb.exec(f"python /vtools/ingest.py stdf rs {fl}", timeout=600)
        assert "dataset py" in py and "dataset rs" in rs and "(rust)" in rs and "(rust)" not in py, (py, rs)
        assert py.split("dataset py: ")[1].split(" in ")[0] == rs.split("dataset rs: ")[1].split(" in ")[0], (py, rs)
        for note in ("cut short", "without a PRR"):
            assert note in py and note in rs, (note, py, rs)
        a, b = tables(sb, "py"), tables(sb, "rs")
        for t in a:
            assert a[t]["columns"] == b[t]["columns"], (t, a[t]["columns"], b[t]["columns"])
            assert a[t]["rows"] == b[t]["rows"], (t, next((x, y) for x, y in zip(a[t]["rows"], b[t]["rows"]) if x != y))
        print(f"    equal: {len(a['parts']['rows'])} parts, {len(a['results']['rows'])} results, {len(a['tests']['rows'])} tests "
              f"(both byte orders, hand-built records, retest, interleaved sites, cut end)")
        assert sum(g["parts"] for g in gen) + 4 == len(a["parts"]["rows"])  # + the 4 closed parts of spec.stdf
        # a file the agent made itself, with a tab, a quote and a newline in its name, cut short: the summary line
        # (JSON) still reads and the note names it
        out = sb.exec("mkdir -p /tmp/w && head -c -1 uploads/spec.stdf > \"/tmp/w/$(printf 'a\\tb\\\"c\\nd').stdf\" "
                      "&& python /vtools/ingest.py stdf odd /tmp/w/*.stdf", timeout=120)
        assert "dataset odd" in out and "(rust)" in out and "cut short" in out, out
    finally:
        sb.close()


if __name__ == "__main__":
    only, failed = sys.argv[1:], []
    for name, fn in list(globals().items()):
        if name.startswith("t_") and (not only or name in only):
            try:
                t = time.time(); fn(); print(f"PASS {name} ({time.time() - t:.0f}s)")
            except Exception as e:
                failed.append(name)
                print("FAIL", name, repr(e)[:800])
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if failed else 0)
