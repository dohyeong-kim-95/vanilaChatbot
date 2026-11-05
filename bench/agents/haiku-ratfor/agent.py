"""A codegen agent on our weakest model (Loop 41): Haiku at low effort writes the language of its agent.json from
the task and the language's one-page REFERENCE.md, and after a failed attempt from its last program and the checks
it failed. One LLM call per attempt, through the host (ctx.llm, which the evaluation sets to Haiku low). It keeps no
memory between attempts but what the loop hands it. The same file for every language (Loop 43: haiku-ratfor,
haiku-hesk): only agent.json and REFERENCE.md differ.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
LANG = json.loads((HERE / "agent.json").read_text())["language"]
SYSTEM = (f"You write programs in {LANG.capitalize()}. Reply with the complete program in one ```{LANG} code block "
          "and nothing else.\n\n" + (HERE / "REFERENCE.md").read_text())


def run(task, inputs, ctx):
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
    if inputs.get("prev"):
        prev = (ctx.workspace / inputs["prev"]).read_text()
        errors = json.loads((ctx.workspace / inputs["errors"]).read_text())
        listed = "\n".join(f"- [{e['stage']}{'' if e['line'] is None else ' line %d' % e['line']}] {e['message']}"
                           for e in errors)
        msgs += [{"role": "assistant", "content": f"```{LANG}\n{prev}```"},
                 {"role": "user", "content": f"That program failed these checks:\n{listed}\n\nFix it. Reply with "
                                             "the complete program."}]
    text = ctx.llm(msgs)
    m = re.search(r"```[^\n]*\n(.*?)```", text, re.S)  # any fence tag: a slug may have digits and "-"
    code = (m[1] if m else text).strip() + "\n"
    out = ctx.workspace / inputs["out"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(code)
    return f"wrote {code.count(chr(10))} lines" + ("" if m else " (no code block in the reply: took all of it)")
