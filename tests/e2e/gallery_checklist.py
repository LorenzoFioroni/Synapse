"""Browser checklist for galleries (```gallery blocks): the carousel in read mode and in the live
preview (arrows, dots, keys, swipe-free scrolling), editing its source with the carousel below,
autocomplete in captions, citations and images in galleries counted by the backend, and the
exports: one figure with panels (a), (b), … and one caption in HTML/PDF, images on a line in Markdown.
Galleries and figures are an extension of Extended Markdown with a switch of its own: turned off (or
the whole package off), the note reads as the Markdown it holds.

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
        "---\ntitle: Figures\n---\n# Figures\n\nSome text before, see \\cref{fig:speckle} and \\cref{fig:speckle,fig:single}; \\Cref{eq:n} is below.\n\n"
        f"![grey|200]({d})\n*A single figure. \\label{{fig:single}}*\n\n![]({d})\n\n"
        f"```gallery|600\n![red]({a})\n*Clean system, $\\xi\\to\\infty$.*\n"
        f"![green]({b}) With speckle [@smith2021, p. 3]\n![|200]({c})\nNo period here\n\n"
        "Photon number versus $N$, see \\eqref{eq:n} \\label{fig:speckle}\n```\n\n"
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
            await page.click('#sidebar [data-act="open-notes"]')
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
                await page.locator(G).first.locator(".gallery-panel").count() == 3
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
            nums = await page.locator(".note-read .fig-num").all_text_contents()
            check(
                "figures numbered: the image Fig. 1, the galleries Fig. 2 (its caption) and Fig. 3 (on each panel)",
                nums == ["Fig. 1.", "Fig. 2.", "Fig. 3", "Fig. 3", "Fig. 3", "Fig. 3"],
                nums,
            )
            check(
                "an image without a caption is not a figure",
                await page.locator(".note-read figure.figure").count() == 1,
            )
            refs = await page.locator(".note-read p").first.inner_text()
            check(
                "\\cref names figures and equations with their numbers",
                "see Fig. 2 and Figs. 1 and 2; Equation (1) is below." in refs.replace("\u00a0", " "),
                refs,
            )
            await page.locator(".note-read p .eqref").nth(1).click()
            await page.wait_for_timeout(500)
            check(
                "clicking it goes to the figure",
                await page.evaluate("document.querySelector('.note-read figure.figure').classList.contains('flash')"),
            )
            # the label hangs to the left: the caption's text is centred under its image
            centred = await page.evaluate(
                """() => [...document.querySelectorAll('.note-read .gallery .gallery-panel')].slice(0, 1).map((p) => {
                  const t = p.querySelector('.gallery-caption-text').getBoundingClientRect();
                  const i = p.querySelector('.gallery-image img').getBoundingClientRect();
                  const l = p.querySelector('.gallery-label').getBoundingClientRect();
                  return [Math.round(t.left + t.width / 2 - (i.left + i.width / 2)), l.right <= t.left + 1, Math.round(l.top - t.top)]; })[0]"""
            )
            check(
                "panel caption centred, its label to the left",
                abs(centred[0]) <= 1 and centred[1] and abs(centred[2]) <= 2,
                centred,
            )
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
            check(
                "live: Fig. 1. in front of the image's caption, its \\label hidden",
                await page.locator(".cm-lp-chip.fignum").all_text_contents() == ["Fig. 1."]
                and re.sub(r"\s+", "", await page.locator(".cm-line.cm-lp-figcaption").inner_text())
                == "Fig.1.Asinglefigure.",
                await page.locator(".cm-line.cm-lp-figcaption").inner_text(),
            )
            live_nums = await page.locator(".cm-content .cm-lp-gallery .fig-num").all_text_contents()
            check("live: the galleries carry Fig. 2 and Fig. 3", live_nums[:2] == ["Fig. 2.", "Fig. 3"], live_nums)
            live_refs = await page.locator(".cm-content .cm-lp-chip.eqref").all_text_contents()
            check(
                "live: \\cref chips show the names and numbers",
                [t.replace("\u00a0", " ") for t in live_refs[:3]] == ["Fig. 2", "Figs. 1 and 2", "Equation (1)"],
                live_refs,
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
                    "ArrowUp"
                    if here in ("", "Photon number versus $N$, see \\eqref{eq:n} \\label{fig:speckle}", "```")
                    else "ArrowDown"
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
                    r"Fig\. 2\. Photon number versus .*?, see \(1\)\. \(a\) Clean system, .*?\. "
                    r"\(b\) With speckle \(Smith 2021, p\. 3\)\. \(c\) No period here \(Smith 2021\)\.",
                    plain,
                )
                is not None,
                plain,
            )
            check(
                "HTML: four panels' caption",
                len(figs) == 2 and re.sub(r"<[^>]+>", "", figs[1]) == "Fig. 3. (a) One. (b) Two. (c) Three. (d) Four.",
                figs[1:],
            )
            check(
                "HTML: a letter on each panel, nothing under it",
                re.findall(r'<span class="gallery-cell-label">([a-z])</span>', article) == list("abcabcd")
                and "gallery-label" not in article,
            )
            check("HTML: images inlined", article.count("data:image/png;base64,") == 9)
            single = re.search(r'<figcaption class="figure-caption">(.*?)</figcaption>', article, re.S)
            check(
                "HTML: the captioned image is Fig. 1, the other no figure",
                single is not None
                and re.sub(r"<[^>]+>", "", single.group(1)).strip() == "Fig. 1. A single figure."
                and article.count('class="figure"') == 1,
                single.group(1) if single else None,
            )
            check(
                "HTML: \\cref gives names and numbers, linked to the figures",
                re.search(
                    r'see <span class="cref"[^>]*>Fig\.(?:\u00a0|&nbsp;)<a class="eqref" href="#eq-fig-speckle"[^>]*>2</a></span> and '
                    r'<span class="cref"[^>]*>Figs\.(?:\u00a0|&nbsp;)<a class="eqref" href="#eq-fig-single"[^>]*>1</a> and <a class="eqref" href="#eq-fig-speckle"[^>]*>2</a></span>',
                    article,
                )
                is not None
                and 'id="eq-fig-speckle"' in article
                and 'id="eq-fig-single"' in article,
            )
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
            badges = await view.evaluate(
                """() => [...document.querySelectorAll('.gallery-cell')].map((c) => {
                  const b = c.querySelector('.gallery-cell-label'), i = c.querySelector('img').getBoundingClientRect();
                  const r = b.getBoundingClientRect(), st = getComputedStyle(b);
                  return [Math.round(r.left - i.left), Math.round(r.top - i.top), Math.round(r.width) === Math.round(r.height),
                          st.backgroundColor, st.borderTopColor, st.borderRadius, Math.round(r.bottom - i.bottom) < 0]; })"""
            )
            check(
                "HTML: the letter in a white circle with a black border, on the image's upper left corner",
                len(badges) == 7
                and all(
                    0 <= x <= 12
                    and 0 <= y <= 12
                    and sq
                    and bg == "rgb(255, 255, 255)"
                    and bc == "rgb(0, 0, 0)"
                    and rad == "50%"
                    and inside
                    for x, y, sq, bg, bc, rad, inside in badges
                ),
                badges[:2],
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
                "**Fig. 2.** *Photon number versus $N$, see \\eqref{eq:n}. (a) Clean system, $\\xi\\to\\infty$. (b) With speckle (Smith 2021, p. 3)."
                in md,
                md[:900],
            )
            check(
                "Markdown: numbered captions and references",
                "see Fig.\u00a02 and Figs.\u00a01 and 2; \\Cref{eq:n} is below." in md
                and f"![grey]({paths[3]})\n**Fig. 1.** *A single figure.*\n" in md
                and "**Fig. 3.** *(a) One. (b) Two. (c) Three. (d) Four.*" in md
                and "\\label{fig" not in md,
                md[:300],
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

            # ---- galleries and figures switched off in Extended Markdown's settings (and on again), in the open note
            switch = f"{BASE}/api/plugin-host/extended-markdown/settings"
            requests.put(switch, json={"figures": False}, timeout=10).raise_for_status()
            await page.click('[data-mode="read"]')
            for _ in range(50):
                if await page.locator(".note-read .gallery").count() == 0:
                    break
                await page.wait_for_timeout(200)
            read_imgs = await page.locator(".note-read img").count()
            check(
                "off: read mode shows the images and captions as Markdown, no carousel, no numbers",
                await page.locator(".note-read .gallery, .note-read figure, .note-read .fig-num").count() == 0
                and await page.locator(".note-read .code-block").count() == 0
                and read_imgs == 9,
                read_imgs,
            )
            await page.click('[data-mode="live"]')
            await page.wait_for_timeout(500)
            check(
                "off: the live preview draws no carousel and no Fig. chips",
                await page.locator(".cm-lp-gallery, .cm-lp-chip.fignum").count() == 0,
            )
            requests.put(switch, json={"figures": True}, timeout=10).raise_for_status()
            # (the editor draws only what is near the screen, so at least the first carousel)
            drawn = ".cm-content .cm-lp-gallery .gallery"
            for _ in range(50):
                if await page.locator(drawn).count():
                    break
                await page.wait_for_timeout(200)
            check(
                "on again: the live preview draws carousels and Fig. 1. at once",
                await page.locator(drawn).count() >= 1 and await page.locator(".cm-lp-chip.fignum").count() == 1,
            )
            await page.click('[data-mode="read"]')
            await page.wait_for_timeout(300)
            check(
                "on again: read mode shows both carousels, numbered",
                await page.locator(".note-read .gallery").count() == 2
                and await page.locator(".note-read .fig-num").count() >= 3,
                await page.locator(".note-read .fig-num").all_text_contents(),
            )
            # ---- the whole Extended Markdown package off (and on again)
            requests.post(f"{BASE}/api/plugin-host/extended-markdown/disable", timeout=10).raise_for_status()
            for _ in range(50):
                if await page.locator(".note-read .gallery").count() == 0:
                    break
                await page.wait_for_timeout(200)
            check(
                "package off: read mode shows no carousel and no numbers",
                await page.locator(".note-read .gallery, .note-read figure, .note-read .fig-num").count() == 0,
            )
            requests.post(f"{BASE}/api/plugin-host/extended-markdown/enable", timeout=10).raise_for_status()
            for _ in range(50):
                if await page.locator(".note-read .gallery").count() == 2:
                    break
                await page.wait_for_timeout(200)
            check("package on again: both carousels are back", await page.locator(".note-read .gallery").count() == 2)
            await page.wait_for_timeout(300)
            check("no console errors", not errors, errors[:5])
            await b.close()
    finally:
        srv.terminate()
        shutil.rmtree(DATA, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
