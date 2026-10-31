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
        accounts(DATA, "alice", "bob", "carol", "dave", "erin")
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
        assert r.status_code == 200 and "secure" in r.headers["set-cookie"].lower(), r.headers
    finally:
        kill(p)


def t_bad_auth_mode_refused():
    r = subprocess.run([sys.executable, "-c", "import server.app"], cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "AGENT_ROOT": str(TMP / "bad"), "AGENT_AUTH": "none"})
    assert r.returncode != 0 and "AGENT_AUTH" in r.stderr + r.stdout, r.stderr[-300:]


if __name__ == "__main__":
    only = sys.argv[1:]
    try:
        for name, fn in list(globals().items()):
            if name.startswith("t_") and (not only or name in only):
                try:
                    t = time.time(); fn(); print(f"PASS {name} ({time.time() - t:.0f}s)")
                except Exception as e:
                    print("FAIL", name, repr(e)[:600])
    finally:
        if SRV:
            kill(SRV)
