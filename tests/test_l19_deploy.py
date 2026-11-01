"""Deployment (Loop 25): deploy/install.sh on a fresh Ubuntu 24.04 server, end to end.

The "server" is a privileged container with systemd as PID 1 and its own Docker daemon (tests/deploy/Dockerfile),
so the units, Caddy, the sandbox and the hardening run as they would on a real machine. It installs the working
tree as it is now (a snapshot commit, uncommitted changes included). Needs Docker and internet (apt, pip,
Playwright's Chromium, fonts): about 10 minutes.

    python tests/test_l19_deploy.py            # KEEP=1 leaves the container running for a look
"""
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAME = IMAGE = "vanila-deploy-test"
# overlayfs cannot sit on the container's own overlay root: Docker and containerd keep their layers in volumes
VOLUMES = {"vanila-deploy-test-docker": "/var/lib/docker", "vanila-deploy-test-containerd": "/var/lib/containerd"}
APP, RENDER = "app.test", "render.test"
PASSWORD = "deploy-password-1"


def run(*cmd, check=True, input=None):
    p = subprocess.run(cmd, capture_output=True, text=True, input=input)
    if check and p.returncode:
        raise AssertionError(f"{' '.join(cmd)[:200]} -> {p.returncode}\n{p.stdout[-3000:]}\n{p.stderr[-3000:]}")
    return p


def sh(script, check=True, input=None):
    return run("docker", "exec", "-i", NAME, "bash", "-c", script, check=check, input=input)


def curl(method, path, data=None, host=APP, origin=True, extra="", jar=True):
    """Through Caddy (https, its internal CA), with a cookie jar. Returns (status, body)."""
    cmd = (f"curl -sk -o /tmp/body -w '%{{http_code}}' --resolve {host}:443:127.0.0.1 "
           f"{'-b /root/jar -c /root/jar' if jar else ''} -X {method} {extra} https://{host}{path}")
    if origin:
        cmd += f" -H 'Origin: https://{APP}'"
    if data is not None:
        cmd += f" -H 'Content-Type: application/json' --data {shlex.quote(json.dumps(data))}"
    code = sh(cmd).stdout
    body = sh("cat /tmp/body").stdout
    return int(code), body


def snapshot(dest, broken=None):
    """The working tree (tracked and new files) as one commit in a fresh repo at dest."""
    files = run("git", "-C", str(REPO), "ls-files", "-co", "--exclude-standard", "-z").stdout.split("\0")
    if not (dest / ".git").exists():
        run("git", "init", "-q", str(dest))
    for f in filter(None, files):
        src = REPO / f
        if src.is_file():
            (dest / f).parent.mkdir(parents=True, exist_ok=True)
            (dest / f).write_bytes(src.read_bytes())
            (dest / f).chmod(src.stat().st_mode & 0o777)
    if broken:  # a release whose app or renderer cannot start: install.sh must go back to the previous one
        (dest / broken).write_text("raise SystemExit('broken release')\n")
    run("git", "-C", str(dest), "add", "-A")
    run("git", "-C", str(dest), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
        "-m", "broken" if broken else "snapshot")


def install(*args, check=True, umask="022"):
    return sh(f"umask {umask}; /src/deploy/install.sh " + " ".join(args) + " 2>&1", check=check)


def current():
    return sh("readlink /opt/vanila/current").stdout.strip()


