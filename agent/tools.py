"""Predefined tool registry. Each tool = OpenAI function schema + python impl.
Tools flagged `approval=True` are outward-facing and need a human yes before running.
Tools flagged `ask=True` need a human answer (a string), passed to the impl as `answer`."""
import contextlib
import datetime
import json
import os
import re
import shlex
import uuid
import tempfile
from pathlib import Path

from . import codegen, dashboard, delegate, github, logview, report, shmoo
from .artifacts import MAX_SIZE as ARTIFACT_MAX
from .sandbox import JOB_MAX
from .userstore import DISK_RESERVE, MAX_OUTPUTS

CACHE_MAX = int(os.environ.get("AGENT_CACHE_MAX", 20 * 2**30))  # one project's /workspace/cache

REGISTRY = {}


def _schema(name, description, params, required):
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": params, "required": required}}}


def tool(description, params, required=None, approval=False, ask=False):
    def deco(fn):
        REGISTRY[fn.__name__] = {"fn": fn, "approval": approval, "ask": ask,
                                 "schema": _schema(fn.__name__, description, params,
                                                   required if required is not None else list(params))}
        return fn
    return deco


S = {"type": "string"}
READ_MAX = 20 * 1024 * 1024  # read_file / edit_file: bigger files are for bash (head, sed -n, grep)
# shortcut: show_shmoo/show_logview parse in the shared server process (64 MB took 9.5 s, 319 MB RSS); 16 MB keeps
# that small. Move parsing into the sandbox (logs.py --json) when users need bigger logs.
VIEW_MAX = 16 * 1024 * 1024


@contextlib.contextmanager
def _host_copy(ctx, rel):
    """A private host copy of a workspace file (streamed through the container, capped) for server-side parsers."""
    with tempfile.TemporaryDirectory(prefix="view-") as d:
        try:
            p = ctx.sandbox.copy_out(rel, Path(d) / "data", VIEW_MAX)
        except ValueError as e:  # the way on for a big log (Loop 38)
            raise ValueError(f"{e}: too big for this view. For a big log use ingest_log (format text, a pattern) "
                             "and then log_query / show_dashboard") from None
        yield p
TEMPLATES = {"report": "show_report", "shmoo": "show_shmoo", "logview": "show_logview", "dashboard": "show_dashboard"}  # our pages, data only


@tool("Run a bash command in the isolated sandbox (cwd=/workspace, no network). "
      "Returns exit code + output; timeout 1-300 s.", {"command": S, "timeout": {"type": "integer"}}, required=["command"])
def bash(ctx, command, timeout=60):
    return ctx.sandbox.exec(command, max(1, min(int(timeout), 300)))  # timeout(1) reads 0 as no limit


@tool("Run a long bash command in the sandbox (minutes, up to 60): processing big files, building a cache. It "
      "waits for a free slot on the server (the user sees the queue). Print lines 'PROGRESS <done> <total> [note]' "
      "(e.g. bytes read so far and the file size) and the user sees the progress. Other output comes back like bash "
      "(head and tail): write results to files (e.g. /workspace/cache) instead of printing them. Use bash for anything "
      "that takes less than a minute.",
      {"command": S, "label": {"type": "string", "description": "what it does, a few words in the user's language"},
       "timeout_min": {"type": "integer", "description": "1-60, default 60"}}, required=["command", "label"])
