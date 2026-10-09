"""Browser checklist for the Graph workspace: papers, cited works and notes in one network;
options, search, focus (local graph), details panel, opening items and notes, Back/Forward.

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
from backend.references import CitationStore  # noqa: E402

PORT = 8771
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-graph-e2e-")
SHOTS = os.environ.get("SHOTS")
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
        p = requests.post(f"{BASE}/api/items", json={"authors": a, "title": f"Tensor network paper {i}", "year": 2005 + i,
                                                     "doi": f"10.1000/tn{i}", "citation_key": f"p{i}"}).json()
        papers.append(p)
    store = CitationStore(Path(DATA) / "citations.db")
    for i, p in enumerate(papers):
        refs = [{"doi": f"10.1000/tn{j}", "title": f"Tensor network paper {j}", "year": 2005 + j} for j in range(i) if (i + j) % 3 != 1]
        refs += [{"doi": "10.9999/classic", "title": "A classic everyone cites", "authors": "Old, Oscar", "year": 1990},
                 {"doi": f"10.9999/only{i}", "title": f"Cited once {i}", "year": 2000}]
        store.save(p["id"], "test", refs, doi=f"10.1000/tn{i}")
    keys = [requests.get(f"{BASE}/api/items/{p['id']}").json()["citation_key"] for p in papers]

    def note(text_extra, **kw):
        n = requests.post(f"{BASE}/api/notes/note", json=kw).json()
        cur = requests.get(f"{BASE}/api/notes/note", params={"path": n["path"]}).json()
        requests.put(f"{BASE}/api/notes/note", json={"path": n["path"], "text": cur["text"] + text_extra, "hash": cur["hash"]})
        return n["path"]

    review = note(f"\nOverview of [@{keys[1]}] and [@{keys[4]}; @{keys[7]}].\n", title="Review", folder="topics")
    pnote = note(f"\nBuilds on [@{keys[2]}]. See [[Review]].\n", item_id=papers[3]["id"])
    note("\nJust a thought, linked to [[Review]].\n", title="Idea")
    return papers, keys, review, pnote


async def node_count(page, kind=None):
    return await page.evaluate(f"window.zcGraph.view.nodes.filter((n) => {('n.kind === ' + repr(kind)) if kind else 'true'}).length")


async def click_node(page, node_id, double=False):
    await page.evaluate("window.zcGraph.settle()")
    pos = await page.evaluate(f"window.zcGraph.screenOf({node_id!r})")
    box = await page.locator('.graph-canvas').bounding_box()
    x, y = box["x"] + pos["x"], box["y"] + pos["y"]
    await page.mouse.move(x, y)
    if double:
        await page.mouse.dblclick(x, y)
    else:
        await page.mouse.click(x, y)
    await page.wait_for_timeout(200)


async def main():
    env = dict(os.environ, ZCLONE_DATA_DIR=DATA)
    srv = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--no-browser"], cwd=APP, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    try:
        papers, keys, review, pnote = seed()
        g = requests.get(f"{BASE}/api/graph").json()
        check("API: papers, works and notes", g["stats"]["papers"] == 10 and g["stats"]["notes"] == 3 and g["stats"]["works"] >= 11, g["stats"])
        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(viewport={"width": 1440, "height": 900})
            page = await ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(BASE)
            await page.wait_for_selector('.ws-switch [data-ws="graph"]')
            await page.evaluate("localStorage.removeItem('zc.graph.options')")

            # ---- open from the toolbar
            await page.click('.ws-switch [data-ws="graph"]')
            await page.wait_for_function("window.zcGraph && window.zcGraph.view.nodes.length > 0", timeout=8000)
            check("Graph button is active", await page.locator('.ws-switch [data-ws="graph"].active').count() == 1)
            check("library panes are hidden", not await page.is_visible('#items-pane') and not await page.is_visible('#sidebar'))
            n_paper, n_work, n_note = await node_count(page, "paper"), await node_count(page, "work"), await node_count(page, "note")
            check("all papers and notes are shown", n_paper == 10 and n_note == 3, (n_paper, n_note))
            check("only works cited by ≥ 2 papers by default", n_work == 1, n_work)
            await page.evaluate("window.zcGraph.settle()")
            await shot(page, "1-graph")

            # ---- options
            await page.fill('[data-opt="minCited"]', "1")
            await page.dispatch_event('[data-opt="minCited"]', "change")
            check("lower threshold shows works cited once", await node_count(page, "work") == 11, await node_count(page, "work"))
            await page.uncheck('[data-opt="notes"]')
            check("notes can be hidden", await node_count(page, "note") == 0)
            await page.check('[data-opt="notes"]')
            await page.uncheck('[data-edge="cites"]')
            n = await page.evaluate("window.zcGraph.view.edges.filter((e) => e.kind === 'cites').length")
            check("paper→paper links can be hidden", n == 0, n)
            await page.check('[data-edge="cites"]')
            await page.fill('[data-opt="minCited"]', "2")
            await page.dispatch_event('[data-opt="minCited"]', "change")
            stored = await page.evaluate("JSON.parse(localStorage.getItem('zc.graph.options')).minCited")
            check("options are remembered", stored == 2, stored)

            # ---- search
            await page.fill('.graph-search input', 'banuls')
            await page.press('.graph-search input', 'Enter')
            sel = await page.evaluate("window.zcGraph.selected")
            check("search (accents ignored) selects the match", sel == f"item:{papers[2]['id']}", sel)
            text = await page.text_content('.graph-details')
            check("details panel shows the paper", "Tensor network paper 2" in text and "References" in text, text[:120])
            check("details list notes citing it", "Cited in notes" in text)
            await shot(page, "2-selected")
            await page.fill('.graph-search input', '')

            # ---- hover tooltip
            await page.evaluate("window.zcGraph.settle()")
            pos = await page.evaluate(f"window.zcGraph.screenOf({'note:' + review!r})")
            box = await page.locator('.graph-canvas').bounding_box()
            await page.mouse.move(box["x"] + pos["x"] + 1, box["y"] + pos["y"] + 1, steps=3)
            await page.wait_for_timeout(150)
            check("tooltip on hover", await page.is_visible('.graph-tip') and "Review" in await page.text_content('.graph-tip'))
            await click_node(page, f"note:{review}")
            check("clicking a note selects it", await page.evaluate("window.zcGraph.selected") == f"note:{review}")

            # ---- focus (local graph) from the details panel, depth control, Back
            await click_node(page, f"item:{papers[3]['id']}")
            await page.click('.graph-details [data-act="focus"]')
            await page.wait_for_timeout(300)
            ids = await page.evaluate("window.zcGraph.view.nodes.map((n) => n.id)")
            want = {f"item:{papers[3]['id']}", f"note:{pnote}", "doi:10.9999/only3", "doi:10.9999/classic"}
            check("focus shows the neighbourhood (incl. works cited once)", want <= set(ids) and len(ids) < 13, len(ids))
            check("focus bar is shown", "Around" in await page.text_content('.graph-focus'))
            await page.click('.graph-focus [data-depth="2"]')
            ids2 = await page.evaluate("window.zcGraph.view.nodes.map((n) => n.id)")
            check("depth 2 shows more", len(ids2) > len(ids) and f"note:{review}" in ids2, (len(ids), len(ids2)))
            await shot(page, "3-focus")
            await page.go_back()
            await page.wait_for_timeout(500)
            check("Back leaves the focus", await page.evaluate("window.zcGraph.opts.focus") is None
                  and await page.is_hidden('.graph-focus'))
            await page.go_forward()
            await page.wait_for_timeout(500)
            check("Forward returns to it", await page.evaluate("window.zcGraph.opts.focus") == f"item:{papers[3]['id']}")
            await page.click('.graph-focus [data-unfocus]')

            # ---- double-click a paper: shows it in the library; Back returns to the graph
            await click_node(page, f"item:{papers[5]['id']}", double=True)
            await page.wait_for_timeout(500)
            check("double-click shows the paper in the library", await page.is_visible('#items-pane')
                  and await page.locator('.ws-switch [data-ws="library"].active').count() == 1)
            sel = await page.evaluate("[...document.querySelectorAll('#items-pane .selected, #items-pane [aria-selected=true]')].length")
            check("…selected", sel >= 1, sel)
            await page.go_back()
            await page.wait_for_timeout(600)
            check("Back returns to the graph", await page.evaluate("document.body.dataset.workspace") == "graph")

            # ---- open a note from the graph
            await click_node(page, f"note:{review}", double=True)
            await page.wait_for_timeout(800)
            check("double-click a note opens it in Notes", await page.evaluate("document.body.dataset.workspace") == "notes"
                  and "Review" in (await page.text_content('.note-crumbs') or ""))
            await page.go_back()
            await page.wait_for_timeout(600)
            check("Back from the note returns to the graph", await page.evaluate("document.body.dataset.workspace") == "graph"
                  and await page.is_hidden('#notes-view'))

            # ---- Show in Graph from the item menu and from the notes tree
            await page.click('.ws-switch [data-ws="library"]')
            await page.wait_for_timeout(300)
            row = page.locator('#items-pane [data-id="%s"]' % papers[7]["id"])
            if not await row.count():
                row = page.locator('#items-pane .item-row, #items-pane tr').filter(has_text="Tensor network paper 7").first
            await row.click(button="right")
            await page.click('.menu >> text=Show in Graph')
            await page.wait_for_timeout(500)
            check("Show in Graph (item) focuses the paper", await page.evaluate("window.zcGraph.opts.focus") == f"item:{papers[7]['id']}")
            await page.keyboard.press("Escape")   # deselect
            await page.keyboard.press("Escape")   # leave focus
            check("Escape leaves the focus", await page.evaluate("window.zcGraph.opts.focus") is None)

            await page.click('.ws-switch [data-ws="notes"]')
            await page.wait_for_selector('#notes-view:not([hidden])')
            await page.locator('.notes-tree [data-path="topics/Review.md"], .notes-tree :text("Review")').first.click(button="right")
            await page.click('.menu >> text=Show in Graph')
            await page.wait_for_timeout(500)
            check("Show in Graph (note) focuses the note", await page.evaluate("window.zcGraph.opts.focus") == f"note:{review}"
                  and await page.is_hidden('#notes-view'))

            # ---- keyboard: ⌘⇧G toggles
            mod = "Meta" if sys.platform == "darwin" else "Control"
            await page.keyboard.press(f"{mod}+Shift+G")
            await page.wait_for_timeout(300)
            check(f"{mod}+Shift+G leaves the graph", await page.evaluate("document.body.dataset.workspace") in (None, "library") and await page.is_visible('#items-pane'))
            await page.keyboard.press(f"{mod}+Shift+G")
            await page.wait_for_timeout(300)
            check(f"{mod}+Shift+G opens it", await page.evaluate("document.body.dataset.workspace") == "graph")

            # ---- the selected node and its neighbours are drawn lit after a reload
            lit = await page.evaluate("""() => { const v = window.zcGraph.view; const sel = window.zcGraph.selected;
                const nb = v.edges.filter((e) => e.source === sel || e.target === sel).length; return [sel, nb]; }""")
            await page.mouse.move(2, 2)
            await page.click('.ws-switch [data-ws="library"]')
            await page.locator('#items-pane .row[data-id="%s"]' % papers[7]["id"]).click(button="right")
            await page.click('.menu >> text=Show in Graph')
            await page.wait_for_timeout(400)
            px = await page.evaluate("""() => { window.zcGraph.settle(); const p = window.zcGraph.screenOf(window.zcGraph.selected);
                const c = document.querySelector('.graph-canvas'); const d = c.getContext('2d');
                const r = devicePixelRatio; const a = d.getImageData(Math.round(p.x * r), Math.round(p.y * r), 1, 1).data; return [...a]; }""")
            check("the focused node is drawn at full strength", px[3] == 255 and px[2] > 150, px)

            # ---- dark theme renders
            await page.evaluate("document.documentElement.dataset.theme = 'dark'; window.zcGraph.settle()")
            await shot(page, "4-dark")
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
