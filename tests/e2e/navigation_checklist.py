"""Workspace navigation: sidebar selection, history, shortcuts and plugin removal.

Run with ``python tests/e2e/run.py navigation``. Uses port 8805 and a temporary library
with an offline plugin page and paper list; never opens or changes the user's data.
"""

import asyncio
import itertools
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from playwright.async_api import TimeoutError, async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from tests.e2e._guard import guard  # noqa: E402

PORT = 8805
BASE = f"http://127.0.0.1:{PORT}"
MOD = "Meta" if sys.platform == "darwin" else "Control"
ENTRIES = {
    "library": '#sidebar [data-view="library"]',
    "notes": '#sidebar [data-act="open-notes"]',
    "graph": '#sidebar [data-navigation="graph.main"]',
    "tasks": '#sidebar [data-navigation="tasks.main"]',
    "page": '#sidebar [data-page="navigation-check.main"]',
    "source": '#sidebar [data-view="source:navigation-check.empty"]',
}
SNAPSHOT = """() => ({
    workspace: document.body.dataset.workspace || 'library',
    graph: !!document.querySelector('.notes-graph:not([hidden])'),
    visible: ['#items-pane', '#notes-view', '#tasks-view', '#page-view'].filter(s => {
        const el = document.querySelector(s); return el && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
    }),
    active: [...document.querySelectorAll('#sidebar .side-item.active')].map(el =>
        el.dataset.navigation || el.dataset.page || el.dataset.view || el.dataset.act),
    history: history.state?.zc,
})"""
ACTIVE = {
    "library": "library",
    "notes": "open-notes",
    "graph": "graph.main",
    "tasks": "tasks.main",
    "page": "navigation-check.main",
    "source": "source:navigation-check.empty",
}
ROOT = {
    "library": "#items-pane",
    "notes": "#notes-view",
    "graph": "#notes-view",
    "tasks": "#tasks-view",
    "page": "#page-view",
    "source": "#items-pane",
}
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name, "" if ok else detail, flush=True)


async def inspect(page, name, destination, *, history=True):
    expected_ws = "notes" if destination == "graph" else "library" if destination == "source" else destination
    expected = {"workspace": expected_ws, "visible": [ROOT[destination]], "active": [ACTIVE[destination]]}
    if expected_ws == "notes":
        expected["graph"] = destination == "graph"
    expected_history = {}
    if history:
        expected_history["ws"] = expected_ws
        if expected_ws == "notes":
            expected_history["graph"] = destination == "graph"
        if destination in ("library", "source"):
            expected_history["view"] = ACTIVE[destination]
    # Opening a workspace may await data. Check its rendered state and history together.
    ok = True
    try:
        await page.wait_for_function(
            "([expected, expectedHistory]) => { const s = ("
            + SNAPSHOT
            + """)();
                return Object.entries(expected).every(([k,v]) => JSON.stringify(s[k]) === JSON.stringify(v))
                    && Object.entries(expectedHistory).every(([k,v]) => k === 'graph'
                        ? !!s.history?.graph === v : s.history?.[k] === v);
            }""",
            arg=[expected, expected_history],
            timeout=3000,
        )
    except TimeoutError:
        ok = False
    check(name, ok, await page.evaluate(SNAPSHOT))


async def toggle(page, pid, enabled):
    requests.post(f"{BASE}/api/plugin-host/{pid}/{'enable' if enabled else 'disable'}", timeout=10).raise_for_status()
    await page.evaluate("window.zcPlugins.sync()")


