"""Browser checklist for attachments stored as web links (issue #21): the viewer opens them
through /api/attachments/<id>/remote, and links to the local network are refused.

    python tests/e2e/remote_pdf_checklist.py

Starts its own server on port 8774 with a throw-away data folder; "the web" is faked inside
that server (journal.example serves tests/e2e/fixtures/twocolumn.pdf). Your library is not touched.
"""
import asyncio, os, subprocess, sys, tempfile, time
from pathlib import Path
import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from tests.e2e._guard import guard  # noqa: E402

PORT = 8774
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-remote-e2e-")
PDF = APP / "tests" / "e2e" / "fixtures" / "twocolumn.pdf"
results = []

SERVER = f"""
import sys, socket
sys.path.insert(0, {str(APP)!r})
from backend import create_app, remote_pdf
from backend.config import Config
PDF = open({str(PDF)!r}, 'rb').read()
IPS = {{'journal.example': '93.184.216.34', 'router.example': '192.168.1.1'}}
def gai(host, *a, **k):
    if host not in IPS: raise socket.gaierror('unknown')
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (IPS[host], 0))]
class R:
    def __init__(self, body): self.status_code, self.headers, self.body = 200, {{}}, body
    ok = True; is_redirect = False
    def iter_content(self, n): yield self.body
    def __enter__(self): return self
    def __exit__(self, *a): return False
remote_pdf.socket.getaddrinfo = gai
remote_pdf.requests.get = lambda url, **k: R(PDF)
app = create_app(Config.from_env({DATA!r}), start_indexer=False)
s = app.extensions['zclone']
for title, url in [('Remote paper', 'https://journal.example/paper.pdf'), ('Router page', 'http://router.example/')]:
    it = s.papers.create({{'title': title}})
    s.papers.add_attachment(it['id'], 'Linked PDF', url, 'pdf')
app.run(port={PORT}, threaded=True)
"""


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


async def main():
    guard(PORT)
    srv = subprocess.Popen([sys.executable, "-c", SERVER], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        items = {i["title"]: i for i in requests.get(BASE + "/api/items").json()["items"]}
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1200, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(BASE)
            await page.wait_for_selector("[data-id]")
            urls = await page.evaluate("""async (items) => {
                const { viewerUrl } = await import('/js/api.js');
                return items.map((it) => viewerUrl(it, it.attachments[0]));
            }""", [items["Remote paper"], items["Router page"]])
            from urllib.parse import parse_qs, urlparse
            file = parse_qs(urlparse(urls[0]).query)["file"][0]
            check("viewer URL uses the attachment endpoint", file.startswith("/api/attachments/") and file.endswith("/remote"), file)
            await page.goto(BASE + "/" + urls[0].lstrip("/"))
            await page.wait_for_selector(".page-wrapper canvas", timeout=20000)
            n = await page.evaluate("document.querySelectorAll('.page-wrapper').length")
            check("remote PDF opens in the viewer", n >= 1, f"{n} pages")
            att = items["Router page"]["attachments"][0]["id"]
            r = requests.get(f"{BASE}/api/attachments/{att}/remote")
            check("link to the local network is refused", r.status_code == 403, r.text[:80])
            r = requests.get(f"{BASE}/api/proxy?url=https://journal.example/paper.pdf")
            check("old open proxy is gone", r.status_code == 404)
            check("no JavaScript errors", not errors, "; ".join(errors[:3]))
            await browser.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
