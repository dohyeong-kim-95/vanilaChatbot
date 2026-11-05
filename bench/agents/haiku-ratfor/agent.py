"""A codegen agent on our weakest model (Loop 41): Haiku at low effort writes Ratfor from the task, and after a
failed attempt from its last program and the checks it failed. One LLM call per attempt, through the host
(ctx.llm, which the evaluation sets to Haiku low). It keeps no memory between attempts but what the loop hands it.
"""
import json
import re
from pathlib import Path

REFERENCE = (Path(__file__).parent / "RATFOR.md").read_text()
SYSTEM = ("You write programs in Ratfor. Reply with the complete program in one ```ratfor code block and nothing "
          "else.\n\n" + REFERENCE)


def run(task, inputs, ctx):
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
    if inputs.get("prev"):
        prev = (ctx.workspace / inputs["prev"]).read_text()
        errors = json.loads((ctx.workspace / inputs["errors"]).read_text())
        listed = "\n".join(f"- [{e['stage']}{'' if e['line'] is None else ' line %d' % e['line']}] {e['message']}"
                           for e in errors)
        msgs += [{"role": "assistant", "content": f"```ratfor\n{prev}```"},
                 {"role": "user", "content": f"That program failed these checks:\n{listed}\n\nFix it. Reply with "
                                             "the complete program."}]
    text = ctx.llm(msgs)
    m = re.search(r"```[a-zA-Z]*\n(.*?)```", text, re.S)
    code = (m[1] if m else text).strip() + "\n"
    out = ctx.workspace / inputs["out"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(code)
    return f"wrote {code.count(chr(10))} lines" + ("" if m else " (no code block in the reply: took all of it)")