async def main():
    guard(PORT)
    with tempfile.TemporaryDirectory(prefix="synapse-navigation-") as tmp:
        data = Path(tmp)
        plugin = data / "plugins" / "navigation-check"
        (plugin / "backend").mkdir(parents=True)
        (plugin / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "navigation-check",
                    "name": "Navigation Check",
                    "version": "0.1.0",
                    "api": "^1.0",
                    "backend": "backend",
                    "frontend": "main.js",
                }
            )
        )
        (plugin / "backend" / "__init__.py").write_text("""def activate(p):
    @p.item_source("empty", title="Offline List")
    def empty():
        return []
""")
        (plugin / "main.js").write_text("""export function activate(p) {
    p.pages.register({id:'main',title:'Offline Page',mount(el) {
        el.textContent='Offline page'; return () => { window.navigationCleanups=(window.navigationCleanups||0)+1; };
    }});
    p.commands.register({id:'open',title:'Open Offline Page',key:'Mod-Alt-y',run:()=>p.pages.open('main')});
}
""")
        srv = subprocess.Popen(
            [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", tmp],
            cwd=APP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                try:
                    requests.get(BASE + "/api/settings", timeout=1).raise_for_status()
                    break
                except requests.RequestException:
                    time.sleep(0.2)
            requests.post(BASE + "/api/plugin-host/navigation-check/enable", timeout=10).raise_for_status()
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page(viewport={"width": 1400, "height": 1000})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                await page.goto(BASE)
                await page.wait_for_selector(ENTRIES["page"])
                await page.wait_for_selector(ENTRIES["source"])
                for source, destination in itertools.permutations(ENTRIES, 2):
                    await page.click(ENTRIES[source])
                    await inspect(page, f"Prepare {source} before {destination}", source)
                    await page.click(ENTRIES[destination])
                    await inspect(page, f"{source} → {destination}: one visible view and one selection", destination)

                for source, destination in (
                    ("tasks", "library"),
                    ("page", "library"),
                    ("graph", "tasks"),
                    ("notes", "page"),
                    ("source", "graph"),
                ):
                    await page.click(ENTRIES[source])
                    await inspect(page, f"Prepare {source} before {destination}", source)
                    await page.click(ENTRIES[destination])
                    await inspect(page, f"{source} → {destination}: history matches", destination, history=True)
                    await page.go_back()
                    await inspect(page, f"Back returns to {source} from {destination}", source, history=True)
                    await page.go_forward()
                    await inspect(page, f"Forward returns to {destination} from {source}", destination, history=True)

                for destination, key in (
                    ("tasks", "Shift+k"),
                    ("graph", "Shift+g"),
                    ("page", "Alt+y"),
                    ("notes", "Shift+l"),
                ):
                    await page.click(ENTRIES["library"])
                    await page.keyboard.press(f"{MOD}+{key}")
                    await inspect(page, f"Shortcut opens {destination}", destination, history=True)

                for pid, destination, fallback in (
                    ("tasks", "tasks", "library"),
                    ("graph", "graph", "notes"),
                    ("navigation-check", "page", "library"),
                ):
                    await page.click(ENTRIES[destination])
                    await toggle(page, pid, False)
                    if destination == "page":
                        await page.wait_for_function("document.body.dataset.workspace !== 'page'", timeout=6000)
                    await inspect(page, f"Stopping {pid} leaves a usable {fallback} view", fallback, history=True)
                    check(f"Stopping {pid} removes its entry", await page.locator(ENTRIES[destination]).count() == 0)
                    shortcut = {"tasks": "Shift+k", "graph": "Shift+g", "navigation-check": "Alt+y"}[pid]
                    await page.keyboard.press(f"{MOD}+{shortcut}")
                    await inspect(page, f"Stopped {pid} shortcut does not reopen its view", fallback)
                    await toggle(page, pid, True)
                    await page.click(ENTRIES[destination])
                    await inspect(page, f"Restarted {pid} opens once", destination)
                for pid, destination, next_view, fallback in (
                    ("tasks", "tasks", "notes", "library"),
                    ("graph", "graph", "tasks", "notes"),
                    ("navigation-check", "page", "tasks", "library"),
                    ("navigation-check", "source", "notes", "library"),
                ):
                    await page.click(ENTRIES["library"])
                    await page.click(ENTRIES[destination])
                    await inspect(page, f"Prepare {destination} history before stopping {pid}", destination)
                    await page.click(ENTRIES[next_view])
                    await inspect(page, f"Leave {destination} before stopping {pid}", next_view)
                    await toggle(page, pid, False)
                    await page.go_back()
                    await inspect(page, f"Back to stopped {destination} falls back to {fallback}", fallback)
                    await page.go_forward()
                    await inspect(page, f"Forward after stopped {destination} still works", next_view)
                    await toggle(page, pid, True)
                await page.click(ENTRIES["page"])
                await page.keyboard.press(f"{MOD}+f")
                await inspect(page, "Library search shortcut leaves plugin page with matching history", "library")
                # A delayed task load must not overwrite the next workspace's focus/history.
                await toggle(page, "tasks", False)
                release_tasks = asyncio.Event()
                tasks_requested = asyncio.Event()

                async def delayed_tasks(route):
                    tasks_requested.set()
                    await release_tasks.wait()
                    await route.continue_()

                await page.route("**/api/tasks", delayed_tasks)
                await toggle(page, "tasks", True)
                await asyncio.wait_for(tasks_requested.wait(), timeout=5)
                await page.click(ENTRIES["tasks"])
                await page.click(ENTRIES["page"])
                await inspect(page, "Plugin page opens while Tasks is still loading", "page")
                release_tasks.set()
                await page.evaluate("""async () => {
                    await (await import('/js/plugins/core/tasks/taskData.js')).loadTasks();
                    await new Promise(requestAnimationFrame);
                }""")
                await inspect(page, "Delayed Tasks completion preserves the plugin page and history", "page")
                await page.unroute("**/api/tasks", delayed_tasks)

                # Leaving a stopped page cancels its restart grace period.
                await page.evaluate("""async () => {
                    const {pages} = await import('/js/extend.js');
                    window.removeOtherPage = pages.register({id:'audit.other',title:'Other Page',mount(el){el.textContent='Other';}});
                }""")
                cleanups = await page.evaluate("window.navigationCleanups || 0")
                await toggle(page, "navigation-check", False)
                check(
                    "Stopping an open page cleans it up exactly once",
                    await page.evaluate("window.navigationCleanups || 0") == cleanups + 1,
                )
                await page.click('#sidebar [data-page="audit.other"]')
                await page.wait_for_function("history.state?.zc?.page === 'audit.other'")
                await toggle(page, "navigation-check", True)
                check(
                    "Restarting a page after leaving it does not replace the chosen page",
                    await page.evaluate(
                        "history.state.zc.page === 'audit.other' && document.querySelector('#page-view .page-body').textContent === 'Other'"
                    ),
                )
                await page.evaluate("window.removeOtherPage()")
                note = requests.post(BASE + "/api/notes/note", json={"title": "Save on navigation"}, timeout=5)
                note.raise_for_status()
                path = note.json()["path"]
                await page.evaluate(
                    """async path => {
                    (await import('/js/store.js')).emit('open-note', {path});
                }""",
                    path,
                )
                await page.wait_for_function("path => window.zcNotes.path() === path", arg=path)
                editor = page.locator("#notes-view .cm-content:visible")
                await editor.click()
                await page.keyboard.press(f"{MOD}+End")
                await page.keyboard.insert_text("\nSaved before mounting a plugin page.\n")
                await page.click(ENTRIES["page"])
                await inspect(page, "Plugin page replaces an edited note", "page")
                saved = requests.get(BASE + "/api/notes/note", params={"path": path}, timeout=5).json()
                check(
                    "Leaving Notes for a plugin page saves the latest edit",
                    "Saved before mounting a plugin page." in saved["text"],
                )
                check("No browser errors", not errors, errors)
                await browser.close()
        finally:
            srv.terminate()
            srv.wait(timeout=10)
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
