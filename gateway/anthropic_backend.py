"""Anthropic Messages API backend for the gateway (native tool use, no emulation).

OpenAI Chat Completions <-> Anthropic Messages translation:
- system/developer messages -> top-level `system`
- assistant.tool_calls       -> `tool_use` blocks (ids pass through unchanged)
- consecutive role:"tool"    -> ONE user message of `tool_result` blocks (keeps parallel calls working)
- thinking blocks            -> OpenAI clients drop them, but they must be replayed unchanged, so we cache
                                each assistant turn's full content by its tool_call ids and restore it.
"""
import json
import os

import anthropic

MODEL = os.environ.get("GATEWAY_MODEL", "claude-haiku-5-5")
EFFORT = os.environ.get("GATEWAY_EFFORT", "max")
client = anthropic.AsyncAnthropic()  # ANTHROPIC_API_KEY from env
TURNS = {}  # tuple(tool_call ids) -> full assistant content blocks (in-memory, MVP)


def to_anthropic(messages):
    system, out = [], []
    for m in messages:
        role, content = m["role"], m.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if p.get("type") == "text")
        if role in ("system", "developer"):
            system.append(content or "")
        elif role == "user":
            out.append({"role": "user", "content": content or ""})
        elif role == "assistant":
            calls = m.get("tool_calls") or []
            key = tuple(c["id"] for c in calls)
            if key and key in TURNS:
                out.append({"role": "assistant", "content": TURNS[key]})
                continue
            blocks = [{"type": "text", "text": content}] if content else []
            blocks += [{"type": "tool_use", "id": c["id"], "name": c["function"]["name"],
                        "input": json.loads(c["function"]["arguments"] or "{}")} for c in calls]
            out.append({"role": "assistant", "content": blocks or ""})
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": content or ""}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return "\n\n".join(system), out


async def complete(messages, tools, tool_choice=None):
    """Returns (openai_message, finish_reason, usage)."""
    system, msgs = to_anthropic(messages)
    kw = {}
    if tools and tool_choice != "none":
        kw["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                        "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}}
                       for t in tools]
    if system:
        kw["system"] = system
    # streaming + get_final_message: effort=max can think for a long time.
    # drop_block: agent-side compaction edits history, which invalidates restored thinking blocks
    # ("preserved thinking" check). Instead of a 400, the API drops the stale blocks and reports them.
    async with client.beta.messages.stream(
            model=MODEL, max_tokens=64000, messages=msgs, output_config={"effort": EFFORT},
            thinking={"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}},
            betas=["thinking-binding-controls-2026-08-01"],
            cache_control={"type": "ephemeral"}, **kw) as stream:
        resp = await stream.get_final_message()
    dropped = [t for t in (getattr(resp, "input_transformations", None) or [])
               if getattr(t, "type", None) == "thinking_dropped"]
    if dropped:
        print(f"[anthropic] thinking_dropped x{len(dropped)} reason={getattr(dropped[0], 'reason', '?')}", flush=True)

    text = "".join(b.text for b in resp.content if b.type == "text")
    calls = [{"id": b.id, "type": "function",
              "function": {"name": b.name, "arguments": json.dumps(b.input, ensure_ascii=False)}}
             for b in resp.content if b.type == "tool_use"]
    if calls:
        TURNS[tuple(c["id"] for c in calls)] = [b.model_dump(exclude_none=True) for b in resp.content]
    finish = {"tool_use": "tool_calls", "max_tokens": "length", "refusal": "content_filter"}.get(
        resp.stop_reason, "stop")
    msg = {"role": "assistant", "content": text or None}
    if calls:
        msg["tool_calls"] = calls
    u = resp.usage
    cached = u.cache_read_input_tokens or 0
    prompt = u.input_tokens + cached + (u.cache_creation_input_tokens or 0)
    print(f"[anthropic] stop={resp.stop_reason} in={u.input_tokens} cache_read={cached} "
          f"cache_write={u.cache_creation_input_tokens or 0} out={u.output_tokens}", flush=True)
    usage = {"prompt_tokens": prompt, "completion_tokens": u.output_tokens,
             "total_tokens": prompt + u.output_tokens, "prompt_tokens_details": {"cached_tokens": cached}}
    return msg, finish, usage
