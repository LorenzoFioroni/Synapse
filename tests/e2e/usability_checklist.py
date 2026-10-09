"""Browser checklist for moving around notes and the library: selecting several notes, keyboard
delete with Undo, deleting/renaming/moving folders, Back/Forward between notes and the library,
the Library/Notes switch, and the note markers in the item list.

    python tests/e2e/usability_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8769 with a throw-away data folder; your library is not touched.
"""
import asyncio, os, subprocess, sys, tempfile, time
from pathlib import Path
import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
PORT = 8769
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-usability-e2e-")
NOTES = Path(DATA) / "notes"
SHOTS = os.environ.get("SHOTS")
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


async def wait_for(fn, timeout=6):
    end = time.time() + timeout
    while time.time() < end:
        r = fn()
        if asyncio.iscoroutine(r):
            r = await r
        if r:
            return True
        await asyncio.sleep(0.15)
    return False


async def count_is(page, selector, n):
    return await page.locator(selector).count() == n


async def crumbs_have(page, text, timeout=6):
    """Wait until the open note's breadcrumb contains ``text``."""
    async def ok():
        return text in ((await page.text_content('.note-crumbs')) or "")
    return await wait_for(ok, timeout)


def on_disk(rel):
    return (NOTES / rel).exists()


def setup():
    a = requests.post(f"{BASE}/api/items", json={"authors": "Cui, Jian", "title": "Variational operators", "year": 2015,
                                                 "doi": "10.1103/PhysRevLett.114.220601"}).json()
    b = requests.post(f"{BASE}/api/items", json={"authors": "Verstraete, Frank", "title": "Matrix product states", "year": 2008,
                                                 "doi": "10.1080/14789940801912366"}).json()
    # a reference list as if fetched online: a cites b (in the library) and a work that is not
    sys.path.insert(0, str(APP))
    from backend.references import CitationStore
    CitationStore(Path(DATA) / "citations.db").save(a["id"], "semanticscholar", [
        {"doi": "10.1080/14789940801912366", "title": "Matrix product states", "year": 2008},
        {"doi": "10.1000/elsewhere", "title": "A paper you do not have", "authors": "Doe, Jane", "year": 1999}],
        doi="10.1103/physrevlett.114.220601")
    requests.post(f"{BASE}/api/notes/note", json={"item_id": a["id"]})
    for title, folder in [("Ideas", "proj"), ("Plan", "proj/sub"), ("Reading list", ""), ("Scratch", ""), ("Todo", "")]:
        requests.post(f"{BASE}/api/notes/note", json={"title": title, "folder": folder,
                                                      "text": f"# {title}\n\nAbout [@{b['citation_key']}].\n"})
    return a, b


