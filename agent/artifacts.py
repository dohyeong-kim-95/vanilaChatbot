"""Artifacts (Loop 14): standalone content the user views next to the chat (a report, a dashboard, a diagram),
versioned. Modelled on Claude's artifacts: the model creates one, then updates it by exact-snippet replacement
or rewrites it; every change is a new immutable version.

Stored host-side (never inside the sandbox): {dir}/{id}/{n}.json. Rendering is someone else's job
(server/render.py): content is untrusted and only ever runs in a sandboxed iframe on a separate origin.
"""
import json
import time
from pathlib import Path

from .userstore import ID_RE

TYPES = {"html", "markdown", "svg", "mermaid", "code", "report"}  # report: JSON for agent/report.py (show_report)
MAX_SIZE = 1024 * 1024
MAX_VERSIONS = 100


class ArtifactStore:
    def __init__(self, root, on_save=lambda meta: None, source=""):
        """on_save(meta): called after each new version (the server turns it into an `artifact` event).
        source: provenance recorded with each version (session/run)."""
        self.root, self.on_save, self.source = Path(root), on_save, source

    def _dir(self, aid):
        if not isinstance(aid, str) or not ID_RE.match(aid):
            raise ValueError(f"invalid artifact id {aid!r}: use lowercase letters, digits, - and _")
        return self.root / aid

    def versions(self, aid):
        d = self._dir(aid)
        return sorted(int(p.stem) for p in d.glob("*.json")) if d.exists() else []

    def get(self, aid, version=None):
        vs = self.versions(aid)
        if not vs:
            raise KeyError(f"no artifact {aid!r}")
        n = vs[-1] if version is None else int(version)
        if n not in vs:
            raise KeyError(f"artifact {aid!r} has no version {n}")
        return {"id": aid, "version": n, **json.loads((self._dir(aid) / f"{n}.json").read_text(encoding="utf-8"))}

    def list(self):
        if not self.root.exists():
            return []
        out = []
        for d in sorted(self.root.iterdir()):
            if (vs := self.versions(d.name)):
                a = self.get(d.name)
                out.append({"id": a["id"], "title": a["title"], "type": a["type"], "version": a["version"],
                            "versions": vs, "created": a["created"]})
        return sorted(out, key=lambda a: a["created"], reverse=True)

    def save(self, aid, type, title, content, language=None, command="create"):
        if type not in TYPES:
            raise ValueError(f"type must be one of {sorted(TYPES)}")
        if len(content.encode()) > MAX_SIZE:
            raise ValueError(f"artifact exceeds {MAX_SIZE} bytes")
        vs = self.versions(aid)
        if len(vs) >= MAX_VERSIONS:
            raise ValueError(f"artifact {aid!r} has {MAX_VERSIONS} versions; create a new one")
        n = (vs[-1] if vs else 0) + 1
        d = self._dir(aid)
        d.mkdir(parents=True, exist_ok=True)
        rec = {"type": type, "title": title[:200], "language": language, "content": content, "command": command,
               "source": self.source, "created": time.time()}
        with open(d / f"{n}.json", "x", encoding="utf-8") as f:  # "x": a version is never overwritten
            json.dump(rec, f, ensure_ascii=False)
        meta = {"id": aid, "version": n, "type": type, "title": rec["title"], "command": command}
        self.on_save(meta)
        return meta
