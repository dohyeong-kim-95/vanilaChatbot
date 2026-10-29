"""Skills (Loop 15), after Claude's Agent Skills: a folder with SKILL.md (frontmatter name + description, then
instructions) and scripts. Progressive disclosure: only name + description go into the system prompt; the agent
loads the body with use_skill when a task matches, and runs the scripts in the sandbox, where the skills folder
is mounted read-only at /skills. Skills are trusted code and instructions (shipped with the server), unlike
anything the sandbox or the user's files produce.
"""
import re
from pathlib import Path

DIR = Path(__file__).resolve().parents[1] / "skills"
MOUNT = "/skills"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        raise ValueError("SKILL.md must start with a --- frontmatter block")
    meta = dict(line.split(":", 1) for line in m[1].splitlines() if ":" in line)
    return {k.strip(): v.strip() for k, v in meta.items()}, m[2].strip()


def discover(root=DIR):
    """name -> {name, description, body, path}. Invalid skills fail loudly: they are our own files."""
    out = {}
    for f in sorted(Path(root).glob("*/SKILL.md")):
        meta, body = _frontmatter(f.read_text(encoding="utf-8"))
        name = meta.get("name", "")
        if not NAME_RE.match(name) or name != f.parent.name:
            raise ValueError(f"{f}: name {name!r} must be a slug equal to its folder name")
        if not meta.get("description"):
            raise ValueError(f"{f}: description is required")
        out[name] = {"name": name, "description": meta["description"], "body": body, "path": f.parent}
    return out


def prompt(skills):
    if not skills:
        return ""
    return ("## Skills\nTested instructions and scripts for recurring tasks. When a task matches a skill, call "
            "use_skill with its name FIRST and follow it instead of writing your own code for what it covers.\n" +
            "\n".join(f"- {s['name']}: {s['description']}" for s in skills.values()))
