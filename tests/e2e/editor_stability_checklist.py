"""Plain-text typing in a rich note must stay put across autosave and preview refreshes.

    python tests/e2e/run.py editor_stability

Uses a throw-away library; no real notes or local model are needed.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent))
from _guard import guard  # noqa: E402

APP = Path(__file__).resolve().parents[2]
PORT = 8797
BASE = f"http://127.0.0.1:{PORT}"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra, flush=True)


def seed():
    item = requests.post(
        BASE + "/api/items", json={"authors": "Writer, Alice", "year": 2026, "title": "A model"}
    ).json()
    img = requests.post(
        BASE + "/api/notes/attachments",
        files={"file": ("plot.png", (APP / "tests/e2e/fixtures/wide.png").read_bytes(), "image/png")},
    ).json()["markdown"]
    caption = f"*Caption [@{item['citation_key']}]*"
    text = ""
    for i in range(35):
        text += f"## Section {i}\n\n" + "Normal text in a complex document. " * 4 + f" Target {i}.\n\n"
        text += f"```gallery\n{img}\n\n{caption}\n```\n\n"
        text += "$$\n\\frac{a}{b} = c \\label{eq:" + str(i) + "}\n$$\n\n"
        text += "> [!note] A callout\n> Ordinary text with *italic* and $x^2$.\n\n"
    requests.post(BASE + "/api/notes/note", json={"path": "Rich.md", "title": "Rich", "text": text}).raise_for_status()
    callout = "> [!note] Callout\n> Plain *italic* **bold** ***both***.\n>\n> > Nested *inner*.\n\nOutside *outside*.\n"
    requests.post(
        BASE + "/api/notes/note", json={"path": "Callout.md", "title": "Callout", "text": callout}
    ).raise_for_status()
    return item


async def main():
    guard(PORT)
    data = tempfile.mkdtemp(prefix="synapse-editor-stability-")
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser"],
        cwd=APP,
        env=dict(os.environ, SYNAPSE_DATA_DIR=data),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                if requests.get(BASE + "/api/settings", timeout=1).ok:
                    break
            except requests.ConnectionError:
                pass
            await asyncio.sleep(0.1)
        item = seed()
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
            await page.goto(BASE + "/#note=Rich.md")
            await page.wait_for_selector(".cm-content")
            await page.wait_for_timeout(1000)
            await page.evaluate("""async () => {
                window.ed = (await import('/js/views/notes/workspace.js')).ws.editor;
                const cm = await import('/vendor/cm6/cm6.js');
                const at = ed.text().indexOf('Target 22');
                ed.view.dispatch({selection: {anchor: at}, effects: cm.EditorView.scrollIntoView(at, {y: 'center'})});
                ed.focus();
            }""")
            await page.wait_for_timeout(1000)
            await page.evaluate("""() => {
                window.kept = [...document.querySelectorAll('.cm-lp-gallery')];
                window.samples = [];
                window.recording = true;
                const frame = () => {
                    if (!recording) return;
                    const v = ed.view, r = v.coordsAtPos(v.state.selection.main.head);
                    samples.push({y: r?.top, scroll: v.scrollDOM.scrollTop});
                    requestAnimationFrame(frame);
                };
                requestAnimationFrame(frame);
            }""")
            # Plain words only: the edited paragraph contains no expanding Markdown block.
            for _ in range(3):
                await page.keyboard.type(" writing normal words ", delay=20)
                await page.wait_for_timeout(2300)
            stats = await page.evaluate("""() => {
                recording = false;
                return {frames: samples.length,
                    jump: Math.max(...samples.slice(1).map((s, i) => Math.abs(s.y - samples[i].y))),
                    scroll: Math.max(...samples.map(s => s.scroll)) - Math.min(...samples.map(s => s.scroll)),
                    kept: kept.length > 0 && kept.every(el => el.isConnected)};
            }""")
            check(
                "plain typing and autosave keep the viewport steady",
                stats["frames"] > 30 and stats["scroll"] <= 2,
                stats,
            )
            check("cursor moves at most one wrapped text row per frame", stats["jump"] <= 30, stats["jump"])
            check("unchanged galleries retain their loaded DOM across refreshes", stats["kept"])

            # A genuine data change must still redraw the caption, even after several saves.
            requests.patch(BASE + f"/api/items/{item['id']}", json={"year": 2027}).raise_for_status()
            await page.keyboard.type(" updated ")  # the next save reloads this note's resolved citations
            await page.wait_for_function(
                "[...document.querySelectorAll('.cm-lp-gallery .cite')].some(el => el.textContent.includes('2027'))"
            )
            check("changed citation data refreshes gallery captions", True)

            await page.click('.nt-row.note[data-path="Callout.md"]')
            await page.wait_for_function("zcNotes.path() === 'Callout.md'")
            await page.wait_for_timeout(300)
            styles = await page.locator(".cm-content .cmt-em").evaluate_all(
                "els => els.map(el => [el.textContent, getComputedStyle(el).fontStyle])"
            )
            check(
                "callouts, nested quotes and ordinary text retain explicit italics",
                len(styles) >= 4 and all(s[1] == "italic" for s in styles),
                styles,
            )
            plain = page.locator(".cm-lp-callout .cmt-quote", has_text="Plain").first
            check(
                "plain callout text stays upright",
                await plain.evaluate("el => getComputedStyle(el).fontStyle") == "normal",
            )
            both = page.locator(".cm-lp-callout .cmt-em.cmt-strong").first
            check(
                "combined bold and italic keeps both styles",
                await both.evaluate(
                    "el => getComputedStyle(el).fontStyle === 'italic' && Number(getComputedStyle(el).fontWeight) >= 600"
                ),
            )
            await page.click('[data-mode="read"]')
            await page.wait_for_selector(".note-read:not([hidden]) em")
            check(
                "read mode keeps the same explicit italics",
                await page.locator(".note-read em").evaluate_all(
                    "els => els.length >= 4 && els.every(el => getComputedStyle(el).fontStyle === 'italic')"
                ),
            )
            check("no browser errors or unstable viewport warnings", not errors, errors[:5])
            await browser.close()
    finally:
        srv.terminate()
        srv.wait()
        shutil.rmtree(data, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
