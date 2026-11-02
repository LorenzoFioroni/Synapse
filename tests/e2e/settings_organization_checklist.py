"""Notes preferences, Settings search and dynamic plugin sections on desktop and phone.

python tests/e2e/run.py settings_organization
"""

import asyncio
import json
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

PORT = 8808
BASE = f"http://127.0.0.1:{PORT}"
MOD = "Meta" if sys.platform == "darwin" else "Control"
results = []


def check(name, condition):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + name, flush=True)


async def shot(page, name):
    if os.environ.get("SHOTS"):
        folder = Path(os.environ["SHOTS"])
        folder.mkdir(parents=True, exist_ok=True)
        await page.locator("#settings-view").evaluate(
            "async el => { await Promise.all(el.getAnimations().map(a => a.finished.catch(() => {}))); }"
        )
        await page.screenshot(path=str(folder / name))


async def main():
    guard(PORT)
    with tempfile.TemporaryDirectory(prefix="synapse-settings-") as data:
        plugin = Path(data) / "plugins" / "search-demo"
        plugin.mkdir(parents=True)
        (plugin / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "search-demo",
                    "name": "Search Demo",
                    "version": "1.0.0",
                    "api": "^1.0",
                    "frontend": "main.js",
                    "settings": [
                        {
                            "key": "calibration",
                            "type": "text",
                            "label": "Calibration mode",
                            "default": "privatevalue123",
                        }
                    ],
                }
            )
        )
        (plugin / "main.js").write_text("""export function activate(p) {
          p.settingsPage.register({id: 'advanced', title: 'Advanced controls', mount(el) {
            el.innerHTML = '<div class="setting"><span>Pending edit</span><input data-pending value="keep me"></div><div data-late></div>';
            const add = () => setTimeout(() => {
              el.querySelector('[data-late]').innerHTML = '<div class="setting"><div class="s-title">Zephyr integration</div><button data-zephyr>Configure</button></div>';
            }, 0);
            document.addEventListener('search-demo-details', add);
            return () => document.removeEventListener('search-demo-details', add);
          }});
        }""")
        server = subprocess.Popen(
            [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", data],
            cwd=APP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(240):
                try:
                    requests.get(BASE + "/api/settings", timeout=1).raise_for_status()
                    break
                except requests.RequestException:
                    time.sleep(0.25)
            session = requests.Session()
            session.headers["Origin"] = BASE
            session.post(BASE + "/api/plugin-host/search-demo/enable").raise_for_status()
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page(viewport={"width": 1440, "height": 950})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(BASE)
                await page.wait_for_selector("#startup-screen", state="hidden")
                await page.click('#toolbar [data-a="settings"]')
                await page.wait_for_selector("#set-plugin-search-demo [data-pending]")
                keys = [
                    "notes_enabled",
                    "notes_trash_retention",
                    "notes_width",
                    "notes_equation_numbers",
                    "notes_math_physics",
                    "notes_math_macros",
                ]
                check(
                    "all note preferences have one home in Notes",
                    await page.evaluate(
                        """keys => keys.every(key => {
                    const controls = document.querySelectorAll(`[data-key="${key}"], [data-seg="${key}"]`);
                    return controls.length === 1 && !!controls[0].closest('#set-notes');
                })""",
                        keys,
                    ),
                )
                await page.click('.settings-nav [data-nav="notes"]')
                await page.locator('#set-notes [data-seg="notes_width"] [data-v="full"]').click()
                await page.select_option('[data-key="notes_trash_retention"]', "2w")
                await page.select_option('[data-key="notes_equation_numbers"]', "all")
                await page.locator('[data-key="notes_math_physics"]').locator("..").click()
                await page.fill('[data-key="notes_math_macros"]', r"\newcommand{\secretneedle}{x}")
                await page.locator('[data-key="notes_math_macros"]').dispatch_event("change")
                await page.wait_for_function(
                    "() => document.querySelector('[data-key=notes_math_macros]').closest('.setting').querySelector('.saved-pill').classList.contains('show')"
                )
                settings = session.get(BASE + "/api/settings").json()["settings"]
                check(
                    "moved controls retain existing save behavior",
                    settings["notes_width"] == "full"
                    and settings["notes_trash_retention"] == "2w"
                    and settings["notes_equation_numbers"] == "all"
                    and not settings["notes_math_physics"]
                    and "secretneedle" in settings["notes_math_macros"],
                )
                await page.locator('[data-key="notes_enabled"]').locator("..").click()
                check(
                    "Notes preferences remain reachable when disabled",
                    await page.locator('[data-key="notes_math_macros"]').is_visible(),
                )
                await page.locator('[data-key="notes_enabled"]').locator("..").click()
                await page.locator("[data-settings-search]").focus()
                await page.keyboard.press(MOD + "+f")
                check(
                    "Cmd/Ctrl+F searches Settings without leaving it",
                    await page.locator("#settings-view").count() == 1
                    and await page.locator("[data-settings-search]").evaluate("(el) => el === document.activeElement"),
                )
                await page.fill("[data-settings-search]", "ÉQUATION NUMBERS")
                check(
                    "search ignores case and accents",
                    await page.locator("#set-notes").is_visible()
                    and not await page.locator("#set-general").is_visible(),
                )
                check(
                    "navigation follows search",
                    await page.locator('.settings-nav [data-nav="notes"]').is_visible()
                    and not await page.locator('.settings-nav [data-nav="general"]').is_visible(),
                )
                check(
                    "matching preferences remain editable",
                    await page.locator('[data-key="notes_equation_numbers"]').is_enabled(),
                )
                await shot(page, "settings-search-desktop.png")
                await page.fill("[data-settings-search]", "secretneedle")
                check(
                    "saved macros and derived validation are not indexed",
                    await page.locator("[data-settings-search-empty]").is_visible(),
                )
                await page.fill("[data-settings-search]", "privatevalue123")
                check(
                    "plugin field values are not indexed",
                    await page.locator("[data-settings-search-empty]").is_visible(),
                )
                await page.fill("[data-settings-search]", "<img onerror=alert(1)>")
                check(
                    "unmatched input has a safe empty state",
                    await page.locator("[data-settings-search-empty]").is_visible()
                    and "0 sections" in await page.locator("[data-settings-search-summary]").inner_text(),
                )
                await page.keyboard.press("Escape")
                check(
                    "Escape restores sections and previous navigation",
                    not await page.locator("[data-settings-search-empty]").is_visible()
                    and await page.input_value("[data-settings-search]") == ""
                    and "active" in (await page.locator('.settings-nav [data-nav="notes"]').get_attribute("class")),
                )
                await page.click('.settings-nav [data-nav="plugins"]')
                await page.fill("[data-settings-search]", "calibration mode")
                await page.wait_for_selector("#set-plugin-search-demo", state="visible")
                check(
                    "plugin settings are searchable with visible navigation",
                    await page.locator('.settings-nav [data-nav="plugin-search-demo"]').is_visible(),
                )
                await page.fill("#set-plugin-search-demo [data-pending]", "unfinished edit")
                await page.locator("#set-plugin-search-demo [data-pending]").evaluate(
                    "(el) => { window.settingsTestInput = el; }"
                )
                await page.fill("[data-settings-search]", "theme")
                await page.fill("[data-settings-search]", "calibration")
                check(
                    "filtering preserves plugin DOM and pending input",
                    await page.locator("[data-pending]").evaluate(
                        '(el) => el === window.settingsTestInput && el.value === "unfinished edit"'
                    ),
                )
                await page.fill("[data-settings-search]", "zephyr integration")
                check(
                    "an initially absent dynamic control yields no results",
                    await page.locator("[data-settings-search-empty]").is_visible(),
                )
                await page.evaluate("document.dispatchEvent(new Event('search-demo-details'))")
                await page.wait_for_selector("[data-zephyr]", state="visible")
                check(
                    "asynchronous plugin content refreshes active search",
                    not await page.locator("[data-settings-search-empty]").is_visible(),
                )
                session.post(BASE + "/api/plugin-host/search-demo/disable").raise_for_status()
                await page.wait_for_selector("#set-plugin-search-demo", state="detached")
                await page.wait_for_selector("[data-settings-search-empty]", state="visible")
                check(
                    "plugin removal keeps the query and updates results",
                    await page.input_value("[data-settings-search]") == "zephyr integration",
                )
                await page.click("[data-settings-search-clear]")
                check("clear button restores ordinary sections", await page.locator("#set-general").is_visible())
                await page.click("[data-close]")
                await page.click('#toolbar [data-a="settings"]')
                check("reopening starts with no search", await page.input_value("[data-settings-search]") == "")
                await page.click('.settings-nav [data-nav="notes"]')
                check(
                    "preferences persist after reopening",
                    await page.input_value('[data-key="notes_trash_retention"]') == "2w"
                    and await page.input_value('[data-key="notes_equation_numbers"]') == "all",
                )
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.fill("[data-settings-search]", "note width")
                check(
                    "phone search and Notes navigation remain usable",
                    await page.locator("[data-settings-search]").is_visible()
                    and await page.locator('.settings-nav [data-nav="notes"]').is_visible(),
                )
                check(
                    "phone Settings has no page overflow",
                    await page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"),
                )
                await shot(page, "settings-search-phone.png")
                await page.click("[data-settings-search-clear]")
                await page.click("[data-close]")
                check("closing Settings returns to the app", await page.locator("#settings-view").count() == 0)
                check("no browser errors", not errors)
                await browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
