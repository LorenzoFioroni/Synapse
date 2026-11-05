"""Browser checklist for the "Save to Library" extension: loaded unpacked into Chromium, its
options page connects to the library, and its popup reads an article page's citation tags and
saves the paper with a tag.

    python tests/e2e/extension_checklist.py

Starts its own server on port 8789 with a throw-away data folder (your library is not touched)
and serves tests/e2e/fixtures/article.html on port 8790. Extensions need the full Chromium in
its new headless mode (channel="chromium"), not the headless shell.
"""

import asyncio
import hashlib
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

PORT, SITE = 8789, 8790
BASE = f"http://127.0.0.1:{PORT}"
EXT = APP / "browser_extension"
DATA = tempfile.mkdtemp(prefix="zc-extension-e2e-")
PROFILE = tempfile.mkdtemp(prefix="zc-extension-profile-")
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


def extension_id(path: Path) -> str:
    """Chrome's id for an unpacked extension: the SHA-256 of its folder's path, as letters a-p."""
    return "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(str(path).encode()).hexdigest()[:32])


async def main():
    guard(PORT)
    guard(SITE)
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", DATA],
        cwd=APP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    site = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(SITE), "-d", str(APP / "tests" / "e2e" / "fixtures")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(150):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                requests.get(f"http://127.0.0.1:{SITE}/article.html", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                PROFILE,
                headless=True,
                channel="chromium",
                args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"],
            )
            errors = []
            ext = f"chrome-extension://{extension_id(EXT)}"
            probe = await ctx.new_page()
            r = await probe.goto(f"{ext}/manifest.json")
            check("the extension is loaded", r is not None and r.ok)

            article = await ctx.new_page()
            await article.goto(f"http://127.0.0.1:{SITE}/article.html")

            options = await ctx.new_page()
            options.on("pageerror", lambda e: errors.append(str(e)))
            await options.goto(f"{ext}/options.html")
            await options.fill("#server", f"127.0.0.1:{PORT}")
            await options.click("#save")
            await options.wait_for_function(
                "() => (document.querySelector('#status').textContent.includes('✓'))", timeout=10000
            )
            status = await options.inner_text("#status")
            check("the options page saves the address and connects", "Connected" in status, status)

            configured = await options.evaluate("chrome.storage.sync.get('server')")
            assert configured["server"] == BASE, "Refuse to open a popup against any non-test library"

            # the popup acts on the active tab; ?tabId= points it at the article instead
            tab_id = await options.evaluate(
                f"chrome.tabs.query({{url: 'http://127.0.0.1:{SITE}/*'}}).then(t => t[0].id)"
            )
            popup = await ctx.new_page()
            popup.on("pageerror", lambda e: errors.append(str(e)))
            await popup.goto(f"{ext}/popup.html?tabId={tab_id}")
            await popup.wait_for_selector("#title", timeout=10000)
            check("the popup reads the title", await popup.input_value("#title") == "A Test Paper About Extensions")
            badges = await popup.inner_text(".badges")
            check("and the DOI", "10.1234/test.5678" in badges, badges)

            await popup.fill("#tagIn", "from-extension")
            await popup.keyboard.press("Enter")
            await popup.uncheck("#lookup")  # no network: keep the page's metadata
            await popup.click("#save")
            await popup.wait_for_selector(".result", timeout=15000)
            result = await popup.inner_text(".result")
            check("Save to Library saves", "Saved" in result, result.splitlines()[:2])

            items = requests.get(BASE + "/api/items").json()["items"]
            saved = [i for i in items if i.get("doi") == "10.1234/test.5678"]
            check(
                "the item is in the library, with its authors and tag",
                len(saved) == 1
                and saved[0]["authors"] == "Doe, Jane; Roe, Rick"
                and saved[0].get("tags") == ["from-extension"],
                saved[:1],
            )
            # Exercise remote setup without depending on an external server or a native
            # permission prompt. Backend tests cover real bearer validation behind a proxy.
            await options.evaluate("""() => {
                window.requestedOrigins = [];
                chrome.permissions.request = async ({origins}) => {
                    window.requestedOrigins.push(...origins);
                    return true;
                };
                window.remoteRequests = [];
                window.fetch = async (url, opts) => {
                    window.remoteRequests.push({url, token: opts.headers.get('Authorization'),
                        credentials: opts.credentials, redirect: opts.redirect});
                    return new Response(JSON.stringify({app: 'synapse', counts: {library: 3}}));
                };
            }""")
            await options.fill("#server", "https://synapse.example")
            await options.click("#save")
            check("remote setup requires a token", "Paste an access token" in await options.inner_text("#status"))
            await options.fill("#token", "syn_browser_test")
            await options.click("#save")
            await options.wait_for_function("() => document.querySelector('#status').textContent.includes('✓')")
            remote = await options.evaluate("window.remoteRequests[0]")
            check(
                "remote setup requests the selected server permission",
                await options.evaluate("window.requestedOrigins") == ["https://synapse.example/*"],
            )
            check(
                "remote ping uses bearer authentication without cookies or redirects",
                remote
                == {
                    "url": "https://synapse.example/api/connector/ping",
                    "token": "Bearer syn_browser_test",
                    "credentials": "omit",
                    "redirect": "error",
                },
            )
            stored = await options.evaluate("chrome.storage.local.get('connection')")
            check(
                "the token is stored with its server locally",
                stored["connection"] == {"server": "https://synapse.example", "token": "syn_browser_test"},
            )
            synced = await options.evaluate("chrome.storage.sync.get(null)")
            check("the token is not synced", "syn_browser_test" not in str(synced))
            await options.fill("#server", BASE)
            check("changing servers clears the token field", await options.input_value("#token") == "")
            await options.click("#save")
            await options.wait_for_function("() => document.querySelector('#status').textContent.includes('✓')")
            stored = await options.evaluate("chrome.storage.local.get('connection')")
            check("switching back to localhost removes the saved token", stored["connection"]["token"] == "")
            check("no JavaScript errors", errors == [], errors[:3])
            await ctx.close()
    finally:
        srv.terminate()
        site.terminate()
        srv.wait(timeout=10)
        shutil.rmtree(DATA, ignore_errors=True)
        shutil.rmtree(PROFILE, ignore_errors=True)
    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
