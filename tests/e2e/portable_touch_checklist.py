"""Real touch taps on iPad/iPhone profiles in Chromium or WebKit.

Raw multi-finger/pen gestures stay in the Chromium CDP touch and touch_app checklists.
"""

import asyncio
import sys
from pathlib import Path

import requests
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.e2e._portable import APP, browser_type, screenshot, server  # noqa: E402

results = []


def check(name, condition):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + name, flush=True)


async def main():
    with server(8809) as base:
        item = requests.post(base + "/api/items", json={"title": "Portable touch paper"}).json()
        with (APP / "tests/e2e/fixtures/twocolumn.pdf").open("rb") as pdf:
            requests.post(
                base + f"/api/items/{item['id']}/attachments", files={"file": ("paper.pdf", pdf, "application/pdf")}
            ).raise_for_status()
        note = requests.post(
            base + "/api/notes/note", json={"title": "Touch note", "text": "# Touch note\n\nA portable note.\n"}
        ).json()["path"]
        async with async_playwright() as p:
            browser = await browser_type(p).launch()
            for profile in ["iPad Pro 11", "iPhone 13"]:
                context = await browser.new_context(**p.devices[profile])
                page = await context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(base)
                await page.wait_for_selector("#startup-screen", state="hidden")
                await page.locator(f'.row[data-id="{item["id"]}"] .c-title').tap()
                await page.locator('#item-pane [data-tool="open-pdf"]').tap()
                frame = page.frame_locator("#tab-views .tab-frame.active")
                await frame.locator('.page-wrapper[data-page-number="1"]').wait_for(timeout=15000)
                check(
                    profile + ": touch opens the PDF above the app",
                    await page.locator("body").evaluate(
                        "() => { const f=document.querySelector('#tab-views .tab-frame.active'); return document.elementFromPoint(innerWidth/2,innerHeight/2) === f; }"
                    ),
                )
                check(
                    profile + ": viewer fits the viewport",
                    await frame.locator("body").evaluate("() => document.documentElement.scrollWidth <= innerWidth"),
                )
                await page.locator("#tabbar .tab.home").tap()
                await page.locator('#toolbar [data-a="sidebar"]').tap()
                await page.locator('#sidebar [data-act="open-notes"]').tap()
                await page.locator(f'.nt-row.note[data-path="{note}"]').tap()
                await page.wait_for_selector(".cm-content")
                await page.locator('[data-mode="source"]').tap()
                await page.locator(".cm-content").tap()
                await page.keyboard.press("End")
                await page.keyboard.insert_text(" touch")
                await page.wait_for_function(
                    "() => document.querySelector('#statusbar [data-s=sel]').textContent === 'Saved'"
                )
                check(
                    profile + ": touch-selected note edits save",
                    " touch" in requests.get(base + "/api/notes/note", params={"path": note}).json()["text"],
                )
                if await page.locator('#toolbar [data-a="more"]').is_visible():
                    await page.locator('#toolbar [data-a="more"]').tap()
                else:
                    await page.locator('#toolbar [data-a="settings"]').tap()
                if await page.get_by_role("menuitem", name="Settings", exact=True).count():
                    await page.get_by_role("menuitem", name="Settings", exact=True).tap()
                await page.locator("[data-settings-search]").fill("note width")
                check(
                    profile + ": Settings touch navigation/search works", await page.locator("#set-notes").is_visible()
                )
                await screenshot(page, profile.replace(" ", "-") + ".png")
                check(
                    profile + ": no page overflow or browser errors",
                    await page.evaluate("document.documentElement.scrollWidth <= innerWidth") and not errors,
                )
                await context.close()
            await browser.close()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
