"""Browser checklist for note rendering (plan M4). Not part of the pytest run.

    python tests/e2e/render_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8766 with a throw-away data folder; your library is not touched.
"""
import asyncio, base64, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
PORT = 8766
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-render-e2e-")
NOTES = Path(DATA) / "notes"
SHOTS = os.environ.get("SHOTS")
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png", clip={"x": 0, "y": 0, "width": 1440, "height": 1700})


async def count(page, sel):
    return await page.locator(sel).count()


async def wait_for(fn, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if await fn():
            return True
        await asyncio.sleep(0.15)
    return False


def setup():
    item = requests.post(f"{BASE}/api/items", json={"authors": "Cui, Jian; Cirac, J. Ignacio", "title": "Variational matrix product operators", "year": 2015}).json()
    assert item["citation_key"] == "cuiVariational2015", item
    requests.post(f"{BASE}/api/notes/note", json={"title": "Other", "text": "# Other\n\n## Results\n\nEmbedded **content** with $x^2$.\n"})
    img = requests.post(f"{BASE}/api/notes/attachments", files={"file": ("pixel.png", PNG, "image/png")}).json()
    text = (APP / "tests/e2e/kitchen-sink.md").read_text().replace("attachments/pixel.png", img["path"])
    body = text.split("---\n", 2)[2]   # drop the fixture's front-matter; the app writes its own
    requests.post(f"{BASE}/api/notes/note", json={"path": "Kitchen sink.md", "title": "Kitchen sink", "text": body})
    return item


async def main():
    env = dict(os.environ, SYNAPSE_DATA_DIR=DATA)
    srv = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--no-browser"], cwd=APP, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    try:
        item = setup()
        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(viewport={"width": 1440, "height": 3200})   # tall: CodeMirror only draws lines near the screen
            page = await ctx.new_page()
            errors, dialogs = [], []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("dialog", lambda d: (dialogs.append(d.message), asyncio.ensure_future(d.dismiss())))
            await page.goto(BASE + "/")
            await page.wait_for_timeout(800)
            await page.click('[data-act="open-notes"]')
            await page.click('.nt-row.note[data-path="Kitchen sink.md"]')
            await page.wait_for_selector('.cm-content')
            await wait_for(lambda: count(page, '.cm-lp-chip.unresolved'))

            # ---- live preview (cursor is on the first line, so everything else is rendered)
            check("live: inline and one-line display math rendered", await count(page, '.cm-lp-math .katex') >= 3)
            check("live: $$ block rendered", await count(page, '.cm-lp-mathblock .katex-display') == 1)
            money = await page.evaluate("[...document.querySelectorAll('.cm-line')].find(l => l.textContent.includes('costs'))?.innerHTML || ''")
            check("live: '$5 and $10' is not math", '$5 and $10' in money and 'katex' not in money)
            check("live: table rendered", await count(page, '.cm-lp-table table') == 1)
            check("live: wiki links as chips", await count(page, '.cm-lp-chip.wikilink') >= 2)
            check("live: missing note marked unresolved", await count(page, '.cm-lp-chip.unresolved') == 1)
            labels = await page.locator('.cm-lp-chip.cite').all_text_contents()
            check("live: citations as chips with author-year", any("Cui & Cirac 2015, p. 4" in l for l in labels), labels)
            check("live: unknown key marked missing", await count(page, '.cm-lp-chip.cite.missing') == 1)
            check("live: two task checkboxes", await count(page, '.cm-lp-task') == 2)
            check("live: callout lines styled", await count(page, '.cm-line.cm-lp-callout') >= 4)
            check("live: code stays code", await page.evaluate("[...document.querySelectorAll('.cm-line')].some(l => l.textContent.includes('[@notacite]'))"))
            await wait_for(lambda: count(page, '.cm-lp-mermaid svg'), 15)
            check("live: mermaid diagram rendered", await count(page, '.cm-lp-mermaid svg') == 1)
            check("live: image shown", await page.evaluate("(() => { const i = document.querySelector('.cm-lp-image img'); return !!i && i.complete && i.naturalWidth > 0; })()"))
            check("live: heading marks hidden", await page.evaluate("!document.querySelector('.cm-lp-h1').textContent.startsWith('#')"))
            await shot(page, "live-light")

            # ---- reveal: put the cursor on the math line
            await page.locator('.cm-line', has_text='Inline math').click(position={"x": 5, "y": 8})
            await page.wait_for_timeout(200)
            check("live: clicking math shows its source", await count(page, '.cm-lp-mathsrc') >= 1)

            # ---- tick a task in the live preview
            await page.locator('.cm-lp-task').first.dispatch_event('mousedown')
            await page.wait_for_timeout(2200)
            check("live: ticking a task edits the file", "- [x] an open task" in (NOTES / "Kitchen sink.md").read_text())

            # ---- read mode
            await page.click('[data-mode="read"]')
            await page.wait_for_selector('.note-read:not([hidden]) .prose h1')
            await wait_for(lambda: count(page, '.note-read .mermaid-block svg'), 15)
            await wait_for(lambda: page.evaluate("document.querySelector('.note-read .note-embed .embed-body')?.textContent.includes('Embedded')"))
            check("read: math", await count(page, '.note-read .katex') >= 4)
            check("read: table, callouts, tasks", await count(page, '.note-read table') == 1 and await count(page, '.note-read .callout') == 2
                  and await count(page, '.note-read .task-box') == 2)
            check("read: mermaid", await count(page, '.note-read .mermaid-block svg') == 1)
            check("read: code highlighted", await count(page, '.note-read .code-block .cmt-kw') >= 1)
            check("read: embed filled", await page.evaluate("document.querySelector('.note-read .note-embed .embed-body').textContent.includes('Embedded content')"))
            check("read: properties box", await count(page, '.note-read details.properties') == 1)
            check("read: citations and missing key", await count(page, '.note-read .cite') == 4 and await count(page, '.note-read .cite.missing') == 1)
            html = await page.inner_html('.note-read .prose')
            check("read: raw HTML shown as text, never run", '&lt;script&gt;' in html and '<script' not in html and not dialogs)
            check("read: footnote", await count(page, '.note-read .footnotes') == 1)
            await shot(page, "read-light")

            await page.locator('.note-read .task-box').nth(1).click()
            await page.wait_for_timeout(2200)
            check("read: ticking a task edits the file", "- [ ] a done task" in (NOTES / "Kitchen sink.md").read_text())

            await page.locator('.note-read .cite:not(.missing)').first.click()
            await page.wait_for_timeout(700)
            check("read: clicking a citation opens the item in the library", await page.is_visible('#items-pane')
                  and await page.evaluate(f"!!document.querySelector('.item-row.selected, [data-id=\"{item['id']}\"].selected')"))
            await page.click('[data-act="open-notes"]')
            await page.wait_for_selector('.note-read:not([hidden]) .prose h1')

            # ---- dark theme
            await page.evaluate("document.documentElement.dataset.theme='dark'")
            await page.wait_for_timeout(300)
            await shot(page, "read-dark")

            # ---- Cmd/Ctrl+E back to live
            await page.locator('.note-read').focus()
            await page.keyboard.press('ControlOrMeta+e')
            await page.wait_for_timeout(300)
            check("Cmd/Ctrl+E returns to the editor", await page.is_hidden('.note-read') and await count(page, '.cm-lp-widget') > 0)
            await shot(page, "live-dark")

            # ---- source mode shows plain Markdown
            await page.click('[data-mode="source"]')
            await page.wait_for_timeout(300)
            check("source mode: no rendered widgets", await count(page, '.cm-lp-widget') == 0 and await count(page, '.cm-lp-chip') == 0)
            await page.click('[data-mode="live"]')

            # ---- links: existing note opens; missing note offers to create it
            await page.locator('.cm-lp-chip.wikilink:not(.unresolved)').first.click()
            await page.wait_for_timeout(700)
            check("clicking [[Other]] opens it", "Other" in await page.text_content('.note-crumbs'))
            await page.click('.nt-row.note[data-path="Kitchen sink.md"]')
            await wait_for(lambda: count(page, '.cm-lp-chip.unresolved'))
            await page.locator('.cm-lp-chip.unresolved').click()
            await page.wait_for_selector('.dialog')
            await page.click('.dialog button:has-text("Create")')
            await page.wait_for_timeout(800)
            check("clicking a missing link creates the note", (NOTES / "Missing note.md").exists()
                  and "Missing note" in await page.text_content('.note-crumbs'))
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
