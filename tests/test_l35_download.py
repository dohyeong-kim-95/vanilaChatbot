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


def until(page, js, timeout=10):
    """wait_for_function polls with eval, which the app's CSP refuses: poll from here."""
    import time
    end = time.time() + timeout
    while not page.evaluate(f"() => {js}"):
        assert time.time() < end, js
        time.sleep(0.1)


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
                      ("codegen/maze/attempt-1/errors.json", "[]"), ("report.csv", "a,b\n"),
                      ("extra/bins.hk", "say 1.\n"), ("extra/hi.hk", "say 2.\n")]:
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_text(text)
    history = [
        {"role": "system", "content": "s"}, {"role": "user", "content": "미로"},
        {"role": "assistant", "content": None, "tool_calls": [
            call("w1", "write_code", {"language": "hesk", "task": "t", "id": "maze"}),
            call("w2", "write_code", {"language": "hesk", "task": "t", "id": "nope"}),
            call("w3", "write_code", {"language": "hesk", "task": "t", "id": "bins", "role": "binning"}),
            call("w4", "write_code", {"language": "hesk", "task": "t", "id": "hi"}),
            call("b1", "bash", {"command": "echo"})]},
        {"role": "tool", "tool_call_id": "w1", "content": BUILT},
        {"role": "tool", "tool_call_id": "w2", "content": "NOT BUILT: hesk, 1 attempt(s)\nstopped: x"},
        {"role": "tool", "tool_call_id": "w3", "content": (
            "BUILT: hesk, 2 attempt(s); every attempt's source and errors.json are under outputs/codegen/bins/\n"
            "- attempt 1: outputs/codegen/bins/attempt-1/main.hk (9 lines): 1 error(s), examples 0/2 right\n"
            "    [test] example 1: wrong\n"
            "- attempt 2: outputs/codegen/bins/attempt-2/main.hk (9 lines): ok, examples 2/2 right, hidden 480/500 right\n"
            "final source: outputs/extra/bins.hk")},
        {"role": "tool", "tool_call_id": "w4", "content": (
            "BUILT: hesk, 1 attempt(s); every attempt's source and errors.json are under outputs/codegen/hi/\n"
            "- attempt 1: outputs/codegen/hi/attempt-1/main.hk (1 lines): ok\n"
            "final source: outputs/extra/hi.hk\nprogram output:\nhi")},
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
            ("user", "미로"), ("download", "codegen/maze/main.hk"), ("download", "extra/bins.hk"),
            ("download", "extra/hi.hk"), ("assistant", "만들었습니다")], msgs
        # what was checked, the same shape as the live event (codegen._attempts): hidden as [wrong, total]
        assert [(m["attempts"], m["score"], m["ran"]) for m in msgs if m["role"] == "download"] == [
            (1, {"public": [2, 2]}, False), (2, {"public": [2, 2], "hidden": [20, 500]}, False), (1, {}, True)], msgs
        context = browser.new_context(accept_downloads=True)
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=l9.APP)
        page = context.new_page()
        l9.ui_login(page, "alice")
        page.wait_for_selector("#app:not([hidden])")
        page.click(f"#sessions li >> text={sid}")
        page.wait_for_selector(".download-ref")
        assert page.locator(".download-ref").count() == 3
        with page.expect_download() as d:  # the card's download icon: at once
            page.locator(".download-ref").first.click()
        assert d.value.suggested_filename == "main.hk" and Path(d.value.path()).read_text() == "say 4.\n"
        # the card itself: the code beside the chat, numbered, what was checked above it, copy and download there
        page.locator(".file-open").first.click()
        page.wait_for_selector("#file-code:not([hidden]) .line")
        assert page.inner_text("#art-title") == "main.hk" and page.is_hidden("#frame-wrap") and page.is_hidden("#art-version")
        assert page.inner_text("#file-note") == "1번째 시도에 빌드 · 예시 2/2 통과", page.inner_text("#file-note")
        assert page.locator("#file-code .line").all_inner_texts() == ["say 4."]
        page.click("#file-copy")
        assert page.evaluate("() => navigator.clipboard.readText()") == "say 4.\n"
        assert page.get_attribute("#file-copy", "title") == "복사됨"
        with page.expect_download() as d:
            page.click("#file-get")
        assert d.value.suggested_filename == "main.hk"
        page.locator(".file-open").nth(1).click()
        until(page, "document.querySelector('#file-note').textContent.includes('숨긴')")
        assert page.inner_text("#file-note") == "2번째 시도에 빌드 · 예시 2/2 통과 · 숨긴 검사 480/500 통과"
        page.locator(".file-open").nth(2).click()
        until(page, "document.querySelector('#file-note').textContent.includes('입력 없이')")
        page.click("#art-close")
        assert page.is_hidden("#panel")
        # 결과 파일: folders closed at first, a top-level file as itself; a click opens one level
        rows = lambda: [x.strip() for x in page.locator("#outputs li").all_inner_texts()]  # noqa: E731
        until(page, "document.querySelectorAll('#outputs li').length === 3")
        assert rows()[0].startswith("▸") and "codegen/" in rows()[0] and "extra/" in rows()[1] and "report.csv" in rows()[2], rows()
        page.click("#outputs li.folder >> text=codegen/")
        page.click("#outputs li.folder >> text=maze/")
        got = rows()
        assert len(got) == 6 and "attempt-1/" in got[2] and "main.hk" in got[3] and "report.csv" in got[5], got
        page.click("#outputs li.folder >> text=attempt-1/")
        assert len(rows()) == 8 and "errors.json" in rows()[3], rows()
        page.click("#outputs li.folder >> text=codegen/")  # closing a folder hides everything under it
        assert len(rows()) == 3, rows()
        with page.expect_download() as d:
            page.click("#outputs a >> text=report.csv")
        assert d.value.suggested_filename == "report.csv"
        # review 45.1: the keyboard keeps its place, another project starts closed, no download while a run is on
        page.locator("#outputs li.folder").first.focus()  # the row, not its text span
        page.keyboard.press("Enter")  # opens codegen/ again: maze/ and attempt-1/ were left open
        assert page.evaluate("document.activeElement.dataset.path") == "codegen" and len(rows()) == 8, rows()
        store.create_project("alice", "dl2")
        (store.project_dir("alice", "dl2") / "outputs" / "codegen").mkdir(parents=True)
        (store.project_dir("alice", "dl2") / "outputs" / "codegen" / "x.txt").write_text("x")
        page.reload()
        page.wait_for_selector("#app:not([hidden])")
        page.select_option("#project", "dl2")
        until(page, "document.querySelector('#outputs li')?.textContent.includes('codegen/')")
        page.select_option("#project", "dl")
        until(page, "document.querySelectorAll('#outputs li').length === 3")
        page.click("#outputs li.folder >> text=codegen/")
        page.select_option("#project", "dl2")
        until(page, "document.querySelector('#outputs li')?.textContent.includes('codegen/')")
        assert len(rows()) == 1 and rows()[0].startswith("▸"), rows()  # dl2: codegen/ closed
        page.select_option("#project", "dl")
        page.click(f"#sessions li >> text={sid}")
        page.wait_for_selector(".download-ref")
        page.evaluate("state.streaming = true")
        page.click(".download-ref")
        page.wait_for_selector(".msg.error >> text=작업이 끝나면", timeout=5000)
        page.evaluate("state.streaming = false")
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
