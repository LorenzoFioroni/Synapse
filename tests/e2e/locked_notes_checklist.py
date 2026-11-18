"""Browser checklist for locked notes (views/notes/lockScreen.ts, backend/notes/locks.py): locking a
note from its menu keeps its tab open, a reload shows the lock in the tab, a wrong password keeps it
locked, the right one opens it, closing its tab locks it again, an embed of it shows only that it is
locked, Settings → Notes offers to change the password, and the lock fits a phone.

    python tests/e2e/locked_notes_checklist.py      (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8871 with a throw-away data folder; your library is not touched.
"""

import asyncio
import os
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

PORT = 8871
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-locked-e2e-")
SHOTS = os.environ.get("SHOTS")
MOD = "Meta" if sys.platform == "darwin" else "Control"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


async def secret_shown(page):
    return await page.evaluate(
        "document.body.innerText.includes('zebracorn') || !!document.querySelector('.cm-content')?.textContent.includes('zebracorn')"
    )


async def main():
    guard(PORT)
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", DATA],
        cwd=APP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    s = requests.Session()
    s.headers["Origin"] = BASE

    def api(path, method="GET", **kw):
        r = s.request(method, BASE + path, timeout=15, **kw)
        r.raise_for_status()
        return r.json() if r.content else None

    try:
        for _ in range(60):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                break
            except requests.RequestException:
                time.sleep(0.25)
        api("/api/notes/locks/password", "PUT", json={"password": "hunter22"})
        api(
            "/api/notes/note",
            "POST",
            json={"path": "secret.md", "title": "Diary", "text": "# Diary\n\nThe zebracorn plan.\n"},
        )
        api(
            "/api/notes/note",
            "POST",
            json={"path": "public.md", "title": "Public", "text": "# Public\n\n![[secret]]\n"},
        )

        async with async_playwright() as pw:
            b = await pw.chromium.launch()
            page = await b.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            # 423 and 403 answers are expected here; the browser logs them as failed resources
            page.on(
                "console",
                lambda m: (
                    errors.append(m.text)
                    if m.type == "error" and "status of 423" not in m.text and "status of 403" not in m.text
                    else None
                ),
            )
            await page.goto(BASE + "/")
            await page.wait_for_timeout(1000)
            await page.click('[data-act="open-notes"]')
            await page.click('.nt-row.note[data-path="secret.md"]', modifiers=[])
            await page.wait_for_selector(".cm-content")
            await page.dblclick(".tab.note.active")  # keep the tab (not the preview tab)

            # 1. locking from the menu: the open tab stays open, the list shows the lock
            await page.click('.nt-row.note[data-path="secret.md"]', button="right")
            await page.click('.menu-item:has-text("Lock")')
            await page.wait_for_selector('.nt-row.note[data-path="secret.md"] .nt-lock')
            check("Lock marks the note in the list", True)
            check("the tab that locked it stays open", await secret_shown(page))
            r = requests.get(BASE + "/api/notes/note?path=secret.md", timeout=5)
            check("other pages get 423", r.status_code == 423 and "zebracorn" not in r.text, r.status_code)

            # 2. a reload shows the lock in the tab
            await page.reload()
            await page.wait_for_selector(".note-locked input[type=password]")
            await page.wait_for_timeout(1500)  # the start screen stays a second
            await shot(page, "locked-tab")
            check("after a reload the tab shows the lock, not the text", not await secret_shown(page))
            check("the lock is big and centred", await page.locator(".note-locked-icon").is_visible())

            # 3. wrong, then right password
            await page.fill(".note-locked input[type=password]", "wrong")
            await page.press(".note-locked input[type=password]", "Enter")
            await page.wait_for_timeout(800)
            check(
                "a wrong password keeps it locked",
                not await secret_shown(page) and await page.locator(".note-locked").is_visible(),
            )
            await page.fill(".note-locked input[type=password]", "hunter22")
            await page.press(".note-locked input[type=password]", "Enter")
            await page.wait_for_selector(".cm-content")
            await page.wait_for_timeout(400)
            check("the right password opens it", await secret_shown(page))
            async with page.expect_request("**/api/notes/unlock/touch", timeout=5000) as touched:
                await page.click(".cm-content")
            check(
                "clicking in it keeps it open (reported as use)", "secret.md" in ((await touched.value).post_data or "")
            )

            # 4. an embed in another note shows only that it is locked (another page has no grant)
            other = await b.new_page(viewport={"width": 1200, "height": 800})
            await other.goto(BASE + "/")
            await other.wait_for_timeout(1000)
            await other.click('[data-act="open-notes"]')
            await other.click('.nt-row.note[data-path="public.md"]')
            await other.wait_for_selector(".cm-content")
            await other.keyboard.press(f"{MOD}+e")
            await other.wait_for_selector(".note-embed .embed-locked")
            check("an embed of it elsewhere shows only a lock", not await secret_shown(other))
            await other.close()

            # 5. closing its tab locks it again
            await page.click(".tab.note.active .tab-close")
            await page.wait_for_timeout(600)
            if not await page.locator('.nt-row.note[data-path="secret.md"]').is_visible():
                await page.click('[data-act="open-notes"]')
            await page.click('.nt-row.note[data-path="secret.md"]')
            await page.wait_for_selector(".note-locked input[type=password]")
            check("closing its tab locks it again", not await secret_shown(page))

            # 6. Settings → Notes
            await page.keyboard.press(f"{MOD}+,")
            await page.wait_for_selector("#set-notes", state="attached")
            await page.click('.settings-nav [data-nav="notes"]')
            await page.wait_for_function(
                "document.querySelector('[data-note-password]')?.textContent.includes('Change')"
            )
            check("Settings → Notes offers to change the password", True)
            await page.keyboard.press("Escape")

            # 7. the lock on a phone: the note screen shows it, the password opens the note
            ctx = await b.new_context(**pw.devices["Pixel 5"])
            phone = await ctx.new_page()
            phone.on("pageerror", lambda e: errors.append(str(e)))
            await phone.goto(BASE + "/")
            await phone.locator('[data-slot="notes"]').tap()
            await phone.locator('.ph-note-row[data-note="secret.md"]').tap()
            await phone.wait_for_selector(".note-locked input[type=password]")
            await phone.wait_for_timeout(800)
            await shot(phone, "locked-phone")
            fits = await phone.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            check("on a phone the note screen shows the lock and fits", fits and not await secret_shown(phone))
            await phone.fill(".note-locked input[type=password]", "hunter22")
            await phone.press(".note-locked input[type=password]", "Enter")
            await phone.wait_for_selector(".ph-prose")
            check("on a phone the password opens it", await secret_shown(phone))
            await ctx.close()
            check("no JavaScript errors", not errors, str(errors[:3]))
            await b.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
