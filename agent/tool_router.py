"""Tool routing for large tool sets (many MCP servers). Hybrid strategy from Loop 11:
pinned core tools + top-k retrieved on the user's message + a `search_tools` fallback the model can call
(it writes the query itself, so language mismatch and retrieval misses are recoverable)."""
import math
import re
from collections import Counter

from .tools import REGISTRY, schemas


def toks(s):
    return re.findall(r"[a-z0-9]+|[가-힣]+", s.lower().replace("_", " "))


class ToolRouter:
    def __init__(self, names=None, pinned=(), k=8):
        self.names = [n for n in (names or REGISTRY) if n != "search_tools"]
        self.pinned, self.k, self.loaded = set(pinned), k, set()
        self.docs = {n: toks(n + " " + REGISTRY[n]["schema"]["function"]["description"][:300] + " "
                             + " ".join(REGISTRY[n]["schema"]["function"]["parameters"].get("properties", {})))
                     for n in self.names}
        self.df = Counter(w for d in self.docs.values() for w in set(d))
        self.avg = sum(map(len, self.docs.values())) / max(len(self.docs), 1)

    def search(self, query, k=5):
        N, q, out = len(self.docs), toks(query), []
        for n, d in self.docs.items():
            tf = Counter(d)
            s = sum(math.log(1 + (N - self.df[w] + .5) / (self.df[w] + .5)) * tf[w] * 2.2
                    / (tf[w] + 1.2 * (.25 + .75 * len(d) / self.avg)) for w in q if w in tf)
            if s > 0:
                out.append((s, n))
        found = [n for _, n in sorted(out, reverse=True)[:k]]
        self.loaded.update(found)
        return found

    def start(self, task):
        self.loaded = set(self.pinned)
        self.search(task, self.k)

    def schemas(self):
        return schemas(self.pinned | self.loaded | {"search_tools"})
