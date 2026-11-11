"""Browser checklist for exporting a note: the Export button and the notes-list menu, the HTML
page (rendered like read mode, light theme, citations linked to full references with their DOI, embeds, formulas
with their fonts, diagrams, images inlined, nothing that needs the app or the network), portable
Markdown, and PDF through the print dialog.

    python tests/e2e/export_checklist.py                (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8792 with a throw-away data folder; your library is not touched.
"""

import asyncio
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from tests.e2e._guard import guard  # noqa: E402

PORT = 8792
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-export-e2e-")
SHOTS = os.environ.get("SHOTS")
results = []
PNG = (APP / "tests/e2e/fixtures/wide.png").read_bytes()


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


def seed(s):
    s.post(
        BASE + "/api/items",
        json={
            "citation_key": "smith2021",
            "item_type": "journalArticle",
            "title": "Quantum things",
            "authors": "Smith, Anna; Doe, John; Roe, Rita",
            "year": 2021,
            "journal": "Physical Review X",
            "volume": "11",
            "issue": "2",
            "pages": "021001",
            "doi": "10.1103/PhysRevX.11.021001",
        },
    ).raise_for_status()
    s.post(
        BASE + "/api/items",
        json={
            "citation_key": "lee2019",
            "item_type": "book",
            "title": "Tensor networks",
            "authors": "Lee, Ji-Hoon",
            "year": 2019,
            "publisher": "Springer",
            "place": "Cham",
            "edition": "2",
            "url": "https://example.org/tn",
        },
    ).raise_for_status()
    img = s.post(BASE + "/api/notes/attachments", files={"file": ("plot.png", PNG, "image/png")}).json()
    s.post(
        BASE + "/api/notes/note",
        json={"path": "part.md", "title": "Part", "text": "# Part\n\nThe embedded part cites [@lee2019].\n"},
    ).raise_for_status()
    report = (
        "---\ntitle: Report\n---\nIntro citing [@smith2021, p. 4]. See [[Part|the part]].\n\n"
        "$$\nE = mc^2 \\label{eq:e}\n$$\n\nAs \\eqref{eq:e} shows.\n\n![[Part]]\n\n"
        "```python\ndef f():\n    return 1\n```\n\n```mermaid\ngraph TD; A-->B\n```\n\n"
        f"- [x] done task\n- [ ] open task\n\n![Plot|200]({img['path']})\n"
    )
    s.post(BASE + "/api/notes/note", json={"path": "report.md", "title": "Report", "text": report}).raise_for_status()


