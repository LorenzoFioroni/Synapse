"""Browser checklist for offline use (docs/offline.md): it is off until Settings → Offline Use is
turned on; then the app and the PDFs open again with the browser offline after every window was
closed, papers whose PDF is not saved are marked, an annotation made offline reaches the server once
the browser is back online, and turning it off deletes the saved copy.

    python tests/e2e/offline_checklist.py

Starts its own server on port 8872 with a throw-away data folder. Your library is not touched.
"""

import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from tests.e2e._guard import guard  # noqa: E402

PORT = 8872
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-offline-e2e-")
PDF = APP / "tests" / "e2e" / "fixtures" / "twocolumn.pdf"
results = []

SERVER = f"""
import sys, shutil
sys.path.insert(0, {str(APP)!r})
from backend import create_app
from backend.config import Config
cfg = Config.from_env({DATA!r})
app = create_app(cfg, start_indexer=False)
s = app.extensions['synapse']
shutil.copy({str(PDF)!r}, cfg.pdf_dir / 'paper.pdf')
shutil.copy({str(PDF)!r}, cfg.pdf_dir / 'second.pdf')
it = s.papers.create({{'title': 'Offline paper'}})
att = s.papers.add_attachment(it['id'], 'PDF', 'paper.pdf', 'pdf')
two = s.papers.create({{'title': 'Second paper'}})
s.papers.add_attachment(two['id'], 'PDF', 'second.pdf', 'pdf')
s.papers.create({{'title': 'Record only paper'}})
open({DATA!r} + '/ids.txt', 'w').write(f"{{it['id']}} {{att['id']}}")
app.run(port={PORT}, threaded=True)
"""


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)


