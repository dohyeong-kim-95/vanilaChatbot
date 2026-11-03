"""Loop 31: big data. Files in parts (resumable), runs that link uploads instead of copying them, the project's
cache (/workspace/cache), long jobs in the sandbox (slots, progress, cancel, timeout) and `run_long`.
Deterministic (Docker; the UI part also Chrome and a scripted model):  python tests/test_l24_bigdata.py [test_name ...]"""
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
FAKE_PORT = 8393
os.environ["LLM_BASE_URL"] = f"http://127.0.0.1:{FAKE_PORT}/v1"
import test_l9_artifacts as l9  # noqa: E402
from agent import sandbox as sbx, userstore  # noqa: E402
from agent.loop import Context, run_tool  # noqa: E402
from agent.sandbox import Sandbox, Slots  # noqa: E402
from agent.userstore import Conflict, TooLarge, UserStore  # noqa: E402

TMP = l9.TMP / "l24"


def expect(exc, fn, *args):
    try:
        fn(*args)
    except exc as e:
        return e
    raise AssertionError(f"{fn.__name__}{args[:3]} did not raise {exc.__name__}")


def big(s, path, data, user="alice", project="p", part=None):
    b = s.begin_upload(user, project, path, len(data))
    step = part or userstore.PART_MAX
    for off in range(0, len(data), step):
        s.upload_part(user, project, b["upload_id"], off, data[off:off + step])
    return s.finish_upload(user, project, b["upload_id"])


def t_parts_store():
    s = UserStore(TMP / "store")
    s.create_project("alice", "p")
    data = os.urandom(3 * 1024 * 1024 + 17)
    r = big(s, "로그/장비A.log", data, part=1024 * 1024)
    assert r == {"sha256": hashlib.sha256(data).hexdigest(), "path": "로그/장비A.log"}, r
    assert [f["path"] for f in s.uploads("alice", "p")] == ["로그", "로그/장비A.log"]
    d = s.project_dir("alice", "p") / "uploads"
    assert (d / "blobs" / r["sha256"]).read_bytes() == data and not list((d / "partial").iterdir())
    # parts: only from where it stands (a resent or skipped part is a Conflict that says where), never past the size
    b = s.begin_upload("alice", "p", "b.bin", 10)
    uid = b["upload_id"]
    assert b["received"] == 0 and b["part_max"] == userstore.PART_MAX
    s.upload_part("alice", "p", uid, 0, b"01234")
    e = expect(Conflict, s.upload_part, "alice", "p", uid, 0, b"01234")
    assert e.received == 5
    expect(Conflict, s.upload_part, "alice", "p", uid, 7, b"7")
    expect(ValueError, s.upload_part, "alice", "p", uid, 5, b"567890")  # 11 > 10
    expect(ValueError, s.finish_upload, "alice", "p", uid)  # incomplete
    assert s.upload_status("alice", "p", uid)["received"] == 5
    s.upload_part("alice", "p", uid, 5, b"56789")
    s.finish_upload("alice", "p", uid)
    expect(KeyError, s.upload_status, "alice", "p", uid)  # done: gone
    # the same content again under another name: the existing blob is used, never rewritten
    blob = d / "blobs" / r["sha256"]
    ino = blob.stat().st_ino
    big(s, "copy.log", data)
    assert blob.stat().st_ino == ino and len(list((d / "blobs").iterdir())) == 2
    # checks at the start: name, existing path, a file in the way of a folder, size
    expect(FileExistsError, s.begin_upload, "alice", "p", "b.bin", 1)
    expect(FileExistsError, s.begin_upload, "alice", "p", "b.bin/x", 1)
    expect(ValueError, s.begin_upload, "alice", "p", "../x", 1)
    expect(TooLarge, s.begin_upload, "alice", "p", "huge", userstore.MAX_FILE + 1)
    expect(TooLarge, s.begin_upload, "alice", "p", "neg", -1)
    # ids are ours only
    for bad in ("../../etc/passwd", "x" * 16, "", None):
        expect(KeyError, s.upload_status, "alice", "p", bad)
    # another user's upload id is not found in my project
    s.create_project("bob", "p")
    b = s.begin_upload("bob", "p", "secret.log", 4)
    expect(KeyError, s.upload_part, "alice", "p", b["upload_id"], 0, b"evil")
    # cancel frees it; a part or a finish that comes after it finds nothing (no orphan file, no 500)
    s.cancel_upload("bob", "p", b["upload_id"])
    expect(KeyError, s.upload_status, "bob", "p", b["upload_id"])
    c = s.begin_upload("bob", "p", "late.log", 4)
    meta = s.project_dir("bob", "p") / "uploads" / "partial" / f"{c['upload_id']}.json"
    meta.unlink()  # as a sweep or a cancel between our lookup and the lock would
    expect(KeyError, s.upload_part, "bob", "p", c["upload_id"], 0, b"late")
    assert not (meta.with_suffix(".data")).exists() or (meta.with_suffix(".data")).stat().st_size == 0
    s.sweep_uploads()
    assert c["upload_id"] not in userstore._PART_LOCKS and b["upload_id"] not in userstore._PART_LOCKS
    print("    parts in order, conflicts say where, same content keeps the blob, ids are per project")


