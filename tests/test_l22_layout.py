"""Loop 27: layout. The artifact panel's width (drag the divider, arrow keys, double-click resets; kept per browser and
clamped so the chat keeps its minimum), a full view of the artifact, hiding the sidebar, and reopening a closed
artifact. No LLM; starts the app on :8306 and the renderer on :8307. python tests/test_l22_layout.py [test_name ...]"""
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.artifacts import ArtifactStore  # noqa: E402
from agent.userstore import UserStore  # noqa: E402
from server import auth  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l22-"))
DATA = TMP / "data"
PW = "test-password-1"
APP, RENDER = "http://127.0.0.1:8306", "http://localhost:8307"
PAGE = "<!doctype html><title>t</title><body style='margin:0'><button id=b onclick=\"this.textContent='clicked'\">click me</button></body>"
# handles Escape itself (closes its own dialog) and stops it there
OWN_ESC = ("<!doctype html><body><p id=d>dialog open</p><script>document.addEventListener('keydown', e => {"
           " if (e.key === 'Escape') { document.getElementById('d').textContent = 'dialog closed'; e.stopPropagation(); } });"
           "</script></body>")
REPORT = {"title": "9월 지출", "period": "2026-09", "total": 300, "count": 2,
          "categories": [{"name": "식비", "amount": 200}, {"name": "교통", "amount": 100}],
          "top": [{"date": "2026-09-02", "merchant": "식당", "amount": 200, "category": "식비"}], "notes": ["메모"]}


def start(module, port):
    env = {**os.environ, "AGENT_ROOT": str(DATA), "AGENT_AUTH": "login", "AGENT_APP_ORIGIN": APP,
           "AGENT_RENDER_ORIGIN": RENDER}
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", module, "--port", str(port)], cwd=ROOT, env=env,
                         stdout=subprocess.DEVNULL, stderr=open(TMP / f"{port}.log", "a"))
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{port}/"); return p
        except httpx.TransportError:
            time.sleep(0.2)
    raise RuntimeError(f"{module} did not start")


def setup():
    DATA.mkdir(parents=True)
    db = sqlite3.connect(DATA / "agent.db", isolation_level=None)
    auth.init(db)
    auth.set_password(lambda sql, *a: db.execute(sql, a).fetchall(), "alice", PW)
    db.close()
    s = UserStore(DATA)
    s.create_project("alice", "p")
    arts = ArtifactStore(s.project_dir("alice", "p") / "artifacts")
    arts.save("dash", "html", "대시보드", PAGE)
    arts.save("rep", "report", "리포트", json.dumps(REPORT, ensure_ascii=False))
    arts.save("own", "html", "자체 Esc", OWN_ESC)