def t_fresh_install():
    out = install("--app-host", APP, "--render-host", RENDER, "--tls", "internal").stdout
    assert "running: https://app.test" in out, out[-2000:]
    assert "next: sudo vanila-auth add" in out, out[-2000:]
    for unit in ("vanila-app", "vanila-render", "vanila-gateway", "caddy"):
        assert sh(f"systemctl is-active {unit}", check=False).stdout.strip() == "active", unit
    # only Caddy listens outside; the services are on loopback
    listen = sh("ss -Hltn | awk '{print $4}'").stdout.split()
    assert {"127.0.0.1:8200", "127.0.0.1:8201", "127.0.0.1:8100"} <= set(listen), listen
    assert not [a for a in listen if a.rsplit(":", 1)[1] in ("8200", "8201", "8100") and not a.startswith("127.")]
    users = sh("ps -o user= -C uvicorn | sort -u").stdout.split()
    assert users == ["vanila"], users
    modes = sh("stat -c '%a %U:%G' /etc/vanila/vanila.env /etc/vanila/gateway.env /var/lib/vanila/data").stdout
    assert modes.split("\n")[:3] == ["640 root:vanila", "600 root:root", "700 vanila:vanila"], modes


def t_login_through_caddy():
    assert curl("GET", "/me")[0] == 401
    sh("vanila-auth add alice", input=f"{PASSWORD}\n{PASSWORD}\n")
    assert "alice" in sh("vanila-auth list").stdout
    assert curl("POST", "/login", {"user": "alice", "password": PASSWORD})[0] == 200
    jar = sh("cat /root/jar").stdout
    assert "__Host-vc_session" in jar and "\tFALSE\t/\tTRUE\t" in jar, jar  # Secure cookie for the https origin
    code, body = curl("GET", "/me")
    assert code == 200 and json.loads(body)["user"] == "alice", body
    # another origin cannot post; X-User from outside is dropped by Caddy (and ignored in login mode)
    assert curl("POST", "/projects", {"project_id": "x"}, extra="-H 'Origin: https://evil.test'", origin=False)[0] == 403
    code, _ = curl("GET", "/me", extra="-H 'X-User: alice'", jar=False)
    assert code == 401, code
    # http redirects to https
    loc = sh(f"curl -s -o /dev/null -w '%{{redirect_url}}' --resolve {APP}:80:127.0.0.1 http://{APP}/").stdout
    assert loc == f"https://{APP}/", loc


def t_run_in_sandbox():
    """A chat run end to end on the installed server: the app (in its systemd sandbox) starts a Docker sandbox,
    which runs as the service account, and the files come back."""
    sh("cp /src/tests/deploy/fake_llm.py /root/ && (nohup python3 /root/fake_llm.py >/root/fake.log 2>&1 &)")
    sh("sed -i 's|^LLM_BASE_URL=.*|LLM_BASE_URL=http://127.0.0.1:9100/v1|' /etc/vanila/vanila.env "
       "&& systemctl restart vanila-app")
    for _ in range(60):
        if sh("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8200/me", check=False).stdout == "401":
            break
        time.sleep(1)
    assert curl("POST", "/projects", {"project_id": "p"})[0] == 200
    sid = json.loads(curl("POST", "/sessions", {"project_id": "p"})[1])["session_id"]
    code, body = curl("POST", f"/sessions/{sid}/messages", {"content": "deploy check"})
    assert code == 200, body
    rid = json.loads(body)["run_id"]
    for _ in range(300):
        status = json.loads(curl("GET", f"/runs/{rid}")[1])["status"]
        if status != "running":
            break
        time.sleep(1)
    assert status == "succeeded", status
    code, events = curl("GET", f"/runs/{rid}/events", extra="-N --max-time 20")
    assert code == 200 and "event: run_finished" in events, events[-1000:]
    uid = sh("id -u vanila").stdout.strip()
    msgs = json.loads(curl("GET", f"/sessions/{sid}")[1])["messages"]
    final = [m["content"] for m in msgs if m["role"] == "assistant"][-1]
    assert f"uid={uid}(" in final or f"uid={uid} " in final, final  # the sandbox runs as the service account
    saved = sh("find /var/lib/vanila/data -name deployed.txt -path '*outputs*' -exec cat {} +").stdout
    assert "ok" in saved, saved
    assert not sh("ls /var/lib/vanila/data/work 2>/dev/null", check=False).stdout.strip()  # run dir removed


