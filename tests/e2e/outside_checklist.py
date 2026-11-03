"""Browser checklist for papers outside the library: a plugin's list (an item source) has one entry
with a badge under Plugins in the sidebar and puts its variants in a second pane. It shows like a
folder, in the plugin's order until a column is sorted (a third click goes back); papers not in the library have a "+" and a pane with
why they are listed, links to the library papers involved and buttons to add them; "+" adds a
paper (Undo takes it out again), several go into a folder at once; a list the plugin changes is
shown again by itself, and the entry goes when the plugin is turned off.

    python tests/e2e/outside_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server (python server.py) on port 8799 with a throw-away data folder; your
library is not touched. Metadata Sources is off in the fixture, so synthetic identifiers are
added as listed without depending on real papers or a network.
"""

import asyncio
import json
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
sys.path.insert(0, str(APP))
from tests.e2e._guard import guard  # noqa: E402

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
DATA = Path(tempfile.mkdtemp(prefix="zc-outside-e2e-"))
SHOTS = os.environ.get("SHOTS")
results = []
mod = "Meta" if sys.platform == "darwin" else "Control"

PLUGIN = """
from backend.plugins import Plugin

def activate(plugin: Plugin):
    store = plugin.storage.json("list")

    def unread():
        return sum(1 for p in store.get("papers", []) if not plugin.library.find(doi=p.get("doi"), arxiv=p.get("arxiv")))

    @plugin.item_source("today", title="Today's List", icon="calendar", description="Papers for today", badge=unread,
                        variants=lambda: [{"id": "today", "title": "26 Oct 2026", "highlights": store.get("highlights", [])},
                                          {"id": "yesterday", "title": "25 Oct 2026"}])
    def today(day=None):
        return store.get("papers", [])

    @plugin.route("/set", methods=["POST"])
    def set_list():
        from flask import request
        store.update(request.get_json())
        plugin.refresh_sources()
        return {}

    @plugin.route("/drop", methods=["POST"])
    def drop():
        from flask import request
        ids = set(request.get_json()["ids"])
        keep = [p for p in store.get("papers", []) if f"x:arxiv:{p.get('arxiv')}" not in ids and f"x:doi:{p.get('doi')}" not in ids]
        store.set("papers", keep)
        plugin.refresh_sources()
        return {}
"""

FRONTEND = """
export function activate(p) {
  p.itemActions.register({
    id: 'drop', title: 'Drop From the List', outside: true,
    when: (items) => items.every((i) => i._outside),
    run: async (items) => { await p.backend.post('/drop', { ids: items.map((i) => i.id) }); await p.sources.refresh(); },
  });
  p.itemActions.register({ id: 'lib', title: 'Only For Library Items', run: () => {} });
  p.commands.register({ id: 'open', title: 'Open the Demo List', run: () => p.sources.open('today') });
}
"""


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
        await asyncio.sleep(0.15)
    return False


def api(path, method="GET", body=None):
    return requests.request(method, BASE + path, json=body, timeout=30).json()


ENTRY = '#sidebar [data-view="source:lists-demo.today"]'


async def titles(page):
    return await page.eval_on_selector_all(
        ".items-body .row[data-id] .c-title .t", "els => els.map(e => e.textContent)"
    )


