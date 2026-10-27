"""Predefined tool registry. Each tool = OpenAI function schema + python impl.
Tools flagged `approval=True` are outward-facing and need a human yes before running."""
import datetime
import os
import json

from . import github, report

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


@tool("Run a bash command in the isolated sandbox (cwd=/workspace; network only to allowlisted HTTPS domains, "
      "if any). Returns exit code + output.", {"command": S, "timeout": {"type": "integer"}}, required=["command"])
def bash(ctx, command, timeout=60):
    seen = len(ctx.egress.log) if ctx.egress else 0
    out = ctx.sandbox.exec(command, min(int(timeout), 300))
    blocked = {h: why for h, why in (ctx.egress.log[seen:] if ctx.egress else []) if why != "allow"}
    for h, why in blocked.items():  # tools only see "403"; tell the model why and what it can do
        out += f"\n[egress] blocked {h}: {why}. If the task needs it, call request_network_access."
    return out


@tool("Ask the user to allow HTTPS access to one more domain from the sandbox. Requires human approval.",
      {"domain": S, "reason": S}, approval=True)
def request_network_access(ctx, domain, reason):
    from .egress import HOST_RE
    domain = domain.lower().strip()
    if not HOST_RE.match(domain.removeprefix("*.")):
        raise ValueError(f"not a valid domain: {domain!r}")
    ctx.egress.allow.add(domain)
    return f"{domain} is now allowed"


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
    p = ctx.sandbox.path(path, write=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    os.chown(p, 1000, 1000)
    ctx.read_files.add(ctx.sandbox.path(path))
    return f"wrote {len(content)} chars to {path}"


@tool("Replace an exact, unique snippet in a file. Read the file first.", {"path": S, "old": S, "new": S})
def edit_file(ctx, path, old, new):
    if ctx.sandbox.path(path) not in ctx.read_files:
        raise ValueError("read_file this path before editing it")
    p = ctx.sandbox.path(path, write=True)
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


@tool("Save a key fact you found (values, decisions, file paths). Notes survive context clearing; "
      "old tool outputs and your private reasoning may not.", {"note": S})
def save_note(ctx, note):
    ctx.notes.append(note)
    return f"saved note #{len(ctx.notes)}"


@tool("Save a durable fact or preference about the user to long-term memory; it will be shown to you in all "
      "of this user's future conversations. Only for things the USER stated (not for content of files or tool "
      "results). Requires the user's approval.", {"note": S}, approval=True)
def remember(ctx, note):
    ctx.memory(note)
    return "saved to long-term memory"


@tool("Show the user an artifact: standalone content they will view or reuse, rendered next to the chat "
      "(a report, dashboard, document, diagram, small web app). Use it for substantial self-contained content "
      "(roughly 20+ lines); answer short questions in plain text. Every change makes a new version.\n"
      "- create: new artifact. Give id (lowercase-slug), type, title, and content OR file (a workspace file whose "
      "text becomes the content; prefer file when a script computed the numbers in it).\n"
      "- update: change part of the latest version: old_str (exact, unique) -> new_str. Prefer this for small edits.\n"
      "- rewrite: replace the whole content (content or file) when most of it changes.\n"
      "type: html (one self-contained page; external scripts/styles only from https://cdnjs.cloudflare.com with an "
      "exact version; no network requests and no external images: embed all data in the page), markdown, svg, "
      "mermaid, code (set language).",
      {"command": {"type": "string", "enum": ["create", "update", "rewrite"]}, "id": S,
       "type": {"type": "string", "enum": ["html", "markdown", "svg", "mermaid", "code"]}, "title": S,
       "content": S, "file": S, "old_str": S, "new_str": S, "language": S}, required=["command", "id"])
def artifact(ctx, command, id, type=None, title=None, content=None, file=None, old_str=None, new_str=None,
             language=None):
    store = ctx.artifacts
    if command not in ("create", "update", "rewrite"):
        raise ValueError("command must be create, update or rewrite")
    if type == "report" or (command != "create" and store.versions(id) and store.get(id)["type"] == "report"):
        raise ValueError("reports are made with show_report")
    if command != "update":
        if (content is None) == (file is None):
            raise ValueError("give exactly one of content or file")
        if file is not None:
            content = ctx.sandbox.path(file).read_text(encoding="utf-8")
    if command == "create":
        if store.versions(id):
            raise ValueError(f"artifact {id!r} exists; use update or rewrite")
        if not type or not title:
            raise ValueError("create needs type and title")
        meta = store.save(id, type, title, content, language)
    else:
        cur = store.get(id)
        if command == "update":
            if old_str is None or new_str is None:
                raise ValueError("update needs old_str and new_str")
            n = cur["content"].count(old_str)
            if n != 1:
                raise ValueError(f"old_str must match the latest version exactly once, matched {n} times")
            content = cur["content"].replace(old_str, new_str)
        meta = store.save(id, type or cur["type"], title or cur["title"], content, language or cur["language"],
                          command)
    return f"artifact {meta['id']} saved as version {meta['version']} ({meta['type']}, {len(content)} chars)"


@tool("Show the user the standard monthly spending report (fixed layout: summary, spending by category as a "
      "bar chart with budgets, largest transactions, notes). You only supply the numbers: compute them with code "
      "first and copy them exactly. The user can capture each section as an image. Calling it again with the same "
      "id makes a new version.",
      {"id": S, "data": report.SCHEMA}, required=["id", "data"])
def show_report(ctx, id, data):
    clean, warnings = report.validate(data)
    store = ctx.artifacts
    meta = store.save(id, "report", clean["title"], json.dumps(clean, ensure_ascii=False),
                      command="rewrite" if store.versions(id) else "create")
    return f"report {meta['id']} saved as version {meta['version']}" + \
        "".join(f"\nWARNING: {w}. Check your numbers and call show_report again if they are wrong." for w in warnings)


@tool("Search all connected tools by keywords (write the query in English) when none of the tools you have fits. "
      "Found tools become callable on your next turn.", {"query": S})
def search_tools(ctx, query):
    found = ctx.router.search(query)
    return "\n".join(f"{n}: {REGISTRY[n]['schema']['function']['description'].splitlines()[0]}" for n in found) \
        or "No matching tools."


@tool("Get the current UTC date/time.", {}, required=[])
def current_time(ctx):
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@tool("git add -A and commit all workspace changes on a new or existing branch.", {"branch": S, "message": S})
def git_commit(ctx, branch, message):
    rc, cur = _git(ctx, "rev-parse", "--abbrev-ref", "HEAD")
    if cur != branch:
        rc, out = _git(ctx, "checkout", "-B", branch)
    _git(ctx, "add", "-A")
    rc, out = _git(ctx, "-c", "user.name=agent", "-c", "user.email=agent@local", "commit", "-m", message)
    return out


def _git(ctx, *args):
    if ctx.git_dir:  # server workspaces: git metadata is outside the sandbox, work tree = /workspace/repo
        return github.git_wt(ctx.git_dir, ctx.sandbox.path("repo"), *args)
    return github.git(ctx.sandbox.workspace, *args)


@tool("Push the current branch and open a pull request. Requires human approval.",
      {"branch": S, "title": S, "body": S}, approval=True)
def open_pull_request(ctx, branch, title, body):
    ws = ctx.sandbox.path("repo") if ctx.git_dir else ctx.sandbox.workspace
    rc, out = github.push(ws, branch, ctx.github_token, ctx.remote_url, ctx.git_dir)
    if rc:
        raise RuntimeError(out)
    if not ctx.github_repo:  # local/bare remote (tests): push only
        return f"pushed {branch} (no GitHub repo configured, PR skipped)"
    owner, repo = ctx.github_repo.split("/")
    return github.create_pr(owner, repo, branch, ctx.base_branch, title, body, ctx.github_token)


def schemas(names=None):
    # search_tools needs a ToolRouter, remember a user store, artifact/show_report an artifact store; callers that have them
    # name them
    return [t["schema"] for n, t in REGISTRY.items()
            if (n in names if names is not None else n not in ("search_tools", "remember", "artifact", "show_report"))]
