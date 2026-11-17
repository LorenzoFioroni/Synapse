"""Browser checklist for the "Save to Library" extension: loaded unpacked into Chromium, it reaches
the library through Synapse Companion (native messaging, with the companion's token), its options
page shows the connection, and its popup reads an article page's citation tags and saves the paper
with a tag. The companion runs from this checkout with its own throw-away home folder.

    python tests/e2e/extension_checklist.py

Starts its own server on port 8789 with a throw-away data folder (your library is not touched)
and serves tests/e2e/fixtures/article.html on port 8790. Extensions need the full Chromium in
its new headless mode (channel="chromium"), not the headless shell.
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

PORT, SITE = 8789, 8790
BASE = f"http://127.0.0.1:{PORT}"
EXT = Path(tempfile.mkdtemp(prefix="zc-extension-copy-"))
DATA = tempfile.mkdtemp(prefix="zc-extension-e2e-")
PROFILE = tempfile.mkdtemp(prefix="zc-extension-profile-")
COMPANION_HOME = tempfile.mkdtemp(prefix="zc-extension-companion-")
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


def setup_companion() -> str:
    """Connect a companion profile to the test server and register it as this Chromium profile's
    native messaging host (Chromium on Linux reads <user data dir>/NativeMessagingHosts). Answers
    the extension id, fixed by the manifest's key."""
    os.environ["SYNAPSE_COMPANION_HOME"] = COMPANION_HOME
    from synapse_companion import extension
    from synapse_companion.service import Service

    token = requests.post(BASE + "/api/mcp/tokens", json={"name": "E2E", "scope": "companion"}).json()["token"]
    Service("default").connect(BASE, token, "E2E computer")
    # A copy granted 127.0.0.1 up front (headless Chromium cannot answer a permission prompt): the
    # popup reads the test article without a click on the toolbar button. Same key, same id.
    shutil.copytree(APP / "browser_extension", EXT, dirs_exist_ok=True)
    manifest = json.loads((EXT / "manifest.json").read_text())
    manifest["host_permissions"] = ["http://127.0.0.1/*"]
    (EXT / "manifest.json").write_text(json.dumps(manifest))
    ext_id = extension.extension_id(EXT)
    launcher = extension.host_launcher("default")
    launcher.write_text(extension._launcher_text("default"), encoding="utf-8")
    launcher.chmod(0o700)
    extension.write_host_manifest(Path(PROFILE) / "NativeMessagingHosts", ext_id, launcher)
    return ext_id


def extension_seen():
    from synapse_companion.extension import last_seen

    return last_seen("default")


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
        ext_id = setup_companion()
        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                PROFILE,
                headless=True,
                channel="chromium",
                args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"],
            )
            errors = []
            ext = f"chrome-extension://{ext_id}"
            probe = await ctx.new_page()
            r = await probe.goto(f"{ext}/manifest.json")
            check("the extension is loaded with its fixed id", r is not None and r.ok)

            article = await ctx.new_page()
            await article.goto(f"http://127.0.0.1:{SITE}/article.html")

            options = await ctx.new_page()
            options.on("pageerror", lambda e: errors.append(str(e)))
            await options.goto(f"{ext}/options.html")
            await options.wait_for_function(
                "() => !document.querySelector('#status').textContent.startsWith('Checking')", timeout=10000
            )
            status = await options.inner_text("#status")
            check("the options page shows the companion's connection", f"Connected to {BASE}" in status, status)
            check("and the computer's name", "E2E computer" in status, status)
            check("the options page has no address or token field", not await options.query_selector("#server, #token"))
            seen = extension_seen()
            check("the companion recorded that the extension said hello", seen is not None, seen)

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
            check("Save to Library saves through the companion", "Saved" in result, result.splitlines()[:2])

            items = requests.get(BASE + "/api/items").json()["items"]
            saved = [i for i in items if i.get("doi") == "10.1234/test.5678"]
            check(
                "the item is in the library, with its authors and tag",
                len(saved) == 1
                and saved[0]["authors"] == "Doe, Jane; Roe, Rick"
                and saved[0].get("tags") == ["from-extension"],
                saved[:1],
            )
            tokens = requests.get(BASE + "/api/mcp/tokens").json()["tokens"]
            check("the save used the companion's token", tokens[0]["last_used"] is not None, tokens)
            stored = await options.evaluate(
                "Promise.all([chrome.storage.local.get(null), chrome.storage.sync.get(null)])"
            )
            check("the browser stores no token", "syn_" not in str(stored), stored)

            # Without the companion's connection the popup says what to do, and saves nothing.
            (Path(COMPANION_HOME) / "default.json").rename(Path(COMPANION_HOME) / "default.json.off")
            popup2 = await ctx.new_page()
            await popup2.goto(f"{ext}/popup.html?tabId={tab_id}")
            await popup2.wait_for_selector(".result", timeout=10000)
            offline = await popup2.inner_text(".result")
            check("without a connected companion the popup says to open it", "Companion" in offline, offline[:200])
            (Path(COMPANION_HOME) / "default.json.off").rename(Path(COMPANION_HOME) / "default.json")
            check("no JavaScript errors", errors == [], errors[:3])
            await ctx.close()
    finally:
        srv.terminate()
        site.terminate()
        srv.wait(timeout=10)
        shutil.rmtree(DATA, ignore_errors=True)
        shutil.rmtree(PROFILE, ignore_errors=True)
        shutil.rmtree(COMPANION_HOME, ignore_errors=True)
        shutil.rmtree(EXT, ignore_errors=True)
    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
