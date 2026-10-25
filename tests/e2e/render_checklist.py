"""Browser checklist for note rendering (plan M4). Not part of the pytest run.

    python tests/e2e/render_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8766 with a throw-away data folder; your library is not touched.
"""

import asyncio
import base64
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
PORT = 8766
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-render-e2e-")
NOTES = Path(DATA) / "notes"
SHOTS = os.environ.get("SHOTS")
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
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
    item = requests.post(
        f"{BASE}/api/items",
        json={"authors": "Cui, Jian; Cirac, J. Ignacio", "title": "Variational matrix product operators", "year": 2015},
    ).json()
    assert item["citation_key"] == "cuiVariational2015", item
    requests.post(
        f"{BASE}/api/notes/note",
        json={"title": "Other", "text": "# Other\n\n## Results\n\nEmbedded **content** with $x^2$.\n"},
    )
    # embeds inside embeds: each level cites the item; the fourth level is one too deep
    requests.post(f"{BASE}/api/notes/note", json={"path": "Embeds.md", "title": "Embeds", "text": "![[Deep 1]]\n"})
    for i in range(1, 5):
        more = f"\n\n![[Deep {i + 1}]]\n" if i < 4 else "\n"
        text = f"Level {i} cites [@cuiVariational2015].{more}"
        requests.post(f"{BASE}/api/notes/note", json={"path": f"Deep {i}.md", "title": f"Deep {i}", "text": text})
    img = requests.post(f"{BASE}/api/notes/attachments", files={"file": ("pixel.png", PNG, "image/png")}).json()
    text = (APP / "tests/e2e/kitchen-sink.md").read_text().replace("attachments/pixel.png", img["path"])
    body = text.split("---\n", 2)[2]  # drop the fixture's front-matter; the app writes its own
    requests.post(f"{BASE}/api/notes/note", json={"path": "Kitchen sink.md", "title": "Kitchen sink", "text": body})
    table = (
        "# Tables\n\n## Method \\label{sec:method}\n\n"
        "$$E = mc^2 \\label{eq:energy}$$\n\nIn the convention of [@cuiVariational2015, p. 1]:\n\n"
        "| Regime | Ref |\n|---|---|\n"
        "| small $\\mu$ | [@cuiVariational2015]; \\cref{eq:energy} and \\cref{sec:method} |\n\nEnd.\n"
    )
    requests.post(f"{BASE}/api/notes/note", json={"path": "Table math.md", "title": "Table math", "text": table})
    long = "# Long\n\n" + "\n\n".join(f"Paragraph {i} of a long note." for i in range(1, 301)) + "\n"
    requests.post(f"{BASE}/api/notes/note", json={"path": "Long.md", "title": "Long", "text": long})
    return item