def t_layout():
    from playwright.sync_api import sync_playwright
    setup()
    procs = [start("server.app:app", 8306), start("server.render:app", 8307)]
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(channel="chrome", headless=True)
            ctx = br.new_context(viewport={"width": 1400, "height": 800})
            page = ctx.new_page()
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(APP + "/")
            page.fill("#login-user", "alice")
            page.fill("#login-password", PW)
            page.click("#login button")
            page.wait_for_selector("#artifacts li")
            box = lambda sel: page.locator(sel).bounding_box()

            def open_artifact(title="대시보드"):
                page.click(f"#artifacts li:has-text('{title}')")
                page.wait_for_selector("#panel:not([hidden])")
                page.frame_locator("#frame-wrap iframe").locator({"대시보드": "#b", "리포트": "section", "자체 Esc": "#d"}[title]).first.wait_for()

            # closed, then opened again from the sidebar list (and from the chat button after a reload)
            open_artifact()
            page.click("#art-close")
            assert page.locator("#panel").is_hidden()
            open_artifact()
            default = box("#panel")["width"]

            # drag the divider 120px to the left (across the iframe): the panel grows by that much
            r = box("#resizer")
            x, y = r["x"] + r["width"] / 2, r["y"] + 200
            page.mouse.move(x, y)
            page.mouse.down()
            for dx in range(0, 121, 20):
                page.mouse.move(x - dx, y)
            page.mouse.up()
            grown = box("#panel")["width"]
            assert abs(grown - (default + 120)) <= 4, (default, grown)
            # the page in the frame still takes clicks after a drag (the frame's hit testing settles a moment after
            # pointer-events comes back; well under a person's next click)
            page.wait_for_timeout(300)
            page.frame_locator("#frame-wrap iframe").locator("#b").click()
            assert page.frame_locator("#frame-wrap iframe").locator("#b").inner_text() == "clicked"

            # far to the left: the chat keeps its minimum width
            r = box("#resizer")
            page.mouse.move(r["x"] + 3, y)
            page.mouse.down()
            page.mouse.move(5, y, steps=5)
            page.mouse.up()
            fits = lambda w: box("#chat")["width"] >= 359 and box("#panel")["x"] + box("#panel")["width"] <= w + 1
            assert fits(1400), (box("#chat"), box("#panel"))  # and nothing runs off the right edge

            # keys: ← widens, → narrows
            before = box("#panel")["width"]
            page.focus("#resizer")
            page.keyboard.press("ArrowRight")
            page.keyboard.press("ArrowRight")
            assert abs(box("#panel")["width"] - (before - 64)) <= 2, (before, box("#panel"))

            # kept after a reload
            kept = box("#panel")["width"]
            page.reload()
            page.wait_for_selector("#artifacts li")
            page.click("#messages >> text=대시보드", timeout=2000) if page.locator("#messages >> text=대시보드").count() else open_artifact()
            page.wait_for_selector("#panel:not([hidden])")
            assert abs(box("#panel")["width"] - kept) <= 2, (kept, box("#panel"))

            # a narrower window: clamped so the chat keeps its minimum; the saved width is not lost
            page.set_viewport_size({"width": 1000, "height": 800})
            page.wait_for_timeout(200)
            assert fits(1000), (box("#chat"), box("#panel"))
            page.set_viewport_size({"width": 1400, "height": 800})
            page.wait_for_timeout(200)  # the resize event arrives after the viewport changes
            assert abs(box("#panel")["width"] - kept) <= 2, ("the chosen width was lost", kept, box("#panel"))
            # the sidebar hidden and shown again does not change it either
            page.click("#side-toggle")
            page.click("#side-toggle")
            assert abs(box("#panel")["width"] - kept) <= 2, ("lost after the sidebar", kept, box("#panel"))
            # touch drags move the divider instead of scrolling the page
            assert page.eval_on_selector("#resizer", "e => getComputedStyle(e).touchAction") == "none"
            # double-click: back to the default share
            page.dblclick("#resizer")
            assert abs(box("#panel")["width"] - default) <= 2, (default, box("#panel"))

            # full view: covers the window, the page in it keeps its state; Esc returns
            frame = page.frame_locator("#frame-wrap iframe").locator("#b")
            frame.click()
            assert frame.inner_text() == "clicked"
            page.click("#art-expand")
            b = box("#panel")
            assert b["x"] == 0 and b["width"] == 1400 and b["height"] == 800, b
            assert frame.inner_text() == "clicked", "the artifact was reloaded"
            # what it covers is out of reach (Tab, clicks)
            assert page.evaluate("['side', 'chat', 'resizer'].every(id => document.getElementById(id).inert)")
            # Esc after clicking into the artifact (keys then go to the frame, another origin)
            frame.click()
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.getElementById('app').classList.contains('panel-full')", timeout=3000)
            assert not page.evaluate("document.getElementById('chat').inert")
            page.click("#art-expand")
            page.keyboard.press("Escape")
            assert abs(box("#panel")["width"] - default) <= 2 and page.inner_text("#art-expand") == "크게"
            # closing while full leaves the normal layout for the next one
            page.click("#art-expand")
            page.click("#art-close")
            open_artifact()
            assert box("#panel")["x"] > 0
            assert page.get_attribute("#art-expand", "title") == "크게 보기 (Esc로 돌아오기)"

            # Esc from inside the frame also for our report template (no scripts of its own)
            open_artifact("리포트")
            page.click("#art-expand")
            page.frame_locator("#frame-wrap iframe").locator("section").first.click()
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.getElementById('app').classList.contains('panel-full')", timeout=3000)
            # an artifact that handles Escape itself keeps the full view
            open_artifact("자체 Esc")
            page.click("#art-expand")
            own = page.frame_locator("#frame-wrap iframe").locator("#d")
            own.click()
            page.keyboard.press("Escape")
            assert own.inner_text() == "dialog closed"
            page.wait_for_timeout(300)
            assert page.evaluate("document.getElementById('app').classList.contains('panel-full')")
            page.click("#art-expand")
            open_artifact()

            # hide the sidebar: the chat starts at the left edge; kept after a reload; shown again
            page.click("#side-toggle")
            assert page.locator("#side").is_hidden() and box("#chat")["x"] == 0
            page.reload()
            page.wait_for_selector("#app:not([hidden])")
            assert page.locator("#side").is_hidden()
            page.click("#side-toggle")
            page.wait_for_selector("#artifacts li")
            assert page.locator("#side").is_visible() and box("#chat")["x"] > 200

            # another project: the open artifact closes
            UserStore(DATA).create_project("alice", "q")
            page.reload()
            open_artifact()
            page.select_option("#project", "q")
            page.wait_for_function("() => document.getElementById('panel').hidden")
            page.select_option("#project", "p")
            page.wait_for_selector("#artifacts li")

            # narrow (stacked) layout: no divider
            page.set_viewport_size({"width": 800, "height": 900})
            open_artifact()
            assert page.locator("#resizer").is_hidden()
            assert not errs, errs
            br.close()
    finally:
        for p in procs:
            p.send_signal(signal.SIGKILL); p.wait()


if __name__ == "__main__":
    only, failed = sys.argv[1:], []
    for name, fn in list(globals().items()):
        if name.startswith("t_") and (not only or name in only):
            try:
                t = time.time(); fn(); print(f"PASS {name} ({time.time() - t:.0f}s)")
            except Exception as e:
                failed.append(name)
                print("FAIL", name, repr(e)[:600])
    sys.exit(1 if failed else 0)
