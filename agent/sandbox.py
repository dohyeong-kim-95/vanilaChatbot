"""Isolated execution: one hardened Docker container per session, workspace bind-mounted."""
import contextlib
import os
import re
import subprocess
import threading
import uuid
from pathlib import Path

IMAGE = os.environ.get("SANDBOX_IMAGE", "python:3.12-slim")
CA = os.environ.get("EGRESS_CA")  # CA bundle to trust inside the sandbox, if TLS is re-terminated upstream
MAX_OUT = 10_000  # chars; head+tail kept beyond this (mini-swe-agent style)


def truncate(s, limit=MAX_OUT):
    if len(s) <= limit:
        return s
    h = limit // 2
    return f"{s[:h]}\n\n[... {len(s) - limit} chars truncated; use a narrower command (head/grep) ...]\n\n{s[-h:]}"


class _Cap(threading.Thread):
    """Reads a pipe to the end, keeping the first and last n bytes."""
    def __init__(self, stream, n=MAX_OUT // 2):
        super().__init__(daemon=True)
        self.stream, self.n, self.head, self.tail, self.total = stream, n, bytearray(), bytearray(), 0

    def run(self):
        for chunk in iter(lambda: self.stream.read(65536), b""):
            self.total += len(chunk)
            take = max(0, self.n - len(self.head))
            self.head += chunk[:take]
            self.tail += chunk[take:]
            del self.tail[:max(0, len(self.tail) - self.n)]

    @property
    def dropped(self):
        return self.total - len(self.head) - len(self.tail)

    def text(self):
        mid = f"\n\n[... {self.dropped:,} bytes truncated; use a narrower command (head/grep) ...]\n\n" \
            if self.dropped else ""
        return self.head.decode(errors="replace") + mid + self.tail.decode(errors="replace")


class Sandbox:
    def __init__(self, workspace, network="none", egress=None, readonly=(), name=None, mounts=None, outputs=None):
        """egress: an EgressProxy -> internal network whose only way out is that proxy's allowlist.
        readonly: workspace subdirs mounted read-only.
        mounts: {host dir: container path}, read-only, outside /workspace (e.g. skills).
        outputs: a host dir mounted writable at /workspace/outputs, the rest of the workspace read-only (fresh())."""
        self.workspace = Path(workspace).resolve()
        self.readonly = [self.workspace / d for d in readonly]
        self._fresh = dict(readonly=readonly, mounts=mounts)
        self.workspace.mkdir(parents=True, exist_ok=True)
        os.chown(self.workspace, 1000, 1000)  # sandbox runs as uid 1000
        self.name = "sbx-" + (name or uuid.uuid4().hex[:10])
        # orphans of a crashed server: this container and the agent containers made from it (fresh())
        old = subprocess.run(["docker", "ps", "-aq", "--filter", f"name=^{re.escape(self.name)}(-a[0-9a-f]{{8}})?$"],
                             capture_output=True, text=True).stdout.split()
        subprocess.run(["docker", "rm", "-f", self.name, *old], capture_output=True)
        extra = []
        if egress:
            from .egress import NETWORK
            network = NETWORK
            extra = [a for k in ("HTTPS_PROXY", "https_proxy") for a in ("-e", f"{k}={egress.url}")]
            if CA:  # only needed when an upstream proxy re-terminates TLS (as in this test env)
                extra += ["-v", f"{CA}:/etc/egress-ca.pem:ro"] + [
                    a for k in ("SSL_CERT_FILE", "PIP_CERT", "REQUESTS_CA_BUNDLE") for a in ("-e", f"{k}=/etc/egress-ca.pem")]
        subprocess.run([
            "docker", "run", "-d", "--rm", "--name", self.name, *extra,
            "--network", network, "--read-only", "--tmpfs", "/tmp:size=256m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "256", "--memory", "1g", "--cpus", "1",
            "--user", "1000:1000", "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{self.workspace}:/workspace" + (":ro" if outputs else ""),
            *(["-v", f"{Path(outputs).resolve()}:/workspace/outputs"] if outputs else []),
            *[a for d in self.readonly for a in (
                "-v", f"{d}:/workspace/{d.relative_to(self.workspace)}:ro")],
            *[a for h, c in (mounts or {}).items() for a in ("-v", f"{Path(h).resolve()}:{c}:ro")], "-w", "/workspace",
            IMAGE, "sleep", "infinity"], check=True, capture_output=True)

    def exec(self, command, timeout=60):
        # Stream and keep only head + tail: capture_output would hold everything (e.g. `cat` of a 1 GB log) in the
        # server process before truncating, and the server is shared by every run.
        p = subprocess.Popen(["docker", "exec", self.name, "bash", "-lc", command],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        caps = [_Cap(p.stdout), _Cap(p.stderr)]
        for c in caps:
            c.start()
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "exec", self.name, "pkill", "-f", command[:50]], capture_output=True)
            p.kill()
            p.wait()
            return f"exit_code=124\n[timeout after {timeout}s]"
        finally:
            for c in caps:
                c.join(5)
        out, err = (c.text() for c in caps)
        out = (out + (("\n[stderr]\n" + err) if err else "")).strip()
        return f"exit_code={rc}\n{out if any(c.dropped for c in caps) else truncate(out)}"

    # Host-side tools (read_file, write_file, artifact file=, views) go through the container, never through the
    # host path: a process in the sandbox can swap a directory for a symlink between a host-side check and open
    # (measured: 972/3000 reads escaped). Inside the container, symlinks resolve in its own filesystem and :ro
    # mounts are enforced by the kernel.
    def cpath(self, rel):
        """Workspace-relative path -> absolute container path; refuses ../ escapes (lexically, no host access)."""
        norm = os.path.normpath("/workspace/" + str(rel).removeprefix("/workspace/").lstrip("/"))
        if norm != "/workspace" and not norm.startswith("/workspace/"):
            raise PermissionError(f"path escapes workspace: {rel}")
        return norm

    def read_bytes(self, rel, limit):
        p = subprocess.run(["docker", "exec", self.name, "sh", "-c", 'test -f "$1" && head -c "$2" -- "$1"', "_",
                            self.cpath(rel), str(limit + 1)], capture_output=True)
        if p.returncode:
            raise FileNotFoundError(f"no such file: {rel} {p.stderr.decode(errors='replace').strip()}".strip())
        if len(p.stdout) > limit:
            raise ValueError(f"{rel} is larger than {limit:,} bytes; use bash (head, grep, sed -n) on it instead")
        return p.stdout

    def write_bytes(self, rel, data):
        p = subprocess.run(["docker", "exec", "-i", self.name, "sh", "-c", 'mkdir -p "$(dirname "$1")" && cat > "$1"',
                            "_", self.cpath(rel)], input=data, capture_output=True)
        if p.returncode:
            raise PermissionError(f"cannot write {rel}: {p.stderr.decode(errors='replace').strip()}")

    def copy_out(self, rel, dest, limit):
        """Stream a workspace file to a host file (for server-side parsing), at most `limit` bytes."""
        with open(dest, "wb") as fh:
            p = subprocess.run(["docker", "exec", self.name, "sh", "-c", 'test -f "$1" && head -c "$2" -- "$1"', "_",
                                self.cpath(rel), str(limit + 1)], stdout=fh, stderr=subprocess.PIPE)
        if p.returncode:
            raise FileNotFoundError(f"no such file: {rel}")
        if os.path.getsize(dest) > limit:
            raise ValueError(f"{rel} is larger than {limit:,} bytes")
        return dest

    def path(self, rel):
        """Host path of a workspace dir/file, for trusted host code. Use it only inside frozen(): while the container
        runs it can swap any directory for a symlink between this check and the use."""
        p = (self.workspace / rel.removeprefix("/workspace/").lstrip("/")).resolve()
        if p != self.workspace and self.workspace not in p.parents:
            raise PermissionError(f"path escapes workspace: {rel}")
        return p

    @contextlib.contextmanager
    def frozen(self):
        """Pause every process in the sandbox while host code (git on the work tree) runs."""
        subprocess.run(["docker", "pause", self.name], check=True, capture_output=True)
        try:
            yield
        finally:
            subprocess.run(["docker", "unpause", self.name], capture_output=True)

    def fresh(self, outputs):
        """A new container over the same workspace and mounts, never with network: for code that must not share
        processes or /tmp with this one (connected agents). The workspace is read-only (no json.py planted for a
        later python here to import); `outputs`, a host dir only the caller controls, is its /workspace/outputs.
        Never this workspace's own outputs/: the run can swap that for a symlink, and a host-side bind mount of
        it would follow the link to any host directory. Named after this sandbox so a restart removes it."""
        self.exec("mkdir -p /workspace/outputs", timeout=10)  # the mountpoint, made inside the container
        return Sandbox(self.workspace, name=f"{self.name[4:]}-a{uuid.uuid4().hex[:8]}", outputs=outputs, **self._fresh)

    def close(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
