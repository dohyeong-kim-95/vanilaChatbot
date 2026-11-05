"""Isolated execution: one hardened Docker container per session, workspace bind-mounted."""
import collections
import contextlib
import hashlib
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

# Our analysis image (sandbox/Dockerfile: polars, duckdb, pyarrow). Its tag is the Dockerfile's hash, so a changed
# Dockerfile means a new image (deploy/install.sh builds it; locally: docker build -t <IMAGE> sandbox/).
_DOCKERFILE = Path(__file__).resolve().parents[1] / "sandbox" / "Dockerfile"
# what goes into the image, in this order (deploy/install.sh hashes the same files the same way)
_IMAGE_SOURCES = [_DOCKERFILE, _DOCKERFILE.parent / "stdf-rs" / "Cargo.toml", _DOCKERFILE.parent / "stdf-rs" / "Cargo.lock",
                  *sorted((_DOCKERFILE.parent / "stdf-rs" / "src").glob("*.rs"), key=lambda p: p.name.encode())]  # C order
DEFAULT_IMAGE = "vanilachatbot/sandbox:" + hashlib.sha256(b"".join(p.read_bytes() for p in _IMAGE_SOURCES)).hexdigest()[:12]
IMAGE = os.environ.get("SANDBOX_IMAGE") or DEFAULT_IMAGE
VTOOLS = _DOCKERFILE.parent / "vtools"  # our scripts for big logs, mounted read-only at /vtools
MAX_OUT = 10_000  # chars; head+tail kept beyond this (mini-swe-agent style)
# The sandbox runs as the server's own (non-root) user, so the server can read and remove what it writes and a
# server account that is not uid 1000 still works. A root server keeps uid 1000 for the sandbox.
UID, GID = (os.getuid(), os.getgid()) if os.getuid() else (1000, 1000)
OWNER = f"{UID}:{GID}"
AGENT_OUTPUTS = f"size=128m,nr_inodes=4096,uid={UID},gid={GID},mode=0755"  # counts against the container's memory


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
            self.feed(chunk)

    def feed(self, chunk):
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


class Slots:
    """At most n long jobs at once on this server (each takes a CPU), first come first served. A waiting job hears
    how many are ahead of it."""
    def __init__(self, n):
        self.n, self.running, self.queue, self.cv = n, 0, collections.deque(), threading.Condition()

    def acquire(self, cancel=None, waiting=lambda ahead: None):
        ticket = object()
        with self.cv:
            self.queue.append(ticket)
            try:
                last = None
                while self.queue[0] is not ticket or self.running >= self.n:
                    ahead = self.queue.index(ticket)  # waiting before this one (0: next when a slot frees)
                    if ahead != last:
                        last = ahead
                        self.cv.release()  # the callback writes an event: not while holding up every other job
                        try:
                            waiting(ahead)
                        finally:
                            self.cv.acquire()
                        continue
                    if cancel is not None and cancel.is_set():
                        return False
                    self.cv.wait(0.5)
                self.running += 1
                return True
            finally:
                self.queue.remove(ticket)
                self.cv.notify_all()

    def release(self):
        with self.cv:
            self.running -= 1
            self.cv.notify_all()


JOBS = Slots(int(os.environ.get("AGENT_JOB_SLOTS", max(1, (os.cpu_count() or 2) - 1))))
JOB_MAX = 3600  # seconds, one long job
PROGRESS = re.compile(r"^PROGRESS (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)(?: (.*))?$")  # "PROGRESS done total [note]"


