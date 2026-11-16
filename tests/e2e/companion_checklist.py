"""Browser checklist for Ask AI and the companion token: Settings -> AI Access makes a companion
token and shows the commands for the other computer; "Ask AI" appears in a note and in the PDF
viewer only once a token exists; with no companion connected the panel says how to start one; a
scripted companion (plain HTTP calls, no AI) then answers: streamed text, the tool line, a
rendered citation and math, hostile output shown as inert text, Stop, a follow-up in the same
conversation, Escape. The question from a note carries the selection, the one from the PDF the
quoted words and page.

    python tests/e2e/companion_checklist.py

Starts its own server on port 8860 with a throw-away data folder; your library is not touched.
"""

import asyncio
import os
import re
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
from tests.e2e._guard import guard  # noqa: E402

PORT = 8860
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-companion-e2e-")
PDF = APP / "tests" / "e2e" / "fixtures" / "twocolumn.pdf"
SHOTS = os.environ.get("SHOTS")
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


class Companion(threading.Thread):
    """The computer's part, played with HTTP calls: polls, then answers according to a word in the question."""

    def __init__(self, token, key):
        super().__init__(daemon=True)
        self.token, self.key = token, key
        self.jobs = []
        self.saw_cancel = threading.Event()
        self.gate = threading.Event()
        self.quit = False

    def call(self, path, body):
        h = {"Authorization": f"Bearer {self.token}"}
        return requests.post(
            f"{BASE}/api/companion/{path}", json={"client": "e2e", **body}, headers=h, timeout=40
        ).json()

    def post(self, job, *events):
        return self.call(f"jobs/{job['id']}/events", {"events": list(events)}).get("cancelled")

    def run(self):
        while not self.quit:
            try:
                r = self.call(
                    "poll", {"name": "E2E laptop", "providers": [{"id": "claude", "label": "Claude"}], "running": []}
                )
            except requests.RequestException:
                time.sleep(0.3)
                continue
            if r.get("job"):
                self.answer(r["job"])

    def answer(self, job):
        self.jobs.append(job)
        said = job["prompt"]
        if "SLOW" in said:
            self.post(job, {"type": "text", "text": "Starting a long answer. "})
            for _ in range(100):
                time.sleep(0.2)
                if self.post(job, {"type": "text", "text": "more "}):
                    self.saw_cancel.set()
                    return
            self.post(job, {"type": "done"})
        elif "STREAM" in said:
            self.post(job, {"type": "text", "text": "Thinking about the **state**. "})
            time.sleep(1.0)
            self.post(job, {"type": "tool", "name": "mcp__synapse__get_paper"})
            time.sleep(1.2)
            self.post(
                job,
                {
                    "type": "text",
                    "text": f"The result holds [@{self.key}, p. 2] and $x^2$ is small.\n\n"
                    '<img src=x onerror="window.__pwn=1"><script>window.__pwn=1</script>\n\n'
                    "![t](https://evil.example/x.png)\n\n![u](../../../traversal.png)\n\n![v](//evil.example/y.png)\n\n[click](javascript:window.__pwn=1)",
                },
            )
            self.post(job, {"type": "done"})
        else:
            self.post(
                job,
                {"type": "text", "text": f"Follow-up answer {'resumed' if job.get('resume') else 'fresh'}."},
                {"type": "done"},
            )


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


async def until(page, js, t=8000):
    end = time.time() + t / 1000
    while time.time() < end:
        if await page.evaluate(js):
            return True
        await page.wait_for_timeout(100)
    return False


