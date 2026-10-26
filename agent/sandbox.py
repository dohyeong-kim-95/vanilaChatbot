"""Isolated execution: one hardened Docker container per session, workspace bind-mounted."""
import os
import subprocess
import uuid
from pathlib import Path

IMAGE = os.environ.get("SANDBOX_IMAGE", "python:3.12-slim")
MAX_OUT = 10_000  # chars; head+tail kept beyond this (mini-swe-agent style)


def truncate(s, limit=MAX_OUT):
    if len(s) <= limit:
        return s
    h = limit // 2
    return f"{s[:h]}\n\n[... {len(s) - limit} chars truncated; use a narrower command (head/grep) ...]\n\n{s[-h:]}"


class Sandbox:
    def __init__(self, workspace, network="none"):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        os.chown(self.workspace, 1000, 1000)  # sandbox runs as uid 1000
        self.name = "sbx-" + uuid.uuid4().hex[:10]
        subprocess.run([
            "docker", "run", "-d", "--rm", "--name", self.name,
            "--network", network, "--read-only", "--tmpfs", "/tmp:size=256m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "256", "--memory", "1g", "--cpus", "1",
            "--user", "1000:1000", "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{self.workspace}:/workspace", "-w", "/workspace",
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

    def path(self, rel):
        """Resolve a workspace-relative path; refuse escapes (../, absolute, symlinks out)."""
        p = (self.workspace / rel.removeprefix("/workspace/").lstrip("/")).resolve()
        if p != self.workspace and self.workspace not in p.parents:
            raise PermissionError(f"path escapes workspace: {rel}")
        return p

    def close(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