def t_parts_limits():
    s = UserStore(TMP / "limits")
    s.create_project("alice", "p")
    old = userstore.MAX_UPLOAD_TOTAL, userstore.DISK_RESERVE, userstore.PART_MAX
    try:
        userstore.MAX_UPLOAD_TOTAL = 100
        s.upload("alice", "p", "small", b"x" * 30)
        a = s.begin_upload("alice", "p", "a", 40)  # reserved while unfinished
        expect(TooLarge, s.begin_upload, "alice", "p", "b", 40)  # 30 + 40 + 40 > 100
        s.cancel_upload("alice", "p", a["upload_id"])
        s.begin_upload("alice", "p", "b", 40)
        # an unfinished upload untouched for a day is dropped (and frees its share)
        part = s.project_dir("alice", "p") / "uploads" / "partial"
        for f in part.iterdir():
            os.utime(f, (time.time() - userstore.PARTIAL_TTL - 10,) * 2)
        s.begin_upload("alice", "p", "c", 60)
        assert len(list(part.glob("*.json"))) == 1
        # not enough disk left
        userstore.MAX_UPLOAD_TOTAL = 10**15
        userstore.DISK_RESERVE = 10**15
        e = expect(TooLarge, s.begin_upload, "alice", "p", "d", 1)
        assert "disk" in str(e)
        userstore.DISK_RESERVE = old[1]
        # a part bigger than the part size
        userstore.PART_MAX = 4
        b = s.begin_upload("alice", "p", "e", 10)
        expect(TooLarge, s.upload_part, "alice", "p", b["upload_id"], 0, b"12345")
        userstore.PART_MAX = old[2]
        # a small upload counts the open big ones too
        userstore.MAX_UPLOAD_TOTAL = 30 + 60 + 10 + 5
        expect(TooLarge, s.upload, "alice", "p", "f", b"x" * 6)
        userstore.MAX_UPLOAD_TOTAL = 10**15
        # the user's projects together (projects are free to make)
        old_u = userstore.USER_QUOTA
        userstore.USER_QUOTA = 200
        s.create_project("alice", "q")
        expect(TooLarge, s.begin_upload, "alice", "q", "g", 120)  # 30 + 60 + 10 + 120 > 200
        s.begin_upload("alice", "q", "g", 90)
        expect(TooLarge, s.upload, "alice", "q", "h", b"x" * 20)
        userstore.USER_QUOTA = old_u
        # the disk: open uploads of everyone count with what they will still write
        real = userstore.shutil.disk_usage
        userstore.shutil.disk_usage = lambda p: type("U", (), {"free": 11 * 2**30})()
        userstore.DISK_RESERVE = 2 * 2**30
        try:
            s.create_project("bob", "p")
            s.begin_upload("alice", "p", "big1", 4 * 2**30)
            s.begin_upload("bob", "p", "big2", 4 * 2**30)  # 11 - 4 owed >= 4 + 2
            e = expect(TooLarge, s.begin_upload, "alice", "q", "big3", 4 * 2**30)  # 11 - 8 < 4 + 2
            assert "disk" in str(e)
        finally:
            userstore.shutil.disk_usage = real
        # expired uploads of another project go too (that project never uploads again)
        stale = s.project_dir("bob", "p") / "uploads" / "partial"
        for f in stale.iterdir():
            os.utime(f, (time.time() - userstore.PARTIAL_TTL - 10,) * 2)
        s.sweep_uploads()
        assert not list(stale.iterdir()), list(stale.iterdir())
    finally:
        userstore.MAX_UPLOAD_TOTAL, userstore.DISK_RESERVE, userstore.PART_MAX = old


