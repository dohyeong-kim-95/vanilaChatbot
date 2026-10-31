"""Loop 24: login. Cookie sessions instead of a trusted X-User, password storage, lockout, logout and password
change revoking logins, the same-origin check, cookie flags, the account CLI. No LLM; starts its own servers.
python tests/test_l18_auth.py [test_name ...]"""
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
from server import auth  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l18-"))
PW = "test-password-1"


def server(port, data, **env):
    data.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.app:app", "--port", str(port)], cwd=ROOT,
                         env={**os.environ, "AGENT_ROOT": str(data), "AGENT_APP_ORIGIN": f"http://127.0.0.1:{port}",
                              "AGENT_AUTH": "login", **env},
                         stdout=subprocess.DEVNULL, stderr=open(TMP / f"{port}.log", "a"))
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{port}/docs"); return p
        except httpx.TransportError:
            time.sleep(0.2)
    raise RuntimeError("server did not start")


def kill(p):
    p.send_signal(signal.SIGKILL); p.wait()


def accounts(data, *users):
    data.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(data / "agent.db", isolation_level=None)
    auth.init(db)
    for u in users:
        auth.set_password(lambda sql, *a: db.execute(sql, a).fetchall(), u, PW)
    db.close()


def login(url, user, pw=PW):
    c = httpx.Client(base_url=url, timeout=30)
    r = c.post("/login", json={"user": user, "password": pw})
    return c, r


DATA = TMP / "main"
URL = "http://127.0.0.1:8302"
SRV = None


def main_server():
    global SRV
    if SRV is None:
        DATA.mkdir(parents=True, exist_ok=True)
        accounts(DATA, "alice", "bob", "carol", "dave", "erin", "gus", "hal", "ivy", "jon", "kit")
        SRV = server(8302, DATA)
    return SRV


def t_requires_login():
    main_server()
    anon = httpx.Client(base_url=URL)
    for path in ("/me", "/projects", "/memory"):
        assert anon.get(path).status_code == 401, path
    # the old stand-in identity means nothing now
    assert anon.get("/projects", headers={"X-User": "alice"}).status_code == 401
    assert anon.post("/projects", json={"project_id": "x"}, headers={"X-User": "alice"}).status_code == 401
    assert anon.get("/").status_code == 200  # the page itself (it shows the login form)


def t_login_and_isolation():
    main_server()
    a, r = login(URL, "alice")
    assert r.status_code == 200 and r.json() == {"user": "alice"}, r.text
    assert a.get("/me").json() == {"user": "alice"}
    assert a.post("/projects", json={"project_id": "secret"}).status_code == 200
    b, _ = login(URL, "bob")
    assert b.get("/projects").json()["projects"] == []
    assert b.get("/projects/secret/sessions").status_code == 404
    # identity comes from the cookie only: a header naming someone else changes nothing
    assert b.get("/projects", headers={"X-User": "alice"}).json()["projects"] == []


def t_failures_look_alike_and_lock():
    main_server()
    _, wrong = login(URL, "carol", "wrong-password")
    _, unknown = login(URL, "nobody", "wrong-password")
    assert wrong.status_code == unknown.status_code == 401 and wrong.json() == unknown.json()
    for _ in range(auth.MAX_FAILS - 1):
        login(URL, "carol", "wrong-password")
    _, locked = login(URL, "carol")  # right password, but the name is locked now
    assert locked.status_code == 401 and locked.json() == wrong.json(), locked.text
    _, other = login(URL, "dave")
    assert other.status_code == 200, "a lock on one name must not affect others"


def t_cookie_flags_and_stored_hash():
    main_server()
    _, r = login(URL, "dave")
    sc = r.headers["set-cookie"].lower()
    for flag in ("httponly", "samesite=strict", "path=/"):
        assert flag in sc, sc
    assert "secure" not in sc  # http origin: a Secure cookie would never come back
    token = r.cookies[auth.COOKIE]
    raw = b"".join(f.read_bytes() for f in DATA.glob("agent.db*"))
    assert token.encode() not in raw, "the login token itself is stored"
    assert PW.encode() not in raw


def t_logout_revokes_token():
    main_server()
    c, r = login(URL, "erin")
    token = r.cookies[auth.COOKIE]
    assert c.post("/logout").status_code == 200
    replay = httpx.Client(base_url=URL, cookies={auth.COOKIE: token})
    assert replay.get("/me").status_code == 401, "a logged-out token still works"