async def main():
    guard(PORT)
    folder = DATA / "plugins" / "lists-demo"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(
        json.dumps(
            {
                "id": "lists-demo",
                "name": "Lists Demo",
                "version": "0.1",
                "api": "^1.0",
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
        for _ in range(100):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        api(
            "/api/settings", "PUT", {"add_find_pdf": "never", "add_fetch_references": "never", "references_auto": False}
        )
        api("/api/plugin-host/metadata-sources/disable", "POST")
        mine = api(
            "/api/items", "POST", {"title": "Mine on tensor networks", "doi": "10.5555/mine", "authors": "Doe, Jane"}
        )
        api("/api/plugin-host/lists-demo/enable", "POST")
        api("/api/folders", "POST", {"name": "To read"})
        papers = [
            {
                "arxiv": "2610.00002",
                "title": "Banana tensor networks",
                "authors": ["Jane Doe"],
                "year": 2026,
                "abstract": "We study tensor networks.",
                "highlights": [
                    {"label": "Following", "text": 'Following: Jane <Doe> & "Ann"', "tone": "gold", "icon": "star"},
                    {"label": "Cites you", "text": "Cites a paper you authored", "tone": "red", "icon": "megaphone"},
                ],
                "reasons": [
                    {"kind": "cites", "text": "Cites one of your papers", "items": [mine["id"]]},
                    "Listed today",
                ],
            },
            {
                "doi": "10.5555/mine",
                "reasons": ["Already yours"],
                "highlight": {"label": "Following", "text": "Following: Jane Doe"},
            },
            {"doi": "10.5555/apple", "title": "Apple of discord", "year": 2025},
            {"arxiv": "2610.00003", "title": "Cherry picking", "year": 2026},
        ]
        api("/api/plugins/lists-demo/set", "POST", {"papers": papers, "highlights": papers[0]["highlights"]})

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1300, "height": 950})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(BASE)
            await page.wait_for_selector(".row[data-id]", timeout=15000)

            # ---------------------------------------------------------- the sidebar
            check("the list has an entry under Plugins", await wait_for(lambda: page.is_visible(ENTRY)))
            badge = await page.text_content(f"{ENTRY} .count")
            check("with the number of papers not in the library", badge == "3", badge)
            await page.click(ENTRY)
            ok = await wait_for(
                lambda: page.evaluate("document.querySelectorAll('.items-body .row[data-id]').length === 4")
            )
            check("it shows the papers", ok)
            check("source badges show on outside and library papers", await page.locator(".row-highlight").count() == 3)
            check(
                "source badge tooltip escapes author names",
                await page.locator('.row[data-id="x:arxiv:2610.00002"] .row-highlight.gold').get_attribute("title")
                == 'Following: Jane <Doe> & "Ann"',
            )
            check(
                "retained-day badges do not leak from non-calendar variants",
                await page.locator('.source-variant-item[data-source-variant="today"] .source-badge.compact').count()
                == 2
                and await page.locator(f"{ENTRY} .source-badge.compact").count() == 0,
            )
            check(
                "navigation badges share paper colours and use white icons",
                await page.locator(".source-variant-item .source-badge.gold").first.evaluate(
                    "el => getComputedStyle(el).backgroundColor === getComputedStyle(document.querySelector('.row-highlight.gold')).backgroundColor && getComputedStyle(el.querySelector('svg')).color === 'rgb(255, 255, 255)'"
                ),
            )
            check(
                "variants use a second pane, not extra main-sidebar rows",
                await page.locator("#sidebar [data-view^='source:lists-demo.today']").count() == 1
                and await page.is_visible(".source-variants-pane")
                and await page.locator(".source-variant-item").count() == 2,
            )
            check(
                "non-date variants keep their supplied title",
                "26 Oct 2026" in (await page.text_content(".source-variant-item.active") or ""),
            )
            await page.evaluate("""async () => {
                const {state, emit} = await import('/js/store.js');
                const now = new Date();
                const day = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
                const variants = state.sources.find(s => s.id === 'lists-demo.today').variants;
                variants[0] = {...variants[0], id: day, title: day};
                emit('sources');
            }""")
            check(
                "a variant dated today gets the Today label",
                "Today" in (await page.text_content(".source-variant-item.active") or ""),
            )
            check(
                "today's badges appear in the main sidebar",
                await page.locator(f"{ENTRY} .source-badge.compact").count() == 2,
            )
            check(
                "citation badge uses Synapse red and a megaphone",
                await page.locator(".source-variant-item .source-badge.red").evaluate(
                    "el => getComputedStyle(el).backgroundColor === 'rgb(204, 41, 54)' && el.querySelector('svg path').getAttribute('d').startsWith('M6 5.5')"
                ),
            )
            check(
                "main-sidebar badge icons stay white when selected",
                await page.locator(f"{ENTRY} .source-badge.gold svg").evaluate(
                    "el => getComputedStyle(el).color === 'rgb(255, 255, 255)'"
                ),
            )
            await shot(page, "following-and-cites-you")
            await page.evaluate("""async () => {
                const {state} = await import('/js/store.js');
                const variants = state.sources.find(s => s.id === 'lists-demo.today').variants;
                variants[0] = {...variants[0], id: '2020-01-01', title: '01 Jan 2020'};
                document.dispatchEvent(new Event('visibilitychange'));
            }""")
            check(
                "returning to an old feed updates its date without a fetch",
                "01 Jan 2020" in (await page.text_content(".source-variant-item.active") or "")
                and "Today" not in (await page.text_content(".source-variant-item.active") or ""),
            )
            check(
                "old days retain both badges while the main sidebar clears",
                await page.locator(f"{ENTRY} .source-badge.compact").count() == 0
                and await page.locator('.source-variant-item[data-source-variant="2020-01-01"] .source-badge').count()
                == 2,
            )
            await page.click('.source-variant-item[data-source-variant="yesterday"]')
            check(
                "a retained day opens from that pane",
                await wait_for(lambda: page.is_visible('.source-variant-item.active[data-source-variant="yesterday"]')),
            )
            order = await titles(page)
            check(
                "in the plugin's order",
                order == ["Banana tensor networks", "Mine on tensor networks", "Apple of discord", "Cherry picking"],
                order,
            )
            n_add = await page.locator(".items-body .row-add").count()
            check("papers not in the library have a +", n_add == 3, n_add)
            check(
                "the library paper has none", await page.locator(".items-body .row:not(.outside) .row-add").count() == 0
            )
            status = await page.text_content("#statusbar [data-s='count']")
            check(
                "the status bar names the list",
                "in Today's List" in status and "3 not in your library" in status,
                status,
            )
            check(
                "its entry is active",
                await page.evaluate(f"document.querySelector('{ENTRY}').classList.contains('active')"),
            )
            await shot(page, "o1-list")

            # ---------------------------------------------------------- sorting
            head = '.items-head .head-cell[data-col="title"]'
            await page.click(head)
            asc = await titles(page)
            await page.click(head)
            desc = await titles(page)
            await page.click(head)
            back = await titles(page)
            check(
                "a column sorts the list",
                asc[0] == "Apple of discord" and desc[0] == "Mine on tensor networks",
                (asc, desc),
            )
            check("a third click goes back to the plugin's order", back == order, back)
            check(
                "the library's own sort is untouched",
                json.loads(await page.evaluate("localStorage.getItem('zc.sort')") or '{"col":"date_added"}')["col"]
                != "title",
            )

            # ---------------------------------------------------------- the pane
            await page.click('.items-body .row[data-id="x:arxiv:2610.00002"] .c-title .t')
            ok = await wait_for(lambda: page.is_visible("#item-pane .ip-reasons"))
            pane = await page.text_content("#item-pane")
            check(
                "the pane says why it is listed",
                ok and "Cites one of your papers" in pane and "Listed today" in pane,
                pane[:300],
            )
            check("and that it is not in the library", "Not in your library" in pane)
            check("with its abstract", "We study tensor networks." in pane)
            ok = await wait_for(lambda: page.is_visible("#item-pane .ip-reasons [data-show-item]"))
            check("the library paper it cites is linked", ok)
            await shot(page, "o2-pane")
            await page.click("#item-pane .ip-reasons [data-show-item]")
            ok = await wait_for(
                lambda: page.evaluate(
                    f"document.querySelector('.items-body .row.selected')?.dataset.id === '{mine['id']}'"
                )
            )
            check("the link shows it in the library", ok)
            await page.click(ENTRY)
            await page.wait_for_selector('.items-body .row[data-id="x:arxiv:2610.00002"]')

            # ---------------------------------------------------------- the menu
            await page.click('.items-body .row[data-id="x:doi:10.5555/apple"] .c-title .t', button="right")
            menu = await page.text_content(".menu")
            check(
                "its menu offers adding it",
                "Add to My Library" in menu and "Add to Folder" in menu and "Copy Identifier" in menu,
                menu,
            )
            check("and nothing that needs a library item", "Move Item to Trash" not in menu and "Edit Tags" not in menu)
            check(
                "a plugin's action for outside papers is there, the others are not",
                "Drop From the List" in menu and "Only For Library Items" not in menu,
                menu,
            )
            await page.keyboard.press("Escape")

            # ---------------------------------------------------------- adding
            await page.click('.items-body .row[data-id="x:arxiv:2610.00002"] .row-add')
            ok = await wait_for(
                lambda: page.evaluate(
                    "[...document.querySelectorAll('.notification-message')].some(t => t.textContent.includes('Added to My Library'))"
                ),
                40,
            )
            check("+ adds it", ok)
            ok = await wait_for(
                lambda: page.evaluate("!document.querySelector('.items-body .row[data-id=\"x:arxiv:2610.00002\"]')")
            )
            check("its row is now the library item", ok and await page.locator(".items-body .row-add").count() == 2)
            check(
                "and selected",
                await page.evaluate("!!document.querySelector('.items-body .row.selected:not(.outside)')"),
            )
            ok = await wait_for(lambda: page.evaluate(f"document.querySelector('{ENTRY} .count').textContent === '2'"))
            check("the badge counts one fewer", ok)
            check("the library has it", api("/api/items")["counts"]["library"] == 2)
            await shot(page, "o3-added")
            await page.click('[data-a="notifications"]')
            await (
                page.locator("#notification-center article")
                .filter(has_text="Added to My Library")
                .locator('[data-n="action"]')
                .click()
            )
            ok = await wait_for(
                lambda: page.evaluate("!!document.querySelector('.items-body .row[data-id=\"x:arxiv:2610.00002\"]')")
            )
            check("Undo takes it out again", ok and api("/api/items")["counts"]["library"] == 1)

            # several at once, into a folder
            await page.click('.items-body .row[data-id="x:doi:10.5555/apple"] .c-title .t')
            await page.click('.items-body .row[data-id="x:arxiv:2610.00003"] .c-title .t', modifiers=["Shift"])
            ok = await wait_for(lambda: page.is_visible('#item-pane [data-a="add-folder"]'))
            pane = await page.text_content("#item-pane")
            check(
                "several selected: the pane offers adding them", ok and "papers not in your library" in pane, pane[:200]
            )
            await page.click('#item-pane [data-a="add-folder"]')
            await page.click('.fp-row:has-text("To read")')
            await page.click('.dialog button:has-text("Add")')
            ok = await wait_for(
                lambda: page.evaluate(
                    "[...document.querySelectorAll('.notification-message')].some(t => t.textContent.includes('“To read”'))"
                ),
                60,
            )
            check("they are added to the folder", ok)
            items = api("/api/items")["items"]
            in_folder = sorted(i["title"] for i in items if i.get("folder_ids"))
            check("both of them", in_folder == ["Apple of discord", "Cherry picking"], in_folder)

            # ---------------------------------------------------------- the plugin changes its list
            api(
                "/api/plugins/lists-demo/set",
                "POST",
                {"papers": papers + [{"arxiv": "2610.00004", "title": "Date palms"}]},
            )
            ok = await wait_for(
                lambda: page.evaluate(
                    "[...document.querySelectorAll('.items-body .row .t')].some(t => t.textContent === 'Date palms')"
                ),
                15,
            )
            check("a changed list is shown again by itself", ok)

            # ---------------------------------------------------------- a plugin's frontend and its list
            await page.click('.items-body .row[data-id="x:arxiv:2610.00004"] .c-title .t', button="right")
            await page.click('.menu :text("Drop From the List")')
            ok = await wait_for(
                lambda: page.evaluate("!document.querySelector('.items-body .row[data-id=\"x:arxiv:2610.00004\"]')"), 2
            )
            check("its action changes the list, shown again at once", ok)
            await page.click('#sidebar [data-act="open-notes"]')
            await wait_for(lambda: page.evaluate("document.body.dataset.workspace === 'notes'"))
            await page.keyboard.press(f"{mod}+Shift+KeyP")
            await page.wait_for_selector(".palette-input")
            await page.keyboard.type("demo list")
            await page.keyboard.press("Enter")
            ok = await wait_for(
                lambda: page.evaluate(
                    f"!document.body.dataset.workspace && document.querySelector('{ENTRY}').classList.contains('active')"
                )
            )
            check("it can open its list from Notes", ok)

            # ---------------------------------------------------------- turned off
            api("/api/plugin-host/lists-demo/disable", "POST")
            ok = await wait_for(lambda: page.evaluate(f"!document.querySelector('{ENTRY}')"), 15)
            check("turned off, its entry goes", ok)
            ok = await wait_for(lambda: page.evaluate("!document.querySelector('.items-body .row.outside')"))
            check(
                "and the library is shown",
                ok
                and await page.evaluate(
                    "document.querySelector('#sidebar [data-view=\"library\"]').classList.contains('active')"
                ),
            )

            check("no JavaScript errors", not errors, errors[:3])
            await browser.close()
    finally:
        srv.terminate()
        srv.wait()
        shutil.rmtree(DATA, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
