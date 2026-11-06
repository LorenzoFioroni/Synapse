"""Browser checklist for the phone layout (frontend/src/phone/): a phone gets its own shell with a
tab bar (Library, Notes, "+", one pinned plugin destination and Plugins), papers as rows and pages,
menus as sheets, notes that open for reading and edit with a bar above the keyboard, a plugin's
list of papers as cards and rows grouped as the plugin says (added or dismissed with buttons or a
swipe), a plugin's page drawn for phones, the "+" sheet with what plugins add, Tasks on a phone, a
plugin turned off leaving the tab bar, and switching to the desktop layout and back.

    python tests/e2e/phone_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8824 with a throw-away data folder; your library is not touched.
Runs Chromium with the Pixel 5 profile (touch, no hover), and a narrow mouse window for contrast.
"""

import asyncio
import json
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

PORT = 8824
BASE = f"http://127.0.0.1:{PORT}"
DATA = Path(tempfile.mkdtemp(prefix="zc-phone-e2e-"))
PDF = APP / "tests" / "e2e" / "fixtures" / "twocolumn.pdf"
SHOTS = os.environ.get("SHOTS")
results = []

PLUGIN = """
from backend.plugins import Plugin

def activate(plugin: Plugin):
    store = plugin.storage.json("feed")

    def days():
        # today (with the badges of its papers, as arXiv Today sends them) and a day before it
        from datetime import date, timedelta
        today = date.today()
        marks = store.get("marks", [])
        return [{"id": today.isoformat(), "title": "Today", "badge": len(store.get("papers", [])) or None, "highlights": marks},
                {"id": (today - timedelta(days=1)).isoformat(), "title": "Yesterday", "highlights": store.get("old_marks", [])}]

    @plugin.item_source("feed", title="Phone Feed", icon="today", description="Papers for the phone",
                        badge=lambda: len(store.get("papers", [])) or None, variants=days)
    def feed(day=None):
        from datetime import date
        return store.get("papers", []) if day in (None, date.today().isoformat()) else []

    @plugin.route("/set", methods=["POST"])
    def set_feed():
        from flask import request
        store.update(request.get_json())
        plugin.refresh_sources()
        return {}

    @plugin.route("/dismiss", methods=["POST"])
    def dismiss():
        from flask import request
        ids = set(request.get_json()["ids"])
        store.set("papers", [p for p in store.get("papers", []) if f"x:arxiv:{p['arxiv']}" not in ids])
        plugin.refresh_sources()
        return {}
"""

FRONTEND = """
export function activate(p) {
  p.itemActions.register({
    id: 'dismiss', title: 'Dismiss From the Feed', outside: true, role: 'dismiss',
    when: (items) => items.every((i) => i._outside),
    run: async (items) => { await p.backend.post('/dismiss', { ids: items.map((i) => i.id) }); await p.sources.refresh(); },
  });
  p.capture.register({ id: 'ping', title: 'Ping', icon: 'star', run: () => { window.__pinged = (window.__pinged || 0) + 1; } });
  p.editor.toolbar.register({ id: 'stamp', title: 'Stamp', label: '§', run: (view) => {
    const at = view.state.selection.main.head;
    view.dispatch({ changes: { from: at, insert: '§' }, selection: { anchor: at + 1 } });
  } });
  p.pages.register({ id: 'board', title: 'Board', icon: 'list', phone: (el, nav) => {
    el.innerHTML = '<p class="demo-phone">Hello phone</p><button class="demo-push">More</button>';
    el.querySelector('.demo-push').addEventListener('click', () => nav.push({ title: 'Inner', mount: (box) => {
      box.innerHTML = '<p class="demo-inner">Inner screen</p>';
    } }));
  } });
  p.pages.register({ id: 'wide', title: 'Wide Only', icon: 'columns', mount: (el) => { el.textContent = 'desktop'; } });
}
"""

