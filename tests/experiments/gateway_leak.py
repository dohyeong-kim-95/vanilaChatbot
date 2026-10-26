"""Loop 4 experiment: CLI-internal tool-call leak rate, --json-schema vs plain-text JSON protocol."""
import json, subprocess, sys, re
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), "..", ".."))
from gateway.app import TOOL_PROTOCOL
OUTPUT_SCHEMA = {"type": "object", "properties": {"type": {"enum": ["message", "tool_calls"]}, "content": {"type": "string"}, "tool_calls": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}}, "required": ["name", "arguments"]}}}, "required": ["type"]}
tools=[{"name":"read_file","description":"Read a file","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}]
sysmsg="You are an agent.\n\n"+TOOL_PROTOCOL+json.dumps(tools)
prompt="USER:\nRead log0.txt, log1.txt one at a time, then tell me the deploy code.\n\nASSISTANT TOOL_CALL id=1 name=read_file arguments={\"path\":\"log0.txt\"}\n\nTOOL_RESULT id=1:\n"+"log line: nothing\n"*300+"\n\nNow produce the next ASSISTANT turn."
def run(mode):
    cmd=["claude","-p","--model","claude-haiku-5-5","--effort","low","--tools","","--output-format","json","--no-session-persistence","--strict-mcp-config","--setting-sources",""]
    s=sysmsg
    if mode=="schema": cmd+=["--json-schema",json.dumps(OUTPUT_SCHEMA)]
    else: s+="\n\nRespond with ONLY that JSON object as plain text, no prose, no code fences."
    d=json.loads(subprocess.run(cmd+["--system-prompt",s],input=prompt,capture_output=True,text=True,cwd="/tmp").stdout)
    ok=False
    try:
        r=d["result"]; m=re.search(r"\{.*\}",r,re.S); o=json.loads(m.group(0)); ok=o.get("type")=="tool_calls" and o["tool_calls"][0]["name"]=="read_file"
    except Exception: pass
    return d["num_turns"], ok
for mode in ("schema","text"):
    res=[run(mode) for _ in range(8)]
    print(mode, res)
