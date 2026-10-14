"""Browser checklist for links in the PDF viewer: internal links jump, external links open a new
tab, hovering shows a preview of the target, and Back/Forward return to the previous place.

    python tests/e2e/viewer_links_checklist.py         (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8770 with a throw-away data folder; your library is not touched.
"""
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
PORT = 8770
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-viewer-links-e2e-")
SHOTS = os.environ.get("SHOTS")
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


def make_linked_pdf(path: Path) -> Path:
    """Five pages. Page 1 links to page 3 at y=400 (explicit XYZ), to the named destination
    'refs' (page 4, top), to https://example.org, and to 'figure.1' (LaTeX-style: the end of the
    caption at y=400 on page 3, with the figure above it). Page 3 has text at y=400."""
    n = 5
    page_obj = lambda i: 3 + 2 * i            # noqa: E731
    font = 3 + 2 * n
    a_xyz, a_named, a_uri, dests, a_fig = font + 1, font + 2, font + 3, font + 4, font + 5
    texts = {0: [("Introduction: see Section 3 and [1] and the website", 72, 700), ("Fig. 1", 520, 700)],
             1: [("Page two", 72, 700)],
             2: [("Page three top", 72, 700), ("FIGURE BODY", 72, 560), ("SECTION THREE TARGET", 72, 400)],
             3: [("References: [1] Somebody 2020", 72, 700)],
             4: [("Appendix", 72, 700)]}
    objs = {1: b"<< /Type /Catalog /Pages 2 0 R /Names << /Dests " + f"{dests} 0 R".encode() + b" >> >>",
            2: b"<< /Type /Pages /Kids [" + b" ".join(f"{page_obj(i)} 0 R".encode() for i in range(n)) + f"] /Count {n} >>".encode()}
    for i in range(n):
        stream = " ".join(f"BT /F1 16 Tf {x} {y} Td ({t}) Tj ET" for t, x, y in texts[i]).encode("latin-1")
        annots = f" /Annots [{a_xyz} 0 R {a_named} 0 R {a_uri} 0 R {a_fig} 0 R]" if i == 0 else ""
        objs[page_obj(i)] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {page_obj(i) + 1} 0 R "
                             f"/Resources << /Font << /F1 {font} 0 R >> >>{annots} >>").encode()
        objs[page_obj(i) + 1] = b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    objs[font] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    objs[a_xyz] = f"<< /Type /Annot /Subtype /Link /Rect [180 690 260 715] /Border [0 0 0] /Dest [{page_obj(2)} 0 R /XYZ 72 420 0] >>".encode()
    objs[a_named] = b"<< /Type /Annot /Subtype /Link /Rect [290 690 315 715] /Border [0 0 0] /A << /S /GoTo /D (refs) >> >>"
    objs[a_uri] = b"<< /Type /Annot /Subtype /Link /Rect [400 690 500 715] /Border [0 0 0] /A << /S /URI /URI (https://example.org/) >> >>"
    objs[a_fig] = b"<< /Type /Annot /Subtype /Link /Rect [515 690 570 715] /Border [0 0 0] /A << /S /GoTo /D (figure.1) >> >>"
    objs[dests] = f"<< /Names [(figure.1) [{page_obj(2)} 0 R /XYZ 72 400 0] (refs) [{page_obj(3)} 0 R /FitH 792]] >>".encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for k in sorted(objs):
        offsets[k] = len(out)
        out += f"{k} 0 obj\n".encode() + objs[k] + b"\nendobj\n"
    size = max(objs) + 1
    xref = len(out)
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for k in range(1, size):
        out += f"{offsets[k]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


