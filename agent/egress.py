"""Egress allowlist proxy for sandboxes (HTTPS CONNECT only).

The sandbox sits on an `--internal` Docker network: no route out, no DNS. The only way out is this proxy,
listening on the network's gateway IP. It decides per hostname (no TLS interception), logs every decision,
and either dials the host itself (refusing non-public IPs, so no SSRF to metadata/LAN) or chains to an
upstream CONNECT proxy (e.g. a corporate one).
"""
import asyncio
import ipaddress
import re
import socket
import subprocess
import threading
from urllib.parse import urlparse

NETWORK = "sbx-egress"
# strict DNS name: rejects IP literals, NUL/space/@/# tricks, trailing dots, userinfo
HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def ensure_network():
    """Create the internal network once; return the gateway IP the proxy must listen on."""
    subprocess.run(["docker", "network", "create", "--internal", NETWORK], capture_output=True)
    return subprocess.run(["docker", "network", "inspect", NETWORK, "-f", "{{(index .IPAM.Config 0).Gateway}}"],
                          capture_output=True, text=True, check=True).stdout.strip()


class EgressProxy:
    def __init__(self, allow=(), upstream=None, host=None, port=0):
        self.allow = set(allow)    # "pypi.org" exact, "*.example.com" subdomains
        self.upstream = urlparse(upstream) if upstream else None
        self.log = []              # [(host, "allow" | reason)]
        self.host = host or ensure_network()
        self.loop = asyncio.new_event_loop()
        srv = self.loop.run_until_complete(asyncio.start_server(self.handle, self.host, port))
        self.port = srv.sockets[0].getsockname()[1]
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://{self.host}:{self.port}"

    def allowed(self, host):
        return any(host == a or (a.startswith("*.") and host.endswith(a[1:])) for a in self.allow)

    def check(self, method, target):
        host, _, port = target.rpartition(":")
        host = host.lower()
        if method != "CONNECT":
            return host, "only HTTPS (CONNECT) is allowed"
        if port != "443":
            return host, "only port 443 is allowed"
        if not HOST_RE.match(host):
            return host, "invalid hostname (IP literals are not allowed)"
        if not self.allowed(host):
            return host, f"{host} is not in the egress allowlist"
        return host, None

    async def dial(self, host):
        if self.upstream:  # chain: the upstream proxy resolves and enforces its own policy
            r, w = await asyncio.open_connection(self.upstream.hostname, self.upstream.port)
            w.write(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
            status = (await r.readline()).split()
            while (await r.readline()) not in (b"\r\n", b""):
                pass
            if len(status) < 2 or status[1] != b"200":
                raise ConnectionError(f"upstream refused: {b' '.join(status).decode()}")
            return r, w
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ip = infos[0][4][0]
        if not ipaddress.ip_address(ip).is_global:  # pin the checked IP: no rebinding between check and connect
            raise ConnectionError(f"{host} resolves to non-public {ip}")
        return await asyncio.open_connection(ip, 443)

    async def handle(self, r, w):
        try:
            line = (await r.readline()).decode("latin-1").split()
            while (await r.readline()) not in (b"\r\n", b"\n", b""):
                pass
            host, reason = self.check(*line[:2]) if len(line) == 3 else ("?", "malformed request")
            if not reason:
                try:
                    ur, uw = await self.dial(host)
                except Exception as e:
                    reason = str(e)
            self.log.append((host, reason or "allow"))
            if reason:
                w.write(f"HTTP/1.1 403 Forbidden\r\nX-Egress-Reason: {reason}\r\n"
                        f"Content-Length: {len(reason)}\r\n\r\n{reason}".encode())
                await w.drain()
                return
            w.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await asyncio.gather(pipe(r, uw), pipe(ur, w))
        finally:
            w.close()


async def pipe(r, w):
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except ConnectionError:
        pass
    finally:
        w.close()
