"""Browser checklist for Claude's edits of notes (issue #40): a change saved by the MCP server
(another process) appears in the open note, Cmd/Ctrl+Z undoes it, the bar's Undo / Show changes
work, and the note's list of Claude edits restores any earlier version.

    python tests/e2e/claude_edits_checklist.py

Starts its own server on port 8785 with a throw-away data folder and runs the real MCP server
(mcp_server.py) as Claude; your library is not touched.
"""
import asyncio
import json
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

PORT = 8785
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-claude-edits-e2e-")
NOTES = Path(DATA) / "notes"
results = []
ORIGINAL = "# Plan\n\nFirst paragraph about the model.\n\nSecond paragraph.\n"


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


def claude(tool, **args):
    """One call to the real MCP server, as Claude makes it."""
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "e2e", "version": "1"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": args}}]
    p = subprocess.run([sys.executable, str(APP / "mcp_server.py"), "--data", DATA], capture_output=True, text=True,
                       input="".join(json.dumps(m) + "\n" for m in msgs), timeout=60)
    res = json.loads(p.stdout.splitlines()[-1])["result"]
    assert not res["isError"], res
    return json.loads(res["content"][0]["text"])


def body(name="plan.md"):
    text = (NOTES / name).read_text()
    return text[text.index("# Plan"):] if "# Plan" in text else text


async def editor_text(page):
    return await page.evaluate("window.zcNotes.text()")


async def wait_for(page, js, t=9000):
    end = time.time() + t / 1000
    while time.time() < end:
        if await page.evaluate(js):
            return True
        await page.wait_for_timeout(150)
    return False


async def saved(page):
    return await wait_for(page, "document.querySelector('.note-status').textContent.includes('Saved')")


async def main():
    guard(PORT)
    srv = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", DATA],
                           cwd=APP, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                break
            except requests.RequestException:
                time.sleep(0.25)
        s = requests.Session()
        s.headers["Origin"] = BASE
        s.post(BASE + "/api/notes/note", json={"path": "plan.md", "title": "Plan", "text": ORIGINAL}).raise_for_status()
        s.post(BASE + "/api/notes/note", json={"path": "other.md", "title": "Other", "text": "# Other\n"}).raise_for_status()
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
            await page.wait_for_selector('.cm-content')
            await page.wait_for_timeout(500)

            # 1. Claude edits the open note from another process
            claude("edit_note", note="plan", summary="Expanded the model",
                   edits=[{"old": "about the model.", "new": "about the model, now with noise."}])
            got = await wait_for(page, "window.zcNotes.text().includes('now with noise')")
            check("the open note shows Claude's change without a reload", got)
            check("a bar names the change", await page.is_visible('.note-ai-bar')
                  and "Expanded the model" in await page.text_content('.note-ai-bar'))
            # 2. Cmd/Ctrl+Z undoes it, Cmd/Ctrl+Shift+Z brings it back
            await page.click('.cm-content')
            await page.keyboard.press('ControlOrMeta+z')
            await saved(page)
            await page.wait_for_timeout(300)
            check("Cmd/Ctrl+Z in the note undoes Claude's change (and saves)",
                  "now with noise" not in await editor_text(page) and body() == ORIGINAL)
            check("the bar goes away once the change is undone", not await page.is_visible('.note-ai-bar'))
            await page.keyboard.press('ControlOrMeta+Shift+z')
            await saved(page)
            await page.wait_for_timeout(600)
            check("redo brings it back", "now with noise" in body() and await page.is_visible('.note-ai-bar'))
            # 3. the bar's Undo
            await page.click('.note-ai-bar [data-act="ai-undo"]')
            await saved(page)
            await page.wait_for_timeout(300)
            check("the bar's Undo restores the version before", body() == ORIGINAL and "now with noise" not in await editor_text(page))
            # 4. Show changes → Undo this change
            claude("edit_note", note="plan", append="## Next\n\nRead more.", summary="Added next steps")
            await wait_for(page, "window.zcNotes.text().includes('Read more')")
            await wait_for(page, "!document.querySelector('.note-ai-bar').hidden")
            await page.click('.note-ai-bar [data-act="ai-show"]')
            await page.wait_for_selector('.dialog .diff')
            adds = await page.locator('.dialog .diff .d-add').all_text_contents()
            check("Show changes lists the added lines", any("Read more" in a for a in adds), str(adds))
            await page.click('.dialog button:has-text("Undo this change")')
            await saved(page)
            await page.wait_for_timeout(300)
            check("Undo this change restores the note", body() == ORIGINAL)
            # 5. hiding the bar keeps the change, also after a reload
            claude("edit_note", note="plan", edits=[{"old": "Second paragraph.", "new": "Second paragraph, revised."}],
                   summary="Revised")
            await wait_for(page, "window.zcNotes.text().includes('revised')")
            await wait_for(page, "!document.querySelector('.note-ai-bar').hidden")
            await page.click('.note-ai-bar [data-act="ai-dismiss"]')
            await page.reload()
            await page.wait_for_selector('.cm-content')
            await page.wait_for_timeout(800)
            check("hidden bar stays hidden; the change stays", not await page.is_visible('.note-ai-bar') and "revised" in body())
            # 6. the list of Claude edits restores an earlier version
            await page.wait_for_selector('.ai-edits-btn')
            label = await page.text_content('.ai-edits-btn')
            await page.click('.ai-edits-btn')
            await page.wait_for_selector('.dialog .ai-row')
            rows = await page.locator('.dialog .ai-row').count()
            check("the note's details list Claude's edits", rows == 3 and "3 edits" in label, f"{rows} rows, {label!r}")
            await page.locator('.dialog .ai-row button').nth(rows - 1).click()   # the oldest one
            await page.wait_for_selector('.dialog .dialog-title:has-text("Before Claude")')
            await page.click('.dialog button:has-text("Restore the version before it")')
            await saved(page)
            await page.wait_for_timeout(400)
            check("restoring the oldest version gives the original note", body() == ORIGINAL and await page.locator('.dialog').count() == 0)
            # 7. a note edited while another one is open shows the bar when opened
            await page.click('.nt-row.note[data-path="other.md"]')
            await page.wait_for_timeout(500)
            claude("edit_note", note="plan", append="Done while away.", summary="While away")
            await page.click('.nt-row.note[data-path="plan.md"]')
            await page.wait_for_timeout(800)
            check("opening a note Claude edited shows the bar", "While away" in (await page.text_content('.note-ai-bar') or "")
                  and "Done while away." in await editor_text(page))
            # 8. the user typing hides the bar (the note is no longer what Claude left)
            await page.click('.cm-content')
            await page.keyboard.press('ControlOrMeta+End')
            await page.keyboard.type(" Mine.")
            await saved(page)
            await page.wait_for_timeout(600)
            check("typing in the note hides the bar", not await page.is_visible('.note-ai-bar'))
            check("no JavaScript errors", not errors, str(errors[:3]))
            await b.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
