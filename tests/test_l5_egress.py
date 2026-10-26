"""Loop 8: sandbox egress allowlist. Proxy rules (no LLM) + the agent asking for a new domain (gateway on :8100).
In this test env the proxy chains to $HTTPS_PROXY, which re-terminates TLS -> EGRESS_CA is mounted in the sandbox."""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("EGRESS_CA", "/root/.ccr/ca-bundle.crt")
from agent.egress import EgressProxy  # noqa: E402
from agent.loop import Context, run  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l5-"))
PYPI = ["pypi.org", "files.pythonhosted.org"]
UP = os.environ.get("HTTPS_PROXY")

PROBE = r'''
import socket, sys, urllib.request
host, port = sys.argv[1].rsplit(":", 1)
def connect(target):
    s = socket.create_connection((host, int(port)), timeout=10)
    s.sendall(b"CONNECT " + target + b" HTTP/1.1\r\n\r\n")
    return s.recv(200).split(b"\r\n")[0].decode()
for t in [b"pypi.org:443", b"example.com:443", b"pypi.org\x00.evil.com:443", b"evil.com#pypi.org:443",
          b"169.254.169.254:443", b"pypi.org:22", b"user@pypi.org:443"]:
    print(repr(t), connect(t))
s = socket.create_connection((host, int(port)), timeout=10)
s.sendall(b"GET http://pypi.org/ HTTP/1.1\r\nHost: pypi.org\r\n\r\n"); print("plain GET", s.recv(200).split(b"\r\n")[0].decode())
try:
    socket.create_connection(("151.101.0.223", 443), timeout=5); print("DIRECT OK")
except OSError as e:
    print("direct:", e)
'''


def t_proxy_rules():
    px = EgressProxy(PYPI, upstream=UP)
    sb = Sandbox(TMP / "rules", egress=px)
    try:
        (sb.workspace / "probe.py").write_text(PROBE)
        out = sb.exec(f"python probe.py {px.host}:{px.port}")
        print("   ", out.replace("\n", "\n    "))
        lines = out.splitlines()
        assert "200" in lines[1] and all("403" in l for l in lines[2:9]), out
        assert "DIRECT OK" not in out and "unreachable" in out, out
        pip = sb.exec("pip install -q --user tabulate 2>&1 | tail -2; python -c 'import tabulate; print(\"ok\")'", 120)
        assert pip.rstrip().endswith("ok"), pip
        print("    proxy log:", px.log[-4:])
    finally:
        sb.close()


def agent_fetch(approve_answer):
    px = EgressProxy(PYPI, upstream=UP)
    sb = Sandbox(TMP / f"fetch{approve_answer}", egress=px)
    asked, log = [], []
    try:
        # registry.npmjs.org: not in our allowlist, but allowed by this test env's upstream policy
        ans, _ = run("Use Python in the sandbox to download https://registry.npmjs.org/left-pad (JSON) and tell me "
                     "its dist-tags.latest version.",
                     Context(sandbox=sb, egress=px, approve=lambda n, a: asked.append((n, a)) or approve_answer),
                     tool_names=["bash", "write_file", "request_network_access"], log=log.append)
        print(f"    approvals asked: {asked}\n    answer: {ans[:160]!r}")
        return ans, asked, px
    finally:
        sb.close()


def t_agent_requests_new_domain():
    ans, asked, px = agent_fetch(True)
    assert asked and asked[0][0] == "request_network_access" and "npmjs.org" in asked[0][1]["domain"], asked
    assert "1.3.0" in ans and ("registry.npmjs.org", "allow") in px.log, (ans, px.log)


def t_agent_denied_domain():
    ans, asked, px = agent_fetch(False)
    assert asked and ("registry.npmjs.org", "allow") not in px.log, px.log


if __name__ == "__main__":
    only = sys.argv[1:]
    for name, fn in list(globals().items()):
        if name.startswith("t_") and (not only or name in only):
            try:
                t = time.time(); fn(); print(f"PASS {name} ({time.time() - t:.0f}s)")
            except Exception as e:
                print("FAIL", name, repr(e)[:600])