async def main():
    paper_id, att_id = Path(DATA, "ids.txt").read_text().split()
    viewer = "/viewer.html?" + urlencode(
        {"file": "/pdfs/paper.pdf", "title": "Offline paper", "paper_id": paper_id, "att": att_id}
    )
    profile = tempfile.mkdtemp(prefix="zc-offline-profile-")
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(profile, headless=True)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # Off by default: nothing is registered or saved, and the app does not open offline.
        await page.goto(BASE + "/")
        await page.wait_for_selector("text=Offline paper", timeout=20000)
        await page.wait_for_timeout(1500)
        state = await page.evaluate(
            "async () => [(await navigator.serviceWorker.getRegistrations()).length, await caches.keys()]"
        )
        check("off by default: no worker, nothing saved", state == [0, []], str(state))
        await ctx.set_offline(True)
        off = await ctx.new_page()
        try:
            await off.goto(BASE + "/", timeout=5000)
            opened = await off.locator("text=Offline paper").count() > 0
        except Exception:
            opened = False
        check("the app does not open offline before it is turned on", not opened)
        await off.close()
        await ctx.set_offline(False)

        # Turn it on in Settings → Offline Use.
        await page.keyboard.press("Control+,")
        await page.click('.settings-nav [data-nav="offline"]')
        check("switch is off", not await page.locator("[data-offline-switch]").is_checked())
        await page.click("#set-offline .switch")
        await page.wait_for_function(
            "() => /PDFs? saved/.test(document.querySelector('[data-offline-title]')?.textContent || '')", timeout=60000
        )
        title = await page.locator("[data-offline-title]").text_content()
        check("every PDF saved", title.startswith("2 of 2 PDFs"), title)
        # One saved PDF disappears (as if it were added after the last save).
        await page.evaluate("caches.open('synapse-offline-v1').then((c) => c.delete('/pdfs/second.pdf'))")
        for pg in list(ctx.pages):
            await pg.close()

        # Offline, everything closed: the app opens again.
        await ctx.set_offline(True)
        page = await ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        bad = []
        page.on("response", lambda r: bad.append(f"{r.status} {r.url[len(BASE) :]}") if r.status >= 400 else None)
        page.on("requestfailed", lambda r: bad.append(f"failed {r.url[len(BASE) :]}"))
        await page.goto(BASE + "/")
        ok = True
        try:
            await page.wait_for_selector("text=Offline paper", timeout=15000)
        except Exception:
            ok = False
        check("app opens offline after all windows were closed", ok, "; ".join(bad[:12]))
        pill = (
            await page.locator(".zc-offline-pill").text_content()
            if await page.locator(".zc-offline-pill").count()
            else ""
        )
        check("offline pill shown", "Offline" in (pill or ""), pill)
        await page.wait_for_timeout(500)
        missing = await page.locator(".row.offline-missing .t").all_text_contents()
        check("only the paper whose PDF is not saved is marked", missing == ["Second paper"], str(missing))
        check(
            "record without a PDF is not marked", await page.locator('.row:has-text("Record only paper")').count() == 1
        )
        await page.click('.row:has-text("Second paper") .pdf-dot')
        await page.wait_for_selector("text=not saved on this device", timeout=3000)
        check("opening an unsaved PDF explains itself", True)

        # Offline: the PDF opens, also with another query (another page number).
        v = await ctx.new_page()
        await v.goto(BASE + viewer + "&page=1")
        ok = True
        try:
            await v.wait_for_selector(".page-wrapper canvas", timeout=15000)
        except Exception:
            ok = False
        check("opened PDF renders offline", ok)

        # Offline annotation: queued, then sent when online again.
        res = await v.evaluate(f"""async () => {{
          const r = await fetch('/api/attachments/{att_id}/annotations', {{ method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ id: 'offline-test-1', type: 'highlight', page: 1, color: '#ffd400',
              rects: [[0.1, 0.1, 0.2, 0.02]], text: 'x' }}) }});
          return [r.status, r.headers.get('X-Synapse-Queued')];
        }}""")
        check("annotation queued while offline", res == [202, "1"], str(res))
        await ctx.set_offline(False)
        anns = []
        for _ in range(40):
            await asyncio.sleep(0.5)
            anns = requests.get(f"{BASE}/api/attachments/{att_id}/annotations", timeout=5).json()["annotations"]
            if any(a["id"] == "offline-test-1" for a in anns):
                break
        check(
            "queued annotation reaches the server once online",
            any(a["id"] == "offline-test-1" for a in anns),
            str([a["id"] for a in anns]),
        )

        # Turn it off again: the worker and the saved copy go.
        page = ctx.pages[0]
        await page.reload()
        await page.wait_for_selector("text=Offline paper", timeout=20000)
        await page.keyboard.press("Control+,")
        await page.click('.settings-nav [data-nav="offline"]')
        await page.click("#set-offline .switch")
        await page.click(".dialog-footer .btn:has-text('Turn Off')")
        await page.wait_for_timeout(1500)
        state = await page.evaluate(
            "async () => [(await navigator.serviceWorker.getRegistrations()).length, await caches.keys()]"
        )
        check("turning it off removes the worker and the saved copy", state == [0, []], str(state))
        left = await page.evaluate(
            """async () => [
              Object.keys(localStorage).filter((k) => k.startsWith('zc.offline')),
              (await indexedDB.databases()).map((d) => d.name).filter((n) => n === 'synapse-offline'),
              document.querySelectorAll('.zc-offline-pill, .row.offline-missing').length,
            ]"""
        )
        check(
            "turning it off also clears the remembered state, the queue and the marks", left == [[], [], 0], str(left)
        )

        # And it can be turned on again.
        await page.click("#set-offline .switch")
        await page.wait_for_function(
            "() => /PDFs? saved/.test(document.querySelector('[data-offline-title]')?.textContent || '')", timeout=60000
        )
        check("turning it on again saves everything again", True)
        check("no page errors", not errors, "; ".join(errors[:3]))
        await ctx.close()
    shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    guard(PORT)
    proc = subprocess.Popen([sys.executable, "-c", SERVER], cwd=APP)
    try:
        for _ in range(100):
            try:
                requests.get(BASE + "/manifest.webmanifest", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        asyncio.run(main())
    finally:
        proc.terminate()
        shutil.rmtree(DATA, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)
