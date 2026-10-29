"""Chat backend (MVP): durable runs decoupled from HTTP, replayable event log, approval = interrupt/resume,
per-user storage (agent/userstore.py) with a disposable sandbox per run.

Every request carries the caller's identity (X-User: stand-in for real auth); resources of other users are 404.
PUT  /instructions | /projects/{p}/instructions   {text}      user / project CLAUDE.md
PUT  /settings     | /projects/{p}/settings       {settings}  never visible to the sandbox
POST /projects                          {project_id}
POST /projects/{p}/uploads              {name, text}           (immutable, read-only in the sandbox)
POST /sessions                          {project_id} -> {session_id}
POST /sessions/{id}/documents           {name, text}           (RAG upload)
POST /sessions/{id}/messages            {content}  -> {run_id}  (409 if a run is active in the project)
GET  /runs/{id}                         -> {status, ...}
GET  /runs/{id}/events                  SSE, `id:` = seq; resume with Last-Event-ID (or ?after=)
POST /runs/{id}/approval                {approved: bool} | {answer: str}   (resumes an interrupted run; answer for ask_user)
POST /runs/{id}/cancel
GET  /projects | /projects/{p}/sessions | /sessions/{id}            lists, chat history (for the UI)
GET  /projects/{p}/artifacts | /projects/{p}/artifacts/{a}?version=n  -> {..., view_url} (renderer, server/render.py)
GET  /projects/{p}/artifacts/{a}/sections/{s}.png?version=n&preset=L|M|S&title=0|1   a report section as a slide image (server/capture.py)
GET  /                                  minimal web UI (server/static)

Everything a run needs (history, notes, status, in-flight tool) is in SQLite and checkpointed after every
message, so a server restart resumes `running` runs and keeps `awaiting_approval` ones. Files live in the user
store; a run checks the project out, and writes repo/outputs back when it ends or pauses for approval.
"""
import asyncio
import json
import os
import shutil
import sqlite3
import threading
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from agent import report, skills
from agent.artifacts import ArtifactStore
from agent.loop import SYSTEM, Context, Interrupt, run
from agent.rag import DocStore
from agent.sandbox import Sandbox
from agent.tools import REGISTRY, schemas
from agent.userstore import ID_RE, UserStore
from server import capture, render

ROOT = Path(os.environ.get("AGENT_ROOT", "/tmp/agent-sessions"))
ROOT.mkdir(parents=True, exist_ok=True)
DB = sqlite3.connect(ROOT / "agent.db", check_same_thread=False, isolation_level=None)
DB.executescript("""
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT, history TEXT,
                                    notes TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, session_id TEXT, status TEXT, inflight TEXT, pending TEXT);
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, type TEXT, data TEXT);
""")
LOCK = threading.Lock()
CANCEL = {}  # process-local
_read = lambda env: Path(os.environ[env]).read_text() if os.environ.get(env) else ""
STORE = UserStore(ROOT, org_policy=_read("AGENT_ORG_POLICY"), org_settings=json.loads(_read("AGENT_ORG_SETTINGS") or "{}"))
TOOLS = [t["function"]["name"] for t in schemas()] + ["remember", "artifact", "show_report", "show_shmoo", "show_logview", "use_skill", "ask_user"]
SKILLS = skills.discover()
# Loop 15: effort=max was ~9x slower than low on the finance eval with the same accuracy (k=1). Default low;
# a project can raise it with settings {"effort": "high"} (user/project/org layers, §9.3).
EFFORT = os.environ.get("AGENT_EFFORT", "low")
STATIC = Path(__file__).parent / "static"
app = FastAPI()


def q(sql, *args):
    with LOCK:
        return DB.execute(sql, args).fetchall()


def emit(rid, ev):
    q("INSERT INTO events(run_id, type, data) VALUES (?,?,?)", rid, ev["type"], json.dumps(ev, ensure_ascii=False))


def drive(rid, task=None, decisions=None):
    """Run (or resume) an agent run in a worker thread. All state goes through the DB and the user store;
    the sandbox lives only while this worker does (an approval wait holds no container)."""
    try:
        paused = _work(rid, task, decisions)
    except Exception as e:  # checkout, docker, storage: fail the run visibly instead of leaving it 'running' (locked)
        q("UPDATE runs SET status='error', inflight=NULL WHERE id=?", rid)
        emit(rid, {"type": "error", "message": f"{type(e).__name__}: {e}"})
        emit(rid, {"type": "run_finished", "outcome": "error"})
        paused = False
    if not paused:
        CANCEL.pop(rid, None)