def run_long(ctx, command, label, timeout_min=60):
    label = " ".join(str(label).split())[:60] or "작업"
    minutes = max(1, min(int(timeout_min), JOB_MAX // 60))
    # the cache persists and the disk is shared: no new long job over the limit or on a nearly full disk
    du = ctx.sandbox.exec("du -sb /workspace/cache 2>/dev/null | cut -f1; df -B1 --output=avail /workspace/cache | tail -1",
                          timeout=60).split("\n")[1:]
    used, free = (int(x) if x.strip().isdigit() else 0 for x in (du + ["", ""])[:2])
    if used > CACHE_MAX:
        raise ValueError(f"/workspace/cache holds {used:,} bytes (limit {CACHE_MAX:,}); remove what is not needed "
                         "or ask the user to clear the cache")
    if free and free < DISK_RESERVE:
        raise ValueError("the server's disk is nearly full; ask the user to free space")
    say = lambda **ev: ctx.emit({"type": "job", "label": label, **ev})  # noqa: E731
    return ctx.sandbox.job(command, timeout=minutes * 60, cancel=ctx.cancel,
                           progress=lambda done, total, note: say(done=done, total=total, note=note),
                           waiting=lambda ahead: say(ahead=ahead), started=lambda: say(state="running"))


DATASET = {"type": "string", "description": "name of the dataset in /workspace/cache: lowercase letters, digits, _"}


@tool("Turn big log files into a Parquet dataset in /workspace/cache/<dataset>/ for fast SQL (log_query), as a long "
      "job with progress. Do this first for files of more than a few MB instead of reading them yourself; a dataset "
      "whose files did not change is reused. format 'stdf': STDF V4 test data -> tables parts (lot, wafer, head, "
      "site, part, part_id, x, y, hard_bin, soft_bin, passed, part_flg, test_t, file), results (part, test_num, "
      "result, failed, test_flg, file; one row per parametric test; join to parts on (file, part)), tests (test_num, "
      "name, lo, hi, units). A die tested again (part_flg & 3 != 0, a retest) appears more than once: for yield keep "
      "the last part per (file, wafer, x, y). format 'text': one row per line "
      "matched by `pattern`, a regex with named groups (Python syntax, no lookaround or backreferences) as the "
      "columns; look at the first lines (head) to write "
      "it; time_column + time_format (e.g. %Y-%m-%dT%H:%M:%S%.3f) parse a timestamp column.",
      {"paths": {"type": "array", "items": S, "description": "files under /workspace (e.g. uploads/logs/a.stdf)"},
       "format": {"type": "string", "enum": ["stdf", "text"]}, "dataset": DATASET, "pattern": S,
       "time_column": S, "time_format": S}, required=["paths", "format", "dataset"])
def ingest_log(ctx, paths, format, dataset, pattern=None, time_column=None, time_format=None):
    if not isinstance(paths, list) or not paths or len(paths) > 200:
        raise ValueError("paths: 1-200 files")
    if format not in ("stdf", "text") or not re.match(r"^[a-z][a-z0-9_]{0,40}$", str(dataset)):
        raise ValueError("format: stdf or text; dataset: lowercase letters, digits and _, starting with a letter")
    files = [ctx.sandbox.cpath(p) for p in paths]  # refuses ../ escapes
    cmd = ["python", "/vtools/ingest.py", format, dataset, *files]
    if pattern:
        cmd += [f"--pattern={pattern}"]  # = : a pattern starting with - is not an option
    if time_column:
        cmd += [f"--time={time_column}"] + ([f"--time-format={time_format}"] if time_format else [])
    return run_long(ctx, " ".join(shlex.quote(c) for c in cmd), f"로그 변환: {dataset}")


@tool("Run SQL (DuckDB) over the datasets made by ingest_log: every dataset is a schema, every table a view "
      "(SELECT ... FROM <dataset>.parts). Aggregate in SQL; at most max_rows rows come back. sql='' lists the "
      "datasets, tables, columns and row counts.",
      {"sql": S, "max_rows": {"type": "integer", "description": "1-1000, default 200"}}, required=["sql"])
def log_query(ctx, sql, max_rows=200):
    n = max(1, min(int(max_rows), 1000))
    if not sql.strip():
        return ctx.sandbox.exec("python /vtools/query.py --tables", timeout=120)
    return ctx.sandbox.exec(f"python /vtools/query.py --max-rows {n} {shlex.quote(sql)}", timeout=300)


@tool("Show the user a dashboard of charts over the datasets made by ingest_log. Each chart is a kind and the SQL "
      "(as for log_query) that gives its data; the server runs the SQL and draws it in a fixed layout. kinds: bar "
      "(x = a category column, y = a number column), line (x = number or time, y, optional series column; ORDER BY "
      "x), scatter (x, y), wafer_map (x, y = die coordinates of one wafer, value = a number or a category such as "
      "hard_bin), table. Aggregate in SQL: at most 60 rows for bar, 5000 for line/scatter, 20000 for wafer_map, 100 "
      "for table. Calling it again with the same id makes a new version.",
      {"id": S, "title": S, "charts": {"type": "array", "items": {"type": "object", "properties": {
          "title": S, "kind": {"type": "string", "enum": sorted(dashboard.KINDS)}, "sql": S, "x": S, "y": S,
          "value": S, "series": S, "note": {"type": "string", "description": "one line under the chart title"}},
          "required": ["kind", "sql"]}}})
def show_dashboard(ctx, id, title, charts):
    title, specs = dashboard.validate_spec(title, charts)
    ctx.sandbox.exec("mkdir -p /workspace/cache/.tmp", timeout=30)
    sizes = []
    for c in specs:
        if ctx.cancel.is_set():
            raise ValueError("cancelled by the user")
        f = f"cache/.tmp/dash-{uuid.uuid4().hex[:8]}.json"
        try:
            out = ctx.sandbox.exec(f"python /vtools/query.py --max-rows {dashboard.MAX_ROWS[c['kind']]} "
                                   f"--json /workspace/{f} {shlex.quote(c['sql'])}", timeout=300)
            if not out.startswith("exit_code=0"):
                raise ValueError(f"chart {c['title']!r}: the SQL failed: {out[-1500:]}")
            try:
                r = json.loads(ctx.sandbox.read_bytes(f, 8 * 2**20))
            except FileNotFoundError:
                raise ValueError(f"chart {c['title']!r}: the SQL gave no rows to draw (a SELECT is needed)")
        finally:
            ctx.sandbox.exec(f"rm -f /workspace/{f}", timeout=30)
        if not (isinstance(r, dict) and isinstance(r.get("columns"), list) and isinstance(r.get("rows"), list)):
            raise ValueError(f"chart {c['title']!r}: unreadable result")
        for k in ("x", "y", "value", "series"):
            if k in c and c[k] not in r["columns"]:
                raise ValueError(f"chart {c['title']!r}: {k}={c[k]!r} is not a column of its result {r['columns']}")
        keep = list(range(len(r["columns"]))) if c["kind"] == "table" else \
            [r["columns"].index(c[k]) for k in ("x", "y", "value", "series") if k in c]  # only what is drawn
        c.update(columns=[r["columns"][i] for i in keep], rows=[[row[i] for i in keep] for row in r["rows"]],
                 truncated=bool(r.get("truncated")))
        sizes.append(len(json.dumps(c["rows"], ensure_ascii=False)))
    if sum(sizes) > ARTIFACT_MAX - 64 * 1024:
        raise ValueError("the dashboard's data is too big for one page (" + ", ".join(
            f"{c['title']}: {n // 1024} KB" for c, n in zip(specs, sizes)) + f"; limit about {ARTIFACT_MAX // 1024} KB): "
            "aggregate more or split it")
    meta = _save_view(ctx, id, "dashboard", title, {"title": title, "charts": specs})
    return f"dashboard {meta['id']} saved as version {meta['version']}: " + "; ".join(
        f"{c['title']}: {len(c['rows'])} rows" + (" (cut: aggregate more)" if c["truncated"] else "") for c in specs) \
        + cite_hint(meta, [(f"c{i + 1}", c["title"]) for i, c in enumerate(specs)])


@tool("Export query results for Spotfire: one SBDF file (Spotfire data file) per table, plus an Automation Services "
      "job outline and a README, in /workspace/outputs/spotfire/<name>/ (the user downloads it). A .dxp analysis "
      "cannot be made without Spotfire: if the user uploaded their analysis template (.dxp), pass it as template "
      "and it goes into the bundle. Tables are SQL over the datasets (as for log_query); aggregate to what the "
      "analysis needs (at most 1 million rows per table; the whole bundle must fit in what is left of the outputs, "
      "50 MB by default).",
      {"name": S, "tables": {"type": "array", "items": {"type": "object", "properties": {"name": S, "sql": S},
                                                         "required": ["name", "sql"]}},
       "template": {"type": "string", "description": "optional: a .dxp under /workspace/uploads"}},
      required=["name", "tables"])
def export_spotfire(ctx, name, tables, template=None):
    if not isinstance(tables, list) or not 1 <= len(tables) <= 20 or not all(isinstance(t, dict) for t in tables):
        raise ValueError("tables: 1-20 {name, sql}")
    # what is left for outputs (MAX_OUTPUTS in all; a file past it is not saved): a bundle cut short is no use
    du = ctx.sandbox.exec("du -sb /workspace/outputs | cut -f1", timeout=60).split("\n")[-1].strip()
    budget = MAX_OUTPUTS - (int(du) if du.isdigit() else 0)
    if budget <= 0:
        raise ValueError(f"/workspace/outputs is full ({MAX_OUTPUTS:,} bytes): remove something first")
    cmd = ["python", "/vtools/export.py", str(name), f"--budget={budget}"] + \
        [f"--table={t.get('name')}={t.get('sql')}" for t in tables]
    if template:
        cmd.append(f"--template={ctx.sandbox.cpath(template)}")
    return run_long(ctx, " ".join(shlex.quote(c) for c in cmd), f"Spotfire 내보내기: {name}")


@tool("Read a UTF-8 text file in the workspace, with line numbers.",
      {"path": S, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, required=["path"])
def read_file(ctx, path, offset=1, limit=400):
    offset = max(1, int(offset))  # 1-based; a 0 from the model would slice from the end
    lines = ctx.sandbox.read_bytes(path, READ_MAX).decode("utf-8").splitlines()
    ctx.read_files.add(ctx.sandbox.cpath(path))
    sel = lines[offset - 1: offset - 1 + limit]
    more = f"\n[... {len(lines) - offset + 1 - len(sel)} more lines]" if len(lines) > offset - 1 + limit else ""
    return "\n".join(f"{i:>5}\t{l}" for i, l in enumerate(sel, offset)) + more


@tool("Create or overwrite a file in the workspace.", {"path": S, "content": S})
def write_file(ctx, path, content):
    ctx.sandbox.write_bytes(path, content.encode("utf-8"))
    ctx.read_files.add(ctx.sandbox.cpath(path))
    return f"wrote {len(content)} chars to {path}"


@tool("Replace an exact, unique snippet in a file. Read the file first.", {"path": S, "old": S, "new": S})
def edit_file(ctx, path, old, new):
    if ctx.sandbox.cpath(path) not in ctx.read_files:
        raise ValueError("read_file this path before editing it")
    text = ctx.sandbox.read_bytes(path, READ_MAX).decode("utf-8")
    n = text.count(old)
    if n != 1:
        raise ValueError(f"`old` must match exactly once, matched {n} times")
    ctx.sandbox.write_bytes(path, text.replace(old, new).encode("utf-8"))
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


PLAN_STATUS = ("pending", "in_progress", "done")
PLAN_MAX, PLAN_TITLE_MAX = 12, 120


@tool("Show the user your plan and progress for a task that takes several steps (e.g. inspect data, compute, check, "
      "present). Call it before the first step with all steps (the first one in_progress), and again whenever a step "
      "starts or finishes or the plan changes (in the same turn as your next tool call is fine), and a last time "
      "with the finished steps done before your final answer. Send the whole list each time, in order. Write titles "
      "in the user's language, short. Do not use it for a question you can answer in one or two tool calls.",
      {"steps": {"type": "array", "items": {"type": "object", "properties": {
          "title": S, "status": {"type": "string", "enum": list(PLAN_STATUS)}}, "required": ["title", "status"]}}})
def update_plan(ctx, steps):
    steps = check_plan(steps)
    ctx.emit({"type": "plan", "steps": steps})
    return f"plan shown to the user: {sum(s['status'] == 'done' for s in steps)}/{len(steps)} done"


def check_plan(steps):
    """The steps as shown (titles stripped), or ValueError back to the model."""
    if not isinstance(steps, list) or not 1 <= len(steps) <= PLAN_MAX:
        raise ValueError(f"steps must be a list of 1-{PLAN_MAX} steps")
    out = []
    for s in steps:
        if not isinstance(s, dict) or not isinstance(s.get("title"), str) or not s["title"].strip() \
                or len(s["title"]) > PLAN_TITLE_MAX or s.get("status") not in PLAN_STATUS:
            raise ValueError(f"each step needs a title (1-{PLAN_TITLE_MAX} chars) and a status in {list(PLAN_STATUS)}")
        out.append({"title": s["title"].strip(), "status": s["status"]})
    if sum(s["status"] == "in_progress" for s in out) > 1:
        raise ValueError("at most one step can be in_progress")
    return out


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
      "mermaid, code (set language). html: give each part your answer may refer to an id, e.g. <section "
      "id=\"sec-trend\">; then a paragraph can end with [short name](artifact:ID@VERSION#trend) to open it there.",
      {"command": {"type": "string", "enum": ["create", "update", "rewrite"]}, "id": S,
       "type": {"type": "string", "enum": ["html", "markdown", "svg", "mermaid", "code"]}, "title": S,
       "content": S, "file": S, "old_str": S, "new_str": S, "language": S}, required=["command", "id"])
def artifact(ctx, command, id, type=None, title=None, content=None, file=None, old_str=None, new_str=None,
             language=None):
    store = ctx.artifacts
    if command not in ("create", "update", "rewrite"):
        raise ValueError("command must be create, update or rewrite")
    kind = type if type in TEMPLATES else command != "create" and store.versions(id) and store.get(id)["type"]
    if kind and kind in TEMPLATES:
        raise ValueError(f"{kind} views are made with {TEMPLATES[kind]}")
    if command != "update":
        if (content is None) == (file is None):
            raise ValueError("give exactly one of content or file")
        if file is not None:
            content = ctx.sandbox.read_bytes(file, ARTIFACT_MAX).decode("utf-8")
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
    return f"artifact {meta['id']} saved as version {meta['version']} ({meta['type']}, {len(content)} chars)" + (
        f"\nA part with id=\"sec-NAME\" is cited as [short name](artifact:{meta['id']}@{meta['version']}#NAME)"
        if meta["type"] == "html" else "")


def _save_view(ctx, id, kind, title, data):
    """A template view's data as the next version of artifact `id` (the first one creates it)."""
    store = ctx.artifacts
    return store.save(id, kind, title, json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                      command="rewrite" if store.versions(id) else "create")


def cite_hint(meta, parts):
    """How the answer points at the part of this view that backs a paragraph (Loop 37; app.js opens it there)."""
    clean = lambda t: " ".join(t.replace("[", "(").replace("]", ")").split())[:60]  # noqa: E731
    return (f"\nTo show the user where a claim in your answer comes from, end that paragraph with "
            f"[short name](artifact:{meta['id']}@{meta['version']}#PART), PART being the part that shows it: "
            + ", ".join(f"{r} = {clean(t)}" for r, t in parts))


@tool("Show the user the standard monthly spending report (fixed layout: summary, spending by category as a "
      "bar chart with budgets, largest transactions, notes). You only supply the numbers: compute them with code "
      "first and copy them exactly. The user can capture each section as an image. Calling it again with the same "
      "id makes a new version.",
      {"id": S, "data": report.SCHEMA}, required=["id", "data"])
def show_report(ctx, id, data):
    clean, warnings = report.validate(data)
    meta = _save_view(ctx, id, "report", clean["title"], clean)
    return f"report {meta['id']} saved as version {meta['version']}" + \
        cite_hint(meta, [(k, report.SECTIONS[k]) for k in report.sections(clean)]) + \
        "".join(f"\nWARNING: {w}. Check your numbers and call show_report again if they are wrong." for w in warnings)


@tool("Ask the user a question and wait for the answer. Use it when the answer changes what you will do and you "
      "cannot infer it (e.g. what to focus on in a log you just looked at). Base the options on what you found, "
      "2-4 concrete ones, the one you recommend first; the user can also type their own answer. Do not ask what "
      "the user already told you.",
      {"question": S, "options": {"type": "array", "items": {"type": "object", "properties": {
          "label": {"type": "string", "description": "short, shown on a button"},
          "description": {"type": "string", "description": "one line: what you will do if chosen"}},
          "required": ["label"]}}}, ask=True)
def ask_user(ctx, question, options, answer):
    return f"The user answered: {answer}"


def check_question(question, options):
    if not isinstance(question, str) or not question.strip() or len(question) > 500:
        raise ValueError("question must be a non-empty string (max 500 chars)")
    if not isinstance(options, list) or not 2 <= len(options) <= 5 or not all(
            isinstance(o, dict) and isinstance(o.get("label"), str) and o["label"].strip() for o in options):
        raise ValueError("options must be 2-5 objects with a label")


@tool("Show the user an interactive shmoo plot built from a test log in the workspace (one row per device x condition "
      "x sweep point, with a pass/fail column). You only name the file and its columns; the server reads the log. "
      "The user can filter conditions, pick a device or see all devices combined, click cells, and copy the current "
      "view as an image. Returns a summary with pattern findings. Calling it again with the same id makes a new version.",
      {"id": S, "file": S, "x": {"type": "string", "description": "column for the horizontal axis, e.g. frequency"},
       "y": {"type": "string", "description": "column for the vertical axis, e.g. voltage"},
       "result": {"type": "string", "description": "pass/fail column"},
       "device": {"type": "array", "items": S, "description": "columns that together identify one device/die"},
       "filters": {"type": "array", "items": S, "description": "condition columns to choose from, e.g. temperature"},
       "pass_values": {"type": "array", "items": S, "description": "result values meaning pass, if not P/PASS/1/OK"},
       "spec": {"type": "object", "description": "spec window to draw, only if the user gave one, in the log's own "
                "units: {\"x\": [low, high], \"y\": [low, high]}",
                "properties": {"x": {"type": "array", "items": {"type": "number"}},
                               "y": {"type": "array", "items": {"type": "number"}}}},
       "op": {"type": "object", "description": "operating point to mark, only if the user gave one: {\"x\": v, \"y\": v}",
              "properties": {"x": {"type": "number"}, "y": {"type": "number"}}},
       "title": S}, required=["id", "file", "x", "y", "result", "device"])
def show_shmoo(ctx, id, file, x, y, result, device, filters=(), pass_values=None, title=None, spec=None, op=None):
    with _host_copy(ctx, file) as p:
        data, warnings, summary = shmoo.build(p, x, y, result, device, filters, pass_values, title, spec, op,
                                              source=Path(file).name)
    meta = _save_view(ctx, id, "shmoo", data["title"], data)
    return f"shmoo {meta['id']} saved as version {meta['version']}\n{summary}" + \
        "".join(f"\nWARNING: {w}" for w in warnings)


@tool("Show the user an interactive view of a text log in the workspace (application/server/syslog/access logs): "
      "entries over time stacked by level, and message patterns (numbers, ids, IPs, paths masked) with counts and "
      "first/last time. The user toggles levels, clicks a pattern to chart only it, clicks a time bucket for its top "
      "patterns, and copies the chart as an image. No raw lines are shown. The server parses the file; returns a "
      "summary (levels, top patterns, busiest error buckets).",
      {"id": S, "file": S, "title": S,
       "bucket_seconds": {"type": "integer", "description": "time bucket; omit for automatic (~100 bars)"}},
      required=["id", "file"])
def show_logview(ctx, id, file, title=None, bucket_seconds=None):
    with _host_copy(ctx, file) as p:
        data, summary = logview.build(p, title or f"로그: {Path(file).name}", bucket_seconds, source=Path(file).name)
    meta = _save_view(ctx, id, "logview", data["title"], data)
    return f"logview {meta['id']} saved as version {meta['version']}\n{summary}"


@tool("Load a skill's instructions (see the Skills list in your instructions). Its files are mounted read-only in "
      "the sandbox at /skills/<name>/.", {"name": S})
def use_skill(ctx, name):
    if name not in ctx.skills:
        raise ValueError(f"unknown skill {name!r}. Available: {sorted(ctx.skills)}")
    return f"Skill {name} (files in /skills/{name}/)\n\n" + ctx.skills[name]["body"]


@tool("Get the current UTC date/time.", {}, required=[])
def current_time(ctx):
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@tool("git add -A and commit all workspace changes on a new or existing branch.", {"branch": S, "message": S})
def git_commit(ctx, branch, message):
    with ctx.sandbox.frozen():  # host git follows a work tree swapped for a symlink (review, Loop 19)
        rc, cur = _git(ctx, "rev-parse", "--abbrev-ref", "HEAD")
        if cur != branch:
            rc, out = _git(ctx, "checkout", "-B", branch)
        _git(ctx, "add", "-A")
        rc, out = _git(ctx, "-c", "user.name=agent", "-c", "user.email=agent@local", "commit", "-m", message)
    return out


def _git(ctx, *args):  # call inside ctx.sandbox.frozen()
    if ctx.git_dir:  # server workspaces: git metadata is outside the sandbox, work tree = /workspace/repo
        return github.git_wt(ctx.git_dir, ctx.sandbox.path("repo"), *args)
    return github.git(ctx.sandbox.workspace, *args)


@tool("Push a committed branch to the project's remote, where the user opens the pull request. Requires human "
      "approval.", {"branch": S}, approval=True)
def push_branch(ctx, branch):
    with ctx.sandbox.frozen():
        ws = ctx.sandbox.path("repo") if ctx.git_dir else ctx.sandbox.workspace
        rc, out = github.push(ws, branch, ctx.remote_url, ctx.git_dir)
    if rc:
        raise RuntimeError(out)
    return f"pushed {branch}"


def schemas(names=None):
    # remember needs a user store, artifact/show_* an artifact store, use_skill skills, agent__* connected agents
    # (ctx.agents), write_code/compile_code toolchains; callers that have them name them
    return [t["schema"] for n, t in REGISTRY.items()
            if (n in names if names is not None else
                n not in ("remember", "artifact", *TEMPLATES.values(), "use_skill", "ask_user", "write_code",
                          "compile_code") and not n.startswith("agent__"))]


# Connected agents (agent/delegate.py): one tool per agents/{name}/agent.json, run inside the sandbox.
AGENTS = delegate.discover()  # the server passes these as ctx.agents
delegate.register(AGENTS, REGISTRY, _schema)
# Code generation in a language the model does not know (agent/codegen.py): toolchains/{language}/ + a codegen agent.
TOOLCHAINS = codegen.discover()  # the server passes these as ctx.toolchains
codegen.register(TOOLCHAINS, AGENTS, REGISTRY, _schema)
