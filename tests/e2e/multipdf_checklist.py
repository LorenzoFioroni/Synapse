"""Browser checklist for items with several PDFs (issue #39): citations, cards and menus.

    python tests/e2e/multipdf_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8788 with a throw-away data folder; your library is not touched.
"""
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from tests.conftest import make_pdf  # noqa: E402

PORT = 8788
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-multipdf-e2e-")
SHOTS = os.environ.get("SHOTS")
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png", clip={"x": 0, "y": 0, "width": 1440, "height": 950})


async def wait_for(fn, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if await fn():
            return True
        await asyncio.sleep(0.15)
    return False


def setup():
    item = requests.post(f"{BASE}/api/items", json={"authors": "Cui, Jian; Cirac, J. Ignacio", "title": "Variational matrix product operators", "year": 2015}).json()
    ids = []
    for name in ("paper.pdf", "supplement.pdf"):
        pdf = make_pdf(Path(DATA) / f"up-{name}", name)
        with open(pdf, "rb") as fh:
            r = requests.post(f"{BASE}/api/items/{item['id']}/attachments", files={"file": (name, fh, "application/pdf")}).json()
        ids.append(r["attachments"][0]["id"])
    main_id, supp_id = ids
    k = item["citation_key"]
    text = (f"# Review\n\nNo attachment: [@{k}, p. 2].\n\nNamed: [@{k}, p. 3, att:{supp_id}].\n\nSee [[Other]].\n")
    requests.post(f"{BASE}/api/notes/note", json={"title": "Review", "text": text})
    requests.post(f"{BASE}/api/notes/note", json={"title": "Other", "text": "# Other\n\nNothing.\n"})
    return item, main_id, supp_id


async def main():
    env = dict(os.environ, SYNAPSE_DATA_DIR=DATA)
    srv = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--no-browser"], cwd=APP, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    try:
        item, main_id, supp_id = setup()
        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(viewport={"width": 1440, "height": 950})
            page = await ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(BASE + "/")
            await page.wait_for_timeout(800)

            # ---- item pane: Open PDF lists every PDF
            await page.locator('.row', has_text="Variational matrix product").first.click()
            btn = page.locator('[data-tool="open-pdf"]')
            await btn.wait_for()
            await btn.click()
            menu = page.locator('.menu')
            await menu.wait_for()
            labels = await menu.locator('.menu-item, button').all_text_contents()
            check("item pane 'Open PDF' lists both PDFs", any("paper.pdf" in l for l in labels) and any("supplement.pdf" in l for l in labels), labels)
            await page.keyboard.press('Escape')
            await shot(page, "1-item-menu")

            # ---- notes: a citation with att: and one without
            await page.click('[data-act="open-notes"]')
            await page.click('.nt-row.note[data-path="Review.md"]')
            await page.wait_for_selector('.cm-content')
            await page.mouse.move(5, 5)
            await wait_for(lambda: page.locator('.cm-lp-chip.cite').count())
            chips = page.locator('.cm-lp-chip.cite')
            check("two citation chips rendered", await chips.count() == 2, await chips.count())
            label_texts = await chips.all_text_contents()
            check("the label never shows att:", all("att:" not in t for t in label_texts), label_texts)

            # the card of the citation without att: lists both PDFs
            await chips.nth(0).hover()
            await wait_for(lambda: page.locator('.cite-card:not([hidden]) [data-cc="pdf"]').count())
            pdf_buttons = page.locator('.cite-card:not([hidden]) [data-cc="pdf"]')
            n = await pdf_buttons.count()
            check("card without att: offers both PDFs", n == 2, n)
            await shot(page, "2-card-both-pdfs")
            await page.mouse.move(5, 5)
            await page.wait_for_timeout(500)

            # the card of the citation with att: offers just that PDF
            await chips.nth(1).hover()
            await wait_for(lambda: page.locator('.cite-card:not([hidden]) [data-cc="pdf"]').count())
            named = page.locator('.cite-card:not([hidden]) [data-cc="pdf"]')
            check("card with att: offers the named PDF only",
                  await named.count() == 1 and await named.first.get_attribute("data-att") == str(supp_id),
                  await named.count())

            # clicking it opens the viewer on the supplement
            async with ctx.expect_page() as newp:
                await named.first.click()
            viewer = await newp.value
            await viewer.wait_for_load_state()
            check("opens the viewer on the named PDF", f"att={supp_id}" in viewer.url and "page=3" in viewer.url, viewer.url)
            await viewer.close()
            await page.mouse.move(5, 5)
            await page.wait_for_timeout(500)

            # the first PDF button of the card without att: opens paper.pdf
            await chips.nth(0).hover()
            await wait_for(lambda: page.locator('.cite-card:not([hidden]) [data-cc="pdf"]').count())
            first = page.locator('.cite-card:not([hidden]) [data-cc="pdf"]').first
            async with ctx.expect_page() as newp2:
                await first.click()
            viewer2 = await newp2.value
            await viewer2.wait_for_load_state()
            check("the default PDF is the first one", f"att={main_id}" in viewer2.url, viewer2.url)
            await viewer2.close()

            check("no console errors", errors == [], errors[:3])
            await b.close()
    finally:
        srv.terminate()
        srv.wait(timeout=10)
        shutil.rmtree(DATA, ignore_errors=True)
    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