async def main():
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser"],
        cwd=APP,
        env=dict(os.environ, SYNAPSE_DATA_DIR=DATA),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.5)
    try:
        item = requests.post(
            f"{BASE}/api/items", json={"authors": "Doe, Jane", "title": "Open systems", "year": 2020}
        ).json()
        key = item["citation_key"]
        with open(PDF, "rb") as fh:
            att = requests.post(
                f"{BASE}/api/items/{item['id']}/attachments", files={"file": ("paper.pdf", fh, "application/pdf")}
            ).json()["attachments"][0]
        requests.put(
            f"{BASE}/api/notes/note",
            json={
                "path": "Review.md",
                "create": True,
                "text": f"# Review\n\nMatrix product operators reach steady states [@{key}, p. 1].\n",
            },
        )
        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(viewport={"width": 1280, "height": 800})
            page = await ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            asks = []
            made = []
            requests_seen = []
            page.on("request", lambda r: requests_seen.append(r.url))
            page.on(
                "response",
                lambda r: made.append(r) if r.url.endswith("/api/mcp/tokens") and r.request.method == "POST" else None,
            )
            page.on("response", lambda r: asks.append(r) if r.url.endswith("/api/ai/ask") else None)

            # ---- without a companion token nothing is offered
            await page.goto(f"{BASE}/#note=Review.md")
            await page.wait_for_selector(".note-head", timeout=15000)
            await page.wait_for_selector(".cm-content", timeout=15000)
            await page.wait_for_timeout(600)
            check(
                "no Ask AI button before a companion token exists",
                await page.locator('[data-act="ask-ai"]').is_hidden(),
            )
            await page.keyboard.press("Control+Shift+P")
            await page.fill(".palette-input", "Ask AI")
            check("no Ask AI command either", await page.locator(".palette-item").count() == 0)
            await page.keyboard.press("Escape")

            # ---- Settings -> AI Access: a companion token and the commands for the other computer
            await page.keyboard.press("Control+,")
            await page.click('.settings-nav [data-nav="ai"]')
            await page.wait_for_selector("[data-companion-new]", timeout=8000)
            check(
                "Settings offers New Companion Token",
                "New Companion Token" in await page.inner_text("[data-companion-new]"),
            )
            await page.click("[data-companion-new]")
            await page.fill(".dialog input", "E2E laptop")
            await page.click(".dialog .btn-primary, .dialog [data-ok]")
            await page.wait_for_selector("[data-ai-command]", timeout=8000)
            cmds = await page.inner_text("[data-ai-command]")
            token = (await made[-1].json())["token"]
            check("a companion token is made", token.startswith("syn_companion_"), token[:16])
            check(
                "the commands connect and run; no token inside any command",
                f"connect {BASE}" in cmds
                and "synapse_companion.py run" in cmds
                and token not in cmds
                and "--token" not in cmds,
                cmds,
            )
            dl = page.locator("[data-ai-download]")
            check("a download button fetches the script", await dl.get_attribute("href") == "/api/ai/companion-script")
            async with page.expect_download() as got:
                await dl.click()
            name = (await got.value).suggested_filename
            check("the download is named synapse_companion.py", name == "synapse_companion.py", name)
            check(
                "the token row says what the token is for",
                "Companion · local AI and extension updates" in await page.inner_text("[data-ai-list]"),
            )
            await shot(page, "1-settings")
            await page.keyboard.press("Escape")

            # ---- with a token: the entry points appear (a fresh load asks the server again)
            await page.goto(f"{BASE}/#note=Review.md")
            await page.reload()
            await page.wait_for_selector(".cm-content", timeout=15000)
            check(
                "the Ask AI button appears in the note",
                await until(page, "!document.querySelector('[data-act=ask-ai]')?.hidden"),
            )
            await page.keyboard.press("Control+Shift+P")
            await page.fill(".palette-input", "Ask AI")
            check("and the palette command", await page.locator(".palette-item").count() == 1)
            await page.keyboard.press("Escape")

            # ---- no companion connected: the panel explains how to start one
            await page.click(".cm-content")
            await page.keyboard.press("Control+A")
            await page.click('[data-act="ask-ai"]')
            await page.wait_for_selector(".ai-panel .ai-help:not([hidden])", timeout=8000)
            helptext = await page.inner_text(".ai-help")
            check(
                "no companion: the panel says how to start one", "python synapse_companion.py run" in helptext, helptext
            )
            check("the selection is the context chip", "Review" in await page.inner_text(".ai-chip-text"))
            await shot(page, "2-no-companion")

            comp = Companion(token, key)
            comp.start()
            await page.wait_for_timeout(500)
            await page.click("[data-recheck]")
            check(
                "Check again finds the companion",
                await until(page, "document.querySelector('.ai-help')?.hidden === true"),
            )

            # ---- a streamed answer
            await page.fill(".ai-input", "STREAM please")
            await page.keyboard.press("Enter")
            check("the question is shown", "STREAM please" in await page.inner_text(".ai-q"))
            check(
                "the first words arrive while the answer is running",
                await until(
                    page,
                    "document.querySelector('.ai-answer')?.textContent.includes('Thinking about') && document.querySelector('.ai-turn.streaming') !== null",
                ),
            )
            check(
                "the state is shown while it runs",
                await until(page, "!!document.querySelector('.ai-status')?.textContent"),
            )
            check(
                "the tool line names what is being read",
                await until(page, "document.querySelector('.ai-status')?.textContent.includes('get paper')"),
            )
            check("the Stop button is shown while it runs", await page.locator("[data-stop]").is_visible())
            check(
                "the answer finishes",
                await until(
                    page,
                    "document.querySelector('.ai-turn') && !document.querySelector('.ai-turn.streaming') && document.querySelector('.ai-answer .cite')",
                    15000,
                ),
            )
            check("the answer is rendered Markdown", await page.locator(".ai-answer strong").count() == 1)
            check(
                "the citation is a link with a label",
                "Doe" in await page.inner_text(".ai-answer a.cite"),
                await page.inner_text(".ai-answer a.cite"),
            )
            check("math is rendered", await page.locator(".ai-answer .katex").count() >= 1)
            check(
                "hostile output is inert: no script, no event handler, no remote image, no javascript: link",
                await page.evaluate(
                    """() => { const a = document.querySelector('.ai-answer');
                      return !a.querySelector('script') && !a.querySelector('[onerror]') && !a.querySelector('img')
                        && ![...a.querySelectorAll('a')].some(x => /^javascript:/i.test(x.getAttribute('href') || '')) && !window.__pwn; }"""
                ),
            )
            await page.wait_for_timeout(500)
            stray = [
                u
                for u in requests_seen
                if not u.startswith(BASE) or ("/traversal" in u or u.rstrip("/") == BASE + "/x")
            ]
            check("no request to a remote host or outside /api/notes/ for answer images", not stray, stray)
            check("the markup is shown as text", "<script>" in await page.inner_text(".ai-answer"))
            check(
                "the context went along (the selected note text)",
                "Matrix product operators" in comp.jobs[0]["prompt"],
                comp.jobs[0]["prompt"][:200],
            )
            await shot(page, "3-answer")
            await page.click(".ai-answer a.cite")
            check(
                "clicking the citation opens the PDF",
                await until(page, "window.zcTabs && window.zcTabs.list().some(t => t.kind === 'pdf')"),
            )
            await page.keyboard.press("Control+1")  # back to the Synapse tab (tabs/tabs.ts)
            await page.wait_for_timeout(300)

            # ---- stop
            if not await page.locator(".ai-panel").count():
                await page.click('[data-act="ask-ai"]')
            await page.fill(".ai-input", "SLOW one")
            await page.keyboard.press("Enter")
            check(
                "a long answer starts",
                await until(
                    page, "document.querySelector('.ai-turn:last-child .ai-answer')?.textContent.includes('Starting')"
                ),
            )
            await page.click("[data-stop]")
            check(
                "Stop ends it and says so",
                await until(
                    page, "document.querySelector('.ai-turn:last-child .ai-status')?.textContent === 'Stopped.'"
                ),
            )
            check("the companion is told to stop", await asyncio.to_thread(comp.saw_cancel.wait, 8))
            first = (await asks[0].json())["conversation"]
            job_id = (await asks[-1].json())["job"]
            check(
                "the job is cancelled at the server",
                requests.get(f"{BASE}/api/ai/jobs/{job_id}?after=0", timeout=30).json()["state"] == "cancelled",
            )

            # ---- a follow-up stays in the conversation
            await page.fill(".ai-input", "FOLLOW up")
            await page.keyboard.press("Enter")
            check(
                "the follow-up is answered",
                await until(
                    page,
                    "document.querySelector('.ai-turn:last-child .ai-answer')?.textContent.includes('Follow-up answer')",
                    12000,
                ),
            )
            last = comp.jobs[-1]
            check(
                "in the same conversation, resumed",
                (await asks[-1].json())["conversation"] == first
                and last.get("resume") is True
                and last.get("conversation") == first,
                (last.get("conversation"), first, last.get("resume")),
            )
            check("a follow-up does not repeat the context", await page.locator(".ai-chip [data-drop]").is_hidden())

            # ---- Escape closes the panel and gives the focus back
            await page.keyboard.press("Escape")
            check("Escape closes the panel", await page.locator(".ai-panel").count() == 0)

            # ---- the PDF viewer: select words, Ask AI
            v = await ctx.new_page()
            v.on("pageerror", lambda e: errors.append(str(e)))
            await v.goto(f"{BASE}/viewer.html?file=/pdfs/{att['url']}&paper_id={item['id']}&title=x&att={att['id']}")
            await v.wait_for_selector(".textLayer span", timeout=20000)
            await v.wait_for_timeout(600)
            await v.evaluate(
                """() => { const s = document.querySelector('.page-wrapper .textLayer span');
                  const r = document.createRange(); r.selectNodeContents(s);
                  const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
                  document.getElementById('pdf-container').dispatchEvent(new MouseEvent('mouseup', { bubbles: true })); }"""
            )
            await v.wait_for_selector(".zc-pill [data-ask]", timeout=5000)
            quote = await v.evaluate("getSelection().toString()")
            await v.click(".zc-pill [data-ask]")
            await v.wait_for_selector(".ai-panel", timeout=5000)
            check(
                "the viewer's panel names the page and the words",
                re.match(r"p\. 1 — ", await v.inner_text(".ai-chip-text")) is not None,
                await v.inner_text(".ai-chip-text"),
            )
            await v.fill(".ai-input", "STREAM what is this")
            await v.keyboard.press("Enter")
            check(
                "the viewer shows the answer with its citation",
                await until(
                    v,
                    "!!document.querySelector('.ai-answer a.cite') && !document.querySelector('.ai-turn.streaming')",
                    15000,
                ),
            )
            prompt = comp.jobs[-1]["prompt"]
            check(
                "the quoted words and the page reached the companion",
                quote.strip()[:20] in prompt and "1" in prompt,
                prompt[:300],
            )
            await shot(v, "4-viewer")
            before = await v.evaluate("document.getElementById('pdf-container').getBoundingClientRect().width")
            await v.evaluate(
                "document.querySelector('.page-wrapper[data-page-number=\"2\"]').scrollIntoView({ block: 'start' })"
            )
            await v.wait_for_timeout(600)
            indicator = await v.inner_text("#page-num-text")
            check("the page indicator follows the scroll with the panel open", indicator.startswith("2 /"), indicator)
            check(
                "the panel overlays the viewer without resizing it",
                before > 0
                and await v.evaluate("document.getElementById('pdf-container').getBoundingClientRect().width")
                == before,
            )
            await v.click(".ai-answer a.cite")
            await v.wait_for_timeout(500)
            check("a citation of this PDF scrolls it (no new page)", len(ctx.pages) == 2)
            await v.keyboard.press("Escape")
            check("Escape closes the viewer's panel", await v.locator(".ai-panel").count() == 0)

            # ---- the phone layout keeps working
            m = await b.new_context(viewport={"width": 390, "height": 760}, has_touch=True, is_mobile=True)
            ph = await m.new_page()
            ph.on("pageerror", lambda e: errors.append(str(e)))
            await ph.goto(BASE)
            await ph.wait_for_timeout(1500)
            check("the phone layout loads", await ph.locator("body").count() == 1)
            check("no page errors", not errors, errors[:5])
            comp.quit = True
            await b.close()
    finally:
        srv.terminate()
        shutil.rmtree(DATA, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


guard(PORT)
asyncio.run(main())