def t_password_change_ends_other_logins():
    main_server()
    c1, _ = login(URL, "bob")
    c2, _ = login(URL, "bob")
    assert c1.post("/password", json={"old": "wrong-password", "new": "another-pass-2"}).status_code == 403
    assert c1.post("/password", json={"old": PW, "new": "short"}).status_code == 422
    assert c2.get("/me").status_code == 200, "a failed change must not sign anyone out"
    r = c1.post("/password", json={"old": PW, "new": "another-pass-2"})
    assert r.status_code == 200, r.text
    assert c1.get("/me").json() == {"user": "bob"}, "the changer keeps a working login"
    assert c2.get("/me").status_code == 401, "other logins survive a password change"
    assert login(URL, "bob")[1].status_code == 401
    assert login(URL, "bob", "another-pass-2")[1].status_code == 200


def t_cross_origin_writes_refused():
    main_server()
    a, _ = login(URL, "alice")
    r = a.post("/projects", json={"project_id": "x1"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403, r.text
    r = a.post("/projects", json={"project_id": "x1"}, headers={"Origin": "http://localhost:8301"})  # sibling site
    assert r.status_code == 403, r.text
    assert a.post("/projects", json={"project_id": "x1"}, headers={"Origin": URL}).status_code == 200
    assert login(URL, "dave")[0].post("/logout", headers={"Origin": "http://evil.example"}).status_code == 403


def t_cli_list_and_remove():
    main_server()
    env = {**os.environ, "AGENT_ROOT": str(DATA)}
    cli = lambda *a: subprocess.run([sys.executable, "-m", "server.auth", *a], cwd=ROOT, env=env,
                                    capture_output=True, text=True)
    c, _ = login(URL, "dave")
    assert "dave" in cli("list").stdout.split()
    assert cli("remove", "dave").stdout.strip() == "ok"
    assert c.get("/me").status_code == 401, "a removed user's login still works"
    assert "dave" not in cli("list").stdout.split()
    assert cli("remove", "dave").stdout.strip() == "no such user"
    r = cli("add", "alice")  # exits before asking for a password
    assert r.returncode != 0 and "already exists" in r.stderr, r.stderr
    r = cli("passwd", "dave")
    assert r.returncode != 0 and "no such user" in r.stderr, r.stderr
    try:
        auth.set_password(lambda *a: [], "Bad Name", PW)
        raise AssertionError("invalid user id accepted")
    except ValueError:
        pass


def t_https_origin_sets_secure_cookie():
    data = TMP / "https"
    accounts(data, "alice")
    p = server(8303, data, AGENT_APP_ORIGIN="https://chat.example")
    try:
        _, r = login("http://127.0.0.1:8303", "alice")
        sc = r.headers["set-cookie"].lower()
        assert r.status_code == 200 and "secure" in sc and sc.startswith("__host-vc_session="), r.headers
        token = r.cookies["__Host-vc_session"]
        base = "http://127.0.0.1:8303"
        assert httpx.get(base + "/me", headers={"Cookie": f"__Host-vc_session={token}"}).status_code == 200
        assert httpx.get(base + "/me", headers={"Cookie": f"vc_session={token}"}).status_code == 401
        out = httpx.post(base + "/logout", headers={"Cookie": f"__Host-vc_session={token}"})
        sc = out.headers["set-cookie"].lower()
        assert sc.startswith("__host-vc_session=") and "secure" in sc and "max-age=0" in sc, sc
        assert httpx.get(base + "/me", headers={"Cookie": f"__Host-vc_session={token}"}).status_code == 401
    finally:
        kill(p)


def t_bad_auth_mode_refused():
    r = subprocess.run([sys.executable, "-c", "import server.app"], cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "AGENT_ROOT": str(TMP / "bad"), "AGENT_AUTH": "none"})
    assert r.returncode != 0 and "AGENT_AUTH" in r.stderr + r.stdout, r.stderr[-300:]


def local(name):
    """auth on its own DB, in this process (for races that need exact timing)."""
    import threading
    db = sqlite3.connect(TMP / f"{name}.db", check_same_thread=False, isolation_level=None)
    auth.init(db)
    lock = threading.Lock()

    def q(sql, *args):
        with lock:
            return db.execute(sql, args).fetchall()
    return q


def t_parallel_guesses_cannot_pass_the_lock():
    import threading
    q = local("parallel")
    auth.set_password(q, "pat", PW)
    stored = q("SELECT pw FROM users WHERE id='pat'")[0][0]
    real, orig = [0], auth._check

    def counting(password, h):
        if h == stored:
            real[0] += 1
        return orig(password, h)
    auth._check, auth._FAILS = counting, {}
    try:
        stop = time.time() + 1.5
        hits = []

        def guess():
            while time.time() < stop:
                if auth.login(q, "pat", "wrong-password"):
                    hits.append(1)
        ts = [threading.Thread(target=guess) for _ in range(16)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert real[0] <= auth.MAX_FAILS, f"{real[0]} guesses were checked, the lock allows {auth.MAX_FAILS}"
        assert auth.login(q, "pat", PW) is None, "the right password got in while the name is locked"
    finally:
        auth._check, auth._FAILS = orig, {}


def t_login_racing_a_password_change_loses():
    q = local("race")
    orig = auth._check
    for change in ("passwd", "remove"):
        auth.set_password(q, "rae", PW)
        done = []

        def during_hash(password, h):  # the change lands while login is hashing the old password
            if not done:
                done.append(1)
                if change == "passwd":
                    auth.set_password(q, "rae", "brand-new-password")
                else:
                    q("DELETE FROM users WHERE id='rae'")
            return orig(password, h)
        auth._check = during_hash
        try:
            token = auth.login(q, "rae", PW)
        finally:
            auth._check = orig
        assert token is None or auth.user_of(q, token) is None, f"{change}: a login with the old password survived"
    auth.set_password(q, "rae", PW)
    token = auth.login(q, "rae", PW)
    q("DELETE FROM users WHERE id='rae'")  # removal deletes the user first, then its logins
    assert auth.user_of(q, token) is None, "a removed user's login still works"


def t_expired_login_rejected():
    q = local("expiry")
    auth.set_password(q, "eve", PW)
    token = auth.login(q, "eve", PW)
    assert auth.user_of(q, token) == "eve"
    q("UPDATE logins SET expires = ?", int(time.time()) - 1)
    assert auth.user_of(q, token) is None


def t_name_tracking_is_bounded():
    q = local("bounded")
    auth.set_password(q, "vic", PW)
    calls, orig = [0], auth._hash

    def counting(*a):
        calls[0] += 1
        return orig(*a)
    auth._hash, auth._FAILS = counting, {}
    try:
        for name in ("ghost", "Bad Name", "x" * 5000):
            calls[0] = 0
            assert auth.login(q, name, "whatever-password") is None
            assert calls[0] == 1, f"{name[:10]!r}: {calls[0]} hashes (an existing name costs 1)"
        assert "Bad Name" not in auth._FAILS and "x" * 5000 not in auth._FAILS
        for _ in range(auth.MAX_FAILS):
            auth.login(q, "vic", "wrong-password")
        for i in range(auth._MAX_TRACKED):  # made-up names fill the table
            auth._FAILS.setdefault(f"n{i}", [1, 0, 0])
        auth.login(q, "one-more", "wrong-password")
        assert auth.login(q, "vic", PW) is None, "made-up names pushed out the lock on a real one"
    finally:
        auth._hash, auth._FAILS = orig, {}


def t_relogin_ends_previous_login():
    main_server()
    c, r = login(URL, "erin")
    old = r.cookies[auth.COOKIE]
    assert c.post("/login", json={"user": "erin", "password": PW}).status_code == 200
    assert httpx.Client(base_url=URL, cookies={auth.COOKIE: old}).get("/me").status_code == 401


def t_bad_app_origin_refused():
    for origin in ("http://127.0.0.1:8200/", "https://chat.example/app", "https://Chat.example", "https://chat.example:443"):
        r = subprocess.run([sys.executable, "-c", "import server.app"], cwd=ROOT, capture_output=True, text=True,
                           env={**os.environ, "AGENT_ROOT": str(TMP / "bad"), "AGENT_APP_ORIGIN": origin})
        assert r.returncode != 0 and "AGENT_APP_ORIGIN" in r.stderr + r.stdout, (origin, r.stderr[-300:])


def t_ui_logout_leaves_nothing():
    from playwright.sync_api import sync_playwright
    main_server()
    a, _ = login(URL, "alice")
    a.post("/projects", json={"project_id": "alice-secret"})
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        page = b.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))

        def enter(user):
            page.wait_for_selector("#login:not([hidden])")
            page.fill("#login-user", user)
            page.fill("#login-password", PW)
            page.click("#login button")
            try:
                page.wait_for_selector("#app:not([hidden])", timeout=8000)
            except Exception:
                raise AssertionError(f"{user}: login failed: {page.inner_text('#login-error')!r} {errs}")
        page.goto(URL + "/")
        enter("alice")
        page.wait_for_function("() => [...document.querySelectorAll('#project option')].some(o => o.value === 'alice-secret')")
        page.fill("#input", "alice's unsent draft")
        with page.expect_navigation():  # logout reloads the page
            page.click("#logout")
        page.wait_for_selector("#login:not([hidden])")
        html = page.evaluate("() => document.getElementById('app').outerHTML + document.getElementById('input').value")
        assert "alice-secret" not in html and "unsent draft" not in html, "alice's data is still in the page"
        enter("gus")
        page.wait_for_function("() => document.getElementById('whoami').textContent === 'gus'")
        html = page.evaluate("() => document.getElementById('app').outerHTML + document.getElementById('input').value")
        assert "alice-secret" not in html and "unsent draft" not in html, "the next user sees alice's data"
        b.close()