PAPERS = [
    {
        "arxiv": "2611.00001",
        "title": "Read me first on Lindblad dynamics",
        "authors": ["Ann Lee", "Bo Chen"],
        "year": 2026,
        "group": "Read",
        "highlights": [{"label": "Cites you", "text": "Cites a paper of yours", "tone": "red", "icon": "megaphone"}],
        "reasons": [{"kind": "match", "text": "Match 91 · Read", "items": []}, "Cites 3 library papers"],
    },
    {
        "arxiv": "2611.00002",
        "title": "Second card about steady states",
        "authors": ["Cara Diaz"],
        "year": 2026,
        "group": "Read",
        "reasons": [{"kind": "match", "text": "Match 77 · Read", "items": []}, "Shares 9 rare references"],
    },
    {
        "arxiv": "2611.00003",
        "title": "Skim row one",
        "authors": ["Dan Ewe"],
        "year": 2026,
        "group": "Skim",
        "reasons": ["Topic"],
    },
    {
        "arxiv": "2611.00004",
        "title": "Skim row two",
        "authors": ["Eve Fox"],
        "year": 2026,
        "group": "Skim",
        "highlights": [{"label": "Following", "text": "By Eve Fox, whom you follow", "tone": "gold", "icon": "star"}],
    },
]


MARKS = [
    {"label": "Cites you", "text": "A paper today cites yours", "tone": "red", "icon": "megaphone"},
    {"label": "Following", "text": "A paper today is by someone you follow", "tone": "gold", "icon": "star"},
]


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
        try:
            if await fn():
                return True
        except Exception:
            pass
        await asyncio.sleep(0.15)
    return False


def api(path, method="GET", body=None):
    r = requests.request(method, BASE + path, json=body, timeout=30)
    return r.json() if r.content else {}


def setup():
    api("/api/settings", "PUT", {"add_find_pdf": "never", "add_fetch_references": "never", "references_auto": False})
    api("/api/plugin-host/metadata-sources/disable", "POST")
    cui = api(
        "/api/items",
        "POST",
        {
            "title": "Variational matrix product operators",
            "authors": "Cui, Jian; Cirac, J. Ignacio",
            "year": 2015,
            "journal": "Physical Review Letters",
        },
    )
    with open(PDF, "rb") as fh:
        requests.post(f"{BASE}/api/items/{cui['id']}/attachments", files={"file": ("paper.pdf", fh, "application/pdf")})
    for k in range(3):
        api("/api/items", "POST", {"title": f"Another paper {k}", "authors": "Doe, Jane", "year": 2020 + k})
    api("/api/folders", "POST", {"name": "Manuscript"})
    key = cui["citation_key"]
    note = api(
        "/api/notes/note",
        "POST",
        {"title": "Steady state", "text": f"# Steady state\n\nWe follow [@{key}, p. 2].\n\n- [ ] check it\n"},
    )
    api("/api/plugin-host/tasks/enable", "POST")
    api("/api/plugin-host/phone-demo/enable", "POST")
    api("/api/plugins/phone-demo/set", "POST", {"papers": PAPERS, "marks": MARKS, "old_marks": [MARKS[1]]})
    return cui, note["path"], key


