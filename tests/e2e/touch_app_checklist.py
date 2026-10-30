"""Browser checklist for the library on tablets and phones: the collections become a drawer on
narrow screens, a long press opens the menus a right-click opens (items, folders, notes) and the
cards a hover opens (citations, PDF link previews), hover-only tools are shown, the item pane can be
resized with a finger, phone panes become full-screen views, and the page can be added to the Home Screen.

    python tests/e2e/touch_app_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8783 with a throw-away data folder; your library is not touched.
Runs Chromium with iPad Pro 11 and Pixel 5 profiles (touch, no hover).
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from tests.e2e._guard import guard  # noqa: E402

PORT = 8783
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-touch-app-e2e-")
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


async def wait_for(fn, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if await fn():
            return True
        await asyncio.sleep(0.15)
    return False


class Finger:
    def __init__(self, cdp):
        self.cdp = cdp

    async def send(self, typ, pts):
        await self.cdp.send("Input.dispatchTouchEvent", {"type": typ, "touchPoints": pts})

    async def long_press(self, x, y, hold=0.8):
        await self.send("touchStart", [{"x": x, "y": y}])
        await asyncio.sleep(hold)
        await self.send("touchEnd", [])

    async def drag(self, a, b, steps=10):
        await self.send("touchStart", [{"x": a[0], "y": a[1]}])
        for k in range(1, steps + 1):
            await self.send(
                "touchMove", [{"x": a[0] + (b[0] - a[0]) * k / steps, "y": a[1] + (b[1] - a[1]) * k / steps}]
            )
            await asyncio.sleep(0.016)
        await self.send("touchEnd", [])


async def centre(page, sel):
    b = await page.locator(sel).first.bounding_box()
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


def setup():
    item = requests.post(
        f"{BASE}/api/items", json={"authors": "Cui, Jian", "title": "Touch paper", "year": 2015}
    ).json()
    with open(PDF, "rb") as fh:
        att = requests.post(
            f"{BASE}/api/items/{item['id']}/attachments", files={"file": ("paper.pdf", fh, "application/pdf")}
        ).json()["attachments"][0]
    for k in range(5):
        requests.post(f"{BASE}/api/items", json={"title": f"Another paper {k}", "year": 2020 + k})
    folder = requests.post(f"{BASE}/api/folders", json={"name": "Manuscript"}).json()
    key = item["citation_key"]
    requests.post(
        f"{BASE}/api/notes/note", json={"title": "Reading", "text": f"# Reading\n\nAs shown in [@{key}, p. 2].\n"}
    )
    return item, att, folder, key


async def main():
    guard(PORT)
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", DATA],
        cwd=APP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        item, att, folder, key = setup()
        requests.post(f"{BASE}/api/plugin-host/tasks/enable", timeout=10).raise_for_status()
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None)
            errors = []

            # ---- Home Screen ------------------------------------------------------------------
            r = requests.get(BASE + "/manifest.webmanifest")
            man = r.json() if r.ok else {}
            check(
                "the manifest is served as a web app manifest",
                r.ok
                and "manifest+json" in r.headers.get("Content-Type", "")
                and man.get("name") == "Synapse"
                and man.get("id") == "/"
                and man.get("display") == "standalone",
                r.headers.get("Content-Type"),
            )
            sizes = {i.get("sizes") for i in man.get("icons", [])}
            purposes = {word for i in man.get("icons", []) for word in i.get("purpose", "").split()}
            check(
                "… and declares installable Android icons",
                {"192x192", "512x512"} <= sizes and {"any", "maskable"} <= purposes,
                f"sizes {sorted(sizes)}, purposes {sorted(purposes)}",
            )
            icons = [requests.get(BASE + i["src"]).status_code for i in man.get("icons", [])] + [
                requests.get(BASE + "/assets/apple-touch-icon.png").status_code
            ]
            check("… with its icons and an Apple touch icon", icons and all(c == 200 for c in icons), icons)

            # ---- portrait: the collections are a drawer ----------------------------------------------
            ctx = await browser.new_context(**p.devices["iPad Pro 11"])
            page = await ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(BASE)
            await page.wait_for_selector(".row[data-id]")
            f = Finger(await ctx.new_cdp_session(page))
            phone_links = await page.locator("link").evaluate_all(
                "links => links.map(link => ({ rel: link.rel, sizes: link.sizes?.value || '', href: link.getAttribute('href') }))"
            )
            check(
                "the page links the manifest and phone icons",
                any(link["rel"] == "manifest" for link in phone_links)
                and any(link["rel"] == "apple-touch-icon" for link in phone_links)
                and any(
                    link["rel"] == "icon" and link["sizes"] == "192x192" and link["href"] == "/assets/icon-192.png?v=3"
                    for link in phone_links
                ),
                phone_links,
            )
            side = await page.evaluate("document.getElementById('sidebar').getBoundingClientRect().right")
            toggle = await page.locator('[data-a="sidebar"]').is_visible()
            check(
                "portrait: the collections are hidden, with a button to show them",
                side <= 1 and toggle,
                f"sidebar right {side}",
            )
            lw = await page.evaluate("document.getElementById('items-pane').getBoundingClientRect().width")
            check("… so the item list gets the room", lw > 380, f"{lw:.0f}px")
            await shot(page, "01-portrait")
            await page.tap('[data-a="sidebar"]')
            ok = await wait_for(
                lambda: page.evaluate("document.getElementById('sidebar').getBoundingClientRect().left >= -1")
            )
            check("the button slides the collections in", ok)
            await page.wait_for_timeout(300)
            await shot(page, "02-drawer")
            await page.tap('#sidebar [data-view="trash"]')
            ok = await wait_for(lambda: page.evaluate("!document.body.classList.contains('sidebar-open')"))
            view = await page.evaluate("document.querySelector('#sidebar .side-item.active')?.dataset.view")
            check("choosing a collection opens it and closes the drawer", ok and view == "trash", view)
            await page.tap('[data-a="sidebar"]')
            await page.wait_for_timeout(300)
            await page.tap('#sidebar [data-view="library"]')
            await page.wait_for_selector(".row[data-id]")

            # ---- long press = right-click -----------------------------------------------------------
            await page.wait_for_timeout(400)
            x, y = await centre(page, ".row[data-id]")
            await f.long_press(x, y)
            ok = await wait_for(lambda: page.evaluate("!!document.querySelector('.menu .menu-item')"), timeout=3)
            labels = await page.eval_on_selector_all(".menu .menu-item", "es => es.map(e => e.textContent.trim())")
            check("a long press on an item opens its menu", ok and any("Trash" in l for l in labels), labels[:4])
            hs = await page.eval_on_selector_all(
                ".menu .menu-item", "es => es.map(e => e.getBoundingClientRect().height)"
            )
            check("… with finger-sized entries", hs and min(hs) >= 44, f"{min(hs) if hs else None}px")
            await page.wait_for_timeout(500)
            check(
                "… which stays open after lifting the finger (nothing chosen by accident)",
                await page.evaluate("!!document.querySelector('.menu')")
                and len(requests.get(f"{BASE}/api/items?view=trash").json()["items"]) == 0,
            )
            await shot(page, "03-item-menu")
            await page.tap("#statusbar")
            ok = await wait_for(lambda: page.evaluate("!document.querySelector('.menu')"), timeout=3)
            check("a tap elsewhere closes the menu", ok)

            await page.tap('[data-a="sidebar"]')
            await page.wait_for_timeout(300)
            x, y = await centre(page, f'#sidebar [data-folder="{folder["id"]}"]')
            await f.long_press(x, y)
            ok = await wait_for(lambda: page.evaluate("!!document.querySelector('.menu .menu-item')"), timeout=3)
            labels = await page.eval_on_selector_all(".menu .menu-item", "es => es.map(e => e.textContent.trim())")
            check(
                "a long press on a folder opens the folder menu", ok and any("Rename" in l for l in labels), labels[:4]
            )
            check(
                "… and keeps the drawer open meanwhile",
                await page.evaluate("document.body.classList.contains('sidebar-open')"),
            )
            await page.keyboard.press("Escape")
            await page.touchscreen.tap(650, 700)  # outside the drawer: closes it
            ok = await wait_for(lambda: page.evaluate("!document.body.classList.contains('sidebar-open')"), timeout=3)
            check("a tap outside the drawer closes it", ok)
            await page.wait_for_timeout(300)

            # hover-only tools are shown
            await page.tap(".row[data-id]")
            await page.wait_for_selector("#item-pane .field-row")
            op = await page.evaluate(
                "(() => { const b = document.querySelector('#item-pane .field-row .field-tools .icon-btn'); return b ? getComputedStyle(b).opacity : null; })()"
            )
            check(
                "the item pane's hover tools are always shown on touch screens", op is None or float(op) == 1, str(op)
            )

            # ---- notes: long press on a note and on a citation ------------------------------------------
            # Notes is in the sidebar, a drawer on this screen
            await page.tap('[data-a="sidebar"]')
            await page.wait_for_timeout(300)
            await page.tap('#sidebar [data-act="open-notes"]')
            await page.wait_for_selector('.nt-row.note[data-path="Reading.md"]')
            await wait_for(lambda: page.evaluate("!document.body.classList.contains('sidebar-open')"))
            await page.wait_for_timeout(400)  # the drawer slides away
            x, y = await centre(page, '.nt-row.note[data-path="Reading.md"]')
            await f.long_press(x, y)
            ok = await wait_for(lambda: page.evaluate("!!document.querySelector('.menu .menu-item')"), timeout=3)
            labels = await page.eval_on_selector_all(".menu .menu-item", "es => es.map(e => e.textContent.trim())")
            check("a long press on a note opens the note menu", ok and any("Rename" in l for l in labels), labels[:4])
            await page.keyboard.press("Escape")
            await page.tap('.nt-row.note[data-path="Reading.md"]')
            await page.wait_for_selector(".cm-content")
            await page.tap('[data-mode="read"]')
            await page.wait_for_selector(".note-read .cite")
            x, y = await centre(page, ".note-read .cite")
            pages_before = len(ctx.pages)
            await f.long_press(x, y)
            ok = await wait_for(
                lambda: page.evaluate("!!document.querySelector('.cite-card:not([hidden]) .cc-title')"), timeout=4
            )
            check("a long press on a citation shows its card (no hover on a tablet)", ok)
            await page.wait_for_timeout(500)
            check("… without opening the PDF as a tap would", len(ctx.pages) == pages_before, f"{len(ctx.pages)} tabs")
            await shot(page, "04-cite-card")
            await page.tap(".note-read h1")
            ok = await wait_for(lambda: page.evaluate("!document.querySelector('.cite-card:not([hidden])')"), timeout=3)
            check("a tap elsewhere closes the card", ok)
            await ctx.close()

            # ---- phone: one useful pane at a time -----------------------------------------------
            ctx = await browser.new_context(**p.devices["Pixel 5"])
            page = await ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(BASE)
            await page.wait_for_selector(".row[data-id]")
            viewport = await page.evaluate("({ w: innerWidth, scroll: document.documentElement.scrollWidth })")
            check("phone: the app has no horizontal page overflow", viewport["scroll"] <= viewport["w"], viewport)
            toolbar = await page.locator("#toolbar").bounding_box()
            search = await page.locator("#toolbar .search").bounding_box()
            check(
                "phone: the toolbar wraps search onto a full-width second row",
                toolbar["height"] >= 80 and search["width"] > viewport["w"] * 0.85,
                f"toolbar {toolbar['height']:.0f}px, search {search['width']:.0f}px",
            )
            visible_heads = await page.eval_on_selector_all(
                ".items-head .head-cell",
                "es => es.filter(e => getComputedStyle(e).display !== 'none').map(e => e.dataset.col)",
            )
            list_scroll = await page.evaluate(
                "(() => { const e = document.querySelector('.items-scroll'); return { width: e.clientWidth, scroll: e.scrollWidth }; })()"
            )
            check(
                "phone: every configured list column remains available in the list scroller",
                len(visible_heads) > 1 and list_scroll["scroll"] > list_scroll["width"],
                f"{visible_heads}, {list_scroll}",
            )
            await shot(page, "05-phone-library")

            await page.locator(".row[data-id]", has_text="Touch paper").tap()
            await page.wait_for_selector("#item-pane .ip-mobile-back", state="visible")
            pane = await page.locator("#item-pane").bounding_box()
            check(
                "phone: tapping an item opens full-width details",
                pane["width"] >= viewport["w"] - 1 and await page.locator("#item-pane").is_visible(),
                f"{pane['width']:.0f}px",
            )
            await shot(page, "06-phone-details")

            await page.wait_for_selector('#item-pane [data-tool="open-pdf"]')
            await page.tap('#item-pane [data-tool="open-pdf"]')
            await page.wait_for_selector("#tab-views .tab-frame.active")
            frame = page.frame_locator("#tab-views .tab-frame.active")
            await frame.locator('.page-wrapper[data-page-number="1"]').wait_for(timeout=10000)
            pdf_layout = await page.evaluate(
                """(() => {
                  const frame = document.querySelector('#tab-views .tab-frame.active');
                  const hit = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
                  return { width: frame.getBoundingClientRect().width, viewport: innerWidth,
                           viewerOnTop: hit === frame };
                })()"""
            )
            check(
                "phone: opening a PDF fully covers item details",
                pdf_layout["viewerOnTop"] and pdf_layout["width"] >= pdf_layout["viewport"] - 1,
                pdf_layout,
            )
            viewer_layout = await frame.locator("body").evaluate(
                """() => {
                  const c = document.getElementById('pdf-container');
                  const p = document.querySelector('.page-wrapper');
                  return { page: p.getBoundingClientRect().width, container: c.clientWidth,
                           overflow: c.scrollWidth - c.clientWidth,
                           shellOverflow: document.documentElement.scrollWidth - innerWidth };
                }"""
            )
            check(
                "phone: the PDF initially fits the viewer width without a blank right region",
                viewer_layout["page"] >= viewer_layout["container"] - 18
                and viewer_layout["overflow"] <= 1
                and viewer_layout["shellOverflow"] <= 0,
                viewer_layout,
            )
            await shot(page, "07-phone-pdf")
            await frame.locator("#zoom-in").click()
            pan_layout = await frame.locator("body").evaluate(
                """() => {
                  const c = document.getElementById('pdf-container');
                  c.scrollLeft = c.scrollWidth;
                  window.scrollTo(9999, 0);
                  return { pdfLeft: c.scrollLeft, shellLeft: scrollX,
                           shellOverflow: document.documentElement.scrollWidth - innerWidth };
                }"""
            )
            check(
                "phone: horizontal PDF panning stays inside the viewer instead of exposing a black strip",
                pan_layout["pdfLeft"] > 0 and pan_layout["shellLeft"] == 0 and pan_layout["shellOverflow"] <= 0,
                pan_layout,
            )
            await page.tap("#tabbar .tab.home")
            await page.wait_for_selector("#item-pane .ip-mobile-back", state="visible")
            await page.tap("#item-pane .ip-mobile-back")
            check("phone: Back returns to the library", not await page.locator("#item-pane").is_visible())

            await page.tap('[data-a="more"]')
            labels = await page.eval_on_selector_all(".menu .menu-item", "es => es.map(e => e.textContent.trim())")
            check(
                "phone: less frequent actions remain available in More",
                any("Import" in label for label in labels) and any("Settings" in label for label in labels),
                labels,
            )
            await page.get_by_role("menuitem", name="Settings").click()
            await page.wait_for_selector("#settings-view")
            settings = await page.evaluate(
                "({ page: document.getElementById('settings-view').scrollWidth, viewport: innerWidth, nav: getComputedStyle(document.querySelector('.settings-nav')).display, control: getComputedStyle(document.querySelector('.setting')).flexDirection })"
            )
            check(
                "phone: Settings uses a horizontal section strip and stacked controls",
                settings["page"] <= settings["viewport"]
                and settings["nav"] == "flex"
                and settings["control"] == "column",
                settings,
            )
            await page.wait_for_timeout(200)
            await shot(page, "08-phone-settings")

            await page.tap("#settings-view [data-close]")
            await page.locator(".row[data-id]", has_text="Touch paper").tap()
            await page.wait_for_selector("#item-pane .ip-mobile-back", state="visible")
            await page.tap('[data-a="sidebar"]')
            await page.tap('#sidebar [data-act="open-notes"]')
            await page.wait_for_selector("#notes-view:not([hidden])")
            await wait_for(
                lambda: page.evaluate("document.getElementById('sidebar').getBoundingClientRect().right <= 1")
            )
            check("phone: leaving Library closes item details", not await page.locator("#item-pane").is_visible())
            notes_tree = await page.locator(".notes-tree-pane").bounding_box()
            check("phone: Notes starts with a full-width note list", notes_tree["width"] >= viewport["w"] - 1)
            await page.tap('.notes-tree-head [data-act="graph"]')
            await page.wait_for_selector(".notes-graph:not([hidden])")
            graph = await page.locator(".notes-graph").bounding_box()
            check(
                "phone: Graph replaces the notes panes at full width",
                not await page.locator(".notes-tree-pane").is_visible()
                and not await page.locator(".notes-side").is_visible()
                and graph["width"] >= viewport["w"] - 1,
                f"{graph['width']:.0f}px",
            )
            await shot(page, "09-phone-graph")
            await page.tap('.notes-graph [data-ng="close"]')
            await page.tap('.nt-row.note[data-path="Reading.md"]')
            await page.wait_for_selector(".note-mobile-list", state="visible")
            await page.wait_for_selector(".notes-tree-pane", state="hidden")
            check(
                "phone: opening a note gives the editor the full screen",
                not await page.locator(".notes-tree-pane").is_visible(),
            )
            await shot(page, "10-phone-note")
            await page.tap(".note-mobile-list")
            check(
                "phone: the note header returns to the note list", await page.locator(".notes-tree-pane").is_visible()
            )

            await page.tap('[data-a="sidebar"]')
            await page.wait_for_selector('#sidebar [data-navigation="tasks.main"]')
            await page.tap('#sidebar [data-navigation="tasks.main"]')
            await page.wait_for_selector("#tasks-view:not([hidden])")
            await page.wait_for_function("() => (!document.body.classList.contains('sidebar-open'))")
            await page.locator(".tv-quick-input").fill("Phone task")
            await page.locator(".tv-quick-input").press("Enter")
            await page.wait_for_selector(".tv-row:has-text('Phone task')")
            await page.wait_for_selector(".tv-mobile-back", state="visible")
            task_detail = await page.locator(".tv-detail").bounding_box()
            check("phone: a selected task opens full-width details", task_detail["width"] >= viewport["w"] - 1)
            await shot(page, "11-phone-task")
            await page.tap(".tv-mobile-back")
            check("phone: Back returns to the task list", not await page.locator(".tv-detail").is_visible())
            await ctx.close()

            # ---- landscape --------------------------------------------------------------------------------
            ctx = await browser.new_context(**p.devices["iPad Pro 11 landscape"])
            page = await ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(BASE)
            await page.wait_for_selector(".row[data-id]")
            f = Finger(await ctx.new_cdp_session(page))
            left = await page.evaluate("document.getElementById('sidebar').getBoundingClientRect().left")
            check(
                "landscape: the collections stay in place, no drawer button",
                left >= 0 and not await page.locator('[data-a="sidebar"]').is_visible(),
            )
            await shot(page, "12-landscape")
            w0 = await page.evaluate("document.getElementById('item-pane').offsetWidth")
            x, y = await centre(page, "#resizer-right")
            await f.drag((x, y), (x - 120, y))
            w1 = await page.evaluate("document.getElementById('item-pane').offsetWidth")
            check("the item pane can be resized with a finger", w1 > w0 + 80, f"{w0} → {w1}px")

            # ---- the viewer: long press on a link previews it --------------------------------------------------
            q = urlencode(
                {"file": f"/pdfs/{att['url']}", "paper_id": item["id"], "title": item["title"], "att": att["id"]}
            )
            v = await ctx.new_page()
            v.on("pageerror", lambda e: errors.append(str(e)))
            await v.goto(f"{BASE}/viewer.html?{q}")
            await v.wait_for_selector('.page-wrapper[data-page-number="1"] .zc-link', timeout=10000)
            await v.wait_for_timeout(500)
            fv = Finger(await ctx.new_cdp_session(v))
            top0 = await v.evaluate("window.zcViewer.container.scrollTop")
            x, y = await centre(v, '.page-wrapper[data-page-number="1"] .zc-link')
            await fv.long_press(x, y)
            ok = await wait_for(lambda: v.evaluate("!!document.querySelector('.zc-link-pop')"), timeout=5)
            await v.wait_for_timeout(400)
            top1 = await v.evaluate("window.zcViewer.container.scrollTop")
            check("viewer: a long press on a link shows its preview", ok)
            check("… without following the link", abs(top1 - top0) < 5, f"scroll {top0} → {top1}")
            await shot(v, "13-link-preview")
            await v.tap("#page-num-text")
            ok = await wait_for(lambda: v.evaluate("!document.querySelector('.zc-link-pop')"), timeout=3)
            check("… and a tap elsewhere closes it", ok)
            await v.tap('.page-wrapper[data-page-number="1"] .zc-link')
            ok = await wait_for(lambda: v.evaluate(f"window.zcViewer.container.scrollTop > {top0 + 20}"), timeout=4)
            check("a tap on a link follows it", ok)
            await ctx.close()

            check("no JavaScript errors", not errors, errors[:3])
            await browser.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
