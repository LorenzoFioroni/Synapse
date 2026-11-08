"""Browser checklist for Retrieve Metadata in bulk and Settings → Adding Items:
lookups run side by side, the review lists articles with updates first and folds the up-to-date
ones away, it can continue in the background, and new items follow never / always / ask.

    python tests/e2e/adding_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8771 with a throw-away data folder and fake online services
(each lookup takes 0.6 s); your library is not touched.
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from tests.e2e._guard import guard  # noqa: E402

PORT = 8771
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-adding-e2e-")
SHOTS = os.environ.get("SHOTS")
results = []

# The server, with the online services replaced by slow fakes.
SERVER = f"""
import sys, time, threading
sys.path.insert(0, {str(APP)!r})
from backend import create_app
from backend.config import Config
from backend.api import metadata as M
from backend.online import metadata as online_metadata, pdf_finder
from backend.online.http import LookupFailed
calls = []
lock = threading.Lock()
def fake_lookup(**k):
    with lock: calls.append(time.monotonic())
    time.sleep(0.6)
    title = k.get('title') or ''
    if 'Unknown' in title: raise LookupFailed('No match found on Crossref for this title.')
    if 'Old' in title: return {{'title': title.replace('Old', 'New'), 'journal': 'Physical Review Letters'}}
    return {{'title': title}}
M.lookup = fake_lookup
online_metadata.lookup = fake_lookup
pdf_finder.find_and_download = lambda *a, **k: {{'status': 'not_found', 'message': 'No open-access PDF found'}}
app = create_app(Config.from_env({DATA!r}), start_indexer=False)
@app.get('/test/calls')
def _calls():
    return {{'calls': calls}}
