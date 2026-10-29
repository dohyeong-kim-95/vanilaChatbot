"""Synthetic application log (Loop 18): 6 hours of an order service with known incidents (ground truth).
  steady INFO traffic, a WARN every 10 minutes (disk usage)
  02:00 deploy -> new ERROR pattern "NullPointerException in OrderService.validate line <n>" every ~3 min after
  03:10-03:25 incident: burst of "timeout after <n>ms calling payment-api at <ip>" ERRORs (+ retries as WARN)
  stack-trace continuation lines (no timestamp) after each NullPointerException
  a line with markup in the message (must stay text in the view)
python tests/eval/logview/gen.py OUT.log
"""
import datetime as dt
import random
import sys
from collections import Counter
from pathlib import Path

SEED = 18
START = dt.datetime(2026, 10, 29, 0, 0, 0)
HOURS = 6
DEPLOY = START + dt.timedelta(hours=2)
INCIDENT = (START + dt.timedelta(hours=3, minutes=10), START + dt.timedelta(hours=3, minutes=25))
MARKUP = '<img src=x onerror=alert(1)> user-agent "<script>alert(2)</script>"'


def generate():
    rng = random.Random(SEED)
    ev = []  # (time, level, message, continuation lines)
    t = START
    while t < START + dt.timedelta(hours=HOURS):
        t += dt.timedelta(seconds=rng.expovariate(1 / 4))
        ev.append((t, "INFO", rng.choice([f"order {rng.randrange(10**6)} created by user {rng.randrange(5000)}",
                                         f"GET /api/orders/{rng.randrange(10**6)} 200 {rng.randrange(5, 90)}ms",
                                         f"cache hit ratio {rng.random():.2f}"]), []))
    for k in range(HOURS * 6):
        ev.append((START + dt.timedelta(minutes=10 * k, seconds=30), "WARN",
                   f"disk usage {80 + k % 15}% on /var/lib/orders", []))
    t = DEPLOY + dt.timedelta(seconds=40)
    while t < START + dt.timedelta(hours=HOURS):
        ev.append((t, "ERROR", f"NullPointerException in OrderService.validate line {rng.choice([88, 91])}",
                   ["\tat com.shop.OrderService.validate(OrderService.java:88)", "\tat com.shop.Api.post(Api.java:41)"]))
        t += dt.timedelta(seconds=rng.randrange(120, 240))
    t = INCIDENT[0]
    while t < INCIDENT[1]:
        ev.append((t, "ERROR", f"timeout after {rng.randrange(3000, 9000)}ms calling payment-api at "
                               f"10.2.{rng.randrange(4)}.{rng.randrange(1, 250)}:8443", []))
        if rng.random() < 0.5:
            ev.append((t + dt.timedelta(milliseconds=300), "WARN", f"retry {rng.randrange(1, 4)}/3 payment-api", []))
        t += dt.timedelta(seconds=rng.uniform(1, 6))
    ev.append((START + dt.timedelta(hours=1, minutes=5), "INFO", f"request from {MARKUP}", []))
    return sorted(ev, key=lambda e: e[0])


def write(path):
    ev = generate()
    with open(path, "w", encoding="utf-8") as fh:
        for t, lvl, msg, cont in ev:
            fh.write(f"{t:%Y-%m-%d %H:%M:%S},{t.microsecond // 1000:03d} {lvl:5} [order-svc] {msg}\n")
            for c in cont:
                fh.write(c + "\n")
    return ev


def truth(ev):
    levels = Counter(e[1] for e in ev)
    npe = [e[0] for e in ev if e[2].startswith("NullPointerException")]
    timeouts = [e[0] for e in ev if e[2].startswith("timeout after")]
    return {"levels": dict(levels), "lines": len(ev), "npe_count": len(npe), "npe_first": npe[0],
            "timeout_count": len(timeouts), "timeout_first": timeouts[0], "timeout_last": timeouts[-1]}


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/app.log")
    print(out, truth(write(out)))