async def main():
    env = dict(os.environ, ZCLONE_DATA_DIR=DATA)
    srv = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--no-browser"], cwd=APP, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    try:
        a, b = setup()
        async with async_playwright() as p:
            br = await p.chromium.launch()
            page = await (await br.new_context(viewport={"width": 1440, "height": 900})).new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(BASE + "/")
            await page.wait_for_timeout(900)
            row = lambda path: f'.nt-row.note[data-path="{path}"]'          # noqa: E731
            frow = lambda path: f'.nt-row.folder[data-folder="{path}"]'     # noqa: E731

            # ---- item pane: references (citation graph)
            await page.click(f'.row[data-id="{a["id"]}"] .cell[data-col="title"]')
            await wait_for(lambda: count_is(page, '.ip-ref', 2))
            status = await page.text_content('.ip-refs-status')
            check("item pane lists the references", "2 references" in status and "1 in your library" in status, " ".join(status.split()))
            check("a reference not in the library can be added", await page.locator('.ip-ref [data-add-ident="10.1000/elsewhere"]').count() == 1)
            await page.click('.ip-ref.in-lib')
            await wait_for(lambda: page.evaluate(f"!!document.querySelector('.row.selected[data-id=\"{b['id']}\"]')"))
            check("clicking a reference in the library selects it", await page.locator(f'.row.selected[data-id="{b["id"]}"]').count() == 1)
            await wait_for(lambda: count_is(page, '.ip-ref.in-lib', 1))
            check("…whose pane shows who cites it", "Cited by in your library" in (await page.text_content('.ip-refs')))
            await shot(page, "u0-references")

            # ---- item list: note markers
            await wait_for(lambda: page.locator(f'.row[data-id="{a["id"]}"] .note-dot.paper').count())
            check("item with a paper note shows the note marker", await page.locator(f'.row[data-id="{a["id"]}"] .note-dot.paper').count() == 1)
            check("item cited in notes shows a (lighter) marker", await page.locator(f'.row[data-id="{b["id"]}"] .note-dot:not(.paper)').count() == 1)
            await shot(page, "u1-library")
            await page.click(f'.row[data-id="{a["id"]}"] .note-dot')
            check("clicking the marker opens the paper note", await crumbs_have(page, "cuiVariational2015"))
            check("paper note offers Show in library / PDF", await page.locator('[data-act="paper-item"]').count() == 1)

            # ---- Back/Forward between notes and library
            await page.go_back()
            await wait_for(lambda: page.is_hidden('#notes-view'))
            check("Back returns to the library", await page.is_hidden('#notes-view'))
            await page.go_forward()
            check("Forward returns to the note", await crumbs_have(page, "cuiVariational2015") and await page.is_visible('#notes-view'))
            await page.click('[data-act="paper-item"]')
            await wait_for(lambda: page.is_hidden('#notes-view'))
            sel = await page.eval_on_selector_all('.row.selected', 'rs => rs.map(r => r.dataset.id)')
            check("Show in library selects the item", sel == [a["id"]], sel)
            await page.go_back()
            check("…and Back goes back to the note", await wait_for(lambda: page.is_visible('#notes-view')) and await crumbs_have(page, "cuiVariational2015"))

            # ---- workspace switch + shortcut
            await page.click('.ws-switch [data-ws="library"]')
            await wait_for(lambda: page.is_hidden('#notes-view'))
            check("toolbar switch goes to the library", await page.is_hidden('#notes-view')
                  and 'active' in (await page.get_attribute('.ws-switch [data-ws="library"]', 'class')))
            await page.keyboard.press('ControlOrMeta+Shift+L')
            check("⌘⇧L switches back to the same note", await wait_for(lambda: page.is_visible('#notes-view'))
                  and await crumbs_have(page, "cuiVariational2015"))

            # ---- library context menu
            await page.keyboard.press('ControlOrMeta+Shift+L')
            await wait_for(lambda: page.is_hidden('#notes-view'))
            await page.click(f'.row[data-id="{b["id"]}"]', button="right")
            labels = await page.eval_on_selector_all('.menu .menu-item', 'xs => xs.map(x => x.textContent.trim())')
            check("item menu offers the notes citing it", any("Open Notes Citing This" in x for x in labels))
            await page.keyboard.press('Escape')
            await page.click('.ws-switch [data-ws="notes"]')
            await wait_for(lambda: page.is_visible('#notes-view'))

            # ---- keyboard: select + Delete + Undo
            await page.click(row("Scratch.md"))
            await page.wait_for_timeout(300)
            focused = await page.evaluate("document.activeElement?.classList.contains('notes-tree')")
            check("clicking a note keeps the keyboard in the list", focused)
            await page.keyboard.press('Delete')
            await wait_for(lambda: count_is(page, row("Scratch.md"), 0))
            check("Delete moves the note to the trash", not on_disk("Scratch.md") and await page.locator(row("Scratch.md")).count() == 0)
            check("…and the open note is closed", "Scratch" not in (await page.text_content('.note-crumbs')))
            await page.click('.toast button:has-text("Undo")')
            await wait_for(lambda: count_is(page, row("Scratch.md"), 1))
            check("Undo brings it back", on_disk("Scratch.md"))

            # ---- multi-select: ctrl-click and shift-arrows, then Backspace
            await page.click(row("Reading list.md"))
            await page.click(row("Todo.md"), modifiers=["ControlOrMeta"])
            n = await page.locator('.nt-row.selected').count()
            check("⌘-click adds to the selection", n == 2, n)
            await page.keyboard.press('Backspace')
            await wait_for(lambda: count_is(page, row("Todo.md"), 0))
            check("Backspace trashes all selected notes", not on_disk("Reading list.md") and not on_disk("Todo.md") and on_disk("Scratch.md"))
            await page.click('.toast button:has-text("Undo")')
            await wait_for(lambda: count_is(page, row("Todo.md"), 1))
            check("one Undo restores them all", on_disk("Reading list.md") and on_disk("Todo.md"))
            await page.click(row("Reading list.md"))
            await page.keyboard.press('Shift+ArrowDown')
            await page.keyboard.press('Shift+ArrowDown')
            sel = await page.eval_on_selector_all('.nt-row.selected', 'rs => rs.map(r => r.dataset.path)')
            check("Shift+arrows extend the selection", sel == ["Reading list.md", "Scratch.md", "Todo.md"], sel)
            await page.keyboard.press('ArrowUp')
            sel = await page.eval_on_selector_all('.nt-row.selected', 'rs => rs.map(r => r.dataset.path)')
            check("an arrow without Shift selects one row", sel == ["Scratch.md"], sel)
            await page.keyboard.press('Enter')
            check("Enter opens it", await crumbs_have(page, "Scratch"))

            # ---- drag a selection into a folder
            await page.click(row("Scratch.md"))
            await page.click(row("Todo.md"), modifiers=["Shift"])
            await page.drag_and_drop(row("Todo.md"), frow("proj"))
            await wait_for(lambda: count_is(page, row("proj/Todo.md"), 1))
            check("dragging the selection moves every selected note", on_disk("proj/Scratch.md") and on_disk("proj/Todo.md"))
            check("…and the open note follows", await crumbs_have(page, "proj"))

            # ---- folders: rename with F2 (open note follows), move via menu, delete with Undo
            await page.click(frow("proj"))      # collapses
            await page.keyboard.press('F2')
            await page.fill('.dialog input', 'project')
            await page.keyboard.press('Enter')
            await wait_for(lambda: count_is(page, frow("project"), 1))
            check("F2 renames a folder", on_disk("project/Ideas.md") and on_disk("project/sub/Plan.md") and not on_disk("proj"))
            check("…the open note follows the folder", await crumbs_have(page, "project"))
            await page.keyboard.press('ArrowRight')
            await wait_for(lambda: count_is(page, frow("project/sub"), 1))
            await page.click(frow("project/sub"), button="right")
            await page.click('.menu >> text=Move to folder…')
            await page.click('.menu .menu-item:has-text("Top level")')
            await wait_for(lambda: count_is(page, frow("sub"), 1))
            check("a folder moves to the top level from its menu", on_disk("sub/Plan.md") and not on_disk("project/sub"))
            await page.click(frow("project"), button="right")
            await page.click('.menu >> text=Move to Trash')
            await page.wait_for_selector('.dialog')
            text = await page.text_content('.dialog')
            check("deleting a folder with notes asks first", "3 notes inside" in text, text[:120])
            await page.click('.dialog button:has-text("Move to Trash")')
            await wait_for(lambda: count_is(page, frow("project"), 0))
            check("the folder and its notes go to the trash", not on_disk("project") and await page.locator(row("project/Ideas.md")).count() == 0)
            check("…closing the open note that was inside", "Scratch" not in (await page.text_content('.note-crumbs')))
            await shot(page, "u2-folder-deleted")
            await page.click('.toast button:has-text("Undo")')
            await wait_for(lambda: count_is(page, frow("project"), 1))
            check("Undo restores the folder with its notes", on_disk("project/Ideas.md") and on_disk("project/Scratch.md"))

            # ---- empty folder can be deleted without a question
            await page.click('#notes-view [data-act="new-folder"]')
            await page.fill('.dialog input', 'Empty')
            await page.keyboard.press('Enter')
            empty = '.nt-row.folder[data-folder$="Empty"]'
            await wait_for(lambda: count_is(page, empty, 1))
            where = await page.get_attribute(empty, "data-folder")
            check("a new folder goes into the folder of the open note", where == "project/Empty", where)
            await page.click(empty)
            await page.keyboard.press('Delete')
            await wait_for(lambda: count_is(page, empty, 0))
            check("an empty folder is deleted straight away", not on_disk(where) and await page.locator('.dialog').count() == 0)

            # ---- select all, then Escape
            await page.click(row("Reading list.md"))
            await crumbs_have(page, "Reading list")
            await page.keyboard.press('ControlOrMeta+a')
            n_all = await page.locator('.nt-row.selected').count()
            check("⌘A selects every row", n_all == await page.locator('.nt-row').count() and n_all > 3, n_all)
            await page.keyboard.press('Escape')
            n_sel = await page.locator('.nt-row.selected').count()
            check("Escape goes back to just the open note", n_sel == 1, n_sel)
            await shot(page, "u3-notes")

            check("no console errors", not errors, errors[:3])
            await br.close()
    finally:
        srv.terminate()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _guard import guard  # noqa: E402
guard(PORT)
asyncio.run(main())
