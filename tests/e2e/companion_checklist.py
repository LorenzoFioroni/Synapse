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


PROVIDERS = [
    {
        "id": "claude",
        "label": "Claude",
        "models": [
            {"id": "sonnet", "label": "Sonnet"},
            {"id": "opus", "label": "Opus"},
            {"id": "long", "label": "Opus with extended thinking and a context window of one million tokens"},
        ],
        "efforts": ["low", "medium", "high"],
    }
]
SECOND = {
    "id": "codex",
    "label": "Codex",
    "models": [{"id": "gpt-x", "label": "GPT-X"}, {"id": "gpt-y", "label": "GPT-Y"}],
    "efforts": ["minimal", "high"],
}


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
                r = self.call("poll", {"name": "E2E laptop", "providers": list(PROVIDERS), "running": []})
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
            self.post(job, {"type": "started"})
            time.sleep(0.8)
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
            self.post(
                job,
                {"type": "usage", "input_tokens": 1234, "output_tokens": 56, "cost_usd": 0.0123, "duration_ms": 3400},
            )
            self.post(job, {"type": "done"})
        else:
            self.post(
                job,
                {"type": "text", "text": f"Follow-up answer {'resumed' if job.get('resume') else 'fresh'}."},
                {"type": "done"},
            )


async def shot(page, name):
    try:
        print("SHOT", name, await page.evaluate("window.zcTabs && window.zcTabs.list().map(t => t.active)"))
    except Exception:
        pass
    if SHOTS:
        await page.wait_for_timeout(300)  # entrance animations
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


