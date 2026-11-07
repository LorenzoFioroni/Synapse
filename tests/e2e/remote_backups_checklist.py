"""Browser setup for remote backups. Provider network calls use explicit browser stubs;
archive delivery/recovery is covered with real rclone in tests/test_remote_backup.py.
"""

import asyncio
import configparser
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
from tests.certificates import make_certificate  # noqa: E402
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
                await page.select_option("[data-remote-provider]", "ftp")
                check(
                    "FTP defaults to explicit TLS",
                    await page.input_value('[data-remote-option="security"]') == "explicit",
                )
                obscured = "QW4tZXhhbXBsZS1JVjEyMy1jaXBoZXJ0ZXh0"
                for name, value in {
                    "host": "backup.example",
                    "port": "47406",
                    "user": "backup-user",
                    "pass": obscured,
                }.items():
                    await page.fill(f'[data-remote-option="{name}"]', value)
                await page.fill("[data-remote-folder]", "Backups")
                await page.check("[data-remote-password-obscured]")
                await page.wait_for_timeout(4500)
                check(
                    "polling preserves imported password and format choice",
                    await page.is_checked("[data-remote-password-obscured]")
                    and await page.input_value('[data-remote-option="pass"]') == obscured,
                )
                async with page.expect_response(
                    lambda r: r.url.endswith("/api/backups/remote") and r.request.method == "PUT"
                ) as saved:
                    await page.click("[data-remote-save]")
                check("FTP connection saves without a terminal", (await saved.value).status == 200)
                await expect(page.locator('[data-remote-option="pass"]')).to_have_value("")
                check(
                    "password format resets after saving", not await page.is_checked("[data-remote-password-obscured]")
                )
                parser = configparser.ConfigParser(interpolation=None)
                parser.read(Path(data) / "backup-remote/rclone.conf")
                check(
                    "explicit FTPS and imported password are persisted correctly",
                    parser["remote"]["tls"] == "false"
                    and parser["remote"]["explicit_tls"] == "true"
                    and parser["remote"]["port"] == "47406"
                    and parser["remote"]["pass"] == obscured,
                )
                await page.click("#settings-view [data-close]")
                await page.keyboard.press("Meta+," if sys.platform == "darwin" else "Control+,")
                await page.click('.settings-nav [data-nav="backups"]')
                await page.wait_for_selector('[data-remote-option="security"]')
                check(
                    "FTP security and custom port survive reopening",
                    await page.input_value('[data-remote-option="security"]') == "explicit"
                    and await page.input_value('[data-remote-option="port"]') == "47406",
                )
                await page.select_option('[data-remote-option="security"]', "implicit")
                await page.fill('[data-remote-option="port"]', "")
                async with page.expect_response(
                    lambda r: r.url.endswith("/api/backups/remote") and r.request.method == "PUT"
                ) as saved:
                    await page.click("[data-remote-save]")
                check("FTP security can change while keeping a saved password", (await saved.value).status == 200)
                parser.read(Path(data) / "backup-remote/rclone.conf")
                check(
                    "implicit FTPS uses its default port and preserves the password",
                    parser["remote"]["tls"] == "true"
                    and parser["remote"]["explicit_tls"] == "false"
                    and parser["remote"]["port"] == "990"
                    and parser["remote"]["pass"] == obscured,
                )
                ca_input = page.locator("[data-remote-ca]")
                await ca_input.set_input_files(
                    {"name": "invalid.crt", "mimeType": "text/plain", "buffer": b"not a certificate"}
                )
                before = (Path(data) / "backup-remote/rclone.conf").read_bytes()
                async with page.expect_response(
                    lambda r: r.url.endswith("/api/backups/remote") and r.request.method == "PUT"
                ) as saved:
                    await page.click("[data-remote-save]")
                check(
                    "invalid certificate is rejected without changing the connection",
                    (await saved.value).status == 400
                    and (Path(data) / "backup-remote/rclone.conf").read_bytes() == before,
                )
                certificate = make_certificate()[0]
                await ca_input.set_input_files(
                    {"name": "ca-certificates.crt", "mimeType": "text/plain", "buffer": certificate.encode()}
                )
                await page.wait_for_timeout(4500)
                check(
                    "polling preserves a selected certificate",
                    "ca-certificates.crt selected" in await page.inner_text("[data-remote-ca-state]"),
                )
                async with page.expect_response(
                    lambda r: r.url.endswith("/api/backups/remote") and r.request.method == "PUT"
                ) as saved:
                    await page.click("[data-remote-save]")
                check("trusted certificate uploads through Settings", (await saved.value).status == 200)
                await expect(page.locator("[data-remote-ca-state]")).to_contain_text("Saved: 1 certificate")
                parser.read(Path(data) / "backup-remote/rclone.conf")
                stored_ca = Path(parser["remote"]["override.ca_cert"])
                check(
                    "uploaded certificate is stored privately on the server",
                    stored_ca.read_text() == certificate and stored_ca.stat().st_mode & 0o777 == 0o600,
                )
                await page.click("#settings-view [data-close]")
                await page.keyboard.press("Meta+," if sys.platform == "darwin" else "Control+,")
                await page.click('.settings-nav [data-nav="backups"]')
                await expect(page.locator("[data-remote-ca-state]")).to_contain_text("Saved: 1 certificate")
                check(
                    "saved certificate status survives reopening Settings",
                    await page.input_value("[data-remote-ca]") == "",
                )
                if shots:
                    await page.locator("[data-remote-ca]").scroll_into_view_if_needed()
                    await page.screenshot(path=str(Path(shots) / "trusted-certificate-settings.png"))
                async with page.expect_response(
                    lambda r: r.url.endswith("/api/backups/remote") and r.request.method == "PUT"
                ) as saved:
                    await page.click("[data-remote-save]")
                check(
                    "saving other settings preserves the certificate",
                    (await saved.value).status == 200 and stored_ca.exists(),
                )
                replacement = make_certificate()[0]
                await page.locator("[data-remote-ca]").set_input_files(
                    {"name": "replacement.pem", "mimeType": "text/plain", "buffer": replacement.encode()}
                )
                async with page.expect_response(
                    lambda r: r.url.endswith("/api/backups/remote") and r.request.method == "PUT"
                ) as saved:
                    await page.click("[data-remote-save]")
                parser.read(Path(data) / "backup-remote/rclone.conf")
                replacement_ca = Path(parser["remote"]["override.ca_cert"])
                check(
                    "replacing a certificate updates server trust",
                    (await saved.value).status == 200
                    and replacement_ca.read_text() == replacement
                    and not stored_ca.exists(),
                )
                await page.click("[data-remote-ca-remove]")
                check(
                    "certificate removal waits for Save connection",
                    replacement_ca.exists() and "will be removed" in await page.inner_text("[data-remote-ca-state]"),
                )
                async with page.expect_response(
                    lambda r: r.url.endswith("/api/backups/remote") and r.request.method == "PUT"
                ) as saved:
                    await page.click("[data-remote-save]")
                check(
                    "certificate can be removed through Settings",
                    (await saved.value).status == 200 and not replacement_ca.exists(),
                )
                await expect(page.locator("[data-remote-ca-state]")).to_contain_text("No custom certificate")
                await page.select_option("[data-remote-provider]", "sftp")
                check(
                    "SFTP keeps SSH host verification instead of TLS uploads",
                    await page.locator("[data-remote-ca]").count() == 0,
                )
                await page.select_option("[data-remote-provider]", "ftp")
                if shots:
                    await page.screenshot(path=str(Path(shots) / "ftp-backup-settings.png"))
                check("no JavaScript errors", not errors)
                await browser.close()
        finally:
            server.kill()
            server.wait()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


sys.exit(asyncio.run(main()))
