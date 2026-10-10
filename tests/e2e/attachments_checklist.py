"""Browser checklist for note attachments (issue #12): unused images go to the attachment trash,
the Attachments dialog (in use / not used / trash, insert into the open note, restore, delete),
the retention setting, and the deletion dates in Deleted notes.

    python tests/e2e/attachments_checklist.py           (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8772 with a throw-away data folder; your library is not touched.
"""
import asyncio, io, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
PORT = 8772
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-attachments-e2e-")
NOTES = Path(DATA) / "notes"
SHOTS = os.environ.get("SHOTS")
results = []
# a 2×2 PNG
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000002000000020802000000fdd49a73000000164944415478"
                    "9c63f8cfc0f01f0c18fe33fc6700002cfc05fb0c0e5b9e0000000049454e44ae426082")


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


def upload(name):
    r = requests.post(f"{BASE}/api/notes/attachments", files={"file": (name, io.BytesIO(PNG), "image/png")})
    return r.json()["path"]


def note(title, extra):
    n = requests.post(f"{BASE}/api/notes/note", json={"title": title}).json()
    cur = requests.get(f"{BASE}/api/notes/note", params={"path": n["path"]}).json()
    requests.put(f"{BASE}/api/notes/note", json={"path": n["path"], "text": cur["text"] + extra, "hash": cur["hash"]})
    return n["path"]


def age(rel, hours=2):
    t = time.time() - hours * 3600
    os.utime(NOTES / rel, (t, t))


async def main():
    env = dict(os.environ, ZCLONE_DATA_DIR=DATA)
    srv = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--no-browser"], cwd=APP, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    try:
        used, unused, fresh = upload("used.png"), upload("unused.png"), upload("fresh.png")
        for p in (used, unused):
            age(p)
        note("Figures", f"\n![]({used})\n")
        gone = note("Old idea", "\nNothing here.\n")
        requests.post(f"{BASE}/api/notes/trash", json={"path": gone})
        r = requests.post(f"{BASE}/api/notes/attachments/clean").json()
        check("unused attachment goes to the attachment trash (not the fresh one)",
              r["trashed"] == [unused.split("/", 1)[1]] and (NOTES / used).exists() and (NOTES / fresh).exists(), r)

        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(viewport={"width": 1400, "height": 900})
            page = await ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 400 else None)
            await page.goto(BASE)
            await page.click('.ws-switch [data-ws="notes"]')
            await page.wait_for_selector('#notes-view:not([hidden])')
            await page.locator('.notes-tree').get_by_text("Figures", exact=True).click()
            await page.wait_for_function("window.zcNotes.path() === 'Figures.md'")

            # ---- the Attachments dialog
            await page.click('.notes-tree-head [data-act="tree-menu"]')
            await page.click('.menu >> text=Attachments…')
            await page.wait_for_selector('.att-grid')
            tabs = " ".join((await page.text_content('.att-tabs')).split())
            check("tabs count all / not used / trash", tabs == "All 2 Not used 1 Trash 1", tabs)
            loaded = await page.evaluate("[...document.querySelectorAll('.att-thumb img')].every((i) => i.complete && i.naturalWidth > 0)")
            check("thumbnails load", loaded)
            names = await page.locator('.att-card .att-name').all_text_contents()
            check("lists the attachments with their notes", len(names) == 2 and await page.locator('.att-card .att-notes:has-text("Figures")').count() == 1, names)
            await shot(page, "1-attachments")

            # insert an existing attachment into the open note (reuse in several notes)
            fresh_card = page.locator('.att-card', has_text=fresh.split("/", 1)[1])
            await fresh_card.locator('[data-act="insert"]').click()
            await page.wait_for_timeout(300)
            text = await page.evaluate("window.zcNotes.text()")
            check("Insert puts the image into the open note", f"![]({fresh})" in text, text[-80:])

            # the trash tab: restore and delete
            await page.click('.att-tabs [data-tab="trash"]')
            trash_cards = page.locator('.att-card.trashed')
            check("the trash tab shows the unused attachment with its deletion date",
                  await trash_cards.count() == 1 and "deleted for good on" in await trash_cards.first.text_content())
            await shot(page, "2-trash")
            await trash_cards.first.locator('[data-act="restore"]').click()
            await page.wait_for_timeout(400)
            check("Restore brings it back", (NOTES / unused).exists() and await page.locator('.att-card.trashed').count() == 0)
            await page.click('.dialog-footer button:has-text("Close")')

            # delete for good from the trash
            age(unused, 3)
            requests.post(f"{BASE}/api/notes/attachments/clean")
            await page.click('.notes-tree-head [data-act="tree-menu"]')
            await page.click('.menu >> text=Attachments…')
            await page.click('.att-tabs [data-tab="trash"]')
            await page.locator('.att-card.trashed [data-act="delete"]').first.click()
            await page.wait_for_timeout(400)
            check("Delete removes it for good", not any((NOTES / ".trash-attachments").rglob("*.png")))
            await page.click('.dialog-footer button:has-text("Close")')

            # ---- deleted notes show when they go
            await page.click('.notes-tree-head [data-act="tree-menu"]')
            await page.click('.menu >> text=Deleted notes…')
            await page.wait_for_selector('.trash-row')
            sub = await page.text_content('.dialog-subtitle')
            title = await page.locator('.trash-row .muted').first.get_attribute("title")
            check("Deleted notes says when they are deleted for good", "after 1 month" in sub and title.startswith("Deleted for good on"), (sub, title))
            await page.click('.dialog-footer button:has-text("Close")')

            # ---- the setting
            await page.keyboard.press("Control+,")
            await page.wait_for_selector('select[data-key="notes_trash_retention"]')
            await page.select_option('select[data-key="notes_trash_retention"]', "2w")
            await page.wait_for_timeout(500)
            stored = requests.get(f"{BASE}/api/settings").json()["settings"]["notes_trash_retention"]
            check("the retention setting is saved", stored == "2w", stored)
            await shot(page, "3-setting")
            check("no console errors", not errors, errors[:5])
            await b.close()
    finally:
        srv.terminate()
        shutil.rmtree(DATA, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _guard import guard  # noqa: E402
guard(PORT)
asyncio.run(main())