async def one_line(page):
    """Where the buttons of the question box are: Send must share the row of the @ button, inside the box."""
    return await page.evaluate(
        """() => { const r = (s) => document.querySelector(s).getBoundingClientRect();
          const row = r('.ai-controls'), at = r('[data-mention]'), send = r('[data-send]'), box = r('.ai-composer');
          const val = document.querySelector('[data-model] .ai-pick-val');
          return { rowH: row.height, sameRow: Math.abs((send.top + send.height / 2) - (at.top + at.height / 2)) < 3,
            inside: send.right <= box.right && send.left >= box.left, sendW: send.width,
            clipped: !!val && val.scrollWidth > val.clientWidth, noScroll: document.documentElement.scrollWidth <= innerWidth }; }"""
    )


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

            # ---- the popup: a floating card in the lower right corner; the details panel stays
            box = await page.locator(".ai-panel").bounding_box()
            check(
                "the panel starts as a popup in the lower right corner, inside the window",
                await page.locator(".ai-panel").get_attribute("data-placement") == "popup"
                and box["x"] + box["width"] <= 1280
                and box["y"] + box["height"] <= 800
                and box["x"] > 700
                and box["y"] > 150,
                box,
            )
            check("the details panel is still there", await page.locator(".notes-side .ns-details").is_visible())
            check("the agent selector is hidden with a single agent", await page.locator("[data-agent]").is_hidden())
            check(
                "model and effort are icon buttons that show their value, Default until chosen",
                await page.locator("[data-model] .ai-pick-val").inner_text() == "Default"
                and "Effort: Default" in (await page.locator("[data-effort]").get_attribute("aria-label")),
            )
            await page.click("[data-model]")
            items = await page.locator(".menu .menu-item").all_inner_texts()
            check(
                "the model menu lists the allowed values, Default first and checked",
                [i.replace("✓", "").strip() for i in items][:3] == ["Default", "Sonnet", "Opus"]
                and await page.locator(".menu .menu-item .check").first.inner_text() == "✓",
                items,
            )
            await shot(page, "2c-model-menu")
            await page.keyboard.press("Escape")
            check(
                "Escape closes the menu and not the panel",
                await page.locator(".menu").count() == 0 and await page.locator(".ai-panel").count() == 1,
            )
            await page.click("[data-model]")
            await page.locator(".menu .menu-item", has_text="Opus").first.click()
            await page.focus("[data-effort]")
            await page.keyboard.press("Enter")
            check("a picker opens from the keyboard", await page.locator(".menu .menu-item").count() == 4)
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            check(
                "arrows and Enter choose a value; the buttons show it",
                await page.locator("[data-model] .ai-pick-val").inner_text() == "Opus"
                and await page.locator("[data-effort]").get_attribute("aria-label") == "Effort: High"
                and await page.locator(".menu").count() == 0,
                await page.locator("[data-effort]").get_attribute("aria-label"),
            )
            await shot(page, "2b-popup-empty")

            # ---- citing papers and notes: "[@" and "[[" open a list over the question box
            n_asks = len(asks)
            await page.click(".ai-input")
            await page.keyboard.type("see [@")
            check(
                "typing [@ lists library items by key, author and title",
                await until(page, "document.querySelectorAll('.ai-mentions .ai-mention').length === 1")
                and key in await page.inner_text(".ai-mention-key")
                and "Doe" in await page.inner_text(".ai-mention-detail")
                and "Open systems" in await page.inner_text(".ai-mention-title"),
            )
            await shot(page, "2d-mentions-popup")
            await page.keyboard.press("Escape")
            check(
                "Escape closes the list and leaves the panel and the text",
                await page.locator(".ai-mentions").is_hidden()
                and await page.locator(".ai-panel").count() == 1
                and await page.input_value(".ai-input") == "see [@",
            )
            await page.keyboard.type("Doe")
            check("typing again reopens it", await until(page, "!document.querySelector('.ai-mentions')?.hidden"))
            await page.keyboard.press("Enter")
            check(
                "Enter inserts [@key] and does not send",
                await page.input_value(".ai-input") == f"see [@{key}]"
                and await page.locator(".ai-mentions").is_hidden()
                and len(asks) == n_asks
                and await page.locator(".ai-turn").count() == 0,
                await page.input_value(".ai-input"),
            )
            await page.fill(".ai-input", "")
            await page.keyboard.type("and [[Rev")
            check(
                "typing [[ lists notes",
                await until(page, "document.querySelectorAll('.ai-mentions .ai-mention').length >= 1")
                and "Review" in await page.inner_text(".ai-mention-key"),
            )
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("ArrowUp")
            await page.keyboard.press("Tab")
            check(
                "Tab inserts [[note]]",
                await page.input_value(".ai-input") == "and [[Review]]",
                await page.input_value(".ai-input"),
            )
            await page.fill(".ai-input", "")
            await page.click("[data-mention]")
            check(
                "the @ button inserts [@ and opens the list, the caret staying in the text",
                await page.input_value(".ai-input") == "[@"
                and await until(page, "!document.querySelector('.ai-mentions')?.hidden")
                and await page.evaluate("document.activeElement === document.querySelector('.ai-input')"),
            )
            await page.locator(".ai-mention", has_text=key).click()
            check(
                "clicking an item inserts it",
                await page.input_value(".ai-input") == f"[@{key}]" and await page.locator(".ai-mentions").is_hidden(),
            )
            await page.fill(".ai-input", "zzz [@nothing-like-this")
            check(
                "an unknown query says so",
                await until(page, "document.querySelector('.ai-mention-empty')?.textContent === 'No matching paper'"),
            )
            await page.fill(".ai-input", "")
            await page.click(".ai-input")

            # ---- the working state shows at once (the server is made slow to answer on purpose)
            async def slow(route):
                await asyncio.sleep(1.0)
                try:
                    await route.continue_()
                except Exception:  # noqa: BLE001 - the route was removed meanwhile
                    pass

            await page.route("**/api/ai/ask", slow)
            await page.fill(".ai-input", "STREAM please")
            await page.keyboard.press("Enter")
            check(
                "the question and 'Sending…' appear before the server has answered",
                await until(
                    page,
                    "document.querySelector('.ai-q')?.textContent === 'STREAM please' && document.querySelector('.ai-status')?.textContent === 'Sending…'",
                    700,
                ),
            )
            await page.unroute("**/api/ai/ask", slow)
            check(
                "then 'Waiting for' the computer or 'Thinking…'",
                await until(
                    page, "/Waiting for E2E laptop|Thinking/.test(document.querySelector('.ai-status')?.textContent)"
                ),
            )
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
                await until(page, "document.querySelector('.ai-tools')?.textContent.includes('get paper')"),
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
            check(
                "model and effort reach the companion",
                (comp.jobs[0].get("model"), comp.jobs[0].get("effort")) == ("opus", "high"),
                (comp.jobs[0].get("model"), comp.jobs[0].get("effort")),
            )
            usage = await page.inner_text(".ai-usage-line")
            check(
                "the usage of the answer is shown", "1.2k in" in usage and "56 out" in usage and "$0.01" in usage, usage
            )
            await shot(page, "3-answer")
            await page.emulate_media(color_scheme="dark")
            await page.wait_for_timeout(400)
            await shot(page, "3-answer-dark")
            await page.emulate_media(color_scheme="light")

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

            # ---- earlier conversations: list, search, rename, continue
            await page.click("[data-history]")
            await page.wait_for_selector(".ai-conv", timeout=5000)
            check(
                "history lists the conversation with its title and agent",
                "STREAM please" in await page.inner_text(".ai-conv-title")
                and "Claude" in await page.inner_text(".ai-conv-meta"),
                await page.inner_text(".ai-conv"),
            )
            await shot(page, "5-history")
            await page.fill(".ai-search input", "zzzz")
            check("searching narrows the list", await until(page, "document.querySelectorAll('.ai-conv').length === 0"))
            await page.fill(".ai-search input", "")
            await page.wait_for_selector(".ai-conv")
            await page.hover(".ai-conv")
            await page.click("[data-rename]")
            await page.fill(".ai-rename", "My chat")
            await page.keyboard.press("Enter")
            check(
                "a conversation can be renamed",
                await until(page, "document.querySelector('.ai-conv-title')?.textContent === 'My chat'"),
            )
            titles = [
                c["title"] for c in requests.get(f"{BASE}/api/ai/conversations", timeout=10).json()["conversations"]
            ]
            check("the new title is stored", "My chat" in titles, titles)
            await page.click(".ai-conv-main")
            await page.wait_for_selector(".ai-thread:not([hidden])")
            check("continuing shows the earlier messages", await page.locator(".ai-turn").count() == 3)
            check("with its title", await page.inner_text(".ai-title-text") == "My chat")
            check("and the agent locked", await page.locator("[data-agent]").is_hidden())
            await page.fill(".ai-input", "FOLLOW again")
            await page.keyboard.press("Enter")
            check(
                "a continued conversation is answered in the same conversation",
                await until(
                    page,
                    "document.querySelectorAll('.ai-turn').length === 4 && document.querySelector('.ai-turn:last-child .ai-answer')?.textContent.includes('Follow-up answer')",
                    12000,
                )
                and comp.jobs[-1].get("conversation") == first
                and comp.jobs[-1].get("resume") is True,
            )

            # ---- leave an answer running, start another conversation, come back to the running one
            await page.click("[data-new]")
            await page.fill(".ai-input", "SLOW second")
            await page.keyboard.press("Enter")
            check(
                "a long answer in a new conversation starts",
                await until(page, "document.querySelector('.ai-answer')?.textContent.includes('Starting')"),
            )
            await page.click("[data-new]")
            check("a new conversation shows the friendly start", await page.locator(".ai-empty .ai-idea").count() >= 2)
            await page.click("[data-history]")
            await page.wait_for_selector(".ai-conv")
            check(
                "history marks the running one",
                await until(
                    page,
                    "[...document.querySelectorAll('.ai-conv-meta')].some(m => m.textContent.includes('answering'))",
                ),
            )
            await page.locator(".ai-conv", has_text="SLOW second").locator(".ai-conv-main").click()
            check(
                "opening it re-attaches to the answer",
                await until(
                    page,
                    "document.querySelector('.ai-turn:last-child .ai-answer')?.textContent.includes('Starting') && document.querySelector('[data-stop]') && !document.querySelector('[data-stop]').hidden",
                ),
            )
            await page.click("[data-stop]")
            check(
                "which can still be stopped",
                await until(
                    page, "document.querySelector('.ai-turn:last-child .ai-status')?.textContent === 'Stopped.'"
                ),
            )

            # ---- delete (with a confirmation)
            await page.click("[data-new]")
            await page.fill(".ai-input", "FOLLOW doomed")
            await page.keyboard.press("Enter")
            await until(page, "document.querySelector('.ai-answer')?.textContent.includes('Follow-up answer')", 12000)
            await page.click("[data-new]")
            await page.click("[data-history]")
            await page.wait_for_selector(".ai-conv")
            n = await page.locator(".ai-conv").count()
            doomed = page.locator(".ai-conv", has_text="FOLLOW doomed")
            await doomed.hover()
            await doomed.locator("[data-delete]").click()
            check("deleting asks first", await page.locator("[data-yes]").is_visible())
            await page.click("[data-no]")
            check("Keep leaves it", await page.locator(".ai-conv").count() == n)
            await doomed.hover()
            await doomed.locator("[data-delete]").click()
            await page.click("[data-yes]")
            check("Delete removes it", await until(page, f"document.querySelectorAll('.ai-conv').length === {n - 1}"))
            await page.keyboard.press("Escape")
            check(
                "Escape leaves the history, not the panel",
                await page.locator(".ai-panel").count() == 1 and await page.locator(".ai-history").is_hidden(),
            )

            # ---- a sent question draws its citations and note links like an answer does
            PROVIDERS.append(SECOND)  # the companion reports it with its next poll
            await page.click("[data-new]")
            await page.fill(".ai-input", f"FOLLOW about [@{key}, p. 2] and [[Review]] <b>raw</b>")
            await page.keyboard.press("Enter")
            check(
                "a cited key in the sent question is a citation chip, a [[note]] a link",
                await until(
                    page,
                    "document.querySelectorAll('.ai-q a.cite').length === 1 && !!document.querySelector('.ai-q a.wikilink')",
                )
                and "Doe" in await page.inner_text(".ai-q a.cite")
                and "Review" in await page.inner_text(".ai-q a.wikilink"),
            )
            await until(
                page,
                "document.querySelector('.ai-turn:last-child .ai-answer')?.textContent.includes('Follow-up answer')",
                12000,
            )
            check(
                "raw HTML in the question stays text; the companion got the question as typed",
                await page.locator(".ai-q b").count() == 0
                and f"[@{key}, p. 2] and [[Review]] <b>raw</b>" in comp.jobs[-1]["prompt"],
            )
            await shot(page, "2f-question-with-cites")
            await page.click(".ai-q a.wikilink")
            await page.wait_for_timeout(600)
            check(
                "clicking the note link opens that note",
                await page.locator(".note-head").is_visible() and "Review" in await page.inner_text(".note-head"),
            )

            # ---- Send never wraps, however long a label is: the pickers shrink instead
            await page.click("[data-new]")
            await page.click("[data-model]")
            await page.locator(".menu .menu-item", has_text="context window").click()
            geo = await one_line(page)
            check(
                "popup: a very long model label truncates; Send stays on the first line, at the right",
                geo["sameRow"] and geo["inside"] and geo["rowH"] < 40 and geo["clipped"] and geo["noScroll"],
                geo,
            )
            check(
                "the full label is in the tooltip and the aria-label",
                "context window" in await page.locator("[data-model]").get_attribute("title")
                and "context window" in await page.locator("[data-model]").get_attribute("aria-label"),
            )
            await shot(page, "2e-long-label")

            # ---- two agents: the agent picker appears, its choice reaches the companion, a conversation locks it
            for _ in range(24):
                if await page.locator("[data-agent]").is_visible():
                    break
                await page.wait_for_timeout(500)
                await page.click("[data-new]")
            check("a second agent makes the agent picker appear", await page.locator("[data-agent]").is_visible())
            await page.click("[data-agent]")
            names = [t.replace("✓", "").strip() for t in await page.locator(".menu .menu-item").all_inner_texts()]
            check("the agent menu lists both agents", names == ["Claude", "Codex"], names)
            await page.locator(".menu .menu-item", has_text="Codex").click()
            check(
                "choosing an agent shows its own models and drops the other's",
                await page.locator("[data-agent] .ai-pick-val").inner_text() == "Codex"
                and await page.locator("[data-model] .ai-pick-val").inner_text() == "Default",
            )
            await page.click("[data-model]")
            await page.locator(".menu .menu-item", has_text="GPT-Y").click()
            await page.click("[data-effort]")
            await page.locator(".menu .menu-item", has_text="Minimal").click()
            await page.fill(".ai-input", "FOLLOW with the other agent")
            await page.keyboard.press("Enter")
            await until(
                page,
                "document.querySelector('.ai-turn:last-child .ai-answer')?.textContent.includes('Follow-up answer')",
                12000,
            )
            last = comp.jobs[-1]
            check(
                "agent, model and effort reach the companion",
                (last.get("provider"), last.get("model"), last.get("effort")) == ("codex", "gpt-y", "minimal"),
                (last.get("provider"), last.get("model"), last.get("effort")),
            )
            check(
                "inside the conversation the agent cannot change, and the tooltip says why",
                await page.locator("[data-agent]").get_attribute("aria-disabled") == "true"
                and "cannot change" in await page.locator("[data-agent]").get_attribute("title"),
            )
            await page.click("[data-agent]", force=True)  # aria-disabled: Playwright would wait
            check("and its menu stays shut", await page.locator(".menu").count() == 0)
            await shot(page, "2g-locked-agent")
            await page.click("[data-new]")
            check(
                "a new conversation remembers the last choice",
                await page.locator("[data-agent] .ai-pick-val").inner_text() == "Codex"
                and await page.locator("[data-model] .ai-pick-val").inner_text() == "GPT-Y",
            )
            await page.click("[data-agent]")
            await page.locator(".menu .menu-item", has_text="Claude").click()
            await page.click("[data-new]")

            # ---- the sidebar: it takes the place of the details panel
            await page.click("[data-place]")
            await page.wait_for_timeout(300)
            geo = await page.evaluate(
                """() => { const p = document.querySelector('.ai-panel').getBoundingClientRect();
                  const m = document.querySelector('.notes-main').getBoundingClientRect();
                  const d = document.querySelector('.notes-side .ns-details');
                  return { place: document.querySelector('.ai-panel').dataset.placement, left: p.left, right: p.right, w: p.width,
                    mainRight: m.right, details: d ? getComputedStyle(d).display : null, inSide: !!document.querySelector('.notes-side > .ai-panel'),
                    top: p.top, bottom: p.bottom }; }"""
            )
            check(
                "docked, the panel sits in the details panel's slot and the note is not covered",
                geo["place"] == "dock"
                and geo["inSide"]
                and geo["details"] == "none"
                and geo["mainRight"] <= geo["left"] + 1
                and geo["right"] <= 1280,
                geo,
            )
            await page.click("[data-model]")
            await page.locator(".menu .menu-item", has_text="context window").click()
            geo = await one_line(page)
            check(
                "docked: Send still on the first line with a very long label",
                geo["sameRow"] and geo["inside"] and geo["rowH"] < 40 and geo["clipped"],
                geo,
            )
            await page.fill(".ai-input", "compare [@")
            await until(page, "!document.querySelector('.ai-mentions')?.hidden")
            check(
                "docked: the list opens above the box, inside the panel",
                await page.evaluate(
                    """() => { const l = document.querySelector('.ai-mentions').getBoundingClientRect(), p = document.querySelector('.ai-panel').getBoundingClientRect(),
                      i = document.querySelector('.ai-input').getBoundingClientRect();
                      return l.bottom <= i.top && l.left >= p.left - 1 && l.right <= p.right + 1 && l.top >= p.top; }"""
                ),
            )
            await shot(page, "6-dock")
            await page.emulate_media(color_scheme="dark")
            await page.wait_for_timeout(400)
            await shot(page, "6-dock-dark")
            await page.emulate_media(color_scheme="light")
            await page.keyboard.press("Escape")
            await page.fill(".ai-input", "")
            await page.reload()
            await page.wait_for_selector(".cm-content", timeout=15000)
            await page.wait_for_selector('[data-act="ask-ai"]:not([hidden])')
            await page.click('[data-act="ask-ai"]')
            check(
                "the placement is remembered",
                await page.locator(".ai-panel").get_attribute("data-placement") == "dock",
            )
            await page.click("[data-place]")
            check(
                "moving it back restores the details panel",
                await page.locator(".ai-panel").get_attribute("data-placement") == "popup"
                and await page.locator(".notes-side .ns-details").is_visible(),
            )

            await page.click("[data-history]")
            await page.locator(".ai-conv", has_text="My chat").locator(".ai-conv-main").click()
            await page.wait_for_selector(".ai-answer a.cite")
            await page.click(".ai-answer a.cite")
            check(
                "clicking the citation opens the PDF",
                await until(page, "window.zcTabs && window.zcTabs.list().some(t => t.kind === 'pdf')"),
            )
            await until(page, "window.zcTabs.list().some(t => t.kind === 'pdf' && t.active)")
            await page.click("#tabbar >> text=Review")  # back to the note's tab (tabs/tabs.ts)
            await page.wait_for_timeout(300)

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
            # citing from the viewer: the library data is fetched on first use, with plain requests
            await v.click(".ai-input")
            await v.keyboard.type("compare [@")
            check(
                "the viewer lists library items after [@",
                await until(v, "document.querySelectorAll('.ai-mentions .ai-mention').length === 1")
                and key in await v.inner_text(".ai-mention-key"),
            )
            check(
                "in the app's menu look: the list is styled in the viewer too",
                await v.evaluate("getComputedStyle(document.querySelector('.ai-mentions')).boxShadow !== 'none'"),
            )
            await shot(v, "4b-viewer-mentions")
            await v.keyboard.press("Enter")
            await v.keyboard.type(" with [[Rev")
            check(
                "and notes after [[",
                await until(v, "document.querySelectorAll('.ai-mentions .ai-mention').length >= 1")
                and "Review" in await v.inner_text(".ai-mention-key"),
            )
            await v.keyboard.press("Enter")
            check("both are in the text", await v.input_value(".ai-input") == f"compare [@{key}] with [[Review]]")
            await v.keyboard.press("Enter")
            check(
                "the viewer shows them as a chip and a link in the sent question",
                await until(
                    v, "!!document.querySelector('.ai-q a.cite') && !!document.querySelector('.ai-q a.wikilink')"
                ),
            )
            await until(v, "!document.querySelector('.ai-turn.streaming')", 12000)
            check(
                "the companion got the question as typed",
                f"compare [@{key}] with [[Review]]" in comp.jobs[-1]["prompt"],
            )
            async with ctx.expect_page() as opened:
                await v.click(".ai-q a.wikilink")
            note_win = await opened.value
            check(
                "a [[note]] link opens the note in the app (a new window here)",
                "note=Review.md" in note_win.url,
                note_win.url,
            )
            await note_win.close()
            await v.keyboard.press("Escape")
            check("Escape closes the viewer's panel", await v.locator(".ai-panel").count() == 0)

            # ---- the viewer without a selection, and docked as a column
            await v.evaluate("getSelection().removeAllRanges()")
            await v.wait_for_selector("#zc-ask-btn:not([hidden])", timeout=5000)
            check("the viewer toolbar has an Ask AI button", True)
            await v.click("#zc-ask-btn")
            await v.wait_for_selector(".ai-panel", timeout=5000)
            chip = await v.inner_text(".ai-chip-text")
            check(
                "it opens without a selection, about the page in view", re.fullmatch(r"p\. \d+", chip) is not None, chip
            )
            check("as a popup first", await v.locator(".ai-panel").get_attribute("data-placement") == "popup")
            await shot(v, "7-viewer-popup")
            before = await v.evaluate("document.getElementById('pdf-container').getBoundingClientRect().width")
            await v.click("[data-place]")
            await v.wait_for_timeout(300)
            geo = await v.evaluate(
                """() => { const c = document.getElementById('pdf-container').getBoundingClientRect();
                  const p = document.querySelector('.ai-panel').getBoundingClientRect();
                  return { w: c.width, right: c.right, left: p.left, pw: p.width, ph: p.height, vh: innerHeight,
                    inMain: !!document.querySelector('#main-content > .ai-panel') }; }"""
            )
            check(
                "docked, the viewer makes room: a column beside the PDF, no overlay",
                geo["inMain"] and geo["w"] < before - 300 and geo["right"] <= geo["left"] + 1 and geo["pw"] >= 300,
                (before, geo),
            )
            await shot(v, "7-viewer-dock")
            await v.emulate_media(color_scheme="dark")
            await v.wait_for_timeout(400)
            await shot(v, "7-viewer-dock-dark")
            await v.emulate_media(color_scheme="light")
            await v.click("[data-place]")
            check(
                "moving it back gives the PDF its width again",
                await v.evaluate("document.getElementById('pdf-container').getBoundingClientRect().width") == before,
            )
            await v.keyboard.press("Escape")

            # ---- the viewer in an app tab: the same panel inside the frame
            tabbed = await ctx.new_page()
            await tabbed.goto(f"{BASE}/#note=Review.md")
            await tabbed.wait_for_selector(".cm-content", timeout=15000)
            viewer_url = (
                f"/viewer.html?file=/pdfs/{att['url']}&paper_id={item['id']}&title=Open%20systems&att={att['id']}"
            )
            await tabbed.evaluate(f"location.hash = '#pdf=' + encodeURIComponent({viewer_url!r})")
            frame = None
            for _ in range(60):
                frame = next((f for f in tabbed.frames if "/viewer.html" in f.url), None)
                if frame:
                    break
                await tabbed.wait_for_timeout(250)
            check("the PDF opens in an app tab", frame is not None)
            if frame:
                await frame.wait_for_selector("#zc-ask-btn:not([hidden])", timeout=10000)
                await frame.click("#zc-ask-btn")
                await frame.wait_for_selector(".ai-panel", timeout=5000)
                await frame.click("[data-place]")
                await frame.wait_for_timeout(300)
                check(
                    "docked inside the tab's frame, as a column",
                    await frame.evaluate("!!document.querySelector('#main-content > .ai-panel')"),
                )
                await frame.click(".ai-input")
                await tabbed.keyboard.type("see [[Rev")
                check(
                    "in an app tab's frame the notes are listed too",
                    await until(frame, "document.querySelectorAll('.ai-mentions .ai-mention').length >= 1"),
                )
                await tabbed.keyboard.press("Tab")
                await tabbed.keyboard.press("Enter")
                await until(frame, "!!document.querySelector('.ai-q a.wikilink')", 8000)
                await until(frame, "!document.querySelector('.ai-turn.streaming')", 12000)
                await shot(tabbed, "7-viewer-in-tab")
                await frame.click(".ai-q a.wikilink")
                check(
                    "its [[note]] link opens the note in the app around it",
                    await until(tabbed, "window.zcTabs.list().find(t => t.active)?.kind !== 'pdf'", 6000)
                    and await tabbed.locator(".note-head").is_visible(),
                    await tabbed.evaluate("window.zcTabs.list().map(t => t.kind + ':' + t.active)"),
                )

            # ---- the phone layout keeps working
            m = await b.new_context(viewport={"width": 390, "height": 760}, has_touch=True, is_mobile=True)
            ph = await m.new_page()
            ph.on("pageerror", lambda e: errors.append(str(e)))
            await ph.goto(BASE)
            await ph.wait_for_timeout(1500)
            check("the phone layout loads", await ph.locator("body").count() == 1)
            # a narrow window: the panel is a bottom sheet, never a column
            narrow = await b.new_context(viewport={"width": 390, "height": 760}, has_touch=True)
            nw = await narrow.new_page()
            await nw.goto(f"{BASE}/viewer.html?file=/pdfs/{att['url']}&paper_id={item['id']}&title=x&att={att['id']}")
            await nw.wait_for_selector("#zc-ask-btn:not([hidden])", timeout=15000)
            await nw.click("#zc-ask-btn")
            await nw.wait_for_selector(".ai-panel")
            await nw.wait_for_timeout(500)  # the entrance animation
            sheet = await nw.evaluate(
                """() => { const r = document.querySelector('.ai-panel').getBoundingClientRect();
                  return { l: r.left, r: r.right, b: r.bottom, w: innerWidth, h: innerHeight, place: document.querySelector('.ai-panel').dataset.placement,
                    toggle: getComputedStyle(document.querySelector('[data-place]')).display }; }"""
            )
            check(
                "on a narrow screen the popup is a full-width bottom sheet without a dock button",
                sheet["place"] == "popup"
                and sheet["l"] <= 1
                and sheet["r"] >= sheet["w"] - 1
                and sheet["b"] >= sheet["h"] - 1
                and sheet["toggle"] == "none",
                sheet,
            )
            await nw.click("[data-model]")
            await nw.locator(".menu .menu-item", has_text="context window").click()
            geo = await one_line(nw)
            check(
                "phone width: Send stays on the first line at the right with a very long label",
                geo["sameRow"] and geo["inside"] and geo["rowH"] < 50 and geo["clipped"] and geo["noScroll"],
                geo,
            )
            check(
                "the touch targets are at least 36px",
                await nw.evaluate(
                    "[...document.querySelectorAll('.ai-controls button:not([hidden])')].every(b => b.getBoundingClientRect().height >= 36 && b.getBoundingClientRect().width >= 34)"
                ),
            )
            await nw.click(".ai-input")
            await nw.keyboard.type("[@")
            await until(nw, "!document.querySelector('.ai-mentions')?.hidden")
            await shot(nw, "8-phone-mentions")
            await nw.fill(".ai-input", "")
            await nw.click("[data-effort]")
            await nw.wait_for_timeout(300)
            await shot(nw, "8-phone-effort-menu")
            await nw.keyboard.press("Escape")
            await shot(nw, "8-phone-sheet")
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
