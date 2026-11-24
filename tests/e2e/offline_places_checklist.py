"""Browser checklist for renaming, moving and deleting notes offline, and for moving, deleting and
creating tasks from a note offline (docs/offline.md): the changes show at once, survive a reload,
and go into Synapse once it answers again, in order (a note deleted offline that was changed in
Synapse meanwhile is not deleted).

    python tests/e2e/offline_places_checklist.py

Starts its own server on port 8875 with a throw-away data folder. Your library is not touched.
"""

import asyncio
import json
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

PORT = 8875
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-offline-places-e2e-")
results = []

SERVER = f"""
import json, sys
sys.path.insert(0, {str(APP)!r})
from backend import create_app
from backend.config import Config
cfg = Config.from_env({DATA!r})
app = create_app(cfg, start_indexer=False)
ns = app.extensions['synapse'].note_service
ns.save('alpha.md', '# Alpha\\n\\nkept\\n', None)
ns.save('beta.md', '# Beta\\n', None)
ns.save('gone.md', '# Gone\\n', None)
ns.save('busy.md', '# Busy\\n', None)
ns.save('Trips/plan.md', '# Plan\\n', None)
store = app.extensions['synapse'].task_store
ids = {{t: store.create(t)['id'] for t in ('Pack', 'Socks', 'Old task')}}
open({DATA!r} + '/ids.json', 'w').write(json.dumps(ids))
app.run(port={PORT}, threaded=True)
"""


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)


def note(path):
    r = requests.get(f"{BASE}/api/notes/note", params={"path": path}, timeout=5)
    return r.json() if r.ok else None


def tasks():
    return requests.get(f"{BASE}/api/tasks", timeout=5).json()