async def main():
    guard(PORT)
    folder = DATA / "plugins" / "phone-demo"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(
        json.dumps(
            {
                "id": "phone-demo",
                "name": "Phone Demo",
                "version": "0.1",
                "api": "^1.11",
                "backend": "p.py",
                "frontend": "main.js",
                "permissions": {"library": ["read"]},
            }
        )
    )
    (folder / "p.py").write_text(PLUGIN)
    (folder / "main.js").write_text(FRONTEND)
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", str(DATA)],
        cwd=APP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(150):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        cui, note_path, key = setup()
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None)
            errors = []

            # ---- a narrow window with a mouse keeps the desktop layout ----------------------
            page = await browser.new_page(viewport={"width": 390, "height": 800})
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(BASE)
            await page.wait_for_selector(".row[data-id]", timeout=15000)
            check("a narrow mouse window keeps the desktop layout", not await page.locator("#phone").count())
            await page.close()

            # ---- the same groups on the desktop: headings in the list, folded with a click -------
            page = await browser.new_page(viewport={"width": 1300, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(BASE)
            await page.wait_for_selector(".row[data-id]", timeout=15000)
            await page.locator('#sidebar [data-view="source:phone-demo.feed"]').click()
            await page.wait_for_selector(".items-body .row.group-head")

            async def desk():
                return await page.evaluate("""() => [...document.querySelectorAll('.items-body .row.group-head, .items-body .row[data-id]')]
                    .map(r => r.classList.contains('group-head') ? '# ' + r.textContent.trim() : r.querySelector('.c-title .t').textContent)""")

            rows = await desk()
            check(
                "desktop: a plugin's groups head its list",
                rows[0] == "# Read2" and rows[3] == "# Skim2" and len(rows) == 6,
                rows,
            )
            await shot(page, "00-desktop-groups")
            await page.locator('.row.group-head[data-group="Read"]').click()
            ok = await wait_for(
                lambda: page.evaluate("document.querySelectorAll('.items-body .row[data-id]').length === 2")
            )
            check("… a click on a heading folds its group", ok, await desk())
            await page.reload()
            await page.wait_for_selector(".row[data-id]", timeout=15000)
            await page.locator('#sidebar [data-view="source:phone-demo.feed"]').click()
            await page.wait_for_selector(".items-body .row.group-head")
            check("… and it stays folded", len(await desk()) == 4, await desk())
            await page.locator('.row.group-head[data-group="Read"]').click()
            await page.locator('.head-cell[data-col="title"]').click()
            ok = await wait_for(lambda: page.evaluate("!document.querySelector('.items-body .row.group-head')"))
            check("… sorting by a column drops the headings", ok)
            await page.close()

            # ---- a phone gets the phone layout ------------------------------------------------
            ctx = await browser.new_context(**p.devices["Pixel 5"])
            page = await ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on(
                "console", lambda m: errors.append(m.text) if m.type == "error" and "favicon" not in m.text else None
            )
            page.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 400 else None)
            await page.goto(BASE)
            await page.wait_for_selector("#phone .ph-row", timeout=15000)
            check(
                "a phone gets the phone layout",
                await page.locator("#phone").is_visible() and not await page.locator("#toolbar").is_visible(),
            )
            box = await page.evaluate("({ w: innerWidth, s: document.documentElement.scrollWidth })")
            check("… with no sideways overflow", box["s"] <= box["w"], box)
            await wait_for(lambda: page.evaluate("(window.zcPlugins?.loaded() || []).includes('phone-demo')"), 15)
            slots = await page.eval_on_selector_all(".ph-tabbar [data-slot]", "els => els.map(e => e.dataset.slot)")
            check(
                "tab bar: Library, Notes, +, a pinned list and Plugins",
                slots == ["library", "notes", "add", "pin", "plugins"],
                slots,
            )
            pin_label = await page.locator('[data-slot="pin"] .ph-tab-label').inner_text()
            check(
                "… the first plugin list is pinned, with its badge",
                pin_label == "Phone Feed" and await page.locator('[data-slot="pin"] .ph-tab-badge').inner_text() == "4",
                pin_label,
            )
            marks = await page.eval_on_selector_all(
                '[data-slot="pin"] .ph-tab-marks .source-badge', "els => els.map(e => e.getAttribute('aria-label'))"
            )
            check(
                "… and today's Cites you / Following on it, as on the desktop sidebar",
                marks == [MARKS[0]["text"], MARKS[1]["text"]],
                marks,
            )
            check(
                "… not on Plugins while the list with them is pinned",
                not await page.locator('[data-slot="plugins"] .ph-tab-marks').count(),
            )
            await shot(page, "01-library")

            # ---- the library -------------------------------------------------------------------
            meta = await page.locator(f'.ph-row[data-id="{cui["id"]}"] .ph-row-meta').inner_text()
            check(
                "a row shows authors, year and journal",
                "Cui and Cirac" in meta and "2015" in meta and "Physical Review" in meta,
                meta,
            )
            await page.locator(".ph-search input").fill("Another paper 1")
            ok = await wait_for(lambda: page.evaluate("document.querySelectorAll('.ph-list .ph-row').length === 1"))
            check("searching narrows the list", ok)
            await page.locator(".ph-search input").fill("")
            await wait_for(lambda: page.evaluate("document.querySelectorAll('.ph-list .ph-row').length === 4"))
            await page.locator(f'.ph-row[data-id="{cui["id"]}"]').tap()
            await page.wait_for_selector(".ph-item h1")
            check(
                "a tap opens the paper's page",
                "Variational" in await page.locator(".ph-item h1").inner_text()
                and await page.locator('.ph-action[data-act="read"]').count() == 1,
            )
            await shot(page, "02-item")
            await page.locator('.ph-nav [data-act="more"]').tap()
            await page.wait_for_selector(".menu.menu-sheet")
            labels = await page.eval_on_selector_all(
                ".menu-sheet .menu-item", "els => els.map(e => e.textContent.trim())"
            )
            check(
                "⋯ opens a sheet with the paper's actions",
                "Move to Trash" in labels and "Copy as BibTeX" in labels,
                labels,
            )
            await page.mouse.click(200, 60)
            await wait_for(lambda: page.evaluate("!document.querySelector('.menu-sheet')"))
            await page.locator('.ph-action[data-act="read"]').tap()
            ok = await wait_for(lambda: page.evaluate("!!document.querySelector('.ph-reader-frame')"), 5)
            check("Read opens the PDF over the screen", ok)
            await page.go_back()
            ok = await wait_for(
                lambda: page.evaluate(
                    "!document.querySelector('.ph-reader-frame') && !!document.querySelector('.ph-item h1')"
                )
            )
            check("the phone's Back leaves the PDF", ok)
            await page.locator(".ph-screen:not([hidden]) [data-ph=back]").tap()
            ok = await wait_for(lambda: page.locator(".ph-search").is_visible())
            check("the back button returns to the list", ok)

            # ---- notes -------------------------------------------------------------------------
            await page.locator('[data-slot="notes"]').tap()
            await page.wait_for_selector(".ph-note-row")
            await page.locator(f'.ph-note-row[data-note="{note_path}"]').tap()
            await page.wait_for_selector(".ph-prose .cite")
            check(
                "a note opens for reading, its citation as a chip",
                await page.locator(".ph-prose .cite").count() == 1
                and not await page.locator(".ph-prose .properties").count(),
            )
            await page.locator(".ph-prose .cite").tap()
            await page.wait_for_selector(".ph-sheet")
            check(
                "a tapped citation opens its sheet",
                "page 2" in await page.locator(".ph-sheet [data-c=pdf]").inner_text(),
            )
            await shot(page, "03-cite")
            await page.locator(".ph-sheet-close").tap()
            await wait_for(lambda: page.evaluate("!document.querySelector('.ph-sheet')"))
            await page.locator('.ph-screen:not([hidden]) [data-act="edit"]').tap()
            await page.wait_for_selector(".ph-keybar:not([hidden]) .ph-key")
            keys = await page.eval_on_selector_all(
                ".ph-keybar .ph-key", "els => els.map(e => e.getAttribute('aria-label'))"
            )
            check(
                "Edit shows the editor and the bar above the keyboard",
                "Cite a paper" in keys and "Link a note" in keys,
                keys,
            )
            check("… with the buttons plugins add", "Stamp" in keys and "Make this line a task" in keys, keys)
            check("… and the tab bar steps aside", not await page.locator(".ph-tabbar").is_visible())
            await page.locator(".ph-note-edit .cm-content").tap()
            await page.keyboard.press("Control+End")
            await page.keyboard.insert_text("\nWritten on a phone ")
            await page.locator('.ph-key[aria-label="Stamp"]').tap()
            await shot(page, "04-edit")
            ok = await wait_for(
                lambda: page.evaluate(f"""fetch('/api/notes/note?path={note_path}').then(r => r.json())
                .then(n => n.text.includes('Written on a phone §'))"""),
                10,
            )
            check("typing (and a plugin's button) is saved", ok)
            await page.locator('.ph-screen:not([hidden]) [data-act="edit"]').tap()
            ok = await wait_for(
                lambda: page.evaluate("document.querySelector('.ph-prose')?.textContent.includes('Written on a phone')")
            )
            check("Done returns to reading, showing the change", ok)

            # ---- a plugin's list -----------------------------------------------------------------
            await page.locator('[data-slot="pin"]').tap()
            await page.wait_for_selector(".ph-card")
            heads = await page.eval_on_selector_all(".ph-group-head h2", "els => els.map(e => e.textContent.trim())")
            check("the plugin's groups head the list", heads == ["Read 2", "Skim 2"], heads)
            check(
                "the first group as cards, the next as rows",
                await page.locator(".ph-card").count() == 2
                and await page.locator(".ph-source-list .ph-row-wrap").count() == 2,
            )
            row_badge = page.locator(".ph-row-wrap").filter(has_text="Skim row two").locator(".ph-badge")
            check(
                "a row of a later group shows its badges too",
                await row_badge.count() == 1 and "Following" in await row_badge.inner_text(),
            )
            chip_marks = await page.eval_on_selector_all(
                ".ph-variants .ph-chip", "els => els.map(e => e.querySelectorAll('.source-badge').length)"
            )
            check("the day chips show each day's badges", chip_marks == [2, 1], chip_marks)
            first = page.locator(".ph-card").first
            check(
                "a card shows the badge and the score",
                "Cites you" in await first.locator(".ph-badges").inner_text()
                and "Match 91" in await first.locator(".ph-score").inner_text(),
            )
            await shot(page, "05-feed")
            await first.locator('[data-act="add"]').tap()
            ok = await wait_for(
                lambda: page.evaluate(
                    "fetch('/api/items?view=library').then(r => r.json()).then(d => d.items.some(i => i.title.startsWith('Read me first')))"
                ),
                15,
            )
            check("Add puts the paper in the library", ok)
            ok = await wait_for(lambda: page.locator(".ph-card .ph-in-library").count(), 10)
            check("… and the card says so", ok)
            await page.locator('.ph-card [data-act="dismiss"]').first.tap()
            ok = await wait_for(lambda: page.evaluate("!document.body.textContent.includes('Second card about')"), 10)
            check("Not for me runs the plugin's dismiss action", ok)
            await page.wait_for_timeout(800)  # the list settles after the dismissal reloads it
            row = page.locator(".ph-row-wrap").filter(has_text="Skim row one")
            b = await row.bounding_box()
            cdp = await ctx.new_cdp_session(page)
            y = b["y"] + b["height"] / 2
            await cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": 300, "y": y}]})
            for k in range(1, 11):
                await cdp.send(
                    "Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": 300 - 25 * k, "y": y}]}
                )
                await asyncio.sleep(0.016)
            await cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            ok = await wait_for(lambda: page.evaluate("!document.body.textContent.includes('Skim row one')"), 10)
            check("swiping a row to the left dismisses it", ok)

            # ---- Plugins tab, a plugin's page, choosing the pinned tab ----------------------------
            await page.locator('[data-slot="plugins"]').tap()
            await page.wait_for_selector(".ph-dest")
            dests = await page.eval_on_selector_all(".ph-dest .ph-row-title", "els => els.map(e => e.textContent)")
            check(
                "Plugins lists every plugin list and page",
                {"Phone Feed", "Tasks", "Board", "Wide Only"} <= set(dests),
                dests,
            )
            check(
                "… a desktop-only page as needing a larger screen",
                "larger screen" in await page.locator('.ph-dest[data-dest="page:phone-demo.wide"]').inner_text(),
            )
            await shot(page, "06-plugins")
            await page.locator('.ph-dest[data-dest="page:phone-demo.board"]').tap()
            await page.wait_for_selector(".demo-phone")
            await page.locator(".demo-push").tap()
            ok = await wait_for(lambda: page.locator(".demo-inner").is_visible())
            check("a plugin's phone page draws, and pushes a screen of its own", ok)
            await page.locator('[data-slot="plugins"]').tap()
            await page.locator('[data-slot="plugins"]').tap()
            await page.locator('.ph-link-row[data-act="pin"]').tap()
            await page.locator(".menu-sheet .menu-item", has_text="Tasks").tap()
            ok = await wait_for(
                lambda: page.evaluate(
                    "document.querySelector('[data-slot=pin] .ph-tab-label')?.textContent === 'Tasks'"
                )
            )
            check("choosing another pinned tab puts it in the tab bar", ok)
            plug_marks = await page.locator('[data-slot="plugins"] .ph-tab-marks .source-badge').count()
            check(
                "… and today's badges of the list it left move to Plugins",
                plug_marks == 2 and not await page.locator('[data-slot="pin"] .ph-tab-marks').count(),
                plug_marks,
            )
            check(
                "… and to its row there",
                await page.locator('.ph-dest[data-dest="source:phone-demo.feed"] .source-badge').count() == 2,
            )

            # ---- Tasks on a phone ------------------------------------------------------------------
            await page.locator('[data-slot="pin"]').tap()
            await page.wait_for_selector(".tp-segments")
            await page.locator(".tp-add input").fill("Reply to the referee")
            await page.locator(".tp-add input").press("Enter")
            ok = await wait_for(lambda: page.locator(".tp-row", has_text="Reply to the referee").count())
            check("quick add on the Tasks page makes a task due today", ok)
            await page.locator(".tp-row", has_text="Reply to the referee").locator(".tp-check").tap()
            ok = await wait_for(
                lambda: page.evaluate(
                    "fetch('/api/tasks').then(r => r.json()).then(d => d.tasks.some(t => t.title === 'Reply to the referee' && t.status === 'done'))"
                ),
                10,
            )
            check("its round box ticks it", ok)
            await shot(page, "07-tasks")

            # ---- the + sheet -------------------------------------------------------------------------
            await page.locator('[data-slot="add"]').tap()
            await page.wait_for_selector(".ph-sheet .ph-tile")
            tiles = await page.eval_on_selector_all(".ph-sheet .ph-tile span", "els => els.map(e => e.textContent)")
            check(
                "+ offers a note, a PDF and what plugins add", {"Note", "PDF file", "Task", "Ping"} <= set(tiles), tiles
            )
            await shot(page, "08-add")
            await page.locator(".ph-tile", has_text="Ping").tap()
            ok = await wait_for(lambda: page.evaluate("window.__pinged === 1"))
            check("a plugin's entry runs", ok)

            # ---- a plugin turned off leaves -------------------------------------------------------
            api("/api/plugin-host/phone-demo/disable", "POST")
            await page.evaluate("window.zcPlugins.sync()")
            ok = await wait_for(
                lambda: page.evaluate("""!document.querySelector('[data-dest^="source:phone-demo"]')
                && document.querySelector('[data-slot=pin] .ph-tab-label')?.textContent === 'Tasks'"""),
                10,
            )
            check("a plugin turned off leaves the Plugins tab; the pin stays", ok)

            # ---- switching layouts ---------------------------------------------------------------------
            await page.evaluate("document.querySelector('[data-slot=library]').click()")
            await page.locator('.ph-screen:not([hidden]) [data-act="settings"]').first.tap()
            await page.locator(".menu-sheet .menu-item", has_text="Use the Desktop Layout").tap()
            await page.wait_for_selector("#toolbar .mobile-more", timeout=15000)
            check("Use the Desktop Layout reloads into the desktop layout", not await page.locator("#phone").count())
            await page.locator("#toolbar .mobile-more").tap()
            await page.locator(".menu .menu-item", has_text="Use the Phone Layout").tap()
            await page.wait_for_selector("#phone .ph-tabbar", timeout=15000)
            check(
                "… and the desktop's More menu goes back to the phone layout",
                await page.evaluate("localStorage.getItem('zc.layout') === null"),
            )

            check("no JavaScript errors", not errors, errors[:5])
            await ctx.close()
            await browser.close()
    finally:
        srv.terminate()
    n = sum(results)
    print(f"\n{n}/{len(results)} passed")
    sys.exit(0 if n == len(results) else 1)


asyncio.run(main())
