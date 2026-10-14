"""Browser checklist for the graph in Notes: the local graph next to a note (depth, papers,
collapse, click to open), the full graph (notes-first, options, search, paper card, Show in
Library), Show in Graph from the item and note menus, Cmd/Ctrl+Shift+G and Back/Forward.

    python tests/e2e/graph_checklist.py                 (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8771 with a throw-away data folder; your library is not touched.
Reference lists are written straight into citations.db (no network).
"""
import asyncio, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from backend.references.store import CitationStore  # noqa: E402

PORT = 8771
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-graph-e2e-")
SHOTS = os.environ.get("SHOTS")
MOD = "Meta" if sys.platform == "darwin" else "Control"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


def seed():
    authors = ["Cirac, J. Ignacio", "Verstraete, Frank", "Bañuls, Mari Carmen", "Schollwöck, Ulrich", "Vidal, Guifre",
               "White, Steven", "Orús, Román", "Haegeman, Jutho", "Pollmann, Frank", "Zaletel, Michael"]
    papers = []
    for i, a in enumerate(authors):
        papers.append(requests.post(f"{BASE}/api/items", json={"authors": a, "title": f"Tensor network paper {i}", "year": 2005 + i,
                                                               "doi": f"10.1000/tn{i}", "citation_key": f"p{i}"}).json())
    store = CitationStore(Path(DATA) / "citations.db")
    for i, p in enumerate(papers):
        refs = [{"doi": f"10.1000/tn{j}", "title": f"Tensor network paper {j}", "year": 2005 + j} for j in range(i) if (i + j) % 3 != 1]
        refs += [{"doi": "10.9999/classic", "title": "A classic everyone cites", "authors": "Old, Oscar", "year": 1990}]
        store.save(p["id"], "test", refs, doi=f"10.1000/tn{i}")
    keys = [requests.get(f"{BASE}/api/items/{p['id']}").json()["citation_key"] for p in papers]

    def note(text_extra, **kw):
        n = requests.post(f"{BASE}/api/notes/note", json=kw).json()
        cur = requests.get(f"{BASE}/api/notes/note", params={"path": n["path"]}).json()
        requests.put(f"{BASE}/api/notes/note", json={"path": n["path"], "text": cur["text"] + text_extra, "hash": cur["hash"]})
        return n["path"]

    review = note(f"\nOverview of [@{keys[1]}] and [@{keys[4]}; @{keys[7]}].\n", title="Review", folder="topics")
    pnote = note(f"\nBuilds on [@{keys[2]}] and [@{keys[4]}]. See [[Review]].\n", item_id=papers[3]["id"])
    idea = note("\nJust a thought, linked to [[Review]].\n", title="Idea")
    note("\nNothing linked here.\n", title="Lonely")
    return papers, keys, review, pnote, idea


async def ids(page, which):
    return set(await page.evaluate(f"window.zcGraph.{which}()?.nodes().map((n) => n.id) || []"))


async def settled(page, which, timeout=8):
    """Wait until the nodes have positions (the simulation has started)."""
    end = time.time() + timeout
    while time.time() < end:
        ok = await page.evaluate(f"(() => {{ const g = window.zcGraph.{which}(); const ns = g?.nodes() || [];"
                                 f" return ns.length > 0 && ns.every((n) => Number.isFinite(n.x)); }})()")
        if ok:
            await page.wait_for_timeout(900)
            return True
        await asyncio.sleep(0.15)
    return False


async def click_node(page, which, host_sel, node_id):
    pos = await page.evaluate(f"window.zcGraph.{which}().screenOf({node_id!r})")
    box = await page.locator(host_sel).bounding_box()
    x, y = box["x"] + pos["x"], box["y"] + pos["y"]
    await page.mouse.move(x, y, steps=4)
    await page.wait_for_timeout(200)
    await page.mouse.click(x, y)
    await page.wait_for_timeout(400)


