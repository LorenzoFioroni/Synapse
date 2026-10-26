"""Browser checklist for the Metadata Check plugin: its interval setting, Run Now (Settings button
and command palette), ignoring an item's suggestion from the item menu with Undo, stopping from the
item menu and from Settings, and the selected row following a field value that changed (the item
pane's cached copy of an item used to keep old plugin values).

    python tests/e2e/metadata_check_checklist.py      (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8810 with a throw-away data folder; your library is not touched. A
small plugin installed there answers metadata lookups, so no network is needed.
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
PORT = 8810
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-metacheck-e2e-")
SHOTS = os.environ.get("SHOTS")
MOD = "Meta" if sys.platform == "darwin" else "Control"
PID = "metadata-check"
FIELD = f"{PID}.metadata_check"
results = []

# The title the fake source reports: the checklist changes it to see a new suggestion come through.
FAKE = """
import json
from pathlib import Path
from backend.plugins import Plugin

def activate(plugin: Plugin):
    answer = Path(__file__).with_name("answer.json")

    @plugin.metadata_source("fake", title="Fake", order=0)
    def look(query):
        if query["kind"] != "doi":
            return None
        return {"title": json.loads(answer.read_text())["title"], "authors": "Doe, Jane", "year": 2020,
                "doi": query["value"]}
