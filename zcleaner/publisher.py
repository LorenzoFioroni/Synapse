"""Fetch the publisher's PDF through your institutional access (VPN / campus network).

Two strategies, tried in order for one DOI:

1. **Web requests** (fast): resolve the DOI to the publisher's article page and
   find the PDF link the way a reader would: the ``citation_pdf_url`` tag, the
   publisher's known PDF address pattern, or a "Download PDF" link/button on
   the page; PDF viewers that wrap the file in a frame (IEEE) are unwrapped.
2. **A real browser** (optional, needs ``pip install playwright``): for sites
   that only serve the PDF to a real browser or after clicking a button
   (Elsevier/ScienceDirect, Science, IEEE, publishers behind a bot check).
   The tool opens the article page in Chrome, clicks "Download PDF"/"PDF", and
   saves the file it receives.  The window is visible, so you can complete a
   login (e.g. your university's single sign-on) or a check yourself; the
   browser profile is kept, so this is needed only once.

Use this for your own research copies, one item at a time — the tool waits
between requests and never downloads in bulk.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from .config import WORK_DIR
from .lookups import RateLimiter

log = logging.getLogger(__name__)
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")
PROFILE_DIR = WORK_DIR / "browser_profile"

APS_JOURNALS = {"physrevlett": "prl", "physreva": "pra", "physrevb": "prb", "physrevc": "prc", "physrevd": "prd",
                "physreve": "pre", "physrevx": "prx", "physrevresearch": "prresearch", "physrevapplied": "prapplied",
                "revmodphys": "rmp", "prxquantum": "prxquantum", "physrevfluids": "prfluids",
                "physrevmaterials": "prmaterials", "physrevaccelbeams": "prab"}


def _is_pdf(first: bytes) -> bool:
    return b"%PDF-" in first[:1024]


CHALLENGE_TITLES = re.compile(r"just a moment|attention required|are you a robot|robot or human|human verification|"
                              r"verify you are (a )?human|security check|pardon our interruption|access denied|"
                              r"please confirm you are a human|checking your browser|captcha", re.I)
CHALLENGE_MARKERS = re.compile(r"cf-chl|challenge-platform|cf-turnstile|/cdn-cgi/challenge|g-recaptcha|recaptcha/api|"
                               r"h-captcha|hcaptcha\.com|px-captcha|captcha-delivery|perimeterx|challenge-form|"
                               r"press (&amp;|&) hold|are you a robot|not a robot|unusual traffic|datadome", re.I)


def is_challenge(html: str, title: str = "") -> bool:
    """A captcha / bot-check page instead of the article (Cloudflare, reCAPTCHA,
    hCaptcha, PerimeterX, DataDome, Elsevier's 'Are you a robot?')."""
    if title and CHALLENGE_TITLES.search(title):
        return True
    head = (html or "")[:60000]
    m = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
    if m and CHALLENGE_TITLES.search(m.group(1)):
        return True
    return bool(CHALLENGE_MARKERS.search(head)) and len(re.sub(r"<[^>]+>", "", head)) < 20000


def pdf_urls_from_page(html: str, page_url: str, doi: str) -> list[str]:
    """Every plausible PDF link on an article page, best first."""
    out: list[str] = []

    def add(u):
        if u:
            u = urljoin(page_url, u.replace("&amp;", "&").strip())
            if u not in out and u.startswith("http"):
                out.append(u)

    for pat in (r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url'):
        for m in re.finditer(pat, html, re.I):
            add(m.group(1))
    host = urlparse(page_url).hostname or ""
    path = urlparse(page_url).path
    d = doi.lower()
    # publisher address patterns (need the final article page address for some)
    m = re.search(r"/document/(\d+)", path)
    if "ieeexplore" in host and m:
        add(f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={m.group(1)}&ref=")
    m = re.search(r"/pii/([A-Z0-9]+)", page_url, re.I)
    if "sciencedirect" in host and m:
        add(f"https://www.sciencedirect.com/science/article/pii/{m.group(1)}/pdfft?isDTMRedir=true&download=true")
    if "nature.com" in host and "/articles/" in path and not path.endswith(".pdf"):
        add(page_url.split("?")[0].rstrip("/") + ".pdf")
    if d.startswith("10.1103/"):
        j = re.match(r"10\.1103/([a-z]+)\.", d)
        if j and j.group(1) in APS_JOURNALS:
            add(f"https://journals.aps.org/{APS_JOURNALS[j.group(1)]}/pdf/{doi}")
    templates = {
        ("10.1007/", "10.1140/", "10.1186/"): "https://link.springer.com/content/pdf/{doi}.pdf",
        ("10.1002/", "10.1111/"): "https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}",
        ("10.1088/", "10.1209/"): "https://iopscience.iop.org/article/{doi}/pdf",
        ("10.1080/",): "https://www.tandfonline.com/doi/pdf/{doi}",
        ("10.1142/",): "https://www.worldscientific.com/doi/pdf/{doi}",
        ("10.1021/",): "https://pubs.acs.org/doi/pdf/{doi}",
        ("10.1143/", "10.7566/"): "https://journals.jps.jp/doi/pdf/{doi}",
        ("10.1126/",): "https://www.science.org/doi/pdf/{doi}",
        ("10.1137/",): "https://epubs.siam.org/doi/pdf/{doi}",
        ("10.1145/",): "https://dl.acm.org/doi/pdf/{doi}",
        ("10.1073/",): "https://www.pnas.org/doi/pdf/{doi}",
        ("10.1146/",): "https://www.annualreviews.org/doi/pdf/{doi}",
        ("10.1063/",): "https://pubs.aip.org/doi/pdf/{doi}",
        ("10.1364/",): "https://opg.optica.org/viewmedia.cfm?doi={doi}",
    }
    for prefixes, tpl in templates.items():
        if d.startswith(prefixes):
            add(tpl.format(doi=doi))
    # links / buttons a reader would click: first those that look like PDF addresses, then any
    # link whose label says PDF (e.g. 'View PDF' leading to a viewer page)
    labelled = []
    for m in re.finditer(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html, re.I | re.S):
        href, text = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        tag = m.group(0)
        pdfish = re.search(r"\.pdf(\b|$|\?)|/pdf(/|$|\?)|/pdfft|/epdf/|pdfdirect|/download|getpdf|type=pdf", href, re.I)
        texty = re.search(r"\b(download\s+(full[- ]text\s+)?pdf|view\s+pdf|pdf|full\s+text\s+pdf|download\s+article)\b",
                          text + " " + tag, re.I)
        if pdfish and texty:
            add(href)
        elif texty and re.search(r"\bpdf\b", text, re.I):
            labelled.append(href)
    for h in labelled:
        add(h)
    for m in re.finditer(r"<a\b[^>]*href=[\"']([^\"']+\.pdf(?:\?[^\"']*)?)[\"']", html, re.I):
        add(m.group(1))
    # PDF embedded in a viewer page
    for m in re.finditer(r"<(?:iframe|embed|object)\b[^>]*(?:src|data)=[\"']([^\"']+)[\"']", html, re.I):
        if re.search(r"pdf|getPDF|stamp", m.group(1), re.I):
            add(m.group(1))
    return out[:12]


class PublisherFetcher:
    def __init__(self, settings):
        self.settings = settings
        cfg = settings.get("pdf") or {}
        self.resolver = (cfg.get("doi_resolver") or "https://doi.org/").rstrip("/") + "/"
        self.use_browser = bool(cfg.get("use_browser", True))
        self.headless = bool(cfg.get("browser_headless", False))
        self.elsevier_key = (cfg.get("elsevier_api_key") or "").strip()
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9",
                                  "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
        self.rl = RateLimiter(0.5)  # at most one publisher request every 2 s
        self.max_bytes = int(cfg.get("max_download_mb") or 150) * 1024 * 1024
        self._pw = None
        self._ctx = None
        self.browser_error = None
        self.blocked = False          # the last fetch met a captcha / bot check
        self.rejects_automation = False  # ... that loops for automated browsers (needs your own browser)
        self._challenges = 0
        self.looping_prefixes: set[str] = set()
        self.progress = None          # callback(message) to tell you when your help is needed
        self.captcha_wait = float(cfg.get("captcha_wait_seconds") or 180)

    # ------------------------------------------------------------------ plain web requests
    def _save_if_pdf(self, url: str, dest: Path, referer: str | None = None) -> tuple[bool, str, str | None]:
        """Download *url*; returns (is_pdf, message, html_if_not_pdf)."""
        self.rl.wait()
        try:
            with self.http.get(url, stream=True, timeout=60, allow_redirects=True,
                               headers={"Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
                                        **({"Referer": referer} if referer else {})}) as r:
                if r.status_code != 200:
                    return False, f"HTTP {r.status_code}", None
                it = r.iter_content(1 << 16)
                first = next(it, b"")
                if not _is_pdf(first):
                    rest = first + b"".join(c for _, c in zip(range(40), it))
                    return False, "not a PDF", rest.decode("utf-8", "replace")
                dest.parent.mkdir(parents=True, exist_ok=True)
                size, t0 = len(first), time.monotonic()
                with open(dest, "wb") as f:
                    f.write(first)
                    for chunk in it:
                        size += len(chunk)
                        if size > self.max_bytes or time.monotonic() - t0 > 180:
                            return False, "too large or too slow", None
                        f.write(chunk)
            return True, url, None
        except requests.RequestException as e:
            return False, str(e)[:150], None

    def fetch_http(self, doi: str, dest: Path) -> tuple[bool, str]:
        tried = []
        if self.elsevier_key and doi.lower().startswith("10.1016/"):
            # Elsevier's official full-text API honours your institution's subscription (by network address)
            self.rl.wait()
            try:
                r = self.http.get(f"https://api.elsevier.com/content/article/doi/{doi}",
                                  params={"httpAccept": "application/pdf"},
                                  headers={"X-ELS-APIKey": self.elsevier_key, "Accept": "application/pdf"}, timeout=60)
                if r.status_code == 200 and _is_pdf(r.content[:1024]):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(r.content)
                    return True, "Elsevier API"
                tried.append(f"Elsevier API: HTTP {r.status_code}")
            except requests.RequestException as e:
                tried.append(f"Elsevier API: {e}"[:80])
        self.rl.wait()
        try:
            page = self.http.get(self.resolver + doi, timeout=60, allow_redirects=True)
        except requests.RequestException as e:
            return False, f"publisher page unreachable: {e}"[:150]
        if is_challenge(page.text if "html" in page.headers.get("Content-Type", "") else ""):
            self.blocked = True
            tried.append("the publisher showed a captcha / bot check")
            html, page_url = "", page.url
        elif page.status_code in (401, 403, 429, 503):
            self.blocked = self.blocked or page.status_code in (403, 429, 503)
            tried.append(f"article page: HTTP {page.status_code} (bot protection?)")
            html, page_url = "", page.url
        elif _is_pdf(page.content[:1024]):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(page.content)
            return True, page.url
        else:
            html, page_url = page.text, page.url
        for u in pdf_urls_from_page(html, page_url, doi):
            ok, msg, body = self._save_if_pdf(u, dest, referer=page_url)
            if ok:
                return True, u
            if body and is_challenge(body):
                self.blocked = True
                tried.append(f"{urlparse(u).hostname}: captcha / bot check")
                continue
            # a viewer page that wraps the PDF (e.g. IEEE stamp page): follow its frame once
            if body:
                inner = [x for x in pdf_urls_from_page(body, u, doi) if x != u][:2]
                for u2 in inner:
                    ok2, _, _ = self._save_if_pdf(u2, dest, referer=u)
                    if ok2:
                        return True, u2
            tried.append(f"{urlparse(u).hostname}: {msg}")
        return False, "; ".join(tried[-3:]) or "no PDF link found on the publisher's page"

    # ------------------------------------------------------------------ real browser
    def _browser(self):
        if self._ctx is not None:
            return self._ctx
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.browser_error = "browser mode needs Playwright: run  pip install playwright  (it uses your Google Chrome)"
            return None
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        # look like a normal Chrome: no 'controlled by automated software' flags (bot checks look for them)
        opts = dict(user_data_dir=str(PROFILE_DIR), headless=self.headless, accept_downloads=True,
                    viewport={"width": 1200, "height": 900}, ignore_default_args=["--enable-automation"],
                    args=["--disable-blink-features=AutomationControlled"])
        for channel in ("chrome", "msedge", None):  # prefer the installed Chrome: fewer bot checks
            try:
                self._ctx = self._pw.chromium.launch_persistent_context(**opts, **({"channel": channel} if channel else {}))
                break
            except Exception as e:  # channel not installed
                self.browser_error = f"could not start a browser: {e}"[:200]
        if self._ctx is not None:
            self.browser_error = None
            try:
                self._ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            except Exception:
                pass
        return self._ctx

    def _say(self, msg):
        if self.progress:
            try:
                self.progress(msg)
            except Exception:
                pass

    def _page_is_challenge(self, page) -> bool:
        try:
            return is_challenge(page.content(), page.title())
        except Exception:
            return False

    def _wait_for_human(self, page, what: str) -> bool:
        """If *page* shows a captcha: bring the window to the front and wait until
        you have solved it. Returns False if it is still there after the timeout
        (or if the browser is hidden and nobody can solve it)."""
        if not self._page_is_challenge(page):
            return True
        self._challenges += 1
        if self._challenges > 2:
            # solved, and asked again: the site rejects the automated browser whatever you do
            self.rejects_automation = True
            self._say(f"The site keeps asking for the check in the automated browser — skipping ({what})")
            return False
        if self.headless:
            # many checks (e.g. Cloudflare's) pass by themselves after a few seconds
            for _ in range(6):
                page.wait_for_timeout(2000)
                if not self._page_is_challenge(page):
                    return True
            return False
        try:
            page.bring_to_front()
        except Exception:
            pass
        # Solving a check makes the site reload the page. If the check is still
        # there after reloads, the site is rejecting the automated browser and
        # would ask again forever: stop and hand over to your own browser.
        loads = [0]

        def on_nav(frame):
            try:
                if frame == page.main_frame:
                    loads[0] += 1
            except Exception:
                pass
        try:
            page.on("framenavigated", on_nav)
        except Exception:
            pass
        deadline = time.monotonic() + self.captcha_wait
        try:
            while time.monotonic() < deadline:
                left = int(deadline - time.monotonic())
                self._say(f"ACTION NEEDED: solve the check in the browser window ({what}) — waiting {left} s. "
                          f"If it keeps coming back, just close that tab")
                try:
                    page.wait_for_timeout(2000)
                except Exception:   # tab or window closed by you
                    pass
                if page.is_closed():
                    self.rejects_automation = True
                    self._say(f"Tab closed — skipping, use 'Get with my own browser' ({what})")
                    return False
                if not self._page_is_challenge(page):
                    self._say(f"Check solved, continuing ({what})")
                    page.wait_for_timeout(1500)
                    return True
                if loads[0] >= 2:
                    self.rejects_automation = True
                    self._say(f"The check came back after you solved it: the site rejects the automated browser — "
                              f"skipping ({what})")
                    return False
        finally:
            try:
                page.remove_listener("framenavigated", on_nav)
            except Exception:
                pass
        return False

    def fetch_browser(self, doi: str, dest: Path, wait_for_user: float | None = None) -> tuple[bool, str]:
        """Returns (ok, url_or_reason); sets self.blocked when a captcha stopped it."""
        if wait_for_user is None:
            wait_for_user = float((self.settings.get("pdf") or {}).get("browser_wait_seconds") or 30)
        self._challenges = 0
        ok, msg = self._fetch_browser_once(doi, dest, wait_for_user)
        if self.rejects_automation:
            return False, "the site keeps re-asking the captcha in the automated browser"
        if not ok and self.blocked and self.headless:
            # nobody can solve a captcha in a hidden window: show the browser for this item
            self.close()
            self.headless = False
            ok, msg = self._fetch_browser_once(doi, dest, wait_for_user)
        return ok, msg

    def _fetch_browser_once(self, doi: str, dest: Path, wait_for_user: float) -> tuple[bool, str]:
        ctx = self._browser()
        if ctx is None:
            return False, self.browser_error or "browser unavailable"
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        got = {}

        def on_response(resp):
            try:
                if "application/pdf" in (resp.headers.get("content-type") or "") and "body" not in got:
                    body = resp.body()
                    if _is_pdf(body[:1024]):
                        got["body"], got["url"] = body, resp.url
            except Exception:
                pass
        ctx.on("response", on_response)

        def try_download(action, timeout=20000):
            try:
                with page.expect_download(timeout=timeout) as dl:
                    action()
                path = dl.value.path()
                if path and _is_pdf(Path(path).read_bytes()[:1024]):
                    got.setdefault("body", Path(path).read_bytes())
                    got.setdefault("url", dl.value.url)
            except Exception:
                pass

        try:
            self.rl.wait()
            try:
                page.goto(self.resolver + doi, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass  # navigation into a download raises; the response hook has the PDF then
            if "body" not in got and not self._wait_for_human(page, "article page"):
                self.blocked = True
                return False, "a captcha on the article page was not solved"
            # give redirects / a login you may complete yourself some time
            deadline = time.monotonic() + wait_for_user
            html = ""
            while time.monotonic() < deadline and "body" not in got:
                page.wait_for_timeout(1500)
                html = page.content()
                if self._page_is_challenge(page):
                    if not self._wait_for_human(page, "article page"):
                        self.blocked = True
                        return False, "a captcha on the article page was not solved"
                    continue
                if pdf_urls_from_page(html, page.url, doi) or re.search(r"download|pdf", html, re.I):
                    break
            # 1) PDF addresses on the page, fetched with the browser's cookies; if the site
            #    answers with a check, open the address in the window so you can solve it
            for u in ([] if "body" in got else pdf_urls_from_page(html, page.url, doi)):
                if "body" in got:
                    break
                try:
                    r = ctx.request.get(u, timeout=60000, headers={"Referer": page.url})
                    b = r.body()
                    if r.ok and _is_pdf(b[:1024]):
                        got["body"], got["url"] = b, u
                        break
                    challenged = is_challenge(b[:60000].decode("utf-8", "replace")) or r.status in (403, 429, 503)
                except Exception:
                    challenged = True
                if challenged:
                    try_download(lambda: page.goto(u, wait_until="domcontentloaded", timeout=60000), timeout=15000)
                    if "body" not in got and not self._wait_for_human(page, "PDF link"):
                        self.blocked = True
                        return False, "a captcha on the PDF link was not solved"
                    page.wait_for_timeout(3000)
            # 2) click what a reader would click
            if "body" not in got:
                for sel in ('a:has-text("Download PDF")', 'button:has-text("Download PDF")', 'a:has-text("View PDF")',
                            'a[aria-label*="PDF" i]', 'button[aria-label*="PDF" i]', 'a:has-text("PDF")',
                            'button:has-text("PDF")', 'a:has-text("Download")'):
                    loc = page.locator(sel).first
                    try:
                        if not loc.count():
                            continue
                    except Exception:
                        continue
                    try_download(lambda: loc.click(timeout=5000))
                    page.wait_for_timeout(3000)
                    for p in list(ctx.pages):
                        if "body" in got:
                            break
                        if self._page_is_challenge(p):  # the click led to a check
                            if not self._wait_for_human(p, "PDF download"):
                                self.blocked = True
                                return False, "a captcha on the PDF download was not solved"
                            p.wait_for_timeout(3000)
                        if "body" not in got and p is not page and "pdf" in p.url.lower():
                            try:
                                r = ctx.request.get(p.url, timeout=60000)
                                if r.ok and _is_pdf(r.body()[:1024]):
                                    got["body"], got["url"] = r.body(), p.url
                            except Exception:
                                pass
                    if "body" in got:
                        break
        except Exception as e:
            return False, f"browser: {e}"[:200]
        finally:
            try:
                ctx.remove_listener("response", on_response)
            except Exception:
                pass
            for p in list(ctx.pages)[1:]:
                try:
                    p.close()
                except Exception:
                    pass
        if "body" not in got:
            return False, "the publisher's page offers no PDF to this network (no subscription?)"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(got["body"])
        return True, got["url"]

    def fetch(self, doi: str, dest: Path) -> tuple[bool, str, str]:
        """Returns (ok, message_or_url, how). After a failure, ``self.blocked`` tells
        whether a captcha (rather than missing access) was the reason."""
        self.blocked = False
        self.rejects_automation = False
        ok, msg = self.fetch_http(doi, dest)
        if ok:
            return True, msg, "web"
        host = (doi.split("/")[0] if "/" in doi else doi)
        if self.use_browser and host in self.looping_prefixes:
            self.blocked = self.rejects_automation = True
            return False, f"{msg} | this publisher rejects the automated browser (seen earlier in this run)", "browser"
        if self.use_browser:
            http_blocked = self.blocked
            ok2, msg2 = self.fetch_browser(doi, dest)
            if ok2:
                self.blocked = False
                return True, msg2, "browser"
            if self.rejects_automation:
                self.blocked = True
                self.looping_prefixes.add(host)
            if self._ctx is None and http_blocked:
                self.blocked = True  # the browser could not even start: the captcha is still the reason
            return False, f"{msg} | {msg2}", "browser"
        return False, msg, "web"

    def close(self):
        try:
            if self._ctx:
                self._ctx.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._ctx = self._pw = None
