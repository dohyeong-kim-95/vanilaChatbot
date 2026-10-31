"""Login (Loop 24). Accounts are made by whoever runs the server, there is no sign-up page:

    python -m server.auth add alice        # asks for the password
    python -m server.auth passwd alice     # new password, signs alice out everywhere
    python -m server.auth remove alice     # the account and its logins (stored files stay)
    python -m server.auth list

A login is a random token in an HttpOnly, SameSite=Strict cookie; the DB keeps only its SHA-256, so a copy of the
DB does not let anyone in. Passwords are scrypt hashes. AGENT_AUTH=header trusts X-User instead (local
development and tests only: anyone who can reach the port can claim any user).
"""
import getpass
import hashlib
import hmac
import os
import secrets
import sqlite3
import sys
import threading
import time
from pathlib import Path

from agent.userstore import ID_RE

COOKIE = "vc_session"
TTL = int(os.environ.get("AGENT_LOGIN_TTL", 7 * 24 * 3600))
MIN_PASSWORD = 10
MAX_FAILS, LOCK_SECONDS = 5, 300  # per user name: 5 wrong passwords lock the name for 5 minutes
_N, _R, _P = 2 ** 14, 8, 1        # scrypt cost: ~50 ms, 16 MB
_FAILS = {}                       # name -> [fails, locked_until, attempts in flight]; process-local
_MAX_TRACKED = 10000
_FLOCK = threading.Lock()


def init(db):  # the sqlite connection itself; everything else takes q(sql, *args) -> rows (the server's locked q)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, pw TEXT);
    CREATE TABLE IF NOT EXISTS logins(token TEXT PRIMARY KEY, user_id TEXT, expires INTEGER);
    """)


def _hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 2 ** 20)
    return f"scrypt${salt.hex()}${h.hex()}"


_DUMMY = _hash("not a password")  # unknown names cost the same time as known ones (no probing for names)


def _check(password, stored):
    _, salt, h = stored.split("$")
    return hmac.compare_digest(_hash(password, bytes.fromhex(salt)), stored)


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def set_password(q, user, password):
    if not ID_RE.match(user):
        raise ValueError("user id: lowercase letters, digits, - and _, at most 64")
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password: at least {MIN_PASSWORD} characters")
    q("INSERT INTO users(id, pw) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET pw=excluded.pw", user, _hash(password))
    q("DELETE FROM logins WHERE user_id=?", user)  # a new password ends every old login


def _begin(user, now):
    """Reserve one guess. Attempts still hashing count as failures-to-be, so parallel requests cannot run more than
    MAX_FAILS guesses before the lock (checking and recording separately let them overwrite the lock)."""
    with _FLOCK:
        e = _FAILS.get(user)
        if e is None:
            if len(_FAILS) >= _MAX_TRACKED:  # made-up names: forget only names that are neither locked nor busy
                for k in [k for k, (_, until, busy) in _FAILS.items() if until <= now and not busy]:
                    del _FAILS[k]
                if len(_FAILS) >= _MAX_TRACKED:
                    return False  # cannot track this name, so do not let it guess
            e = _FAILS[user] = [0, 0, 0]
        if e[1] > now or e[0] + e[2] >= MAX_FAILS:
            return False
        e[2] += 1
        return True


def _end(user, ok, now):
    with _FLOCK:
        e = _FAILS[user]
        e[2] -= 1
        e[0] = 0 if ok else e[0] + 1
        if e[0] >= MAX_FAILS:
            e[0], e[1] = 0, max(e[1], now + LOCK_SECONDS)
        if e == [0, 0, 0]:
            del _FAILS[user]


def login(q, user, password):
    """Returns a new token, or None. Wrong passwords, unknown names and locked names look the same to the caller."""
    now = time.time()
    if not ID_RE.match(user):  # no account can have this name: same cost, nothing tracked
        _check(password, _DUMMY)
        return None
    if not _begin(user, now):
        return None
    row = (q("SELECT pw FROM users WHERE id=?", user) or [None])[0]
    ok = _check(password, row[0] if row else _DUMMY) and bool(row)
    _end(user, ok, now)
    if not ok:
        return None
    token = secrets.token_urlsafe(32)
    q("DELETE FROM logins WHERE expires < ?", int(now))
    # only if the hash we checked is still the account's: a password change or removal while we were hashing wins
    if not q("""INSERT INTO logins(token, user_id, expires) SELECT ?, ?, ? WHERE (SELECT pw FROM users WHERE id=?) = ?
                RETURNING token""", _digest(token), user, int(now) + TTL, user, row[0]):
        return None
    return token


def user_of(q, token):
    if not token:
        return None
    row = q("""SELECT l.user_id FROM logins l JOIN users u ON u.id = l.user_id WHERE l.token=? AND l.expires >= ?""",
            _digest(token), int(time.time()))
    return row[0][0] if row else None


def logout(q, token):
    if token:
        q("DELETE FROM logins WHERE token=?", _digest(token))


def main(argv):
    root = Path(os.environ.get("AGENT_ROOT", "/tmp/agent-sessions"))
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "agent.db", isolation_level=None)
    init(db)
    q = lambda sql, *args: db.execute(sql, args).fetchall()
    cmd, *rest = argv or ["help"]
    if cmd == "list":
        for (u,) in q("SELECT id FROM users ORDER BY id"):
            print(u)
    elif cmd in ("add", "passwd") and len(rest) == 1:
        exists = q("SELECT 1 FROM users WHERE id=?", rest[0])
        if (cmd == "add") == bool(exists):
            sys.exit(f"{rest[0]}: " + ("already exists (use passwd)" if exists else "no such user (use add)"))
        pw = getpass.getpass("password: ")
        if pw != getpass.getpass("again: "):
            sys.exit("passwords differ")
        try:
            set_password(q, rest[0], pw)
        except ValueError as e:
            sys.exit(str(e))
        print("ok")
    elif cmd == "remove" and len(rest) == 1:
        gone = q("DELETE FROM users WHERE id=? RETURNING id", rest[0])  # first: a login racing this one fails
        q("DELETE FROM logins WHERE user_id=?", rest[0])
        print("ok" if gone else "no such user")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
