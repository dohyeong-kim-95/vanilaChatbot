"""OpenAI-compatible gateway (MVP).

Exposes /v1/models and /v1/chat/completions. Backend = headless `claude -p` CLI.
The CLI has no native OpenAI tool calling, so tool calls are emulated with
a strict JSON reply protocol. Swap `call_backend` to point at a real
OpenAI-compatible model later; the agent side never changes.
"""
import asyncio
import json
import re
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

MODEL = os.environ.get("GATEWAY_MODEL", "claude-haiku-5-5")
EFFORT = os.environ.get("GATEWAY_EFFORT", "max")
API_KEY = os.environ.get("GATEWAY_API_KEY")  # optional bearer check
BACKEND = os.environ.get("GATEWAY_BACKEND", "cli")  # "cli" (claude -p, emulated tools) | "anthropic" (API key)

TOOL_PROTOCOL = """You are a text-completion backend behind an OpenAI-compatible API.
You CANNOT execute anything yourself and you have no built-in tools.
The caller executes tools for you. To use a tool, answer with
{"type":"tool_calls","tool_calls":[{"name":...,"arguments":{...}}]} and stop;
the results will come back in a later TOOL_RESULT turn. Never invent tool results.
You may put a short note in "content" alongside tool_calls (e.g. key facts found so far).
When you can answer without (more) tools, answer with {"type":"message","content":"..."}.
Respond with ONLY that JSON object as plain text: no prose, no code fences.
Available tools (JSON Schema):
"""

app = FastAPI()


def render_prompt(messages):
    """Flatten OpenAI messages into a transcript; return (system, transcript)."""
    system, lines = [], []
    for m in messages:
        role, content = m["role"], m.get("content")
        if isinstance(content, list):  # content parts -> text only (MVP)
            content = "".join(p.get("text", "") for p in content if p.get("type") == "text")
        if role in ("system", "developer"):
            system.append(content or "")
        elif role == "user":
            lines.append(f"USER:\n{content}")
        elif role == "assistant":
            if content:
                lines.append(f"ASSISTANT:\n{content}")
            for tc in m.get("tool_calls") or []:
                f = tc["function"]
                lines.append(f"ASSISTANT TOOL_CALL id={tc['id']} name={f['name']} arguments={f['arguments']}")
        elif role == "tool":
            lines.append(f"TOOL_RESULT id={m.get('tool_call_id')}:\n{content}")
    lines.append("Now produce the next ASSISTANT turn.")
    return "\n\n".join(system), "\n\n".join(lines)


async def call_backend(system, prompt, tools):
    if tools:
        system = system + "\n\n" + TOOL_PROTOCOL + json.dumps([t["function"] for t in tools], ensure_ascii=False)
    cmd = ["claude", "-p", "--model", MODEL, "--effort", EFFORT, "--tools", "",
           "--output-format", "json", "--no-session-persistence", "--strict-mcp-config",
           "--setting-sources", "", "--system-prompt", system or "You are a helpful assistant."]
    # Plain-text JSON, not --json-schema: with --json-schema the CLI exposes a tool to the model,
    # and the model then sometimes "calls" the caller's tools inside the CLI (fake results).
    # With zero tools that cannot happen; we parse the JSON and retry if it is malformed.
    for attempt in range(3):
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd="/tmp")
        out, err = await proc.communicate(prompt.encode())
        data = json.loads(out)
        if data.get("is_error"):
            raise RuntimeError(data.get("result") or err.decode())
        if data.get("num_turns", 1) > 1:  # should never happen with zero CLI tools
            print(f"[gateway] LEAK num_turns={data['num_turns']}", flush=True)
        if not tools:
            so = {"type": "message", "content": data["result"]}
            break
        try:
            so = json.loads(re.search(r"\{.*\}", data["result"], re.S).group(0))
            if so.get("type") in ("message", "tool_calls"):
                break
        except (AttributeError, ValueError):
            pass
        print(f"[gateway] malformed output, retry {attempt}: {data['result'][:200]!r}", flush=True)
    else:  # give up: surface the raw text as a normal message
        so = {"type": "message", "content": data["result"]}
    u = data.get("usage", {})
    usage = {"prompt_tokens": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
             + u.get("cache_creation_input_tokens", 0),
             "completion_tokens": u.get("output_tokens", 0)}
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return so, usage


def to_message(so):
    if so.get("type") == "tool_calls" and so.get("tool_calls"):
        calls = [{"id": "call_" + uuid.uuid4().hex[:24], "type": "function",
                  "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                 for c in so["tool_calls"]]
        return {"role": "assistant", "content": so.get("content") or None, "tool_calls": calls}, "tool_calls"
    return {"role": "assistant", "content": so.get("content", "")}, "stop"


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "gateway"}]}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    if API_KEY and req.headers.get("authorization") != f"Bearer {API_KEY}":
        return JSONResponse({"error": {"message": "invalid api key"}}, status_code=401)
    body = await req.json()
    tools = body.get("tools") or []
    if body.get("tool_choice") == "none":
        tools = []
    try:
        if BACKEND == "anthropic":
            from . import anthropic_backend
            msg, finish, usage = await anthropic_backend.complete(body["messages"], tools)
        else:
            system, prompt = render_prompt(body["messages"])
            so, usage = await call_backend(system, prompt, tools)
            msg, finish = to_message(so)
    except Exception as e:
        return JSONResponse({"error": {"message": str(e), "type": "backend_error"}}, status_code=502)
    cid, created = "chatcmpl-" + uuid.uuid4().hex, int(time.time())

    if not body.get("stream"):
        return {"id": cid, "object": "chat.completion", "created": created, "model": MODEL,
                "choices": [{"index": 0, "message": msg, "finish_reason": finish}], "usage": usage}

    # Pseudo-streaming: backend is non-streaming, so we chunk the final result
    # in the exact OpenAI SSE shape (clients' delta parsers get exercised).
    def chunk(delta, finish_reason=None, **extra):
        return {"id": cid, "object": "chat.completion.chunk", "created": created, "model": MODEL,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}], **extra}

    def events():
        yield chunk({"role": "assistant", "content": ""})
        text = msg.get("content") or ""
        for j in range(0, len(text), 20):
            yield chunk({"content": text[j:j + 20]})
        if msg.get("tool_calls"):
            for i, tc in enumerate(msg["tool_calls"]):
                a = tc["function"]["arguments"]
                yield chunk({"tool_calls": [{"index": i, "id": tc["id"], "type": "function",
                                             "function": {"name": tc["function"]["name"], "arguments": ""}}]})
                for j in range(0, len(a), 16):
                    yield chunk({"tool_calls": [{"index": i, "function": {"arguments": a[j:j + 16]}}]})
        yield chunk({}, finish)
        if (body.get("stream_options") or {}).get("include_usage"):
            yield {"id": cid, "object": "chat.completion.chunk", "created": created, "model": MODEL,
                   "choices": [], "usage": usage}

    async def sse():
        for ev in events():
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")
