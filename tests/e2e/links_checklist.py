"""Browser checklist for linking (plan M5): autocomplete, citation cards, side panels, item pane.

    python tests/e2e/links_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8767 with a throw-away data folder; your library is not touched.
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

PORT = 8767
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-links-e2e-")
NOTES = Path(DATA) / "notes"
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


def text(name):
    return (NOTES / name).read_text()


async def doc(page):
    return await page.evaluate("window.zcNotes.text()")


async def completion_open(page):
    # CodeMirror ignores Enter for a moment after the list opens or changes (interactionDelay,
    # 75 ms): pressing it at once made this checklist fail now and then
    await page.wait_for_selector(".cm-tooltip-autocomplete")
    await page.wait_for_timeout(250)


async def type_at_end(page, s):
    await page.click(".cm-content")
    await page.keyboard.press("ControlOrMeta+End")
    await page.keyboard.type(s)


def setup():
    cui = requests.post(
        f"{BASE}/api/items",
        json={"authors": "Cui, Jian; Cirac, J. Ignacio", "title": "Variational matrix product operators", "year": 2015},
    ).json()
    smith = requests.post(
        f"{BASE}/api/items", json={"authors": "Smith, Anna", "title": "Quantum things", "year": 2021}
    ).json()
    pdf = make_pdf(Path(DATA) / "upload.pdf", "hello")
    with open(pdf, "rb") as fh:
        requests.post(f"{BASE}/api/items/{cui['id']}/attachments", files={"file": ("paper.pdf", fh, "application/pdf")})
    requests.post(
        f"{BASE}/api/notes/note",
        json={
            "title": "Review",
            "text": f"# Review\n\nCui shows it [@{cui['citation_key']}, p. 3].\n\nSee [[Other]].\n",
        },
    )
    requests.post(
        f"{BASE}/api/notes/note", json={"title": "Other", "text": "# Other\n\nBack to [[Review]] for details.\n"}
    )
    return cui, smith


async def main():
    env = dict(os.environ, SYNAPSE_DATA_DIR=DATA)
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser"],
        cwd=APP,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.5)
    try:
        cui, smith = setup()
        ck, sk = cui["citation_key"], smith["citation_key"]
        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(viewport={"width": 1440, "height": 950})
            page = await ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(BASE + "/")
            await page.wait_for_timeout(800)
            await page.click('[data-act="open-notes"]')
            await page.click('.nt-row.note[data-path="Review.md"]')
            await page.wait_for_selector(".cm-content")
            await wait_for(lambda: page.locator(".notes-side .cite-row").count())

            # ---- side panel
            rows = await page.locator(".notes-side .cite-row .ns-main").all_text_contents()
            check("side panel: cited papers", rows == ["Cui & Cirac 2015"], rows)
            back = await page.locator(".notes-side [data-open-path]").all_text_contents()
            check("side panel: linked from Other", len(back) == 1 and "Other" in back[0], back)

            # ---- autocomplete: [@ inside brackets
            await type_at_end(page, "\nAlso [@smi")
            await completion_open(page)
            labels = await page.locator(".cm-tooltip-autocomplete li .cm-completionLabel").all_text_contents()
            check("[@ offers matching keys", labels and labels[0] == sk, labels)
            await page.keyboard.press("Enter")
            d = await doc(page)
            check(
                "accepting gives [@key] (no doubled bracket)",
                f"Also [@{sk}]" in d and f"[@{sk}]]" not in d,
                d.splitlines()[-1],
            )
            await shot(page, "1-autocomplete")

            # ---- bare @ becomes a bracketed citation
            await type_at_end(page, " and @cui")
            await completion_open(page)
            await page.keyboard.press("Enter")
            d = await doc(page)
            check("bare @cui becomes [@cuiVariational2015]", f"and [@{ck}]" in d, d.splitlines()[-1])

            # ---- [[ notes
            await type_at_end(page, " and [[Oth")
            await completion_open(page)
            await page.keyboard.press("Enter")
            d = await doc(page)
            check(
                "[[Oth completes to [[Other]]",
                d.rstrip().endswith("and [[Other]]") and "[[Other]]]]" not in d,
                d.splitlines()[-1],
            )

            # ---- no completion inside code
            await type_at_end(page, "\nX [@sm")
            await page.keyboard.press("Escape")
            await page.keyboard.press("Control+Space")
            await page.wait_for_timeout(400)
            check(
                "Ctrl+Space opens autocomplete (control)", await page.locator(".cm-tooltip-autocomplete").count() == 1
            )
            await page.keyboard.press("Escape")
            for _ in range(len("X [@sm]")):
                await page.keyboard.press("Backspace")
            await type_at_end(page, "\n`[@smi`")
            await page.keyboard.press("ArrowLeft")
            await page.keyboard.press("Control+Space")  # ask for completion explicitly
            await page.wait_for_timeout(400)
            check("no autocomplete inside inline code", await page.locator(".cm-tooltip-autocomplete").count() == 0)
            await page.keyboard.press("Escape")
            await type_at_end(page, "\nMissing [[Nope]] and [@ghost2000].")
            await page.wait_for_timeout(2500)
            check("saved to disk", f"Also [@{sk}]" in text("Review.md"))
            await wait_for(lambda: page.evaluate("document.querySelectorAll('.notes-side .cite-row').length >= 3"))
            check("side panel updates after save", await page.locator(".notes-side .cite-row").count() == 3)
            unres = await page.locator(".notes-side .ns-row.warn .ns-main").all_text_contents()
            check("unresolved: [[Nope]] and @ghost2000", sorted(unres) == ["@ghost2000", "[[Nope]]"], unres)

            # ---- hover card on a citation chip (cursor elsewhere so chips render)
            await page.keyboard.press("ControlOrMeta+Home")
            chip = page.locator(".cm-lp-chip.cite", has_text="p. 3").first
            await chip.hover()
            await page.wait_for_selector(".cite-card:not([hidden])", timeout=4000)
            card = await page.text_content(".cite-card")
            check(
                "hover card shows the item",
                "Variational matrix product operators" in card and "PDF p. 3" in card,
                card[:120],
            )
            await shot(page, "2-card")

            # PDF button opens the viewer at page 3
            async with ctx.expect_page() as pinfo:
                await page.click('.cite-card [data-cc="pdf"]')
            viewer = await pinfo.value
            check(
                "PDF opens at the cited page", "viewer.html" in viewer.url and "page=3" in viewer.url, viewer.url[:120]
            )
            await viewer.close()

            # create the paper note from the card
            await page.mouse.move(5, 5)
            await page.wait_for_timeout(300)
            await chip.hover()
            await page.wait_for_selector(".cite-card:not([hidden])")
            await page.click('.cite-card [data-cc="new-note"]')
            await page.wait_for_timeout(1000)
            check(
                "card creates the paper note and opens it",
                (NOTES / f"papers/{ck}.md").exists() and ck in await page.text_content(".note-crumbs"),
            )

            # ---- item pane: Notes section
            await page.click('[data-view="library"]')
            await page.wait_for_timeout(500)
            await page.click(f'.row[data-id="{cui["id"]}"]')
            # the pane fills in after a short delay (it may still show the previous item, or "Loading…")
            await wait_for(
                lambda: page.evaluate(
                    "(document.querySelector('[data-ip-notes]')?.textContent || '').includes('p. 3]')"
                )
            )
            notes_txt = await page.text_content("[data-ip-notes]")
            check(
                "item pane lists the paper note and citing notes",
                "paper note" in notes_txt and "Review" in notes_txt and "p. 3]" in notes_txt,
                notes_txt[:160],
            )
            await shot(page, "3-itempane")
            await page.locator("[data-ip-notes] .ip-quote").first.click()
            await page.wait_for_timeout(800)
            check(
                "clicking a quote opens the note",
                await page.is_visible("#notes-view") and "Review" in await page.text_content(".note-crumbs"),
            )

            # item without notes offers to create one
            await page.click('[data-view="library"]')
            await page.wait_for_timeout(300)
            await page.click(f'.row[data-id="{smith["id"]}"]')
            await wait_for(lambda: page.locator('[data-ip-notes] [data-tool="new-paper-note"]').count())
            check(
                "item pane shows Create paper note / cited in",
                await page.locator('[data-ip-notes] [data-tool="new-paper-note"]').count() == 1,
            )

            # ---- read mode: clicking a citation with a page opens the PDF there
            await page.click('[data-act="open-notes"]')
            await page.click('.nt-row.note[data-path="Review.md"]')
            await page.click('[data-mode="read"]')
            await page.wait_for_selector(".note-read .cite")
            async with ctx.expect_page() as pinfo:
                await page.locator('.note-read .cite[data-page="3"]').click()
            viewer = await pinfo.value
            check("read mode: citation click opens the PDF page", "page=3" in viewer.url)
            await viewer.close()
            await page.click('[data-mode="live"]')

            # ---- settings label
            await page.keyboard.press("ControlOrMeta+,")
            await page.wait_for_selector("#settings-view")
            st = await page.text_content("#settings-view")
            check(
                "settings: 'Notes' switch without beta", "Notes (beta)" not in st and "Markdown notes for papers" in st
            )
            check("no console errors", not errors, errors[:5])
            await b.close()
    finally:
        srv.terminate()
        shutil.rmtree(DATA, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _guard import guard  # noqa: E402

guard(PORT)
asyncio.run(main())