async def wait_for(fn, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if await fn():
            return True
        await asyncio.sleep(0.15)
    return False


async def main():
    env = dict(os.environ, SYNAPSE_DATA_DIR=DATA)
    srv = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--no-browser"], cwd=APP, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    try:
        item = requests.post(f"{BASE}/api/items", json={"authors": "Doe, Jane", "title": "Linked paper", "year": 2020}).json()
        pdf = make_linked_pdf(Path(DATA) / "linked.pdf")
        with open(pdf, "rb") as fh:
            att = requests.post(f"{BASE}/api/items/{item['id']}/attachments",
                                files={"file": ("linked.pdf", fh, "application/pdf")}).json()["attachments"][0]
        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(viewport={"width": 1200, "height": 800})
            v = await ctx.new_page()
            errors = []
            v.on("pageerror", lambda e: errors.append(str(e)))
            await v.goto(f"{BASE}/viewer.html?file=/pdfs/{att['url']}&paper_id={item['id']}&title=x")
            # every page is laid out at once; only those near the viewport are drawn (#19)
            await v.wait_for_selector('.page-wrapper[data-page-number="5"]', timeout=15000)
            await v.wait_for_selector('.page-wrapper[data-page-number="1"] canvas', timeout=15000)
            await v.wait_for_selector('.page-wrapper[data-page-number="1"] .zc-link', timeout=8000)
            n = await v.locator('.page-wrapper[data-page-number="1"] .zc-link').count()
            check("page 1 has its four links", n == 4, n)
            check("Back is disabled before any jump", await v.is_disabled('#zc-back'))

            # scroll a little so 'before' is not just the top
            await v.evaluate("document.getElementById('pdf-container').scrollTop = 40")
            await v.wait_for_timeout(400)
            before = await v.evaluate("window.zcLinks.currentPosition()")

            links = v.locator('.page-wrapper[data-page-number="1"] .zc-link')
            # ---- hover preview
            await links.nth(0).hover()
            await v.wait_for_selector('.zc-link-pop.preview canvas', timeout=5000)
            await v.wait_for_timeout(400)
            head = await v.text_content('.zc-link-pop .zc-pop-head')
            check("hover preview names the target page", "Page 3" in head, head)
            painted = await v.evaluate("""() => {
                const c = document.querySelector('.zc-link-pop canvas');
                const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                let dark = 0; for (let i = 0; i < d.length; i += 4) if (d[i] < 100) dark++;
                return dark; }""")
            check("preview shows the target text", painted > 50, painted)
            await shot(v, "1-preview")
            geo = await v.evaluate("""() => { const s = document.querySelector('.zc-link-pop .zc-pop-page');
                return { st: s.scrollTop, sh: s.scrollHeight, ch: s.clientHeight }; }""")
            # destination y=420pt → (792 - 420) * 1.35 = 502 css px, 16px margin above
            check("preview is scrollable and starts at the destination", geo["sh"] > geo["ch"] * 2 and abs(geo["st"] - 486) < 3, geo)
            box = await v.locator('.zc-link-pop .zc-pop-page').bounding_box()
            await v.mouse.move(box["x"] + 100, box["y"] + 100)
            await v.mouse.wheel(0, -200)
            await v.wait_for_timeout(300)
            st = await v.evaluate("document.querySelector('.zc-link-pop .zc-pop-page')?.scrollTop")
            page_st = await v.evaluate("document.getElementById('pdf-container').scrollTop")
            check("scrolling inside the preview scrolls the target page, not the document", st is not None and st < geo["st"] - 100
                  and abs(page_st - 40) < 2, (st, page_st))
            await v.mouse.move(5, 5)
            await v.wait_for_timeout(500)
            check("preview closes when the pointer leaves", await v.locator('.zc-link-pop').count() == 0)

            # ---- figure link: the end of the caption is near the bottom, the figure above is in view
            await links.nth(3).hover()
            await v.wait_for_selector('.zc-link-pop.preview canvas', timeout=5000)
            await v.wait_for_timeout(400)
            fig = await v.evaluate("""() => { const s = document.querySelector('.zc-link-pop .zc-pop-page');
                return { st: s.scrollTop, ch: s.clientHeight }; }""")
            dest_in_view = (792 - 400) * 1.35 - fig["st"]          # where the caption end is in the popup
            body_in_view = (792 - 575) * 1.35 - fig["st"]          # top of the FIGURE BODY text
            check("figure preview: taller, caption end near the bottom, figure above in view",
                  fig["ch"] == 360 and fig["ch"] * 0.8 < dest_in_view < fig["ch"] and 0 <= body_in_view, (fig, dest_in_view, body_in_view))
            await shot(v, "1b-figure-preview")
            await v.mouse.move(5, 5)
            await v.wait_for_timeout(500)

            # ---- internal link jumps to the destination
            await links.nth(0).click()
            await v.wait_for_timeout(400)
            pos = await v.evaluate("window.zcLinks.currentPosition()")
            target_visible = await v.evaluate("""() => {
                const c = document.getElementById('pdf-container').getBoundingClientRect();
                const s = [...document.querySelectorAll('.page-wrapper[data-page-number="3"] .textLayer span')]
                    .find((x) => x.textContent.includes('SECTION THREE'));
                if (!s) return null;
                const r = s.getBoundingClientRect();
                return r.top - c.top; }""")
            check("internal link jumps to page 3", pos["page"] == 3, pos)
            check("the destination is near the top of the view", target_visible is not None and 0 <= target_visible < 120, target_visible)
            check("Back is enabled after a jump", not await v.is_disabled('#zc-back'))
            await shot(v, "2-jumped")

            # ---- Back returns to the place before the click (toolbar button)
            await v.click('#zc-back')
            await v.wait_for_timeout(500)
            back = await v.evaluate("window.zcLinks.currentPosition()")
            check("Back returns to the previous position", back["page"] == before["page"] and abs(back["frac"] - before["frac"]) < 0.01, (before, back))
            check("Forward is enabled after going back", not await v.is_disabled('#zc-forward'))
            await v.click('#zc-forward')
            await v.wait_for_timeout(500)
            fwd = await v.evaluate("window.zcLinks.currentPosition()")
            check("Forward goes to the link target again", fwd["page"] == 3, fwd)

            # ---- browser Back (Alt+Left / mouse button) works too
            await v.go_back()
            await v.wait_for_timeout(500)
            back2 = await v.evaluate("window.zcLinks.currentPosition()")
            check("browser Back also returns", back2["page"] == 1, back2)

            # ---- named destination
            await links.nth(1).click()
            await v.wait_for_timeout(400)
            pos = await v.evaluate("window.zcLinks.currentPosition()")
            check("named destination jumps to page 4", pos["page"] == 4 and pos["frac"] < 0.1, pos)
            await v.go_back()
            await v.wait_for_timeout(400)

            # ---- position survives a zoom between jump and Back
            await links.nth(0).click()
            await v.wait_for_timeout(300)
            await v.click('#zoom-in')
            await v.wait_for_timeout(700)
            await v.click('#zc-back')
            await v.wait_for_timeout(500)
            z = await v.evaluate("window.zcLinks.currentPosition()")
            check("Back after zooming lands on the same spot", z["page"] == 1 and abs(z["frac"] - before["frac"]) < 0.02, (before, z))

            # ---- external link: hover shows the address, click opens a new tab
            await links.nth(2).hover()
            await v.wait_for_selector('.zc-link-pop .zc-pop-url', timeout=5000)
            check("hovering an external link shows the address", "example.org" in await v.text_content('.zc-pop-url'))
            href = await links.nth(2).get_attribute("href")
            tgt = await links.nth(2).get_attribute("target")
            check("external link opens in a new tab", href == "https://example.org/" and tgt == "_blank", (href, tgt))

            # ---- links are inert with an annotation tool
            await v.click('.tool-btn[data-tool="highlight"]')
            pe = await links.nth(0).evaluate("(e) => getComputedStyle(e).pointerEvents")
            check("links are inert while highlighting", pe == "none", pe)

            # ---- a real two-column LaTeX paper (tests/e2e/fixtures/twocolumn.pdf)
            with open(Path(__file__).parent / "fixtures" / "twocolumn.pdf", "rb") as fh:
                att2 = requests.post(f"{BASE}/api/items/{item['id']}/attachments",
                                     files={"file": ("twocolumn.pdf", fh, "application/pdf")}).json()["attachments"][-1]
            t = await ctx.new_page()
            t.on("pageerror", lambda e: errors.append(str(e)))
            await t.goto(f"{BASE}/viewer.html?file=/pdfs/{att2['url']}&paper_id={item['id']}&title=x")
            await t.wait_for_selector('.page-wrapper[data-page-number="2"]', timeout=15000)
            await t.wait_for_selector('.page-wrapper[data-page-number="1"] canvas', timeout=15000)
            await t.wait_for_selector('.page-wrapper[data-page-number="1"] .zc-link', timeout=15000)
            await t.wait_for_timeout(600)
            names = await t.evaluate("[...document.querySelectorAll('.page-wrapper[data-page-number=\"1\"] .zc-link')].map((a) => a.zcTarget.dest)")

            async def preview(dest_name):
                await t.mouse.move(2, 2)
                await t.wait_for_timeout(350)
                await t.locator('.page-wrapper[data-page-number="1"] .zc-link').nth(names.index(dest_name)).hover()
                await t.wait_for_selector('.zc-link-pop.preview canvas', timeout=5000)
                await t.wait_for_timeout(500)
                return await t.evaluate("""async (nm) => { const s = document.querySelector('.zc-link-pop .zc-pop-page');
                    const ex = await window.zcViewer.pdfDoc.getDestination(nm);
                    return { sl: s.scrollLeft, st: s.scrollTop, cw: s.clientWidth, ch: s.clientHeight, sw: s.scrollWidth,
                             x: ex[2] * 1.35, y: (792 - ex[3]) * 1.35 }; }""", dest_name)

            # sections: \ref targets are section*.N here (revtex); the first two are in the left column
            left, right = await preview(names[0]), await preview(names[6])
            check("left-column section: window on the left column, title near the top",
                  left["sl"] == 0 and 0 < left["y"] - left["st"] < 60 and left["x"] - left["sl"] > 40, left)
            check("right-column section: window on the right column, title near the top",
                  right["sl"] + right["cw"] >= right["sw"] - 12 and 0 < right["y"] - right["st"] < 60 and right["x"] - right["sl"] > 40, right)
            await t.locator('.zc-link-pop').screenshot(path=f"{SHOTS}/2a-right-section.png") if SHOTS else None
            # Figures and tables: the whole drawing and its caption fit in the popup, whatever their size.
            async def shown():
                return await t.evaluate("""() => { const p = document.querySelector('.zc-link-pop'); const s = p.querySelector('.zc-pop-page');
                    const r = p.zcPreview.region, k = p.zcPreview.scale;
                    return r && { scale: k, sl: s.scrollLeft, st: s.scrollTop, cw: s.clientWidth, ch: s.clientHeight,
                                  x0: r.x * k, y0: r.y * k, x1: (r.x + r.w) * k, y1: (r.y + r.h) * k }; }""")

            def whole(v):
                return v and v["x0"] >= v["sl"] - 1 and v["y0"] >= v["st"] - 1 and v["x1"] <= v["sl"] + v["cw"] + 1 and v["y1"] <= v["st"] + v["ch"] + 1

            # Fig. 1: anchored at the END of its caption under a non-figure name (\phantomsection), like many PDFs
            await preview(names[8])                  # links in reading order: Sections I–VIII, then Fig. 1
            f1 = await shown()
            check("figure anchored at its caption end: the whole figure and caption are shown", whole(f1), f1)
            if SHOTS:
                await t.locator('.zc-link-pop').screenshot(path=f"{SHOTS}/2b-figure-end.png")
            await preview("figure.3")                # anchored at the START of its caption; a tall figure
            f2 = await shown()
            check("tall figure: shown whole (scaled down to fit)", whole(f2) and f2["y1"] - f2["y0"] > 400 and f2["scale"] < 1.35, f2)
            if SHOTS:
                await t.locator('.zc-link-pop').screenshot(path=f"{SHOTS}/2c-tall-figure.png")
            await preview("figure.4")                # a vector figure (paths) with text labels
            f3 = await shown()
            check("vector figure with labels: shown whole", whole(f3) and f3["x1"] - f3["x0"] > 300, f3)
            await preview("figure.2")                # two panels with a gap, "(a)" and a title above the top one
            f4 = await shown()
            check("two-panel figure: both panels and the labels above them", whole(f4) and (f4["y1"] - f4["y0"]) / f4["scale"] > 260, f4)
            if SHOTS:
                await t.locator('.zc-link-pop').screenshot(path=f"{SHOTS}/2d-panels.png")
            await preview("table.1")
            tb = await shown()
            check("table: the caption and the whole table below it", whole(tb) and tb["y1"] - tb["y0"] > 100, tb)

            # zoom: the +/- buttons and pinch / Ctrl+wheel, around the pointer; scrolling still works
            await preview("figure.3")
            k0 = (await shown())["scale"]
            await t.click('.zc-pop-zoom [data-z="1"]')
            await t.wait_for_timeout(300)
            k1 = (await shown())["scale"]
            check("+ zooms in (and the popup stays open)", abs(k1 - k0 * 1.25) < 0.01 and await t.is_visible('.zc-link-pop'), (k0, k1))
            box = await t.locator('.zc-link-pop .zc-pop-page').bounding_box()
            await t.mouse.move(box["x"] + 100, box["y"] + 200)
            before = await t.evaluate("""() => { const s = document.querySelector('.zc-link-pop .zc-pop-page');
                return { x: (s.scrollLeft + 100) / document.querySelector('.zc-link-pop').zcPreview.scale, y: (s.scrollTop + 200) / document.querySelector('.zc-link-pop').zcPreview.scale }; }""")
            await t.keyboard.down("Control")
            await t.mouse.wheel(0, -100)
            await t.keyboard.up("Control")
            await t.wait_for_timeout(400)
            after = await t.evaluate("""() => { const s = document.querySelector('.zc-link-pop .zc-pop-page'); const k = document.querySelector('.zc-link-pop').zcPreview.scale;
                return { k, x: (s.scrollLeft + 100) / k, y: (s.scrollTop + 200) / k }; }""")
            doc_scale = await t.evaluate("window.zcViewer.scale")
            check("pinch / Ctrl+wheel zooms the preview around the pointer, not the document",
                  after["k"] > k1 * 1.5 and abs(after["x"] - before["x"]) < 3 and abs(after["y"] - before["y"]) < 3 and doc_scale == 1.5,
                  (k1, after, before, doc_scale))
            sharp = await t.evaluate("(() => { const c = document.querySelector('.zc-link-pop canvas'); return c.width / parseFloat(c.style.width); })()")
            check("the zoomed preview is re-rendered sharp", sharp >= 0.99, sharp)
            st0 = await t.evaluate("document.querySelector('.zc-link-pop .zc-pop-page').scrollTop")
            await t.mouse.wheel(0, 250)
            await t.wait_for_timeout(300)
            st = await t.evaluate("document.querySelector('.zc-link-pop .zc-pop-page')?.scrollTop")
            check("wheel/touchpad scrolls the preview", st is not None and st > st0 + 150, (st0, st))
            check("no console errors", not errors, errors[:5])
            await b.close()
    finally:
        srv.terminate()
        shutil.rmtree(DATA, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _guard import guard  # noqa: E402

guard(PORT)
asyncio.run(main())
