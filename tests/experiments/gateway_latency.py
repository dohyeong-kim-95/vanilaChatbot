"""Loop 2 experiment: gateway latency by effort (8100=max, 8101=low) and prompt size."""
import time, json
from openai import OpenAI
T=[{"type":"function","function":{"name":"bash","description":"run bash","parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}}}]
for port in (8100, 8101):
    c=OpenAI(base_url=f"http://127.0.0.1:{port}/v1",api_key="x")
    for pad in (0, 40000):
        msgs=[{"role":"user","content":"Context:\n"+("lorem ipsum "*(pad//12))+"\nTask: list files in /workspace."}]
        ts=[]
        for _ in range(2):
            t=time.time(); r=c.chat.completions.create(model="m",messages=msgs,tools=T); ts.append(round(time.time()-t,1))
        print(port, pad, ts, r.choices[0].finish_reason, r.usage.prompt_tokens)
