"""Server-side git actions: the sandbox only edits + commits; push runs here, after human approval."""
import os
import subprocess

from .sandbox import OWNER


# The workspace is sandbox-writable, so .git/hooks and .git/config are untrusted:
# disable hooks/fsmonitor whenever the host runs git on it.
SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "safe.directory=*"]


def git(ws, *args):
    p = subprocess.run(["git", *SAFE, "-C", str(ws), *args], capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def git_wt(git_dir, work_tree, *args):
    """git whose metadata (config, hooks, filters, core.worktree) lives OUTSIDE the sandbox-writable tree.
    With `git -C <workspace>`, a planted .git/config can define a clean filter that `git add` runs on the host,
    or point core.worktree at host files. Here only the work tree is untrusted."""
    p = subprocess.run(["git", *SAFE, f"--git-dir={git_dir}", f"--work-tree={work_tree}", *args],
                       capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def clone(url, ws):
    subprocess.run(["git", "clone", "--depth", "50", url, str(ws)], check=True, capture_output=True)
    subprocess.run(["chown", "-R", OWNER, str(ws)], check=True)  # sandbox user owns the tree


def push(ws, branch, remote_url=None, git_dir=None):
    run = (lambda *a: git_wt(git_dir, ws, *a)) if git_dir else (lambda *a: git(ws, *a))
    url = remote_url or run("remote", "get-url", "origin")[1]
    return run("push", url, f"HEAD:refs/heads/{branch}")
