"""Minimal RAG: chunk -> SQLite FTS5 (BM25). Embedding/hybrid search is a later swap behind `search`."""
import sqlite3
from pathlib import Path


class DocStore:
    def __init__(self, db=":memory:"):
        self.db = sqlite3.connect(db)
        self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(source, text, tokenize='trigram')")

    def add_text(self, source, text, size=800, overlap=150):
        step = size - overlap
        rows = [(source, text[i:i + size]) for i in range(0, max(len(text) - overlap, 1), step)]
        self.db.executemany("INSERT INTO chunks VALUES (?, ?)", rows)
        self.db.commit()
        return len(rows)

    def add_file(self, path):
        return self.add_text(Path(path).name, Path(path).read_text(encoding="utf-8"))

    def search(self, query, k=4):
        # OR the terms so natural-language queries still match; trigram needs >=3 chars
        terms = [t.replace('"', "") for t in query.split() if len(t) >= 3]
        if not terms:
            return []
        q = " OR ".join(f'"{t}"' for t in terms)
        cur = self.db.execute(
            "SELECT source, text, bm25(chunks) FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks) LIMIT ?", (q, k))
        return [{"source": s, "text": t, "score": round(-b, 3)} for s, t, b in cur]