def _work(rid, task, decisions):
    sid = q("SELECT session_id FROM runs WHERE id=?", rid)[0][0]
    user, project, history, notes = q("SELECT user_id, project_id, history, notes FROM sessions WHERE id=?", sid)[0]
    msgs = json.loads(history)
    if task is not None:  # instructions/memory may have changed since the last message; fixed during a run
        msgs[0] = {"role": "system", "content": "\n\n".join(
            filter(None, [SYSTEM, skills.prompt(SKILLS), STORE.instructions(user, project)]))}
    run_dir = ROOT / "work" / rid
    if not run_dir.exists():  # else: a crashed worker's uncommitted workspace, keep it
        STORE.checkout(user, project, run_dir)
    sb = Sandbox(run_dir / "ws", readonly=("uploads",), name=rid, mounts={skills.DIR: skills.MOUNT})
    settings = STORE.settings(user, project)
    ctx = Context(sandbox=sb, skills=SKILLS, docs=DocStore(str(ROOT / f"{sid}.docs.db")), notes=json.loads(notes),
                  git_dir=str(run_dir / "git"), remote_url=settings.get("git_remote"),
                  memory=lambda note: STORE.remember(user, note, f"session {sid}, run {rid}"),
                  artifacts=artifacts(user, project, on_save=lambda meta: emit(rid, {"type": "artifact", **meta}),
                                      source=f"session {sid}, run {rid}"),
                  approve=lambda name, args: None,  # never block a thread on a human: interrupt instead
                  decisions=decisions or {}, cancel=CANCEL.setdefault(rid, threading.Event()))
    try:
        finish, paused = _drive(rid, task, ctx, msgs, sid, settings.get("effort") or EFFORT)
    finally:
        sb.close()
    try:  # write back before announcing the end, so a client that sees run_finished sees the saved files
        r = STORE.commit(user, project, run_dir, f"run {rid}")
        if r["skipped"]:
            emit(rid, {"type": "warning", "message": f"not saved (symlinks/special files): {r['skipped']}"})
        if not paused:  # a paused run resumes in this workspace: files outside repo/outputs survive the wait
            # shortcut: an approval/question never answered keeps its run dir; add cleanup by age when disk matters
            shutil.rmtree(run_dir)
    except Exception as e:  # keep run_dir for inspection; storage is unchanged
        emit(rid, {"type": "error", "message": f"saving workspace failed: {type(e).__name__}: {e}"})
    finish()
    return paused


def _drive(rid, task, ctx, msgs, sid, effort):
    """Returns the step that publishes the outcome (run after the workspace is saved)."""
    def checkpoint(m, inflight):
        q("UPDATE sessions SET history=?, notes=? WHERE id=?", json.dumps(m), json.dumps(ctx.notes), sid)
        q("UPDATE runs SET inflight=? WHERE id=?", inflight, rid)

    emit(rid, {"type": "run_started", "resumed": task is None})
    try:
        run(task, ctx, tool_names=TOOLS, history=msgs, emit=lambda ev: emit(rid, ev), log=lambda *_: None,
            checkpoint=checkpoint, effort=effort)
        status = "cancelled" if ctx.cancel.is_set() else "succeeded"
    except Interrupt as i:
        req = {"call_id": i.call_id, "tool": i.name, "arguments": i.arguments}

        def finish():
            q("UPDATE runs SET status='awaiting_approval', inflight=NULL, pending=? WHERE id=?", json.dumps(req), rid)
            emit(rid, {"type": "question" if REGISTRY[req["tool"]].get("ask") else "approval_request", **req})
            emit(rid, {"type": "run_finished", "outcome": "interrupt"})
        return finish, True
    except Exception as e:
        status = "error"
        emit(rid, {"type": "error", "message": f"{type(e).__name__}: {e}"})

    def finish():
        q("UPDATE runs SET status=? WHERE id=?", status, rid)
        emit(rid, {"type": "run_finished", "outcome": status})
    return finish, False


