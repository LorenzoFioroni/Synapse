"""Browser checklist for the change feed (issue #34): with the computer and an iPad open at the same
time, each shows what the other changed without a reload — items, notes and PDF annotations — as
well as changes made outside the app; typing is never interrupted; an idle page does not keep
refreshing itself.

    python tests/e2e/changes_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8784 with a throw-away data folder; your library is not touched.
"""

import asyncio
import os
import sqlite3
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

PORT = 8784
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-changes-e2e-")
PDF = APP / "tests" / "e2e" / "fixtures" / "twocolumn.pdf"
SHOTS = os.environ.get("SHOTS")
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


async def wait_for(fn, timeout=12):
    end = time.time() + timeout
    while time.time() < end:
        if await fn():
            return True
        await asyncio.sleep(0.2)
    return False


async def titles(page):
    return await page.eval_on_selector_all(".row[data-id]", "rs => rs.map(r => r.textContent)")


async def as_page(page, js):
    """Run an API call the way the page itself does (with its own client id)."""
    return await page.evaluate(f"import('/js/api.js').then(({{ api }}) => {js})")


async def main():
    guard(PORT)
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", DATA],
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
        first = requests.post(f"{BASE}/api/items", json={"title": "First paper", "year": 2019}).json()
        second = requests.post(f"{BASE}/api/items", json={"title": "Second paper", "year": 2021}).json()
        with open(PDF, "rb") as fh:
            att = requests.post(
                f"{BASE}/api/items/{first['id']}/attachments", files={"file": ("p.pdf", fh, "application/pdf")}
            ).json()["attachments"][0]

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            errors = []
            mac_ctx = await browser.new_context(viewport={"width": 1400, "height": 900})
            mac = await mac_ctx.new_page()
            ipad_ctx = await browser.new_context(**p.devices["iPad Pro 11 landscape"])
            ipad = await ipad_ctx.new_page()
            for pg, who in ((mac, "mac"), (ipad, "ipad")):
                pg.on("pageerror", lambda e, w=who: errors.append(f"{w}: {e}"))
                await pg.goto(BASE)
                await pg.wait_for_selector(".row[data-id]")
            await mac.wait_for_timeout(1500)  # both pages have started their feed

            # ---- an item added on the iPad appears on the computer ---------------------------------
            await as_page(ipad, "api.createItem({ title: 'Added on the iPad' })")
            ok = await wait_for(lambda: _has(mac, "Added on the iPad"))
            check("an item added on the iPad appears on the computer, no reload", ok)

            # ---- an edit on the computer appears on the iPad --------------------------------------------
            await as_page(mac, f"api.updateItem('{second['id']}', {{ title: 'Second paper (revised)' }})")
            ok = await wait_for(lambda: _has(ipad, "Second paper (revised)"))
            check("a title changed on the computer changes on the iPad", ok)
            await shot(ipad, "01-ipad-updated")

            # ---- a page is not refreshed for its own edits ------------------------------------------------
            st0 = await mac.evaluate("window.zcChanges.state()")
            await as_page(mac, f"api.updateItem('{first['id']}', {{ year: 2018 }})")
            await mac.wait_for_timeout(5000)
            st1 = await mac.evaluate("window.zcChanges.state()")
            r = requests.get(
                f"{BASE}/api/changes?since={st0['seq']}&epoch={st0['epoch']}",
                headers={"X-Synapse-Client": "someone-else"},
            ).json()
            check(
                "the feed reports an edit to others but not back to the page that made it",
                "library" in r["changes"] and st1["pending"] == [],
                f"{r['changes']} / pending {st1['pending']}",
            )

            # ---- typing is not interrupted -----------------------------------------------------------------
            await mac.click(f'.row[data-id="{first["id"]}"]')
            await mac.wait_for_selector('#item-pane [data-field="title"]')
            await mac.click('#item-pane [data-field="title"]')
            await mac.keyboard.press("End")
            await mac.keyboard.type(" — draft")
            await as_page(ipad, "api.createItem({ title: 'While typing' })")
            await mac.wait_for_timeout(6000)
            still = await mac.evaluate("""(() => { const a = document.activeElement;
                return [a?.dataset?.field, a?.textContent]; })()""")
            check(
                "while the computer types in a field, a change from the iPad waits",
                still[0] == "title" and still[1].endswith("— draft"),
                str(still),
            )
            await mac.keyboard.press("Escape")
            await mac.evaluate("document.activeElement.blur()")
            ok = await wait_for(lambda: _has(mac, "While typing"))
            check("… and shows up once the field is left", ok)

            # ---- notes -------------------------------------------------------------------------------------
            await mac.click('[data-ws="notes"]')
            await mac.wait_for_selector("#notes-view:not([hidden])")
            await as_page(
                ipad,
                "fetch('/api/notes/note', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-Synapse-Client': 'ipad-notes' }, body: JSON.stringify({ path: 'From the iPad.md', create: true, text: '# From the iPad\\n' }) })",
            )
            ok = await wait_for(
                lambda: mac.evaluate("!!document.querySelector('.nt-row.note[data-path=\"From the iPad.md\"]')")
            )
            check("a note written on the iPad appears in the computer's notes", ok)
            await mac.click('[data-ws="library"]')

            # ---- annotations in an open PDF -------------------------------------------------------------------
            q = urlencode(
                {"file": f"/pdfs/{att['url']}", "paper_id": first["id"], "title": first["title"], "att": att["id"]}
            )
            v = await mac_ctx.new_page()
            v.on("pageerror", lambda e: errors.append(f"viewer: {e}"))
            await v.goto(f"{BASE}/viewer.html?{q}")
            await v.wait_for_function("document.querySelectorAll('.page-wrapper .textLayer span').length > 20")
            await v.evaluate("window.zcAnnotations.ready")
            await v.wait_for_timeout(1500)
            requests.post(
                f"{BASE}/api/attachments/{att['id']}/annotations",
                headers={"X-Synapse-Client": "ipad-viewer"},
                json={"type": "box", "page": 1, "rects": [[0.1, 0.2, 0.2, 0.1]], "color": "#ff0000", "size": 2},
            )
            ok = await wait_for(lambda: v.evaluate("document.querySelectorAll('.anno-layer g.anno-box').length === 1"))
            check("an annotation made on the iPad appears in the computer's open PDF", ok)
            await shot(v, "02-viewer")
            await v.close()

            # ---- changes made outside the app ------------------------------------------------------------------
            with sqlite3.connect(Path(DATA) / "library.db") as c:
                c.execute("UPDATE papers SET title = 'Changed by another program' WHERE id = ?", (first["id"],))
            ok1 = await wait_for(lambda: _has(mac, "Changed by another program"))
            ok2 = await wait_for(lambda: _has(ipad, "Changed by another program"))
            check("a change made outside the app (e.g. the MCP server) shows on both", ok1 and ok2)

            # ---- idle pages stay quiet ---------------------------------------------------------------------------
            await mac.wait_for_timeout(3000)
            n0 = await mac.evaluate(
                "performance.getEntriesByType('resource').filter(e => e.name.includes('/api/items?')).length"
            )
            m0 = await ipad.evaluate(
                "performance.getEntriesByType('resource').filter(e => e.name.includes('/api/items?')).length"
            )
            await mac.wait_for_timeout(13000)
            n1 = await mac.evaluate(
                "performance.getEntriesByType('resource').filter(e => e.name.includes('/api/items?')).length"
            )
            m1 = await ipad.evaluate(
                "performance.getEntriesByType('resource').filter(e => e.name.includes('/api/items?')).length"
            )
            check(
                "idle pages do not keep reloading the list",
                n1 == n0 and m1 == m0,
                f"mac {n1 - n0}, ipad {m1 - m0} reloads in 13 s",
            )

            check("no JavaScript errors", not errors, errors[:3])
            await browser.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


async def _has(page, text):
    return any(text in t for t in await titles(page))


if __name__ == "__main__":
    asyncio.run(main())
