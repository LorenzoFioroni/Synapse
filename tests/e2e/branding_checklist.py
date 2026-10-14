"""Browser checklist for the name and icon (issue #23): the toolbar shows the program's icon
(the favicon), in light and dark theme; the tab title says Synapse.

    python tests/e2e/branding_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8776 with a throw-away data folder; your library is not touched.
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
from tests.e2e._guard import guard  # noqa: E402

PORT = 8776
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-branding-e2e-")
SHOTS = os.environ.get("SHOTS")
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


async def main():
    guard(PORT)
    srv = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", DATA],
                           cwd=APP, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            for scheme in ("light", "dark"):
                page = await browser.new_page(viewport={"width": 1100, "height": 500}, color_scheme=scheme)
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                await page.goto(BASE)
                await page.wait_for_selector(".brand-logo")
                await page.wait_for_function("document.querySelector('.brand-logo').complete")
                logo = await page.evaluate("""(() => { const el = document.querySelector('.brand-logo');
                    const r = el.getBoundingClientRect();
                    return { tag: el.tagName, src: el.getAttribute('src'), w: el.naturalWidth, text: el.textContent,
                             width: r.width, height: r.height }; })()""")
                check(f"[{scheme}] the toolbar shows the favicon image", logo["tag"] == "IMG" and "favicon.svg" in logo["src"]
                      and logo["w"] > 0 and not logo["text"], str(logo))
                check(f"[{scheme}] at 24×24", round(logo["width"]) == 24 and round(logo["height"]) == 24)
                if scheme == "light":
                    check("the tab title says Synapse", "Synapse" in await page.title(), await page.title())
                    icons = await page.eval_on_selector_all('link[rel~="icon"]', "els => els.map((e) => e.href)")
                    ok = all(requests.get(u).status_code == 200 for u in icons)
                    check("every favicon link loads", ok and len(icons) == 3, str(icons))
                    svg = requests.get(BASE + "/assets/favicon.svg")
                    check("the SVG favicon is served as SVG", svg.headers["Content-Type"].startswith("image/svg+xml"))
                if SHOTS:
                    os.makedirs(SHOTS, exist_ok=True)
                    await page.screenshot(path=f"{SHOTS}/toolbar-{scheme}.png", clip={"x": 0, "y": 0, "width": 520, "height": 60})
                check(f"[{scheme}] no JavaScript errors", not errors, "; ".join(errors[:3]))
                await page.close()
            await browser.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
