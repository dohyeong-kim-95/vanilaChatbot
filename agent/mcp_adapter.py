"""MCP adapter: register an MCP server's tools in the agent's tool registry (sync facade over the async SDK).

Trust model (MCP tool annotations are *hints from the server*, not guarantees):
- every MCP tool needs human approval, unless the operator marked the server trusted AND the tool says readOnlyHint
- tool descriptions/schemas are prompt input -> pinned by hash; a changed tool ("rug pull") is not registered
- results go through the same truncate/redact/spotlight path as built-in tools
"""
import asyncio
import hashlib
import json
import re
import threading

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from .tools import REGISTRY


def pin(t):
    return hashlib.sha256(json.dumps([t.name, t.description, t.input_schema], sort_keys=True).encode()).hexdigest()[:16]


class MCPServerTools:
    def __init__(self, name, command, args=(), trusted=False, pins=None, env=None):
        self.name, self.trusted, self.expected = name, trusted, pins
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.stop, ready = None, threading.Event()
        self.task = asyncio.run_coroutine_threadsafe(
            self._hold(StdioServerParameters(command=command, args=list(args), env=env), ready), self.loop)
        if not ready.wait(30):
            raise RuntimeError(f"MCP server {name} did not start: {self.task.exception(0) if self.task.done() else ''}")
        self.pins, self.registered, self.rejected = {}, [], []
        cursor = None
        while True:  # tools/list is paginated
            page = self._call(self.client.list_tools(cursor=cursor))
            for t in page.tools:
                self._register(t)
            if not (cursor := page.next_cursor):
                break

    async def _hold(self, params, ready):  # own the client context in one task (anyio scopes must not cross tasks)
        self.stop = asyncio.Event()
        async with Client(params) as c:
            self.client = c
            ready.set()
            await self.stop.wait()

    def _call(self, coro, timeout=120):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def _register(self, t):
        self.pins[t.name] = pin(t)
        if self.expected is not None and self.expected.get(t.name) != self.pins[t.name]:
            self.rejected.append(t.name)  # new or changed since review: do not show it to the model
            return
        ro = bool(t.annotations and t.annotations.read_only_hint)
        local = re.sub(r"[^a-zA-Z0-9_-]", "_", f"mcp__{self.name}__{t.name}")[:64]
        schema = dict(t.input_schema or {"type": "object", "properties": {}})
        REGISTRY[local] = {
            "fn": lambda ctx, _n=t.name, **args: self.call(_n, args),
            "approval": not (self.trusted and ro),
            "schema": {"type": "function", "function": {
                "name": local, "description": f"[MCP {self.name}] {t.description or ''}"[:1024], "parameters": schema}}}
        self.registered.append(local)

    def call(self, tool, args):
        r = self._call(self.client.call_tool(tool, args))
        parts = [c.text if getattr(c, "type", "") == "text" else f"[{getattr(c, 'type', '?')} content omitted]"
                 for c in r.content or []]
        if r.structured_content is not None and not parts:
            parts.append(json.dumps(r.structured_content, ensure_ascii=False))
        out = "\n".join(parts)
        return f"ERROR: {out}" if r.is_error else out

    def close(self):
        for n in self.registered:
            REGISTRY.pop(n, None)
        self.loop.call_soon_threadsafe(self.stop.set)
        try:
            self.task.result(10)
        except Exception:
            pass
