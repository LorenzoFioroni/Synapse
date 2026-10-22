"""Browser checklist for autocompletion in notes (ghost text from a local model): the safeguards
when Ollama or the model is missing, Settings → Autocompletion, and the ghost text itself
(Tab, Esc, typing through it, word by word, Alt+\\, clicking it, undo, where it never appears).

    python tests/e2e/assist_checklist.py

Starts its own server on port 8793 with a throw-away data folder, and a stand-in for Ollama
(tests/fake_ollama.py) instead of a real model; your library is not touched.
"""

import asyncio
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
from tests.fake_ollama import FakeOllama, free_port  # noqa: E402

PORT = 8793
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-assist-e2e-")
MOD = "Meta" if sys.platform == "darwin" else "Control"
results = []

NOTE = "# Plan\n\nIntro line.\n\nA [[other]] link here.\n\n```python\nx = 1\n```\n"


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


def reply(payload):
    prompt = payload["prompt"]
    if prompt.endswith("The quick"):
        return " brown fox jumps"
    if prompt.endswith("Explicit:"):
        return " asked for"
    if prompt.endswith("Energy $E = "):
        return "mc^2"
    return " world"


async def wait_for(page, js, t=6000):
    end = time.time() + t / 1000
    while time.time() < end:
        if await page.evaluate(js):
            return True
        await page.wait_for_timeout(100)
    return False


GHOST = "(document.querySelector('.cm-ghost') || {}).textContent || ''"


async def ghost(page):
    return await page.evaluate(GHOST)


async def to_end(page):
    await page.locator(".cm-line").last.click()
    await page.keyboard.press(f"{MOD}+End")


