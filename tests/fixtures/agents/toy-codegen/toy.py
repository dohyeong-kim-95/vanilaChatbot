"""Mock codegen for Toy (Loop 40): a Python agent behind the same loop. First a bad line, then right."""
import json
from pathlib import Path


def run(task, inputs, ctx):
    errors = json.loads((ctx.workspace / inputs["errors"]).read_text()) if inputs.get("errors") else []
    out = ctx.workspace / inputs["out"]
    out.parent.mkdir(parents=True, exist_ok=True)
    body = "print hello\nprint world\n" if errors else "print hello\necho world\n"
    out.write_text(body)
    return f"wrote {Path(inputs['out']).name} ({len(errors)} errors seen)"