# the number of the first "Paragraph N" at the top of the editor or of the read view
TOP_PARAGRAPH = """(read) => {
  const box = document.querySelector(read ? '.note-read' : '.note-editor-view:not([hidden]) .cm-scroller').getBoundingClientRect();
  const els = document.querySelectorAll(read ? '.note-read .prose p' : '.note-editor-view:not([hidden]) .cm-line');
  const el = [...els].find((e) => /Paragraph/.test(e.textContent) && e.getBoundingClientRect().bottom > box.top + 1);
  return el ? Number(/Paragraph (\\d+)/.exec(el.textContent)[1]) : null;
}"""


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
        item = setup()
        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(
                viewport={"width": 1440, "height": 3200}
            )  # tall: CodeMirror only draws lines near the screen
            page = await ctx.new_page()
            errors, dialogs = [], []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("dialog", lambda d: (dialogs.append(d.message), asyncio.ensure_future(d.dismiss())))
            await page.goto(BASE + "/")
            await page.wait_for_timeout(800)
            await page.click('[data-act="open-notes"]')
            await page.click('.nt-row.note[data-path="Kitchen sink.md"]')
            await page.wait_for_selector(".cm-content")
            await wait_for(lambda: count(page, ".cm-lp-chip.unresolved"))

            # ---- live preview (cursor is on the first line, so everything else is rendered)
            check("live: inline and one-line display math rendered", await count(page, ".cm-lp-math .katex") >= 3)
            check("live: $$ block rendered", await count(page, ".cm-lp-mathblock .katex-display") == 1)
            money = await page.evaluate(
                "[...document.querySelectorAll('.note-editor-view:not([hidden]) .cm-line')].find(l => l.textContent.includes('costs'))?.innerHTML || ''"
            )
            check("live: '$5 and $10' is not math", "$5 and $10" in money and "katex" not in money)
            check("live: table rendered", await count(page, ".cm-lp-table table") == 1)
            check("live: wiki links as chips", await count(page, ".cm-lp-chip.wikilink") >= 2)
            check("live: missing note marked unresolved", await count(page, ".cm-lp-chip.unresolved") == 1)
            labels = await page.locator(".cm-lp-chip.cite").all_text_contents()
            check(
                "live: citations as chips with author-year", any("Cui & Cirac 2015, p. 4" in l for l in labels), labels
            )
            check("live: unknown key marked missing", await count(page, ".cm-lp-chip.cite.missing") == 1)
            check("live: two task checkboxes", await count(page, ".cm-lp-task") == 2)
            check("live: callout lines styled", await count(page, ".cm-line.cm-lp-callout") >= 4)
            check(
                "live: code stays code",
                await page.evaluate(
                    "[...document.querySelectorAll('.note-editor-view:not([hidden]) .cm-line')].some(l => l.textContent.includes('[@notacite]'))"
                ),
            )
            await wait_for(lambda: count(page, ".cm-lp-mermaid svg"), 15)
            check("live: mermaid diagram rendered", await count(page, ".cm-lp-mermaid svg") == 1)
            check(
                "live: image shown",
                await page.evaluate(
                    "(() => { const i = document.querySelector('.cm-lp-image img'); return !!i && i.complete && i.naturalWidth > 0; })()"
                ),
            )
            check(
                "live: heading marks hidden",
                await page.evaluate("!document.querySelector('.cm-lp-h1').textContent.startsWith('#')"),
            )
            await shot(page, "live-light")

            # ---- reveal: the cursor's line keeps its formulas rendered; clicking one opens only that one
            line = page.locator(".cm-line", has_text="Inline math")
            await line.click(position={"x": 5, "y": 8})
            await page.wait_for_timeout(200)
            check(
                "live: the cursor's line keeps its formulas rendered",
                await line.locator(".cm-lp-math").count() >= 3 and await count(page, ".cm-lp-mathsrc") == 0,
            )
            bb = await line.locator(".cm-lp-math .katex-html").nth(1).bounding_box()
            await page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
            await page.wait_for_timeout(200)
            src = await page.locator(".cm-lp-mathsrc").all_text_contents()
            check(
                "live: clicking a formula shows only its source",
                src == ["$\\alpha_i \\leq \\beta$"] and await line.locator(".cm-lp-math").count() >= 2,
                src,
            )

            # ---- tick a task in the live preview
            await page.locator(".cm-lp-task").first.dispatch_event("mousedown")
            await page.wait_for_timeout(2200)
            check(
                "live: ticking a task edits the file", "- [x] an open task" in (NOTES / "Kitchen sink.md").read_text()
            )

            # ---- read mode
            await page.click('[data-mode="read"]')
            await page.wait_for_selector(".note-read:not([hidden]) .prose h1")
            await wait_for(lambda: count(page, ".note-read .mermaid-block svg"), 15)
            await wait_for(
                lambda: page.evaluate(
                    "document.querySelector('.note-read .note-embed .embed-body')?.textContent.includes('Embedded')"
                )
            )
            check("read: math", await count(page, ".note-read .katex") >= 4)
            check(
                "read: table, callouts, tasks",
                await count(page, ".note-read table") == 1
                and await count(page, ".note-read .callout") == 2
                and await count(page, ".note-read .task-box") == 2,
            )
            check("read: mermaid", await count(page, ".note-read .mermaid-block svg") == 1)
            check("read: code highlighted", await count(page, ".note-read .code-block .cmt-kw") >= 1)
            check(
                "read: embed filled",
                await page.evaluate(
                    "document.querySelector('.note-read .note-embed .embed-body').textContent.includes('Embedded content')"
                ),
            )
            check("read: properties box", await count(page, ".note-read details.properties") == 1)
            check(
                "read: citations and missing key",
                await count(page, ".note-read .cite") == 4 and await count(page, ".note-read .cite.missing") == 1,
            )
            html = await page.inner_html(".note-read .prose")
            check(
                "read: raw HTML shown as text, never run",
                "&lt;script&gt;" in html and "<script" not in html and not dialogs,
            )
            check("read: footnote", await count(page, ".note-read .footnotes") == 1)
            await shot(page, "read-light")

            await page.evaluate("document.querySelector('.note-read .math-block').dataset.mark = 'kept'")
            await page.locator(".note-read .task-box").nth(1).click()
            await page.wait_for_timeout(2200)
            check("read: ticking a task edits the file", "- [ ] a done task" in (NOTES / "Kitchen sink.md").read_text())
            kept = await count(page, ".note-read .math-block[data-mark='kept']")
            done = await page.locator(".note-read li.task.done").all_text_contents()
            check("read: ticking a task redraws only its list", kept == 1 and done == ["an open task"], (kept, done))

            await page.locator(".note-read .cite:not(.missing)").first.click()
            await page.wait_for_timeout(700)
            check(
                "read: clicking a citation opens the item in the library",
                await page.is_visible("#items-pane")
                and await page.evaluate(
                    f"!!document.querySelector('.item-row.selected, [data-id=\"{item['id']}\"].selected')"
                ),
            )
            await page.click('[data-act="open-notes"]')
            await page.wait_for_selector(".note-read:not([hidden]) .prose h1")

            # ---- dark theme
            await page.evaluate("document.documentElement.dataset.theme='dark'")
            await page.wait_for_timeout(300)
            await shot(page, "read-dark")

            # ---- Cmd/Ctrl+E back to live
            await page.locator(".note-read").focus()
            await page.keyboard.press("ControlOrMeta+e")
            await page.wait_for_timeout(300)
            check(
                "Cmd/Ctrl+E returns to the editor",
                await page.is_hidden(".note-read") and await count(page, ".cm-lp-widget") > 0,
            )
            await page.locator(".cm-lp-mathblock").first.click()
            await page.wait_for_timeout(200)
            math_source = "\n".join(await page.locator(".cm-line.cm-lp-mathsrc-block").all_text_contents())
            check(
                "live: editing display math keeps TeX escapes literal",
                r"\," in math_source,
                math_source,
            )
            await shot(page, "live-dark")

            # ---- source mode shows plain Markdown
            await page.click('[data-mode="source"]')
            await page.wait_for_timeout(300)
            check(
                "source mode: no rendered widgets",
                await count(page, ".cm-lp-widget") == 0 and await count(page, ".cm-lp-chip") == 0,
            )
            await page.click('[data-mode="live"]')

            # ---- links: existing note opens; missing note offers to create it
            await page.locator(".cm-lp-chip.wikilink:not(.unresolved)").first.click()
            await page.wait_for_timeout(700)
            check("clicking [[Other]] opens it", "Other" in await page.text_content(".note-crumbs"))
            await page.click('.nt-row.note[data-path="Kitchen sink.md"]')
            returned = await wait_for(lambda: count(page, ".cm-lp-chip.unresolved"))
            tab_state = await page.evaluate("""({
                path: window.zcNotes.path(), tabs: window.zcTabs.list(),
                mode: [...document.querySelectorAll('.note-modes button')].find(b => b.classList.contains('active'))?.dataset.mode,
                links: [...document.querySelectorAll('.cm-lp-chip.wikilink')].map(e => [e.textContent, e.className]),
                widgets: document.querySelectorAll('.cm-lp-widget').length
            })""")
            check("one tree click returns to the note", returned, tab_state)
            if not returned:
                raise RuntimeError(f"note switch did not finish: {tab_state}")
            await page.locator(".cm-lp-chip.unresolved").click()
            await page.wait_for_selector(".dialog")
            await page.click('.dialog button:has-text("Create")')
            await page.wait_for_timeout(800)
            check(
                "clicking a missing link creates the note",
                (NOTES / "Missing note.md").exists() and "Missing note" in await page.text_content(".note-crumbs"),
            )

            # ---- embedded notes render with their own link data
            await page.click('.nt-row.note[data-path="Embeds.md"]')
            await page.click('[data-mode="read"]')
            await wait_for(
                lambda: page.evaluate(
                    "document.querySelector('.note-read .prose').textContent.includes('Embedded too deeply')"
                )
            )
            levels = await page.evaluate(
                "[...document.querySelectorAll('.note-read .note-embed.filled')].map(e => e.dataset.path)"
            )
            check(
                "read: embeds inside embeds are filled", levels[:3] == ["Deep 1.md", "Deep 2.md", "Deep 3.md"], levels
            )
            check(
                "read: the fourth level is one too deep",
                "Embedded too deeply"
                in await page.evaluate(
                    "document.querySelector('.note-embed[data-path=\"Deep 4.md\"] .embed-body')?.textContent || ''"
                ),
            )
            labels = await page.locator(".note-read .note-embed .cite").all_text_contents()
            check(
                "read: citations inside embeds know their item",
                len(labels) == 3
                and await count(page, ".note-read .note-embed .cite.missing") == 0
                and all("Cui & Cirac 2015" in l for l in labels),
                labels,
            )
            # ---- tables with math and citations; a citation followed by a colon
            await page.click('.nt-row.note[data-path="Table math.md"]')
            await page.click('[data-mode="live"]')
            await wait_for(lambda: count(page, ".cm-lp-table table"))
            check(
                "live: a table with math and a citation is rendered",
                await count(page, ".cm-lp-table .katex") == 1 and await count(page, ".cm-lp-table .cite") == 1,
            )
            table_text = (await page.locator(".cm-lp-table").inner_text()).replace("\u00a0", " ")
            check(
                "live: table references use labels from the whole note",
                "Eq. (1) and Sec. 1.1" in table_text and "??" not in table_text,
                table_text,
            )
            labels = await page.locator(".cm-lp-chip.cite").all_text_contents()
            check("live: [@key, p. 1]: is a citation", any("Cui & Cirac 2015, p. 1" in l for l in labels), labels)

            # ---- switching modes keeps the line at the top
            await page.click('.nt-row.note[data-path="Long.md"]')
            await page.wait_for_function("window.zcNotes.text().includes('Paragraph 300')")
            await page.evaluate(
                "document.querySelector('.note-editor-view:not([hidden]) .cm-scroller').scrollTop = 6000"
            )
            await page.wait_for_function(f"() => ({TOP_PARAGRAPH})(false) > 50")
            tops = [await page.evaluate(TOP_PARAGRAPH, False)]
            for mode in ("read", "source", "read", "live"):
                await page.click(f'[data-mode="{mode}"]')
                await wait_for(
                    lambda: page.evaluate(f"read => Math.abs(({TOP_PARAGRAPH})(read) - {tops[0]}) <= 1", mode == "read")
                )
                tops.append(await page.evaluate(TOP_PARAGRAPH, mode == "read"))
            check(
                "modes keep the place: live, read, source, read, live",
                tops[0] and tops[0] > 50 and max(tops) - min(tops) <= 1,
                tops,
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
