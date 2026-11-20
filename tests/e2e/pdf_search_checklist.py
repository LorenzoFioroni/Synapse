"""Full-document PDF search, virtualized pages, keyboard routing, themes and touch controls.

    python tests/e2e/run.py pdf_search

Uses a disposable server/library and a generated 300-page PDF; no AI provider is contacted.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
import tests.e2e.citation_spot_checklist as spot  # noqa: E402
from tests.e2e._guard import guard  # noqa: E402

shutil.rmtree(spot.DATA, ignore_errors=True)
PORT = 8863
BASE = f"http://127.0.0.1:{PORT}"
MOD = "Meta" if sys.platform == "darwin" else "Control"
SHOTS = os.environ.get("SHOTS")
results = []


def check(name, condition, extra=""):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + name, extra, flush=True)


async def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


async def finished(page, expected):
    await page.wait_for_function(
        "s => document.querySelector('.pdf-findbar')?.getAttribute('aria-busy') === 'false' && document.querySelector('.pdf-find-count')?.textContent === s",
        arg=expected,
        timeout=30000,
    )


async def main():
    guard(PORT)
    with tempfile.TemporaryDirectory(prefix="synapse-find-e2e-") as data:
        lines = [[f"Document page {i}"] for i in range(1, 301)]
        lines[249] += ["Needle needle needles"]
        lines[254] += ["needle"]
        lines[298] += ["A quantum", "state evolves.", "trans-", "port"]
        lines[299] += ["finaltarget"]
        pdf = spot.make_lines_pdf(Path(data) / "search.pdf", lines)
        empty = spot.make_lines_pdf(Path(data) / "empty.pdf", [[]])
        server = subprocess.Popen(
            [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", data],
            cwd=APP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                try:
                    requests.get(BASE + "/api/settings", timeout=1).raise_for_status()
                    break
                except requests.RequestException:
                    if server.poll() is not None:
                        raise RuntimeError("PDF search test server did not start") from None
                    time.sleep(0.1)
            item = requests.post(BASE + "/api/items", json={"title": "PDF search fixture"}).json()

            def attach(path):
                with path.open("rb") as file:
                    return requests.post(
                        f"{BASE}/api/items/{item['id']}/attachments",
                        files={"file": (path.name, file, "application/pdf")},
                    ).json()["attachments"][-1]

            attachment = attach(pdf)
            empty_attachment = attach(empty)

            def viewer_url(att):
                return (
                    BASE
                    + "/viewer.html?"
                    + urlencode(
                        {
                            "file": f"/pdfs/{att['url']}",
                            "att": att["id"],
                            "paper_id": item["id"],
                            "title": "Search fixture",
                        }
                    )
                )

            async with async_playwright() as p:
                browser = await p.chromium.launch()
                context = await browser.new_context(viewport={"width": 1280, "height": 850})
                errors = []
                context.on("weberror", lambda error: errors.append(str(error.error)))
                page = await context.new_page()
                await page.goto(viewer_url(attachment))
                await page.wait_for_selector('.page-wrapper[data-page-number="300"]')
                check(
                    "the distant hit starts without a canvas",
                    await page.locator('[data-page-number="250"] canvas').count() == 0,
                )
                await page.keyboard.press(f"{MOD}+f")
                field = page.get_by_role("searchbox", name="Find in PDF")
                check(
                    "Cmd/Ctrl+F opens and focuses PDF search", await field.evaluate("e => e === document.activeElement")
                )
                await field.fill("needle")
                await finished(page, "1 of 4")
                await page.wait_for_selector('[data-page-number="250"] .pdf-search-hit.current')
                check(
                    "search includes all 300 pages and jumps to the first hit",
                    await page.locator('[data-page-number="250"] canvas').count() == 1,
                )
                check("search does not render all pages", await page.locator(".page-wrapper canvas").count() <= 14)
                await page.get_by_role("button", name="Previous match").focus()
                await page.keyboard.press("Enter")
                await finished(page, "4 of 4")
                await page.wait_for_selector('[data-page-number="255"] .pdf-search-hit.current')
                await page.get_by_role("button", name="Next match").click()
                await finished(page, "1 of 4")
                check("previous and next wrap through the document", True)
                await field.focus()
                await page.keyboard.press("Enter")
                await finished(page, "2 of 4")
                await page.keyboard.press("Shift+Enter")
                await finished(page, "1 of 4")
                await page.keyboard.press(f"{MOD}+g")
                await finished(page, "2 of 4")
                await page.keyboard.press(f"{MOD}+Shift+g")
                await finished(page, "1 of 4")
                check("Enter, Shift+Enter and Cmd/Ctrl+G navigate matches", True)
                await page.get_by_label("Match case", exact=True).check()
                await finished(page, "1 of 3")
                await page.get_by_label("Whole words", exact=True).check()
                await finished(page, "1 of 2")
                check("match-case and whole-word options affect results", True)
                await page.click("#zoom-in")
                await page.wait_for_function(
                    "() => document.querySelector('[data-page-number=\"250\"]').dataset.rendered === String(window.zcViewer.scale) && !!document.querySelector('[data-page-number=\"250\"] .pdf-search-hit.current')"
                )
                await page.evaluate("window.zcViewerGoto({page: 1})")
                await page.wait_for_function("!document.querySelector('[data-page-number=\"250\"] .textLayer')")
                await page.evaluate("window.zcViewerGoto({page: 250})")
                await page.wait_for_selector('[data-page-number="250"] .pdf-search-hit.current')
                check("zoom and page unloading/revisiting preserve highlights", True)
                for theme in ("light", "dark"):
                    await page.emulate_media(color_scheme=theme)
                    await shot(page, f"pdf-search-{theme}")
                await field.fill("quantum state")
                await finished(page, "1 of 1")
                await page.wait_for_selector('[data-page-number="299"] .pdf-search-hit.current')
                check(
                    "a phrase can cross a printed line break",
                    await page.locator('[data-page-number="299"] .pdf-search-hit.current').count() >= 2,
                )
                await field.fill("transport")
                await finished(page, "1 of 1")
                check("line-end hyphenation is searchable as one word", True)
                await field.fill("not in this document")
                await finished(page, "No matches")
                check(
                    "no matches disables navigation", await page.get_by_role("button", name="Next match").is_disabled()
                )
                await field.fill("needle")
                await field.fill("finaltarget")
                await finished(page, "1 of 1")
                await page.wait_for_selector('[data-page-number="300"] .pdf-search-hit.current')
                check("replacing a query does not jump back to its old results", True)
                await page.keyboard.press("Escape")
                check(
                    "Escape closes search and clears highlights",
                    await page.locator(".pdf-findbar").is_hidden()
                    and await page.locator(".pdf-search-hit").count() == 0,
                )
                await page.keyboard.press(f"{MOD}+f")
                await finished(page, "1 of 1")
                await page.get_by_role("button", name="Close PDF search").focus()
                await page.keyboard.press("Enter")
                check("Enter activates the Close button normally", await page.locator(".pdf-findbar").is_hidden())

                # Real app panel, outside the viewer iframe: the shell must forward Cmd/Ctrl+F.
                app = await context.new_page()
                await app.goto(BASE)
                await app.wait_for_function("!!window.zcTabs && !!window.zcAskAi")
                await app.evaluate(
                    "url => window.zcTabs.open({key: 'search-test', url, title: 'Search fixture', spot: {}, newTab: true})",
                    viewer_url(attachment).removeprefix(BASE),
                )
                frame = await (await app.query_selector(".tab-frame.active")).content_frame()
                await frame.wait_for_selector('[data-page-number="300"]')
                await app.evaluate("window.zcAskAi()")
                await app.locator(".ai-input").fill("needle " * 10)
                await app.keyboard.press(f"{MOD}+f")
                framed_field = frame.get_by_role("searchbox", name="Find in PDF")
                check(
                    "Cmd/Ctrl+F from the AI panel focuses the PDF search",
                    await framed_field.evaluate("e => e === document.activeElement"),
                )
                await framed_field.fill("needle")
                await finished(frame, "1 of 4")
                check(
                    "AI panel text is excluded from PDF results",
                    await app.locator(".ai-input").input_value() == "needle " * 10,
                )
                await framed_field.press("Escape")
                await frame.locator("#pdf-find-toggle").focus()
                await app.keyboard.press(f"{MOD}+f")
                check("the shortcut also works with iframe focus", await framed_field.is_visible())

                touch = await browser.new_context(
                    viewport={"width": 390, "height": 780}, has_touch=True, is_mobile=True
                )
                narrow = await touch.new_page()
                await narrow.goto(viewer_url(attachment))
                await narrow.locator("#pdf-find-toggle").tap()
                await narrow.get_by_role("searchbox", name="Find in PDF").fill("finaltarget")
                await finished(narrow, "1 of 1")
                geometry = await narrow.evaluate("""() => ({
                  overflow: document.documentElement.scrollWidth > innerWidth,
                  targets: [...document.querySelectorAll('.pdf-find-actions button')].every(b => b.getBoundingClientRect().height >= 36),
                  inputSize: getComputedStyle(document.querySelector('.pdf-find-query input')).fontSize
                })""")
                check(
                    "touch search fits the viewport with usable controls",
                    not geometry["overflow"] and geometry["targets"] and geometry["inputSize"] == "16px",
                    geometry,
                )
                await shot(narrow, "pdf-search-touch")
                await page.goto(viewer_url(empty_attachment))
                await page.click("#pdf-find-toggle")
                await page.get_by_role("searchbox", name="Find in PDF").fill("anything")
                await finished(page, "No searchable text")
                check("an image-only/blank PDF reports missing searchable text", True)
                check("no JavaScript errors", not errors, errors[:5])
                await browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