def artifacts(user, project, **kw):
    # host-side, outside the run's workspace: saved the moment the tool runs, not on write-back
    return ArtifactStore(STORE.project_dir(user, project) / "artifacts", **kw)


def start(rid, **kw):
    threading.Thread(target=drive, args=(rid,), kwargs=kw, daemon=True).start()


@app.on_event("startup")
def recover():
    """Resume runs that were mid-flight when the process died. A tool that was running is NOT re-executed
    (it may have had side effects): it gets an 'outcome unknown' result and the model decides."""
    for rid, sid, inflight in q("SELECT id, session_id, inflight FROM runs WHERE status='running'"):
        if inflight:
            msgs = json.loads(q("SELECT history FROM sessions WHERE id=?", sid)[0][0])
            msgs.append({"role": "tool", "tool_call_id": inflight, "content": "ERROR: the server restarted while "
                         "this tool was running; its outcome is unknown. Check the current state before retrying."})
            q("UPDATE sessions SET history=? WHERE id=?", json.dumps(msgs), sid)
            q("UPDATE runs SET inflight=NULL WHERE id=?", rid)
        start(rid)


def caller(x_user: str = Header(None)):
    """Stand-in for real authentication. The user id always comes from here, never from a request body."""
    if not x_user or not ID_RE.match(x_user):
        raise HTTPException(401, "missing or invalid X-User")
    return x_user


def own_session(sid, user):
    row = q("SELECT user_id, project_id FROM sessions WHERE id=?", sid)
    if not row or row[0][0] != user:
        raise HTTPException(404, "no such session")  # same answer as "does not exist": no probing
    return row[0][1]


def own_run(rid, user):
    row = q("SELECT session_id FROM runs WHERE id=?", rid)
    if not row:
        raise HTTPException(404, "no such run")
    own_session(row[0][0], user)


def own_project(project, user):
    if not ID_RE.match(project) or project not in STORE.projects(user):
        raise HTTPException(404, "no such project")
    return project


class Msg(BaseModel):
    content: str


class Project(BaseModel):
    project_id: str


class SessionReq(BaseModel):
    project_id: str


class Text(BaseModel):
    text: str


class Settings(BaseModel):
    settings: dict


class Upload(BaseModel):
    name: str
    text: str


class Doc(BaseModel):
    name: str
    text: str


class Decision(BaseModel):
    call_id: str                  # the pending call this answers (a stale card must not answer a newer call)
    approved: bool | None = None  # approval tools
    answer: str | None = None     # ask_user


@app.post("/projects")
def create_project(p: Project, user=Depends(caller)):
    if not ID_RE.match(p.project_id):
        raise HTTPException(422, "invalid project_id")
    STORE.create_project(user, p.project_id)
    return {"project_id": p.project_id}


@app.put("/instructions")
def user_instructions(t: Text, user=Depends(caller)):
    STORE.set_instructions(user, t.text)
    return {"ok": True}


@app.put("/projects/{pid}/instructions")
def project_instructions(pid: str, t: Text, user=Depends(caller)):
    STORE.set_instructions(user, t.text, own_project(pid, user))
    return {"ok": True}


@app.put("/settings")
def user_settings(st: Settings, user=Depends(caller)):
    STORE.set_settings(user, st.settings)
    return {"ok": True}


@app.put("/projects/{pid}/settings")
def project_settings(pid: str, st: Settings, user=Depends(caller)):
    STORE.set_settings(user, st.settings, own_project(pid, user))
    return {"ok": True}


@app.get("/memory")
def get_memory(user=Depends(caller)):
    return {"memory": STORE.memory(user)}


@app.post("/projects/{pid}/uploads")
def upload(pid: str, u: Upload, user=Depends(caller)):
    try:
        return {"sha256": STORE.upload(user, own_project(pid, user), u.name, u.text.encode())}
    except FileExistsError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/sessions")
def create_session(r: SessionReq, user=Depends(caller)):
    own_project(r.project_id, user)
    sid = uuid.uuid4().hex[:12]
    q("INSERT INTO sessions(id, user_id, project_id, history) VALUES (?,?,?,?)", sid, user, r.project_id,
      json.dumps([{"role": "system", "content": SYSTEM}]))
    return {"session_id": sid}


@app.post("/sessions/{sid}/documents")
def add_doc(sid: str, d: Doc, user=Depends(caller)):
    own_session(sid, user)
    return {"chunks": DocStore(str(ROOT / f"{sid}.docs.db")).add_text(d.name, d.text)}


