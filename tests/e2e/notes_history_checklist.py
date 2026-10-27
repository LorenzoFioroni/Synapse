"""Browser checklist for the notes history (views/notes/historyView.ts, backend/notes/history/): a
note's versions grouped (the user's edits close together as one row, an AI app's change apart), what
a version changed and how it differs from now, putting a version back (recorded as a restore),
switching notes records the edits at once, changes made outside Synapse, the history of all notes
with a milestone marked, compared and restored, and Settings → Backups saying the history is on.

    python tests/e2e/notes_history_checklist.py      (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8811 with a throw-away data folder; your library is not touched. The
AI change is made the way the MCP server makes it: from another process, on the same data folder.
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

PORT = 8811
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-history-e2e-")
NOTES = Path(DATA) / "notes"
SHOTS = os.environ.get("SHOTS")
MOD = "Meta" if sys.platform == "darwin" else "Control"
results = []

AI_SCRIPT = """
import sys
sys.path.insert(0, sys.argv[1])
from backend.config import Config
from backend.library.agent import acting_as
from backend.services import Services
ns = Services(Config.from_env(sys.argv[2])).note_service
with acting_as("Claude"):
    text, h = ns.vault.read("plan.md")
    ns.edit("plan.md", text.replace("a first idea", "a first, sharper idea"), h, by="ai", summary="Sharpened the idea")
