"""Browser checklist for the extension's "Find Missing PDFs" page: loaded unpacked into Chromium,
it opens each item's DOI in a background tab, waits out a browser check, fetches the PDF from the
article page and the library attaches it when it is the paper.

    python tests/e2e/find_pdfs_checklist.py

Starts its own server on port 8851 with a throw-away data folder (your library is not touched)
and a fake publisher on port 8852, whose DOIs stand for the cases met on real sites:

* ``10.5555/check``: a browser check ("Just a moment…") that sets a cookie and reloads, then the
  article page; its PDF is only given with that cookie (Cloudflare-style).
* ``10.5555/script``: the PDF link leads to a page that moves on by script (ScienceDirect-style).
* ``10.5555/login``: the PDF link gives a login page (no subscription).
* ``10.5555/wrong``: a PDF of another paper.
* ``10.5555/nolink``: an article page without a PDF link.
"""

import asyncio
import http.server
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from tests.conftest import make_pdf  # noqa: E402
from tests.e2e._guard import guard  # noqa: E402
from tests.e2e.extension_checklist import extension_id  # noqa: E402

PORT, SITE = 8851, 8852
BASE = f"http://127.0.0.1:{PORT}"
PUB = f"http://127.0.0.1:{SITE}"
EXT = APP / "browser_extension"
DATA = tempfile.mkdtemp(prefix="zc-findpdfs-e2e-")
PROFILE = tempfile.mkdtemp(prefix="zc-findpdfs-profile-")
TMP = Path(tempfile.mkdtemp(prefix="zc-findpdfs-pdfs-"))
results = []

PAPERS = {
    "check": "Behind a Browser Check",
    "script": "Behind a Script Redirect",
    "login": "Behind a Login",
    "wrong": "The Paper Asked For",
    "nolink": "Without a PDF Link",
}
PDFS = {k: make_pdf(TMP / f"{k}.pdf", f"doi:10.5555/{k} {t}").read_bytes() for k, t in PAPERS.items()}
PDFS["wrong"] = make_pdf(TMP / "other.pdf", "doi:10.9999/other Another Paper").read_bytes()


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


def article(key: str, pdf: str | None) -> bytes:
    link = f'<meta name="citation_pdf_url" content="{pdf}">' if pdf else ""
    return f"""<!doctype html><html><head><title>{PAPERS[key]}</title>
<meta name="citation_title" content="{PAPERS[key]}"><meta name="citation_doi" content="10.5555/{key}">{link}
</head><body><h1>{PAPERS[key]}</h1></body></html>""".encode()


CHECK_PAGE = b"""<!doctype html><html><head><title>Just a moment...</title></head><body>
<p>Checking your browser before accessing the site.</p>
<script>setTimeout(() => { document.cookie = 'cleared=1; path=/'; location.reload(); }, 1500);</script>
</body></html>"""
LOGIN_PAGE = (
    b"<!doctype html><html><head><title>Sign in</title></head><body><form>Institutional login</form></body></html>"
)


