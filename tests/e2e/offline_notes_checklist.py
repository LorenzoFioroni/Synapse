"""Browser checklist for notes kept offline (docs/offline.md): chosen notes open and save with the
browser offline, a note can be created offline, and once back online the offline versions go into
the note's history, merged with what changed meanwhile; overlapping changes ask, and "Fix by hand"
puts Git's conflict markers in the note.

    python tests/e2e/offline_notes_checklist.py

Starts its own server on port 8873 with a throw-away data folder. Your library is not touched.
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

PORT = 8873
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-offline-notes-e2e-")
results = []

SERVER = f"""
import sys
sys.path.insert(0, {str(APP)!r})
from backend import create_app
from backend.config import Config
cfg = Config.from_env({DATA!r})
app = create_app(cfg, start_indexer=False)
ns = app.extensions['synapse'].note_service
ns.save('trip.md', '# Trip\\n\\nalpha\\n\\nbeta\\n\\ngamma\\n', None)
ns.save('clash.md', '# Clash\\n\\nline\\n', None)
ns.save('other.md', '# Other\\n', None)
app.run(port={PORT}, threaded=True)
"""


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)


def note(path):
    return requests.get(f"{BASE}/api/notes/note", params={"path": path}, timeout=5).json()


def server_edit(path, old, new):
    n = note(path)
    r = requests.put(
        f"{BASE}/api/notes/note", json={"path": path, "text": n["text"].replace(old, new), "hash": n["hash"]}, timeout=5
    )
    assert r.ok, r.text


async def keep(page, path):
    await page.click(f'.nt-row.note[data-path="{path}"]', button="right")
    await page.click(".menu >> text=Make Available Offline")
    await page.wait_for_selector(f'.nt-row.note[data-path="{path}"] .nt-offline', timeout=10000)


async def type_end(page, text):
    await page.click(".cm-content")
    await page.keyboard.press("ControlOrMeta+End")
    await page.keyboard.type(text)


async def until(fn, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        await asyncio.sleep(0.4)
    return fn()


async def main():
    profile = tempfile.mkdtemp(prefix="zc-offline-notes-profile-")
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            profile, headless=True, viewport={"width": 1300, "height": 800}
        )
        page = ctx.pages[0]
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # Online: turn offline use on, keep two notes.
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
        await page.click('[data-act="open-notes"]')
        await page.wait_for_selector('.nt-row.note[data-path="trip.md"]')
        await keep(page, "trip.md")
        await keep(page, "clash.md")
        check("kept notes are marked", await page.locator(".nt-row.note .nt-offline").count() == 2)
        await page.wait_for_timeout(1500)
        for pg in list(ctx.pages):
            await pg.close()

        # Offline, every window closed: kept notes open and save here.
        await ctx.set_offline(True)
        page = await ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(BASE + "/#note=trip.md")
        await page.wait_for_selector(".cm-content", timeout=20000)
        await page.wait_for_function(
            "() => document.querySelector('.cm-content')?.innerText.includes('alpha')", timeout=10000
        )
        check("a kept note opens offline", True)
        check(
            "a note not kept is dimmed",
            await page.locator('.nt-row.note.offline-missing[data-path="other.md"]').count() == 1,
        )
        await type_end(page, "\noffline line\n")
        await page.wait_for_selector('.nt-row.note[data-path="trip.md"] .nt-offline.pending', timeout=10000)
        check("an offline save waits as a local version", True)
        await page.click('.nt-row.note[data-path="clash.md"]')
        await page.wait_for_function(
            "() => document.querySelector('.cm-content')?.innerText.includes('line')", timeout=10000
        )
        await type_end(page, "\nmine offline\n")
        await page.wait_for_selector('.nt-row.note[data-path="clash.md"] .nt-offline.pending', timeout=10000)

        # A new note, offline.
        await page.click('#notes-view [data-act="new-note"]')
        await page.fill(".dialog input", "Offline idea")
        await page.keyboard.press("Enter")
        await page.wait_for_function(
            "() => document.querySelector('.cm-content')?.innerText.includes('Offline idea')", timeout=10000
        )
        await type_end(page, "written on the train\n")
        await page.wait_for_selector('.nt-row.note[data-path="Offline idea.md"] .nt-offline.pending', timeout=10000)
        check("a note can be created offline", True)

        # Meanwhile, in Synapse: trip.md elsewhere in the note, clash.md on the same line.
        server_edit("trip.md", "alpha", "ALPHA")
        server_edit("clash.md", "line", "server line")

        # Back online: the edits go in.
        await page.click('.nt-row.note[data-path="trip.md"]')
        await page.wait_for_timeout(500)
        await ctx.set_offline(False)
        merged = await until(lambda: "offline line" in note("trip.md")["text"] and note("trip.md")["text"], 30)
        check(
            "offline edits are merged with Synapse's",
            merged and "ALPHA" in merged and "offline line" in merged,
            repr(merged),
        )
        hist = requests.get(f"{BASE}/api/notes/history", params={"path": "trip.md"}, timeout=5).json()["entries"]
        check(
            "they are in the note's history as offline edits",
            any("offline" in e["summary"] for e in hist),
            str([e["summary"] for e in hist]),
        )
        created = await until(
            lambda: requests.get(f"{BASE}/api/notes/note", params={"path": "Offline idea.md"}, timeout=5).ok, 20
        )
        check(
            "the note created offline is in Synapse",
            created and "written on the train" in note("Offline idea.md")["text"],
        )
        await page.wait_for_function(
            "() => document.querySelector('.cm-content')?.innerText.includes('ALPHA')", timeout=15000
        )
        check("the open note shows the merged text", True)

        # The overlapping one asks; Fix by hand puts the conflict markers in the note.
        await page.click('.nt-row.note[data-path="clash.md"]')
        await page.wait_for_selector(".dialog >> text=Offline edits and changes in Synapse overlap", timeout=15000)
        check("an overlapping change asks", True)
        await page.click(".dialog-footer .btn:has-text('Fix by hand')")
        text = await until(lambda: "<<<<<<<" in note("clash.md")["text"] and note("clash.md")["text"], 15)
        check(
            "Fix by hand saves the conflict markers to edit",
            text and "server line" in text and "mine offline" in text,
            repr(text),
        )
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
