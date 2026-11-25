"""Browser checklist for changing folders and the PDFs of the notes folder offline (docs/offline.md):
a folder is created, renamed, moved and deleted, a PDF renamed, moved and deleted, without Synapse;
the changes show at once, survive a reload, and go into Synapse in order once it answers again (a
folder holding a note changed in Synapse meanwhile keeps that note).

    python tests/e2e/offline_folders_checklist.py

Starts its own server on port 8877 with a throw-away data folder. Your library is not touched.
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

PORT = 8877
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-offline-folders-e2e-")
PDF = APP / "tests" / "e2e" / "fixtures" / "twocolumn.pdf"
results = []

SERVER = f"""
import sys
sys.path.insert(0, {str(APP)!r})
from backend import create_app
from backend.config import Config
cfg = Config.from_env({DATA!r})
app = create_app(cfg, start_indexer=False)
ns = app.extensions['synapse'].note_service
ns.save('Proj/a.md', '# A\\n\\nkept\\n', None)
ns.save('Proj/b.md', '# B\\n', None)
ns.save('Old/x.md', '# X\\n', None)
ns.save('Keep/z.md', '# Z\\n', None)
ns.save('Busy/busy.md', '# Busy\\n', None)
ns.save('Busy/calm.md', '# Calm\\n', None)
ns.make_folder('Docs')
app.run(port={PORT}, threaded=True)
"""


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)


def listing():
    return requests.get(f"{BASE}/api/notes", timeout=5).json()


def upload(name, folder=""):
    with PDF.open("rb") as f:
        r = requests.post(
            f"{BASE}/api/notes/pdf", files={"file": (name, f, "application/pdf")}, data={"folder": folder}, timeout=10
        )
    r.raise_for_status()


async def until(fn, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        await asyncio.sleep(0.4)
    return fn()


def note(path):
    return f'.nt-row.note[data-path="{path}"]'


def pdf(path):
    return f'.nt-row.pdf[data-path="{path}"]'


def folder(path):
    return f'.nt-row.folder[data-folder="{path}"]'


async def menu(page, sel, label):
    await page.click(sel, button="right")
    await page.click(f".menu >> text={label}")


async def prompt(page, value):
    await page.fill(".dialog input", value)
    await page.keyboard.press("Enter")


async def expand(page, path):
    row = page.locator(folder(path))
    if await row.get_attribute("aria-expanded") != "true":
        await row.click()
        await page.wait_for_timeout(200)


async def main():
    profile = tempfile.mkdtemp(prefix="zc-offline-folders-profile-")
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            profile, headless=True, viewport={"width": 1300, "height": 900}
        )
        page = ctx.pages[0]
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # Online: offline use on, the folder Proj kept.
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
        await page.wait_for_selector(folder("Proj"))
        await menu(page, folder("Proj"), "Make Available Offline")
        await page.wait_for_timeout(1500)
        for pg in list(ctx.pages):
            if pg is not page:
                await pg.close()

        # Offline.
        off = await ctx.new_page()
        off.on("pageerror", lambda e: errors.append(str(e)))
        await page.close()
        page = off
        await ctx.set_offline(True)
        await page.goto(BASE + "/")
        await page.wait_for_timeout(1200)
        await page.click('[data-act="open-notes"]')
        await page.wait_for_selector(folder("Proj"), timeout=20000)

        await page.click('[data-act="new-folder"]:visible')
        await prompt(page, "Ideas")
        await page.wait_for_selector(folder("Ideas"), timeout=10000)
        check("a folder is created offline", True)

        await menu(page, folder("Proj"), "Rename…")
        await prompt(page, "Project")
        await page.wait_for_selector(folder("Project"), timeout=10000)
        await expand(page, "Project")
        check(
            "a folder is renamed offline, with its notes",
            await page.locator(folder("Proj")).count() == 0
            and await page.locator(note("Project/a.md")).count() == 1
            and await page.locator(note("Project/b.md")).count() == 1,
        )
        await page.click(note("Project/a.md"))
        await page.wait_for_function(
            "() => document.querySelector('.cm-content')?.innerText.includes('kept')", timeout=10000
        )
        await page.click(".cm-content")
        await page.keyboard.press("ControlOrMeta+End")
        await page.keyboard.type("\nedited in the renamed folder\n")
        await page.wait_for_timeout(2500)
        check("a kept note in it opens and is edited offline", True)

        await menu(page, folder("Old"), "Move to folder…")
        await page.click(".menu >> text=Ideas")
        await page.wait_for_selector(folder("Ideas/Old"), timeout=10000)
        check("a folder is moved into another offline", await page.locator(folder("Old")).count() == 0)

        for f in ("Keep", "Busy"):
            await menu(page, folder(f), "Move to Trash")
            await page.click(".dialog >> text=Move to Trash")
            await page.wait_for_selector(folder(f), state="detached", timeout=10000)
        check("folders are deleted offline", True)

        await menu(page, pdf("paper.pdf"), "Rename…")
        await prompt(page, "Article")
        await page.wait_for_selector(pdf("Article.pdf"), timeout=10000)
        check("a PDF is renamed offline", await page.locator(pdf("paper.pdf")).count() == 0)
        await menu(page, pdf("loose.pdf"), "Move to folder…")
        await page.click(".menu >> text=Docs")
        await expand(page, "Docs")
        await page.wait_for_selector(pdf("Docs/loose.pdf"), timeout=10000)
        check("a PDF is moved to a folder offline", True)
        await menu(page, pdf("gone.pdf"), "Move to Trash")
        if await page.locator(".dialog >> text=Move to Trash").count():
            await page.click(".dialog >> text=Move to Trash")
        await page.wait_for_selector(pdf("gone.pdf"), state="detached", timeout=10000)
        check("a PDF is deleted offline", True)

        # A reload, still offline: everything is as it was left.
        await page.reload()
        await page.wait_for_timeout(1500)
        await page.click('[data-act="open-notes"]')
        await page.wait_for_selector(folder("Project"), timeout=20000)
        await expand(page, "Ideas")
        same = {
            "Ideas/Old": await page.locator(folder("Ideas/Old")).count() == 1,
            "no Keep": await page.locator(folder("Keep")).count() == 0,
            "Article": await page.locator(pdf("Article.pdf")).count() == 1,
            "no gone": await page.locator(pdf("gone.pdf")).count() == 0,
        }
        check("they survive a reload offline", all(same.values()), str(same))

        # Meanwhile, in Synapse: Busy/busy.md changes, so it must stay.
        busy = requests.get(f"{BASE}/api/notes/note", params={"path": "Busy/busy.md"}, timeout=5).json()
        time.sleep(1.1)
        requests.put(
            f"{BASE}/api/notes/note",
            json={"path": "Busy/busy.md", "text": busy["text"] + "\nchanged in Synapse\n", "hash": busy["hash"]},
            timeout=5,
        ).raise_for_status()

        await ctx.set_offline(False)
        # the last change made offline (deleting gone.pdf) goes in last
        done = await until(
            lambda: (lambda r: r if all(x["path"] != "gone.pdf" for x in r.get("pdfs", [])) else None)(listing())
        )
        r = done or listing()
        notes = {n["path"] for n in r["notes"]}
        pdfs = {x["path"] for x in r.get("pdfs", [])}
        check("the new folder is in Synapse", "Ideas" in r["folders"], str(r["folders"]))
        check(
            "the renamed folder is in Synapse, with its notes",
            "Proj" not in r["folders"] and {"Project/a.md", "Project/b.md"} <= notes,
            str(sorted(notes)),
        )
        a = await until(
            lambda: (lambda t: t if "edited in the renamed folder" in t else None)(
                requests.get(f"{BASE}/api/notes/note", params={"path": "Project/a.md"}, timeout=5)
                .json()
                .get("text", "")
            )
        )
        check("the edit made in it goes in after the rename", a)
        check("the moved folder is in Synapse", "Ideas/Old/x.md" in notes and "Old" not in r["folders"])
        check("the deleted folder is gone", "Keep" not in r["folders"] and "Keep/z.md" not in notes)
        check(
            "a note changed in Synapse meanwhile is not deleted with its folder (the other one is)",
            "Busy/busy.md" in notes and "Busy/calm.md" not in notes,
            str(sorted(n for n in notes if n.startswith("Busy"))),
        )
        check(
            "the PDFs are renamed, moved and deleted in Synapse",
            {"Article.pdf", "Docs/loose.pdf"} <= pdfs and not pdfs & {"paper.pdf", "loose.pdf", "gone.pdf"},
            str(sorted(pdfs)),
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
        for name in ("paper.pdf", "loose.pdf", "gone.pdf"):
            upload(name)
        asyncio.run(main())
    finally:
        proc.terminate()
        shutil.rmtree(DATA, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)