def t_checkout_links_and_cache():
    s = UserStore(TMP / "co")
    s.create_project("alice", "p")
    data = b"2026-09-01 TOOL01 PASS\n" * 1000
    r = big(s, "logs/a.log", data)
    blob = s.project_dir("alice", "p") / "uploads" / "blobs" / r["sha256"]
    before = blob.stat()
    ws, _ = s.checkout("alice", "p", TMP / "co-run")
    f = ws / "uploads" / "logs" / "a.log"
    assert f.stat().st_ino == before.st_ino, "copied, not linked"
    assert oct(f.stat().st_mode & 0o777) == "0o444" and blob.stat().st_uid == before.st_uid
    cache = s.cache_dir("alice", "p")
    sb = Sandbox(ws, readonly=("uploads",), rw_mounts={cache: "/workspace/cache"})
    try:
        out = sb.exec("wc -l uploads/logs/a.log; echo x >> uploads/logs/a.log; echo rc=$?; rm -f uploads/logs/a.log;"
                      " echo rc=$?; echo kept > cache/a.parquet; mkdir -p cache/d && echo 1 > cache/d/b; ln -s /etc cache/etc;"
                      " mkdir -p cache/locked/in && echo z > cache/locked/in/f && chmod 000 cache/locked; ls cache")
        assert "1000 uploads/logs/a.log" in out and "rc=0" not in out, out
        assert "a.parquet" in out, out
    finally:
        sb.close()
    assert blob.read_bytes() == data, "a blob changed through a run"
    assert (cache / "a.parquet").read_text() == "kept\n", "the cache is not kept"
    assert s.cache_size("alice", "p") == len("kept\n") + 2, s.cache_size("alice", "p")  # files only; /etc not followed
    # the next run sees it; deleting the upload keeps the running copy (a link) but frees the blob
    ws2, _ = s.checkout("alice", "p", TMP / "co-run2")
    s.delete_upload("alice", "p", "logs")
    assert not blob.exists() and (ws2 / "uploads" / "logs" / "a.log").read_bytes() == data
    s.clear_cache("alice", "p")
    assert not cache.exists() and Path("/etc/passwd").exists()
    print("    uploads linked (same inode, 0444, read-only in the sandbox); cache kept between runs; symlinks not followed")


def t_jobs():
    s = UserStore(TMP / "jobs")
    s.create_project("alice", "p")
    ws, _ = s.checkout("alice", "p", TMP / "jobs-run")
    sb = Sandbox(ws)
    old = sbx.JOBS
    try:
        sbx.JOBS = Slots(1)
        seen = []
        out = sb.job("for i in 1 2 3; do echo PROGRESS $i 3 step$i; echo line$i; sleep 2.1; done; echo done",
                     progress=lambda d, t, n: seen.append((d, t, n)))
        assert out.startswith("exit_code=0") and "line1" in out and "done" in out and "PROGRESS" not in out, out
        assert seen and seen[0] == (1.0, 3.0, "step1") and len(seen) >= 2, seen
        # timeout: the whole group goes (a child that ignores the parent's end too)
        t = time.time()
        out = sb.job("(sleep 300 &); sleep 300", timeout=2)
        assert out.startswith("exit_code=124") and time.time() - t < 15, out
        assert "sleep 300" not in sb.exec("ps -eo args 2>/dev/null || for p in /proc/[0-9]*; do tr '\\0' ' ' < $p/cmdline; echo; done")
        # cancel
        ev = threading.Event()
        threading.Timer(1.5, ev.set).start()
        out = sb.job("sleep 300", cancel=ev)
        assert out.startswith("exit_code=130") and "cancelled" in out, out
        # one slot: the second job waits (and hears it is next), then runs
        order, waits = [], []
        th = threading.Thread(target=lambda: order.append(sb.job("sleep 3; echo first")))
        th.start()
        time.sleep(0.8)
        order.append(sb.job("echo second", waiting=waits.append))
        th.join()
        assert "first" in order[0] and "second" in order[1] and waits == [0], (order, waits)
        # cancelled while waiting: it never runs
        th = threading.Thread(target=lambda: sb.job("sleep 3"))
        th.start()
        time.sleep(0.5)
        ev = threading.Event()
        threading.Timer(1, ev.set).start()
        out = sb.job("echo ran", cancel=ev)
        th.join()
        assert out.startswith("exit_code=130") and "ran" not in out, out
        # a very long line without newline is cut, not held
        out = sb.job("head -c 50000000 /dev/zero | tr '\\0' a; echo; echo end")
        assert out.startswith("exit_code=0") and len(out) < 30000 and "end" in out, len(out)
        # the tool: label and progress as events
        events = []
        r = run_tool(Context(sandbox=sb, emit=events.append), "run_long",
                     json.dumps({"command": "echo PROGRESS 5 10 half; echo ok", "label": "  로그   읽기 ", "timeout_min": 999}))
        assert r.startswith("exit_code=0") and "ok" in r, r
        assert {"type": "job", "label": "로그 읽기", "done": 5.0, "total": 10.0, "note": "half"} in events, events
        assert events[0] == {"type": "job", "label": "로그 읽기", "state": "running"}, events  # it started (no queue)
        # the last report is kept even when it comes right after another (often the 100%)
        seen = []
        sb.job("echo PROGRESS 1 2; echo PROGRESS 2 2", progress=lambda d, t, n: seen.append(d))
        assert seen == [1.0, 2.0], seen
        # a cache over its limit: no new long job
        from agent import tools
        old_c = tools.CACHE_MAX
        tools.CACHE_MAX = 3
        try:
            sb.exec("echo 0123456789 > /workspace/cache/x")
            r = run_tool(Context(sandbox=sb), "run_long", json.dumps({"command": "echo hi", "label": "x"}))
            assert r.startswith("ERROR") and "cache" in r and "hi" not in r, r
        finally:
            tools.CACHE_MAX = old_c
    finally:
        sbx.JOBS = old
        sb.close()
    print("    progress lines, timeout and cancel kill the group, one slot queues, long lines cut, run_long events")


