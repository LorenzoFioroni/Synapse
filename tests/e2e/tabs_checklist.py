"""Browser checklist for the app's own tabs and the server that stops with its window: PDFs open
in tabs beside the Synapse tab (one per PDF, reused and pointed at a new spot), the tab keys work
from inside a PDF too, closing goes back where the PDF was opened from, tabs can be reopened,
reordered and come back after a reload at the page they were left on, Back/Forward inside a PDF
stay inside it, and Settings can send PDFs to browser tabs instead. Then a server started with
--quit-when-closed stops once its window is closed.

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
        "window.zcLinks && window.zcViewer && document.querySelector('.page-wrapper canvas')", timeout=timeout
    )


async def main():
    guard(PORT)
    guard(QUIT_PORT)
    srv = start(PORT, DATA)
    try:
        first, att1 = add_item("Superdiffusive energy transport in kinetically constrained models")
        second, att2 = add_item("Second paper")
        third, att3 = add_item("Third paper")

        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(viewport={"width": 1300, "height": 850})
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
            await page.keyboard.press("Alt+w")
            t = await tabs(page)
            check(
                "Alt+W (⌃W on a Mac) closes the tab; the tab to its left is shown (it was left since it opened)",
                len(t) == 2 and t[1]["active"],
                [(x["title"], x["active"]) for x in t],
            )
            await page.keyboard.press("Alt+Shift+t")
            t = await tabs(page)
            check("Alt+Shift+T reopens it", len(t) == 3 and t[2]["active"])
            await viewer_ready(await active_frame(page))
            await page.keyboard.press("Alt+1")
            check("Alt+1 shows the Synapse tab", (await active(page))["id"] == "home")
            await page.keyboard.press("Alt+9")
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

            # ---- the Synapse tab is named after the workspace -------------------------------------------
            await page.click(".tab.home")
            await page.click('.ws-switch [data-ws="notes"]')
            await page.wait_for_timeout(500)
            check("the Synapse tab says Notes while Notes is open", (await tabs(page))[0]["title"] == "Notes")
            await page.click('.ws-switch [data-ws="library"]')
            await page.wait_for_timeout(300)

            # ---- Settings → PDF Viewer → Browser Tabs ---------------------------------------------------
            await page.keyboard.press("Control+,")
            await page.wait_for_selector('[data-seg="pdf_open_in"]')
            check("Settings → PDF Viewer has “Open PDFs in”", True)
            await page.click('[data-seg="pdf_open_in"] [data-v="browser"]')
            await page.wait_for_timeout(500)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
            async with ctx.expect_page() as popup:
                await page.locator(".row", has_text="Second paper").first.dblclick()
            pdf_page = await popup.value
            await pdf_page.wait_for_load_state()
            check(
                "with Browser Tabs a PDF opens in a browser tab, not in a Synapse tab",
                "/viewer.html" in pdf_page.url and len(await tabs(page)) == 2,
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
            await page.wait_for_selector("#toolbar .ws-switch")
            await page.wait_for_timeout(1500)
            check("the server runs while its window is open", srv2.poll() is None)
            await page.close()
            await b.close()
        t0 = time.time()
        while srv2.poll() is None and time.time() - t0 < 40:
            time.sleep(0.5)
        check(
            "closing the window stops a --quit-when-closed server (within ~20 s)",
            srv2.poll() == 0,
            f"{time.time() - t0:.0f} s",
        )
    finally:
        if srv2.poll() is None:
            srv2.terminate()
            srv2.wait()

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