async def main():
    env = dict(os.environ, SYNAPSE_DATA_DIR=DATA)
    srv = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--no-browser"], cwd=APP, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    try:
        papers, keys, review, pnote, idea = seed()
        g = requests.get(f"{BASE}/api/graph").json()
        check("API: papers, works and notes", g["stats"]["papers"] == 10 and g["stats"]["notes"] == 4, g["stats"])
        P = lambda i: f"item:{papers[i]['id']}"   # noqa: E731
        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(viewport={"width": 1440, "height": 900})
            page = await ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(BASE)
            await page.wait_for_selector('.ws-switch [data-ws="notes"]')
            await page.evaluate("['zc.graph.local','zc.graph.global','zc.graph.localOpen'].forEach((k) => localStorage.removeItem(k))")

            # ---- toolbar: two workspaces, the logo keeps its size
            check("toolbar has Library | Notes only", await page.locator('.ws-switch [data-ws]').count() == 2)
            w = await page.evaluate("document.querySelector('.brand-logo').getBoundingClientRect().width")
            check("logo is not squeezed", abs(w - 24) < 0.5, w)

            # ---- local graph next to the note
            await page.click('.ws-switch [data-ws="notes"]')
            await page.wait_for_selector('#notes-view:not([hidden])')
            await page.locator('.notes-tree').get_by_text("Review", exact=True).click()
            await page.wait_for_function("window.zcGraph?.localPath() === 'topics/Review.md'")
            await page.wait_for_selector('.ns-graph-canvas canvas', timeout=8000)
            check("local graph shown by default", await page.is_visible('.ns-graph-canvas canvas'))
            await settled(page, "local")
            want1 = {f"note:{review}", P(1), P(4), P(7), f"note:{pnote}", f"note:{idea}"}
            got = await ids(page, "local")
            check("depth 1: the note, the papers it cites, notes linking to it", got == want1, sorted(got ^ want1))
            await shot(page, "1-local")
            await page.click('.ns-graph [data-lg-depth="2"]')
            await page.wait_for_timeout(500)
            got2 = await ids(page, "local")
            check("depth 2 adds what those connect to", want1 < got2 and P(2) in got2 and P(3) in got2, sorted(got2 - want1))
            await page.click('.ns-graph [data-lg="papers"]')
            await page.wait_for_timeout(400)
            got3 = await ids(page, "local")
            check("papers can be hidden", not any(i.startswith("item:") for i in got3) and f"note:{idea}" in got3, sorted(got3))
            await page.click('.ns-graph [data-lg="papers"]')
            await page.click('.ns-graph [data-lg-depth="1"]')
            await page.click('.ns-graph [data-lg="toggle"]')
            check("local graph can be collapsed", await page.is_hidden('.ns-graph-canvas'))
            stored = await page.evaluate("localStorage.getItem('zc.graph.localOpen')")
            await page.click('.ns-graph [data-lg="toggle"]')
            check("…and the choice is remembered", stored == "false" and await page.is_visible('.ns-graph-canvas'), stored)
            await settled(page, "local")

            # ---- clicking a note in the local graph opens it, and the graph follows
            await click_node(page, "local", '.ns-graph-canvas', f"note:{idea}")
            await page.wait_for_function(f"window.zcNotes.path() === {idea!r}", timeout=5000)
            await page.wait_for_function(f"window.zcGraph.localPath() === {idea!r}")
            await settled(page, "local")
            check("click opens the note; the local graph follows", await ids(page, "local") == {f"note:{idea}", f"note:{review}"})

            # ---- full graph (expand button)
            await page.click('.ns-graph [data-lg="expand"]')
            await page.wait_for_selector('.notes-graph:not([hidden]) .ng-canvas canvas')
            check("full graph replaces the editor", await page.is_hidden('.note-body'))
            fits = await page.evaluate("""() => { const bar = document.querySelector('.ng-bar').getBoundingClientRect();
                return [...document.querySelectorAll('.ng-bar input, .ng-bar [data-ng]')].every((el) => {
                  const r = el.getBoundingClientRect(); return r.width > 0 && r.right <= bar.right + 1 && r.left >= bar.left; }); }""")
            check("every option in the bar is visible", fits)
            await settled(page, "global")
            gids = await ids(page, "global")
            want = {f"note:{review}", f"note:{pnote}", f"note:{idea}", "note:Lonely.md", P(1), P(2), P(3), P(4), P(7)}
            check("notes-first: notes and the papers they cite (no others)", gids == want, sorted(gids ^ want))
            await shot(page, "2-global")
            await page.click('.ng-bar [data-ng="opts"]')
            check("Filters opens the options", await page.is_visible('.ng-opts'))
            await page.check('[data-go="citations"]')
            await page.wait_for_timeout(500)
            gids2 = await ids(page, "global")
            check("“Paper citations” adds every paper", sum(i.startswith("item:") for i in gids2) == 10, len(gids2))
            await page.check('[data-go="works"]')
            await page.wait_for_timeout(400)
            check("…and cited works not in the library", "doi:10.9999/classic" in await ids(page, "global"))
            await page.uncheck('[data-go="works"]')
            await page.uncheck('[data-go="citations"]')
            await page.uncheck('[data-go="orphans"]')
            await page.wait_for_timeout(400)
            check("orphans can be hidden", "note:Lonely.md" not in await ids(page, "global"))
            await page.check('[data-go="orphans"]')
            check("options are remembered",
                  await page.evaluate("JSON.parse(localStorage.getItem('zc.graph.global')).orphans") is True)

            # ---- search
            await page.fill('.ng-search input', 'banuls')
            await page.wait_for_timeout(200)
            await page.press('.ng-search input', 'Enter')
            await page.wait_for_timeout(700)
            await shot(page, "3-search")
            await page.fill('.ng-search input', '')
            await page.dispatch_event('.ng-search input', 'input')

            # ---- paper card → Show in Library, Back returns to the graph
            await settled(page, "global")
            await click_node(page, "global", '.ng-canvas', P(4))
            check("clicking a paper shows its card", await page.is_visible('.graph-card')
                  and "Tensor network paper 4" in await page.text_content('.graph-card'))
            await shot(page, "4-card")
            await page.click('.graph-card [data-gc="item"]')
            await page.wait_for_timeout(600)
            sel = await page.evaluate("[...document.querySelectorAll('#items-pane .row.selected')].map((r) => r.dataset.id)")
            check("Show in Library selects the paper", await page.is_visible('#items-pane') and sel == [papers[4]["id"]], sel)
            await page.go_back()
            await page.wait_for_timeout(800)
            check("Back returns to the graph", await page.is_visible('.notes-graph') and await page.is_hidden('.note-body'))

            # ---- Close / Escape go back to the note
            await page.click('.ng-bar [data-ng="close"]')
            check("Close shows the note again", await page.is_hidden('.notes-graph') and await page.is_visible('.note-body'))
            await page.go_back()
            await page.wait_for_timeout(700)
            check("Back reopens the graph", await page.is_visible('.notes-graph'))
            await page.locator('.notes-graph').focus()
            await page.keyboard.press("Escape")
            check("Escape closes it", await page.is_hidden('.notes-graph'))

            # ---- Cmd/Ctrl+Shift+G toggles
            await page.keyboard.press(f"{MOD}+Shift+G")
            await page.wait_for_timeout(500)
            check(f"{MOD}+Shift+G opens the graph", await page.is_visible('.notes-graph'))
            await page.keyboard.press(f"{MOD}+Shift+G")
            await page.wait_for_timeout(300)
            check(f"{MOD}+Shift+G closes it", await page.is_hidden('.notes-graph'))

            # ---- Show in Graph from the item menu and the notes tree
            await page.click('.ws-switch [data-ws="library"]')
            await page.wait_for_timeout(300)
            await page.locator(f'#items-pane .row[data-id="{papers[7]["id"]}"]').click(button="right")
            await page.click('.menu >> text=Show in Graph')
            await page.wait_for_selector('.notes-graph:not([hidden])')
            check("Show in Graph (item) opens the graph on the paper", await page.evaluate("window.zcGraph.globalFocus()") == P(7))
            await settled(page, "global")
            px = await page.evaluate(f"""() => {{ const p = window.zcGraph.global().screenOf({P(1)!r});
                const c = document.querySelector('.ng-canvas canvas'); const r = devicePixelRatio;
                return [...c.getContext('2d').getImageData(Math.round(p.x * r), Math.round(p.y * r), 1, 1).data]; }}""")
            check("focusing a paper does not fade the others", px[3] > 200, px)
            await page.click('.ng-bar [data-ng="close"]')
            await page.locator('.notes-tree').get_by_text("Review", exact=True).click(button="right")
            await page.click('.menu >> text=Show in Graph')
            await page.wait_for_timeout(500)
            check("Show in Graph (note) opens the graph on the note", await page.evaluate("window.zcGraph.globalFocus()") == f"note:{review}")

            # ---- clicking a note in the full graph opens it
            await settled(page, "global")
            await click_node(page, "global", '.ng-canvas', f"note:{pnote}")
            await page.wait_for_function(f"window.zcNotes.path() === {pnote!r}", timeout=5000)
            check("clicking a note in the full graph opens it", await page.is_hidden('.notes-graph'))

            # ---- the local graph updates after an edit
            await page.locator('.cm-content').click()
            await page.keyboard.press(f"{MOD}+End")
            await page.keyboard.type("\nAlso [[Lonely]].\n")
            await page.keyboard.press(f"{MOD}+s")
            await page.wait_for_function("window.zcGraph.local().nodes().some((n) => n.id === 'note:Lonely.md')", timeout=8000)
            check("local graph picks up a new link after saving", True)

            # ---- dark theme
            await page.evaluate("document.documentElement.dataset.theme = 'dark'")
            await page.keyboard.press(f"{MOD}+Shift+G")
            await settled(page, "global")
            await shot(page, "5-dark")
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