# -------------------------------------------------------------------- API and UI (scripted model)
SCRIPT = [
    {"role": "assistant", "content": None, "tool_calls": [{"id": "j1", "type": "function", "function": {
        "name": "run_long", "arguments": json.dumps({"command": "for i in 1 2 3 4; do echo PROGRESS $i 4; sleep 2.2; done; "
                                                                "wc -c < uploads/big.log > cache/size.txt; cat cache/size.txt",
                                                     "label": "로그 변환"})}}]},
    {"role": "assistant", "content": "변환 끝"}]


def fake_llm():
    import uvicorn
    from fastapi import FastAPI
    app = FastAPI()

    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": [{"id": "fake", "object": "model"}]}

    @app.post("/v1/chat/completions")
    def chat(body: dict):
        msgs = body["messages"]
        last = max(i for i, m in enumerate(msgs) if m["role"] == "user")
        done = sum(m["role"] == "assistant" for m in msgs[last:])
        msg = SCRIPT[done] if msgs[last]["content"] == "변환해 줘" and done < len(SCRIPT) else {"role": "assistant", "content": "no"}
        return {"id": "x", "object": "chat.completion", "created": 0, "model": "fake",
                "choices": [{"index": 0, "finish_reason": "stop", "message": msg}]}
    srv = uvicorn.Server(uvicorn.Config(app, port=FAKE_PORT, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{FAKE_PORT}/v1/models"); break
        except httpx.TransportError:
            time.sleep(0.1)
    return srv


def t_api_and_ui():
    UserStore(l9.DATA).create_project("alice", "big")
    stale = l9.DATA / "work" / "old-run"  # a kept workspace of a finished run, days old: dropped at start
    (stale / "ws").mkdir(parents=True)
    os.utime(stale, (time.time() - 4 * 86400,) * 2)
    srv = fake_llm()
    ps = l9.servers()
    pw, b = l9.browser()
    try:
        a = l9.client("alice")
        assert not stale.exists(), "an old run workspace was kept"
        # API: a part from the wrong place says where to go on
        r = a.post("/projects/big/files/uploads", json={"path": "api.bin", "size": 6}).json()
        uid = r["upload_id"]
        assert a.put(f"/projects/big/files/uploads/{uid}", params={"offset": 0}, content=b"abc").json()["received"] == 3
        c = a.put(f"/projects/big/files/uploads/{uid}", params={"offset": 0}, content=b"abc")
        assert c.status_code == 409 and c.json()["detail"]["received"] == 3, c.text
        assert a.get(f"/projects/big/files/uploads/{uid}").json()["received"] == 3
        a.put(f"/projects/big/files/uploads/{uid}", params={"offset": 3}, content=b"def")
        assert a.post(f"/projects/big/files/uploads/{uid}/finish").json()["sha256"] == hashlib.sha256(b"abcdef").hexdigest()
        bob = l9.client("bob")
        UserStore(l9.DATA).create_project("bob", "big")
        r = a.post("/projects/big/files/uploads", json={"path": "x.bin", "size": 3}).json()
        assert bob.put(f"/projects/big/files/uploads/{r['upload_id']}", params={"offset": 0}, content=b"bob").status_code == 404
        assert a.post("/projects/big/files/uploads", json={"path": "y", "size": 10**13}).status_code == 413
        # UI: a 40 MB file goes up in parts; one part fails on the way (a 502) and the upload goes on
        data = os.urandom(40 * 1024 * 1024)
        f = TMP / "big.log"
        f.write_bytes(data)
        page = b.new_page(viewport={"width": 1300, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        failed = []

        def flaky(route):
            if route.request.method == "PUT" and "offset=16777216" in route.request.url and not failed:
                failed.append(1)
                return route.fulfill(status=502, body="bad gateway")
            route.continue_()
        page.route("**/files/uploads/**", flaky)
        l9.ui_login(page, "alice")
        page.wait_for_function("() => document.querySelector('#project option[value=big]')")
        page.select_option("#project", "big")
        page.wait_for_selector("#files li")
        progress = []
        page.set_input_files("#file-input", str(f))
        for _ in range(600):
            t = page.evaluate("document.querySelector('#files .busy')?.textContent || ''")
            if "%" in t:
                progress.append(t)
            if page.locator("#files li:not(.busy):has-text('big.log')").count():
                break
            time.sleep(0.1)
        page.wait_for_selector("#files li:not(.busy):has-text('big.log')")
        assert failed, "the flaky part was never sent"
        # a reload in the middle of an upload: choosing the same file again goes on where it stood
        g = TMP / "again.log"
        g.write_bytes(os.urandom(48 * 1024 * 1024))
        page.unroute("**/files/uploads/**")
        page.set_input_files("#file-input", str(g))
        page.wait_for_function("() => /again.log [3-9]\\d%/.test(document.querySelector('#files .busy')?.textContent || '')",
                               timeout=30000)
        page.reload()
        page.wait_for_function("() => document.querySelector('#project option[value=big]')")
        page.select_option("#project", "big")
        offsets = []
        page.route("**/files/uploads/**", lambda r: (offsets.append(r.request.url), r.continue_()))
        page.set_input_files("#file-input", str(g))
        page.wait_for_selector("#files li:not(.busy):has-text('again.log')", timeout=30000)
        puts = [u for u in offsets if "offset=" in u]
        assert puts and not puts[0].endswith("offset=0"), puts[:3]
        files = {x["path"]: x for x in a.get("/projects/big/files").json()["files"]}
        assert files["again.log"]["size"] == g.stat().st_size
        assert any("big.log" in p and "MB" in p for p in progress), progress
        files = {x["path"]: x for x in a.get("/projects/big/files").json()["files"]}
        assert files["big.log"]["size"] == len(data), files
        blob = l9.DATA / "users" / "alice" / "projects" / "big" / "uploads" / "blobs" / hashlib.sha256(data).hexdigest()
        assert blob.exists(), "the file arrived damaged"
        # a run: the long job shows its label and progress; it reads the upload and writes the cache
        page.fill("#input", "변환해 줘")
        page.click("#send")
        page.wait_for_function("() => /로그 변환 \\d+%/.test(document.querySelector('.status')?.textContent || '')", timeout=20000)
        page.wait_for_selector("text=변환 끝", timeout=30000)
        size = (l9.DATA / "users" / "alice" / "projects" / "big" / "cache" / "size.txt").read_text().strip()
        assert size == str(len(data)), size
        assert a.get("/projects/big/cache").json()["size"] > 0
        assert a.delete("/projects/big/cache").json() == {"ok": True}
        assert a.get("/projects/big/cache").json()["size"] == 0
        assert not errs, errs
        print(f"    40 MB in parts with a failed part resumed; progress shown ({progress[-1] if progress else ''}); "
              "run_long progress in the status line; cache written and cleared")
    finally:
        b.close(); pw.stop(); l9.kill(ps); srv.should_exit = True


if __name__ == "__main__":
    TMP.mkdir(parents=True, exist_ok=True)
    only, failed = sys.argv[1:], []
    for name, fn in list(globals().items()):
        if name.startswith("t_") and (not only or name in only):
            try:
                t = time.time(); fn(); print(f"PASS {name} ({time.time() - t:.0f}s)")
            except Exception as e:
                failed.append(name)
                print("FAIL", name, repr(e)[:600])
    sys.exit(1 if failed else 0)
