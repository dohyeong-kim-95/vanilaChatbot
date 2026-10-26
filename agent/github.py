"""Server-side git/GitHub actions. The token never enters the sandbox:
the sandbox only edits + commits; push and PR run here, after human approval."""
import json
import os
import subprocess
import urllib.request


# The workspace is sandbox-writable, so .git/hooks and .git/config are untrusted:
# disable hooks/fsmonitor whenever the host runs git on it.
SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "safe.directory=*"]


def git(ws, *args):
    p = subprocess.run(["git", *SAFE, "-C", str(ws), *args], capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def clone(url, ws, token=None):
    if token and url.startswith("https://"):
        url = url.replace("https://", f"https://x-access-token:{token}@", 1)
    subprocess.run(["git", "clone", "--depth", "50", url, str(ws)], check=True, capture_output=True)
    # strip credentials from the stored remote so the sandbox cannot read them
    clean = url.split("@", 1)[-1] if token else url
    git(ws, "remote", "set-url", "origin", clean if "://" in clean or clean.startswith("/") else "https://" + clean)
    subprocess.run(["chown", "-R", "1000:1000", str(ws)], check=True)  # sandbox user owns the tree


def push(ws, branch, token=None, remote_url=None):
    url = remote_url or git(ws, "remote", "get-url", "origin")[1]
    if token and url.startswith("https://"):
        url = url.replace("https://", f"https://x-access-token:{token}@", 1)
    return git(ws, "push", url, f"HEAD:refs/heads/{branch}")


def create_pr(owner, repo, head, base, title, body, token):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/pulls", method="POST",
        data=json.dumps({"title": title, "head": head, "base": base, "body": body}).encode(),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["html_url"]
