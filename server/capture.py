"""Section images of a report: headless Chrome screenshots one section drawn as a slide (agent/report.slide, a fixed
L/M/S canvas, docs/DESIGN.md), at 2x with a 192 dpi tag so it pastes at its logical size. The page is our template
(no scripts); JavaScript is off and every network request is refused, so a capture never fetches anything."""
import os
from concurrent.futures import ThreadPoolExecutor

from agent import report
from agent.views import PRESETS, with_dpi

CHANNEL = os.environ.get("AGENT_CHROME_CHANNEL", "chrome")  # "" = Playwright's bundled Chromium
_worker = ThreadPoolExecutor(1)  # Playwright's sync objects only work on the thread that created them
_pw = _browser = None


def _get_browser():
    global _pw, _browser
    if _browser is None or not _browser.is_connected():
        from playwright.sync_api import sync_playwright
        _pw = _pw or sync_playwright().start()
        _browser = _pw.chromium.launch(channel=CHANNEL or None, headless=True)
    return _browser


def png(data, section, preset=None, title=True):
    """data: report JSON. section: a slide section (report.SLIDE_SECTIONS) or "all" for the whole screen page.
    preset: "L" | "M" | "S" (default per section)."""
    if section == "all":
        return _worker.submit(_shot, report.render(data), None).result(timeout=60)
    preset = preset if preset in PRESETS else report.SLIDE_SECTIONS.get(section)
    html = report.slide(data, section, preset, title)  # KeyError for notes / unknown sections
    return with_dpi(_worker.submit(_shot, html, PRESETS[preset]).result(timeout=60))


def _shot(html, size, scale=2):  # 2x: sharp in a document (192 dpi, with_dpi)
    w, h = size or (report.WIDTH + 32, 800)
    page = _get_browser().new_page(java_script_enabled=False, device_scale_factor=scale, viewport={"width": w, "height": h})
    try:
        page.route("**/*", lambda route: route.abort())
        page.set_content(html, wait_until="load")
        return page.screenshot(full_page=size is None)
    finally:
        page.close()
