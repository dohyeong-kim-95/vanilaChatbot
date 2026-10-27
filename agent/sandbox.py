"""Isolated execution: one hardened Docker container per session, workspace bind-mounted."""
import os
import subprocess
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


class Sandbox:
    def __init__(self, workspace, network="none", egress=None, readonly=(), name=None):
        """egress: an EgressProxy -> internal network whose only way out is that proxy's allowlist.
        readonly: workspace subdirs mounted read-only (and refused by host-side write tools too)."""
        self.workspace = Path(workspace).resolve()
        self.readonly = [self.workspace / d for d in readonly]
        self.workspace.mkdir(parents=True, exist_ok=True)
        os.chown(self.workspace, 1000, 1000)  # sandbox runs as uid 1000
        self.name = "sbx-" + (name or uuid.uuid4().hex[:10])
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)  # orphan of a crashed server
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
            "-v", f"{self.workspace}:/workspace", *[a for d in self.readonly for a in (
                "-v", f"{d}:/workspace/{d.relative_to(self.workspace)}:ro")], "-w", "/workspace",
            IMAGE, "sleep", "infinity"], check=True, capture_output=True)

    def exec(self, command, timeout=60):
        try:
            p = subprocess.run(["docker", "exec", self.name, "bash", "-lc", command],
                               capture_output=True, text=True, timeout=timeout)
            out = (p.stdout + (("\n[stderr]\n" + p.stderr) if p.stderr else "")).strip()
            return f"exit_code={p.returncode}\n{truncate(out)}"
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "exec", self.name, "pkill", "-f", command[:50]], capture_output=True)
            return f"exit_code=124\n[timeout after {timeout}s]"

    def path(self, rel, write=False):
        """Resolve a workspace-relative path; refuse escapes (../, absolute, symlinks out). write=True also
        refuses read-only mounts: host-side tools write to the host directory, bypassing the container's :ro."""
        p = (self.workspace / rel.removeprefix("/workspace/").lstrip("/")).resolve()
        if p != self.workspace and self.workspace not in p.parents:
            raise PermissionError(f"path escapes workspace: {rel}")
        if write and any(p == d or d in p.parents for d in self.readonly):
            raise PermissionError(f"read-only: {rel}")
        return p

    def close(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
