"""Browser checklist for the command palette while a note is being edited (views/notes/commands.ts):
inserting images, figures, galleries, citations, links and tasks; several images at once become one
gallery with Extended Markdown (chosen, pasted or dropped) and separate images without it; modes and
the note's own commands; none of them in the library.

    python tests/e2e/note_commands_checklist.py      (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8809 with a throw-away data folder; your library is not touched.
"""

import asyncio
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from tests.conftest import make_pdf  # noqa: E402

PORT = 8809
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-notecmd-e2e-")
SHOTS = os.environ.get("SHOTS")
MOD = "Meta" if sys.platform == "darwin" else "Control"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


def png(path, w, h, rgb):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    )
    return str(path)


def api(path, method="GET", **kw):
    r = requests.request(method, BASE + path, timeout=10, **kw)
    r.raise_for_status()
    return r.json() if r.content else None


PLAN = "# Plan\n\nFirst idea.\n"


def reset_plan():
    note = api("/api/notes/note?path=plan.md")
    api("/api/notes/note", "PUT", json={"path": "plan.md", "text": PLAN, "hash": note["hash"]})


async def main():
    env = dict(os.environ, SYNAPSE_DATA_DIR=DATA)
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser"],
        cwd=APP,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        try:
            requests.get(BASE, timeout=1)
            break
        except requests.RequestException:
            time.sleep(0.2)
    try:
        smith = api(
            "/api/items", "POST", json={"title": "Open quantum dynamics", "authors": "Smith, John", "year": 2021}
        )
        pdf = make_pdf(Path(DATA) / "upload.pdf", "hello")
        with open(pdf, "rb") as fh:
            api(f"/api/items/{smith['id']}/attachments", "POST", files={"file": ("paper.pdf", fh, "application/pdf")})
        api("/api/notes/note", "POST", json={"path": "plan.md", "title": "Plan", "text": PLAN})
        api("/api/notes/note", "POST", json={"path": "other.md", "title": "Other", "text": "# Other\n"})
        api("/api/notes/note", "POST", json={"item_id": smith["id"]})
        speckle = png(Path(DATA) / "speckle.png", 40, 30, (200, 60, 60))
        clean = png(Path(DATA) / "clean.png", 30, 30, (60, 60, 200))

        async with async_playwright() as p:
            b = await p.chromium.launch()
            page = await b.new_page(viewport={"width": 1300, "height": 850})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(BASE)
            await page.wait_for_selector(".row[data-id]")

            async def palette(q):
                await page.keyboard.press(f"{MOD}+Shift+P")
                await page.wait_for_selector(".palette")
                await page.keyboard.type(q)
                await page.wait_for_timeout(150)
                return await page.eval_on_selector_all(
                    ".palette-item .p-title", "es => es.map(e => e.textContent.trim())"
                )

            async def close_palette():
                if await page.locator(".palette").count():
                    await page.keyboard.press("Escape")

            async def run(title):
                listed = await palette(title)
                if title not in listed:
                    await close_palette()
                    return False
                await page.click(f".palette-item:has(.p-title:text-is('{title}'))")
                return True

            async def text():
                return await page.evaluate("window.zcNotes.text()")

            async def choose(title, files):
                async with page.expect_file_chooser() as fc:
                    ran = await run(title)
                if ran:
                    await (await fc.value).set_files(files)
                return ran

            async def open_plan():
                await page.click('.nt-row.note[data-path="other.md"]')
                await page.wait_for_function("window.zcNotes?.path() === 'other.md'")
                await page.wait_for_timeout(800)  # plan.md saved
                reset_plan()
                await page.click('.nt-row.note[data-path="plan.md"]')
                await page.wait_for_function(
                    "window.zcNotes?.path() === 'plan.md' && window.zcNotes.text().includes('First idea')"
                )
                await page.wait_for_timeout(300)

            async def completion():
                await page.wait_for_selector(".cm-tooltip-autocomplete", timeout=4000)
                await page.wait_for_timeout(250)  # CodeMirror ignores Enter for 75 ms after opening

            listed = await palette("insert")
            await close_palette()
            check(
                "in the library the palette has no note commands",
                not any(t.startswith("Insert ") for t in listed),
                listed,
            )

            await page.click('#sidebar [data-act="open-notes"]')
            await page.wait_for_selector(".nt-row.note")
            await open_plan()
            listed = await palette("insert")
            await close_palette()
            want = {
                "Insert Image…",
                "Insert Figure with Caption…",
                "Insert Citation…",
                "Insert Link to Note…",
                "Insert Table",
            }
            check("with a note open, the insert commands are listed", want <= set(listed), listed)

            # ---- images and figures
            await choose("Insert Image…", [speckle])
            await page.wait_for_function("window.zcNotes.text().includes('](attachments/')")
            t = await text()
            check(
                "Insert Image…: one image is uploaded and inserted",
                t.count("![](attachments/") == 1 and "```" not in t,
                repr(t),
            )

            await open_plan()
            await choose("Insert Image…", [speckle, clean])
            await page.wait_for_function("window.zcNotes.text().includes('```gallery')")
            t = await text()
            g = re.search(r"```gallery\n(!\[\]\(attachments/[^)]+\)\n){2}```", t)
            check("Insert Image…: two images become one gallery, without captions", bool(g), repr(t))
            await page.wait_for_selector(".cm-gallery, .gallery", timeout=4000)
            await shot(page, "1-gallery")

            await open_plan()
            await choose("Insert Figure with Caption…", [speckle])
            await page.wait_for_function("window.zcNotes.text().includes('*Caption.')")
            await page.keyboard.type("Speckle pattern")
            t = await text()
            check(
                "Insert Figure with Caption…: the caption is selected to type over, with a label",
                re.search(r"!\[\]\(attachments/[^)]+\)\n\*Speckle pattern\. \\label\{fig:speckle(-\d+)?\}\*", t),
                repr(t),
            )

            await open_plan()
            await choose("Insert Figure with Caption…", [speckle, clean])
            await page.wait_for_function("window.zcNotes.text().includes('```gallery')")
            await page.keyboard.type("Two systems")
            t = await text()
            check(
                "… with two images: a numbered gallery, panels to caption",
                "Panel a." in t
                and "Panel b." in t
                and re.search(r"\n\nTwo systems\. \\label\{fig:speckle(-\d+)?\}\n```", t),
                repr(t),
            )

            # ---- several images pasted at once
            await open_plan()

            async def paste(paths):
                files = [
                    {"name": Path(x).name, "b64": __import__("base64").b64encode(Path(x).read_bytes()).decode()}
                    for x in paths
                ]
                await page.click(".cm-content")
                await page.keyboard.press(f"{MOD}+End")
                await page.evaluate(
                    """(files) => {
                    const dt = new DataTransfer();
                    for (const f of files) {
                      const bytes = Uint8Array.from(atob(f.b64), (c) => c.charCodeAt(0));
                      dt.items.add(new File([bytes], f.name, { type: 'image/png' }));
                    }
                    document.querySelector('.cm-content').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
                  }""",
                    files,
                )

            await paste([speckle, clean])
            await page.wait_for_function("window.zcNotes.text().includes('```gallery')", timeout=6000)
            t = await text()
            check(
                "pasting two images makes one gallery",
                t.count("```gallery") == 1 and t.count("![](attachments/") == 2,
                repr(t),
            )

            # without Extended Markdown: separate images
            api("/api/plugin-host/extended-markdown/disable", "POST")
            await page.evaluate("window.zcPlugins.sync()")
            await open_plan()
            listed = await palette("insert")
            await close_palette()
            check("without Extended Markdown no figure command", "Insert Figure with Caption…" not in listed, listed)
            await paste([speckle, clean])
            await page.wait_for_function(
                "(window.zcNotes.text().match(/\\]\\(attachments\\//g) || []).length === 2", timeout=6000
            )
            t = await text()
            check("… and pasted images stay separate images", "```" not in t, repr(t))
            api("/api/plugin-host/extended-markdown/enable", "POST")
            await page.evaluate("window.zcPlugins.sync()")

            # ---- citations, links, tasks
            await open_plan()
            await page.click(".cm-content")
            await page.keyboard.press(f"{MOD}+End")
            await run("Insert Citation…")
            await completion()
            await page.keyboard.press("Enter")
            await page.keyboard.press("End")
            t = await text()
            check(
                "Insert Citation…: completion, then [@key] with its bracket",
                f"[@{smith['citation_key']}]" in t,
                repr(t[-60:]),
            )
            await page.keyboard.press("Enter")
            await run("Insert Link to Note…")
            await completion()
            await page.keyboard.type("Oth")
            await page.wait_for_timeout(250)
            await page.keyboard.press("Enter")
            t = await text()
            check("Insert Link to Note…: [[other]], closed once", "[[other]]" in t and "]]]]" not in t, repr(t[-60:]))
            await page.keyboard.press("End")
            await page.keyboard.press("Enter")
            await run("Insert Existing Task…")
            await page.keyboard.type("Draft the outlook")  # no tasks yet: the list starts with what is typed
            await completion()
            await page.keyboard.press("Enter")
            ok = await page.wait_for_function(
                "/\\{\\{task:[a-z0-9]+\\}\\}\\s*$/.test(window.zcNotes.text())", timeout=6000
            )
            t = await text()
            check("Insert Existing Task…: a {{task:id}} line, closed once", ok and "}}}}" not in t, repr(t[-60:]))
            await page.keyboard.press("End")
            await page.keyboard.press("Enter")
            await run("Add Task to This Note…")
            await page.wait_for_selector(".tn-title-input", timeout=4000)
            await page.keyboard.type("Check the speckle model")
            await page.keyboard.press("Enter")
            await page.wait_for_function(
                "(window.zcNotes.text().match(/\\{\\{task:/g) || []).length === 2", timeout=6000
            )
            check("Add Task to This Note…: the new-task popup adds its line", True)

            await run("Insert Table")
            await page.keyboard.type("Time")
            check("Insert Table: the first heading is selected to type over", "| Time | Column |" in await text())

            # ---- modes
            await run("Read Mode")
            await page.wait_for_timeout(300)
            check("Read Mode", await page.evaluate("!!document.querySelector('.note-read:not([hidden])')"))
            listed = await palette("mode")
            await close_palette()
            check("the mode shown is not offered", "Read Mode" not in listed and "Source Mode" in listed, listed)
            await run("Insert Callout")
            await page.keyboard.type("Remember")
            check("inserting from Read goes back to editing", "> [!note] Remember" in await text())

            listed = await palette("this note")
            await close_palette()
            want = {
                "Rename This Note…",
                "Move This Note to a Folder…",
                "Delete This Note…",
                "Copy Link to This Note",
                "Export This Note as PDF",
            }
            check("the note's own commands are listed", want <= set(listed), listed)

            # ---- a paper note
            await page.wait_for_timeout(1000)  # saved
            if not await page.locator('.nt-row.note[data-path^="papers/"]').count():
                await page.click('.nt-row.folder[data-folder="papers"]')
            await page.click('.nt-row.note[data-path^="papers/"]')
            await page.wait_for_function("window.zcNotes?.path().startsWith('papers/')")
            await page.wait_for_timeout(500)
            listed = await palette("paper")
            await close_palette()
            check(
                "a paper note offers its paper and PDF",
                "Show This Note’s Paper in the Library" in listed and "Open This Note’s Paper PDF" in listed,
                listed,
            )

            await page.click('#sidebar [data-view="library"]')
            await page.wait_for_selector(".row[data-id]")
            listed = await palette("insert")
            await close_palette()
            check("back in the library, no note commands", not any(t.startswith("Insert ") for t in listed), listed)
            check("no page errors", not errors, errors)
            await b.close()
    finally:
        srv.terminate()
        srv.wait(timeout=10)
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
