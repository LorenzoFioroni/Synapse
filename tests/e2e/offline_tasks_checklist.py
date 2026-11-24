"""Browser checklist for plugins' offline writes (plugin API 1.16), through Tasks (docs/offline.md):
with the browser offline, ticking a task and adding one wait on the device and survive a reload;
back online they go in, merged with a change made in Synapse meanwhile.

    python tests/e2e/offline_tasks_checklist.py

Starts its own server on port 8874 with a throw-away data folder. Your library is not touched.
"""

import asyncio
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

PORT = 8874
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-offline-tasks-e2e-")
results = []

SERVER = f"""
import sys
sys.path.insert(0, {str(APP)!r})
from backend import create_app
from backend.config import Config
cfg = Config.from_env({DATA!r})
app = create_app(cfg, start_indexer=False)
store = app.extensions['synapse'].task_store
t = store.create('Pack')
store.create('Call the hotel')
open({DATA!r} + '/id.txt', 'w').write(t['id'])
app.run(port={PORT}, threaded=True)
"""


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)


def tasks():
    return requests.get(f"{BASE}/api/tasks", timeout=5).json()["tasks"]


async def until(fn, timeout=25.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        await asyncio.sleep(0.4)
    return fn()


async def open_tasks(page):
    await page.click('#sidebar [data-navigation="tasks.main"]', timeout=20000)
    await page.wait_for_selector(".tv-row", timeout=20000)


async def main():
    pack = Path(DATA, "id.txt").read_text().strip()
    profile = tempfile.mkdtemp(prefix="zc-offline-tasks-profile-")
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            profile, headless=True, viewport={"width": 1300, "height": 800}
        )
        page = ctx.pages[0]
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
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
        await page.wait_for_timeout(1000)
        for pg in list(ctx.pages):
            await pg.close()

        # Offline: the tasks are there; tick one, add one.
        await ctx.set_offline(True)
        page = await ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(BASE + "/")
        await page.wait_for_timeout(1500)
        await open_tasks(page)
        check("tasks show offline", await page.locator(".tv-row", has_text="Call the hotel").count() == 1)
        await page.locator(".tv-row", has_text="Pack").click()
        await page.keyboard.press(" ")
        await page.wait_for_selector('.tv-row.done:has-text("Pack")', timeout=5000)
        await page.locator(".tv-quick-input").fill("Buy tickets")
        await page.keyboard.press("Enter")
        await page.wait_for_selector('.tv-row:has-text("Buy tickets")', timeout=5000)
        check("ticking and adding work offline", True)
        await page.reload()
        await page.wait_for_timeout(1500)
        await open_tasks(page)
        # the done task leaves the list of open ones; the new one is there
        kept = (
            await page.locator('.tv-row:has-text("Pack")').count() == 0
            and await page.locator('.tv-row:has-text("Buy tickets")').count() == 1
        )
        check("they survive a reload offline", kept)

        # Meanwhile, in Synapse: another field of the same task, and another task.
        requests.patch(f"{BASE}/api/tasks/{pack}", json={"title": "Pack the car"}, timeout=5)
        hotel = next(t for t in tasks() if t["title"] == "Call the hotel")
        requests.patch(f"{BASE}/api/tasks/{hotel['id']}", json={"title": "Call the hotel today"}, timeout=5)

        await ctx.set_offline(False)
        done = await until(lambda: next((t for t in tasks() if t["id"] == pack and t["status"] == "done"), None))
        check("the tick goes in, merged with Synapse's change", done and done["title"] == "Pack the car", str(done))
        made = await until(lambda: [t for t in tasks() if t["title"] == "Buy tickets"])
        check("the task added offline is in Synapse once", made and len(made) == 1, str(made))
        await page.wait_for_selector('.tv-row:has-text("Call the hotel today")', timeout=15000)
        check("the page shows Synapse's tasks again", True)
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
