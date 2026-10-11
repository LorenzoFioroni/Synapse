"""Browser checklist for the PDF viewer on long documents (issue #19): pages are drawn only near
the viewport, so a 300-page PDF opens fast, keeps few canvases alive, jumps and zooms quickly, and
annotations on far pages are neither lost nor missing when you get there.

    python tests/e2e/lazy_viewer_checklist.py

Starts its own server on port 8778 with a throw-away data folder and a 300-page PDF made from
tests/e2e/fixtures/twocolumn.pdf; your library is not touched.
"""
import asyncio, os, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.parse import urlencode
import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from tests.e2e._guard import guard  # noqa: E402

PORT = 8778
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-lazy-e2e-")
PAGES = 300
MAX_LIVE = 14
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


def long_pdf() -> Path:
    from pypdf import PdfReader, PdfWriter
    src = PdfReader(APP / "tests" / "e2e" / "fixtures" / "twocolumn.pdf")
    w = PdfWriter()
    while len(w.pages) < PAGES:
        for p in src.pages:
            if len(w.pages) < PAGES:
                w.add_page(p)
    out = Path(DATA) / "long.pdf"
    w.write(out)
    return out


async def canvases(page):
    return await page.evaluate("document.querySelectorAll('.page-wrapper canvas').length")


async def main():
    guard(PORT)
    pdf = long_pdf()
    srv = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", DATA],
                           cwd=APP, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        item = requests.post(BASE + "/api/items", json={"title": "A long thesis"}).json()
        with open(pdf, "rb") as fh:
            att = requests.post(f"{BASE}/api/items/{item['id']}/attachments",
                                files={"file": ("long.pdf", fh, "application/pdf")}).json()["attachments"][0]
        # an annotation on a far page, made earlier
        requests.post(f"{BASE}/api/attachments/{att['id']}/annotations",
                      json={"type": "box", "page": 250, "rects": [[0.2, 0.2, 0.3, 0.1]], "color": "#ff0000", "size": 3})
        url = f"{BASE}/viewer.html?" + urlencode({"file": f"/pdfs/{att['url']}", "paper_id": item["id"], "att": att["id"], "title": "x"})

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1200, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))

            t0 = time.time()
            await page.goto(url)
            await page.wait_for_selector('.page-wrapper[data-page-number="1"] canvas', timeout=30000)
            first = time.time() - t0
            check("the first page shows quickly", first < 3, f"{first:.2f} s")
            await page.wait_for_timeout(1200)
            n_wrappers = await page.evaluate("document.querySelectorAll('.page-wrapper').length")
            check(f"all {PAGES} pages are laid out", n_wrappers == PAGES, str(n_wrappers))
            check("the page counter knows the length", (await page.text_content("#page-num-text")).strip() == f"1 / {PAGES}")
            live = await canvases(page)
            check("only the pages near the viewport are drawn", 1 <= live <= 4, f"{live} canvases")

            # scroll through a long stretch: the number of canvases stays bounded
            most = 0
            for k in range(40):
                await page.evaluate("document.getElementById('pdf-container').scrollBy(0, 1400)")
                await page.wait_for_timeout(60)
                most = max(most, await canvases(page))
            await page.wait_for_timeout(800)
            most = max(most, await canvases(page))
            check(f"scrolling never keeps more than {MAX_LIVE} canvases", most <= MAX_LIVE, f"max {most}")
            here = await page.evaluate("window.zcViewer.renderedPages()")
            check("the pages on screen are the ones drawn", here and min(here) > 20, str(here))

            # jump far
            t0 = time.time()
            await page.evaluate("window.zcViewerGoto({ page: 250 })")
            await page.wait_for_selector('.page-wrapper[data-page-number="250"] canvas', timeout=10000)
            jump = time.time() - t0
            check("jumping to page 250 draws it quickly", jump < 2, f"{jump:.2f} s")
            await page.wait_for_timeout(600)
            check("page 1 has given its canvas back", 1 not in await page.evaluate("window.zcViewer.renderedPages()"))
            boxes = await page.evaluate("document.querySelectorAll('.page-wrapper[data-page-number=\"250\"] .anno-layer g.anno').length")
            check("the annotation on page 250 is there", boxes == 1, str(boxes))
            has_text = await page.evaluate("document.querySelectorAll('.page-wrapper[data-page-number=\"250\"] .textLayer span').length")
            check("page 250 has its text layer (selection, highlights, quotes)", has_text > 20, str(has_text))

            # zoom: only what is near is redrawn
            t0 = time.time()
            await page.click("#zoom-in")
            await page.wait_for_function("document.querySelector('.page-wrapper[data-page-number=\"250\"]').dataset.rendered === String(window.zcViewer.scale)", timeout=10000)
            zoom = time.time() - t0
            check("zooming redraws the visible page quickly", zoom < 2, f"{zoom:.2f} s")
            await page.wait_for_timeout(800)
            live = await canvases(page)
            check("and keeps few canvases", live <= MAX_LIVE, f"{live}")

            # a new annotation on a far page lands on that page
            await page.evaluate("document.querySelector('.page-wrapper[data-page-number=\"250\"]').scrollIntoView()")
            await page.wait_for_timeout(400)
            w = await page.locator('.page-wrapper[data-page-number="250"]').bounding_box()
            await page.click('.tool-btn[data-tool="box"]')
            await page.mouse.move(w["x"] + 60, w["y"] + 300)
            await page.mouse.down()
            await page.mouse.move(w["x"] + 200, w["y"] + 380, steps=6)
            await page.mouse.up()
            await page.wait_for_timeout(600)
            pages = sorted(a["page"] for a in requests.get(f"{BASE}/api/attachments/{att['id']}/annotations").json()["annotations"])
            check("drawing on page 250 saves it on page 250", pages == [250, 250], str(pages))

            # opening at page 250 directly
            await page.goto(url + "&page=250")
            await page.wait_for_selector('.page-wrapper[data-page-number="250"] canvas', timeout=10000)
            await page.wait_for_timeout(800)
            r = await page.evaluate("window.zcViewer.renderedPages()")
            check("?page=250 opens there and draws only around it", 250 in r and min(r) >= 245, str(r))

            # thumbnails are drawn as they scroll into the sidebar
            await page.click("#sidebar-toggle")
            await page.wait_for_timeout(800)
            done = await page.evaluate("document.querySelectorAll('.thumb-wrapper[data-done]').length")
            total = await page.evaluate("document.querySelectorAll('.thumb-wrapper').length")
            check("the sidebar lists every page but draws only those in view", total == PAGES and 0 < done < 40, f"{done} of {total}")
            check("no JavaScript errors", not errors, "; ".join(errors[:3]))
            await browser.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
