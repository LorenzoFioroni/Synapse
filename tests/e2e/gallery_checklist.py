"""Browser checklist for galleries (```gallery blocks): the carousel in read mode and in the live
preview (arrows, dots, keys, swipe-free scrolling), editing its source with the carousel below,
autocomplete in captions, citations and images in galleries counted by the backend, and the
exports: one figure with panels (a), (b), … and one caption in HTML/PDF, images on a line in Markdown.

    python tests/e2e/gallery_checklist.py               (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8794 with a throw-away data folder; your library is not touched.
"""

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from backend.pdf.png import png_encode  # noqa: E402
from tests.e2e._guard import guard  # noqa: E402

PORT = 8794
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-gallery-e2e-")
SHOTS = os.environ.get("SHOTS")
results = []
LINE = "(() => { const t = zcNotes.text(), h = zcNotes.cursor(); return t.slice(t.lastIndexOf('\\n', h - 1) + 1, (t.indexOf('\\n', h) + 1 || t.length + 1) - 1); })()"


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


def image(rgb, w, h):
    return png_encode(bytes(rgb) * (w * h), w, h)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


def seed(s):
    s.post(
        BASE + "/api/items",
        json={
            "citation_key": "smith2021",
            "item_type": "journalArticle",
            "title": "Quantum things",
            "authors": "Smith, Anna",
            "year": 2021,
            "doi": "10.1103/PhysRevX.11.021001",
        },
    ).raise_for_status()
    paths = []
    for name, rgb, w, h in (
        ("red", (220, 60, 60), 480, 300),
        ("green", (60, 170, 90), 480, 360),
        ("blue", (60, 90, 220), 400, 300),
        ("grey", (150, 150, 150), 300, 300),
    ):
        r = s.post(BASE + "/api/notes/attachments", files={"file": (f"{name}.png", image(rgb, w, h), "image/png")})
        r.raise_for_status()
        paths.append(r.json()["path"])
    a, b, c, d = paths
    text = (
        "---\ntitle: Figures\n---\n# Figures\n\nSome text before.\n\n"
        f"```gallery|600\n![red]({a})\n*Clean system, $\\xi\\to\\infty$.*\n"
        f"![green]({b}) With speckle [@smith2021, p. 3]\n![|200]({c})\nNo period here\n\n"
        "Photon number versus $N$, see \\eqref{eq:n}\n```\n\n"
        "$$\nN = 1 \\label{eq:n}\n$$\n\n"
        f"```gallery\n![]({a}) One\n![]({b}) Two\n![]({c}) Three\n![]({d}) Four\n```\n\nThe end.\n"
    )
    s.post(BASE + "/api/notes/note", json={"path": "figures.md", "title": "Figures", "text": text}).raise_for_status()
    return paths


async def download(page, run):
    async with page.expect_download() as info:
        await run()
    d = await info.value
    return d.suggested_filename, Path(await d.path()).read_text()


async def current(page, scope):
    """Index of the panel a gallery shows, the active dot, and the arrows' state."""
    return await page.evaluate(
        """(scope) => { const g = document.querySelector(scope); const t = g.querySelector('.gallery-track');
          return { panel: Math.round(t.scrollLeft / t.clientWidth),
                   dot: [...g.querySelectorAll('.gallery-dot')].findIndex((d) => d.classList.contains('active')),
                   prev: g.querySelector('.gallery-prev').disabled, next: g.querySelector('.gallery-next').disabled }; }""",
        scope,
    )


