"""Core provider/export lifecycle and spoiler rendering, using port 8807 and temporary data."""

import asyncio
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

PORT = 8807
BASE = f"http://127.0.0.1:{PORT}"
MOD = "Meta" if sys.platform == "darwin" else "Control"
TEXT = """# Spoilers

Before ||**inline secret** with $x$ and [[Target]]|| after.

:::spoiler Answer
A **block secret** and $y$.

:::spoiler Nested
Nested secret.
:::
:::

## After

Outside $z$ and `||literal code||`.
"""
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name, "" if ok else detail, flush=True)


async def toggle(page, pid, enabled):
    r = requests.post(f"{BASE}/api/plugin-host/{pid}/{'enable' if enabled else 'disable'}", timeout=10)
    r.raise_for_status()
    await page.evaluate("window.zcPlugins.sync()")


async def open_export(page):
    await page.evaluate("() => { void import('/js/views/exportDialog.js').then(m => m.openExport()); }")


async def main():
    guard(PORT)
    with tempfile.TemporaryDirectory(prefix="synapse-extensions-") as data:
        server = subprocess.Popen(
            [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", data],
            cwd=APP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                try:
                    requests.get(BASE + "/api/settings", timeout=1).raise_for_status()
                    break
                except requests.RequestException:
                    time.sleep(0.2)
            note = requests.post(BASE + "/api/notes/note", json={"title": "Spoilers", "text": TEXT}, timeout=5).json()
            path = note["path"]
            target = requests.post(BASE + "/api/notes/note", json={"title": "Target"}, timeout=5).json()["path"]
            original = requests.get(BASE + "/api/notes/note", params={"path": path}, timeout=5).json()["text"]
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page(viewport={"width": 1400, "height": 1100})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                await page.goto(BASE)
                await page.wait_for_function("window.zcPlugins?.loaded().includes('extended-markdown')")
                await page.evaluate(
                    """async path => { (await import('/js/store.js')).emit('open-note',{path}); }""", path
                )
                await page.wait_for_selector("#notes-view .cm-content")
                await page.locator(".note-head .note-status").click()
                await page.wait_for_selector(".cm-lp-spoiler .spoiler-toggle")
                inline = page.locator(".cm-lp-spoiler .spoiler-toggle").first
                check(
                    "Live preview conceals the inline spoiler", await inline.get_attribute("aria-expanded") == "false"
                )
                await inline.click()
                check(
                    "Live preview toggles inline spoiler without opening source",
                    await inline.get_attribute("aria-expanded") == "true",
                )
                summary = page.locator(".cm-lp-spoiler > .spoiler-block > summary").first
                await summary.click()
                check(
                    "Live preview opens a spoiler block",
                    await page.locator(".cm-lp-spoiler .spoiler-block").first.get_attribute("open") is not None,
                )
                check("Math after the block still renders", await page.locator(".cm-lp-math").count() > 0)
                await page.click('#notes-view [data-mode="read"]')
                await page.wait_for_selector(".note-read:not([hidden]) .spoiler-toggle")
                inline = page.locator(".note-read .spoiler-toggle").first
                await inline.focus()
                await page.keyboard.press("Space")
                check(
                    "Read mode spoiler works from the keyboard", await inline.get_attribute("aria-expanded") == "true"
                )
                summary = page.locator(".note-read .spoiler-block > summary").first
                await summary.focus()
                await page.keyboard.press("Enter")
                check(
                    "Read mode block works from the keyboard",
                    await page.locator(".note-read .spoiler-block").first.get_attribute("open") is not None,
                )
                check("Nested spoiler blocks render", await page.locator(".note-read .spoiler-block").count() == 2)
                check(
                    "Code markers stay literal",
                    "||literal code||" in await page.locator(".note-read code").last.text_content(),
                )
                for cycle in range(2):
                    await toggle(page, "extended-markdown", False)
                    await page.wait_for_function("!document.querySelector('.note-read .spoiler-toggle')")
                    check(
                        f"Spoilers off exposes readable source ({cycle + 1})",
                        "||inline secret" in await page.locator(".note-read").inner_text(),
                    )
                    await toggle(page, "extended-markdown", True)
                    await page.wait_for_selector(".note-read .spoiler-toggle")
                    check(
                        f"Spoilers on restores one inline control ({cycle + 1})",
                        await page.locator(".note-read .spoiler-toggle").count() == 1,
                    )
                # the package's own switch for spoilers, the rest of the package still on
                switch = f"{BASE}/api/plugin-host/extended-markdown/settings"
                requests.put(switch, json={"spoilers": False}, timeout=10).raise_for_status()
                await page.wait_for_function("!document.querySelector('.note-read .spoiler-toggle')", timeout=15000)
                check(
                    "The Spoilers switch alone shows the source",
                    "||inline secret" in await page.locator(".note-read").inner_text()
                    and "extended-markdown" in await page.evaluate("window.zcPlugins.loaded()"),
                )
                requests.put(switch, json={"spoilers": True}, timeout=10).raise_for_status()
                await page.wait_for_selector(".note-read .spoiler-toggle", timeout=15000)
                check(
                    "The Spoilers switch on again restores the control",
                    await page.locator(".note-read .spoiler-toggle").count() == 1,
                )
                saved = requests.get(BASE + "/api/notes/note", params={"path": path}, timeout=5).json()["text"]
                check("Toggling the plugin does not rewrite the note", saved == original)
                async with page.expect_download() as pending:
                    await page.evaluate(
                        """async path => { await (await import('/js/views/notes/exportNote.js')).exportNote(path,'html'); }""",
                        path,
                    )
                download = await pending.value
                html = Path(await download.path()).read_text()
                check(
                    "HTML/PDF preparation reveals spoiler contents",
                    "inline secret" in html
                    and "block secret" in html
                    and 'spoiler-content" hidden' not in html
                    and '<details class="spoiler-block" open' in html,
                )
                await page.click('.ws-switch [data-ws="library"]')
                requests.post(
                    BASE + "/api/items", json={"title": "An exported paper", "citation_key": "export2026"}, timeout=5
                ).raise_for_status()
                await open_export(page)
                await page.wait_for_selector(".format-opt")
                check("Existing five export formats are available", await page.locator(".format-opt").count() == 5)
                await page.click('.format-opt[data-fmt="ris"]')
                await page.wait_for_function("document.querySelector('.export-preview').textContent.includes('TY  -')")
                check("Selected writer supplies the preview", True)
                await page.click('.dialog button:text-is("Cancel")')
                await toggle(page, "export-formats", False)
                await page.evaluate("""async () => {
                    navigator.clipboard.writeText = async text => { window.copiedReference = text; };
                    await (await import('/js/actions.js')).loadItems();
                }""")
                await page.locator("#items-pane .row").first.click(button="right")
                await page.click('.menu-item:has-text("Copy as BibTeX")')
                await page.wait_for_function("window.copiedReference?.includes('export2026')")
                check("Copy BibTeX works with the export plugin disabled", True)
                await open_export(page)
                await page.wait_for_selector('.toast:has-text("No export formats")')
                check(
                    "Disabled export plugin leaves no stale format dialog", await page.locator(".dialog").count() == 0
                )
                await page.keyboard.press(f"{MOD}+,")
                await page.wait_for_function("document.querySelector('[data-key=export_format]').disabled")
                check("Settings follows disabled export formats", True)
                await toggle(page, "export-formats", True)
                await page.wait_for_function("!document.querySelector('[data-key=export_format]').disabled")
                check(
                    "Settings follows reactivated export formats",
                    await page.locator("[data-key=export_format] option:not([disabled])").count() == 5,
                )
                await page.click("[data-close]")
                await open_export(page)
                await page.wait_for_selector(".format-opt")
                check(
                    "Reactivated export plugin has five fresh options", await page.locator(".format-opt").count() == 5
                )
                await page.click('.dialog button:text-is("Cancel")')
                await page.evaluate(
                    """async path => { (await import('/js/store.js')).emit('open-note',{path}); }""", path
                )
                await page.click('#notes-view [data-mode="live"]')
                await page.locator(".note-head .note-status").click()
                await page.locator(".cm-lp-spoiler .spoiler-toggle").first.click()
                await page.locator(".cm-lp-spoiler a.wikilink").click()
                await page.wait_for_function("path => window.zcNotes.path() === path", arg=target)
                check("Wiki links inside Live spoilers open their note", True)
                check("No browser errors", not errors, errors)
                await browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
