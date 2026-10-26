"""Loop 1: gateway compatibility, using the official openai SDK as the client."""
import json
import os

from openai import OpenAI

c = OpenAI(base_url=os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8100/v1"), api_key="dummy")
M = c.models.list().data[0].id
WEATHER = [{"type": "function", "function": {
    "name": "get_weather", "description": "Current weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]


def t_plain():
    r = c.chat.completions.create(model=M, messages=[{"role": "user", "content": "Reply with exactly: PONG"}])
    assert "PONG" in r.choices[0].message.content, r


def t_tool_call():
    msgs = [{"role": "user", "content": "What's the weather in Seoul?"}]
    r = c.chat.completions.create(model=M, messages=msgs, tools=WEATHER)
    m = r.choices[0].message
    assert r.choices[0].finish_reason == "tool_calls" and m.tool_calls, r
    tc = m.tool_calls[0]
    assert tc.function.name == "get_weather" and "seoul" in json.loads(tc.function.arguments)["city"].lower()
    # round-trip the tool result
    msgs += [m.model_dump(exclude_none=True),
             {"role": "tool", "tool_call_id": tc.id, "content": '{"temp_c": 17, "sky": "clear"}'}]
    r2 = c.chat.completions.create(model=M, messages=msgs, tools=WEATHER)
    assert r2.choices[0].finish_reason == "stop" and "17" in r2.choices[0].message.content, r2


def t_parallel():
    r = c.chat.completions.create(model=M, tools=WEATHER, messages=[
        {"role": "user", "content": "Weather in Seoul and Busan? Call tools for both at once."}])
    names = [json.loads(t.function.arguments)["city"] for t in r.choices[0].message.tool_calls or []]
    assert len(names) == 2, names


def t_stream_tool():
    s = c.chat.completions.create(model=M, tools=WEATHER, stream=True, stream_options={"include_usage": True},
                                  messages=[{"role": "user", "content": "Weather in Tokyo?"}])
    args, finish, usage = {}, None, None
    for ch in s:
        if ch.usage:
            usage = ch.usage
        if not ch.choices:  # usage chunk has choices=[]
            continue
        for d in ch.choices[0].delta.tool_calls or []:
            args[d.index] = args.get(d.index, "") + (d.function.arguments or "")
        finish = ch.choices[0].finish_reason or finish
    assert finish == "tool_calls" and json.loads(args[0])["city"] and usage, (args, finish)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("t_"):
            try:
                fn(); print("PASS", name)
            except Exception as e:
                print("FAIL", name, repr(e)[:400])