def t_renderer_through_caddy():
    code, _ = curl("GET", "/a/not-a-token", host=RENDER, origin=False)
    assert code in (403, 404), code  # the renderer answered (not 502)
    csp = sh(f"curl -sk -D - -o /dev/null --resolve {APP}:443:127.0.0.1 https://{APP}/ | grep -i content-security-policy").stdout
    assert f"frame-src https://{RENDER}" in csp, csp


def t_local_accounts_cannot_reach_admin_or_docker():
    """Another account on the server cannot rewrite Caddy's config (it terminates TLS: passwords, cookies), and the
    renderer and gateway, which share the docker group with the app, cannot reach Docker."""
    tcp = sh("runuser -u nobody -- curl -s -m 3 -o /dev/null -w '%{http_code}' http://localhost:2019/config/",
             check=False).stdout
    assert tcp == "000", tcp
    sock = sh("runuser -u nobody -- curl -s -m 3 -o /dev/null -w '%{http_code}' --unix-socket "
              "/var/lib/caddy/admin.sock http://x/config/", check=False).stdout
    assert sock == "000", sock
    sh("systemctl reload caddy")  # reload still reaches it, through the socket
    assert curl("GET", "/me")[0] == 200
    docker = lambda unit: sh(  # noqa: E731  Docker's API through the socket as the unit's mounts show it
        f"nsenter -t $(systemctl show -p MainPID --value {unit}) -m -- curl -s -m 3 -o /dev/null -w '%{{http_code}}' "
        "--unix-socket /run/docker.sock http://x/version", check=False).stdout
    assert docker("vanila-render") == "000" and docker("vanila-gateway") == "000"  # systemd's inaccessible node
    assert docker("vanila-app") == "200"


def t_update_keeps_data_and_secrets(tmp):
    env = sh("cat /etc/vanila/vanila.env").stdout
    before = current()
    snapshot(tmp)  # a new commit, like a git pull
    # no arguments: hosts and TLS from the last install. A hardened root umask must not hide files from Caddy.
    sh("rm /etc/caddy/vanila.caddy")  # made anew, as on a first install (an existing file keeps its mode)
    out = install(umask="077").stdout
    assert "running: https://app.test" in out, out[-2000:]
    after = current()
    assert after != before, after
    keep = lambda e: [l for l in e.splitlines() if not l.startswith("LLM_BASE_URL=")]  # noqa: E731
    assert keep(sh("cat /etc/vanila/vanila.env").stdout) == keep(env)  # secrets and settings stay
    assert curl("GET", "/me")[0] == 200  # logins live in the data dir
    assert "tls internal" in sh("cat /etc/caddy/vanila.caddy").stdout
    assert sh("stat -c %a /etc/caddy/vanila.caddy").stdout.strip() == "644"
    sh("systemctl restart caddy")  # reads its files as the caddy account
    assert curl("GET", "/me")[0] == 200


def t_failed_pull_keeps_current():
    """Fails before the switch (sandbox image unavailable): the running release stays, nothing half-switched."""
    before = current()
    sh("echo SANDBOX_IMAGE=vanila-test/does-not-exist:0 >> /etc/vanila/vanila.env")
    try:
        p = install(check=False)
    finally:
        sh("sed -i '/^SANDBOX_IMAGE=/d' /etc/vanila/vanila.env")
    assert p.returncode != 0, p.stdout[-1000:]
    assert current() == before, current()
    assert curl("GET", "/me")[0] == 200
    p = install("--app-host", APP, "--render-host", RENDER, "--no-start", check=False)
    assert p.returncode != 0 and "unchecked release" in p.stdout, p.stdout[-500:]


