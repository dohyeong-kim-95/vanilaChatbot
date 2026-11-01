"""OpenAI-compatible stand-in for tests/test_l19_deploy.py (stdlib only): the first answer runs `id` in the sandbox
and writes outputs/deployed.txt, the second ends the run quoting the tool result."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"object": "list", "data": [{"id": "fake", "object": "model"}]})

    def do_POST(self):
        msgs = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["messages"]
        if not any(m["role"] == "tool" for m in msgs):
            args = {"command": "id && echo ok > outputs/deployed.txt"}
            msg = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "bash", "arguments": json.dumps(args)}}]}
        else:
            msg = {"role": "assistant", "content": "done: " + [m for m in msgs if m["role"] == "tool"][-1]["content"]}
        self._send({"id": "x", "object": "chat.completion", "created": 0, "model": "fake",
                    "choices": [{"index": 0, "finish_reason": "stop", "message": msg}]})


HTTPServer(("127.0.0.1", 9100), H).serve_forever()
