"""Browser checklist for plugins: in Settings → Plugins the example plugin is listed off, turning it
on asks first and lists what it may do, its settings are drawn from its manifest and saved, its
route answers, its log opens, a plugin that cannot start shows why; its frontend adds a page with a
sidebar entry and a count, an item-pane section, an item action, a command in the command palette
and a key; turning a plugin off in one window shows in another and takes its page away.

    python tests/e2e/plugins_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server (python server.py) on port 8798 with a throw-away data folder; your
library is not touched.
"""

import asyncio
import json
import os
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

PORT = 8798
BASE = f"http://127.0.0.1:{PORT}"
DATA = Path(tempfile.mkdtemp(prefix="zc-plugins-e2e-"))
DEV = Path(tempfile.mkdtemp(prefix="zc-plugins-dev-"))
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


def api(path):
    return requests.get(BASE + path, timeout=5).json()


def plugin(pid):
    return next(p for p in api("/api/plugin-host")["plugins"] if p["id"] == pid)


ROW = '.plugin-row[data-plugin="recently-added"]'
SEC = "#set-plugin-recently-added"


async def open_plugins(page):
    await page.wait_for_function("() => (!!window.zcPlugins)")
    await page.keyboard.press(f"{mod}+,")
    await page.wait_for_selector("#set-plugins")
    await page.click('.settings-nav [data-nav="plugins"]')
    await page.wait_for_selector(f"#set-plugins {ROW}")
    await page.wait_for_timeout(600)  # smooth scroll to the section