"""


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


async def wait_for(page, js, t=9000):
    end = time.time() + t / 1000
    while time.time() < end:
        if await page.evaluate(js):
            return True
        await page.wait_for_timeout(150)
    return False


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

    def save(path, text):
        n = api(f"/api/notes/note?path={path}")
        return api("/api/notes/note", "PUT", json={"path": path, "text": text, "hash": n["hash"]})

    try:
        for _ in range(60):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                break
            except requests.RequestException:
                time.sleep(0.25)
        api(
            "/api/notes/note",
            "POST",
            json={"path": "plan.md", "title": "Plan", "text": "# Plan\n\nThis is a first idea.\n"},
        )
        api("/api/notes/note", "POST", json={"path": "other.md", "title": "Other", "text": "# Other\n"})
        api("/api/notes/history/checkpoint", "POST", json={})
        # three versions close together (each saved, then left: a checkpoint): one row
        for line in ("Second line.", "Third line.", "Fourth line."):
            text = api("/api/notes/note?path=plan.md")["text"]
            save("plan.md", text + line + "\n")
            api("/api/notes/history/checkpoint", "POST", json={"path": "plan.md"})
        subprocess.run([sys.executable, "-c", AI_SCRIPT, str(APP), DATA], check=True, timeout=60)
        # changed, recorded (the history was looked at), then undone: no versions
        before_detour = api("/api/notes/history/log")["entries"][0]["id"]
        text = api("/api/notes/note?path=plan.md")["text"]
        save("plan.md", text + "A detour.\n")
        api("/api/notes/history/checkpoint", "POST", json={"path": "plan.md"})
        save("plan.md", text)
        api("/api/notes/history/checkpoint", "POST", json={"path": "plan.md"})
        check(
            "an edit undone leaves no versions",
            api("/api/notes/history/log")["entries"][0]["id"] == before_detour,
        )

        async with async_playwright() as pw:
            b = await pw.chromium.launch()
            page = await b.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(BASE + "/")
            await page.wait_for_timeout(1000)
            await page.click('[data-act="open-notes"]')
            await page.click('.nt-row.note[data-path="plan.md"]')
            await page.wait_for_selector(".cm-content")
            await page.wait_for_timeout(600)

            # 1. the note's versions
            check(
                "the note's head has History where the Task button was",
                await page.locator('.note-head [data-act="history"]').is_visible()
                and await page.locator(".note-head [data-act='add-task']").count() == 0,
            )
            await page.click('.note-head [data-act="history"]')
            await page.wait_for_selector(".hist-dialog .hist-row")
            await page.wait_for_selector(".hist-dialog .hist-body .diff")
            await shot(page, "note-history")
            groups = page.locator(".hist-dialog .hist-list > .h-group")
            whats = await groups.locator(":scope > .hist-row .h-what").all_text_contents()
            metas = await groups.locator(":scope > .hist-row .h-meta").all_text_contents()
            check(
                "the AI change is a row of its own, the user's versions close together one row",
                len(whats) == 2
                and whats[0] == "Sharpened the idea"
                and metas[0].startswith("Claude")
                and whats[1].startswith("Edited")
                and "4 versions" in whats[1],
                f"{whats} {metas}",
            )
            marked = " ".join(await page.locator(".hist-dialog .hist-body .d-add ins").all_text_contents())
            check("the newest change shows what it changed, the changed words marked", "sharper" in marked, marked)
            await groups.nth(1).locator(":scope > .hist-row").click()
            await page.wait_for_function(
                "document.querySelector('.hist-dialog .hist-title').textContent.includes('4 versions')"
            )
            await page.wait_for_selector(".hist-dialog .hist-body .diff")
            adds = await page.locator(".hist-dialog .hist-body .d-add").all_text_contents()
            check(
                "a group shows all its edits together",
                all(any(x in a for a in adds) for x in ("Second", "Third", "Fourth")),
                str(adds),
            )
            await groups.nth(1).locator("[data-expand]").click()
            subs = await page.locator(".hist-dialog .h-sub .hist-row").count()
            check("a group opens into its single versions", subs == 4, str(subs))
            await page.click('.hist-dialog [data-against="now"]')
            await page.wait_for_selector(".hist-dialog .hist-body .diff")
            legend = await page.text_content(".hist-dialog .diff-legend")
            check("a version can be compared with the note now", "the note now" in legend)
            await shot(page, "note-history-compare")
            # put the first version back
            await page.locator(".hist-dialog .h-sub .hist-row").nth(3).click()
            await page.wait_for_timeout(400)
            await page.click('.dialog button:has-text("Restore This Version")')
            await wait_for(page, "document.querySelector('.note-status').textContent.includes('Saved')")
            await page.wait_for_timeout(500)
            text = (NOTES / "plan.md").read_text()
            check(
                "Restore This Version puts that version back", text.endswith("# Plan\n\nThis is a first idea.\n"), text
            )
            h = api("/api/notes/history?path=plan.md")["entries"][0]
            check("… recorded as a restore of it", h["kind"] == "restore" and h["restored_from"], str(h))

            # 2. leaving a note records what was typed in it at once
            await page.click(".cm-content")
            await page.keyboard.press(f"{MOD}+End")
            await page.keyboard.type("Typed just now.")
            await wait_for(page, "document.querySelector('.note-status').textContent.includes('Saved')")
            await page.click('.nt-row.note[data-path="other.md"]')
            await page.wait_for_timeout(1200)
            log = api("/api/notes/history/log")["entries"]
            check(
                "switching to another note records the edits as a version",
                log[0]["summary"] == "Edited plan",
                log[0]["summary"],
            )

            # 3. a change made by another program
            (NOTES / "other.md").write_text("# Other\n\nWritten in another editor.\n")
            api("/api/notes/rescan", "POST")
            log = api("/api/notes/history/log")["entries"]
            check("a change made outside Synapse is recorded as such", log[0]["by"] == "outside", str(log[0]))

            # 4. all notes: a milestone
            await page.keyboard.press(f"{MOD}+Shift+P")
            await page.wait_for_selector(".palette")
            await page.keyboard.type("Notes History")
            await page.wait_for_timeout(200)
            await page.click(".palette-item:has(.p-title:text-is('Notes History…'))")
            await page.wait_for_selector(".hist-dialog .hist-row")
            await page.wait_for_selector(".hist-dialog .hist-file")
            files = await page.locator(".hist-dialog .hist-file-head .mono").all_text_contents()
            check(
                "the history of all notes shows each change with the notes it touched",
                files == ["other.md"],
                str(files),
            )
            await page.click('.dialog-footer button:has-text("Mark Milestone")')
            await page.wait_for_selector(".dialog input")
            await page.wait_for_timeout(150)  # the field takes the focus
            await page.keyboard.type("Submitted")
            await page.keyboard.press("Enter")
            await page.wait_for_selector(".hist-dialog .hist-row.milestone")
            await page.wait_for_timeout(600)
            check(
                "a marked milestone shows in the list",
                "Submitted" in (await page.text_content(".hist-dialog .hist-row.milestone") or ""),
            )
            save("plan.md", "# Plan\n\nRewritten after submitting.\n")
            api("/api/notes/note", "POST", json={"path": "late.md", "title": "Late", "text": "# Late\n"})
            await page.click(".hist-dialog .hist-row.milestone")
            await page.wait_for_selector(".hist-dialog [data-m-restore]")
            await page.wait_for_selector(".hist-dialog .hist-file")
            changed = sorted(await page.locator(".hist-dialog .hist-file-head .mono").all_text_contents())
            check("a milestone shows what changed since", changed == ["late.md", "plan.md"], str(changed))
            await shot(page, "vault-history-milestone")
            await page.click(".hist-dialog [data-m-restore]")
            await page.locator(".dialog-backdrop").last.locator("button", has_text="Restore").click()
            await page.wait_for_timeout(1200)
            check(
                "restoring a milestone puts the notes back and moves newer ones to the trash",
                "Typed just now." in (NOTES / "plan.md").read_text() and not (NOTES / "late.md").exists(),
            )
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)

            # 5. Settings says the history is on
            await page.keyboard.press(f"{MOD}+,")
            await page.wait_for_selector("#set-backups")
            await page.click('.settings-nav [data-nav="backups"]')
            await page.wait_for_selector("[data-history-desc]")
            await wait_for(page, "!document.querySelector('[data-history-desc]').textContent.includes('Loading')")
            desc = await page.text_content("[data-history-desc]")
            check("Settings → Backups says the notes history is on", "Every version" in desc, desc)

            for width in (390,):
                await page.set_viewport_size({"width": width, "height": 800})
                await page.click("[data-history-open]")
                await page.wait_for_selector(".hist-dialog .hist-row")
                await page.wait_for_timeout(500)
                await shot(page, f"vault-history-{width}")
                fits = await page.evaluate("document.querySelector('.hist-dialog').scrollWidth <= innerWidth + 1")
                check(f"the history fits a {width}px screen", fits)
            check("no JavaScript errors", not errors, str(errors[:3]))
            await b.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
