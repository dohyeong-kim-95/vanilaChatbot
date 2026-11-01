"""Gateway errors and the app's retries (Loop 25.9): the app's client retries a 5xx 6 times (~20 s, so a gateway
restart in an update does not fail a run). An error that cannot get better (no API key, a rejected request) must
end at once, a rate limit or an upstream outage may be retried, and the upstream's Retry-After is passed on.
Deterministic, no network: the backend is replaced in process.

    python tests/test_l20_gateway_errors.py
"""
import os
import sys
import threading
import time

import anthropic
import httpx
import uvicorn
from openai import OpenAI

os.environ["GATEWAY_BACKEND"] = "anthropic"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gateway import anthropic_backend, app as gw  # noqa: E402

PORT = 8311
CALLS = []


def upstream(status, headers=None):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.Response(status, request=req, headers=headers or {}, json={"error": {"message": "x"}})


def fail_with(exc):
    async def complete(*a, **k):
        CALLS.append(1)
        raise exc
    anthropic_backend.complete = complete


def ask():
    """The app's client as agent/loop.py makes it. Returns (seconds, gateway calls)."""
    CALLS.clear()
    client = OpenAI(base_url=f"http://127.0.0.1:{PORT}/v1", api_key="k", max_retries=6)
    t0 = time.time()
    try:
        client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
    except Exception:
        pass
    return time.time() - t0, len(CALLS)


def t_missing_key_is_not_retried():
    fail_with(TypeError('"Could not resolve authentication method."'))
    took, calls = ask()
    assert calls == 1 and took < 2, (took, calls)


def t_rejected_request_is_not_retried():
    fail_with(anthropic.BadRequestError("bad", response=upstream(400), body=None))
    took, calls = ask()
    assert calls == 1, calls
    fail_with(anthropic.AuthenticationError("key", response=upstream(401), body=None))
    assert ask()[1] == 1


def t_outage_and_rate_limit_are_retried():
    fail_with(anthropic.InternalServerError("overloaded", response=upstream(529), body=None))
    assert ask()[1] > 1
    fail_with(anthropic.RateLimitError("slow down", response=upstream(429, {"retry-after": "0"}), body=None))
    assert ask()[1] > 1


def t_retry_after_is_passed_on():
    fail_with(anthropic.RateLimitError("slow down", response=upstream(429, {"retry-after": "7"}), body=None))
    r = httpx.post(f"http://127.0.0.1:{PORT}/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 502 and r.headers.get("retry-after") == "7" and "x-should-retry" not in r.headers, r.headers


if __name__ == "__main__":
    srv = uvicorn.Server(uvicorn.Config(gw.app, port=PORT, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    names = [n for n in sys.argv[1:]] or [n for n in list(globals()) if n.startswith("t_")]
    failed = 0
    for name in names:
        fn = globals().get(name)
        try:
            if fn is None:
                raise NameError(f"no test {name}")
            fn()
            print("PASS", name)
        except Exception as e:
            failed += 1
            print("FAIL", name, repr(e)[:400])
    sys.exit(1 if failed else 0)
