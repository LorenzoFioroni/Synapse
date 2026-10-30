"""Reference provider titles and saved-source fallback in the real item pane, entirely offline."""

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

PORT = 8811
BASE = f"http://127.0.0.1:{PORT}"
results = []


def check(name, ok):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name, flush=True)


async def main():
    guard(PORT)
    with tempfile.TemporaryDirectory(prefix="synapse-reference-sources-") as data:
        plugin = Path(data) / "plugins" / "offline-refs"
        plugin.mkdir(parents=True)
        (plugin / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "offline-refs",
                    "name": "Offline References",
                    "version": "1.0",
                    "api": "^1.5",
                    "backend": "server.py",
                }
            )
        )
        (plugin / "server.py").write_text("""
def activate(p):
    @p.reference_source("catalogues", title="Catalogues", order=1, subsources={"index": "Catalogue <b>title</b>"})
    def refs(q):
        return {"source": "index", "references": [{"title": "Cited fixture"}]}
""")
        server = subprocess.Popen(
            [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", data],
            cwd=APP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(80):
                try:
                    requests.get(BASE + "/api/settings", timeout=1).raise_for_status()
                    break
                except requests.RequestException:
                    time.sleep(0.1)
            requests.put(
                BASE + "/api/settings", json={"references_auto": False, "add_fetch_references": "never"}, timeout=5
            ).raise_for_status()
            requests.post(BASE + "/api/plugin-host/offline-refs/enable", timeout=5).raise_for_status()
            paper = requests.post(
                BASE + "/api/items", json={"title": "Reference fixture", "doi": "10.1234/fixture"}, timeout=5
            ).json()
            path = f"/api/items/{paper['id']}/references"
            fetched = requests.post(BASE + path, timeout=5)
            fetched.raise_for_status()
            check(
                "Selected sub-source has a qualified identity",
                fetched.json()["fetch"]["source"] == "offline-refs.catalogues.index",
            )
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    page = await browser.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    async def show():
                        await page.goto(BASE)
                        await page.locator("#items-pane .row").filter(has_text="Reference fixture").click()
                        section = page.locator('[data-section="references"]')
                        if "collapsed" in (await section.get_attribute("class") or ""):
                            await section.locator(".section-head").click()
                        await page.wait_for_selector(".ip-refs-status")

                    await show()
                    status = page.locator(".ip-refs-status")
                    await page.wait_for_function(
                        "() => (document.querySelector('.ip-refs-status')?.textContent.includes('Catalogue <b>title</b>'))"
                    )
                    check(
                        "The item pane uses the declared provider title",
                        "Catalogue <b>title</b>" in await status.inner_text(),
                    )
                    check("Provider titles are escaped as text", await status.locator("b").count() == 0)
                    requests.post(BASE + "/api/plugin-host/offline-refs/disable", timeout=5).raise_for_status()
                    saved = requests.get(BASE + path, timeout=5).json()
                    check(
                        "Disable preserves the recorded identity and references",
                        saved["fetch"]["source"] == "offline-refs.catalogues.index" and len(saved["references"]) == 1,
                    )
                    await show()
                    check(
                        "Unknown stored providers retain their identity as the label",
                        "offline-refs.catalogues.index" in await status.inner_text(),
                    )
                    requests.post(BASE + "/api/plugin-host/offline-refs/enable", timeout=5).raise_for_status()
                    await show()
                    check(
                        "Reactivation restores the provider title",
                        "Catalogue <b>title</b>" in await status.inner_text(),
                    )
                    check("No JavaScript errors", not errors)
                finally:
                    await browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


sys.exit(asyncio.run(main()))