async def main():
    guard(PORT)
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", DATA],
        cwd=APP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                break
            except requests.RequestException:
                time.sleep(0.25)
        s = requests.Session()
        s.headers["Origin"] = BASE
        paths = seed(s)

        # ---- the backend counts what is in a gallery
        links = s.get(BASE + "/api/notes/links", params={"path": "figures.md"}).json()
        out = links.get("outgoing", [])
        check(
            "a citation in a panel caption counts", any(l["kind"] == "cite" and l["target"] == "smith2021" for l in out)
        )
        images = {l["target"] for l in out if l["kind"] == "image"}
        check("gallery images count as used", set(paths) <= images, images)

        async with async_playwright() as pw:
            b = await pw.chromium.launch()
            ctx = await b.new_context(viewport={"width": 1400, "height": 1000}, accept_downloads=True)
            await ctx.add_init_script(
                "window.print = function () { try { window.top.__printed = document.documentElement.outerHTML; }"
                " catch (e) {} };"
            )
            page = await ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(BASE + "/")
            await page.click('.ws-switch [data-ws="notes"]')
            await page.click('.nt-row.note[data-path="figures.md"]')
            await page.wait_for_selector(".cm-content")
            await page.wait_for_timeout(600)

            # ---- read mode: a carousel
            await page.click('[data-mode="read"]')
            await page.wait_for_selector(".note-read:not([hidden]) .gallery")
            g = ".note-read .gallery >> nth=0"
            G = ".note-read .gallery"
            check(
                "two galleries, not code blocks",
                await page.locator(G).count() == 2 and await page.locator(".note-read .code-block").count() == 0,
            )
            check(
                "three panels, three dots",
                await page.locator(f"{G}:first-of-type .gallery-panel").count() == 3
                and await page.locator(".note-read .gallery").first.locator(".gallery-dot").count() == 3,
            )
            width = await page.locator(g).evaluate("(e) => e.getBoundingClientRect().width")
            check("the width set on the block", 590 <= width <= 600, width)
            labels = await page.locator(g).locator(".gallery-label").all_text_contents()
            check("panels labelled (a), (b), (c)", labels == ["(a)", "(b)", "(c)"], labels)
            first = page.locator(".note-read .gallery").first
            check(
                "captions render math and citations",
                await first.locator(".gallery-panel-caption .katex").count() >= 1
                and await first.locator(".gallery-panel-caption .cite").count() == 1,
            )
            cap = await first.locator(".gallery-caption").inner_text()
            check(
                "the gallery caption below, with its equation reference",
                "Photon number versus" in cap and "(1)" in cap,
                cap,
            )
            check("the italic caption is unwrapped", await first.locator(".gallery-panel-caption em").count() == 0)
            vis = await page.evaluate(
                """() => { const g = document.querySelector('.note-read .gallery'); const t = g.querySelector('.gallery-track');
                  const r = t.getBoundingClientRect();
                  return [...t.querySelectorAll('.gallery-panel')].map((p) => { const b = p.getBoundingClientRect();
                    return b.left >= r.left - 1 && b.right <= r.right + 1; }); }"""
            )
            check("one panel shows at a time", vis == [True, False, False], vis)
            st = await current(page, ".note-read .gallery")
            check(
                "starts on the first, Previous disabled", st == {"panel": 0, "dot": 0, "prev": True, "next": False}, st
            )
            await shot(page, "1-read")

            await first.hover()
            await first.locator(".gallery-next").click()
            await page.wait_for_timeout(700)
            st = await current(page, ".note-read .gallery")
            check("Next shows the second", st == {"panel": 1, "dot": 1, "prev": False, "next": False}, st)
            check("…and read mode stays", await page.locator(".note-read:not([hidden])").count() == 1)
            await first.locator(".gallery-dot").nth(2).click()
            await page.wait_for_timeout(700)
            st = await current(page, ".note-read .gallery")
            check(
                "a dot goes to its panel; Next disabled at the end",
                st == {"panel": 2, "dot": 2, "prev": False, "next": True},
                st,
            )
            await page.keyboard.press("ArrowLeft")
            await page.wait_for_timeout(700)
            st = await current(page, ".note-read .gallery")
            check("arrow keys on a focused dot", st["panel"] == 1 and st["dot"] == 1, st)
            # scrolled by other means (a swipe, a trackpad): the dots follow
            await page.evaluate(
                "(() => { const t = document.querySelector('.note-read .gallery .gallery-track'); t.scrollLeft = 0; })()"
            )
            await page.wait_for_timeout(400)
            st = await current(page, ".note-read .gallery")
            check("dots follow a swipe", st == {"panel": 0, "dot": 0, "prev": True, "next": False}, st)
            await shot(page, "2-read-turned")

            # ---- live preview: the carousel in place of the block
            await page.click('[data-mode="live"]')
            await page.wait_for_selector(".cm-lp-gallery .gallery")
            check(
                "live: drawn as a carousel",
                await page.locator(".cm-content .cm-lp-gallery .gallery").count() == 2
                and await page.locator(".cm-lp-gallerysrc").count() == 0,
            )
            lg = page.locator(".cm-content .cm-lp-gallery .gallery").first
            await lg.hover()
            await lg.locator(".gallery-next").click()
            await page.wait_for_timeout(700)
            st = await current(page, ".cm-content .cm-lp-gallery .gallery")
            check(
                "live: Next turns it without opening the source",
                st["panel"] == 1 and await page.locator(".cm-lp-gallerysrc").count() == 0,
                st,
            )
            await lg.locator(".gallery-panel").nth(1).locator("img").click()
            await page.wait_for_timeout(400)
            check(
                "live: a click on an image opens the source, the carousel below it",
                await page.locator(".cm-lp-gallerysrc").count() >= 8
                and await page.locator(".cm-lp-gallerypreview .gallery").count() == 1,
            )
            await shot(page, "3-live-editing")
            # add a citation to a caption: autocomplete works inside a gallery
            await page.locator(".cm-line.cm-lp-gallerysrc", has_text="No period here").click()
            for _ in range(20):  # make sure the cursor is on that line
                here = await page.evaluate(LINE)
                if here == "No period here":
                    break
                await page.keyboard.press(
                    "ArrowUp" if here in ("", "Photon number versus $N$, see \\eqref{eq:n}", "```") else "ArrowDown"
                )
            # to the end of the line (End is not used: in headless Chromium it can step onto the next line)
            for _ in range(40):
                if await page.evaluate(
                    "(() => { const t = zcNotes.text(), h = zcNotes.cursor(); return t[h] === '\\n'; })()"
                ):
                    break
                await page.keyboard.press("ArrowRight")
            await page.keyboard.type(" [@smi")
            try:
                await page.wait_for_selector(".cm-tooltip-autocomplete", timeout=3000)
                ok = "smith2021" in " ".join(await page.locator(".cm-tooltip-autocomplete li").all_text_contents())
            except Exception:
                ok = False
            check("live: citation autocomplete in a caption", ok)
            await page.wait_for_timeout(250)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(500)
            preview = await page.locator(".cm-lp-gallerypreview .gallery-panel").nth(2).inner_text()
            check("live: the carousel below follows the source", "Smith 2021" in preview, preview)
            await page.keyboard.press("ControlOrMeta+End")
            await page.wait_for_timeout(400)
            check("live: leaving the block draws it again", await page.locator(".cm-lp-gallerysrc").count() == 0)

            # ---- HTML export: one figure, panels in a row, one caption
            await page.click('.note-head [data-act="export"]')
            name, html = await download(page, lambda: page.click(".menu >> text=HTML page"))
            article = html[html.index('<article class="prose">') :]
            check(
                "HTML: no carousel controls",
                "gallery-btn" not in article and "gallery-dot" not in article and "gallery-track" not in article,
            )
            check(
                "HTML: figures with 3 and 2 columns",
                'class="gallery-figure cols-3"' in article and 'class="gallery-figure cols-2"' in article,
            )
            figs = re.findall(r'<figcaption class="gallery-figcaption">(.*?)</figcaption>', article, re.S)
            plain = re.sub(r"<(annotation|math)[^>]*>.*?</\1>", "", figs[0], flags=re.S) if figs else ""
            plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", plain)).strip()
            check(
                "HTML: caption = the gallery's, then (a) …. (b) …. (c) ….",
                re.fullmatch(
                    r"Photon number versus .*?, see \(1\)\. \(a\) Clean system, .*?\. "
                    r"\(b\) With speckle \(Smith 2021, p\. 3\)\. \(c\) No period here \(Smith 2021\)\.",
                    plain,
                )
                is not None,
                plain,
            )
            check(
                "HTML: four panels' caption",
                len(figs) == 2 and re.sub(r"<[^>]+>", "", figs[1]) == "(a) One. (b) Two. (c) Three. (d) Four.",
                figs[1:],
            )
            check("HTML: labels under the panels", article.count('class="gallery-cell-label"') == 7)
            check("HTML: images inlined", article.count("data:image/png;base64,") == 7)
            view = await ctx.new_page()
            await view.set_content(html)
            await view.wait_for_timeout(500)
            rows = await view.evaluate(
                """() => [...document.querySelectorAll('.gallery-figure')].map((f) =>
                     [...f.querySelectorAll('.gallery-cell')].map((c) => Math.round(c.getBoundingClientRect().bottom)))"""
            )
            check(
                "HTML: three panels side by side; four as 2 × 2",
                len(rows) == 2
                and len(set(rows[0])) == 1
                and rows[1][0] == rows[1][1]
                and rows[1][2] == rows[1][3]
                and rows[1][2] > rows[1][0],
                rows,
            )
            fw = await view.evaluate("document.querySelector('.gallery-figure').getBoundingClientRect().width")
            check("HTML: the figure keeps the gallery's width", fw <= 600, fw)
            if SHOTS:
                await view.screenshot(path=f"{SHOTS}/4-export-html.png", full_page=True)
                Path(SHOTS, "gallery.pdf").write_bytes(await view.pdf())
            await view.close()

            # ---- Markdown export
            await page.click('.note-head [data-act="export"]')
            _, md = await download(page, lambda: page.click(".menu >> text=Markdown"))
            check("Markdown: no gallery fence", "```gallery" not in md)
            check(
                "Markdown: the images on one line",
                f"![red]({paths[0]}) ![green]({paths[1]}) ![]({paths[2]})" in md,
                md[:400],
            )
            check(
                "Markdown: the figure caption",
                "(a) Clean system, $\\xi\\to\\infty$. (b) With speckle (Smith 2021, p. 3)." in md,
            )

            # ---- PDF goes through the same page
            await page.click('.note-head [data-act="export"]')
            await page.click(".menu >> text=PDF…")
            for _ in range(60):
                if await page.evaluate("!!window.__printed"):
                    break
                await page.wait_for_timeout(200)
            printed = await page.evaluate("window.__printed || ''")
            check(
                "PDF: printed with the figures",
                'class="gallery-figure cols-3"' in printed and 'class="gallery-btn' not in printed,
            )
            await page.wait_for_timeout(300)
            check("no console errors", not errors, errors[:5])
            await b.close()
    finally:
        srv.terminate()
        shutil.rmtree(DATA, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
