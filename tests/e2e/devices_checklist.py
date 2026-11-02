"""Browser checklist for pairing other devices: an unpaired iPad sees only "pair this
device", Settings → Devices on the computer shows a QR code, the pairing link pairs the iPad, the
iPad can then use the library, Cancel voids a code, a paired device can only manage itself, and
renaming / removing the device works.

    python tests/e2e/devices_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server with --lan on port 8781 and a throw-away data folder; your library is not
touched. Everything runs on one machine, so the server is started through a small launcher that
treats only 127.0.0.1 as "this computer": the "iPad" (a touch browser context) reaches the server
through this machine's LAN address and therefore counts as another device.
"""

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
from backend.access import lan  # noqa: E402
from tests.conftest import make_pdf  # noqa: E402
from tests.e2e._guard import guard  # noqa: E402

PORT = 8781
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-devices-e2e-")
SHOTS = os.environ.get("SHOTS")
results = []
mod = "Meta" if sys.platform == "darwin" else "Control"

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


async def main():
    guard(PORT)
    ip = next((a for a in lan.own_addresses() if ":" not in a), None)
    if not ip:
        sys.exit("This machine has no LAN IPv4 address to test with.")
    LAN = f"http://{ip}:{PORT}"
    srv = subprocess.Popen(
        [sys.executable, "-c", LAUNCHER, "--lan", "--port", str(PORT), "--no-browser", "--data-dir", DATA],
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
        item = requests.post(f"{BASE}/api/items", json={"title": "Paper on the Mac", "year": 2020}).json()
        pdf = make_pdf(Path(DATA) / "upload.pdf", "Readable on the iPad")
        with open(pdf, "rb") as fh:
            att = requests.post(
                f"{BASE}/api/items/{item['id']}/attachments", files={"file": ("paper.pdf", fh, "application/pdf")}
            ).json()["attachments"][0]

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            mac = await browser.new_page(viewport={"width": 1300, "height": 950})
            ipad_ctx = await browser.new_context(**p.devices["iPad Pro 11"])
            ipad = await ipad_ctx.new_page()
            errors = []
            mac.on("pageerror", lambda e: errors.append("mac: " + str(e)))
            ipad.on("pageerror", lambda e: errors.append("ipad: " + str(e)))

            # -- unpaired ------------------------------------------------------------
            r = await ipad.goto(LAN + "/")
            body = await ipad.text_content("body")
            check("an unpaired device gets the 'pair this device' page", r.status == 401 and "isn't paired" in body)
            r = await ipad.goto(LAN + "/api/items")
            check("… and no library data", r.status == 401)
            r = await ipad.goto(LAN + f"/pdfs/{att['url']}")
            check("… and no PDFs", r.status == 401)
            await shot(ipad, "01-unpaired")

            # -- the computer: Settings → Devices --------------------------------------
            await mac.goto(BASE)
            await mac.wait_for_selector(".row[data-id]")
            await mac.keyboard.press(f"{mod}+,")
            await mac.wait_for_selector("#set-devices")
            await mac.click('.settings-nav [data-nav="devices"]')
            await mac.wait_for_selector("[data-pair-new]")
            add_text = await mac.text_content("[data-device-add]")
            check(
                "Devices shows Add a Device and where other devices reach Synapse",
                "Add a device" in add_text and f":{PORT}/" in add_text,
                add_text.split("reach it at")[-1].strip()[:40],
            )
            # Cancel voids the code on the server, not only on screen
            await mac.context.grant_permissions(["clipboard-read", "clipboard-write"])
            await mac.click("[data-pair-new]")
            await mac.wait_for_selector("[data-pair-copy]")
            await mac.click("[data-pair-copy]")
            cancelled = (await mac.evaluate("navigator.clipboard.readText()")).split("#code=")[1]
            async with mac.expect_response(
                lambda r: r.url.endswith("/api/devices/pairing") and r.request.method == "DELETE"
            ):
                await mac.click("[data-pair-cancel]")
            await mac.wait_for_selector("[data-pair-new]")
            r = requests.post(f"{BASE}/api/devices/pair", json={"code": cancelled})
            check("Cancel voids the code: its link no longer pairs", r.status_code == 401, r.status_code)

            await mac.click("[data-pair-new]")
            await mac.wait_for_selector(".pair-qr svg")
            box = await mac.eval_on_selector(".pair-qr svg", "e => e.getBoundingClientRect().width")
            check("Add a Device shows a QR code", box >= 150, f"{box}px")
            check("… with a countdown", "expires in" in await mac.text_content("[data-device-add]"))
            await mac.eval_on_selector("#set-devices", "e => e.scrollIntoView({ block: 'start' })")
            await mac.wait_for_timeout(300)
            await shot(mac, "02-qr")
            # the link behind the QR code: pick the IP address (the .local name may not resolve here)
            idx = await mac.evaluate(
                """ip => [...document.querySelectorAll('[data-pair-addr]')].findIndex(b => b.textContent === ip)""", ip
            )
            if idx >= 0:
                await mac.click(f'[data-pair-addr="{idx}"]')
            # the link the QR code holds (Copy Link copies the same)
            await mac.click("[data-pair-copy]")
            link = await mac.evaluate("navigator.clipboard.readText()")
            check(
                "the link carries the code after #, for this computer's address",
                f"{ip}:{PORT}/pair#code=" in link,
                link.split("#")[0],
            )

            # -- pairing on the iPad ------------------------------------------------------
            await ipad.goto(link)
            await ipad.wait_for_selector("#name")
            check("the pairing page proposes the name 'iPad'", await ipad.input_value("#name") == "iPad")
            check("… and removes the code from the address bar", "code=" not in ipad.url)
            await shot(ipad, "03-pair")
            await ipad.click("#go")
            await ipad.wait_for_url(LAN + "/", timeout=10000)
            ok = await wait_for(lambda: ipad.evaluate("() => !!document.querySelector('[data-id]')"))
            check("after pairing the iPad opens the library", ok)
            await shot(ipad, "04-library")
            r = await ipad.evaluate("u => fetch(u).then(r => r.status)", f"/pdfs/{att['url']}")
            check("the iPad can load the PDF", r == 200, r)
            r = await ipad.evaluate("""() => fetch('/api/items', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                         body: JSON.stringify({ title: 'Added on the iPad' }) }).then(r => r.status)""")
            titles = [i["title"] for i in requests.get(f"{BASE}/api/items").json()["items"]]
            check(
                "the iPad can add an item, and the computer sees it",
                r in (200, 201) and "Added on the iPad" in titles,
                r,
            )

            ok = await wait_for(lambda: mac.evaluate("() => !document.querySelector('.pair-qr')"), timeout=8)
            check("the computer notices the new device and hides the QR code", ok)
            rows = await mac.eval_on_selector_all("[data-device] .b-when", "es => es.map(e => e.textContent)")
            check("… and lists it", rows == ["iPad"], rows)
            await shot(mac, "05-listed")

            # -- the iPad's own view of Devices, with a second device paired -------------------------
            code = requests.post(f"{BASE}/api/devices/pairing").json()["links"][0]["url"].split("#code=")[1]
            phone = requests.post(f"{BASE}/api/devices/pair", json={"code": code, "name": "Phone"}).json()["device"]
            await ipad.evaluate("() => import('/js/views/settingsView.js').then(m => m.openSettings('devices'))")
            await ipad.wait_for_selector("[data-device] .device-this")
            add_text = await ipad.text_content("[data-device-add]")
            check(
                "on the iPad: 'This device', and no Add button",
                "computer running Synapse" in add_text and not await ipad.query_selector("[data-pair-new]"),
            )
            buttons = await ipad.evaluate(
                """() => Object.fromEntries([...document.querySelectorAll('[data-device]')].map(r =>
                     [r.querySelector('.b-when').firstChild.textContent.trim(), [...r.querySelectorAll('button')].map(b => b.textContent.trim())]))"""
            )
            check(
                "… Rename and Sign Out for itself, no buttons for the other device",
                buttons == {"iPad": ["Rename…", "Sign Out"], "Phone": []},
                buttons,
            )
            r = await ipad.evaluate(
                "id => fetch('/api/devices/' + id, { method: 'DELETE' }).then(r => r.status)", phone["id"]
            )
            check("… and the server refuses it too", r == 403, r)
            await shot(ipad, "06-ipad-devices")
            requests.delete(f"{BASE}/api/devices/{phone['id']}")

            # -- rename and remove from the computer -----------------------------------------------
            await mac.click("[data-device-rename]")
            await mac.wait_for_selector(".dialog input, dialog input, .modal input")
            inp = await mac.query_selector(".dialog input, dialog input, .modal input")
            await inp.fill("Reading iPad")
            await mac.keyboard.press("Enter")
            ok = await wait_for(
                lambda: mac.evaluate(
                    "() => document.querySelector('[data-device] .b-when')?.textContent === 'Reading iPad'"
                )
            )
            check("the computer can rename the device", ok)
            await mac.click("[data-device-remove]")
            await mac.wait_for_selector("text=Remove Reading iPad?")
            await mac.click("button:has-text('Remove'):not([data-device-remove])")
            ok = await wait_for(lambda: mac.evaluate("() => !document.querySelector('[data-device]')"))
            check("the computer can remove the device", ok)
            r = await ipad.evaluate("() => fetch('/api/items').then(r => r.status)")
            check("a removed device is locked out at once", r == 401, r)
            await ipad.evaluate("() => import('/js/api.js').then(m => m.api.stats()).catch(() => 0)")
            await ipad.get_by_text("isn't paired", exact=False).wait_for(timeout=8000)
            check("… and its open page turns into 'pair this device'", True)

            # -- a used link does not pair again ---------------------------------------------
            await ipad.goto(link)
            await ipad.click("#go")
            await ipad.wait_for_selector("#error:not([hidden])")
            check(
                "a used pairing link is refused with an explanation", "not valid" in await ipad.text_content("#error")
            )

            r = await mac.evaluate("() => fetch('/api/items').then(r => r.status)")
            check("the computer itself never needed pairing", r == 200)
            check("no JavaScript errors", not errors, errors[:3])
            await browser.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
