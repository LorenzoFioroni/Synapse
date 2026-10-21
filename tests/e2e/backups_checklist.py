"""Browser checklist for Settings → Backups: a backup is made when the app starts,
Back Up Now adds one, the folder can be changed (and a bad one is refused), and Restore puts the
library back as it was.

    python tests/e2e/backups_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server (python server.py) on port 8775 with a throw-away data folder; your
library is not touched.
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

PORT = 8775
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-backups-e2e-")
OTHER = tempfile.mkdtemp(prefix="zc-backups-elsewhere-")
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


async def main():
    guard(PORT)
    # one item before the server starts, so the start-up backup contains it
    from backend import create_app
    from backend.config import Config

    create_app(Config.from_env(DATA), start_indexer=False).extensions["synapse"].papers.create(
        {"title": "Original paper"}
    )
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", DATA],
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
            await page.wait_for_selector(".row[data-id]")

            ok = await wait_for(
                lambda: asyncio.to_thread(lambda: len(requests.get(BASE + "/api/backups").json()["backups"]) == 1)
            )
            check("a backup is made when the app starts", ok)

            await page.keyboard.press(f"{mod}+,")
            await page.wait_for_selector("#set-backups")
            await page.click('.settings-nav [data-nav="backups"]')
            await page.wait_for_selector("#set-backups .backup-row")
            desc = await page.text_content("[data-backup-desc]")
            check("the section shows the last backup and the folder", "Last backup" in desc and DATA in desc, desc)
            what = await page.text_content("#set-backups .backup-row .b-what")
            check("each backup says what it holds", "1 item" in what and "0 notes" in what, what)
            await page.wait_for_timeout(700)  # smooth scroll to the section
            await shot(page, "b1-backups")

            await page.click("[data-backup-now]")
            ok = await wait_for(
                lambda: page.evaluate("document.querySelectorAll('#set-backups .backup-row').length === 2")
            )
            check("Back Up Now adds a backup", ok)

            await page.select_option('#set-backups [data-key="backup_keep"]', "7")
            ok = await wait_for(
                lambda: asyncio.to_thread(
                    lambda: requests.get(BASE + "/api/settings").json()["settings"]["backup_keep"] == 7
                )
            )
            check("Keep is saved", ok)
            await page.click('#set-backups [data-seg="backup_interval"] button[data-v="weekly"]')
            ok = await wait_for(
                lambda: asyncio.to_thread(
                    lambda: requests.get(BASE + "/api/settings").json()["settings"]["backup_interval"] == "weekly"
                )
            )
            check("the interval is saved", ok)

            await page.fill("[data-backup-dir]", "not/a/full/path")
            await page.press("[data-backup-dir]", "Tab")
            ok = await wait_for(lambda: page.evaluate("!document.querySelector('[data-backup-dir-error]').hidden"))
            check("a relative folder is refused with a message", ok, await page.text_content("[data-backup-dir-error]"))
            await page.fill("[data-backup-dir]", OTHER)
            await page.press("[data-backup-dir]", "Tab")
            ok = await wait_for(
                lambda: page.evaluate(f"document.querySelector('[data-backup-desc]').textContent.includes({OTHER!r})")
            )
            check("another folder can be chosen", ok)
            await page.click("[data-backup-now]")
            ok = await wait_for(lambda: asyncio.to_thread(lambda: len(list(Path(OTHER).glob("library-*.tar.gz"))) == 1))
            check("Back Up Now writes into the chosen folder", ok)
            await page.wait_for_timeout(300)
            await shot(page, "b2-other-folder")

            # change the library, then restore the backup from before the change
            requests.post(BASE + "/api/items", json={"title": "Added after the backup"})
            await page.click("[data-restore]")
            await page.wait_for_selector(".dialog")
            dialog = await page.text_content(".dialog")
            check(
                "Restore asks first and says the current state is backed up", "backed up first" in dialog, dialog[:120]
            )
            async with page.expect_navigation(timeout=15000):
                await page.click(".dialog .btn-danger, .dialog .btn-primary")
            await page.wait_for_selector(".row[data-id]")
            titles = await page.eval_on_selector_all(
                "[data-id] .cell-title, [data-id]", "els => els.map((e) => e.textContent)"
            )
            joined = " ".join(titles)
            check(
                "after Restore the later item is gone",
                "Original paper" in joined and "Added after the backup" not in joined,
            )
            reasons = [b["reason"] for b in requests.get(BASE + "/api/backups").json()["backups"]]
            check(
                "the state before the restore was kept as a backup",
                any(r and r.startswith("before restoring") for r in reasons),
                str(reasons),
            )
            check("no JavaScript errors", not errors, "; ".join(errors[:3]))
            await browser.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