class Publisher(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body: bytes, ctype: str = "text/html", status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        cleared = "cleared=1" in (self.headers.get("Cookie") or "")
        path = self.path.split("?")[0]
        if path.startswith("/doi/10.5555/"):  # the resolver: on to the publisher's page
            self.send_response(302)
            self.send_header("Location", "/article/" + path.rsplit("/", 1)[1])
            self.end_headers()
        elif path == "/article/check":
            self.send(article("check", "/pdf/check") if cleared else CHECK_PAGE, status=200 if cleared else 403)
        elif path == "/pdf/check":
            self.send(PDFS["check"], "application/pdf") if cleared else self.send(CHECK_PAGE, status=403)
        elif path == "/article/script":
            self.send(article("script", "/pdfft/script"))
        elif path == "/pdfft/script":
            self.send(
                b"<!doctype html><html><body><script>location.replace('/assets/script.pdf')</script></body></html>"
            )
        elif path == "/assets/script.pdf":
            self.send(PDFS["script"], "application/pdf")
        elif path == "/article/login":
            self.send(article("login", "/pdf/login"))
        elif path == "/pdf/login":
            self.send(LOGIN_PAGE)
        elif path == "/article/wrong":
            self.send(article("wrong", "/pdf/wrong"))
        elif path == "/pdf/wrong":
            self.send(PDFS["wrong"], "application/pdf")
        elif path == "/article/nolink":
            self.send(article("nolink", None))
        else:
            self.send(b"not found", "text/plain", 404)


async def main():
    guard(PORT)
    guard(SITE)
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", DATA],
        cwd=APP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    site = http.server.ThreadingHTTPServer(("127.0.0.1", SITE), Publisher)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    try:
        for _ in range(150):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        ids = {}
        for key, title in PAPERS.items():
            item = requests.post(BASE + "/api/items", json={"title": title, "doi": f"10.5555/{key}"}).json()
            ids[item["id"]] = key
        listed = requests.get(BASE + "/api/connector/missing-pdfs").json()
        check("the library lists the five items without a PDF", listed["total"] == 5, listed["total"])

        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                PROFILE,
                headless=True,
                channel="chromium",
                args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"],
            )
            errors = []
            ext = f"chrome-extension://{extension_id(EXT)}"
            setup = await ctx.new_page()
            await setup.goto(f"{ext}/manifest.json")
            await setup.evaluate(f"chrome.storage.sync.set({{server: '{BASE}'}})")

            # a library from before Find Missing PDFs answers its route with Flask's 404
            old = await ctx.new_page()
            old.on("pageerror", lambda e: errors.append(str(e)))
            await old.route(
                f"{BASE}/api/connector/missing-pdfs*",
                lambda route: route.fulfill(
                    status=404, content_type="application/json", body='{"error": "The requested URL was not found"}'
                ),
            )
            await old.goto(f"{ext}/find_pdfs.html")
            await old.wait_for_selector(".result", timeout=10000)
            told = await old.inner_text(".result")
            check("an older library is told apart", "Update Synapse first" in told, told.splitlines()[:1])
            await old.close()

            page = await ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(f"{ext}/find_pdfs.html?resolver={PUB}/doi/&pause=0")
            await page.wait_for_selector("#start", timeout=10000)
            rows = await page.locator(".rows li").count()
            check("the page lists them", rows == 5, rows)
            await page.click("#start")
            await page.wait_for_function(
                "() => /attached of/.test(document.querySelector('.summary')?.textContent || '')", timeout=240000
            )
            statuses = {}
            for li in await page.locator(".rows li").all():
                statuses[ids[await li.get_attribute("data-id")]] = await li.locator(".st").inner_text()
            print("   ", statuses)
            check("past a browser check, with its cookie", "attached" in statuses["check"])
            check("past a redirect by script", "attached" in statuses["script"])
            check("a login page is not attached", "login page" in statuses["login"])
            check("a PDF of another paper is not attached", "not of this paper" in statuses["wrong"])
            check("an article page without a PDF link", "No PDF link" in statuses["nolink"])
            summary = await page.inner_text(".summary")
            check("the summary counts them", "2 PDFs attached of 5 tried" in summary, summary)
            check("the three not found can be retried", "Retry 3" in await page.inner_text("#start"))
            tabs = [pg.url for pg in ctx.pages]
            check("the background tab is closed afterwards", not any(u.startswith(PUB) for u in tabs), tabs)

            left = requests.get(BASE + "/api/connector/missing-pdfs").json()
            check("the library has the two PDFs", left["total"] == 3, left["total"])
            check("no page errors", not errors, errors)
            await ctx.close()
    finally:
        site.shutdown()
        srv.terminate()
        srv.wait(timeout=10)
        for d in (DATA, PROFILE, TMP):
            shutil.rmtree(d, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
