"""Formatting keys, paired delimiters and tables within Live-preview callouts.

    python tests/e2e/run.py writing

Starts a server with a disposable library; the user's data is never opened.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent))
from _guard import guard  # noqa: E402

APP = Path(__file__).resolve().parents[2]
PORT = 8841
BASE = f"http://127.0.0.1:{PORT}"
results = []
TABLE = """> [!note] Results
> Before *italic*.
>
> | Regime | Value |
> | --- | --- |
> | $x^2$ | ++underlined++ |
>
> After **bold**.
>
> > [!tip] Nested
> >
> > | A | B |
> > | --- | --- |
> > | nested | value |

Outside.
"""
DRAG_NOTE = (
    "> [!note] Drag selection\n> Start drag here. "
    + (
        "The state $a_k$ changes as $b_k \\to \\infty$, while the value $P=2^N\\,\\Tr[\\rho^2]$ "
        "changes from $P=1.25$ to $P=12.50$ and $P=27.75$. "
        "Select `inline code`, *italic text* and $8$ values across wrapped rows. "
    )
    * 3
    + "End drag here.\n\nOutside.\n"
)


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra, flush=True)


async def text(page):
    return await page.evaluate("ed.text()")


async def set_text(page, value, anchor=None, head=None):
    if anchor is None:
        anchor = len(value)
    if head is None:
        head = anchor
    await page.evaluate(
        """([value, anchor, head]) => {
        ed.replace(value);
        ed.view.dispatch({selection: {anchor, head}});
        ed.focus();
    }""",
        [value, anchor, head],
    )


async def drag_reflow(page):
    for width, focused in ((w, f) for w in (1440, 1100, 800) for f in (False, True)):
        context = f"{width}px, {'focused' if focused else 'unfocused'}"
        await page.set_viewport_size({"width": width, "height": 1000})
        await set_text(page, DRAG_NOTE, 0)
        await page.click(".note-crumbs")
        if focused:
            await page.evaluate("ed.focus()")
        await page.wait_for_function("document.querySelectorAll('.cm-lp-math').length >= 18")
        await page.evaluate("document.fonts.ready")
        points = await page.evaluate("""() => {
            window.dragWidgets = [...document.querySelectorAll('.cm-lp-math')];
            window.dragRects = dragWidgets.map(el => el.getBoundingClientRect());
            const root = document.querySelector('.cm-lp-callout-block');
            const point = (text, end) => {
                const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                for (let n = walk.nextNode(); n; n = walk.nextNode()) {
                    const i = n.data.indexOf(text);
                    if (i < 0) continue;
                    const from = i + (end ? text.length - 1 : 0), range = document.createRange();
                    range.setStart(n, from); range.setEnd(n, from + 1);
                    const r = range.getBoundingClientRect();
                    return {x: end ? r.right - 1 : r.left + 1, y: (r.top + r.bottom) / 2};
                }
                throw new Error('Missing drag endpoint');
            };
            return {start: point('Start drag here', false), end: point('End drag here', true)};
        }""")
        await page.mouse.move(points["start"]["x"], points["start"]["y"])
        await page.mouse.down()
        await page.mouse.move(points["end"]["x"], points["end"]["y"], steps=40)
        during = await page.evaluate("""() => ({
            held: dragWidgets.every(el => el.isConnected),
            stable: dragWidgets.every((el, i) => {
                const now = el.getBoundingClientRect(), before = dragRects[i];
                return Math.abs(now.x - before.x) < 1 && Math.abs(now.y - before.y) < 1
                    && Math.abs(now.width - before.width) < 1;
            }),
            source: document.querySelectorAll('.cm-lp-mathsrc').length,
            selected: !ed.view.state.selection.main.empty,
        })""")
        check(
            f"{context}: dragging keeps the rendered equations in place",
            during["held"] and during["stable"] and during["source"] == 0 and during["selected"],
            during,
        )
        await page.mouse.up()
        await page.wait_for_function("document.querySelectorAll('.cm-lp-mathsrc').length > 0")
        check(f"{context}: release reveals source without changing the document", await text(page) == DRAG_NOTE)
        await page.keyboard.press("ArrowRight")
        await page.wait_for_function("document.querySelectorAll('.cm-lp-math').length >= 18")
        check(
            f"{context}: collapsing the selection restores readable math",
            await page.locator(".cm-lp-math").evaluate_all(
                "els => els.every(el => el.getBoundingClientRect().width > 1)"
            )
            and await text(page) == DRAG_NOTE,
        )
    await page.set_viewport_size({"width": 1440, "height": 1000})


async def main():
    guard(PORT)
    data = tempfile.mkdtemp(prefix="synapse-writing-")
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser"],
        cwd=APP,
        env=dict(os.environ, SYNAPSE_DATA_DIR=data),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                if requests.get(BASE + "/api/settings", timeout=1).ok:
                    break
            except requests.ConnectionError:
                pass
            await asyncio.sleep(0.1)
        requests.post(
            BASE + "/api/notes/note", json={"path": "Writing.md", "title": "Writing", "text": "Start."}
        ).raise_for_status()
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel=os.environ.get("CHROME_CHANNEL"))
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
            await page.goto(BASE + "/#note=Writing.md")
            await page.wait_for_selector(".cm-content")
            await page.evaluate("async () => { window.ed = (await import('/js/views/notes/workspace.js')).ws.editor; }")
            for mode in ("live", "source"):
                await page.click(f'[data-mode="{mode}"]')
                for key, mark in (("b", "**"), ("i", "*"), ("u", "++"), ("Shift+x", "~~"), ("Shift+c", "`")):
                    await set_text(page, "word", 0, 4)
                    await page.keyboard.press("ControlOrMeta+" + key)
                    check(f"{mode}: {key} formats selected text", await text(page) == mark + "word" + mark)
                    await page.keyboard.press("ControlOrMeta+" + key)
                    check(f"{mode}: {key} toggles formatting off", await text(page) == "word")
                await set_text(page, "word", 0, 4)
                await page.keyboard.press("ControlOrMeta+b")
                await page.keyboard.press("ControlOrMeta+z")
                check(f"{mode}: formatting undoes in one step", await text(page) == "word")
                await page.keyboard.press("ControlOrMeta+Shift+z")
                check(f"{mode}: redo restores formatting", await text(page) == "**word**")
                await set_text(page, "label", 0, 5)
                await page.keyboard.press("ControlOrMeta+k")
                await page.keyboard.type("https://example.org")
                check(f"{mode}: link shortcut selects the URL", await text(page) == "[label](https://example.org)")
                await set_text(page, "")
                await page.keyboard.type("$x$")
                check(
                    f"{mode}: dollars pair and skip the closing mark",
                    await text(page) == "$x$" and await page.evaluate("ed.view.state.selection.main.head") == 3,
                )
                await set_text(page, "word", 4, 0)
                await page.keyboard.type("`")
                check(
                    f"{mode}: backticks surround a backward selection",
                    await text(page) == "`word`"
                    and await page.evaluate("ed.view.state.selection.main.anchor > ed.view.state.selection.main.head"),
                )
                await set_text(page, "")
                await page.keyboard.type("$$")
                check(
                    f"{mode}: consecutive dollars create a display pair",
                    await text(page) == "$$$$" and await page.evaluate("ed.view.state.selection.main.head") == 2,
                )
                await page.keyboard.press("Backspace")
                check(f"{mode}: backspace removes an empty pair", await text(page) == "")
                await page.keyboard.type("$")
                await page.keyboard.press("ControlOrMeta+z")
                check(f"{mode}: paired insertion undoes cleanly", await text(page) == "")
                await page.keyboard.press("ControlOrMeta+Shift+z")
                await page.evaluate("ed.view.dispatch({selection: {anchor: 1}})")
                await page.keyboard.type("x$")
                check(f"{mode}: redo restores a usable generated closer", await text(page) == "$x$")
                await set_text(page, "")
                await page.keyboard.type("```python")
                await page.keyboard.press("Enter")
                await page.keyboard.type("x = 1")
                check(
                    f"{mode}: triple backticks create an editable code fence",
                    await text(page) == "```python\nx = 1\n```",
                )
                await set_text(page, "`code`", 3)
                await page.keyboard.type("$")
                check(f"{mode}: dollars inside code stay literal", await text(page) == "`co$de`")

            await page.click('[data-mode="live"]')
            await drag_reflow(page)
            await set_text(page, TABLE)
            await page.click(".note-crumbs")
            await page.wait_for_selector(".cm-lp-callout-block .cm-lp-table table")
            await page.screenshot(path="/tmp/synapse-callout-tables.png")
            layout = await page.evaluate("""() => {
                const blocks = [...document.querySelectorAll('.cm-lp-callout-block')];
                const outer = blocks.find(el => el.classList.contains('cm-lp-callout-note'));
                const inner = blocks.find(el => el.classList.contains('cm-lp-callout-tip'));
                return {blocks: blocks.length, tables: outer.querySelectorAll('table').length,
                    nested: outer.contains(inner), doubled: outer.querySelectorAll('.cm-lp-table blockquote').length,
                    before: [...outer.querySelectorAll('.cm-line')].some(el => el.textContent.includes('Before')),
                    after: [...outer.querySelectorAll('.cm-line')].some(el => el.textContent.includes('After')),
                    color: getComputedStyle(outer).backgroundColor,
                    math: outer.querySelectorAll('table .katex').length,
                    underline: outer.querySelectorAll('table u').length};
            }""")
            check(
                "tables and surrounding paragraphs share continuous callout containers",
                layout["blocks"] == 2
                and layout["tables"] == 2
                and layout["before"]
                and layout["after"]
                and layout["nested"]
                and layout["doubled"] == 0,
                layout,
            )
            check("table cells render math and underline", layout["math"] == 1 and layout["underline"] == 1, layout)
            await page.locator(".cm-lp-table").first.click()
            await page.wait_for_timeout(100)
            check(
                "a callout table remains inside its container while editing its source",
                await page.locator(".cm-lp-callout-note .cm-lp-tablesrc").count() >= 3 and await text(page) == TABLE,
            )
            await page.keyboard.press(
                "ControlOrMeta+z"
            )  # restore the preceding fixture edit only after checking the table
            await set_text(page, TABLE)
            await page.click('[data-mode="read"]')
            check(
                "read mode keeps both tables inside their callouts",
                await page.locator(".note-read .callout table").count() == 2,
            )
            await page.click('[data-mode="live"]')
            await set_text(page, "> [!note] Title\n> ++under **bold** and *italic*++\n\nOutside.")
            await page.click(".note-crumbs")
            styles = await page.locator(".cm-lp-callout .cmt-underline").evaluate_all(
                "els => els.map(el => getComputedStyle(el).textDecorationLine)"
            )
            check(
                "live underline renders inside callouts", bool(styles) and all("underline" in s for s in styles), styles
            )
            await page.screenshot(path="/tmp/synapse-writing.png")
            check("no browser errors or viewport stabilization warnings", not errors, errors[:5])
            await browser.close()
    finally:
        srv.terminate()
        srv.wait()
        shutil.rmtree(data, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
