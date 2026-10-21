"""Browser checklist for Settings → Plugins: the example plugin is listed off, turning it on asks
first and lists what it may do, its settings are drawn from its manifest and saved, its route
answers, its log opens, a plugin that cannot start shows why, and turning a plugin on in one
window shows in another.

    python tests/e2e/plugins_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server (python server.py) on port 8798 with a throw-away data folder; your
library is not touched.
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

PORT = 8798
BASE = f"http://127.0.0.1:{PORT}"
DATA = Path(tempfile.mkdtemp(prefix="zc-plugins-e2e-"))
SHOTS = os.environ.get("SHOTS")
results = []
mod = "Meta" if sys.platform == "darwin" else "Control"


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


def api(path):
    return requests.get(BASE + path, timeout=5).json()


def plugin(pid):
    return next(p for p in api("/api/plugin-host")["plugins"] if p["id"] == pid)


ROW = '[data-plugin="recently-added"]'


async def open_plugins(page):
    await page.keyboard.press(f"{mod}+,")
    await page.wait_for_selector("#set-plugins")
    await page.click('.settings-nav [data-nav="plugins"]')
    await page.wait_for_selector(f"#set-plugins {ROW}")
    await page.wait_for_timeout(600)  # smooth scroll to the section


async def main():
    guard(PORT)
    plugins = DATA / "plugins"
    shutil.copytree(APP / "docs" / "examples" / "recently-added", plugins / "recently-added")
    broken = plugins / "broken-one"
    broken.mkdir()
    (broken / "manifest.json").write_text(
        json.dumps({"id": "broken-one", "name": "Broken One", "version": "0.1", "api": "^1.0", "backend": "p.py"})
    )
    (broken / "p.py").write_text("def activate(plugin):\n    raise RuntimeError('needs a key')\n")
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
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1300, "height": 950})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(BASE)
            await page.wait_for_selector(".row[data-id], .empty-state, #item-list", timeout=15000)
            await open_plugins(page)

            desc = await page.text_content("[data-plugins-desc]")
            check("the section says where plugins go", str(plugins) in desc, desc)
            names = await page.eval_on_selector_all("#set-plugins .p-name", "els => els.map(e => e.textContent)")
            check("both plugins are listed", names == ["Broken One", "Recently Added"], names)
            toggle = f"{ROW} [data-ptoggle]"
            check("a new plugin is off", not await page.is_checked(toggle))
            perms = await page.text_content(f"{ROW} .p-perms")
            check("what it may do is shown", "Reads your library" in perms and "Offers tools to Claude" in perms, perms)
            await shot(page, "p1-list")

            # turning on asks first; Cancel leaves it off
            await page.click(f"{ROW} .switch")
            await page.wait_for_selector(".dialog")
            detail = await page.text_content(".dialog")
            check(
                "turning on asks first and names the access", "full access" in detail and "Reads your library" in detail
            )
            await shot(page, "p2-confirm")
            await page.click(".dialog button:has-text('Cancel')")
            await page.wait_for_timeout(300)
            check("Cancel leaves it off", not await page.is_checked(toggle) and not plugin("recently-added")["enabled"])

            await page.click(f"{ROW} .switch")
            await page.click(".dialog button:has-text('Turn On')")
            ok = await wait_for(lambda: asyncio.to_thread(lambda: plugin("recently-added")["running"]))
            check("Turn On starts it", ok)
            check("the switch shows it on", await wait_for(lambda: page.is_checked(toggle)))

            # its settings, drawn from the manifest
            await page.click(f"{ROW} [data-pmore]")
            await page.wait_for_selector(f'{ROW} [data-pset="keep_days"]')
            await page.fill(f'{ROW} [data-pset="keep_days"]', "30")
            await page.press(f'{ROW} [data-pset="keep_days"]', "Tab")
            ok = await wait_for(
                lambda: asyncio.to_thread(lambda: plugin("recently-added")["values"]["keep_days"] == 30)
            )
            check("a number setting is saved", ok)
            await page.fill(f'{ROW} [data-pset="skip_tags"]', "draft, old")
            await page.press(f'{ROW} [data-pset="skip_tags"]', "Tab")
            ok = await wait_for(
                lambda: asyncio.to_thread(lambda: plugin("recently-added")["values"]["skip_tags"] == ["draft", "old"])
            )
            check("a list setting is saved", ok)
            await page.fill(f'{ROW} [data-pset="keep_days"]', "0")
            await page.press(f'{ROW} [data-pset="keep_days"]', "Tab")
            ok = await wait_for(lambda: page.evaluate("!!document.querySelector('.toast')"))
            check("a value out of range is refused", ok and plugin("recently-added")["values"]["keep_days"] == 30)
            check(
                "the field shows the saved value again",
                await wait_for(
                    lambda: page.eval_on_selector(f'{ROW} [data-pset="keep_days"]', "e => e.value === '30'")
                ),
            )
            offers = await page.text_content(f"{ROW} .p-offers")
            check(
                "what it offers is listed", "recently-added__recently_added" in offers and "/recent" in offers, offers
            )
            await shot(page, "p3-details")

            # it works: an item added in the library shows up in its route
            requests.post(BASE + "/api/items", json={"title": "Seen by the plugin"}, timeout=5)
            ok = await wait_for(
                lambda: asyncio.to_thread(
                    lambda: (
                        [i["title"] for i in api("/api/plugins/recently-added/recent")["items"]]
                        == ["Seen by the plugin"]
                    )
                )
            )
            check("its route answers with what it stored", ok)

            await page.click(f"{ROW} [data-plog]")
            await page.wait_for_selector(".plugin-log")
            log = await page.text_content(".plugin-log")
            check("the log opens", "Started (app" in log and "Settings changed: keep_days" in log, log[-300:])
            await shot(page, "p4-log")
            await page.click(".dialog button:has-text('Close')")

            # a plugin that cannot start
            brow = '[data-plugin="broken-one"]'
            await page.click(f"{brow} .switch")
            await page.click(".dialog button:has-text('Turn On')")
            ok = await wait_for(lambda: page.is_visible(f"{brow} .form-error"))
            err = await page.text_content(f"{brow} .form-error") if ok else ""
            check("a plugin that cannot start says why", "RuntimeError: needs a key" in err, err)
            check("and stays off", not await page.is_checked(f"{brow} [data-ptoggle]"))
            await shot(page, "p5-broken")

            # another window hears about it
            other = await browser.new_page(viewport={"width": 1300, "height": 950})
            await other.goto(BASE)
            await other.wait_for_selector(".row[data-id], .empty-state, #item-list", timeout=15000)
            await open_plugins(other)
            await page.click(f"{ROW} .switch")  # turn it off in the first window
            ok = await wait_for(
                lambda: other.evaluate(f"!document.querySelector('{ROW} [data-ptoggle]').checked"), timeout=12
            )
            check("turning it off shows in another window", ok)
            check(
                "off: its route is gone", requests.get(BASE + "/api/plugins/recently-added/recent").status_code == 404
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