@app.post("/sessions/{sid}/messages")
def message(sid: str, m: Msg, user=Depends(caller)):
    project = own_session(sid, user)
    rid = uuid.uuid4().hex[:12]
    # one active run per project: two runs would each check out the repo and the later write-back would win
    row = q("""INSERT INTO runs(id, session_id, status) SELECT ?, ?, 'running' WHERE NOT EXISTS (
               SELECT 1 FROM runs JOIN sessions s ON s.id = runs.session_id WHERE s.user_id=? AND s.project_id=?
               AND runs.status IN ('running','awaiting_approval')) RETURNING id""", rid, sid, user, project)
    if not row:
        raise HTTPException(409, "a run is already active in this project")
    start(rid, task=m.content)
    return {"run_id": rid}


@app.get("/runs/{rid}")
def get_run(rid: str, user=Depends(caller)):
    own_run(rid, user)
    (sid, status, pending), = q("SELECT session_id, status, pending FROM runs WHERE id=?", rid)
    return {"run_id": rid, "session_id": sid, "status": status, "pending": json.loads(pending or "null")}


@app.post("/runs/{rid}/approval")
def approval(rid: str, d: Decision, user=Depends(caller)):
    own_run(rid, user)
    # compare-and-set: a double click / two tabs cannot resume (and run the tool) twice
    pending = q("SELECT pending FROM runs WHERE id=? AND status='awaiting_approval'", rid)
    if not pending or json.loads(pending[0][0])["call_id"] != d.call_id:
        raise HTTPException(409, "run is not awaiting this call")
    ask = REGISTRY[json.loads(pending[0][0])["tool"]].get("ask")
    value = d.answer.strip()[:2000] if ask and d.answer and d.answer.strip() else None if ask else d.approved
    if value is None:
        raise HTTPException(422, "this run waits for an answer" if ask else "this run waits for approved: true/false")
    row = q("""UPDATE runs SET status='running' WHERE id=? AND status='awaiting_approval'
               AND json_extract(pending, '$.call_id')=? RETURNING pending""", rid, d.call_id)
    if not row:
        raise HTTPException(409, "run is not awaiting this call")
    start(rid, decisions={d.call_id: value})
    return {"ok": True}


@app.post("/runs/{rid}/cancel")
def cancel(rid: str, user=Depends(caller)):
    own_run(rid, user)
    if not q("SELECT 1 FROM runs WHERE id=? AND status IN ('running','awaiting_approval')", rid):
        return {"ok": True}  # already finished: nothing to cancel (and no flag left behind)
    CANCEL.setdefault(rid, threading.Event()).set()
    # no worker to notice the flag while paused: resume once (compare-and-set), the pending call gets 'cancelled'
    if q("UPDATE runs SET status='running' WHERE id=? AND status='awaiting_approval' RETURNING id", rid):
        start(rid)
    return {"ok": True}


@app.get("/runs/{rid}/events")
async def events(rid: str, request: Request, after: int = 0, user=Depends(caller)):
    own_run(rid, user)
    after = int(request.headers.get("last-event-id") or after)

    async def sse():
        nonlocal after
        while not await request.is_disconnected():
            rows = q("SELECT seq, type, data FROM events WHERE run_id=? AND seq>? ORDER BY seq", rid, after)
            for seq, typ, data in rows:
                yield f"id: {seq}\nevent: {typ}\ndata: {data}\n\n"
                after = seq
            status = q("SELECT status FROM runs WHERE id=?", rid)[0][0]
            if rows and rows[-1][1] == "run_finished" and status != "running":
                return  # finished or interrupted; reconnect (Last-Event-ID) after approving to follow the resume
            await asyncio.sleep(0.2)

    return StreamingResponse(sse(), media_type="text/event-stream")


@app.get("/projects")
def list_projects(user=Depends(caller)):
    return {"projects": STORE.projects(user)}


@app.get("/projects/{pid}/sessions")
def list_sessions(pid: str, user=Depends(caller)):
    own_project(pid, user)
    return {"sessions": [r[0] for r in q("SELECT id FROM sessions WHERE user_id=? AND project_id=? ORDER BY rowid DESC",
                                          user, pid)]}


