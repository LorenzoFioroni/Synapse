"""Contributed Tasks' private note index follows notes created in another client."""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
PORT = 8808
BASE = f"http://127.0.0.1:{PORT}"
results = []


def check(name, condition, extra=""):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + name, extra)


async def main():
    data = tempfile.mkdtemp(prefix="synapse-task-indexes-")
    server = subprocess.Popen(
        [sys.executable, "server.py", "--no-browser", "--port", str(PORT)],
        cwd=APP,
        env={**os.environ, "SYNAPSE_DATA_DIR": data},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                if requests.get(BASE, timeout=1).ok:
                    break
            except requests.RequestException:
                pass
            await asyncio.sleep(0.1)
        requests.put(
            BASE + "/api/settings", json={"references_auto": False, "add_fetch_references": "never"}, timeout=5
        ).raise_for_status()
        requests.post(BASE + "/api/notes/note", json={"title": "Existing index note"}, timeout=5).raise_for_status()
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(BASE)
            await page.wait_for_function("() => (window.zcPlugins?.loaded().includes('tasks'))")
            await page.click('[data-navigation="tasks.main"]')
            await page.locator('#tasks-view [data-tv="dest"]').click()
            await page.get_by_role("menuitem", name="Another note…").click()
            await page.get_by_role("button", name="Existing index note", exact=False).wait_for()
            await page.keyboard.press("Escape")
            check("Tasks starts with the existing note index", True)
            # Establish the change-feed cursor before the other client creates a note.
            await page.evaluate("window.zcChanges.poll()")
            requests.post(BASE + "/api/notes/note", json={"title": "Fresh index note"}, timeout=5).raise_for_status()
            async with page.expect_response(lambda r: r.url.endswith("/api/notes/cite_index")) as refreshed:
                await page.evaluate("window.zcChanges.poll()")
            await (await refreshed.value).body()
            await page.wait_for_timeout(200)
            await page.locator('#tasks-view [data-tv="dest"]').click()
            await page.get_by_role("menuitem", name="Another note…").click()
            await page.get_by_role("button", name="Fresh index note", exact=False).wait_for()
            check("Tasks sees a note created in another client without restarting", True)
            await page.keyboard.press("Escape")
            await page.evaluate("""async () => {
                const {api} = await import('/js/api.js');
                await api.createNote({title: 'Local index note'});
                (await import('/js/views/notes/notesView.js')).notesChanged();
            }""")
            await page.wait_for_function("""async () => {
                const sdk = await import('/js/plugins/task-runtime.js');
                return sdk.noteIndexSnapshot().notes.some(note => note.title === 'Local index note');
            }""")
            await page.locator('#tasks-view [data-tv="dest"]').click()
            await page.get_by_role("menuitem", name="Another note…").click()
            await page.get_by_role("button", name="Local index note", exact=False).wait_for()
            check("Tasks sees this window's own newly created note", True)
            check("No page errors", not errors, errors)
            await browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(data, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _guard import guard  # noqa: E402

guard(PORT)
raise SystemExit(asyncio.run(main()))