async def download(page, run):
    async with page.expect_download() as info:
        await run()
    d = await info.value
    return d.suggested_filename, Path(await d.path()).read_text()


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
        seed(s)
        async with async_playwright() as pw:
            b = await pw.chromium.launch()
            ctx = await b.new_context(viewport={"width": 1400, "height": 900}, accept_downloads=True)
            # the print dialog cannot be driven: record that it was asked for, and from which page
            await ctx.add_init_script(
                "window.print = function () { try { window.top.__printed = document.documentElement.outerHTML; }"
                " catch (e) {} };"
            )
            page = await ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(BASE + "/")
            await page.evaluate("document.documentElement.dataset.theme = 'dark'")
            await page.click('#sidebar [data-act="open-notes"]')
            await page.click('.nt-row.note[data-path="report.md"]')
            await page.wait_for_selector(".cm-content")
            await page.wait_for_timeout(600)

            # ---- HTML from the Export button
            await page.click('.note-head [data-act="export"]')
            labels = await page.locator(".menu .menu-item").all_text_contents()
            check(
                "Export offers built-in formats and plugin LaTeX",
                [x.strip() for x in labels] == ["PDF…", "HTML page", "Markdown", "LaTeX project…"],
            )
            name, html = await download(page, lambda: page.click(".menu >> text=HTML page"))
            check("HTML file named after the note", name == "Report.html", name)
            check("light theme, whatever the app's", 'data-theme="light"' in html)
            check("a title", "<h1>Report</h1>" in html)
            article = html[html.index('<article class="prose">') :]
            text = re.sub(r"<[^>]+>", "", article)
            cites = re.findall(r'<a class="cite" href="#([^"]+)"', article)
            check(
                "citations link to their reference",
                "(Smith et al. 2021, p. 4)" in text
                and cites == ["ref-smith2021", "ref-lee2019"]
                and all(f'<li id="{c}">' in article for c in cites),
                cites,
            )
            check("links as text", '<span class="wikilink"' in html and "the part" in html)
            check("embedded note included", "The embedded part cites" in html)
            refs = re.search(r'<section class="references">.*?</section>', html, re.S)
            rtext = refs.group(0) if refs else ""
            smith = (
                "Smith, A., Doe, J. &amp; Roe, R. (2021). Quantum things. <i>Physical Review X</i>, <i>11</i>(2), 021001. "
                '<a class="ref-link" href="https://doi.org/10.1103/PhysRevX.11.021001">'
            )
            lee = (
                "Lee, J.-H. (2019). <i>Tensor networks</i> (2nd ed.). Springer, Cham. "
                '<a class="ref-link" href="https://example.org/tn">'
            )
            check(
                "full references (volume, issue, pages, publisher) with DOI or URL links, embeds included, in order",
                smith in rtext and lee in rtext and rtext.index(smith) < rtext.index(lee),
                rtext[:400],
            )
            check("formula and its number", 'class="katex' in html and "(1)" in html)
            check("KaTeX fonts inside the file", "data:font/woff2;base64," in html)
            check("code highlighted", "cmt-kw" in html)
            check("diagram drawn", "<svg" in html and "mermaid-src" not in html)
            check("image inlined", "data:image/png;base64," in html)
            check("tasks shown, not editable", html.count("disabled") >= 2 and "data-task-line" not in html)
            check("no copy buttons or app links", "copy-code" not in article and "data-line" not in article)
            outside = sorted(set(re.findall(r'(?:src="|<link [^>]*href=")(?!data:|#)([^"]+)"', html)))
            check("loads nothing from outside", not outside and "url(fonts/" not in html, outside)

            # open it as a file, offline: it looks like the read view
            view = await ctx.new_page()
            await view.set_content(html)
            await view.wait_for_timeout(500)
            bg = await view.evaluate("getComputedStyle(document.body).backgroundColor")
            check("the page is light", bg == "rgb(255, 255, 255)", bg)
            fam = await view.evaluate(
                "document.fonts.check('16px KaTeX_Main') && [...document.fonts].some((f) => f.family.includes('KaTeX') && f.status === 'loaded')"
            )
            check("KaTeX fonts load from the file", fam)
            await view.click("a.cite >> nth=0")
            await view.wait_for_timeout(300)
            target = await view.evaluate(
                "(() => { const r = document.getElementById('ref-smith2021').getBoundingClientRect();"
                " return r.top >= 0 && r.top < innerHeight; })()"
            )
            check("clicking a citation goes to its reference", target)
            # what "Save as PDF" makes of the page: the DOI and the citations stay links
            pdf_bytes = await view.pdf()
            pdf = pdf_bytes.decode("latin-1")
            check(
                "in the PDF, DOIs and citations are links",
                "/URI (https://doi.org/10.1103/PhysRevX.11.021001)" in pdf and ("/Dest" in pdf or "/GoTo" in pdf),
            )
            if SHOTS:
                os.makedirs(SHOTS, exist_ok=True)
                Path(SHOTS, "export.pdf").write_bytes(pdf_bytes)
                await view.screenshot(path=f"{SHOTS}/export-html.png", full_page=True)
            await view.close()

            # ---- Markdown from the notes list
            await page.click('.nt-row.note[data-path="report.md"]', button="right")
            await page.click(".menu >> text=Export…")
            name, md = await download(page, lambda: page.click(".menu >> text=Markdown"))
            check("Markdown file named after the note", name == "Report.md", name)
            check(
                "front matter dropped, title added",
                md.startswith("# Report\n\nIntro citing (Smith et al. 2021, p. 4). See the part."),
                md[:80],
            )
            check("embed inlined", "# Part\n\nThe embedded part cites (Lee 2019)." in md)
            check("image without the width", "![Plot](" in md and "|200" not in md)
            check(
                "references at the end",
                md.rstrip().endswith(
                    "## References\n\n1. Smith, A., Doe, J. & Roe, R. (2021). Quantum things. *Physical Review X*, *11*(2), "
                    "021001. <https://doi.org/10.1103/PhysRevX.11.021001>\n"
                    "2. Lee, J.-H. (2019). *Tensor networks* (2nd ed.). Springer, Cham. <https://example.org/tn>"
                ),
                md[-200:],
            )
            check("code and math left alone", "def f():" in md and "E = mc^2 \\label{eq:e}" in md)

            # ---- unsaved text is exported as it is in the editor
            await page.click(".cm-content")
            await page.keyboard.press("ControlOrMeta+End")
            await page.keyboard.type("\nFresh line.")
            await page.click('.note-head [data-act="export"]')
            _, md2 = await download(page, lambda: page.click(".menu >> text=Markdown"))
            check("the editor's text, saved or not", "Fresh line." in md2)

            # ---- LaTeX: current editor text, figures, references, optional linked project
            for linked in (False, True):
                await page.click('.note-head [data-act="export"]')
                await page.click(".menu >> text=LaTeX project…")
                box = page.get_by_role("checkbox", name="Include linked notes")
                check("linked notes default off", not await box.is_checked())
                if linked:
                    await box.focus()
                    await page.keyboard.press("Space")
                columns = page.get_by_label("Gallery images per row")
                if linked:
                    check("gallery layout is remembered", await columns.input_value() == "2")
                else:
                    check("gallery layout defaults to automatic", await columns.input_value() == "0")
                    await columns.select_option("2")
                await page.set_viewport_size({"width": 560, "height": 800})
                bounds = await page.get_by_role("dialog", name="Export LaTeX").bounding_box()
                check("LaTeX dialog fits narrow layout", bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 560)
                await page.locator(".dialog-backdrop").evaluate(
                    "el => Promise.all(el.getAnimations({subtree: true}).map(a => a.finished))"
                )
                if SHOTS:
                    await page.screenshot(path=f"{SHOTS}/export-latex-{linked}.png")
                async with page.expect_download() as export:
                    await page.get_by_role("button", name="Download", exact=True).click()
                result = await export.value
                with zipfile.ZipFile(io.BytesIO(Path(await result.path()).read_bytes())) as archive:
                    check("LaTeX archive integrity", archive.testzip() is None)
                    names = archive.namelist()
                    tex = archive.read("main.tex").decode()
                    bib = archive.read("references.bib").decode()
                    check("LaTeX uses unsaved editor text", "Fresh line." in tex)
                    check(
                        "LaTeX contains figures and cited bibliography",
                        any(n.startswith("figures/") for n in names) and "smith2021" in bib and "lee2019" in bib,
                    )
                    check(
                        "LaTeX file count follows option",
                        len([n for n in names if n.endswith(".tex")]) == (2 if linked else 1),
                    )
                    check(
                        "LaTeX includes linked input or inline embed",
                        "\\input{notes/" in tex if linked else "The embedded part cites" in tex,
                    )
                    readme = archive.read("README.txt").decode()
                    check(
                        "LaTeX targets pdfLaTeX with plain citations and the chosen gallery layout",
                        "\\usepackage[T1]{fontenc}" in tex
                        and "\\cite[p.~4]{smith2021}" in tex
                        and "latexmk -pdf" in readme
                        and "2 images per row" in readme,
                        readme,
                    )
                await page.set_viewport_size({"width": 1400, "height": 900})
                await page.evaluate("document.documentElement.dataset.theme = 'light'")

            # ---- PDF: the print dialog of a hidden frame
            await page.click('.note-head [data-act="export"]')
            await page.click(".menu >> text=PDF…")
            ok = False
            for _ in range(60):
                if await page.evaluate("!!window.__printed"):
                    ok = True
                    break
                await page.wait_for_timeout(200)
            printed = await page.evaluate("window.__printed || ''")
            check(
                "PDF opens the print dialog with the note", ok and "Intro citing" in printed and "references" in printed
            )
            check("…from a frame, not the app", 'id="notes-view"' not in printed and "<title>Report</title>" in printed)
            check("print styles included", "@media print" in printed)
            await page.wait_for_timeout(300)
            # Provider removal and reactivation update the note menu in the existing window.
            for enabled in (False, True):
                action = "enable" if enabled else "disable"
                s.post(BASE + f"/api/plugin-host/export-formats/{action}").raise_for_status()
                await page.evaluate("window.zcPlugins.sync()")
                await page.click('.note-head [data-act="export"]')
                choices = await page.locator(".menu .menu-item").all_text_contents()
                check(
                    "LaTeX availability follows plugin lifecycle",
                    ("LaTeX project…" in [c.strip() for c in choices]) == enabled,
                    choices,
                )
                check("built-in note exports stay available", "HTML page" in [c.strip() for c in choices])
                await page.keyboard.press("Escape")
            check("no console errors", not errors, errors[:5])
            await b.close()
    finally:
        srv.terminate()
        shutil.rmtree(DATA, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
