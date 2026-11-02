"""Workspace toolbar/search/status on desktop and phone, with isolated library data.

python tests/e2e/run.py workspace_shell
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from tests.e2e._guard import guard  # noqa: E402

PORT = 8807
BASE = f"http://127.0.0.1:{PORT}"
MOD = "Meta" if sys.platform == "darwin" else "Control"
results = []


def check(name, condition):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + name, flush=True)


async def main():
    guard(PORT)
    with tempfile.TemporaryDirectory(prefix="synapse-shell-") as data:
        server = subprocess.Popen(
            [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", data],
            cwd=APP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(240):
                try:
                    requests.get(BASE + "/api/settings", timeout=1).raise_for_status()
                    break
                except requests.RequestException:
                    time.sleep(0.25)
            s = requests.Session()
            s.headers["Origin"] = BASE

            def post(path, **body):
                response = s.post(BASE + path, json=body)
                response.raise_for_status()
                return response.json()

            post("/api/items", title="Shell paper")
            post("/api/items", title="Other paper")
            alpha = post("/api/notes/note", title="Alpha", text="# Alpha\n\nOne two three.\n")["path"]
            beta = post("/api/notes/note", title="Beta", text="# Beta\n\nFour five.\n")["path"]
            tid = post("/api/tasks", title="Overdue task", due=(date.today() - timedelta(days=1)).isoformat())["task"][
                "id"
            ]
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page(viewport={"width": 1440, "height": 950})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(BASE)
                await page.wait_for_selector("#startup-screen", state="hidden")
                await page.wait_for_selector('[data-navigation="tasks.main"]')
                await page.wait_for_function(
                    "() => document.querySelector('#statusbar').textContent.includes('2 items')"
                )
                check("library shows item count and library search", await page.locator("#search-input").is_visible())
                await page.fill("#search-input", "Shell")
                await page.wait_for_function(
                    "() => document.querySelector('#statusbar').textContent.includes('matching')"
                )
                await page.click('#sidebar [data-act="open-notes"]')
                await page.wait_for_selector("#notes-view:not([hidden])")
                check(
                    "notes hide library actions and search",
                    not await page.locator('#toolbar [data-a="new"]').is_visible()
                    and not await page.locator("#search-input").is_visible(),
                )
                check(
                    "notes show creation and search controls",
                    await page.locator('#toolbar [data-a="new-note"]').is_visible()
                    and await page.locator(".notes-filter input").is_visible(),
                )
                check(
                    "empty Notes footer and export availability",
                    await page.locator('#statusbar [data-s="count"]').inner_text() == "No note open"
                    and await page.locator('#toolbar [data-a="note-export"]').is_disabled(),
                )
                await page.fill(".notes-filter input", "Alpha")
                await page.wait_for_selector('.nt-row.note[data-path="' + alpha + '"]')
                check(
                    "note search filters notes without leaving workspace",
                    not await page.locator('.nt-row.note[data-path="' + beta + '"]').count()
                    and await page.locator("#notes-view").is_visible(),
                )
                await page.click('.nt-row.note[data-path="' + alpha + '"]')
                await page.wait_for_function(
                    "() => document.querySelector('#statusbar [data-s=count]').textContent === '4 words'"
                )
                check(
                    "note count excludes frontmatter and Markdown-only tokens",
                    await page.locator('#statusbar [data-s="sel"]').inner_text() == "Saved",
                )
                await page.click('[data-mode="source"]')
                await page.click(".cm-content")
                await page.keyboard.press(MOD + "+End")
                save_gate = asyncio.Event()

                async def hold_save(route):
                    if route.request.method == "PUT":
                        await save_gate.wait()
                    await route.continue_()

                await page.route("**/api/notes/note", hold_save)
                await page.keyboard.type(" extra words")
                await page.wait_for_function(
                    "() => document.querySelector('#statusbar [data-s=count]').textContent === '6 words'"
                )
                check("word count follows typing", True)
                await page.wait_for_function(
                    "() => document.querySelector('#statusbar [data-s=sel]').textContent === 'Saving…'"
                )
                check("footer shows an in-flight save", True)
                save_gate.set()
                await page.wait_for_function(
                    "() => document.querySelector('#statusbar [data-s=sel]').textContent === 'Saved'"
                )
                check("footer follows autosave", True)
                await page.unroute("**/api/notes/note", hold_save)
                if os.environ.get("SHOTS"):
                    Path(os.environ["SHOTS"]).mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(Path(os.environ["SHOTS"]) / "workspace-notes-desktop.png"))
                await page.click('#toolbar [data-a="note-export"]')
                check(
                    "note export offers note formats",
                    await page.get_by_role("menuitem", name="Markdown", exact=True).is_visible(),
                )
                await page.keyboard.press("Escape")
                await page.click('#toolbar [data-a="columns"]')
                await page.focus(".notes-filter input")
                await page.mouse.move(120, 300)
                await page.mouse.move(800, 60)
                await page.wait_for_timeout(500)
                check(
                    "toolbar search keeps a hidden notes list open", await page.locator(".notes-tree-pane").is_visible()
                )
                await page.click('#toolbar [data-a="columns"]')
                await page.fill(".notes-filter input", "")
                await page.click('.nt-row.note[data-path="' + beta + '"]')
                await page.wait_for_function(
                    "() => document.querySelector('#statusbar [data-s=count]').textContent === '3 words'"
                )
                check("switching note tabs refreshes word count", True)
                await page.click('#sidebar [data-navigation="tasks.main"]')
                await page.wait_for_selector("#tasks-view:not([hidden])")
                check(
                    "tasks have one search and no library actions",
                    not await page.locator("#search-input").is_visible()
                    and not await page.locator('#toolbar [data-a="new"]').is_visible()
                    and await page.locator(".tv-search-input").is_visible(),
                )
                await page.wait_for_function(
                    "() => document.querySelector('#statusbar').textContent.includes('1 open task')"
                )
                check(
                    "tasks show open and overdue counts", "1 overdue" in await page.locator("#statusbar").inner_text()
                )
                response = s.patch(BASE + "/api/tasks/" + tid, json={"status": "done"})
                response.raise_for_status()
                await page.wait_for_function(
                    "() => document.querySelector('#statusbar').textContent.includes('0 open tasks')"
                )
                check(
                    "task count follows external changes",
                    "overdue" not in await page.locator("#statusbar").inner_text(),
                )
                await page.click('#sidebar [data-view="library"]')
                await page.wait_for_selector("#search-input", state="visible")
                check("returning to library preserves query", await page.input_value("#search-input") == "Shell")
                check("library status returns", "matching" in await page.locator("#statusbar").inner_text())
                # A pending debounced library search must not pull us back out of Notes.
                await page.fill("#search-input", "Other")
                await page.click('#sidebar [data-act="open-notes"]')
                await page.wait_for_timeout(400)
                check("pending library search does not steal Notes", await page.locator("#notes-view").is_visible())
                await page.click('#toolbar [data-a="new-note"]')
                check(
                    "toolbar creates notes rather than library items",
                    await page.get_by_role("heading", name="New note", exact=True).is_visible(),
                )
                await page.keyboard.press("Escape")
                # Compact menus use the same workspace actions.
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.wait_for_function(
                    "() => document.querySelector('#sidebar').getBoundingClientRect().right <= 0"
                )
                check("phone exposes note search", await page.locator(".notes-filter input").is_visible())
                await page.click('#toolbar [data-a="more"]')
                check(
                    "phone Notes menu has note actions only",
                    await page.get_by_role("menuitem", name="New Note…", exact=True).is_visible()
                    and not await page.get_by_role("menuitem", name="New Item", exact=True).count(),
                )
                await page.keyboard.press("Escape")
                await page.fill(".notes-filter input", "Alpha")
                check("phone note search reveals list", await page.locator(".notes-tree-pane").is_visible())
                if os.environ.get("SHOTS"):
                    await page.screenshot(path=str(Path(os.environ["SHOTS"]) / "workspace-notes-phone.png"))
                await page.click('#toolbar [data-a="sidebar"]')
                await page.click('#sidebar [data-navigation="tasks.main"]')
                await page.click('#toolbar [data-a="more"]')
                check(
                    "phone Tasks menu excludes library and note actions",
                    not await page.get_by_role("menuitem", name="New Item", exact=True).count()
                    and not await page.get_by_role("menuitem", name="New Note…", exact=True).count(),
                )
                await page.keyboard.press("Escape")
                check(
                    "phone toolbar stays within viewport",
                    await page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"),
                )
                if os.environ.get("SHOTS"):
                    await page.screenshot(path=str(Path(os.environ["SHOTS"]) / "workspace-tasks-phone.png"))
                check("no browser errors", not errors)
                await browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
