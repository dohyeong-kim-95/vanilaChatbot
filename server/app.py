"""Chat backend (MVP): durable runs decoupled from HTTP, replayable event log, approval = interrupt/resume.

POST /sessions                          -> {session_id}
POST /sessions/{id}/documents           {name, text}           (RAG upload)
POST /sessions/{id}/messages            {content}  -> {run_id}  (409 if a run is active)
GET  /runs/{id}                         -> {status, ...}
GET  /runs/{id}/events                  SSE, `id:` = seq; resume with Last-Event-ID (or ?after=)
POST /runs/{id}/approval                {approved: bool}       (resumes an interrupted run)
POST /runs/{id}/cancel

Everything a run needs (history, notes, status, in-flight tool) is in SQLite and checkpointed after every
message, so a server restart resumes `running` runs and keeps `awaiting_approval` ones.
"""
import asyncio
import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.loop import SYSTEM, Context, Interrupt, run
from agent.rag import DocStore
from agent.sandbox import Sandbox

ROOT = Path(os.environ.get("AGENT_ROOT", "/tmp/agent-sessions"))
ROOT.mkdir(parents=True, exist_ok=True)
DB = sqlite3.connect(ROOT / "agent.db", check_same_thread=False, isolation_level=None)
DB.executescript("""
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, history TEXT, notes TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, session_id TEXT, status TEXT, inflight TEXT, pending TEXT);
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, type TEXT, data TEXT);
""")
LOCK = threading.Lock()
SANDBOXES, CANCEL = {}, {}  # process-local; rebuilt lazily after a restart (workspace is on disk)
app = FastAPI()


def q(sql, *args):
    with LOCK:
        return DB.execute(sql, args).fetchall()


def emit(rid, ev):
    q("INSERT INTO events(run_id, type, data) VALUES (?,?,?)", rid, ev["type"], json.dumps(ev, ensure_ascii=False))


def sandbox(sid):
    if sid not in SANDBOXES:
        SANDBOXES[sid] = Sandbox(ROOT / sid)
    return SANDBOXES[sid]


def drive(rid, task=None, decisions=None):
    """Run (or resume) an agent run in a worker thread. All state goes through the DB."""
    sid = q("SELECT session_id FROM runs WHERE id=?", rid)[0][0]
    history, notes = q("SELECT history, notes FROM sessions WHERE id=?", sid)[0]
    msgs = json.loads(history)
    ctx = Context(sandbox=sandbox(sid), docs=DocStore(str(ROOT / f"{sid}.docs.db")), notes=json.loads(notes),
                  approve=lambda name, args: None,  # never block a thread on a human: interrupt instead
                  decisions=decisions or {}, cancel=CANCEL.setdefault(rid, threading.Event()))

    def checkpoint(m, inflight):
        q("UPDATE sessions SET history=?, notes=? WHERE id=?", json.dumps(m), json.dumps(ctx.notes), sid)
        q("UPDATE runs SET inflight=? WHERE id=?", inflight, rid)

    emit(rid, {"type": "run_started", "resumed": task is None})
    try:
        run(task, ctx, history=msgs, emit=lambda ev: emit(rid, ev), log=lambda *_: None, checkpoint=checkpoint)
        status = "cancelled" if ctx.cancel.is_set() else "succeeded"
    except Interrupt as i:
        req = {"call_id": i.call_id, "tool": i.name, "arguments": i.arguments}
        q("UPDATE runs SET status='awaiting_approval', inflight=NULL, pending=? WHERE id=?", json.dumps(req), rid)
        emit(rid, {"type": "approval_request", **req})
        emit(rid, {"type": "run_finished", "outcome": "interrupt"})
        return
    except Exception as e:
        status = "error"
        emit(rid, {"type": "error", "message": f"{type(e).__name__}: {e}"})
    q("UPDATE runs SET status=? WHERE id=?", status, rid)
    emit(rid, {"type": "run_finished", "outcome": status})


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


class Msg(BaseModel):
    content: str


class Doc(BaseModel):
    name: str
    text: str


class Decision(BaseModel):
    approved: bool


@app.post("/sessions")
def create_session():
    sid = uuid.uuid4().hex[:12]
    q("INSERT INTO sessions(id, history) VALUES (?,?)", sid, json.dumps([{"role": "system", "content": SYSTEM}]))
    sandbox(sid)
    return {"session_id": sid}


@app.post("/sessions/{sid}/documents")
def add_doc(sid: str, d: Doc):
    return {"chunks": DocStore(str(ROOT / f"{sid}.docs.db")).add_text(d.name, d.text)}


@app.post("/sessions/{sid}/messages")
def message(sid: str, m: Msg):
    if q("SELECT 1 FROM runs WHERE session_id=? AND status IN ('running','awaiting_approval')", sid):
        raise HTTPException(409, "a run is already active in this session")
    rid = uuid.uuid4().hex[:12]
    q("INSERT INTO runs(id, session_id, status) VALUES (?,?,'running')", rid, sid)
    start(rid, task=m.content)
    return {"run_id": rid}


@app.get("/runs/{rid}")
def get_run(rid: str):
    (sid, status, pending), = q("SELECT session_id, status, pending FROM runs WHERE id=?", rid)
    return {"run_id": rid, "session_id": sid, "status": status, "pending": json.loads(pending or "null")}


@app.post("/runs/{rid}/approval")
def approval(rid: str, d: Decision):
    # compare-and-set: a double click / two tabs cannot resume (and run the tool) twice
    row = q("UPDATE runs SET status='running' WHERE id=? AND status='awaiting_approval' RETURNING pending", rid)
    if not row:
        raise HTTPException(409, "run is not awaiting approval")
    start(rid, decisions={json.loads(row[0][0])["call_id"]: d.approved})
    return {"ok": True}


@app.post("/runs/{rid}/cancel")
def cancel(rid: str):
    CANCEL.setdefault(rid, threading.Event()).set()
    if q("SELECT 1 FROM runs WHERE id=? AND status='awaiting_approval'", rid):  # no worker to notice the flag
        q("UPDATE runs SET status='running' WHERE id=?", rid)
        start(rid)  # the pending call gets a 'cancelled' result, history stays valid
    return {"ok": True}


@app.get("/runs/{rid}/events")
async def events(rid: str, request: Request, after: int = 0):
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
