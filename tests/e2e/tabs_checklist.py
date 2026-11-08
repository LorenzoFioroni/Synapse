"""Browser checklist for the app's own tabs and the server that stops with its window: PDFs open
in tabs beside the Synapse tab (one per PDF, reused and pointed at a new spot), the tab keys work
from inside a PDF too, closing goes back where the PDF was opened from, tabs can be reopened,
reordered and come back after a reload at the page they were left on, Back/Forward inside a PDF
stay inside it, and Settings can send PDFs to browser tabs instead. Then a server started with
--quit-when-closed stops once its window is closed. A foreign origin cannot embed the app,
while its own PDF frames still work.

    python tests/e2e/tabs_checklist.py        (SHOTS=/some/dir to save screenshots)

Starts its own servers on ports 8796 and 8797 with throw-away data folders; your library is not
touched.
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

PORT, QUIT_PORT = 8796, 8797
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-apptabs-e2e-")
PDF = APP / "tests" / "e2e" / "fixtures" / "twocolumn.pdf"
SHOTS = os.environ.get("SHOTS")
results = []
TAB_MOD = "Control" if sys.platform == "darwin" else "Alt"


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


async def wait_for(fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if await fn():
            return True
        await asyncio.sleep(0.2)
    return False


def start(port, data, *extra):
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(port), "--no-browser", "--data-dir", data, *extra],
        cwd=APP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(150):
        try:
            requests.get(f"http://127.0.0.1:{port}/api/settings", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    return srv


def add_item(title):
    it = requests.post(f"{BASE}/api/items", json={"title": title, "year": 2020}).json()
    with open(PDF, "rb") as fh:
        att = requests.post(
            f"{BASE}/api/items/{it['id']}/attachments", files={"file": ("p.pdf", fh, "application/pdf")}
        ).json()["attachments"][0]
    return it, att


async def tabs(page):
    return await page.evaluate("window.zcTabs.list()")


async def active(page):
    return next(t for t in await tabs(page) if t["active"])


async def active_frame(page):
    handle = await page.query_selector(".tab-frame.active")
    return await handle.content_frame() if handle else None


async def open_pdf(page, item, att, page_no=None, new_tab=False):
    await page.evaluate(
        "([it, att, p, nt]) => import('/js/api.js').then(({ openAttachment }) => openAttachment(it, att, p, {}, { newTab: nt }))",
        [{"id": item["id"], "title": item["title"]}, {"id": att["id"], "url": att["url"]}, page_no, new_tab],
    )


async def viewer_ready(frame, timeout=15000):
    await frame.wait_for_function(
        "() => (window.zcLinks && window.zcViewer && document.querySelector('.page-wrapper canvas'))", timeout=timeout
    )


async def main():
    guard(PORT)
    guard(QUIT_PORT)
    srv = start(PORT, DATA)
    try:
        first, att1 = add_item("Superdiffusive energy transport in kinetically constrained models")
        second, att2 = add_item("Second paper")
        third, att3 = add_item("Third paper")
        folder = requests.post(f"{BASE}/api/folders", json={"name": "Window folder"}).json()
        requests.post(f"{BASE}/api/items/folders", json={"ids": [second["id"]], "add": [folder["id"]]})
        note = requests.post(f"{BASE}/api/notes/note", json={"title": "Window note"}).json()["path"]
        requests.post(f"{BASE}/api/tasks", json={"title": "Window task"}).raise_for_status()
        for title in ("Alpha", "Beta", "Gamma", "Delta"):
            requests.post(f"{BASE}/api/notes/note", json={"title": title}).raise_for_status()
        requests.post(f"{BASE}/api/notes/note", json={"title": "In blue", "folder": "Colored"}).raise_for_status()
        requests.post(f"{BASE}/api/notes/note", json={"title": "Deeper", "folder": "Colored/Deep"}).raise_for_status()

        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(viewport={"width": 1300, "height": 850})
            # Different loopback hostnames are foreign origins without relying on an outside site.
            probe = await ctx.new_page()
            await probe.goto(BASE)
            async with probe.expect_console_message(predicate=lambda m: "frame-ancestors" in m.text) as violation:
                await probe.set_content(f'<iframe src="http://localhost:{PORT}/"></iframe>')
            check(
                "a foreign site cannot frame Synapse",
                not await probe.frames[1].evaluate("Boolean(window.zcTabs)"),
                (await violation.value).text,
            )
            await probe.close()
            page = await ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(BASE)
            await page.wait_for_selector(".row[data-id]")

            check("no tab bar while no PDF is open", await page.locator("#tabbar").is_hidden())
            ping = await page.evaluate(
                "fetch('/api/window/ping', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{\"client\":\"x\"}'}).then((r) => r.json())"
            )
            check("a server started normally does not watch its windows", ping == {"watch": False}, ping)

            # ---- double-click opens the PDF in a tab ------------------------------------------------
            await page.locator(".row", has_text="Superdiffusive").first.dblclick()
            await page.wait_for_selector("#tabbar:not([hidden]) .tab:not(.home)")
            fr = await active_frame(page)
            await viewer_ready(fr)
            t = await tabs(page)
            check(
                "double-click opens the PDF in a tab beside the Synapse tab, and shows it",
                len(t) == 2 and t[1]["active"] and t[0]["title"] == "Library",
                [(x["title"], x["active"]) for x in t],
            )
            check("no browser tab or window was opened", len(ctx.pages) == 1)
            check(
                "the window is named after the PDF",
                (await page.title()).startswith("Superdiffusive"),
                await page.title(),
            )
            inert = await page.evaluate(
                "[...document.querySelectorAll('#toolbar, #app, #statusbar')].every((e) => e.inert)"
            )
            check("the library behind the PDF takes no keys or clicks", inert)
            focus_in_pdf = await page.evaluate("document.activeElement?.classList.contains('tab-frame')")
            check("the PDF has the keyboard", focus_in_pdf)
            await shot(page, "01-pdf-tab")

            # ---- back to the library: the selection keeps the keyboard -------------------------------
            await page.click(".tab.home")
            back_home = await page.evaluate(
                "!document.querySelector('#tab-views').hidden ? 'pdf' : document.activeElement?.closest('#items-pane') ? 'list' : document.activeElement?.tagName"
            )
            check("the Synapse tab shows the library again, keyboard in the list", back_home == "list", back_home)
            check("the window has its name back", await page.title() == "Library — Synapse", await page.title())

            # ---- the same PDF again: its tab, pointed at the page ------------------------------------
            await page.locator(".row", has_text="Superdiffusive").first.dblclick()
            await page.wait_for_timeout(300)
            check("opening the same PDF again shows its tab, not a new one", len(await tabs(page)) == 2)
            await page.click(".tab.home")
            await open_pdf(page, first, att1, 3)
            fr = await active_frame(page)
            ok = await wait_for(lambda: fr.evaluate("(window.zcLinks.currentPosition() || {}).page === 3"))
            check("a citation of page 3 scrolls the open tab there", ok and len(await tabs(page)) == 2)
            await open_pdf(page, first, att1, None, True)
            t = await tabs(page)
            check("Cmd/Ctrl+click opens a second tab of the same PDF", len(t) == 3 and t[2]["active"])
            await viewer_ready(await active_frame(page))

            # ---- tab keys, from inside the PDF -------------------------------------------------------
            await page.keyboard.press("Control+Tab")
            a = await active(page)
            check("Ctrl+Tab inside a PDF goes to the next tab (round to the Synapse tab)", a["id"] == "home", a["id"])
            await page.keyboard.press("Control+Shift+Tab")
            a = await active(page)
            check("Ctrl+Shift+Tab goes back", a["id"] == (await tabs(page))[2]["id"], a["id"])
            await page.keyboard.press(f"{TAB_MOD}+w")
            t = await tabs(page)
            check(
                "Alt+W (⌃W on a Mac) closes the tab; the tab to its left is shown (it was left since it opened)",
                len(t) == 2 and t[1]["active"],
                [(x["title"], x["active"]) for x in t],
            )
            await page.keyboard.press(f"{TAB_MOD}+Shift+t")
            t = await tabs(page)
            check("Alt+Shift+T reopens it", len(t) == 3 and t[2]["active"])
            await viewer_ready(await active_frame(page))
            await page.keyboard.press(f"{TAB_MOD}+1")
            check("Alt+1 shows the Synapse tab", (await active(page))["id"] == "home")
            await page.keyboard.press(f"{TAB_MOD}+9")
            check("Alt+9 shows the last tab", (await active(page))["id"] == (await tabs(page))[2]["id"])

            # ---- closing with the mouse ---------------------------------------------------------------
            await page.locator(".tab.active .tab-close").click()
            check("the × closes the tab", len(await tabs(page)) == 2)
            await page.click(".tab.home")
            await open_pdf(page, third, att3)
            await page.wait_for_timeout(300)
            await page.locator(".tab.active .tab-close").click()
            check(
                "closing a tab right after opening it goes back to where it was opened (the library)",
                (await active(page))["id"] == "home" and len(await tabs(page)) == 2,
            )
            await open_pdf(page, second, att2)
            await open_pdf(page, third, att3)
            await page.wait_for_timeout(300)
            titles = [x["title"] for x in await tabs(page)]
            check(
                "each PDF gets its own tab, in the order opened", titles[2:] == ["Second paper", "Third paper"], titles
            )
            await page.locator(".tab", has_text="Second paper").click(button="middle")
            titles = [x["title"] for x in await tabs(page)]
            check("a middle click closes a tab", "Second paper" not in titles, titles)
            await open_pdf(page, second, att2)
            await page.wait_for_timeout(300)

            # ---- reordering --------------------------------------------------------------------------------
            await page.locator(".tab", has_text="Second paper").drag_to(page.locator(".tab", has_text="Superdiffusive"))
            titles = [x["title"] for x in await tabs(page)]
            check("dragging a tab moves it", titles.index("Second paper") < titles.index("Third paper"), titles)
            await shot(page, "02-three-tabs")

            # ---- Back/Forward inside a PDF stay inside it -----------------------------------------------
            await page.locator(".tab", has_text="Superdiffusive").click()
            fr = await active_frame(page)
            await viewer_ready(fr)
            await fr.evaluate("document.getElementById('pdf-container').scrollTop = 0")
            await page.wait_for_timeout(400)
            app_len = await page.evaluate("history.length")
            link = fr.locator('.page-wrapper[data-page-number="1"] .zc-link').last  # a reference, on the last page
            await link.click()
            await page.wait_for_timeout(500)
            after = await fr.evaluate("window.zcLinks.currentPosition().page")
            check(
                "following a link in a PDF tab adds nothing to the app's history",
                await page.evaluate("history.length") == app_len and after > 1,
                (app_len, after),
            )
            back_enabled = await fr.evaluate("!document.getElementById('zc-back').disabled")
            await fr.click("#zc-back")
            await page.wait_for_timeout(500)
            pos = await fr.evaluate("window.zcLinks.currentPosition().page")
            check("the viewer's Back returns to where the link was", back_enabled and pos == 1, pos)
            await fr.locator("#pdf-container").press("Alt+ArrowRight")
            await page.wait_for_timeout(500)
            pos2 = await fr.evaluate("window.zcLinks.currentPosition().page")
            check(
                "Alt+→ goes forward again, still inside the PDF",
                pos2 == after and (await active(page))["title"].startswith("Superdiffusive"),
                pos2,
            )

            # ---- a reload brings the tabs back -----------------------------------------------------------
            await open_pdf(page, first, att1, 2)
            await wait_for(lambda: fr.evaluate("(window.zcLinks.currentPosition() || {}).page === 2"))
            await page.wait_for_timeout(600)
            await page.reload()
            await page.wait_for_selector(".row[data-id]")
            await page.wait_for_selector("#tabbar:not([hidden])")
            t = await tabs(page)
            fr = await active_frame(page)
            await viewer_ready(fr)
            ok = await wait_for(lambda: fr.evaluate("(window.zcLinks.currentPosition() || {}).page === 2"))
            check(
                "after a reload the tabs are back, the active one at the page it was on",
                [x["title"] for x in t]
                == [
                    "Library",
                    "Second paper",
                    "Superdiffusive energy transport in kinetically constrained models",
                    "Third paper",
                ]
                and t[2]["active"]
                and ok,
                [(x["title"], x["active"]) for x in t],
            )
            check("the other tabs load only when shown", not t[1]["loaded"] and not t[3]["loaded"])

            # ---- the menu of a tab ---------------------------------------------------------------------
            await page.locator(".tab", has_text="Second paper").click(button="right")
            await page.locator(".menu-item", has_text="Show in Library").click()
            await page.wait_for_timeout(500)
            sel = await page.evaluate("[...document.querySelectorAll('.row.selected')].map((r) => r.textContent)")
            check(
                "Show in Library shows the Synapse tab with the item selected",
                (await active(page))["id"] == "home" and any("Second paper" in s for s in sel),
                sel,
            )
            await page.locator(".tab", has_text="Third paper").click(button="right")
            await page.locator(".menu-item", has_text="Close Other Tabs").click()
            titles = [x["title"] for x in await tabs(page)]
            check("Close Other Tabs leaves that one", titles == ["Library", "Third paper"], titles)

            # ---- the first tab: Library or Tasks; Notes has tabs of its own ------------------------------
            await page.click(".tab.home")
            await page.click('#sidebar [data-navigation="tasks.main"]')
            await page.wait_for_timeout(500)
            check("the first tab says Tasks while Tasks is shown", (await tabs(page))[0]["title"] == "Tasks")
            await page.click('#sidebar [data-act="open-notes"]')
            await page.wait_for_timeout(500)
            t = await tabs(page)
            check(
                "Notes with no note tab opens an empty Notes tab; the first tab still says Tasks",
                t[0]["title"] == "Tasks" and t[-1]["kind"] == "note" and t[-1]["key"] == "" and t[-1]["active"],
                [(x["title"], x["kind"], x["active"]) for x in t],
            )
            await page.click(".tab.home")
            await page.wait_for_timeout(500)
            check("the first tab goes back to Tasks", await page.evaluate("document.body.dataset.workspace") == "tasks")
            await page.click('#sidebar [data-view="library"]')
            await page.wait_for_timeout(300)
            check(
                "the empty Notes tab goes once Notes is left",
                [x["title"] for x in await tabs(page)] == ["Library", "Third paper"],
                [x["title"] for x in await tabs(page)],
            )

            # ---- each window has its own tabs ------------------------------------------------------------
            other = await ctx.new_page()
            other.on("pageerror", lambda e: errors.append(f"other window: {e}"))
            await other.goto(BASE)
            await other.wait_for_selector(".row[data-id]")
            check(
                "another window starts with only the Synapse tab",
                len(await tabs(other)) == 1 and await other.locator("#tabbar").is_hidden(),
            )
            await open_pdf(other, second, att2)
            await other.wait_for_timeout(500)
            await other.reload()
            await other.wait_for_selector("#tabbar:not([hidden])")
            t1 = [x["title"] for x in await tabs(page)]
            t2 = [x["title"] for x in await tabs(other)]
            check(
                "each window keeps its own tabs, also through a reload",
                t1 == ["Library", "Third paper"] and t2 == ["Library", "Second paper"],
                (t1, t2),
            )
            await other.close()

            async def new_window(click):
                async with ctx.expect_page() as pop:
                    await click()
                w = await pop.value
                w.on("pageerror", lambda e: errors.append(f"new window: {e}"))
                await w.wait_for_load_state()
                await w.wait_for_selector("#toolbar .brand-logo")
                return w

            # ---- Move to New Window ---------------------------------------------------------------------
            await page.locator(".tab", has_text="Third paper").click()
            await viewer_ready(await active_frame(page))
            await open_pdf(page, third, att3, 2)
            fr = await active_frame(page)
            await wait_for(lambda: fr.evaluate("(window.zcLinks.currentPosition() || {}).page === 2"))
            await page.locator(".tab", has_text="Third paper").click(button="right")
            win = await new_window(lambda: page.locator(".menu-item", has_text="Move to New Window").click())
            await win.wait_for_selector("#tabbar:not([hidden]) .tab:not(.home)")
            wf = await active_frame(win)
            await viewer_ready(wf)
            ok = await wait_for(lambda: wf.evaluate("(window.zcLinks.currentPosition() || {}).page === 2"))
            wt = await tabs(win)
            check(
                "Move to New Window: the PDF opens in a new window, at its page, beside a Synapse tab",
                [x["title"] for x in wt] == ["Library", "Third paper"] and wt[1]["active"] and ok,
                [(x["title"], x["active"]) for x in wt],
            )
            check("… and leaves this window", [x["title"] for x in await tabs(page)] == ["Library"])
            check("the new window's address keeps no '#…'", "#" not in win.url, win.url)
            await shot(win, "03-moved-to-new-window")
            await win.close()

            # ---- New Window from the Synapse tab ---------------------------------------------------------
            await open_pdf(page, first, att1)
            await page.wait_for_selector("#tabbar:not([hidden])")
            await page.locator(".tab.home").click(button="right")
            win = await new_window(lambda: page.locator(".menu-item", has_text="New Window").click())
            await win.wait_for_selector(".row[data-id]")
            check(
                "New Window opens the library alone",
                len(await tabs(win)) == 1 and await win.locator("#tabbar").is_hidden(),
            )
            await win.close()
            await page.click(".tab.home")

            # ---- Open in New Window from the menus ------------------------------------------------------
            await page.locator(".row", has_text="Second paper").first.click(button="right")
            win = await new_window(lambda: page.locator(".menu-item", has_text="Show in New Window").click())
            ok = await wait_for(
                lambda: win.evaluate(
                    "[...document.querySelectorAll('.row.selected')].some((r) => r.textContent.includes('Second paper'))"
                )
            )
            check("an item: Show in New Window selects it in the new window's library", ok)
            await win.close()

            await page.locator(".row", has_text="Second paper").first.click(button="right")
            win = await new_window(lambda: page.locator(".menu-item", has_text="Open PDF in New Window").click())
            await win.wait_for_selector("#tabbar:not([hidden])")
            wt = await tabs(win)
            check(
                "an item: Open PDF in New Window opens its PDF in a tab of a new window",
                [x["title"] for x in wt] == ["Library", "Second paper"] and wt[1]["active"],
                [x["title"] for x in wt],
            )
            await win.close()

            await page.locator(f'.side-item[data-folder="{folder["id"]}"]').click(button="right")
            win = await new_window(lambda: page.locator(".menu-item", has_text="Open in New Window").click())
            ok = await wait_for(lambda: win.locator(f'.side-item.active[data-folder="{folder["id"]}"]').count())
            await wait_for(
                lambda: win.evaluate("document.querySelectorAll('.row[data-id]').length === 1")
            )  # its items loaded
            rows = await win.eval_on_selector_all(".row[data-id]", "rs => rs.map((r) => r.textContent)")
            check(
                "a folder: Open in New Window shows that folder",
                ok and len(rows) == 1 and "Second paper" in rows[0],
                rows,
            )
            await win.close()

            await page.click('#sidebar [data-act="open-notes"]')
            await page.wait_for_selector(f'.nt-row.note[data-path="{note}"]')
            await page.locator(f'.nt-row.note[data-path="{note}"]').click(button="right")
            win = await new_window(lambda: page.locator(".menu-item", has_text="Open in New Window").click())
            ok = await wait_for(lambda: win.evaluate(f"window.zcNotes?.path() === {note!r}"))
            check("a note: Open in New Window opens it in a new window", ok)
            await win.close()

            await page.click('#sidebar [data-navigation="tasks.main"]')
            await page.wait_for_selector(".tv-row")
            await page.locator(".tv-row", has_text="Window task").click(button="right")
            win = await new_window(lambda: page.locator(".menu-item", has_text="Open in New Window").click())
            ok = await wait_for(
                lambda: win.evaluate(
                    "document.body.dataset.workspace === 'tasks' && !!document.querySelector('.tv-row.selected')?.textContent.includes('Window task')"
                )
            )
            check("a task: Open in New Window opens the Tasks page with it selected", ok)
            await win.close()
            await page.click('#sidebar [data-view="library"]')
            await page.wait_for_timeout(300)

            # ---- notes in tabs, VS Code's way ------------------------------------------------------------
            async def note_tabs(pg=page):
                return [(x["title"], x["preview"], x["active"]) for x in await tabs(pg) if x["kind"] == "note"]

            async def note_path(path, pg=page):
                return await wait_for(lambda: pg.evaluate(f"window.zcNotes?.path() === {path!r}"))

            await page.click('#sidebar [data-act="open-notes"]')
            await page.wait_for_selector('.nt-row.note[data-path="Alpha.md"]')
            await page.click('.nt-row.note[data-path="Alpha.md"]')
            await note_path("Alpha.md")
            italic = await page.locator(".tab.note.preview .tab-title").evaluate("(e) => getComputedStyle(e).fontStyle")
            check(
                "a note opens in a preview tab, its title in italics",
                await note_tabs() == [("Alpha", True, True)] and italic == "italic",
                (await note_tabs(), italic),
            )
            await page.click('.nt-row.note[data-path="Beta.md"]')
            await note_path("Beta.md")
            check(
                "the next note opened replaces the preview",
                await note_tabs() == [("Beta", True, True)],
                await note_tabs(),
            )
            await page.locator(".tab.note.preview").dblclick()
            check("double-clicking the tab keeps it", await note_tabs() == [("Beta", False, True)], await note_tabs())
            await page.click('.nt-row.note[data-path="Gamma.md"]')
            await note_path("Gamma.md")
            check(
                "then the next note opens in a new preview tab beside it",
                await note_tabs() == [("Beta", False, False), ("Gamma", True, True)],
                await note_tabs(),
            )
            await page.dblclick('.nt-row.note[data-path="Gamma.md"]')
            await page.wait_for_timeout(300)
            check(
                "double-clicking a note in the list keeps its tab (no rename dialog)",
                await note_tabs() == [("Beta", False, False), ("Gamma", False, True)]
                and not await page.locator(".dialog").count(),
                await note_tabs(),
            )
            await page.click('.nt-row.note[data-path="Delta.md"]')
            await note_path("Delta.md")
            await page.click(".cm-content")
            await page.keyboard.press("Meta+ArrowDown" if sys.platform == "darwin" else "Control+End")
            await page.keyboard.type(" typed in Delta")
            await page.wait_for_timeout(300)
            check(
                "editing a preview note keeps its tab", ("Delta", False, True) in await note_tabs(), await note_tabs()
            )

            # each tab keeps its note's undo history
            await page.locator(".tab.note", has_text="Beta").click()
            await note_path("Beta.md")
            await page.click(".cm-content")
            await page.keyboard.press("Meta+ArrowDown" if sys.platform == "darwin" else "Control+End")
            await page.keyboard.type(" undo me")
            typed = "undo me" in await page.evaluate("window.zcNotes.text()")
            await page.locator(".tab.note", has_text="Gamma").click()
            await note_path("Gamma.md")
            await page.locator(".tab.note", has_text="Beta").click()
            await note_path("Beta.md")
            await page.wait_for_timeout(300)
            await page.keyboard.press("ControlOrMeta+z")
            await page.wait_for_timeout(300)
            check(
                "undo still works after switching tabs and back",
                typed and "undo me" not in await page.evaluate("window.zcNotes.text()"),
            )

            # each tab has its own mode
            await page.locator(".tab.note", has_text="Gamma").click()
            await note_path("Gamma.md")
            await page.click('.note-modes [data-mode="read"]')
            await page.wait_for_selector(".note-read:not([hidden]) .prose *")
            await page.locator(".tab.note", has_text="Beta").click()
            await note_path("Beta.md")
            beta_mode = await page.evaluate("document.querySelector('.note-modes .active')?.dataset.mode")
            await page.locator(".tab.note", has_text="Gamma").click()
            await note_path("Gamma.md")
            gamma_mode = await page.evaluate("document.querySelector('.note-modes .active')?.dataset.mode")
            reading = await page.evaluate("!document.querySelector('.note-read').hidden")
            check(
                "each note tab keeps its own mode (Read in one does not change the others)",
                beta_mode == "live" and gamma_mode == "read" and reading,
                (beta_mode, gamma_mode),
            )

            # going back to an open tab waits for nothing: the server's answers are slowed down here
            async def slow(route):
                await asyncio.sleep(2)
                try:
                    await route.continue_()
                except Exception:
                    pass  # unrouted meanwhile

            await page.route("**/api/notes/note?*", slow)
            t0 = time.time()
            await page.locator(".tab.note", has_text="Beta").click()
            ok = await note_path("Beta.md")
            took = time.time() - t0
            await page.wait_for_timeout(2300)  # the check for changes made elsewhere, in the background
            await page.unroute("**/api/notes/note?*")
            check(
                "switching to an open note tab shows it at once (nothing fetched first)",
                ok and took < 1.0 and await page.evaluate("document.querySelectorAll('.cm-editor').length") == 1,
                f"{took:.2f} s",
            )

            # the first tab and back to Notes
            await page.click(".tab.home")
            await page.wait_for_timeout(400)
            check("the first tab shows the library", await page.evaluate("document.body.dataset.workspace") is None)
            await page.click('#sidebar [data-act="open-notes"]')
            await note_path("Beta.md")
            check(
                "Notes goes back to the note tab used last",
                ("Beta", False, True) in await note_tabs(),
                await note_tabs(),
            )

            # tabs follow renamed and deleted notes
            await page.click('.nt-row.note[data-path="Gamma.md"]')
            await note_path("Gamma.md")
            await page.keyboard.press("F2")
            await page.fill(".dialog input", "Gamma two")
            await page.keyboard.press("Enter")
            await note_path("Gamma two.md")
            check(
                "renaming a note renames its tab",
                [t[0] for t in await note_tabs()] == ["Beta", "Gamma two", "Delta"],
                await note_tabs(),
            )
            await page.locator(".tab.note", has_text="Beta").click()
            await note_path("Beta.md")
            await page.locator('.nt-row.note[data-path="Delta.md"]').click(button="right")
            await page.locator(".menu-item", has_text="Move to Trash").click()
            ok = await wait_for(lambda: page.evaluate("!window.zcTabs.list().some((t) => t.title === 'Delta')"))
            check(
                "deleting a note closes its tab",
                ok and await note_tabs() == [("Beta", False, True), ("Gamma two", False, False)],
                await note_tabs(),
            )

            # a reload brings them back, the note shown again
            await page.reload()
            await page.wait_for_selector("#tabbar:not([hidden])")
            ok = await note_path("Beta.md")
            check(
                "a reload brings the note tabs back, the active note shown",
                ok and await note_tabs() == [("Beta", False, True), ("Gamma two", False, False)],
                await note_tabs(),
            )

            # dragging a tab to another window moves it there
            other = await ctx.new_page()
            other.on("pageerror", lambda e: errors.append(f"other window: {e}"))
            await other.goto(BASE)
            await other.wait_for_selector(".row[data-id]")
            src = await page.evaluate("window.zcTabs.win")
            beta = next(x for x in await tabs(page) if x["title"] == "Beta")
            await other.evaluate(
                """(d) => {
                    const dt = new DataTransfer();
                    dt.setData('application/x-synapse-tab', JSON.stringify(d));
                    document.querySelector('#items-pane').dispatchEvent(
                        new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true, clientX: 400, clientY: 300 }));
                }""",
                {"win": src, "id": beta["id"], "spec": {"kind": "note", "key": "Beta.md", "title": "Beta"}},
            )
            ok = await note_path("Beta.md", other)
            gone = await wait_for(lambda: page.evaluate("!window.zcTabs.list().some((t) => t.title === 'Beta')"))
            check(
                "a tab dropped on another window opens there and leaves this one",
                ok and await note_tabs(other) == [("Beta", False, True)] and gone,
                (await note_tabs(other), await note_tabs()),
            )
            await shot(other, "04-note-tab-dragged-in")
            await other.close()

            # dropped outside every window: a new window
            box = await page.locator(".tab.note", has_text="Gamma two").bounding_box()
            await page.mouse.move(box["x"] + 20, box["y"] + 10)
            await page.mouse.down()  # the press that starts the drag lets the page open a window
            win = await new_window(
                lambda: page.evaluate(
                    """() => {
                        const el = [...document.querySelectorAll('.tab.note')].find((t) => t.textContent.includes('Gamma two'));
                        const dt = new DataTransfer();
                        el.dispatchEvent(new DragEvent('dragstart', { dataTransfer: dt, bubbles: true }));
                        el.dispatchEvent(new DragEvent('dragend', { dataTransfer: dt, bubbles: true, clientX: -40, clientY: 10, screenX: 60, screenY: 60 }));
                    }"""
                )
            )
            await page.mouse.up()
            ok = await note_path("Gamma two.md", win)
            gone = await wait_for(
                lambda: page.evaluate("!window.zcTabs.list().some((t) => t.kind === 'note' && t.key)")
            )
            check(
                "a tab dropped outside every window moves to a new window",
                ok and await note_tabs(win) == [("Gamma two", False, True)] and gone,
                await note_tabs(win),
            )
            await win.close()
            await page.click(".tab.home")
            await page.wait_for_timeout(400)

            # ---- a folder's colour on its notes' tabs and on the title bar ------------------------------
            async def bar_color(pg=page):
                return await pg.evaluate(
                    "[...document.querySelectorAll('meta[name=theme-color]')].map((m) => m.content)"
                )

            neutral = await page.evaluate(
                "getComputedStyle(document.documentElement).getPropertyValue('--bg-sidebar').trim()"
            )
            check(
                "the title bar is neutral by default (the tab bar's colour)",
                await bar_color() == [neutral],
                (await bar_color(), neutral),
            )
            await page.click('#sidebar [data-act="open-notes"]')
            await page.wait_for_selector('.nt-row.folder[data-folder="Colored"]')
            await page.locator('.nt-row.folder[data-folder="Colored"]').click(button="right")
            await page.locator(".menu-item", has_text="Blue").click()
            ok = await wait_for(
                lambda: page.evaluate(
                    "getComputedStyle(document.querySelector('.nt-row.folder[data-folder=\"Colored\"] .nt-folder-icon .icon')).color === 'rgb(0, 144, 255)'"
                )
            )
            check("a folder's colour is chosen from its menu and shown on its icon", ok)
            await page.click('.nt-row.note[data-path="Colored/In blue.md"]')
            await note_path("Colored/In blue.md")
            tab_icon = await page.evaluate("getComputedStyle(document.querySelector('.tab.note.active .icon')).color")
            check(
                "a note in a blue folder: the title bar and its tab's icon are blue",
                await bar_color() == ["#0090ff"] and tab_icon == "rgb(0, 144, 255)",
                (await bar_color(), tab_icon),
            )
            await page.dblclick('.nt-row.note[data-path="Colored/Deep/Deeper.md"]')
            await note_path("Colored/Deep/Deeper.md")
            check(
                "a note in a subfolder takes the colour from the folder above",
                await bar_color() == ["#0090ff"],
                await bar_color(),
            )
            await page.click('.nt-row.note[data-path="Alpha.md"]')
            await note_path("Alpha.md")
            check("a note in no coloured folder: neutral again", await bar_color() == [neutral], await bar_color())
            await page.locator(".tab.note", has_text="Deeper").click()
            await note_path("Colored/Deep/Deeper.md")
            await page.click(".tab.home")
            await page.wait_for_timeout(300)
            check("the first tab: neutral", await bar_color() == [neutral], await bar_color())
            await page.evaluate("document.documentElement.dataset.theme = 'dark'")
            await page.wait_for_timeout(200)
            dark = await page.evaluate(
                "getComputedStyle(document.documentElement).getPropertyValue('--bg-sidebar').trim()"
            )
            check(
                "neutral follows the theme (dark)",
                await bar_color() == [dark] and dark != neutral,
                (await bar_color(), dark),
            )
            await page.evaluate("delete document.documentElement.dataset.theme")
            await page.click('#sidebar [data-act="open-notes"]')
            await page.locator('.nt-row.folder[data-folder="Colored"]').click(button="right")
            await page.locator(".menu-item", has_text="No Colour").click()
            ok = await wait_for(
                lambda: page.evaluate(f"document.querySelector('meta[name=theme-color]').content === {neutral!r}")
            )
            check("No Colour takes it back (the note shown is neutral again)", ok, await bar_color())
            await page.click(".tab.home")
            await page.wait_for_timeout(300)

            # ---- Settings → PDF Viewer → Browser Tabs ---------------------------------------------------
            await page.keyboard.press("Meta+," if sys.platform == "darwin" else "Control+,")
            await page.click('.settings-nav [data-nav="viewer"]')
            await page.wait_for_selector('[data-seg="pdf_open_in"]')
            check("Settings → PDF Viewer has “Open PDFs in”", True)
            await page.click('[data-seg="pdf_open_in"] [data-v="browser"]')
            await page.wait_for_timeout(500)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
            before = len(await tabs(page))
            async with ctx.expect_page() as popup:
                await page.locator(".row", has_text="Second paper").first.dblclick()
            pdf_page = await popup.value
            await pdf_page.wait_for_load_state()
            check(
                "with Browser Tabs a PDF opens in a browser tab, not in a Synapse tab",
                "/viewer.html" in pdf_page.url and len(await tabs(page)) == before,
            )
            await pdf_page.close()
            requests.put(f"{BASE}/api/settings", json={"pdf_open_in": "tabs"})

            check("no JavaScript errors", not errors, errors[:5])
            await b.close()
    finally:
        srv.terminate()
        srv.wait()

    # ---- a server started with --quit-when-closed stops with its window ------------------------------
    data2 = tempfile.mkdtemp(prefix="zc-quit-e2e-")
    srv2 = start(QUIT_PORT, data2, "--quit-when-closed")
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch()
            page = await b.new_page()
            await page.goto(f"http://127.0.0.1:{QUIT_PORT}/")
            await page.wait_for_selector("#toolbar .brand-logo")
            await page.wait_for_function("() => (document.readyState === 'complete')")
            check("the server runs while its window is open", srv2.poll() is None)
            # Playwright closes a headless target without dispatching pagehide. Navigating out
            # exercises the browser's real farewell, then closes the now-empty target. Keep the
            # browser alive until its keepalive request has reached the server.
            await page.goto("about:blank")
            await page.close()
            t0 = time.monotonic()
            while srv2.poll() is None and time.monotonic() - t0 < 30:
                await asyncio.sleep(0.2)
            check(
                "leaving the last window stops a --quit-when-closed server (within ~20 s)",
                srv2.poll() == 0,
                f"{time.monotonic() - t0:.0f} s",
            )
            await b.close()
    finally:
        if srv2.poll() is None:
            srv2.terminate()
            srv2.wait()

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
