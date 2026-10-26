"""Chat backend (MVP): sessions, SSE event stream of agent progress, human approval endpoint.

POST /sessions                         -> {session_id}
POST /sessions/{id}/documents          {name, text}           (RAG upload)
POST /sessions/{id}/messages           {content}  -> SSE: text|tool_call|tool_result|approval_request|final
POST /sessions/{id}/approvals/{req_id} {approved: bool}
"""
import asyncio
import json
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.loop import SYSTEM, Context, run
from agent.rag import DocStore
from agent.sandbox import Sandbox

ROOT = Path("/tmp/agent-sessions")
app = FastAPI()
SESSIONS = {}


class Session:
    def __init__(self, sid):
        self.sandbox = Sandbox(ROOT / sid)
        self.docs = DocStore()
        self.history = [{"role": "system", "content": SYSTEM}]
        self.pending = {}  # req_id -> [threading.Event, decision]
        self.lock = threading.Lock()  # one run at a time per session


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
    SESSIONS[sid] = Session(sid)
    return {"session_id": sid}


@app.post("/sessions/{sid}/documents")
def add_doc(sid: str, d: Doc):
    return {"chunks": SESSIONS[sid].docs.add_text(d.name, d.text)}


@app.post("/sessions/{sid}/approvals/{rid}")
def approve(sid: str, rid: str, d: Decision):
    ev = SESSIONS[sid].pending[rid]
    ev[1] = d.approved
    ev[0].set()
    return {"ok": True}


@app.post("/sessions/{sid}/messages")
async def message(sid: str, m: Msg):
    s = SESSIONS[sid]
    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(ev):
        loop.call_soon_threadsafe(q.put_nowait, ev)

    def ask(name, args):  # blocks the agent thread until the user decides (or timeout -> deny)
        rid = uuid.uuid4().hex[:8]
        s.pending[rid] = [threading.Event(), False]
        emit({"type": "approval_request", "request_id": rid, "tool": name, "arguments": args})
        s.pending[rid][0].wait(timeout=600)
        return s.pending.pop(rid)[1]

    def worker():
        with s.lock:
            try:
                ctx = Context(sandbox=s.sandbox, docs=s.docs, approve=ask)
                run(m.content, ctx, history=s.history, emit=emit, log=lambda *_: None)
            except Exception as e:
                emit({"type": "error", "message": str(e)})
            emit(None)

    threading.Thread(target=worker, daemon=True).start()

    async def sse():
        while (ev := await q.get()) is not None:
            yield f"event: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")
