"""Text logs -> counts over time by level and by message pattern (numbers, ids, IPs, quoted text masked;
paths keep their shape, e.g. /api/orders/<n>).
Standard library only. The server's show_logview builds its view from the same code. No raw lines are kept.

python logs.py LOG [--bucket 60]     summary: time range, levels, top patterns, busiest buckets
Python:  import sys; sys.path.insert(0, "/skills/log-triage/scripts"); import logs
         entries, _ = logs.parse(path); a = logs.aggregate(entries)   # a["levels"], a["patterns"], a["buckets"]
"""
import datetime as dt
import re
import sys
from collections import Counter, defaultdict

MAX_LINES = 3_000_000  # about 240 B per entry in memory: more does not fit the sandbox's 1 GB (Loop 38)
MAX_PATTERNS = 300
MAX_BUCKETS = 2000
LEVELS = ["FATAL", "ERROR", "WARN", "INFO", "DEBUG", "-"]
LEVEL_RE = re.compile(r"\b(FATAL|CRITICAL|SEVERE|ERROR|ERR|WARNING|WARN|INFO|NOTICE|DEBUG|TRACE)\b")
LEVEL_MAP = {"CRITICAL": "FATAL", "SEVERE": "FATAL", "ERR": "ERROR", "WARNING": "WARN", "NOTICE": "INFO",
             "TRACE": "DEBUG"}
MONTHS = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
STAMPS = [  # (regex, parser) tried in order on each line
    (re.compile(r"(\d{4})[-/.](\d{2})[-/.](\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:[.,](\d{1,6}))?"),
     lambda m: dt.datetime(*map(int, m.groups()[:6]))),
    (re.compile(r"\[(\d{2})/([A-Z][a-z]{2})/(\d{4}):(\d{2}):(\d{2}):(\d{2})(?: [+-]\d{4})?\]"),  # access log
     lambda m: dt.datetime(int(m[3]), MONTHS[m[2]], int(m[1]), int(m[4]), int(m[5]), int(m[6]))),
    (re.compile(r"^([A-Z][a-z]{2}) +(\d{1,2}) (\d{2}):(\d{2}):(\d{2})"),  # syslog (no year)
     lambda m: dt.datetime(dt.date.today().year, MONTHS[m[1]], int(m[2]), int(m[3]), int(m[4]), int(m[5]))),
    (re.compile(r"^(1[5-9]\d{8})(?:\.\d+)?\b"), lambda m: dt.datetime.fromtimestamp(int(m[1]))),  # epoch, line start
]
SYSLOG = re.compile(r"^[A-Z][a-z]{2} +\d{1,2} \d{2}:\d{2}:\d{2}")
HTTP_STATUS = re.compile(r'"\s(\d{3})\s')
BUCKETS = [1, 5, 10, 30, 60, 300, 600, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400, 7 * 86400]


def shape(msg):
    """Message template: what repeats once the variable parts are masked."""
    msg = re.sub(r'"([A-Z]{3,7}) (\S+) HTTP/[\d.]+"', r"\1 \2", msg)  # access log request line: keep method + path
    msg = re.sub(r"\b\d{1,3}(\.\d{1,3}){3}(:\d+)?\b", "<ip>", msg)
    msg = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<uuid>", msg, flags=re.I)
    msg = re.sub(r"0x[0-9a-fA-F]+|\b[0-9a-f]{8,}\b", "<id>", msg)
    msg = re.sub(r"\"[^\"]*\"|'[^']*'", "<str>", msg)
    msg = re.sub(r"\d+(\.\d+)?", "<n>", msg)
    return re.sub(r"\s+", " ", msg).strip()[:160] or "(empty)"


def _stamp(line):
    for rx, fn in STAMPS:
        m = rx.search(line)
        if m:
            try:
                return fn(m), m.end()
            except (ValueError, KeyError):
                continue
    return None, 0


def parse(path):
    """-> (entries [(datetime, level, pattern)], continuation line count). Lines without a timestamp (stack traces,
    wrapped text) belong to the entry before them and are not counted separately."""
    out, skipped, year_shift, last = [], 0, 0, None
    with open(path, "rb") as fh:
        for n, raw in enumerate(fh):
            if n >= MAX_LINES:
                raise ValueError(f"log has more than {MAX_LINES:,} lines: use ingest_log (format text) and log_query")
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line.strip():
                continue
            ts, end = _stamp(line)
            if ts is None:
                skipped += 1
                continue
            if SYSLOG.match(line):  # no year in syslog: going back in time means the year rolled over
                ts = ts.replace(year=ts.year + year_shift)
                if last and ts < last - dt.timedelta(days=1):
                    year_shift += 1
                    ts = ts.replace(year=ts.year + 1)
                last = ts
            rest = line[end:]
            if m := LEVEL_RE.search(rest[:80]):         # "<time> ERROR message"
                level, msg = LEVEL_MAP.get(m[1], m[1]), rest[m.end():]
            elif m := LEVEL_RE.search(line[:end]):      # "ERROR <time> message"
                level, msg = LEVEL_MAP.get(m[1], m[1]), rest
            elif h := HTTP_STATUS.search(rest):         # access log: level from the status code
                code = int(h[1])
                level, msg = ("ERROR" if code >= 500 else "WARN" if code >= 400 else "INFO"), rest
            else:
                level, msg = "-", rest
            out.append((ts, level, shape(msg.lstrip(" :-]|"))))
    return out, skipped


