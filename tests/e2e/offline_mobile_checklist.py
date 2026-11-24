"""Browser checklist for offline use on touch devices (docs/offline.md): an Android phone (Chromium
with a phone's screen, the phone layout) and an iPad (WebKit, Safari's engine, with an iPad's
screen: the desktop layout used by touch). On each: offline use is turned on in Settings, notes
are kept by touch (a long press, the ⋯ menu), and offline a kept note opens, is edited, renamed and
deleted, a note is created, and tasks are added, ticked and made from a note; back online it all
goes into Synapse.

    python tests/e2e/offline_mobile_checklist.py            # both
    python tests/e2e/offline_mobile_checklist.py android    # or: ipad

Starts its own server on port 8876 with a throw-away data folder. Your library is not touched.
WebKit stands in for Safari here: it runs the same engine, but not iPadOS itself.
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

PORT = 8876
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-offline-mobile-e2e-")
SHOTS = Path(tempfile.gettempdir(), "zc-offline-mobile-shots")
results = []

SERVER = f"""
import sys
sys.path.insert(0, {str(APP)!r})
from backend import create_app
from backend.config import Config
cfg = Config.from_env({DATA!r})
app = create_app(cfg, start_indexer=False)
app.run(port={PORT}, threaded=True)
"""


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)


def api(path, method="GET", body=None):
    r = requests.request(method, BASE + path, json=body, timeout=10)
    return r.json() if r.ok else None


def seed(tag):
    """The notes and tasks of one device's run (each run has its own names)."""
    for path, text in (
        (f"{tag}/trip.md", "# Trip\n\nalpha\n"),
        (f"{tag}/gone.md", "# Gone\n"),
        (f"{tag}/other.md", "# Other\n"),
    ):
        requests.put(
            BASE + "/api/notes/note", json={"path": path, "text": text, "create": True}, timeout=10
        ).raise_for_status()
    for title in (f"{tag} pack", f"{tag} call"):
        requests.post(BASE + "/api/tasks", json={"title": title}, timeout=10).raise_for_status()


def task(title):
    return next((t for t in api("/api/tasks")["tasks"] if t["title"] == title), None)


