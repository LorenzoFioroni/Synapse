"""Browser checklist for passkeys and AI access: over HTTPS, the computer adds a passkey in
Settings → Devices; an unpaired iPad signs in with it from the "not paired" page, without a code;
by IP address or without HTTPS the page explains why it cannot; Settings → AI Access makes an access
token whose command lets another computer use /mcp, and removing the token locks it out.

    python tests/e2e/passkeys_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server with --lan and a throw-away self-signed certificate for ``synapse.test`` on
port 8794, with a throw-away data folder; your library is not touched. Each browser maps
``synapse.test`` to an address of its own: the computer's to 127.0.0.1, the iPad's to this
machine's LAN address, which the launcher (as in devices_checklist.py) treats as another device.
Chromium's virtual authenticator stands in for the passkey; the iPad gets a copy of the
computer's, as a synced passkey would arrive.
"""

import asyncio
import datetime
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
import urllib3
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from backend.access import lan  # noqa: E402
from tests.e2e._guard import guard  # noqa: E402

PORT = 8794
NAME = "synapse.test"
SITE = f"https://{NAME}:{PORT}"
LOCAL = f"https://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-passkeys-e2e-")
SHOTS = os.environ.get("SHOTS")
results = []
mod = "Meta" if sys.platform == "darwin" else "Control"
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
http = requests.Session()
http.trust_env = False  # only this machine's own server: never through a proxy
http.verify = False  # the throw-away certificate

LAUNCHER = """
import runpy, sys
from backend.access import lan
lan.is_this_computer = lambda a: (lambda ip: ip is not None and ip.is_loopback)(lan.parse_ip(a))
sys.argv = ["server.py"] + sys.argv[1:]
runpy.run_path("server.py", run_name="__main__")
"""


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


def self_signed(folder: Path) -> tuple[str, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NAME)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(NAME)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = folder / "cert.pem", folder / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return str(cert_path), str(key_path)


async def authenticator(page):
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("WebAuthn.enable")
    res = await cdp.send(
        "WebAuthn.addVirtualAuthenticator",
        {
            "options": {
                "protocol": "ctap2",
                "transport": "internal",
                "hasResidentKey": True,
                "hasUserVerification": True,
                "isUserVerified": True,
                "automaticPresenceSimulation": True,
            }
        },
    )
    return cdp, res["authenticatorId"]


async def fill_prompt(page, value):
    await page.wait_for_selector(".dialog input")
    await page.fill(".dialog input", value)
    await page.keyboard.press("Enter")