@app.get("/sessions/{sid}")
def get_session(sid: str, user=Depends(caller)):
    """Chat as the user saw it: their messages, the assistant's text, and which artifacts were saved."""
    project = own_session(sid, user)
    msgs, out = json.loads(q("SELECT history FROM sessions WHERE id=?", sid)[0][0]), []
    for m in msgs[1:]:
        if m["role"] in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"]:
            out.append({"role": m["role"], "content": m["content"]})
        for tc in m.get("tool_calls") or []:
            if tc["function"]["name"] in ("artifact", "show_report", "show_shmoo", "show_logview"):
                try:
                    out.append({"role": "artifact", "id": json.loads(tc["function"]["arguments"])["id"]})
                except (ValueError, KeyError, TypeError):
                    pass
    active = q("""SELECT runs.id, runs.status, runs.pending FROM runs WHERE session_id=?
                  AND status IN ('running','awaiting_approval') ORDER BY rowid DESC LIMIT 1""", sid)
    run = None
    if active:  # history already holds what was said; the UI follows only events after `seq`
        rid, status, pending = active[0]
        pending = json.loads(pending) if status == "awaiting_approval" and pending else None
        run = {"run_id": rid, "status": status, "seq": q("SELECT COALESCE(MAX(seq), 0) FROM events WHERE run_id=?", rid)[0][0],
               "pending": pending and {**pending, "type": "question" if REGISTRY[pending["tool"]].get("ask")
                                       else "approval_request"}}
    return {"session_id": sid, "project_id": project, "messages": out, "active_run": run}


@app.get("/projects/{pid}/artifacts")
def list_artifacts(pid: str, user=Depends(caller)):
    return {"artifacts": artifacts(user, own_project(pid, user)).list()}


@app.get("/projects/{pid}/artifacts/{aid}")
def get_artifact(pid: str, aid: str, version: int = None, user=Depends(caller)):
    try:
        a = artifacts(user, own_project(pid, user)).get(aid, version)
    except (KeyError, ValueError):
        raise HTTPException(404, "no such artifact")
    a["versions"] = artifacts(user, pid).versions(aid)
    if a["type"] == "report":
        a["sections"] = [{"id": s, "title": report.SECTIONS[s], "kind": "image" if s in report.SLIDE_SECTIONS else "text",
                          "preset": report.SLIDE_SECTIONS.get(s)} for s in report.sections(json.loads(a["content"]))]
    a["capture"] = a["type"] in ("shmoo", "logview")  # the page itself renders its current view to PNG (postMessage)
    a["view_url"] = f"{render.RENDER_ORIGIN}/a/{render.sign(user, pid, aid, a['version'])}"
    return a


@app.get("/projects/{pid}/artifacts/{aid}/sections/{section}.png")
def section_png(pid: str, aid: str, section: str, version: int = None, preset: str = None, title: int = 1,
                user=Depends(caller)):
    """One section of a report as a slide image (or section=all: the whole page), for pasting into a deck."""
    try:
        a = artifacts(user, own_project(pid, user)).get(aid, version)
        if a["type"] != "report":
            raise KeyError(aid)
        img = capture.png(json.loads(a["content"]), section, preset, bool(title))
    except (KeyError, ValueError):
        raise HTTPException(404, "no such report section")
    name = f"report_{aid}_v{a['version']}_{section}_{preset or report.SLIDE_SECTIONS.get(section, 'all')}.png"
    return Response(img, media_type="image/png", headers={"Content-Disposition": f'inline; filename="{name}"',
                                                           "Cache-Control": "no-store"})


# The UI's CSP is part of artifact isolation: frame-src = the renderer only, so an artifact cannot navigate its
# iframe to another site (a way to send data out that the renderer's own CSP cannot stop).
UI_CSP = (f"default-src 'self'; frame-src {render.RENDER_ORIGIN}; script-src 'self'; "
          "style-src 'self'; font-src 'none'; img-src 'self' data:; "
          "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


@app.get("/")
def ui():
    return FileResponse(STATIC / "index.html", headers={"Content-Security-Policy": UI_CSP})


@app.get("/static/{name}")
def static(name: str):
    f = STATIC / name
    if name not in ("app.js", "app.css") or not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers={"Content-Security-Policy": UI_CSP, "X-Content-Type-Options": "nosniff"})