def t_odd_input_never_sticks():
    q = local("odd")
    auth.set_password(q, "una", PW)
    auth._FAILS = {}
    try:
        for _ in range(auth.MAX_FAILS + 2):  # lone surrogate: valid JSON, used to raise before the guess was recorded
            assert auth.login(q, "una", "\ud800") is None
        assert all(busy == 0 for _, _, busy in auth._FAILS.values()), auth._FAILS
        auth._FAILS = {}
        assert auth.login(q, "una", PW), "the account stayed blocked"
        try:
            auth.set_password(q, "una", "x" * (auth.MAX_PASSWORD + 1))
            raise AssertionError("a password /login can never accept was set")
        except ValueError:
            pass
        auth.set_password(q, "una", "y" * auth.MAX_PASSWORD)
        assert auth.login(q, "una", "y" * auth.MAX_PASSWORD)
        assert auth.login(q, "una", "y" * (auth.MAX_PASSWORD + 1)) is None
    finally:
        auth._FAILS = {}


def t_failed_login_keeps_current_login():
    main_server()
    c, _ = login(URL, "hal")
    assert c.post("/login", json={"user": "hal", "password": "wrong-password"}).status_code == 401
    assert c.get("/me").json() == {"user": "hal"}


def t_event_stream_ends_after_logout():
    main_server()
    c, _ = login(URL, "ivy")
    db = sqlite3.connect(DATA / "agent.db", isolation_level=None)
    db.execute("INSERT INTO sessions(id, user_id, project_id, history) VALUES ('s-ivy', 'ivy', 'p', '[]')")
    db.execute("INSERT INTO runs(id, session_id, status) VALUES ('r-ivy', 's-ivy', 'running')")  # never finishes
    db.close()
    token = c.cookies[auth.COOKIE]
    reader = httpx.Client(base_url=URL, cookies={auth.COOKIE: token}, timeout=30)
    t0 = time.time()
    with reader.stream("GET", "/runs/r-ivy/events") as r:
        assert r.status_code == 200
        c.post("/logout")
        for _ in r.iter_lines():
            pass
    assert time.time() - t0 < 15, "the stream kept running after logout"


