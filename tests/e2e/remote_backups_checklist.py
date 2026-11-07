"""Browser setup for remote backups. Provider network calls use explicit browser stubs;
archive delivery/recovery is covered with real rclone in tests/test_remote_backup.py.
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from playwright.async_api import async_playwright, expect

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from tests.e2e._guard import guard  # noqa: E402

PORT = 8814
BASE = f"http://127.0.0.1:{PORT}"
results = []


def check(name, condition):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + name, flush=True)


async def main():
    guard(PORT)
    with tempfile.TemporaryDirectory(prefix="synapse-remote-backups-") as data:
        server = subprocess.Popen(
            [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", data],
            cwd=APP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                try:
                    requests.get(BASE + "/api/settings", timeout=1)
                    break
                except requests.RequestException:
                    await asyncio.sleep(0.2)
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                context = await browser.new_context(viewport={"width": 1300, "height": 1000})
                page = await context.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                await page.goto(BASE)
                await page.keyboard.press("Meta+," if sys.platform == "darwin" else "Control+,")
                await page.click('.settings-nav [data-nav="backups"]')
                await page.wait_for_selector("[data-backup-destination]")
                check(
                    "local backups remain the default", await page.input_value("[data-backup-destination]") == "local"
                )
                await page.select_option("[data-backup-destination]", "remote")
                await page.wait_for_selector("[data-remote-provider]")
                await page.select_option("[data-remote-provider]", "s3")
                for name, value in {
                    "provider": "Other",
                    "access_key_id": "example-access",
                    "secret_access_key": "example-secret",
                    "endpoint": "https://storage.example",
                }.items():
                    await page.fill(f'[data-remote-option="{name}"]', value)
                await page.fill("[data-remote-folder]", "bucket/synapse")
                async with page.expect_response(
                    lambda r: r.url.endswith("/api/backups/remote") and r.request.method == "PUT"
                ) as saved:
                    await page.click("[data-remote-save]")
                check("connection is saved entirely through Settings", (await saved.value).status == 200)
                await expect(page.locator('[data-remote-option="secret_access_key"]')).to_have_value("")
                check(
                    "saved secrets are cleared from the form",
                    await page.input_value('[data-remote-option="access_key_id"]') == "",
                )
                check(
                    "server persistence excludes ordinary preferences",
                    (Path(data) / "backup-remote/rclone.conf").exists()
                    and "example-secret" not in (Path(data) / "settings.json").read_text(),
                )
                await page.route("**/api/backups/remote/test", lambda route: route.fulfill(json={"ok": True}))
                async with page.expect_response("**/api/backups/remote/test") as tested:
                    await page.click("[data-remote-test]")
                check("Test Connection is available without a terminal", (await tested.value).status == 200)
                old_set = "synapse-" + "a" * 32
                await page.route("**/api/backups/remote/sets", lambda route: route.fulfill(json={"sets": [old_set]}))
                await page.click("[data-remote-find-sets]")
                await page.wait_for_selector(f'[data-remote-namespace] option[value="{old_set}"]', state="attached")
                check(
                    "existing backup sets can be selected for recovery",
                    await page.locator("[data-remote-namespace] option").count() == 2,
                )
                await page.select_option("[data-remote-provider]", "drive")
                await page.fill('[data-remote-option="client_id"]', "example-client")
                await page.fill('[data-remote-option="client_secret"]', "example-client-secret")
                check(
                    "cloud setup shows the server callback",
                    BASE + "/api/backups/remote/callback" in (await page.text_content("[data-remote-auth-info]")),
                )
                await context.route(
                    "https://accounts.google.com/**",
                    lambda route: route.fulfill(
                        body="<html><body>Provider consent (test stub)</body></html>", content_type="text/html"
                    ),
                )
                async with page.expect_popup() as popup:
                    await page.click("[data-remote-connect]")
                auth = await popup.value
                await auth.wait_for_url("https://accounts.google.com/**")
                check(
                    "Connect Account opens provider consent from Settings",
                    "code_challenge=" in auth.url and "redirect_uri=" in auth.url,
                )
                await auth.close()
                # The settings poll must not discard an in-progress connection edit.
                await page.fill('[data-remote-option="client_secret"]', "unsaved-draft")
                await page.wait_for_timeout(4500)
                check(
                    "status polling preserves an unsaved credential draft",
                    await page.input_value('[data-remote-option="client_secret"]') == "unsaved-draft",
                )
                shots = os.environ.get("SHOTS")
                if shots:
                    Path(shots).mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(Path(shots) / "remote-backup-settings.png"))
                await page.click("#settings-view [data-close]")
                await page.keyboard.press("Meta+," if sys.platform == "darwin" else "Control+,")
                await page.click('.settings-nav [data-nav="backups"]')
                await page.wait_for_selector("[data-remote-provider]")
                check(
                    "connection survives reopening Settings",
                    await page.input_value("[data-remote-provider]") == "drive",
                )
                check(
                    "credentials never appear in the status response",
                    "example-client-secret" not in json.dumps(requests.get(BASE + "/api/backups").json()),
                )
                check("no JavaScript errors", not errors)
                await browser.close()
        finally:
            server.kill()
            server.wait()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


sys.exit(asyncio.run(main()))
