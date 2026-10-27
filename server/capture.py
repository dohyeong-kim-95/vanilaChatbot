"""Section images of a report (Loop 14): headless Chrome screenshots one <section> of the fixed template, so the
user can paste it into a document. The page is our template (no scripts); JavaScript is off and every network
request is refused anyway, so a capture never fetches anything."""
import os
from concurrent.futures import ThreadPoolExecutor

from agent import report

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


def png(data, section, scale=2):
    """data: report JSON. section: one of report.sections(data), or "all" for the whole page."""
    html = report.render(data)
    if section != "all" and section not in report.sections(report.validate(data)[0]):
        raise KeyError(section)
    return _worker.submit(_shot, html, section, scale).result(timeout=60)


def _shot(html, section, scale):
    page = _get_browser().new_page(java_script_enabled=False, device_scale_factor=scale,
                                   viewport={"width": report.WIDTH + 32, "height": 800})
    try:
        page.route("**/*", lambda route: route.abort())
        page.set_content(html, wait_until="load")
        if section == "all":
            return page.screenshot(full_page=True)
        return page.locator(f"#sec-{section}").screenshot()
    finally:
        page.close()