async def main():
    guard(PORT)
    dead = f"http://127.0.0.1:{free_port()}"
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", DATA],
        cwd=APP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    fake = FakeOllama(models=("llama3.2:3b",), reply=reply).__enter__()
    try:
        for _ in range(80):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                break
            except requests.RequestException:
                time.sleep(0.25)
        s = requests.Session()
        s.headers["Origin"] = BASE
        s.post(BASE + "/api/notes/note", json={"path": "plan.md", "title": "Plan", "text": NOTE}).raise_for_status()
        s.post(
            BASE + "/api/notes/note", json={"path": "other.md", "title": "Other", "text": "# Other\n"}
        ).raise_for_status()
        # the plugin on, but no Ollama at the address
        s.post(BASE + "/api/plugin-host/autocompletion/enable").raise_for_status()
        s.put(BASE + "/api/plugin-host/autocompletion/settings", json={"url": dead}).raise_for_status()

        async with async_playwright() as pw:
            b = await pw.chromium.launch()
            page = await b.new_page(viewport={"width": 1400, "height": 1000})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            completes = []
            page.on(
                "request",
                lambda r: (
                    completes.append(r.post_data) if r.url.endswith("/api/plugins/autocompletion/complete") else None
                ),
            )

            await page.goto(BASE + "/")
            await page.wait_for_timeout(800)
            await page.click('[data-act="open-notes"]')
            await page.click('.nt-row.note[data-path="plan.md"]')
            await page.wait_for_selector(".cm-content")
            await page.wait_for_timeout(600)

            # ---- safeguard: no Ollama
            await to_end(page)
            await page.keyboard.type("The quick", delay=30)
            await page.wait_for_timeout(1200)
            await page.keyboard.type(" thing", delay=30)
            await page.wait_for_timeout(1200)
            notices = await page.evaluate(
                "[...document.querySelectorAll('.toast')].filter(t => t.textContent.includes('cannot make suggestions')).length"
            )
            check("Ollama missing: one notice says why", notices == 1, f"({notices})")
            check("Ollama missing: no suggestion is asked for", not completes, f"({len(completes)})")
            check("Ollama missing: no ghost text", await ghost(page) == "")

            # ---- Settings → Autocompletion
            await page.keyboard.press(f"{MOD}+,")
            await page.wait_for_selector("#set-x-autocompletion-ollama")
            await page.click('[data-nav="x-autocompletion-ollama"]')
            ok = await wait_for(page, "document.querySelector('[data-ai-state]').textContent.includes('not running')")
            steps = await page.evaluate("document.querySelector('.ai-steps')?.textContent || ''")
            check(
                "Settings: Ollama not running, with what to do", ok and "ollama.com" in steps and "ollama pull" in steps
            )
            await page.fill("[data-ai-url]", "http://192.168.1.5:11434")
            await page.press("[data-ai-url]", "Enter")
            await page.locator("[data-ai-url]").blur()
            refused = await wait_for(
                page, "document.querySelector('[data-ai-state]').textContent.includes('only a model on this computer')"
            )
            check("Settings: an address on another computer is refused", refused)

            await page.fill("[data-ai-url]", fake.url.replace("http://", ""))
            await page.locator("[data-ai-url]").blur()
            missing = await wait_for(
                page, "document.querySelector('[data-ai-state]').textContent.includes('not installed')"
            )
            installed = await page.evaluate("document.querySelector('[data-ai-installed]').textContent")
            check("Settings: model not installed, installed models listed", missing and "llama3.2:3b" in installed)
            values = next(p for p in s.get(BASE + "/api/plugin-host").json()["plugins"] if p["id"] == "autocompletion")[
                "values"
            ]
            check(
                "Settings: the address is the plugin's setting",
                values["url"] == fake.url.replace("http://", ""),
                values["url"],
            )

            fake.models.append("qwen2.5-coder:1.5b")
            await page.click("[data-ai-check]")
            ready = await wait_for(page, "document.querySelector('[data-ai-state] .ai-state.ok') !== null")
            check("Settings: Check Again finds the model (ready)", ready)
            check("Settings: no generation was asked by the checks", fake.generated() == [])
            await page.click("[data-close]")
            await page.wait_for_timeout(400)

            # ---- ghost text
            await to_end(page)
            await page.keyboard.press("Enter")
            await page.keyboard.press("Enter")
            n0 = len(fake.generated())
            await page.keyboard.type("The quick", delay=40)
            shown = await wait_for(page, f"{GHOST} === ' brown fox jumps'")
            check("Ghost text appears after a pause", shown, repr(await ghost(page)))
            check(
                "One request for a burst of typing", len(fake.generated()) - n0 == 1, f"({len(fake.generated()) - n0})"
            )
            sent = fake.generated()[-1]
            check(
                "The model sees the text before and after the cursor",
                sent["prompt"].endswith("The quick") and "suffix" in sent,
            )
            text = await page.evaluate("window.zcNotes.text()")
            check("Ghost text is not in the note", "brown" not in text)

            await page.keyboard.type(" br", delay=40)
            await page.wait_for_timeout(500)
            check("Typing its characters keeps the rest", await ghost(page) == "own fox jumps", repr(await ghost(page)))
            await page.keyboard.press(f"{MOD}+ArrowRight")
            await page.wait_for_timeout(150)
            text = await page.evaluate("window.zcNotes.text()")
            check(
                "Cmd/Ctrl+→ takes one word",
                text.rstrip().endswith("The quick brown") and await ghost(page) == " fox jumps",
            )
            await page.keyboard.press("Tab")
            await page.wait_for_timeout(150)
            text = await page.evaluate("window.zcNotes.text()")
            check("Tab accepts the rest", text.rstrip().endswith("The quick brown fox jumps"), repr(text[-40:]))
            await page.keyboard.press(f"{MOD}+z")
            await page.wait_for_timeout(150)
            text = await page.evaluate("window.zcNotes.text()")
            check(
                "Undo takes the accepted suggestion back", text.rstrip().endswith("The quick brown"), repr(text[-40:])
            )

            await page.keyboard.press("Enter")
            await page.keyboard.type("Next thing", delay=40)
            await wait_for(page, f"{GHOST} === ' world'")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(150)
            text = await page.evaluate("window.zcNotes.text()")
            check("Esc dismisses it", await ghost(page) == "" and text.rstrip().endswith("Next thing"))
            await page.keyboard.press("Tab")
            await page.wait_for_timeout(150)
            text = await page.evaluate("window.zcNotes.text()")
            check("Without a suggestion Tab still indents", "Next thing" in text and "world" not in text)
            await page.keyboard.press(f"{MOD}+z")

            await page.keyboard.press("Enter")
            await page.keyboard.type("Energy $E = $", delay=40)
            await page.keyboard.press("ArrowLeft")
            await page.keyboard.press("Space")
            await page.keyboard.press("Backspace")
            await page.keyboard.press("Alt+Backslash")
            got_math = await wait_for(page, f"{GHOST} === 'mc^2'")
            check("Before a closing $ a suggestion still appears", got_math, repr(await ghost(page)))
            await page.click(".cm-ghost")
            await page.wait_for_timeout(150)
            text = await page.evaluate("window.zcNotes.text()")
            check("Clicking the ghost text accepts it", "$E = mc^2$" in text, repr(text[-30:]))

            await page.keyboard.press("End")
            await page.keyboard.press("Enter")
            await page.keyboard.type("Explicit:", delay=20)
            await page.wait_for_timeout(100)
            await page.keyboard.press("Escape")
            await page.keyboard.press("Alt+Backslash")
            asked = await wait_for(page, f"{GHOST} === ' asked for'", 3000)
            check("Alt+\\ asks for a suggestion at once", asked)
            await page.keyboard.press("Escape")

            # ---- where it never appears
            n = len(fake.generated())
            await page.click(".cm-line:has-text('Intro line.')")
            await page.keyboard.press("Home")
            await page.keyboard.type("So ", delay=40)
            await page.wait_for_timeout(900)
            check("Not in the middle of a line", len(fake.generated()) == n and await ghost(page) == "")
            # in Source mode: in Live, clicking into a code block shows its fences and moves its lines
            await page.click('[data-mode="source"]')
            await page.click(".cm-line:has-text('x = 1')")
            await page.keyboard.press("End")
            await page.keyboard.type(" + 2", delay=40)
            await page.wait_for_timeout(900)
            text = await page.evaluate("window.zcNotes.text()")
            check(
                "Not in a code block",
                "x = 1 + 2\n```" in text and len(fake.generated()) == n and await ghost(page) == "",
            )
            await page.click('[data-mode="live"]')
            await to_end(page)
            await page.keyboard.press("Enter")
            await page.keyboard.type("See [[oth", delay=40)
            await page.wait_for_timeout(900)
            listed = await page.evaluate("!!document.querySelector('.cm-tooltip-autocomplete')")
            check("Not while the link list is open", listed and await ghost(page) == "", f"list={listed} ")
            await page.keyboard.press("Escape")

            # ---- Ollama goes away while writing
            fake.models.clear()
            await page.keyboard.press("Enter")
            await page.keyboard.type("Later on we", delay=40)
            await page.wait_for_timeout(1200)
            n = len(fake.generated())
            await page.keyboard.type(" add more", delay=40)
            await page.wait_for_timeout(1200)
            check("Model removed: requests stop", len(fake.generated()) == n and await ghost(page) == "")

            # ---- off
            s.post(BASE + "/api/plugin-host/autocompletion/disable").raise_for_status()
            fake.models.append("qwen2.5-coder:1.5b")
            await page.reload()
            await page.wait_for_selector(".cm-content")
            await page.wait_for_timeout(600)
            completes.clear()
            await to_end(page)
            await page.keyboard.type(" The quick", delay=40)
            await page.wait_for_timeout(1200)
            check("Off: nothing is asked for", not completes and await ghost(page) == "")

            path = await page.evaluate("window.zcNotes.path()")
            check("All of it happened in the one note", path == "plan.md", path)
            # the refused address answers 400 on purpose
            js_errors = [e for e in errors if "favicon" not in e and "status of 400" not in e]
            check("No JavaScript errors", not js_errors, str(js_errors[:3]))
            await b.close()
    finally:
        fake.__exit__(None, None, None)
        srv.terminate()
        srv.wait()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
