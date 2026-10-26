"""Predefined tool registry. Each tool = OpenAI function schema + python impl.
Tools flagged `approval=True` are outward-facing and need a human yes before running."""
import datetime
import os
import json

from . import github

REGISTRY = {}


def tool(description, params, required=None, approval=False):
    def deco(fn):
        REGISTRY[fn.__name__] = {
            "fn": fn, "approval": approval,
            "schema": {"type": "function", "function": {
                "name": fn.__name__, "description": description,
                "parameters": {"type": "object", "properties": params,
                               "required": required if required is not None else list(params)}}}}
        return fn
    return deco


S = {"type": "string"}


@tool("Run a bash command in the isolated sandbox (cwd=/workspace, no network). Returns exit code + output.",
      {"command": S, "timeout": {"type": "integer"}}, required=["command"])
def bash(ctx, command, timeout=60):
    return ctx.sandbox.exec(command, min(int(timeout), 300))


@tool("Read a UTF-8 text file in the workspace, with line numbers.",
      {"path": S, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, required=["path"])
def read_file(ctx, path, offset=1, limit=400):
    lines = ctx.sandbox.path(path).read_text(encoding="utf-8").splitlines()
    ctx.read_files.add(ctx.sandbox.path(path))
    sel = lines[offset - 1: offset - 1 + limit]
    more = f"\n[... {len(lines) - offset + 1 - len(sel)} more lines]" if len(lines) > offset - 1 + limit else ""
    return "\n".join(f"{i:>5}\t{l}" for i, l in enumerate(sel, offset)) + more


@tool("Create or overwrite a file in the workspace.", {"path": S, "content": S})
def write_file(ctx, path, content):
    p = ctx.sandbox.path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    os.chown(p, 1000, 1000)
    ctx.read_files.add(ctx.sandbox.path(path))
    return f"wrote {len(content)} chars to {path}"


@tool("Replace an exact, unique snippet in a file. Read the file first.", {"path": S, "old": S, "new": S})
def edit_file(ctx, path, old, new):
    if ctx.sandbox.path(path) not in ctx.read_files:
        raise ValueError("read_file this path before editing it")
    p = ctx.sandbox.path(path)
    text = p.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        raise ValueError(f"`old` must match exactly once, matched {n} times")
    p.write_text(text.replace(old, new), encoding="utf-8")
    return f"edited {path}"


@tool("Search the user's uploaded documents (knowledge base). Returns top passages with sources.",
      {"query": S, "k": {"type": "integer"}}, required=["query"])
def search_documents(ctx, query, k=4):
    return json.dumps(ctx.docs.search(query, k), ensure_ascii=False)


@tool("Get the current UTC date/time.", {}, required=[])
def current_time(ctx):
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@tool("git add -A and commit all workspace changes on a new or existing branch.", {"branch": S, "message": S})
def git_commit(ctx, branch, message):
    ws = ctx.sandbox.workspace
    rc, cur = github.git(ws, "rev-parse", "--abbrev-ref", "HEAD")
    if cur != branch:
        rc, out = github.git(ws, "checkout", "-B", branch)
    github.git(ws, "add", "-A")
    rc, out = github.git(ws, "-c", "user.name=agent", "-c", "user.email=agent@local", "commit", "-m", message)
    return out


@tool("Push the current branch and open a pull request. Requires human approval.",
      {"branch": S, "title": S, "body": S}, approval=True)
def open_pull_request(ctx, branch, title, body):
    rc, out = github.push(ctx.sandbox.workspace, branch, ctx.github_token, ctx.remote_url)
    if rc:
        raise RuntimeError(out)
    if not ctx.github_repo:  # local/bare remote (tests): push only
        return f"pushed {branch} (no GitHub repo configured, PR skipped)"
    owner, repo = ctx.github_repo.split("/")
    return github.create_pr(owner, repo, branch, ctx.base_branch, title, body, ctx.github_token)


def schemas(names=None):
    return [t["schema"] for n, t in REGISTRY.items() if names is None or n in names]
