"""Browser checklist for PDFs in the notes folders: the tree, adding, opening, renaming, moving, deleting.
Not part of the pytest run.

    python tests/e2e/notes_pdfs_checklist.py

Starts its own server on port 8860 with a throw-away data folder; your library is not touched.
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

from playwright.async_api import async_playwright

BROWSER = os.environ.get("SYNAPSE_E2E_BROWSER", "chromium")
APP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PDF = Path(APP) / "tests" / "e2e" / "fixtures" / "twocolumn.pdf"
PORT = 8860
BASE = f"http://127.0.0.1:{PORT}/"
DATA = tempfile.mkdtemp(prefix="zc-notes-pdfs-e2e-")
D = Path(DATA) / "notes"

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name, extra)


def start_server():
    env = dict(os.environ, SYNAPSE_DATA_DIR=DATA)
    return subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser"],
        cwd=APP,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# Drop files on a tree row the way the browser does (a real OS drag cannot be driven from here).
DROP = """([sel, files]) => {
  const dt = new DataTransfer();
  for (const [name, type, b64] of files) {
    const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    dt.items.add(new File([bytes], name, { type }));
  }
  const el = document.querySelector(sel);
  for (const t of ['dragenter', 'dragover', 'drop']) el.dispatchEvent(new DragEvent(t, { dataTransfer: dt, bubbles: true, cancelable: true }));
}"""


async def main():
    srv = start_server()
    time.sleep(1.5)
    pdf_b64 = base64.b64encode(PDF.read_bytes()).decode()
    async with async_playwright() as p:
        b = await getattr(p, BROWSER).launch()
        ctx = await b.new_context(viewport={"width": 1440, "height": 900})
        errs = []
        A = await ctx.new_page()
        A.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        await A.goto(BASE)
        await A.wait_for_timeout(1200)
        await A.click('[data-act="open-notes"]')
        await A.wait_for_timeout(500)
        await A.request.post(BASE + "api/notes/folder", data={"path": "Reading"})
        await A.click('[data-act="tree-menu"]')
        await A.click('.menu :text("Rescan notes folder")')
        await A.wait_for_timeout(500)
        check("a new folder is in the tree", await A.locator('.nt-row.folder[data-folder="Reading"]').count() == 1)

        # 1. dropping a PDF on a folder adds it there
        await A.evaluate(
            DROP, ['.nt-row.folder[data-folder="Reading"]', [["My Paper.pdf", "application/pdf", pdf_b64]]]
        )
        await A.wait_for_selector('.nt-row.pdf[data-path="Reading/My Paper.pdf"]')
        check(
            "a dropped PDF is stored in the folder", (D / "Reading" / "My Paper.pdf").read_bytes() == PDF.read_bytes()
        )

        # 2. dropping something that is not a PDF is refused
        await A.evaluate(DROP, [".notes-tree", [["notes.txt", "text/plain", base64.b64encode(b"hello").decode()]]])
        await A.wait_for_selector('.toast:has-text("Only PDF")')
        check(
            "a dropped text file is refused",
            "Only PDF" in (await A.inner_text("body")) and not list(D.glob("*.txt")) and not list(D.glob("*/*.txt")),
        )
        # a file named .pdf with other content is refused by the server
        await A.evaluate(
            DROP, [".notes-tree", [["fake.pdf", "application/pdf", base64.b64encode(b"<html></html>").decode()]]]
        )
        await A.wait_for_timeout(800)
        check(
            "a fake PDF is refused",
            not (D / "fake.pdf").exists() and await A.locator('.nt-row.pdf[data-path="fake.pdf"]').count() == 0,
        )

        # 3. "Add PDF…" on a folder's menu
        await A.click('.nt-row.folder[data-folder="Reading"]', button="right")
        async with A.expect_file_chooser() as fc:
            await A.click('.menu :text("Add PDF…")')
        await (await fc.value).set_files(str(PDF))
        await A.wait_for_selector('.nt-row.pdf[data-path="Reading/twocolumn.pdf"]')
        check("Add PDF… adds the chosen file to the folder", (D / "Reading" / "twocolumn.pdf").is_file())

        # 4. opening: a click opens the viewer in a tab, by number
        await A.click('.nt-row.pdf[data-path="Reading/My Paper.pdf"]')
        await A.wait_for_selector(".tab-frame.active")
        src = await A.get_attribute(".tab-frame.active", "src")
        check(
            "clicking a PDF opens it in the viewer by its number", src and "npdf=1" in src and "note-pdfs" in src, src
        )
        frame = A.frame_locator(".tab-frame.active")
        await frame.locator(".page-wrapper").first.wait_for(timeout=15000)
        check("the viewer shows its pages", await frame.locator(".page-wrapper").count() >= 1)
        # annotations made in the viewer belong to this PDF and come back after a reload of the tab
        await frame.locator(".page-wrapper .textLayer span").first.wait_for(timeout=15000)
        made = await (await (await A.query_selector(".tab-frame.active")).content_frame()).evaluate(
            """async () => {
              const m = await import('/js/viewer/annotations/model.js');
              const a = await m.create({ type: 'highlight', page: 1, rects: [[0.1, 0.1, 0.3, 0.02]], text: 'x', color: '#ffff00' });
              return a.id;
            }"""
        )
        listed = (await (await A.request.get(BASE + "api/note-pdfs/1/annotations")).json())["annotations"]
        check("a highlight made in the viewer is saved on the notes PDF", [a["id"] for a in listed] == [made])
        await A.reload()
        await A.wait_for_timeout(1500)
        await A.wait_for_selector(".tab-frame.active")
        again = await (await A.query_selector(".tab-frame.active")).content_frame()
        await again.wait_for_function("() => window.zcAnnotations && window.zcAnnotations.ready")
        await again.evaluate("() => window.zcAnnotations.ready")
        check("the highlight is drawn again after a reload", await again.locator(".anno-layer [data-id]").count() >= 1)
        await A.click(".tab.home")
        await A.wait_for_timeout(300)
        if not await A.is_visible("#notes-view"):
            await A.click('[data-act="open-notes"]')
        await A.wait_for_timeout(300)

        # 5. rename keeps the number
        await A.click('.nt-row.pdf[data-path="Reading/My Paper.pdf"]', button="right")
        await A.click('.menu :text("Rename…")')
        await A.fill(".dialog input", "Renamed")
        await A.keyboard.press("Enter")
        await A.wait_for_selector('.nt-row.pdf[data-path="Reading/Renamed.pdf"]')
        check(
            "Rename… renames the PDF on disk",
            (D / "Reading" / "Renamed.pdf").is_file() and not (D / "Reading" / "My Paper.pdf").exists(),
        )

        check(
            "the open viewer tab takes the new name",
            "Renamed" in " ".join(await A.locator("#tabbar .tab .tab-title").all_text_contents()),
        )

        # 6. drag it to another folder
        await A.request.post(BASE + "api/notes/folder", data={"path": "Archive"})
        await A.click('[data-act="tree-menu"]')
        await A.click('.menu :text("Rescan notes folder")')
        await A.wait_for_selector('.nt-row.folder[data-folder="Archive"]')
        await A.locator('.nt-row.pdf[data-path="Reading/Renamed.pdf"]').drag_to(
            A.locator('.nt-row.folder[data-folder="Archive"]')
        )
        await A.wait_for_selector('.nt-row.pdf[data-path="Archive/Renamed.pdf"]')
        check("dragging a PDF onto a folder moves it", (D / "Archive" / "Renamed.pdf").is_file())

        # 7. Move to Trash moves it to the trash, Undo brings it back
        await A.click('.nt-row.pdf[data-path="Archive/Renamed.pdf"]', button="right")
        await A.click('.menu :text("Move to Trash")')
        if await A.locator(".dialog").count():
            await A.click('.dialog button:has-text("Move to Trash")')
        await A.wait_for_selector('.nt-row.pdf[data-path="Archive/Renamed.pdf"]', state="detached")
        check(
            "Move to Trash takes the PDF out of the tree and into the trash",
            not (D / "Archive" / "Renamed.pdf").exists() and any(D.glob(".trash/*/Archive/Renamed.pdf")),
        )
        await A.click('.toast button:has-text("Undo")')
        await A.wait_for_selector('.nt-row.pdf[data-path="Archive/Renamed.pdf"]')
        check("Undo restores it", (D / "Archive" / "Renamed.pdf").is_file())

        # 8. links from a note: rendered with the page, opening the viewer there (and at the quoted words)
        text = "# Reader\n\n> Highlighted words from the paper [[Renamed.pdf#p=2]]\n\nAlso [[Archive/Renamed.pdf#p=1|first page]] and [[nothere.pdf]].\n"
        await A.request.post(BASE + "api/notes/note", data={"title": "Reader", "text": text})
        await A.click('[data-act="tree-menu"]')
        await A.click('.menu :text("Rescan notes folder")')
        await A.wait_for_selector('.nt-row.note[data-path="Reader.md"]')
        await A.click('.nt-row.note[data-path="Reader.md"]')
        await A.wait_for_selector(".cm-content")
        await A.wait_for_timeout(500)
        check(
            "an unresolved PDF link is not offered as a note to create",
            await A.locator("[data-create-note]").count() == 0 and "no such PDF" in await A.inner_text(".notes-side"),
        )
        await A.click('.note-modes [data-mode="read"]')
        await A.wait_for_selector(".note-read a.wikilink.pdf")
        links = await A.locator(".note-read a.wikilink.pdf").all_text_contents()
        check(
            "a note shows its PDF links with their pages",
            "Renamed.pdf · p. 2" in links and "first page" in links,
            links,
        )
        check("a link to a missing PDF is marked", await A.locator(".note-read a.wikilink.pdf.unresolved").count() == 1)
        await A.locator(".note-read a.wikilink.pdf", has_text="p. 2").click()
        await A.wait_for_selector(".tab-frame.active")
        await A.wait_for_timeout(800)
        # the tab for this PDF is reused: the viewer is pointed at the spot (its own address follows)
        src = await (await (await A.query_selector(".tab-frame.active")).content_frame()).evaluate("location.href")
        check(
            "clicking it opens the viewer at that page, on the quoted words",
            "page=2" in src and "q=Highlighted" in src,
            src,
        )
        # Quote to note: selected words go into the open note with a link to the page
        vf = await (await A.query_selector(".tab-frame.active")).content_frame()
        await vf.wait_for_function(
            "() => window.zcViewerBridge && document.querySelectorAll('.textLayer span').length > 5"
        )
        await vf.evaluate(
            """() => {
              const span = document.querySelector('.page-wrapper[data-page-number="1"] .textLayer span');
              const r = document.createRange(); r.selectNodeContents(span);
              const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
              document.getElementById('pdf-container').dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
            }"""
        )
        await vf.wait_for_selector(".zc-pill [data-q]")
        await vf.evaluate("() => document.querySelector('.zc-pill [data-q]').click()")
        await vf.wait_for_selector(".zc-toast")
        check("Quote to note adds to the open note", "Added to" in await vf.inner_text(".zc-toast"))
        await A.click(".tab.home")
        await A.wait_for_timeout(300)
        if not await A.is_visible("#notes-view"):
            await A.click('[data-act="open-notes"]')
        await A.wait_for_timeout(500)
        body = await A.evaluate("document.querySelector('.cm-content, .note-read')?.innerText || ''")
        check(
            "…with a link to the PDF's page", "Renamed.pdf#p=1]]" in body or "Renamed.pdf · p. 1" in body, body[-200:]
        )

        # 9. a reload keeps them
        await A.reload()
        await A.wait_for_timeout(1500)
        check("PDFs are listed after a reload", await A.locator(".nt-row.pdf").count() == 2)
        print("CONSOLE ERRORS:", [e for e in errs if "Failed to load resource" not in e])
        await b.close()
    srv.terminate()
    shutil.rmtree(DATA, ignore_errors=True)
    print(f"\n{sum(r for _, r in results)}/{len(results)} passed")
    sys.exit(0 if all(r for _, r in results) else 1)


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _guard import guard  # noqa: E402

guard(PORT)
asyncio.run(main())