def t_broken_release_rolls_back(tmp):
    before = current()
    for broken in ("server/app.py", "server/render.py"):
        snapshot(tmp, broken=broken)
        # with a new app host: going back restores the settings too, else the old release gets the new origin
        p = install("--app-host", "new.test", check=False)
        assert p.returncode != 0 and "back on " + before in p.stdout, (broken, p.stdout[-2000:])
        assert sh("grep ^AGENT_APP_ORIGIN= /etc/vanila/vanila.env").stdout.strip() == f"AGENT_APP_ORIGIN=https://{APP}"
        assert "new.test" not in sh("cat /etc/caddy/vanila.caddy").stdout
        assert not sh("ls /etc/vanila/*.prev 2>/dev/null", check=False).stdout
        assert current() == before, current()
        code = curl("GET", "/me")[0]
        assert code == 200, code  # install.sh returns only after the previous release answers again
        assert curl("POST", "/projects", {"project_id": "after-rollback"})[0] == 200  # origin check passes
        run("git", "-C", str(tmp), "reset", "-q", "--hard", "HEAD~1")
        run("git", "-C", str(tmp), "clean", "-qfd")
    # a good install afterwards keeps the last good release to go back to, and only releases that ran
    snapshot(tmp)
    install()
    rels = sh("ls -1 /opt/vanila/releases").stdout.split()
    assert before.rsplit("/", 1)[1] in rels and len(rels) == 2, rels
    assert all(sh(f"test -f /opt/vanila/releases/{r}/.ok", check=False).returncode == 0 for r in rels), rels


def t_bad_arguments():
    for args, msg in [(f"--app-host {APP} --render-host app.test:8443", "different host names"),
                      (f"--app-host App.test --render-host {RENDER}", "lowercase"),
                      (f"--app-host {APP}:443 --render-host {RENDER}", "leave out :443"),
                      (f"--app-host {APP}:8200 --render-host {RENDER}", "port 8200 is taken"),
                      (f"--app-host {APP} --render-host {RENDER}:2019", "port 2019 is taken"),
                      (f"--app-host {APP} --render-host {RENDER} --email 'a|b@c'", "--email"),
                      (f"--app-host {APP} --render-host {RENDER} --tls self", "acme or internal")]:
        p = install(args, "--no-start", check=False)
        assert p.returncode != 0 and msg in p.stdout, (args, p.stdout[-500:])


def main():
    tmp = Path(tempfile.mkdtemp(prefix="vanila-deploy-"))
    snapshot(tmp)
    run("docker", "build", "-q", "-t", IMAGE, str(REPO / "tests" / "deploy"))
    run("docker", "rm", "-f", NAME, check=False)
    run("docker", "volume", "rm", *VOLUMES, check=False)
    run("docker", "run", "-d", "--name", NAME, "--privileged", "--cgroupns=private", "--tmpfs", "/run",
        "--tmpfs", "/run/lock", *[a for v, d in VOLUMES.items() for a in ("-v", f"{v}:{d}")], "-v", f"{tmp}:/src",
        IMAGE)
    tests = [t_bad_arguments, t_fresh_install, t_login_through_caddy, t_run_in_sandbox, t_renderer_through_caddy,
             t_local_accounts_cannot_reach_admin_or_docker,
             lambda: t_update_keeps_data_and_secrets(tmp), t_failed_pull_keeps_current,
             lambda: t_broken_release_rolls_back(tmp)]
    names = ["t_bad_arguments", "t_fresh_install", "t_login_through_caddy", "t_run_in_sandbox",
             "t_renderer_through_caddy", "t_local_accounts_cannot_reach_admin_or_docker",
             "t_update_keeps_data_and_secrets", "t_failed_pull_keeps_current",
             "t_broken_release_rolls_back"]
    passed = failed = 0
    try:
        sh("systemctl is-system-running --wait >/dev/null; true")
        for name, t in zip(names, tests):
            t0 = time.time()
            try:
                t()
                passed += 1
                print(f"PASS {name} ({time.time() - t0:.0f}s)", flush=True)
            except Exception as e:
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}", flush=True)
                if name == "t_fresh_install":
                    break  # nothing else can run
    finally:
        if not os.environ.get("KEEP"):
            run("docker", "rm", "-f", NAME, check=False)
            run("docker", "volume", "rm", *VOLUMES, check=False)
    print(f"{passed} pass, {failed} fail")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
