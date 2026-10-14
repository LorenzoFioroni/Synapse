"""Browser checklist for zooming in the PDF viewer: the spot of the page under the pointer stays
under it (a trackpad pinch / Ctrl+wheel), on any page, and +/- keep the middle of the view.

    python tests/e2e/zoom_checklist.py

Starts its own server on port 8780 with a throw-away data folder and a 12-page PDF made from
tests/e2e/fixtures/twocolumn.pdf; your library is not touched.
"""

import asyncio
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
from tests.e2e._guard import guard  # noqa: E402

PORT = 8780
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-zoom-e2e-")
results = []

# where the pointer is: page number and fraction of that page (x, y)
PROBE = """([x, y]) => {
  const w = [...document.querySelectorAll('.page-wrapper')].find((w) => { const r = w.getBoundingClientRect(); return y >= r.top && y <= r.bottom; });
  if (!w) return null;
  const r = w.getBoundingClientRect(), c = document.getElementById('pdf-container');
  return { page: +w.dataset.pageNumber, fx: (x - r.left) / r.width, fy: (y - r.top) / r.height,
           // sideways the spot cannot follow when the page is narrower than the view or the view is scrolled to an edge
           xFree: c.scrollWidth > c.clientWidth && c.scrollLeft > 0 && c.scrollLeft < c.scrollWidth - c.clientWidth - 1 }; }"""


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


def long_pdf() -> Path:
    from pypdf import PdfReader, PdfWriter

    src = PdfReader(APP / "tests" / "e2e" / "fixtures" / "twocolumn.pdf")
    w = PdfWriter()
    for _ in range(4):
        for p in src.pages:
            w.add_page(p)
    out = Path(DATA) / "twelve.pdf"
    w.write(out)
    return out


def same(a, b, tol=0.01, x=True):
    return (
        a and b and a["page"] == b["page"] and abs(a["fy"] - b["fy"]) < tol and (not x or abs(a["fx"] - b["fx"]) < tol)
    )


async def open_at(page, url, start):
    await page.goto(url)
    await page.wait_for_selector('.page-wrapper[data-page-number="1"] canvas', timeout=15000)
    await page.wait_for_timeout(400)
    await page.evaluate(f"document.querySelector('.page-wrapper[data-page-number=\"{start}\"]').scrollIntoView()")
    await page.evaluate("document.getElementById('pdf-container').scrollBy(0, -200)")
    await page.wait_for_timeout(300)


async def main():
    guard(PORT)
    pdf = long_pdf()
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", DATA],
        cwd=APP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        item = requests.post(BASE + "/api/items", json={"title": "Zoom test"}).json()
        with open(pdf, "rb") as fh:
            att = requests.post(
                f"{BASE}/api/items/{item['id']}/attachments", files={"file": ("twelve.pdf", fh, "application/pdf")}
            ).json()["attachments"][0]
        url = f"{BASE}/viewer.html?" + urlencode(
            {"file": f"/pdfs/{att['url']}", "paper_id": item["id"], "att": att["id"], "title": "x"}
        )
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1200, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))

            # a pinch is many small Ctrl+wheel events at one pointer position
            cases = [
                ("zoom in, left column, page 5", 400, 300, -8, 12, 5),
                ("zoom in, right column low on the screen, page 5", 900, 700, -8, 12, 5),
                ("zoom in near the top, page 1", 300, 150, -8, 12, 1),
                ("zoom in at a page break, page 12", 1000, 120, -8, 12, 12),
                ("zoom in to the limit, page 8", 600, 450, -8, 25, 8),
                ("zoom out, page 5", 900, 500, 10, 8, 5),
                ("zoom out low on the screen, page 3", 500, 800, 10, 8, 3),
            ]
            for name, x, y, dy, n, start in cases:
                await open_at(page, url, start)
                before = await page.evaluate(PROBE, [x, y])
                await page.mouse.move(x, y)
                await page.keyboard.down("Control")
                for _ in range(n):
                    await page.mouse.wheel(0, dy)
                    await page.wait_for_timeout(16)
                await page.keyboard.up("Control")
                await page.wait_for_timeout(600)
                after = await page.evaluate(PROBE, [x, y])
                scale = await page.evaluate("window.zcViewer.scale")
                check(
                    f"{name}: the spot under the pointer stays (zoom {scale:.2f})",
                    same(before, after, x=after and after["xFree"]),
                    f"{before and {k: round(v, 3) for k, v in before.items() if k != 'xFree'}} → "
                    f"{after and {k: round(v, 3) for k, v in after.items() if k != 'xFree'}}",
                )

            # the buttons keep the middle of the view
            await open_at(page, url, 6)
            mid = await page.evaluate(
                "(() => { const r = document.getElementById('pdf-container').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()"
            )
            before = await page.evaluate(PROBE, mid)
            for _ in range(3):
                await page.click("#zoom-in")
                await page.wait_for_timeout(60)
            await page.wait_for_timeout(600)
            after = await page.evaluate(PROBE, mid)
            check(
                "+ three times keeps the middle of the view",
                same(before, after, x=after and after["xFree"]),
                f"{before} → {after}",
            )
            await page.click("#zoom-out")
            await page.wait_for_timeout(600)
            again = await page.evaluate(PROBE, mid)
            check("- keeps it too", same(after, again, x=again and again["xFree"]))
            check("no JavaScript errors", not errors, "; ".join(errors[:3]))
            await browser.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