app.run(port={PORT}, threaded=True)
"""


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


CENTER = "#notification-center"
RUNNING = CENTER + " .notification:has(progress)"
REVIEW = CENTER + ' .notification:has([data-n="action"]:has-text("Review"))'


async def open_center(page):
    """Long jobs report in the notification center (the bell beside Settings), not in toasts."""
    if not await page.locator(CENTER).is_visible():
        await page.click('[data-a="notifications"]')
        await page.wait_for_selector(CENTER, state="visible")


async def wait_for(fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if await fn():
            return True
        await asyncio.sleep(0.15)
    return False


async def main():
    guard(PORT)
    srv = subprocess.Popen(
        [sys.executable, "-c", SERVER], cwd=APP, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    for _ in range(50):
        try:
            requests.get(BASE + "/api/schema", timeout=1)
            break
        except requests.RequestException:
            time.sleep(0.2)
    try:
        requests.put(f"{BASE}/api/settings", json={"add_fetch_references": "never"})
        ids = []
        for i in range(12):
            title = f"Old paper {i}" if i in (2, 7, 9) else (f"Unknown paper {i}" if i == 5 else f"Fine paper {i}")
            ids.append(requests.post(f"{BASE}/api/items", json={"title": title, "year": 2000 + i}).json()["id"])

        async with async_playwright() as p:
            br = await p.chromium.launch()
            page = await (await br.new_context(viewport={"width": 1440, "height": 900})).new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(BASE + "/")
            await page.wait_for_selector(".row[data-id]")
            mod = "Meta" if sys.platform == "darwin" else "Control"

            # ---- bulk Retrieve Metadata
            await page.click(f'.row[data-id="{ids[0]}"] .cell[data-col="title"]')
            await page.keyboard.press(f"{mod}+a")
            await page.click(f'.row[data-id="{ids[0]}"] .cell[data-col="title"]', button="right")
            t0 = time.monotonic()
            await page.click('.menu-item:has-text("Retrieve Metadata for 12 Items")')
            await page.wait_for_selector(".mr-dialog")
            await wait_for(lambda: page.evaluate("document.querySelectorAll('.mr-article.pending').length === 0"), 20)
            took = time.monotonic() - t0
            check("12 lookups of 0.6 s finish well under the 12 × 0.6 s it took one by one", took < 4.5, f"{took:.1f}s")
            calls = requests.get(BASE + "/test/calls").json()["calls"]
            overlap = max(sum(1 for c in calls if c <= t < c + 0.6) for t in calls)
            check("several lookups run at the same time (at most 4)", 2 <= overlap <= 4, f"max {overlap}")
            groups = await page.evaluate("""[...document.querySelectorAll('.mr-list > [data-group]')].map((g) => [g.dataset.group,
                g.hidden, [...g.querySelectorAll('.mr-article')].map((a) => +a.dataset.n)])""")
            g = {name: (hidden, ns) for name, hidden, ns in groups}
            check("articles with updates come first, in order", g["changes"][1] == [3, 8, 10], str(g["changes"]))
            check("not found ones are listed next", g["error"] == (False, [6]), str(g["error"]))
            check(
                "up-to-date ones are in a section at the end",
                g["same"][1] == [1, 2, 4, 5, 7, 9, 11, 12],
                str(g["same"]),
            )
            check("…which is collapsed", not await page.evaluate("document.querySelector('[data-group=same]').open"))
            check(
                "the up-to-date articles are not visible",
                not await page.locator("[data-group=same] .mr-article").first.is_visible(),
            )
            head = " ".join((await page.text_content("[data-group=same] summary")).split())
            check("the section says how many", "Already up to date (8)" in head, head)
            await shot(page, "a1-review-grouped")
            await page.click("[data-group=same] summary")
            check("it opens on click", await page.locator("[data-group=same] .mr-article").first.is_visible())
            await page.click(".dialog .btn-primary")
            await page.wait_for_selector(".mr-dialog", state="detached")
            titles = await page.evaluate(
                f"fetch('/api/items/{ids[2]}').then((r) => r.json()).then((i) => i.title + ' | ' + i.journal)"
            )
            check("applying saves the changes", titles == "New paper 2 | Physical Review Letters", titles)

            # ---- continue in the background, review later
            for i in (2, 7, 9):
                requests.patch(f"{BASE}/api/items/{ids[i]}", json={"title": f"Old paper {i}"})
            await page.evaluate("import('/js/actions.js').then((A) => A.loadItems())")
            await page.click(f'.row[data-id="{ids[0]}"] .cell[data-col="title"]')
            await page.keyboard.press(f"{mod}+a")
            await page.click(f'.row[data-id="{ids[0]}"] .cell[data-col="title"]', button="right")
            await page.click('.menu-item:has-text("Retrieve Metadata for 12 Items")')
            await page.wait_for_selector(".mr-dialog")
            bg = page.locator('.dialog-footer button:has-text("Continue in Background")')
            check("'Continue in Background' is offered while looking up", await bg.is_visible())
            await bg.click()
            await page.wait_for_selector(".mr-dialog", state="detached")
            await open_center(page)
            check(
                "a running job shows in the notification center instead",
                await page.locator(RUNNING).count() == 1,
                await page.text_content(RUNNING) if await page.locator(RUNNING).count() else "",
            )
            await shot(page, "a2-background")
            await wait_for(lambda: page.locator(REVIEW).count(), 15)
            msg = " ".join((await page.text_content(REVIEW)).split())
            check("when done, its entry offers Review", "Review" in msg and "up to date" in msg, msg)
            check("…and nothing is left running", await page.locator(RUNNING).count() == 0)
            await page.click(REVIEW + ' [data-n="action"]')
            await page.wait_for_selector(".mr-dialog")
            check(
                "an article whose only change is a new citation key counts as up to date",
                "3 articles with updates" in msg and "8 up to date" in msg,
                msg,
            )
            check(
                "Review opens the finished results",
                await page.locator(".mr-article.pending").count() == 0
                and await page.locator('.dialog-footer button:has-text("Continue in Background")').is_hidden(),
            )
            await page.keyboard.press("Escape")

            # ---- Settings → Adding Items
            await page.keyboard.press(f"{mod}+,")
            await page.wait_for_selector("#set-adding", state="attached")
            await page.click('.settings-nav [data-nav="adding"]')
            labels = await page.eval_on_selector_all(
                "#set-adding .setting:has(.segmented) .s-title", "els => els.map((e) => e.textContent)"
            )
            check(
                "Settings has an Adding Items section with four never/ask/always choices",
                len(labels) == 4 and await page.locator("#set-adding .segmented button").count() == 12,
                str(labels),
            )
            await page.click('#set-adding [data-seg="add_find_pdf"] button[data-v="always"]')
            await wait_for(
                lambda: page.evaluate(
                    "fetch('/api/settings').then((r) => r.json()).then((s) => s.settings.add_find_pdf === 'always')"
                )
            )
            check("choosing saves it", True)
            await shot(page, "a3-settings")
            await page.keyboard.press("Escape")

            # ---- import: ask
            bib = (
                "@article{a1, title={Imported Old one}, author={Doe, J}, year={2020}, keywords={spin, transport}}\n"
                "@article{a2, title={Imported fine}, author={Roe, R}, year={2021}}"
            )
            await page.keyboard.press(f"{mod}+i")
            await page.wait_for_selector(".dialog .drop-zone")
            await page.click(".dialog details summary")
            await page.fill(".dialog textarea", bib)
            await page.click(".dialog [data-parse]")
            await page.wait_for_selector(".import-list")
            boxes = await page.eval_on_selector_all(
                ".import-after [data-after]", "els => els.map((e) => e.dataset.after + (e.checked ? '+' : '-'))"
            )
            check("the import dialog asks about tags and metadata, ticked", boxes == ["tags+", "metadata+"], str(boxes))
            hint = await page.text_content(".import-after .field-hint")
            check("…and names the 'always' action", "looks for PDFs" in hint, hint)
            await shot(page, "a4-import-ask")
            await page.uncheck('.import-after [data-after="tags"]')
            await page.click(".dialog .btn-primary")
            await page.wait_for_selector(".import-list", state="detached")
            await open_center(page)
            # the import's own lookup: a second Review entry, newest first

            async def two_reviews():
                return await page.locator(REVIEW).count() == 2

            await wait_for(two_reviews, 15)
            items = await page.evaluate(
                "fetch('/api/items').then((r) => r.json()).then((d) => d.items.filter((i) => i.title.startsWith('Imported')))"
            )
            first = next(i for i in items if "one" in i["title"])
            check(
                "unticked: keywords are not turned into tags",
                first["tags"] == [] and "spin" in (first.get("keywords") or ""),
                str(first["tags"]),
            )
            msg = " ".join((await page.locator(REVIEW).first.text_content()).split())
            check("the imported items' metadata is retrieved in the background", "1 article with updates" in msg, msg)
            calls_after = len(requests.get(BASE + "/test/calls").json()["calls"])
            check("…only for the imported items", calls_after == 24 + 2, str(calls_after))

            check("no JavaScript errors", not errors, "; ".join(errors[:3]))
            await br.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