class Sandbox:
    def __init__(self, workspace, readonly=(), name=None, mounts=None, agent=False, rw_mounts=None):
        """No network. readonly: workspace subdirs mounted read-only.
        mounts: {host dir: container path}, read-only, outside /workspace (e.g. skills).
        rw_mounts: {host dir: container path}, read-write (the project's cache). Never given to fresh().
        agent: workspace read-only, /workspace/outputs an empty tmpfs (fresh())."""
        self.workspace = Path(workspace).resolve()
        self.readonly = [self.workspace / d for d in readonly]
        self._fresh = dict(readonly=readonly, mounts=mounts)
        self.workspace.mkdir(parents=True, exist_ok=True)
        os.chown(self.workspace, UID, GID)
        self.name = "sbx-" + (name or uuid.uuid4().hex[:10])
        # orphans of a crashed server: this container and the agent containers made from it (fresh())
        old = subprocess.run(["docker", "ps", "-aq", "--filter", f"name=^{re.escape(self.name)}(-a[0-9a-f]{{8}})?$"],
                             capture_output=True, text=True).stdout.split()
        subprocess.run(["docker", "rm", "-f", self.name, *old], capture_output=True)
        subprocess.run([
            "docker", "run", "-d", "--rm", "--name", self.name,
            # an agent's /tmp is exec: write_code runs the programs it builds there (codegen.py, Loop 40)
            "--network", "none", "--read-only", "--tmpfs", "/tmp:size=256m" + (",exec" if agent else ""),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "256", "--memory", "1g", "--cpus", "1",
            "--user", OWNER, "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{self.workspace}:/workspace" + (":ro" if agent else ""),
            *(["--tmpfs", f"/workspace/outputs:{AGENT_OUTPUTS}"] if agent else []),
            *[a for d in self.readonly for a in (
                "-v", f"{d}:/workspace/{d.relative_to(self.workspace)}:ro")],
            *[a for h, c in (mounts or {}).items() for a in ("-v", f"{Path(h).resolve()}:{c}:ro")],
            *[a for h, c in (rw_mounts or {}).items() for a in ("-v", f"{Path(h).resolve()}:{c}")], "-w", "/workspace",
            IMAGE, "sleep", "infinity"], check=True, capture_output=True)

    def exec(self, command, timeout=60):
        t0 = time.monotonic()
        # Stream and keep only head + tail: capture_output would hold everything (e.g. `cat` of a 1 GB log) in the
        # server process before truncating, and the server is shared by every run.
        # The time limit is kept inside the container: killing the docker exec client leaves the command running
        # (Loop 38: pkill -f on the command text missed most of them). timeout(1) signals its whole process group,
        # KILL 5 s after TERM.
        p = subprocess.Popen(["docker", "exec", self.name, "timeout", "-k", "5", str(timeout), "bash", "-lc", command],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        caps = [_Cap(p.stdout), _Cap(p.stderr)]
        for c in caps:
            c.start()
        try:
            rc = p.wait(timeout=timeout + 15)
        except subprocess.TimeoutExpired:  # docker itself hangs
            p.kill()
            p.wait()
            rc = 124
        for c in caps:
            c.join(5)
        if rc in (124, 137) and time.monotonic() - t0 >= timeout:
            return f"exit_code=124\n[timeout after {timeout}s]"
        out, err = (c.text() for c in caps)
        out = (out + (("\n[stderr]\n" + err) if err else "")).strip()
        return f"exit_code={rc}\n{out if any(c.dropped for c in caps) else truncate(out)}"

    def job(self, command, timeout=JOB_MAX, progress=lambda done, total, note: None, waiting=lambda ahead: None,
            cancel=None, started=lambda: None):
        """A long command (minutes): waits for a free slot (JOBS), runs in its own process group, reports each
        `PROGRESS done total [note]` line it prints, stops on cancel or timeout. Returns like exec()."""
        if not JOBS.acquire(cancel, waiting):
            return "exit_code=130\n[cancelled while waiting for a free slot]"
        jid = uuid.uuid4().hex[:8]
        pidf = f"/tmp/job-{jid}.pid"
        try:
            started()
            # setsid: the job and everything it starts form one group, killed together (kill -- -pgid). -w: setsid
            # forks when it already leads a group, and without -w docker exec would end (and lose the output) at once
            p = subprocess.Popen(["docker", "exec", self.name, "setsid", "-w", "bash", "-c", 'echo $$ > "$1"; exec bash -lc "$2"',
                                  "_", pidf, command], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            err = _Cap(p.stderr)
            err.start()
            lines, out = queue.Queue(), _Cap(None)

            def read():
                for raw in iter(lambda: p.stdout.readline(65536), b""):  # a line without \n is cut, not held
                    lines.put(raw)
                lines.put(None)
            threading.Thread(target=read, daemon=True).start()
            start = time.time()
            deadline, stopped, last, held = start + timeout, None, 0.0, None
            while True:
                try:
                    raw = lines.get(timeout=0.5)
                except queue.Empty:
                    raw = ""
                if raw is None:
                    break
                if raw:
                    m = PROGRESS.match(raw.decode(errors="replace").strip())
                    if m:
                        held = (float(m[1]), float(m[2]), (m[3] or "")[:200])
                        if time.time() - last >= 2:  # each report is an event row: a tight loop must not flood them
                            last = time.time()
                            progress(*held)
                            held = None
                    else:
                        out.feed(raw)
                if stopped is None and (time.time() > deadline or (cancel is not None and cancel.is_set())):
                    stopped = "timeout" if time.time() > deadline else "cancelled"
                    subprocess.run(["docker", "exec", self.name, "bash", "-c", 'kill -TERM -- -"$(cat "$1")"', "_", pidf],
                                   capture_output=True)
                    deadline = time.time() + 5  # then hard
                if stopped and time.time() > deadline:
                    subprocess.run(["docker", "exec", self.name, "bash", "-c", 'kill -KILL -- -"$(cat "$1")"', "_", pidf],
                                   capture_output=True)
                    p.kill()
                    break
            rc = p.wait()
            err.join(5)
            if held and not stopped:  # the last report (often 100%) came within 2 s of the one before
                progress(*held)
            text = (out.text() + (("\n[stderr]\n" + err.text()) if err.total else "")).strip()
            if stopped:
                return f"exit_code={124 if stopped == 'timeout' else 130}\n[{stopped} after {int(time.time() - start)}s]\n{text}"
            return f"exit_code={rc}\n{text}"
        finally:
            subprocess.run(["docker", "exec", self.name, "rm", "-f", pidf], capture_output=True)
            JOBS.release()

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

    def _head(self, rel, limit):  # one byte more than the limit, to tell "too big"
        return ["docker", "exec", self.name, "sh", "-c", 'test -f "$1" && head -c "$2" -- "$1"', "_", self.cpath(rel),
                str(limit + 1)]

    def read_bytes(self, rel, limit):
        p = subprocess.run(self._head(rel, limit), capture_output=True)
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
            p = subprocess.run(self._head(rel, limit), stdout=fh, stderr=subprocess.PIPE)
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

    def fresh(self):
        """A new container over the same workspace and mounts, never with network: for code that must not share
        processes or /tmp with this one (connected agents). The workspace is read-only (no json.py planted for a
        later python here to import) and its /workspace/outputs is a tmpfs in memory, filled and emptied by the
        caller through tar streams (delegate.py). Never a bind mount of this workspace's outputs/: the run can
        swap that for a symlink, and a host-side mount would follow it to any host directory. Nothing of the
        agent's lands on the host. Named after this sandbox so a restart removes it."""
        self.exec("mkdir -p /workspace/outputs", timeout=10)  # the mountpoint, made inside the container
        return Sandbox(self.workspace, name=f"{self.name[4:]}-a{uuid.uuid4().hex[:8]}", agent=True, **self._fresh)

    def close(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