async def until(fn, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        await asyncio.sleep(0.4)
    return fn()


async def long_press(page, selector):
    """A finger held still on ``selector`` (ui/longpress.ts turns it into the context menu)."""
    await page.wait_for_timeout(400)  # a list that just repainted settles
    await page.locator(selector).first.scroll_into_view_if_needed()
    await page.eval_on_selector(
        selector,
        """el => { const r = el.getBoundingClientRect(), x = r.left + Math.min(40, r.width / 2), y = r.top + r.height / 2;
           const o = { bubbles: true, cancelable: true, pointerId: 7, pointerType: 'touch', isPrimary: true, clientX: x, clientY: y };
           el.dispatchEvent(new PointerEvent('pointerdown', o));
           setTimeout(() => el.dispatchEvent(new PointerEvent('pointerup', o)), 700); }""",
    )
    await page.wait_for_timeout(1300)  # the tap right after lifting the finger is swallowed (as on a device)


async def menu_item(page, label):
    await page.locator(".menu .menu-item", has_text=label).first.click()


async def shot(page, name):
    SHOTS.mkdir(exist_ok=True)
    await page.screenshot(path=str(SHOTS / f"{name}.png"))


async def turn_on(page, phone):
    if phone:
        await page.locator('.ph-screen:not([hidden]) [data-act="settings"]').first.click()
        await menu_item(page, "Settings")
    else:
        await page.keyboard.press("Control+,")
    await page.click('#settings-view [data-nav="offline"]')
    await page.click("#set-offline .switch")
    await page.wait_for_function(
        "() => /App and library saved/.test(document.querySelector('[data-offline-title]')?.textContent || '')",
        timeout=90000,
    )


async def run(p, device, tag, errors):
    phone = device == "android"
    profile = tempfile.mkdtemp(prefix=f"zc-offline-{device}-")
    if phone:
        spec = {k: v for k, v in p.devices["Pixel 7"].items() if k != "default_browser_type"}
        ctx = await p.chromium.launch_persistent_context(profile, headless=True, **spec)
    else:
        spec = {k: v for k, v in p.devices["iPad Pro 11 landscape"].items() if k != "default_browser_type"}
        ctx = await p.webkit.launch_persistent_context(profile, headless=True, **spec)
    name = "Android phone" if phone else "iPad"
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"{device}: {e}"))
    trip, gone, other = f"{tag}/trip.md", f"{tag}/gone.md", f"{tag}/other.md"
    try:
        await page.goto(BASE + "/")
        await page.wait_for_selector("#phone .ph-tabbar" if phone else "#sidebar", timeout=20000)
        await page.wait_for_timeout(1000)
        await turn_on(page, phone)
        check(f"{name}: offline use turns on in Settings", True)
        await page.goto(BASE + "/")

        # Keep two notes by touch, see the Tasks page once.
        if phone:
            await page.wait_for_selector("#phone .ph-tabbar", timeout=20000)
            await page.locator('[data-slot="notes"]').click()
            await page.locator(f'.ph-folder-row[data-folder="{tag}"]').click()
            row = f'.ph-screen:not([hidden]) .ph-note-row[data-note="{trip}"]'
            await page.wait_for_selector(row, timeout=10000)
            await long_press(page, row)
            await menu_item(page, "Make Available Offline")
            await page.wait_for_selector(f"{row} .ph-offline-dot", timeout=15000)
            check(f"{name}: a note is kept by a long press", True)
            await page.locator(f'.ph-screen:not([hidden]) .ph-note-row[data-note="{gone}"]').click()
            await page.wait_for_selector(".ph-screen:not([hidden]) .ph-note", timeout=10000)
            await page.locator('.ph-screen:not([hidden]) [data-act="more"]').click()
            await menu_item(page, "Make Available Offline")
            await page.wait_for_function(
                f"() => !!JSON.parse(localStorage.getItem('zc.offline.notes') || '{{}}').notes?.includes({json.dumps(gone)})",
                timeout=10000,
            )
            check(f"{name}: a note is kept by its ⋯ menu", True)
            await page.wait_for_timeout(1500)
        else:
            await page.click('[data-act="open-notes"]')
            for path in (trip, gone):
                row = f'.nt-row.note[data-path="{path}"]'
                await page.wait_for_selector(row, timeout=10000)
                await long_press(page, row)
                await menu_item(page, "Make Available Offline")
                await page.wait_for_selector(f"{row} .nt-offline", timeout=15000)
            check(f"{name}: notes are kept by a long press", True)
            await page.click('#sidebar [data-navigation="tasks.main"]')
            await page.wait_for_selector(".tv-row", timeout=20000)
        await page.wait_for_timeout(1500)

        # Offline, every earlier page closed (WebKit ends when its last page closes: the new one first).
        await ctx.set_offline(True)
        old = list(ctx.pages)
        page = await ctx.new_page()
        for pg in old:
            await pg.close()
        page.on("pageerror", lambda e: errors.append(f"{device}: {e}"))
        await page.goto(BASE + "/")
        await page.wait_for_selector("#phone .ph-tabbar" if phone else "#sidebar", timeout=30000)
        check(f"{name}: the app opens offline", True)
        if phone:
            await page.locator('[data-slot="notes"]').click()
            await page.locator(f'.ph-folder-row[data-folder="{tag}"]').click()
            row = f'.ph-screen:not([hidden]) .ph-note-row[data-note="{trip}"]'
            await page.wait_for_selector(row, timeout=10000)
            check(
                f"{name}: a note not kept is dimmed",
                await page.locator(
                    f'.ph-screen:not([hidden]) .ph-note-row.offline-missing[data-note="{other}"]'
                ).count()
                == 1,
            )
            await page.locator(row).click()
            await page.wait_for_function(
                "() => /alpha/.test(document.querySelector('.ph-screen:not([hidden]) .ph-note')?.textContent || '')",
                timeout=15000,
            )
            check(f"{name}: a kept note opens offline", True)
            await page.locator('.ph-screen:not([hidden]) [data-act="edit"]').click()
            await page.wait_for_selector(".ph-screen:not([hidden]) .cm-content", timeout=10000)
            await page.locator(".ph-screen:not([hidden]) .cm-content").click()
            await page.keyboard.press("ControlOrMeta+End")
            for line in ("", "written offline", ""):
                await page.keyboard.type(line)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(400)  # CodeMirror reads Android's Enter a moment later
            # a task from the note: the keyboard bar's task button
            await page.keyboard.type("Book the train")
            await page.locator('.ph-screen:not([hidden]) .ph-keybar [aria-label="Make this line a task"]').click()
            await page.wait_for_selector(".tn-title-input", timeout=10000)
            await page.keyboard.press("Enter")
            await page.wait_for_function(
                "() => /Book the train/.test(document.querySelector('.ph-screen:not([hidden]) .cm-content')?.textContent || '')",
                timeout=10000,
            )
            await page.wait_for_timeout(2500)
            await page.locator('.ph-screen:not([hidden]) [data-act="more"]').click()
            await menu_item(page, "Rename…")
            await page.fill(".dialog input", "Trip renamed")
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(1500)
            await page.goto(BASE + "/")  # back to the list, as a reload
            await page.locator('[data-slot="notes"]').click()
            await page.locator(f'.ph-folder-row[data-folder="{tag}"]').click()
            renamed = f'.ph-screen:not([hidden]) .ph-note-row[data-note="{tag}/Trip renamed.md"]'
            await page.wait_for_selector(renamed, timeout=10000)
            check(
                f"{name}: a kept note is edited and renamed offline",
                await page.locator(f"{renamed} .ph-offline-dot.pending").count() == 1,
            )
            await page.locator(f'.ph-screen:not([hidden]) .ph-note-row[data-note="{gone}"]').click()
            await page.wait_for_selector(".ph-screen:not([hidden]) .ph-note", timeout=10000)
            await page.locator('.ph-screen:not([hidden]) [data-act="more"]').click()
            await menu_item(page, "Delete Note")
            await page.wait_for_selector(
                f'.ph-screen:not([hidden]) .ph-note-row[data-note="{gone}"]', state="detached", timeout=10000
            )
            check(f"{name}: a kept note is deleted offline", True)
            # Tasks: the pinned Tasks tab, or Tasks among the plugins
            await page.locator('[data-slot="pin"]').click()
            if not await page.locator(".tp-add input").count():
                await page.locator('[data-slot="plugins"]').click()
                await page.locator(".ph-screen:not([hidden])", has_text="Tasks").locator("text=Tasks").first.click()
            await page.wait_for_selector(".tp-add input", timeout=10000)
            await page.locator('.tp-segments [data-seg="all"], .tp-segments [role="tab"]').last.click()
            await page.fill(".tp-add input", f"{tag} offline task")
            await page.keyboard.press("Enter")
            await page.wait_for_selector(f'.tp-row:has-text("{tag} offline task")', timeout=10000)
            await page.locator(f'.tp-row:has-text("{tag} pack") .tp-check').click()
            await page.wait_for_timeout(800)
            check(f"{name}: tasks are added and ticked offline", True)
        else:
            await page.click('[data-act="open-notes"]')
            row = f'.nt-row.note[data-path="{trip}"]'
            await page.wait_for_selector(row, timeout=10000)
            check(
                f"{name}: a note not kept is dimmed",
                await page.locator(f'.nt-row.note.offline-missing[data-path="{other}"]').count() == 1,
            )
            await page.locator(row).click()
            await page.wait_for_function(
                "() => document.querySelector('.cm-content')?.innerText.includes('alpha')", timeout=15000
            )
            check(f"{name}: a kept note opens offline", True)
            await page.locator(".cm-content").click()
            await page.keyboard.press("ControlOrMeta+End")
            await page.keyboard.type("\nwritten offline\n- [ ] Passport\nCharger")  # Enter continues the list
            # the checkboxes into tasks (selected, then the task command), offline
            await page.keyboard.press("Shift+ArrowUp")
            await page.keyboard.press("Shift+Home")
            await page.keyboard.press("Meta+Enter")  # CodeMirror on iOS: ⌘
            await page.wait_for_function(
                "() => (document.querySelector('.cm-content')?.innerText.match(/Passport|Charger/g) || []).length >= 2 && !/\\[ \\]/.test(document.querySelector('.cm-content')?.innerText || '')",
                timeout=10000,
            )
            check(f"{name}: checkboxes become tasks offline", True)
            await page.wait_for_timeout(2500)
            await long_press(page, row)
            await menu_item(page, "Rename…")
            await page.fill(".dialog input", "Trip renamed")
            await page.keyboard.press("Enter")
            renamed = f'.nt-row.note[data-path="{tag}/Trip renamed.md"]'
            await page.wait_for_selector(renamed, timeout=10000)
            check(
                f"{name}: a kept note is edited and renamed offline",
                await page.locator(f"{renamed} .nt-offline.pending").count() == 1,
            )
            await long_press(page, f'.nt-row.note[data-path="{gone}"]')
            await menu_item(page, "Move to Trash")
            await page.wait_for_selector(f'.nt-row.note[data-path="{gone}"]', state="detached", timeout=10000)
            check(f"{name}: a kept note is deleted offline", True)
            await page.click('#sidebar [data-navigation="tasks.main"]')
            await page.wait_for_selector(".tv-row", timeout=20000)
            await page.locator(".tv-quick-input").fill(f"{tag} offline task")
            await page.keyboard.press("Enter")
            await page.wait_for_selector(f'.tv-row:has-text("{tag} offline task")', timeout=10000)
            await long_press(page, f'.tv-row:has-text("{tag} call")')
            await menu_item(page, "Delete")
            await page.wait_for_selector(f'.tv-row:has-text("{tag} call")', state="detached", timeout=10000)
            check(f"{name}: tasks are added and deleted offline", True)

        # Back online: everything goes in.
        await ctx.set_offline(False)
        await page.wait_for_timeout(500)
        await page.evaluate("window.dispatchEvent(new Event('online'))")
        renamed_note = await until(lambda: api(f"/api/notes/note?path={tag}/Trip%20renamed.md"), 40)
        check(
            f"{name}: the edit and the rename go into Synapse",
            renamed_note and "written offline" in renamed_note["text"] and not api(f"/api/notes/note?path={trip}"),
            repr(renamed_note and renamed_note["text"]),
        )
        check(
            f"{name}: the deletion goes into Synapse", await until(lambda: api(f"/api/notes/note?path={gone}") is None)
        )
        check(f"{name}: the task added offline is in Synapse", await until(lambda: task(f"{tag} offline task")))
        if phone:
            check(
                f"{name}: the tick goes into Synapse",
                await until(lambda: (task(f"{tag} pack") or {}).get("status") == "done"),
            )
            check(f"{name}: the task made in the note is in Synapse", await until(lambda: task("Book the train")))
        else:
            check(f"{name}: the deletion of a task goes into Synapse", await until(lambda: task(f"{tag} call") is None))
            check(
                f"{name}: the tasks made from checkboxes are in Synapse",
                await until(
                    lambda: (
                        task("Passport")
                        and task("Charger")
                        and "[ ]"
                        not in (api(f"/api/notes/note?path={tag}/Trip%20renamed.md") or {"text": "[ ]"})["text"]
                    )
                ),
            )
    except Exception as e:
        check(f"{name}: the run finished", False, str(e).splitlines()[0])
        try:
            await shot(page, f"{device}-failure")
            print(f"  screenshot: {SHOTS / f'{device}-failure.png'}")
        except Exception:
            pass
    finally:
        await ctx.close()
        shutil.rmtree(profile, ignore_errors=True)


async def main(devices):
    async with async_playwright() as p:
        for device in devices:
            errors = []
            seed(device)
            await run(p, device, device, errors)
            check(f"{device}: no page errors", not errors, "; ".join(errors[:3]))


if __name__ == "__main__":
    wanted = [a for a in sys.argv[1:] if a in ("android", "ipad")] or ["android", "ipad"]
    guard(PORT)
    proc = subprocess.Popen([sys.executable, "-c", SERVER], cwd=APP)
    try:
        for _ in range(100):
            try:
                requests.get(BASE + "/manifest.webmanifest", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        requests.post(BASE + "/api/plugin-host/tasks/enable", timeout=10)
        asyncio.run(main(wanted))
    finally:
        proc.terminate()
        shutil.rmtree(DATA, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)
