"""Loop 45: what write_code built, in the UI. A download button in the chat (live from the loop's event, and after a
reload from the history) and 결과 파일 as folders. Deterministic (no LLM; Chrome via Playwright):
python tests/test_l35_download.py [test_name ...]"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import test_l9_artifacts as l9  # noqa: E402
from agent.userstore import UserStore  # noqa: E402

BUILT = ("BUILT: hesk, 1 attempt(s); every attempt's source and errors.json are under outputs/codegen/maze/\n"
         "- attempt 1: outputs/codegen/maze/attempt-1/main.hk (3 lines): ok, examples 2/2 right\n"
         "final source: outputs/codegen/maze/main.hk\nprogram output on example 1:\n4")


def call(cid, name, args):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def session(a, project, history):
    sid = a.post("/sessions", json={"project_id": project}).json()["session_id"]
    db = sqlite3.connect(l9.DATA / "agent.db")
    db.execute("UPDATE sessions SET history=? WHERE id=?", (json.dumps(history, ensure_ascii=False), sid))
    db.commit()
    db.close()
    return sid


def t_history_and_ui():
    store = UserStore(l9.DATA)
    store.create_project("alice", "dl")
    out = store.project_dir("alice", "dl") / "outputs"
    for rel, text in [("codegen/maze/main.hk", "say 4.\n"), ("codegen/maze/attempt-1/main.hk", "say 4.\n"),
                      ("codegen/maze/attempt-1/errors.json", "[]"), ("report.csv", "a,b\n")]:
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_text(text)
    history = [
        {"role": "system", "content": "s"}, {"role": "user", "content": "미로"},
        {"role": "assistant", "content": None, "tool_calls": [
            call("w1", "write_code", {"language": "hesk", "task": "t", "id": "maze"}),
            call("w2", "write_code", {"language": "hesk", "task": "t", "id": "nope"}),
            call("b1", "bash", {"command": "echo"})]},
        {"role": "tool", "tool_call_id": "w1", "content": BUILT},
        {"role": "tool", "tool_call_id": "w2", "content": "NOT BUILT: hesk, 1 attempt(s)\nstopped: x"},
        # another tool's output that looks like a build: not a button (it is not write_code's)
        {"role": "tool", "tool_call_id": "b1", "content": BUILT.replace("maze", "fake")},
        {"role": "assistant", "content": "만들었습니다"}]
    ps = l9.servers()
    pw, browser = l9.browser()
    try:
        a = l9.client("alice")
        sid = session(a, "dl", history)
        msgs = a.get(f"/sessions/{sid}").json()["messages"]
        assert [(m["role"], m.get("path") or m.get("content")) for m in msgs] == [
            ("user", "미로"), ("download", "codegen/maze/main.hk"), ("assistant", "만들었습니다")], msgs
        page = browser.new_page(accept_downloads=True)
        l9.ui_login(page, "alice")
        page.wait_for_selector("#app:not([hidden])")
        page.click(f"#sessions li >> text={sid}")
        page.wait_for_selector(".download-ref")
        assert page.locator(".download-ref").count() == 1
        with page.expect_download() as d:
            page.click(".download-ref")
        assert d.value.suggested_filename == "main.hk" and Path(d.value.path()).read_text() == "say 4.\n"
        # 결과 파일: folders closed at first, a top-level file as itself; a click opens one level
        rows = lambda: [x.strip() for x in page.locator("#outputs li").all_inner_texts()]  # noqa: E731
        page.wait_for_function("document.querySelectorAll('#outputs li').length === 2")
        assert rows()[0].startswith("▸") and "codegen/" in rows()[0] and "report.csv" in rows()[1], rows()
        page.click("#outputs li.folder >> text=codegen/")
        page.click("#outputs li.folder >> text=maze/")
        got = rows()
        assert len(got) == 5 and "attempt-1/" in got[2] and "main.hk" in got[3] and "report.csv" in got[4], got
        page.click("#outputs li.folder >> text=attempt-1/")
        assert len(rows()) == 7 and "errors.json" in rows()[3], rows()
        page.click("#outputs li.folder >> text=codegen/")  # closing a folder hides everything under it
        assert len(rows()) == 2, rows()
        with page.expect_download() as d:
            page.click("#outputs a >> text=report.csv")
        assert d.value.suggested_filename == "report.csv"
    finally:
        browser.close()
        pw.stop()
        l9.kill(ps)


if __name__ == "__main__":
    import time
    only, failed = sys.argv[1:], []
    for name, fn in list(globals().items()):
        if name.startswith("t_") and (not only or name in only):
            try:
                t = time.time(); fn(); print(f"PASS {name} ({time.time() - t:.0f}s)")
            except Exception as e:
                failed.append(name)
                print("FAIL", name, repr(e)[:1500])
    sys.exit(1 if failed else 0)