async def until(fn, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        await asyncio.sleep(0.4)
    return fn()


def row(path):
    return f'.nt-row.note[data-path="{path}"]'


async def menu(page, path, label):
    await page.click(row(path), button="right")
    await page.click(f".menu >> text={label}")


async def open_tasks(page):
    await page.click('#sidebar [data-navigation="tasks.main"]', timeout=20000)
    await page.wait_for_selector(".tv-row", timeout=20000)


async def main():
    ids = json.loads(Path(DATA, "ids.json").read_text())
    profile = tempfile.mkdtemp(prefix="zc-offline-places-profile-")
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            profile, headless=True, viewport={"width": 1300, "height": 800}
        )
        page = ctx.pages[0]
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # Online: offline use on, four notes kept, the Tasks page seen once.
        await page.goto(BASE + "/")
        await page.wait_for_timeout(1200)
        await page.keyboard.press("Control+,")
        await page.click('.settings-nav [data-nav="offline"]')
        await page.click("#set-offline .switch")
        await page.wait_for_function(
            "() => /App and library saved/.test(document.querySelector('[data-offline-title]')?.textContent || '')",
            timeout=60000,
        )
        await page.keyboard.press("Escape")
        await open_tasks(page)
        await page.click('[data-act="open-notes"]')
        for path in ("alpha.md", "beta.md", "gone.md", "busy.md"):
            await page.wait_for_selector(row(path))
            await menu(page, path, "Make Available Offline")
            await page.wait_for_selector(f"{row(path)} .nt-offline", timeout=10000)
        await page.wait_for_timeout(1500)
        for pg in list(ctx.pages):
            await pg.close()

        # Offline: rename, move, delete.
        await ctx.set_offline(True)
        page = await ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(BASE + "/#note=alpha.md")
        await page.wait_for_selector(".cm-content", timeout=20000)
        await menu(page, "alpha.md", "Rename…")
        await page.fill(".dialog input", "Renamed")
        await page.keyboard.press("Enter")
        await page.wait_for_selector(row("Renamed.md"), timeout=10000)
        check(
            "a kept note is renamed offline (and stays open)",
            await page.locator(row("alpha.md")).count() == 0
            and await page.locator(f"{row('Renamed.md')} .nt-offline.pending").count() == 1,
        )
        await menu(page, "beta.md", "Move to folder…")
        await page.click(".menu >> text=Trips")
        await page.wait_for_selector(row("Trips/beta.md"), timeout=10000)
        check("a kept note is moved to a folder offline", await page.locator(row("beta.md")).count() == 0)
        for path in ("gone.md", "busy.md"):
            await menu(page, path, "Move to Trash")
            await page.wait_for_selector(row(path), state="detached", timeout=10000)
        check("kept notes are deleted offline", True)
        await page.click(row("Renamed.md"))
        await page.wait_for_function(
            "() => document.querySelector('.cm-content')?.innerText.includes('kept')", timeout=10000
        )
        await page.click(".cm-content")
        await page.keyboard.press("ControlOrMeta+End")
        await page.keyboard.type("\nafter the rename\n")
        # a task, from the note (Cmd/Ctrl+Enter)
        await page.keyboard.press("ControlOrMeta+Enter")
        await page.fill(".tn-title-input", "Book the train")
        await page.keyboard.press("Enter")
        await page.wait_for_function(
            "() => /\\{\\{task:|Book the train/.test(document.querySelector('.cm-content')?.innerText || '')",
            timeout=10000,
        )
        check("a task is created from inside a note offline", True)
        await page.wait_for_timeout(2500)  # the note saves (here)

        # Tasks: Socks under Pack (Tab), Old task deleted.
        await open_tasks(page)
        await page.locator(".tv-row", has_text="Socks").click()
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(600)
        await page.locator(".tv-row", has_text="Old task").click()
        await page.keyboard.press("Delete")
        await page.wait_for_selector('.tv-row:has-text("Old task")', state="detached", timeout=5000)
        check("tasks are moved and deleted offline", True)

        # A reload, still offline: everything is as it was left.
        await page.reload()
        await page.wait_for_timeout(1500)
        await open_tasks(page)
        same_tasks = (
            await page.locator('.tv-row:has-text("Old task")').count() == 0
            and await page.locator('.tv-row:has-text("Book the train")').count() == 1
        )
        await page.click('[data-act="open-notes"]')
        await page.wait_for_selector(row("Renamed.md"), timeout=10000)
        same_notes = (
            await page.locator(row("Trips/beta.md")).count() == 1
            and await page.locator(row("alpha.md")).count() == 0
            and await page.locator(row("gone.md")).count() == 0
        )
        check("they survive a reload offline", same_tasks and same_notes, f"tasks {same_tasks}, notes {same_notes}")

        # Meanwhile, in Synapse: busy.md changes (so its deletion must not go in).
        n = note("busy.md")
        requests.put(
            f"{BASE}/api/notes/note",
            json={"path": "busy.md", "text": n["text"] + "\nchanged in Synapse\n", "hash": n["hash"]},
            timeout=5,
        )

        await ctx.set_offline(False)
        renamed = await until(lambda: note("Renamed.md"))
        check(
            "the rename goes in, with the edit made after it",
            renamed and "after the rename" in renamed["text"] and note("alpha.md") is None,
            repr(renamed and renamed["text"]),
        )
        check("the move goes in", await until(lambda: note("Trips/beta.md")) and note("beta.md") is None)
        check("the deletion goes in", await until(lambda: note("gone.md") is None))
        check("a note changed in Synapse meanwhile is not deleted", note("busy.md") is not None)
        over = await until(
            lambda: (lambda o: o if any(t["title"] == "Book the train" for t in o["tasks"]) else None)(tasks())
        )
        train = next((t for t in (over or {"tasks": []})["tasks"] if t["title"] == "Book the train"), None)
        check(
            "the task made in the note is in Synapse, shown in the renamed note",
            train and any(r["path"] == "Renamed.md" for r in over["refs"].get(train["id"], [])),
            str(train and over["refs"].get(train["id"])),
        )
        socks = await until(
            lambda: next((t for t in tasks()["tasks"] if t["id"] == ids["Socks"] and t["parent_id"]), None)
        )
        check("the move of a task goes in", socks and socks["parent_id"] == ids["Pack"], str(socks))
        check(
            "the deletion of a task goes in",
            await until(lambda: all(t["id"] != ids["Old task"] for t in tasks()["tasks"])),
        )
        await page.wait_for_timeout(1500)
        check("no page errors", not errors, "; ".join(errors[:3]))
        await ctx.close()
    shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    guard(PORT)
    proc = subprocess.Popen([sys.executable, "-c", SERVER], cwd=APP)
    try:
        for _ in range(100):
            try:
                requests.get(BASE + "/manifest.webmanifest", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        asyncio.run(main())
    finally:
        proc.terminate()
        shutil.rmtree(DATA, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)
