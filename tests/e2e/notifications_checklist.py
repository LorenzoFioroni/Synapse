"""Notification history, job integration and optional OS delivery. Isolated port 8808."""

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

PORT = 8808
BASE = f"http://127.0.0.1:{PORT}"
results = []


def check(name, ok):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'} {name}", flush=True)


MOCK_OS = """() => {
  Object.defineProperty(crypto, 'randomUUID', {value: undefined});
  window.osNotices = []; window.badges = []; window.permissionRequests = 0;
  class FakeNotification {
    static permission = 'default';
    static async requestPermission() { window.permissionRequests++; return this.permission = 'granted'; }
    constructor(title, options) { window.osNotices.push({title, ...options}); }
    close() {}
  }
  window.Notification = window.TestNotification = FakeNotification;
  navigator.setAppBadge = async count => window.badges.push(count);
  navigator.clearAppBadge = async () => window.badges.push(0);
}"""


async def main():
    guard(PORT)
    with tempfile.TemporaryDirectory(prefix="synapse-notifications-") as data:
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
            item = requests.post(BASE + "/api/items", json={"title": "A test paper"}, timeout=5).json()
            async with async_playwright() as p:
                browser = await p.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None)
                context = await browser.new_context(viewport={"width": 1300, "height": 900})
                await context.add_init_script(f"({MOCK_OS})()")
                page = await context.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                await page.goto(BASE)
                bell = page.locator('[data-a="notifications"]')
                await bell.wait_for()
                check("OS permission is never requested on startup", await page.evaluate("permissionRequests === 0"))
                await bell.click()
                center = page.locator("#notification-center")
                check("Bell opens a labeled center beside Settings", await center.is_visible())
                check(
                    "Notification center only contains history actions",
                    await center.locator('[data-n="desktop"]').count() == 0,
                )
                await page.keyboard.press("Escape")
                check(
                    "Escape closes center and returns focus",
                    not await center.is_visible() and await bell.evaluate("e => e === document.activeElement"),
                )
                await page.locator('[data-a="settings"]').click()
                await page.locator('[data-nav="notifications"]').click()
                os_button = page.locator("[data-desktop-notifications]")
                await os_button.click()
                check(
                    "Settings requests OS permission only after an explicit click",
                    await page.evaluate("permissionRequests === 1")
                    and await os_button.inner_text() == "Disable OS notifications",
                )
                await os_button.click()
                check(
                    "Settings can persistently disable OS delivery",
                    await os_button.inner_text() == "Enable OS notifications",
                )
                await os_button.click()
                check(
                    "Re-enabling after permission was granted does not ask again",
                    await page.evaluate("permissionRequests === 1")
                    and await os_button.inner_text() == "Disable OS notifications",
                )
                await page.locator("[data-close]").click()
                launcher_icon = page.locator('link[rel="icon"][sizes="192x192"]')
                launcher_href = await launcher_icon.get_attribute("href")
                await page.evaluate("""async () => {
                  const m = await import('/js/notifications/center.js');
                  m.notify('<unsafe> ready', {type:'success', desktop:true});
                  m.notify('An ordinary error', {type:'error'});
                  window.job = m.startJob('Long job', () => window.cancelled = true);
                  window.job.set('Long job halfway', 2, 4);
                }""")
                check(
                    "Normal Success can reach OS without error severity",
                    await page.evaluate("osNotices.length === 1 && osNotices[0].body === '<unsafe> ready'"),
                )
                check(
                    "Unread bell and app icon badges count notices and jobs",
                    await bell.get_attribute("aria-label") == "Notifications, 3 unread"
                    and await page.evaluate("badges.at(-1) === 3"),
                )
                await page.wait_for_function(
                    "document.querySelector('link[rel=icon]').href.startsWith('data:image/png')"
                )
                check(
                    "Browser tab icon receives a badge without replacing the launcher icon",
                    await launcher_icon.get_attribute("href") == launcher_href,
                )
                await bell.click()
                check(
                    "Message text is escaped",
                    await center.locator("unsafe").count() == 0 and "<unsafe> ready" in await center.inner_text(),
                )
                check(
                    "Job progress lives in center, not bottom banners",
                    await center.locator("progress").get_attribute("value") == "50"
                    and await page.locator("#toasts .progress-toast").count() == 0,
                )
                await center.locator('[data-n="read"]').click()
                await page.evaluate("window.job.set('Long job nearly done',3,4)")
                check(
                    "Progress updates do not reset read state",
                    await bell.get_attribute("aria-label") == "Notifications",
                )
                await center.locator('[data-n="clear"]').click()
                check("Clear completed protects running jobs", await center.locator("article").count() == 1)
                await center.locator('[data-n="cancel"]').click()
                check(
                    "Cancel invokes the job handler once",
                    await page.evaluate("window.cancelled === true")
                    and await center.locator('[data-n="cancel"]').count() == 0,
                )
                await page.evaluate("window.job.finish('Stopped early', {type:'warning'})")
                check(
                    "Result replaces the running job and is unread",
                    await center.locator("article").count() == 1
                    and await center.locator("progress").count() == 0
                    and await bell.get_attribute("aria-label") == "Notifications, 1 unread",
                )
                await page.screenshot(path="/private/tmp/synapse-notifications.png")
                await page.evaluate("Notification.permission='denied'")
                await page.evaluate(
                    "import('/js/notifications/center.js').then(m=>m.notify('No permission', {desktop:true}))"
                )
                check(
                    "Denied OS permission keeps the notice in center",
                    "No permission" in await center.inner_text() and await page.evaluate("osNotices.length === 1"),
                )
                await page.evaluate("delete window.Notification")
                await page.evaluate(
                    "import('/js/notifications/center.js').then(m=>m.notify('Unsupported OS', {desktop:true}))"
                )
                check("Missing OS API keeps the notice in center", "Unsupported OS" in await center.inner_text())

                # Exercise a production long-job caller, faking only the remote lookup result.
                async def lookup(route):
                    await asyncio.sleep(0.3)
                    await route.fulfill(json={"status": "error", "message": "No open PDF"})

                await page.route("**/api/items/*/find_pdf", lookup)
                await page.evaluate(
                    """id => { void import('/js/actions.js').then(m=>m.findPdfs([id])); }""", item["id"]
                )
                await page.wait_for_function(
                    "[...document.querySelectorAll('.notification-message')].some(e=>e.textContent.includes('Found 0 PDFs'))"
                )
                check(
                    "PDF lookup result stays in center without bottom result banner",
                    await page.locator("#toasts .toast").count() == 0,
                )

                # Actual plugin activation/host notification path, with deterministic daily news.
                await page.evaluate("window.Notification=window.TestNotification; Notification.permission='granted'")
                news_sent = False

                async def news(route):
                    nonlocal news_sent
                    payload = None if news_sent else {"day": "2026-10-24", "count": 7, "connected": 2}
                    news_sent = True
                    await route.fulfill(json={"news": payload})

                await page.route("**/api/plugins/arxiv-today/news", news)
                r = requests.post(BASE + "/api/plugin-host/arxiv-today/enable", timeout=10)
                r.raise_for_status()
                await page.evaluate("window.zcPlugins.sync()")
                await page.wait_for_function(
                    "[...document.querySelectorAll('.notification-message')].some(e=>e.textContent.includes('7 new papers'))"
                )
                check("arXiv ready notice uses notification center", await page.locator("#toasts .toast").count() == 0)
                check(
                    "arXiv Success independently requests OS delivery",
                    await page.evaluate("osNotices.some(n=>n.body.includes('7 new papers'))"),
                )
                await page.evaluate("import('/js/notifications/center.js').then(m=>m.startJob('Reloading job'))")
                await page.reload()
                await bell.wait_for()
                await bell.click()
                arxiv = center.locator("article").filter(has_text="7 new papers")
                await arxiv.locator('[data-n="action"]').wait_for()
                check(
                    "Reload marks this window’s unfinished job interrupted",
                    "check the result before retrying" in await center.inner_text(),
                )
                check(
                    "History and arXiv Show action survive reload",
                    await arxiv.count() == 1 and await arxiv.locator('[data-n="action"]').count() == 1,
                )
                requests.post(BASE + "/api/plugin-host/arxiv-today/disable", timeout=10).raise_for_status()
                await page.evaluate("window.zcPlugins.sync()")
                check(
                    "Plugin stop retains history and removes its navigation action",
                    await arxiv.count() == 1 and await arxiv.locator('[data-n="action"]').count() == 0,
                )
                requests.post(BASE + "/api/plugin-host/arxiv-today/enable", timeout=10).raise_for_status()
                await page.evaluate("window.zcPlugins.sync()")
                await arxiv.locator('[data-n="action"]').wait_for()
                check("Plugin restart restores persistent source navigation", True)
                await arxiv.locator('[data-n="action"]').click()
                await page.wait_for_function(
                    "document.querySelector('#sidebar .selected')?.textContent.includes('arXiv') || document.querySelector('#items-pane')?.textContent.includes('arXiv')"
                )
                check("Persisted Show action opens arXiv source", True)

                second = await context.new_page()
                await second.goto(BASE)
                await second.locator('[data-a="notifications"]').wait_for()
                await page.evaluate("import('/js/notifications/center.js').then(m=>m.notify('Other window notice'))")
                await second.locator('[data-a="notifications"]').click()
                await second.wait_for_function(
                    "document.querySelector('#notification-center').textContent.includes('Other window notice')"
                )
                check("Notifications synchronize across windows", True)
                await second.locator('[data-n="read"]').click()
                await page.wait_for_function(
                    "document.querySelector('[data-a=notifications]').getAttribute('aria-label')==='Notifications'"
                )
                check("Read state synchronizes across windows", True)
                check("No browser errors", not errors)
                await browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
    print(f"\n{sum(results)}/{len(results)} checks passed", flush=True)
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
