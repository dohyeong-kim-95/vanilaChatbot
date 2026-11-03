"""Loop 33: dashboards over query results, the Spotfire bundle (SBDF + job outline), deliverables to download.
Deterministic (Docker with the sandbox image; the last test also Chrome):  python tests/test_l26_dashboard.py [name ...]"""
import json
import os
import shutil
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "tests"))
from agent import dashboard  # noqa: E402
from agent.artifacts import ArtifactStore  # noqa: E402
from agent.loop import Context, run_tool  # noqa: E402
from agent.sandbox import VTOOLS, Sandbox  # noqa: E402
from server import render  # noqa: E402
from stdf_gen import generate  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l26-"))


def t_render():
    evil = "</script><script>alert(1)</script>"
    d = {"title": "수율 " + evil, "charts": [
        {"title": "bar", "kind": "bar", "x": "w", "y": "y", "columns": ["w", "y"], "rows": [["W01", 92.1], [evil, 50]]},
        {"title": "line", "kind": "line", "x": "t", "y": "v", "series": "s", "columns": ["t", "v", "s"],
         "rows": [["2026-09-01 00:00", 1.0, "A"], ["2026-09-01 01:00", 2.0, "A"], ["2026-09-01 00:00", 1.5, "B"]]},
        {"title": "scatter", "kind": "scatter", "x": "a", "y": "b", "columns": ["a", "b"], "rows": [[1, 2], [3, None]]},
        {"title": "map", "kind": "wafer_map", "x": "x", "y": "y", "value": "hard_bin", "columns": ["x", "y", "hard_bin"],
         "rows": [[0, 0, 1], [1, 0, 2], [0, 1, 1]], "truncated": True},
        {"title": "tbl", "kind": "table", "columns": ["a", evil], "rows": [[evil, 1.23456789]]},
        {"title": "bad", "kind": "bar", "x": "nope", "y": "y", "columns": ["w", "y"], "rows": [["a", 1]]}]}
    d["charts"].append({"title": "many", "kind": "line", "x": "t", "y": "v", "series": "s", "columns": ["t", "v", "s"],
                        "rows": [[i, 1.0, f"S{i}"] for i in range(15)]})
    d["charts"].append({"title": "huge", "kind": "scatter", "x": "a", "y": "b", "columns": ["a", "b"],
                        "rows": [[-1e308, 1.7e308], [1e308, -1.7e308]]})
    page = render.page({"type": "dashboard", "title": "t", "content": json.dumps(d)})
    assert "series 3개는 색이 모자라 뺐어요" in page and page.count("<section") == 8, "too many series must be said"
    d["charts"] = d["charts"][:-2]
    page = render.page({"type": "dashboard", "title": "t", "content": json.dumps(d)})
    assert page.count("<script>") == 1, "only the renderer's prelude may be a script"  # the prelude
    assert "<script>alert" not in page and evil not in page.split('id="data">')[0], "unescaped text"
    data = json.loads(page.split('<script type="application/json" id="data">')[1].split("</script>")[0])
    assert data["charts"][0]["rows"][1][0] == evil, "the data JSON lost or changed a value"
    assert page.count("<section") == 6 and "no column &#x27;nope&#x27;" in page and "일부만 그렸어요" in page
    assert page.count("<rect") >= 2 + 3 and "<polyline" in page and "<circle" in page
    for bad in ([], [{"kind": "pie", "sql": "x"}], [{"kind": "bar", "sql": "x", "x": "a"}], "x"):
        try:
            dashboard.validate_spec("t", bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def box(name):
    d = TMP / name
    truth = generate(d / "data", seed=11, lots=1, wafers=3, radius=8, tests=20)
    ws, cache = d / "ws", d / "cache"
    (ws / "uploads").mkdir(parents=True)
    (ws / "outputs").mkdir()
    (ws / "cache").mkdir()
    cache.mkdir()
    for f in (d / "data").glob("*.stdf"):
        shutil.copy(f, ws / "uploads" / f.name)
    (ws / "uploads" / "my.dxp").write_bytes(b"PK\x03\x04 not really a dxp")
    sb = Sandbox(ws, readonly=("uploads",), mounts={VTOOLS: "/vtools"}, rw_mounts={cache: "/workspace/cache"})
    out = sb.exec(f"python /vtools/ingest.py stdf w uploads/{truth['files'][0]}", timeout=300)
    assert "dataset w" in out, out
    return sb, ws, truth


def t_show_dashboard():
    sb, ws, truth = box("dash")
    store = ArtifactStore(TMP / "arts")
    ctx = Context(sandbox=sb, artifacts=store)
    worst = truth["worst_wafer"].split("/")[1]
    try:
        r = run_tool(ctx, "show_dashboard", json.dumps({"id": "yield", "title": "웨이퍼 수율", "charts": [
            {"title": "웨이퍼별 수율", "kind": "bar", "sql": "SELECT wafer, round(100*avg(passed::int),3) y FROM w.parts GROUP BY 1 ORDER BY 1", "x": "wafer", "y": "y"},
            {"title": f"{worst} 맵", "kind": "wafer_map", "sql": f"SELECT x, y, hard_bin FROM w.parts WHERE wafer='{worst}'",
             "x": "x", "y": "y", "value": "hard_bin"},
            {"title": "fail 많은 test", "kind": "table", "sql": "SELECT test_num, count(*) n FROM w.results WHERE failed GROUP BY 1 ORDER BY 2 DESC LIMIT 5"}]}))
        assert r.startswith("dashboard yield saved as version 1"), r
        assert "(artifact:yield@1#PART)" in r and "c2 = " in r, r  # how the answer cites a chart (Loop 37)
        a = store.get("yield")
        c = json.loads(a["content"])
        ys = {row[0]: row[1] for row in c["charts"][0]["rows"]}
        assert ys == {k.split("/")[1]: v for k, v in truth["wafer_yield"].items()}, (ys, truth["wafer_yield"])
        assert len(c["charts"][1]["rows"]) == sum(1 for x in range(-8, 9) for y in range(-8, 9) if x * x + y * y <= 64)
        assert c["charts"][2]["rows"][0][0] == truth["top_fail_test"]
        page = render.page(a)
        assert page.count("<section") == 3 and "<rect" in page
        # SQL errors and wrong column names go back to the model
        r = run_tool(ctx, "show_dashboard", json.dumps({"id": "x", "title": "t", "charts": [{"kind": "bar", "sql": "SELECT nope FROM w.parts", "x": "a", "y": "b"}]}))
        assert r.startswith("ERROR") and "SQL failed" in r, r
        r = run_tool(ctx, "show_dashboard", json.dumps({"id": "x", "title": "t", "charts": [{"kind": "bar", "sql": "SELECT 1 a, 2 b", "x": "a", "y": "c"}]}))
        assert r.startswith("ERROR") and "not a column" in r, r
        # too many rows: cut, and the model is told
        r = run_tool(ctx, "show_dashboard", json.dumps({"id": "cut", "title": "t", "charts": [{"kind": "bar", "sql": "SELECT part, test_t FROM w.parts", "x": "part", "y": "test_t"}]}))
        assert "(cut: aggregate more)" in r, r
        assert not sb.exec("ls /workspace/cache/.tmp").split("\n")[1:] or "dash-" not in sb.exec("ls /workspace/cache/.tmp")
        # DECIMAL results are numbers; only the drawn columns are kept
        r = run_tool(ctx, "show_dashboard", json.dumps({"id": "dec", "title": "t", "charts": [
            {"kind": "bar", "sql": "SELECT wafer, 100.0*sum(passed::int)/count(*) y, count(*) n, min(lot) l FROM w.parts GROUP BY 1", "x": "wafer", "y": "y"}]}))
        assert r.startswith("dashboard dec"), r
        c = json.loads(store.get("dec")["content"])["charts"][0]
        assert c["columns"] == ["wafer", "y"] and all(isinstance(v, float) for _, v in c["rows"]), c
        assert "<rect" in render.page(store.get("dec"))
        # the template is ours: the artifact tool cannot make or change one
        r = run_tool(ctx, "artifact", json.dumps({"command": "update", "id": "yield", "old_str": "a", "new_str": "b"}))
        assert r.startswith("ERROR") and "show_dashboard" in r, r
    finally:
        sb.close()
    print("    dashboard numbers equal the planted yields; SQL errors, unknown columns and cut results reported")


def t_export_spotfire():
    sb, ws, truth = box("export")
    events = []
    ctx = Context(sandbox=sb, emit=events.append)
    try:
        r = run_tool(ctx, "export_spotfire", json.dumps({"name": "수율_9월", "template": "uploads/my.dxp", "tables": [
            {"name": "wafer_yield", "sql": "SELECT lot, wafer, avg(passed::int) yield, count(*) dies FROM w.parts GROUP BY 1, 2"},
            {"name": "dies", "sql": "SELECT * FROM w.parts"}]}))
        assert r.startswith("exit_code=0") and "spotfire bundle" in r, r
        assert events[0] == {"type": "job", "label": "Spotfire 내보내기: 수율_9월", "state": "running"}, events[:2]
        b = ws / "outputs" / "spotfire" / "수율_9월"
        assert sorted(p.name for p in b.iterdir()) == ["README.md", "dies.sbdf", "job.xml", "template.dxp", "wafer_yield.sbdf"]
        assert (b / "template.dxp").read_bytes() == (ws / "uploads" / "my.dxp").read_bytes()
        job = ET.fromstring((b / "job.xml").read_text())
        assert [t.get("name") for t in job.iter("Task")] == ["OpenAnalysisFromLibrary", "ReplaceDataTable", "ReplaceDataTable",
                                                            "SaveAnalysisToLibrary"]
        # the SBDF files read back with the same numbers (spotfire-python, in the sandbox)
        out = sb.exec("python -c \"import spotfire.sbdf as s; d = s.import_data('outputs/spotfire/수율_9월/dies.sbdf'); "
                      "y = s.import_data('outputs/spotfire/수율_9월/wafer_yield.sbdf'); print(len(d), len(y), sorted(d.columns)[:3])\"")
        assert f"{truth['parts']} 3 ['file', 'hard_bin', 'head']" in out, out
        assert "| `dies.sbdf` |" in (b / "README.md").read_text()
        # refused: a bad name, a template that is not a .dxp, a table too big, a path outside the workspace
        for bad in ({"name": "../x", "tables": [{"name": "a", "sql": "SELECT 1"}]},
                    {"name": "x", "tables": [{"name": "a", "sql": "SELECT 1"}], "template": "uploads/none.dxp"},
                    {"name": "x", "tables": [{"name": "a", "sql": "SELECT 1"}], "template": "../../etc/passwd"},
                    {"name": "x", "tables": [{"name": "a b", "sql": "SELECT 1"}]},
                    {"name": "x", "tables": [{"name": "a", "sql": "SELECT 1"}, {"name": "a", "sql": "SELECT 2"}]}):
            r = run_tool(ctx, "export_spotfire", json.dumps(bad))
            assert r.startswith("ERROR") or not r.startswith("exit_code=0"), (bad, r)
        out = sb.exec("python /vtools/export.py big --table='t=SELECT * FROM w.results' --max-rows 10")
        assert "more than 10 rows" in out and not (ws / "outputs" / "spotfire" / "big").exists(), out
        assert not (ws / "outputs" / "spotfire" / "big.building").exists(), "a half bundle was left in the outputs"
        # NULLs in small/unsigned integer columns (a LEFT JOIN), a trailing ; and a comment in the SQL
        r = run_tool(ctx, "export_spotfire", json.dumps({"name": "nulls", "tables": [{"name": "t", "sql":
            "SELECT p.part, p.head, CASE WHEN p.passed THEN NULL ELSE p.hard_bin END bin, 18446744073709551615::ubigint big "
            "FROM w.parts p -- the parts\n;"}]}))
        assert r.startswith("exit_code=0"), r
        out = sb.exec("python -c \"import spotfire.sbdf as s; d = s.import_data('outputs/spotfire/nulls/t.sbdf'); "
                      "print(len(d), int(d['bin'].isna().sum()) > 0, d['big'][0])\"")
        assert f"{truth['parts']} True 1.8446744073709552e+19" in out, out
        # the bundle must fit in what is left of the outputs (else the whole run's outputs would not be saved)
        from agent import tools
        old_max = tools.MAX_OUTPUTS
        tools.MAX_OUTPUTS = sb.exec("du -sb /workspace/outputs | cut -f1")
        tools.MAX_OUTPUTS = int(tools.MAX_OUTPUTS.split("\n")[-1]) + 20_000
        try:
            r = run_tool(ctx, "export_spotfire", json.dumps({"name": "toobig", "tables": [{"name": "r", "sql": "SELECT * FROM w.results"}]}))
        finally:
            tools.MAX_OUTPUTS = old_max
        assert "what is left for outputs" in r and not list((ws / "outputs" / "spotfire").glob("toobig*")), r
    finally:
        sb.close()
    print("    SBDF per table reads back; template copied; job outline; bad names/templates/sizes refused")


def t_outputs_download():
    import test_l9_artifacts as l9
    from agent.userstore import UserStore
    UserStore(l9.DATA).create_project("alice", "out")
    out = l9.DATA / "users" / "alice" / "projects" / "out" / "outputs"
    (out / "spotfire" / "a").mkdir(parents=True)
    (out / "spotfire" / "a" / "t.sbdf").write_bytes(b"SBDF" * 10)
    (out / "page.html").write_text("<script>alert(1)</script>")
    (out / "link").symlink_to("/etc/passwd")
    (out / "dirlink").symlink_to("/etc")
    ps = l9.servers()
    pw, b = l9.browser()
    try:
        a = l9.client("alice")
        files = a.get("/projects/out/outputs").json()["files"]
        assert files == [{"path": "page.html", "size": 25}, {"path": "spotfire/a/t.sbdf", "size": 40}], files
        r = a.get("/projects/out/outputs/file", params={"path": "page.html"})
        assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream", r.headers
        assert r.headers["content-disposition"].startswith("attachment") and r.headers["x-content-type-options"] == "nosniff"
        for bad in ("link", "dirlink/passwd", "../outputs/page.html", "spotfire", "", "/etc/passwd", "spotfire/../../uploads"):
            assert a.get("/projects/out/outputs/file", params={"path": bad}).status_code == 404, bad
        assert l9.client("bob").get("/projects/out/outputs/file", params={"path": "page.html"}).status_code in (403, 404)
        page = b.new_page()
        l9.ui_login(page, "alice")
        page.wait_for_function("() => document.querySelector('#project option[value=out]')")
        page.select_option("#project", "out")
        page.wait_for_selector("#outputs a:has-text('spotfire/a/t.sbdf')")
        with page.expect_download() as dl:
            page.click("#outputs a:has-text('t.sbdf')")
        assert dl.value.suggested_filename == "t.sbdf"
        assert Path(dl.value.path()).read_bytes() == b"SBDF" * 10
    finally:
        b.close(); pw.stop(); l9.kill(ps)
    print("    outputs listed (no links), always downloaded as attachments, traversal and other users refused; UI link")


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
