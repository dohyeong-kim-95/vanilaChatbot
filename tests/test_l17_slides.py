"""Loop 23: slide regressions found in review (shmoo holes, axis units, typed titles, log annotation).
Deterministic (real Chrome via Playwright, no server): python tests/test_l17_slides.py [test_name ...]
The report category slide is checked in tests/test_l9_artifacts.py t_slide_categories_fit.
"""
import csv
import datetime as dt
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent import logview, shmoo  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l17-"))
HOLES = {(2, 2), (2, 3), (3, 2), (6, 6), (6, 7), (7, 6)}  # two separate L-shaped pockets inside the pass region


def shmoo_page(page):
    path = TMP / "two_holes.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["DIE", "FREQ_MHZ", "VCC", "RESULT"])
        for i in range(10):
            for j in range(10):
                fail = (i, j) in HOLES or i + j > 15  # a normal pass/fail boundary at the top right
                w.writerow(["D1", 1000 + 100 * i, f"{0.9 + 0.05 * j:.2f}", "FAIL" if fail else "PASS"])
        w.writerow(["D2", 1000, "0.90", "PASS"])
    d, _, _ = shmoo.build(path, x="FREQ_MHZ", y="VCC", result="RESULT", device=["DIE"])
    page.set_content(shmoo.render(d))
    return d


def t_shmoo_separate_holes():
    """Two pockets are two findings, and no passing cell is marked as a hole on the slide."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        try:
            page = b.new_page()
            d = shmoo_page(page)
            holes = [f for f in d["findings"] if f["kind"] == "hole"]
            assert [(f["cells"], f["x"], f["y"]) for f in holes] == [
                (3, ["1200", "1300"], ["1", "1.05"]), (3, ["1600", "1700"], ["1.2", "1.25"])], holes
            got = page.evaluate("""() => {
                S.device = 0; draw();
                const svg = renderSlide("L");
                // grid cells are the rects before the first hole outline; map each outline back to its cell
                const g = [...svg.querySelectorAll("rect")].filter((r) => !r.classList.contains("hole"));
                return { title: autoTitle("L"), short: autoTitle("M"),
                         outlines: [...svg.querySelectorAll("rect.hole")].map((r) => [+r.getAttribute("x") + 2, +r.getAttribute("y") + 2]),
                         cells: g.slice(0, 200).map((r) => [+r.getAttribute("x") - 0.75, +r.getAttribute("y") - 0.75, r.getAttribute("fill")]) };
            }""")
            assert got["short"] == "D1: 실패 구멍 2곳" and "2곳" in got["title"], got["title"]
            fill = {(round(x), round(y)): f for x, y, f in got["cells"]}
            marked = [fill.get((round(x), round(y))) for x, y in got["outlines"]]
            assert len(marked) == 6 and set(marked) == {"#F2A88A"}, marked  # each outline sits on a Fail cell
        finally:
            b.close()


def t_axis_unit_needs_separator():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        try:
            page = b.new_page()
            shmoo_page(page)
            labels = page.evaluate("""() => ["VCC", "VDD_V", "TEMP_C", "FREQ_MHZ", "TCK NS", "VDDC", "SPEC"].map(
                (n) => axisInfo(n, ["1"]).label)""")
            assert labels == ["VCC", "VDD (V)", "온도 (°C)", "주파수 (MHz)", "TCK (ns)", "VDDC", "SPEC"], labels
        finally:
            b.close()


def t_typed_title_resets_on_new_figure():
    """A title the user typed belongs to that device/condition; another one gets its own automatic title."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        try:
            page = b.new_page()
            shmoo_page(page)
            page.evaluate("() => { S.device = 0; draw(); }")
            page.fill("#slide-title", "내가 쓴 제목")
            assert page.evaluate("() => slideTitle(autoTitle('L'))") == "내가 쓴 제목"
            page.evaluate("() => { S.cell = [1, 1]; draw(); }")  # same figure: kept
            assert page.evaluate("() => slideTitle(autoTitle('L'))") == "내가 쓴 제목"
            page.evaluate("() => { S.device = 1; draw(); }")
            auto = page.evaluate("() => autoTitle('L')")
            assert page.evaluate("() => slideTitle(autoTitle('L'))") == auto != "내가 쓴 제목"
            assert page.input_value("#slide-title") == auto
        finally:
            b.close()


def t_log_annotation_above_ticks():
    """A small ERROR spike under a large WARN volume: the incident note stays above the time axis labels."""
    from playwright.sync_api import sync_playwright
    path = TMP / "small_spike.log"
    t0, lines = dt.datetime(2026, 10, 29, 3, 0), []
    for k in range(24):  # 2 hours of 5-minute buckets
        t = t0 + dt.timedelta(minutes=5 * k)
        lines += [f"{t + dt.timedelta(seconds=s):%Y-%m-%d %H:%M:%S} WARN [svc] slow query {s}ms" for s in range(60)]
        if 10 <= k < 13:
            lines += [f"{t + dt.timedelta(seconds=s):%Y-%m-%d %H:%M:%S} ERROR [svc] timeout calling db" for s in range(4)]
    path.write_text("\n".join(lines) + "\n")
    d, _ = logview.build(path)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        try:
            page = b.new_page()
            page.set_content(logview.render(d))
            got = page.evaluate("""() => {
                const svg = renderSlide("L"), texts = [...svg.querySelectorAll("text")];
                const note = [...svg.querySelectorAll("rect")].find((r) => r.getAttribute("fill-opacity") === "0.9");
                const ticks = texts.filter((t) => /^\\d\\d:\\d\\d$/.test(t.textContent)).map((t) => +t.getAttribute("y"));
                return { note: note && [+note.getAttribute("y"), +note.getAttribute("height")], tick: Math.min(...ticks),
                         label: texts.some((t) => t.textContent.includes("장애 구간")) };
            }""")
            assert got["label"] and got["note"], got
            assert got["note"][0] + got["note"][1] <= got["tick"] - 14, got  # tick glyphs reach ~14px above the baseline
        finally:
            b.close()


if __name__ == "__main__":
    only = sys.argv[1:]
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("t_") and (not only or name in only):
            t = time.time()
            try:
                fn()
                print(f"PASS {name} ({time.time() - t:.0f}s)")
            except Exception as e:
                failed += 1
                print(f"FAIL {name} ({time.time() - t:.0f}s): {type(e).__name__}: {str(e)[:400]}")
    sys.exit(1 if failed else 0)