async def main():
    guard(PORT)
    ip = next((a for a in lan.own_addresses() if ":" not in a), None)
    if not ip:
        sys.exit("This machine has no LAN IPv4 address to test with.")
    cert, key = self_signed(Path(DATA))
    srv = subprocess.Popen(
        [sys.executable, "-c", LAUNCHER, "--lan", "--port", str(PORT), "--no-browser", "--data-dir", DATA]
        + ["--cert", cert, "--key", key, "--allow-host", NAME],
        cwd=APP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                http.get(LOCAL + "/api/settings", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        http.post(f"{LOCAL}/api/items", json={"title": "Paper on the Mac"})

        async with async_playwright() as p:
            flags = ["--ignore-certificate-errors"]
            # Only this machine's own server: never through a proxy from the environment.
            env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
            mac_browser = await p.chromium.launch(args=flags + [f"--host-resolver-rules=MAP {NAME} 127.0.0.1"], env=env)
            ipad_browser = await p.chromium.launch(args=flags + [f"--host-resolver-rules=MAP {NAME} {ip}"], env=env)
            mac_ctx = await mac_browser.new_context(viewport={"width": 1300, "height": 950}, ignore_https_errors=True)
            mac = await mac_ctx.new_page()
            ipad_ctx = await ipad_browser.new_context(**p.devices["iPad Pro 11"], ignore_https_errors=True)
            ipad = await ipad_ctx.new_page()
            errors = []
            mac.on("pageerror", lambda e: errors.append("mac: " + str(e)))
            ipad.on("pageerror", lambda e: errors.append("ipad: " + str(e)))

            # -- an unpaired device -------------------------------------------------------------
            r = await ipad.goto(f"https://{ip}:{PORT}/")
            check(
                "by IP address the sign-in page explains passkeys need a name",
                r.status == 401 and await ipad.is_visible("#nopasskey") and not await ipad.is_visible("#passkey"),
            )
            r = await ipad.goto(SITE + "/")
            check(
                "by name over HTTPS it offers Sign In with a Passkey",
                r.status == 401 and await ipad.is_visible("#go") and not await ipad.is_visible("#nopasskey"),
            )
            await shot(ipad, "01-signin")

            # -- the computer adds a passkey --------------------------------------------------------
            await mac.goto(SITE)
            await mac.wait_for_selector(".row[data-id]")
            mac_cdp, mac_auth = await authenticator(mac)
            await mac.keyboard.press(f"{mod}+,")
            await mac.wait_for_selector("#set-devices", state="attached")
            await mac.click('.settings-nav [data-nav="devices"]')
            await mac.wait_for_selector("[data-passkey-add]")
            desc = await mac.text_content("[data-passkeys-desc]")
            check("Settings → Devices offers to add a passkey for this name", f"https://{NAME}" in desc, desc[-60:])
            await mac.click("[data-passkey-add]")
            await fill_prompt(mac, "Lorenzo's passkey")
            ok = await wait_for(lambda: mac.evaluate("() => !!document.querySelector('[data-passkey]')"))
            row = await mac.text_content("[data-passkey]") if ok else ""
            check(
                "the passkey is added and listed", ok and "Lorenzo's passkey" in row and NAME in row, row.strip()[:80]
            )
            await mac.eval_on_selector("#set-devices", "e => e.scrollIntoView({ block: 'start' })")
            await shot(mac, "02-passkey-listed")
            creds = (await mac_cdp.send("WebAuthn.getCredentials", {"authenticatorId": mac_auth}))["credentials"]
            check("… and saved by the authenticator, for synapse.test", len(creds) == 1 and creds[0]["rpId"] == NAME)

            # -- the iPad signs in with it ------------------------------------------------------------
            ipad_cdp, ipad_auth = await authenticator(ipad)
            await ipad_cdp.send("WebAuthn.addCredential", {"authenticatorId": ipad_auth, "credential": creds[0]})
            await ipad.click("#go")
            ok = await wait_for(lambda: ipad.evaluate("() => !!document.querySelector('[data-id]')"), timeout=15)
            check("the iPad signs in with the passkey and opens the library", ok)
            await shot(ipad, "03-signed-in")
            await mac.evaluate("() => import('/js/views/settingsView.js').then(m => m.openSettings('devices'))")
            ok = await wait_for(
                lambda: mac.evaluate(
                    "() => [...document.querySelectorAll('[data-device] .b-when')].some(e => e.textContent.includes('iPad'))"
                )
            )
            check("the computer lists the iPad as a device", ok)
            used = await mac.text_content("[data-passkey]")
            check("… and the passkey as used from the iPad's address", ip in used, used.strip()[-40:])
            r = await ipad.evaluate("() => fetch('/api/passkeys/' + 'x', { method: 'DELETE' }).then(r => r.status)")
            check("the iPad cannot remove passkeys", r == 403, r)
            r = await ipad.evaluate(
                "() => fetch('/api/mcp/tokens', { method: 'POST', headers: { 'Content-Type': 'application/json' },"
                " body: JSON.stringify({ name: 'x' }) }).then(r => r.json())"
            )
            check("… nor make an access token without confirming", r.get("confirm") is True, r)
            r = await ipad.evaluate(
                "() => fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' },"
                " body: JSON.stringify({ backup_keep: 1 }) }).then(r => r.json())"
            )
            check("… nor change the backup settings without confirming", r.get("confirm") is True, r)
            # Settings → Plugins asks for the passkey like the other pages (it used to show only the refusal)
            await ipad.evaluate("() => import('/js/views/settingsView.js').then(m => m.openSettings('plugins'))")
            toggle = '.plugin-row[data-plugin="graph"] .switch'
            await ipad.wait_for_selector(toggle)
            await ipad.click(toggle)
            graph_on = "() => fetch('/api/plugin-host').then(r => r.json()).then(o => o.plugins.find(p => p.id === 'graph').enabled)"
            ok = await wait_for(lambda: ipad.evaluate(f"() => ({graph_on})().then(on => !on)"))
            refused = await ipad.locator(".toast", has_text="Confirm with your passkey").count()
            check("the iPad turns a plugin off after confirming with its passkey", ok and not refused, refused)
            await ipad.click(toggle)
            await ipad.click(".dialog-backdrop:last-of-type button:has-text('Turn On')")
            ok = await wait_for(lambda: ipad.evaluate(graph_on))
            check("… and on again", ok)
            await ipad.keyboard.press("Escape")
            # Settings → Backups: changing how many backups to keep saves from the iPad (confirmed above)
            await ipad.evaluate("() => import('/js/views/settingsView.js').then(m => m.openSettings('backups'))")
            await ipad.wait_for_selector('[data-key="backup_keep"]')
            await ipad.select_option('[data-key="backup_keep"]', "14")
            keep = "() => fetch('/api/settings').then(r => r.json()).then(o => o.settings.backup_keep)"
            ok = await wait_for(lambda: ipad.evaluate(f"() => ({keep})().then(k => k === 14)"))
            refused = await ipad.locator(".toast", has_text="passkey").count()
            check("the iPad changes the backup settings in Settings", ok and not refused, refused)
            await ipad.keyboard.press("Escape")
            await ipad.evaluate("() => import('/js/views/settingsView.js').then(m => m.openSettings('ai'))")
            await ipad.wait_for_selector("[data-ai-new]")
            await ipad.click("[data-ai-new]")
            await fill_prompt(ipad, "iPad token")
            ok = await wait_for(lambda: ipad.evaluate("() => !!document.querySelector('[data-ai-command]')"))
            check("the iPad makes a token after confirming with its passkey", ok)
            await ipad.click("[data-ai-done]")
            await ipad.click("[data-ai-remove]")
            await ipad.wait_for_selector("text=Remove the token")
            await ipad.click(".dialog-backdrop:last-of-type button:has-text('Remove')")
            ok = await wait_for(lambda: ipad.evaluate("() => !document.querySelector('[data-ai-token]')"))
            check("… and removes it again (the confirmation lasts a few minutes)", ok)
            await ipad.keyboard.press("Escape")

            # -- AI access --------------------------------------------------------------------------
            await mac.click('.settings-nav [data-nav="ai"]')
            await mac.wait_for_selector("[data-ai-new]")
            await mac.click("[data-ai-new]")
            await fill_prompt(mac, "Work laptop")
            await mac.wait_for_selector("[data-ai-command]")
            command = await mac.text_content("[data-ai-command]")
            token = command.split("Bearer ")[1].split('"')[0]
            check(
                "a new token shows the claude mcp add command",
                command.startswith("claude mcp add") and "/mcp" in command and token.startswith("syn_"),
                command[:70],
            )
            await mac.eval_on_selector("#set-ai", "e => e.scrollIntoView({ block: 'start' })")
            await shot(mac, "04-token")
            init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            remote_url = f"https://{ip}:{PORT}/mcp"
            r = http.post(remote_url, json=init, headers={"Authorization": f"Bearer {token}"})
            check(
                "another computer reaches /mcp with the token",
                r.status_code == 200 and "result" in r.json(),
                r.status_code,
            )
            r = http.post(remote_url, json=init)
            check("… and not without it", r.status_code == 401, r.status_code)
            await mac.click("[data-ai-done]")
            await mac.click("[data-ai-remove]")
            await mac.wait_for_selector("text=Remove the token")
            await mac.click(".dialog button:has-text('Remove')")
            ok = await wait_for(lambda: mac.evaluate("() => !document.querySelector('[data-ai-token]')"))
            r = http.post(remote_url, json=init, headers={"Authorization": f"Bearer {token}"})
            check("removing the token locks the app out", ok and r.status_code == 401, r.status_code)

            # -- browser tokens use a separate, revocable scope ---------------------------------------
            await mac.click("[data-connector-new]")
            await fill_prompt(mac, "Browser extension")
            await mac.wait_for_selector("[data-ai-fresh] pre")
            browser_token = (await mac.text_content("[data-ai-fresh] pre")).strip()
            explanation = await mac.inner_text("[data-ai-fresh]")
            check(
                "browser setup shows a dedicated browser token",
                browser_token.startswith("syn_connector_")
                and "until you remove" in explanation
                and "saving and filing" in explanation,
            )
            entries = http.get(f"{LOCAL}/api/mcp/tokens").json()["tokens"]
            check(
                "Settings lists browser scope",
                len(entries) == 1 and entries[0]["scope"] == "connector",
            )
            browser_headers = {"Authorization": f"Bearer {browser_token}", "Origin": "chrome-extension://test"}
            ping = http.get(f"https://{ip}:{PORT}/api/connector/ping", headers=browser_headers)
            denied = http.post(remote_url, json=init, headers={"Authorization": f"Bearer {browser_token}"})
            check(
                "the browser token reaches the connector but cannot use MCP",
                ping.status_code == 200 and denied.status_code == 401,
            )
            await mac.click("[data-ai-done]")
            await mac.click("[data-ai-remove]")
            await mac.wait_for_selector("text=Remove the token")
            await mac.click(".dialog button:has-text('Remove')")
            ok = await wait_for(lambda: mac.evaluate("() => !document.querySelector('[data-ai-token]')"))
            denied = http.get(f"https://{ip}:{PORT}/api/connector/ping", headers=browser_headers)
            check("removing the browser token revokes connector access", ok and denied.status_code == 401)

            # -- removing the passkey ------------------------------------------------------------------
            await mac.click('.settings-nav [data-nav="devices"]')
            await mac.click("[data-passkey-remove]")
            await mac.wait_for_selector("text=Remove the passkey")
            await mac.click(".dialog button:has-text('Remove')")
            ok = await wait_for(lambda: mac.evaluate("() => !document.querySelector('[data-passkey]')"))
            check("the computer can remove the passkey", ok)
            other = await (await ipad_browser.new_context(ignore_https_errors=True)).new_page()
            await other.goto(SITE + "/")
            other_cdp, other_auth = await authenticator(other)
            await other_cdp.send("WebAuthn.addCredential", {"authenticatorId": other_auth, "credential": creds[0]})
            await other.click("#go")
            await other.wait_for_selector("#error:not([hidden])")
            check("a removed passkey no longer signs in", "does not know" in await other.text_content("#error"))
            await shot(other, "05-removed")

            check("no JavaScript errors", not errors, errors[:3])
            await mac_browser.close()
            await ipad_browser.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