def t_ui_expired_login_clears_page():
    from playwright.sync_api import sync_playwright
    main_server()
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        page = b.new_page()
        page.goto(URL + "/")
        page.wait_for_selector("#login:not([hidden])")
        page.fill("#login-user", "gus")
        page.fill("#login-password", PW)
        page.click("#login button")
        page.wait_for_selector("#app:not([hidden])")
        page.fill("#input", "gus's unsent draft")
        accounts(DATA, "gus")  # same password again: ends every login of gus
        page.evaluate("() => loadProjects().catch(() => {})")  # the next API call gets 401
        page.wait_for_selector("#login:not([hidden])")
        assert page.input_value("#input") == "" and page.inner_text("#whoami") == "", "the page kept gus's state"
        b.close()


def t_own_change_cannot_undo_the_admin():
    q = local("cas")
    for admin in ("remove", "reset"):
        auth.set_password(q, "kim", PW)
        old = auth.verify(q, "kim", PW)  # the user proved the old password (/password) ...
        if admin == "remove":                 # ... and the admin acts before the new one is stored
            q("DELETE FROM users WHERE id='kim'")
        else:
            auth.set_password(q, "kim", "admin-reset-pass")
        assert auth.set_password(q, "kim", "users-new-pass", expect=old) is None, admin
        assert auth.login(q, "kim", "users-new-pass") is None, f"{admin} was undone"
        if admin == "reset":
            assert auth.login(q, "kim", "admin-reset-pass")


