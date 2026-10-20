"""Browser checklist for one viewer tab per PDF: clicking another citation of a paper whose PDF is
already open scrolls that tab to the new spot (as a Back/Forward entry) instead of opening another
tab; Cmd/Ctrl+click (Cmd/Ctrl+Shift+click in the editor, or a middle click) opens a new tab.

    python tests/e2e/pdf_tabs_checklist.py        (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8773 with a throw-away data folder; your library is not touched.
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
import tests.e2e.citation_spot_checklist as spot  # noqa: E402  (PDF builder, flash probe)
from tests.e2e._guard import guard  # noqa: E402

shutil.rmtree(spot.DATA, ignore_errors=True)  # made when that module is imported; not used here
PORT = 8773
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-tabs-e2e-")
NOTES = Path(DATA) / "notes"
SHOTS = os.environ.get("SHOTS")
results = []
MOD = "Meta" if sys.platform == "darwin" else "Control"


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


TOP_PAGE = """() => {
  const c = document.getElementById('pdf-container').getBoundingClientRect();
  const ws = [...document.querySelectorAll('.page-wrapper')];
  const w = ws.find((w) => w.getBoundingClientRect().bottom > c.top + 40);
  return w ? +w.dataset.pageNumber : null;
}"""


async def away(page):
    """Move the mouse off any citation so its hover card is gone before the next click."""
    await page.mouse.move(5, 5)
    await page.wait_for_timeout(450)


def viewers(ctx):
    return [p for p in ctx.pages if "viewer.html" in p.url]


async def settle(ctx, ms=900):
    await asyncio.sleep(ms / 1000)
    return viewers(ctx)


async def main():
    guard(PORT)
    env = dict(os.environ, SYNAPSE_DATA_DIR=DATA)
    # PDFs open in browser tabs here, as these checks expect; tabs_checklist.py covers the app's own tabs
    (Path(DATA) / "settings.json").write_text('{"pdf_open_in": "browser"}', encoding="utf-8")
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser"],
        cwd=APP,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.5)
    try:
        item = requests.post(
            f"{BASE}/api/items", json={"authors": "Ljubotina, Marko", "title": "Superdiffusive transport", "year": 2023}
        ).json()
        key = item["citation_key"]
        filler = [spot.FILLER] * 40
        pdf = spot.make_lines_pdf(
            Path(DATA) / "paper.pdf",
            [["Page one", spot.FILLER], spot.PAGE2, ["Page three"] + filler, ["Page four"] + filler],
        )
        with open(pdf, "rb") as fh:
            requests.post(
                f"{BASE}/api/items/{item['id']}/attachments", files={"file": ("paper.pdf", fh, "application/pdf")}
            )
        text = (
            f"# Tabs\n\nPlain [@{key}, p. 3] cite.\n\n"
            f"> We find that energy transport is superdiffusive at infinite temperature in [@{key}, p. 2]\n\n"
            f"Later [@{key}, p. 4] again.\n"
        )
        requests.put(f"{BASE}/api/notes/note", json={"path": "Tabs.md", "create": True, "text": text})

        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(viewport={"width": 1300, "height": 900})
            errors = []
            ctx.on("weberror", lambda e: errors.append(str(e.error)))
            page = await ctx.new_page()
            await page.goto(BASE + "/")
            await page.click('[data-act="open-notes"]')
            await page.click('.nt-row.note[data-path="Tabs.md"]')
            await page.wait_for_selector(".cm-content")
            await page.click('[data-mode="read"]')
            await page.wait_for_selector(".note-read .cite")

            # ---- first citation opens a viewer tab
            async with ctx.expect_page() as pop:
                await page.click('.note-read .cite[data-page="3"]')
            v = await pop.value
            await v.wait_for_function(
                "window.zcViewerGoto && window.zcLinks && document.querySelector('.page-wrapper[data-page-number=\"4\"]')",
                timeout=15000,
            )
            await v.wait_for_timeout(600)
            check(
                "first citation opens the PDF in a tab, at its page",
                len(viewers(ctx)) == 1 and await v.evaluate(TOP_PAGE) == 3,
                await v.evaluate(TOP_PAGE),
            )

            # ---- the quote's citation: same tab, scrolled and flashed
            await page.bring_to_front()
            await away(page)
            await page.click(".note-read blockquote .cite")
            vs = await settle(ctx)
            check(
                "another citation of the same paper reuses that tab",
                len(vs) == 1 and len(ctx.pages) == 2,
                [x.url[-40:] for x in ctx.pages],
            )
            await v.wait_for_selector(".zc-flash", timeout=10000)
            f = await v.evaluate(spot.FLASH)
            check("…scrolled to the quoted words and flashed", len(f) == 1 and f[0]["page"] == 2 and f[0]["visible"], f)
            check(
                "…and its address follows (a reload reopens there)", "page=2" in v.url and "q=We" in v.url, v.url[-80:]
            )
            await shot(v, "1-reused")

            # ---- Back in the viewer returns to where the reader was (page 3)
            await v.wait_for_timeout(400)
            await v.click("#zc-back")
            await v.wait_for_timeout(700)
            check(
                "Back in the viewer returns to the previous spot",
                await v.evaluate(TOP_PAGE) == 3,
                await v.evaluate(TOP_PAGE),
            )
            await v.click("#zc-forward")
            await v.wait_for_timeout(700)
            check("…and Forward to the citation again", await v.evaluate(TOP_PAGE) == 2, await v.evaluate(TOP_PAGE))

            # ---- Cmd/Ctrl+click: a new tab
            await page.bring_to_front()
            await away(page)
            async with ctx.expect_page() as pop:
                await page.locator('.note-read .cite[data-page="4"]').click(modifiers=[MOD])
            v2 = await pop.value
            await v2.wait_for_function(
                "window.zcViewerGoto && document.querySelector('.page-wrapper[data-page-number=\"4\"]')", timeout=15000
            )
            await v2.wait_for_timeout(600)
            check(
                "Cmd/Ctrl+click opens a new tab, at the cited page",
                len(viewers(ctx)) == 2 and await v2.evaluate(TOP_PAGE) == 4,
            )

            # ---- a plain click now reuses the newest tab
            await page.bring_to_front()
            await away(page)
            await page.click('.note-read .cite[data-page="3"]')
            await settle(ctx)
            check(
                "a plain click then reuses the newest tab",
                len(viewers(ctx)) == 2 and await v2.evaluate(TOP_PAGE) == 3 and await v.evaluate(TOP_PAGE) == 2,
            )

            # ---- middle click: new tab
            await away(page)
            async with ctx.expect_page() as pop:
                await page.locator('.note-read .cite[data-page="4"]').click(button="middle")
            v3 = await pop.value
            await v3.wait_for_load_state()
            check("middle click opens a new tab", len(await settle(ctx)) == 3)
            await v3.close()
            await v2.close()

            # ---- editor: Cmd/Ctrl+click reuses, Cmd/Ctrl+Shift+click opens a new tab
            await page.click('[data-mode="live"]')
            await page.wait_for_selector(".cm-lp-chip.cite")
            await away(page)
            await page.locator('.cm-line:has-text("Later") .cm-lp-chip.cite').click(modifiers=[MOD])
            await settle(ctx)
            check(
                "editor: Cmd/Ctrl+click reuses the tab",
                len(viewers(ctx)) == 1 and await v.evaluate(TOP_PAGE) == 4,
                len(viewers(ctx)),
            )
            await away(page)
            async with ctx.expect_page() as pop:
                await page.locator('.cm-line:has-text("Plain") .cm-lp-chip.cite').click(modifiers=[MOD, "Shift"])
            v4 = await pop.value
            await v4.wait_for_load_state()
            check("editor: Cmd/Ctrl+Shift+click opens a new tab", len(await settle(ctx)) == 2)
            await v4.close()

            # ---- hover card PDF button: reuses
            await page.click(".cm-line >> nth=0")
            await page.mouse.move(5, 5)
            await page.locator('.cm-line:has-text("We find") .cm-lp-chip.cite').hover()
            await page.wait_for_selector('.cite-card:not([hidden]) [data-cc="pdf"]')
            await page.click('.cite-card [data-cc="pdf"]')
            await settle(ctx)
            check(
                "citation card's PDF button reuses the tab", len(viewers(ctx)) == 1 and await v.evaluate(TOP_PAGE) == 2
            )

            # ---- the app tab was reloaded (it forgot its tabs): the named tab is still reused
            await page.reload()
            await page.wait_for_selector(".note-read .cite, .cm-lp-chip.cite", timeout=10000)
            if await page.locator(".note-read .cite").count() == 0:
                await page.click('[data-mode="read"]')
            await away(page)
            await page.click('.note-read .cite[data-page="4"]')
            await settle(ctx, 1500)
            vs = viewers(ctx)
            check("after reloading the app, a citation still lands in the same tab", len(vs) == 1, len(vs))
            v = vs[0]
            await v.wait_for_function(
                "window.zcViewerGoto && document.querySelector('.page-wrapper[data-page-number=\"4\"]')", timeout=15000
            )
            await v.wait_for_timeout(600)
            check("…at the cited page", await v.evaluate(TOP_PAGE) == 4)

            # ---- closed tab: a new one opens
            await v.close()
            await page.bring_to_front()
            await away(page)
            async with ctx.expect_page() as pop:
                await page.click('.note-read .cite[data-page="3"]')
            v5 = await pop.value
            await v5.wait_for_load_state()
            check("once the tab is closed, a citation opens a new one", len(await settle(ctx)) == 1)

            check("no JavaScript errors", not errors, errors[:3])
            await b.close()
    finally:
        srv.terminate()
        shutil.rmtree(DATA, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
