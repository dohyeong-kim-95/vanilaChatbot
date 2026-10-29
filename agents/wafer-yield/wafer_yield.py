"""Mock of an externally built agent: per-wafer yield from a bin CSV. Plain code, no LLM."""
import csv
from collections import Counter, defaultdict


def run(task, inputs, ctx):
    path = ctx.workspace / inputs["file"]
    passes = set(inputs.get("pass_bins") or [1])
    ctx.progress(f"reading {inputs['file']}")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    cols = {c.lower(): c for c in rows[0]}
    wcol, bcol = cols.get("wafer"), cols.get("bin") or cols.get("hard_bin") or cols.get("hbin")
    if not (wcol and bcol):
        raise ValueError(f"need wafer and bin columns, got {list(rows[0])}")
    ctx.progress(f"{len(rows)} dies; computing yield")
    total, good, fails = Counter(), Counter(), defaultdict(Counter)
    for r in rows:
        w, b = r[wcol], int(r[bcol])
        total[w] += 1
        if b in passes:
            good[w] += 1
        else:
            fails[w][b] += 1
    lines = ["| wafer | dies | pass | yield | top fail bins |", "|---|---|---|---|---|"]
    for w in sorted(total, key=lambda x: (len(x), x)):
        top = ", ".join(f"bin{b} {n}" for b, n in fails[w].most_common(3)) or "-"
        lines.append(f"| {w} | {total[w]} | {good[w]} | {100 * good[w] / total[w]:.1f}% | {top} |")
    overall = 100 * sum(good.values()) / sum(total.values())
    report = "# Wafer yield\n\n" + "\n".join(lines) + f"\n\nOverall: {overall:.1f}% ({sum(good.values())}/{sum(total.values())})\n"
    out = ctx.workspace / "outputs" / "yield.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    ctx.progress("wrote outputs/yield.md")
    worst = min(total, key=lambda w: good[w] / total[w])
    return (f"overall yield {overall:.1f}% over {len(total)} wafers; lowest {worst} "
            f"{100 * good[worst] / total[worst]:.1f}%. Table in outputs/yield.md\n\n" + "\n".join(lines))