"""


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


def api(path, method="GET", **kw):
    r = requests.request(method, BASE + path, timeout=10, **kw)
    r.raise_for_status()
    return r.json() if r.content else None


def answer(title):
    (Path(DATA) / "plugins" / "fake-metadata" / "answer.json").write_text(json.dumps({"title": title}))


async def wait_for(fn, timeout=12.0):
    end = time.time() + timeout
    while time.time() < end:
        if await fn():
            return True
        await asyncio.sleep(0.2)
    return False


def server_value(item_id):
    fields = api(f"/api/items/{item_id}/fields")["fields"]
    return next((f["value"] for f in fields if f["id"] == FIELD), None)


async def becomes(item, value):
    """Whether the item's value on the server becomes ``value`` (a click's request may still be on its way)."""
    return await wait_for(lambda: asyncio.to_thread(lambda: server_value(item["id"]) == value), timeout=5)


async def main():
    folder = Path(DATA) / "plugins" / "fake-metadata"
    folder.mkdir(parents=True)
    (folder / "plugin.py").write_text(FAKE)
    (folder / "manifest.json").write_text(
        json.dumps(
            {"id": "fake-metadata", "name": "Fake Metadata", "version": "0.1.0", "api": "^1.0", "backend": "plugin.py"}
        )
    )
    answer("The corrected title")
    env = dict(os.environ, SYNAPSE_DATA_DIR=DATA)
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser"],
        cwd=APP,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        try:
            requests.get(BASE, timeout=1)
            break
        except requests.RequestException:
            time.sleep(0.2)
    try:
        api(
            "/api/settings",
            "PUT",
            json={"add_fetch_references": "never", "add_find_pdf": "never", "references_auto": False},
        )
        item = api(
            "/api/items",
            "POST",
            json={"title": "A stored title", "authors": "Doe, Jane", "year": 2020, "doi": "10.1000/checked"},
        )
        api("/api/plugin-host/fake-metadata/enable", "POST")
        api(f"/api/plugin-host/{PID}/enable", "POST")
        end = time.time() + 15
        while time.time() < end and server_value(item["id"]) != "Review: title":
            time.sleep(0.2)
        check(
            "the check flags the changed title", server_value(item["id"]) == "Review: title", server_value(item["id"])
        )

        async with async_playwright() as p:
            b = await p.chromium.launch()
            page = await b.new_page(viewport={"width": 1300, "height": 850})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(BASE)
            await page.wait_for_function(f"window.zcPlugins?.loaded().includes('{PID}')")
            await page.wait_for_selector(".row[data-id]")
            await page.click(f'.row[data-id="{item["id"]}"]')

            async def menu():
                await page.click(".row.selected", button="right")
                labels = await page.eval_on_selector_all(".menu-item", "els => els.map(e => e.textContent.trim())")
                return labels

            async def close_menu():
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(100)

            labels = await menu()
            check("the item menu offers Ignore", "Ignore Metadata Suggestion" in labels, labels)
            await shot(page, "m1-menu")
            await page.click(".menu-item:has-text('Ignore Metadata Suggestion')")
            check("ignoring shows at once on the server", await becomes(item, "Up to date · ignored title"))
            ok = await wait_for(
                lambda: page.evaluate(
                    "[...document.querySelectorAll('.toast, .notification-message')].some(t => t.textContent.includes('Ignoring'))"
                )
            )
            check("a toast with Undo confirms it", ok)

            async def offers(label):
                labels = await menu()
                await close_menu()
                return label in labels

            check(
                "the selected row follows the new value",
                await wait_for(lambda: offers("Stop Ignoring Metadata Suggestion")),
            )
            await page.click(".row.selected", button="right")
            await page.click(".menu-item:has-text('Stop Ignoring Metadata Suggestion')")
            check("Stop Ignoring brings the suggestion back", await becomes(item, "Review: title"))
            check("and the menu offers Ignore again", await wait_for(lambda: offers("Ignore Metadata Suggestion")))

            await page.click(".row.selected", button="right")
            await page.click(".menu-item:has-text('Ignore Metadata Suggestion')")
            await page.keyboard.press(f"{MOD}+Shift+KeyP")
            await page.wait_for_selector(".palette-input")
            await page.keyboard.type("metadata check")
            listed = await page.eval_on_selector_all(".palette-item", "els => els.map(e => e.textContent.trim())")
            check("the palette has Run Now (named once)", "Metadata Check: Run Now" in listed, listed)
            await shot(page, "m2-palette")
            before = api(f"/api/plugins/{PID}/status")["last_checked"]
            await page.click(".palette-item:has-text('Run Now')")
            ok = await wait_for(
                lambda: asyncio.to_thread(lambda: api(f"/api/plugins/{PID}/status")["last_checked"] > before)
            )
            check("Run Now checks again although nothing is due", ok)
            check(
                "the same suggestion stays ignored",
                server_value(item["id"]) == "Up to date · ignored title",
                server_value(item["id"]),
            )

            # Settings: the interval, Run Now and the ignored list
            await page.keyboard.press(f"{MOD}+,")
            await page.wait_for_selector("#set-plugins")
            await page.click('.settings-nav [data-nav="plugins"]')
            await page.click(f'.settings-nav [data-nav="plugin-{PID}"]')
            sec = f"#set-plugin-{PID}"
            await page.wait_for_selector(f"{sec} [data-mc-run]")
            await page.wait_for_selector(f"{sec} .mc-ignored")
            text = await page.text_content(sec)
            check(
                "Settings lists the ignored suggestion with what it was",
                "A stored title" in text and "title: The corrected title" in text,
                text,
            )
            check("Settings has the interval", "Days between checks" in text, text)
            await shot(page, "m3-settings")
            inp = page.locator(f'{sec} [data-pset="interval_days"], {sec} input[type="number"]').first
            await inp.fill("7")
            await inp.press("Tab")
            ok = await wait_for(
                lambda: asyncio.to_thread(
                    lambda: (
                        api("/api/plugin-host")["plugins"]
                        and next(p for p in api("/api/plugin-host")["plugins"] if p["id"] == PID)["values"][
                            "interval_days"
                        ]
                        == 7
                    )
                )
            )
            check("the interval is saved", ok)

            answer("Yet another title")
            before = api(f"/api/plugins/{PID}/status")["last_checked"]
            await page.click(f"{sec} [data-mc-run]")
            ok = await wait_for(lambda: asyncio.to_thread(lambda: server_value(item["id"]) == "Review: title"))
            check("a different suggestion is not hidden by the old ignore", ok, server_value(item["id"]))

            api(f"/api/plugins/{PID}/ignore", "POST", json={"ids": [item["id"]]})
            await page.wait_for_selector(f"{sec} .mc-ignored")
            await page.click(f"{sec} [data-mc-unignore]")
            ok = await wait_for(lambda: page.evaluate(f"!document.querySelector('{sec} .mc-ignored')"))
            check("Stop Ignoring in Settings empties the list", ok)
            check("and the suggestion is back", await becomes(item, "Review: title"))
            check("no page errors", not errors, errors)
            await b.close()
    finally:
        srv.terminate()
        srv.wait(timeout=10)
        shutil.rmtree(DATA, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _guard import guard  # noqa: E402

guard(PORT)
asyncio.run(main())