async def main():
    guard(PORT)
    plugins = DATA / "plugins"
    shutil.copytree(APP / "docs" / "examples" / "recently-added", plugins / "recently-added")
    shutil.copytree(APP / "docs" / "examples" / "hello-library", plugins / "hello-library")
    broken = plugins / "broken-one"
    broken.mkdir()
    (broken / "manifest.json").write_text(
        json.dumps({"id": "broken-one", "name": "Broken One", "version": "0.1", "api": "^1.0", "backend": "p.py"})
    )
    (broken / "p.py").write_text("def activate(plugin):\n    raise RuntimeError('needs a key')\n")
    srv = subprocess.Popen(
        [
            sys.executable,
            "server.py",
            "--port",
            str(PORT),
            "--no-browser",
            "--data-dir",
            str(DATA),
            "--dev-plugins",
            str(DEV),
        ],
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
            await page.wait_for_selector(".row[data-id], .empty-state, #item-list", timeout=15000)
            await open_plugins(page)

            desc = await page.text_content("[data-plugins-desc]")
            check("the section says where plugins go", str(plugins) in desc, desc)
            core_names = await page.eval_on_selector_all(
                "#set-plugins .plugin-group:first-child .p-name", "els => els.map(e => e.textContent)"
            )
            contributed_names = await page.eval_on_selector_all(
                "#set-plugins .plugin-group:last-child .p-name", "els => els.map(e => e.textContent)"
            )
            # the two installed here, and those that come with Synapse (plugins/)
            manifests = [json.loads(m.read_text()) for m in (APP / "plugins").glob("*/manifest.json")]
            want_core = sorted([m["name"] for m in manifests if m.get("core")], key=str.lower)
            want_contributed = sorted(
                ["Broken One", "Hello Library", "Recently Added", *[m["name"] for m in manifests if not m.get("core")]],
                key=str.lower,
            )
            check(
                "core and contributed plugins are listed separately",
                core_names == want_core and contributed_names == want_contributed,
                (core_names, contributed_names),
            )
            check(
                "the plugin sublist expands only in the Plugins area",
                await page.locator(".settings-nav").evaluate("el => el.classList.contains('plugins-expanded')")
                and await page.is_visible(".settings-nav [data-plugin-nav]"),
            )
            await page.click('.settings-nav [data-nav="general"]')
            await page.wait_for_timeout(600)
            check(
                "away from Plugins the sidebar keeps only its top-level entry",
                await page.is_hidden(".settings-nav [data-plugin-nav]")
                and await page.is_visible('.settings-nav [data-nav="plugins"]'),
            )
            await page.click('.settings-nav [data-nav="plugins"]')
            await page.wait_for_timeout(600)
            bundled = await page.text_content('[data-plugin="citation-counts"]')
            check("one comes with Synapse", "comes with Synapse" in bundled, bundled[:200])
            toggle = f"{ROW} [data-ptoggle]"
            check("a new plugin is off", not await page.is_checked(toggle))
            perms = await page.text_content(f"{ROW} .p-perms")
            check(
                "what it may do is shown", "Reads your library" in perms and "Offers tools to AI apps" in perms, perms
            )
            await shot(page, "p1-list")

            # turning on asks first; Cancel leaves it off
            await page.click(f"{ROW} .switch")
            await page.wait_for_selector(".dialog")
            detail = await page.text_content(".dialog")
            check(
                "turning on asks first and names the access", "full access" in detail and "Reads your library" in detail
            )
            await shot(page, "p2-confirm")
            await page.click(".dialog button:has-text('Cancel')")
            await page.wait_for_timeout(300)
            check("Cancel leaves it off", not await page.is_checked(toggle) and not plugin("recently-added")["enabled"])

            await page.click(f"{ROW} .switch")
            await page.click(".dialog button:has-text('Turn On')")
            ok = await wait_for(lambda: asyncio.to_thread(lambda: plugin("recently-added")["running"]))
            check("Turn On starts it", ok)
            check("the switch shows it on", await wait_for(lambda: page.is_checked(toggle)))

            # its settings, drawn from the manifest, in a section of its own that its row's button opens
            check(
                "a plugin that is on has a Settings button",
                await wait_for(lambda: page.is_visible(f"{ROW} [data-popen]")),
            )
            check("its row has no settings of its own", await page.locator(f"{ROW} [data-pset]").count() == 0)
            await page.click(f"{ROW} [data-popen]")
            await page.wait_for_selector(f'{SEC} [data-pset="keep_days"]')
            check(
                "the section is in the navigation, under Plugins",
                await wait_for(
                    lambda: page.evaluate(
                        "document.querySelector('.settings-nav [data-nav=\"plugin-recently-added\"]')?.classList.contains('active')"
                    )
                ),
            )
            await page.fill(f'{SEC} [data-pset="keep_days"]', "30")
            await page.press(f'{SEC} [data-pset="keep_days"]', "Tab")
            ok = await wait_for(
                lambda: asyncio.to_thread(lambda: plugin("recently-added")["values"]["keep_days"] == 30)
            )
            check("a number setting is saved", ok)
            await page.fill(f'{SEC} [data-pset="skip_tags"]', "draft, old")
            await page.press(f'{SEC} [data-pset="skip_tags"]', "Tab")
            ok = await wait_for(
                lambda: asyncio.to_thread(lambda: plugin("recently-added")["values"]["skip_tags"] == ["draft", "old"])
            )
            check("a list setting is saved", ok)
            await page.fill(f'{SEC} [data-pset="keep_days"]', "0")
            await page.press(f'{SEC} [data-pset="keep_days"]', "Tab")
            ok = await wait_for(lambda: page.evaluate("!!document.querySelector('.toast')"))
            check("a value out of range is refused", ok and plugin("recently-added")["values"]["keep_days"] == 30)
            check(
                "the field shows the saved value again",
                await wait_for(
                    lambda: page.eval_on_selector(f'{SEC} [data-pset="keep_days"]', "e => e.value === '30'")
                ),
            )
            offers = await page.text_content(f"{SEC} .p-offers")
            check(
                "what it offers is listed", "recently-added__recently_added" in offers and "/recent" in offers, offers
            )
            await shot(page, "p3-details")

            # it works: an item added in the library shows up in its route
            requests.post(BASE + "/api/items", json={"title": "Seen by the plugin"}, timeout=5)
            ok = await wait_for(
                lambda: asyncio.to_thread(
                    lambda: (
                        [i["title"] for i in api("/api/plugins/recently-added/recent")["items"]]
                        == ["Seen by the plugin"]
                    )
                )
            )
            check("its route answers with what it stored", ok)

            await page.click(f"{SEC} [data-plog]")
            await page.wait_for_selector(".plugin-log")
            log = await page.text_content(".plugin-log")
            check("the log opens", "Started (app" in log and "Settings changed: keep_days" in log, log[-300:])
            await shot(page, "p4-log")
            await page.click(".dialog button:has-text('Close')")

            # a plugin that cannot start
            brow = '[data-plugin="broken-one"]'
            await page.click(f"{brow} .switch")
            await page.click(".dialog button:has-text('Turn On')")
            ok = await wait_for(lambda: page.is_visible(f"{brow} .form-error"))
            err = await page.text_content(f"{brow} .form-error") if ok else ""
            check("a plugin that cannot start says why", "RuntimeError: needs a key" in err, err)
            check("and stays off", not await page.is_checked(f"{brow} [data-ptoggle]"))
            await shot(page, "p5-broken")

            # its frontend: a page in the sidebar, a command, a section in the item pane, an item action
            await page.click("[data-close]")  # Settings → back to the library
            entry = '#sidebar [data-page="recently-added.recent"]'
            check("its page has an entry in the sidebar", await wait_for(lambda: page.is_visible(entry)))
            ok = await wait_for(
                lambda: page.eval_on_selector(entry, "e => e.querySelector('.count').textContent === '1'")
            )
            check("with the number added today", ok)
            await page.click(entry)
            await page.wait_for_selector("#page-view .ra-item")
            titles = await page.eval_on_selector_all("#page-view .ra-item", "els => els.map(e => e.textContent)")
            check("the page lists what was added", titles == ["Seen by the plugin"], titles)
            check("the page takes the library's place", not await page.is_visible("#items-pane"))
            await shot(page, "p6-page")
            await page.click("#page-view .ra-item")
            ok = await wait_for(
                lambda: page.evaluate("!document.body.dataset.workspace && !!document.querySelector('.row.selected')")
            )
            check("an item on the page opens in the library", ok)
            sec = '[data-section="x:recently-added.added"] .ip-ext'
            ok = await wait_for(
                lambda: page.evaluate(f"(document.querySelector('{sec}')?.textContent || '').startsWith('Added ')")
            )
            check("the item pane has its section", ok)
            await shot(page, "p7-section")

            # its per-item fields: the item pane, a column, the search
            today = time.strftime("%Y-%m-%d", time.gmtime())
            pf = '[data-pfield="recently-added.added_on"]'
            ok = await wait_for(
                lambda: page.evaluate(f"(document.querySelector('{pf}')?.textContent || '').includes('{today}')")
            )
            check("the item pane shows its field's value", ok)
            await page.click('[data-a="columns"]')
            await page.click(".menu-item:has-text('Added On (Recently Added)')")
            await page.keyboard.press("Escape")
            col = '.head-cell[data-col="f:recently-added.added_on"]'
            check("its field is a column", await wait_for(lambda: page.is_visible(col)))
            cell = '.row.selected .cell[data-col="f:recently-added.added_on"]'
            ok = await wait_for(
                lambda: page.evaluate(f"(document.querySelector('{cell}')?.textContent || '') === '{today}'")
            )
            check("the column shows the value", ok)
            await shot(page, "p7b-column")
            await page.fill("#search-input", "added_on:>2000-01-01")
            ok = await wait_for(lambda: page.evaluate("document.querySelectorAll('.row[data-id]').length === 1"))
            await page.fill("#search-input", "added_on:none")
            ok2 = await wait_for(lambda: page.evaluate("document.querySelectorAll('.row[data-id]').length === 0"))
            check("the search filters by it", ok and ok2)
            await page.press("#search-input", "Escape")
            await page.wait_for_selector(".row[data-id]")
            await page.click(".row[data-id]")
            await page.click(".row.selected", button="right")
            await page.click(".menu-item:has-text('Forget When It Was Added')")
            ok = await wait_for(
                lambda: page.evaluate(
                    "[...document.querySelectorAll('.notification-message')].some(t => t.textContent.includes('Forgotten'))"
                )
            )
            check("its item action runs", ok and api("/api/plugins/recently-added/recent")["items"] == [])

            await page.keyboard.press(f"{mod}+Shift+KeyP")
            await page.wait_for_selector(".palette-input")
            await page.keyboard.type("recent")
            first = await page.text_content(".palette-item.active")
            check("the command palette finds its command", "Show Recently Added" in first, first)
            await shot(page, "p8-palette")
            await page.keyboard.press("Enter")
            check("Enter runs it", await wait_for(lambda: page.evaluate("document.body.dataset.workspace === 'page'")))
            await page.click('#sidebar [data-view="library"]')
            check("the library comes back", await wait_for(lambda: page.evaluate("!document.body.dataset.workspace")))
            await page.keyboard.press(f"{mod}+Alt+KeyR")
            check(
                "its key opens the page",
                await wait_for(lambda: page.evaluate("document.body.dataset.workspace === 'page'")),
            )
            await page.keyboard.press(f"{mod}+Shift+KeyP")
            await page.keyboard.type("new item")
            first = await page.text_content(".palette-item.active")
            check("Synapse's own commands are in the palette", "New Item" in first, first)
            await page.keyboard.press("Escape")
            check("Escape closes it", await wait_for(lambda: page.evaluate("!document.querySelector('.palette')")))
            await open_plugins(page)

            # another window hears about it
            other = await browser.new_page(viewport={"width": 1300, "height": 950})
            await other.goto(BASE)
            await other.wait_for_selector(".row[data-id], .empty-state, #item-list", timeout=15000)
            await open_plugins(other)
            await page.click(f"{ROW} .switch")  # turn it off in the first window
            ok = await wait_for(
                lambda: other.evaluate(f"!document.querySelector('{ROW} [data-ptoggle]').checked"), timeout=12
            )
            check("turning it off shows in another window", ok)
            await page.click("[data-close]")
            ok = await wait_for(
                lambda: page.evaluate(
                    "!document.querySelector('#sidebar [data-page]') && document.body.dataset.workspace !== 'page'"
                )
            )
            check("turned off, its page and sidebar entry are gone", ok)
            check(
                "off: its route is gone", requests.get(BASE + "/api/plugins/recently-added/recent").status_code == 404
            )

            # a plugin being written: --dev-plugins restarts it when a file changes, and the window follows
            live = DEV / "live-page"
            live.mkdir()
            (live / "manifest.json").write_text(
                json.dumps(
                    {"id": "live-page", "name": "Live Page", "version": "0.1", "api": "^1.0", "frontend": "main.ts"}
                )
            )
            source = (
                "export function activate(p: any): void {\n"
                "  p.pages.register({ id: 'main', title: 'Live Page', mount(el: HTMLElement) { el.textContent = 'Version %s'; } });\n"
                "}\n"
            )
            (live / "main.ts").write_text(source % 1)
            requests.post(BASE + "/api/plugin-host/live-page/enable", timeout=60)
            await page.evaluate("window.zcPlugins.sync()")
            entry = '#sidebar [data-page="live-page.main"]'
            await page.wait_for_selector(entry, timeout=15000)
            await page.click(entry)
            body = "(document.querySelector('#page-view .page-body')?.textContent || '')"
            check(
                "a TypeScript plugin's page shows",
                await wait_for(lambda: page.evaluate(f"{body}.includes('Version 1')")),
            )
            (live / "main.ts").write_text(source % 2)
            ok = await wait_for(lambda: page.evaluate(f"{body}.includes('Version 2')"), timeout=20)
            check("saving its file reloads it in the open window, on the same page", ok)
            await shot(page, "p9-dev")

            # Frontend activation can fail after registering UI. It must leave no controls behind.
            bad_front = plugins / "bad-frontend"
            bad_front.mkdir()
            (bad_front / "manifest.json").write_text(
                json.dumps(
                    {"id": "bad-frontend", "name": "Bad Frontend", "version": "1", "api": "^1.0", "frontend": "main.js"}
                )
            )
            (bad_front / "main.js").write_text(
                "export function activate(p) { "
                "p.pages.register({id:'leftover',title:'Leftover',mount() {}}); "
                "p.commands.register({id:'leftover',title:'Leftover',run() {}}); "
                "throw new Error('activation failed on purpose'); }"
            )
            requests.post(BASE + "/api/plugin-host/bad-frontend/enable").raise_for_status()
            await page.evaluate("window.zcPlugins.sync()")
            check(
                "failed frontend activation removes its page and command",
                await page.evaluate("""async () => {
                const { pages, commands } = await import('/js/extend.js');
                return !pages.get('bad-frontend.leftover') && !commands.get('bad-frontend.leftover')
                    && !window.zcPlugins.loaded().includes('bad-frontend');
            }"""),
            )

            # A route disappearing during async activation must settle, rather than hang host sync.
            pending = plugins / "pending-frontend"
            pending.mkdir()
            (pending / "manifest.json").write_text(
                json.dumps(
                    {
                        "id": "pending-frontend",
                        "name": "Pending Frontend",
                        "version": "1",
                        "api": "^1.0",
                        "frontend": "main.js",
                    }
                )
            )
            (pending / "main.js").write_text("export async function activate(p) { await p.backend.get('/gone'); }")

            async def stopped_route(route):
                requests.post(BASE + "/api/plugin-host/pending-frontend/disable").raise_for_status()
                await route.continue_()

            await page.route("**/api/plugins/pending-frontend/gone", stopped_route)
            requests.post(BASE + "/api/plugin-host/pending-frontend/enable").raise_for_status()
            await page.evaluate("window.zcPlugins.sync()")
            check(
                "a stopped route does not hang frontend synchronization",
                await page.evaluate("!window.zcPlugins.loaded().includes('pending-frontend')"),
            )
            requests.post(BASE + "/api/plugin-host/pending-frontend/disable").raise_for_status()
            requests.post(BASE + "/api/plugin-host/bad-frontend/disable").raise_for_status()

            # Keep the authoring quickstart's public-API example runnable in the browser.
            requests.post(BASE + "/api/plugin-host/hello-library/enable").raise_for_status()
            await page.evaluate("window.zcPlugins.sync()")
            hello = '#sidebar [data-page="hello-library.main"]'
            await page.click(hello)
            check(
                "authoring example opens its offline page",
                await wait_for(lambda: page.is_visible('#page-view p:text-is("Hello from your plugin.")')),
            )
            requests.put(
                BASE + "/api/plugin-host/hello-library/settings", json={"message": "<b>A new greeting</b>"}
            ).raise_for_status()
            await page.click('#page-view button:text-is("Refresh")')
            check(
                "example refresh reads changed settings as text",
                await wait_for(lambda: page.is_visible('#page-view p:text-is("<b>A new greeting</b>")'))
                and await page.locator("#page-view .page-body b").count() == 0,
            )
            await page.click('#sidebar [data-view="library"]')
            await page.keyboard.press(f"{mod}+Alt+KeyY")
            check(
                "example shortcut opens its page",
                await wait_for(lambda: page.is_visible('#page-view p:text-is("<b>A new greeting</b>")')),
            )
            requests.post(BASE + "/api/plugin-host/hello-library/disable").raise_for_status()
            await page.evaluate("window.zcPlugins.sync()")
            check(
                "example disable removes its open page",
                await wait_for(
                    lambda: page.evaluate(
                        """document.body.dataset.workspace !== 'page' && !document.querySelector('[data-page="hello-library.main"]')"""
                    )
                ),
            )
            requests.post(BASE + "/api/plugin-host/hello-library/enable").raise_for_status()
            await page.evaluate("window.zcPlugins.sync()")
            await page.click(hello)
            check(
                "example reactivation restores one page and its setting",
                await page.locator(hello).count() == 1
                and await wait_for(lambda: page.is_visible('#page-view p:text-is("<b>A new greeting</b>")')),
            )
            check("no JavaScript errors", not errors, errors[:3])
            # Safe mode keeps core features usable while suppressing installed plugins.
            requests.post(BASE + "/api/plugin-host/recently-added/enable").raise_for_status()
            srv.terminate()
            srv.wait()
            srv = subprocess.Popen(
                [
                    sys.executable,
                    "server.py",
                    "--port",
                    str(PORT),
                    "--no-browser",
                    "--data-dir",
                    str(DATA),
                    "--safe-mode",
                ],
                cwd=APP,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(100):
                try:
                    requests.get(BASE + "/api/settings", timeout=1)
                    break
                except requests.RequestException:
                    await asyncio.sleep(0.2)
            safe = await browser.new_page(viewport={"width": 1300, "height": 950})
            await safe.goto(BASE)
            await open_plugins(safe)
            # No plugin that comes with Synapse is core any more: safe mode stops them all, Graph
            # included, and leaves their switches alone until Synapse starts normally.
            graph_row = '.plugin-row[data-plugin="graph"]'
            check(
                "safe mode stops plugins that come with Synapse",
                not plugin("graph")["running"] and await safe.is_disabled(f"{graph_row} [data-ptoggle]"),
            )
            check(
                "safe mode suppresses installed plugins",
                not plugin("recently-added")["running"] and await safe.is_disabled(f"{ROW} [data-ptoggle]"),
            )
            check(
                "a stopped plugin's row says safe mode",
                "Safe mode" in (await safe.inner_text(f"{graph_row} .p-name")),
            )
            check(
                "the page says why nothing runs",
                await safe.is_visible("text=Synapse was started in safe mode"),
            )
            await browser.close()
    finally:
        srv.terminate()
        srv.wait()
        shutil.rmtree(DATA, ignore_errors=True)
        shutil.rmtree(DEV, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