def t_password_endpoint_keeps_admin_changes():
    """The endpoint itself (not just auth.set_password) must not undo a removal made while it was hashing."""
    data = TMP / "inproc"
    accounts(data, "mo")
    env = {"AGENT_ROOT": str(data), "AGENT_APP_ORIGIN": "http://testserver", "AGENT_AUTH": "login"}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        from fastapi.testclient import TestClient
        import server.app as app_mod
    finally:
        for k, v in saved.items():
            os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
    c = TestClient(app_mod.app)
    assert c.post("/login", json={"user": "mo", "password": PW}).status_code == 200
    orig, done = auth._check, []

    def remove_during(password, h):
        ok = orig(password, h)
        if not done:  # the admin removes mo while /password is verifying the old password
            done.append(1)
            app_mod.q("DELETE FROM users WHERE id='mo'")
            app_mod.q("DELETE FROM logins WHERE user_id='mo'")
        return ok
    auth._check = remove_during
    try:
        r = c.post("/password", json={"old": PW, "new": "brand-new-pass"})
    finally:
        auth._check = orig
    assert r.status_code in (401, 409), r.status_code
    assert not app_mod.q("SELECT 1 FROM users WHERE id='mo'"), "/password brought back a removed account"


def t_password_endpoint_counts_toward_lock():
    main_server()
    c, _ = login(URL, "jon")
    for _ in range(auth.MAX_FAILS):
        assert c.post("/password", json={"old": "wrong-password", "new": "another-pass-3"}).status_code == 403
    r = c.post("/password", json={"old": PW, "new": "another-pass-3"})
    assert r.status_code == 403, "a cookie could keep guessing the password through /password"


def t_guess_released_on_any_error():
    q = local("errors")
    auth.set_password(q, "lee", PW)
    orig = auth._check

    def boom(password, h):
        raise RuntimeError("hash failed")
    auth._check, auth._FAILS = boom, {}
    try:
        for _ in range(auth.MAX_FAILS + 1):
            try:
                auth.login(q, "lee", PW)
            except RuntimeError:
                pass
        assert all(busy == 0 for _, _, busy in auth._FAILS.values()), auth._FAILS
    finally:
        auth._check, auth._FAILS = orig, {}


def t_ui_failed_logout_does_not_look_logged_out():
    from playwright.sync_api import sync_playwright
    main_server()
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        page = b.new_page()
        page.goto(URL + "/")
        page.wait_for_selector("#login:not([hidden])")
        page.fill("#login-user", "kit")
        page.fill("#login-password", PW)
        page.click("#login button")
        page.wait_for_selector("#app:not([hidden])")
        page.route("**/logout", lambda route: route.abort())
        page.click("#logout")
        page.wait_for_selector(".msg.error")
        assert page.is_hidden("#login") and page.is_visible("#app"), "a failed logout showed the login form"
        b.close()


if __name__ == "__main__":
    only, failed = sys.argv[1:], 0
    for name in only:
        if not name.startswith("t_") or name not in globals():
            print("FAIL", name, "no such test")
            failed += 1
    try:
        for name, fn in list(globals().items()):
            if name.startswith("t_") and (not only or name in only):
                try:
                    t = time.time(); fn(); print(f"PASS {name} ({time.time() - t:.0f}s)")
                except Exception as e:
                    failed += 1
                    print("FAIL", name, repr(e)[:600])
    finally:
        if SRV:
            kill(SRV)
    sys.exit(1 if failed else 0)