def aggregate(entries, bucket=None):
    if not entries:
        raise ValueError("no timestamped lines found")
    entries = sorted(entries, key=lambda e: e[0])
    t0, t1 = entries[0][0], entries[-1][0]
    span = max((t1 - t0).total_seconds(), 1)
    bucket = bucket or next((b for b in BUCKETS if span / b <= 120), BUCKETS[-1])
    if bucket <= 0:
        raise ValueError("bucket_seconds must be positive")
    start = dt.datetime.fromtimestamp(t0.timestamp() // bucket * bucket)
    n = int((t1.timestamp() - start.timestamp()) // bucket) + 1
    if n > MAX_BUCKETS:  # a 1 s bucket over 9 months was 23 M counters, 4 GB in the server
        raise ValueError(f"{n:,} time buckets; use a larger bucket_seconds (at most {MAX_BUCKETS:,} buckets)")
    levels = Counter(e[1] for e in entries)
    by_bucket = [Counter() for _ in range(n)]
    pats = defaultdict(lambda: {"count": 0, "levels": Counter(), "first": None, "last": None, "series": Counter()})
    for ts, level, pat in entries:
        b = int((ts.timestamp() - start.timestamp()) // bucket)
        by_bucket[b][level] += 1
        p = pats[pat]
        p["count"] += 1
        p["levels"][level] += 1
        p["first"] = p["first"] or ts
        p["last"] = ts
        p["series"][b] += 1
    ranked = sorted(pats.items(), key=lambda kv: (-severity(kv[1]["levels"]), -kv[1]["count"]))
    keep, rest = ranked[:MAX_PATTERNS], ranked[MAX_PATTERNS:]
    patterns = [{"pattern": k, "count": v["count"], "level": v["levels"].most_common(1)[0][0],
                 "first": v["first"].isoformat(sep=" "), "last": v["last"].isoformat(sep=" "),
                 "series": sorted(v["series"].items())} for k, v in keep]
    if rest:
        other = Counter()
        for _, v in rest:
            other.update(v["series"])
        patterns.append({"pattern": f"(기타 패턴 {len(rest)}개)", "count": sum(v["count"] for _, v in rest),
                         "level": "-", "first": "", "last": "", "series": sorted(other.items())})
    return {"start": start.isoformat(sep=" "), "end": t1.isoformat(sep=" "), "bucket_seconds": bucket,
            "lines": len(entries), "levels": {l: levels[l] for l in LEVELS if levels[l]},
            "buckets": [{l: c[l] for l in LEVELS if c[l]} for c in by_bucket], "patterns": patterns}


def severity(levels):
    """Rank key: patterns with errors first."""
    return max((len(LEVELS) - LEVELS.index(l) for l in levels), default=0)


def summary(a, top=12):
    fmt = lambda s: f"{s // 3600}h" if s >= 3600 else f"{s // 60}m" if s >= 60 else f"{s}s"  # noqa: E731
    lines = [f"{a['lines']:,} entries, {a['start']} .. {a['end']}, bucket {fmt(a['bucket_seconds'])} "
             f"({len(a['buckets'])} buckets), levels {a['levels']}, {len(a['patterns'])} patterns"]
    lines.append("top patterns (errors first):")
    for p in a["patterns"][:top]:
        lines.append(f"  {p['level']:5} {p['count']:>7,}  {p['first'][5:16]}..{p['last'][5:16]}  {p['pattern'][:110]}")
    err = [(i, sum(c.get(l, 0) for l in ("FATAL", "ERROR"))) for i, c in enumerate(a["buckets"])]
    busy = sorted((x for x in err if x[1]), key=lambda x: -x[1])[:3]
    if busy:
        t = lambda i: (dt.datetime.fromisoformat(a["start"]) + dt.timedelta(seconds=i * a["bucket_seconds"])).strftime("%m-%d %H:%M:%S")  # noqa: E731
        lines.append("most errors: " + ", ".join(f"{t(i)} ({c})" for i, c in busy))
    return "\n".join(lines)


def main(argv):
    bucket = int(argv[argv.index("--bucket") + 1]) if "--bucket" in argv else None
    path = [a for a in argv if not a.startswith("--") and not a.isdigit()][0]
    e, skipped = parse(path)
    print(summary(aggregate(e, bucket)))
    if skipped:
        print(f"{skipped} lines without a timestamp were treated as continuation lines")


if __name__ == "__main__":
    main(sys.argv[1:])
