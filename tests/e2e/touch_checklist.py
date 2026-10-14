"""Browser checklist for the PDF viewer on a tablet (issue #34): every annotation tool works with a
pen (Apple Pencil), fingers scroll once a pen has been used (and draw before that), a finger
selection can be highlighted, pinching zooms the pages around the fingers, a snapshot can be cut
with the pen, and fewer, smaller canvases are kept.

    python tests/e2e/touch_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8782 with a throw-away data folder; your library is not touched.
Runs Chromium with the "iPad Pro 11" profile (touch, no hover). Pen input is sent as pen pointer
events (DevTools protocol), finger input as touch events. Safari's pinch events are simulated:
the real Pencil, palm and pinch behaviour still need a try on an iPad.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from tests.e2e._guard import guard  # noqa: E402

PORT = 8782
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-touch-e2e-")
PDF = APP / "tests" / "e2e" / "fixtures" / "twocolumn.pdf"
SHOTS = os.environ.get("SHOTS")
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


async def wait_for(fn, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if await fn():
            return True
        await asyncio.sleep(0.15)
    return False


def anns(att_id):
    return requests.get(f"{BASE}/api/attachments/{att_id}/annotations").json()["annotations"]


def setup_library():
    from pypdf import PdfReader, PdfWriter

    from backend import create_app
    from backend.config import Config

    s = create_app(Config.from_env(DATA), start_indexer=False).extensions["synapse"]
    item = s.papers.create({"title": "Touch paper", "authors": "Cui, Jian", "year": 2015})
    if not item.get("citation_key"):
        item = s.papers.update(item["id"], {"citation_key": "cui2015touch"})
    shutil.copy(PDF, s.config.pdf_dir / "touch.pdf")
    att = s.papers.add_attachment(item["id"], "touch.pdf", "touch.pdf", "pdf")
    # a long document for the canvas limits
    src = PdfReader(str(PDF))
    w = PdfWriter()
    while len(w.pages) < 24:
        for pg in src.pages:
            if len(w.pages) < 24:
                w.add_page(pg)
    with open(s.config.pdf_dir / "long.pdf", "wb") as fh:
        w.write(fh)
    long_item = s.papers.create({"title": "Long touch paper"})
    long_att = s.papers.add_attachment(long_item["id"], "long.pdf", "long.pdf", "pdf")
    return item, att, long_item, long_att


def viewer(item, att):
    q = {"file": f"/pdfs/{att['url']}", "paper_id": item["id"], "title": item["title"], "att": att["id"]}
    return f"{BASE}/viewer.html?{urlencode(q)}"


async def open_viewer(page, url):
    await page.goto(url)
    await page.wait_for_function("document.querySelectorAll('.page-wrapper .textLayer span').length > 20")
    await page.evaluate("window.zcAnnotations.ready")
    await page.wait_for_timeout(300)


async def tool(page, name):
    await page.evaluate(f"""(() => {{ const b = document.querySelector('.tool-btn[data-tool="{name}"]');
        if (!b.classList.contains('active')) b.click(); }})()""")


class Input:
    """Pen strokes and finger touches through the DevTools protocol."""

    def __init__(self, cdp):
        self.cdp = cdp

    async def pen(self, points, hold=0):
        m = lambda t, x, y, **kw: self.cdp.send(
            "Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "pointerType": "pen", "button": "left", **kw}
        )
        x0, y0 = points[0]
        await m("mouseMoved", x0, y0, buttons=0)
        await m("mousePressed", x0, y0, buttons=1, clickCount=1)
        for x, y in points[1:]:
            await m("mouseMoved", x, y, buttons=1)
        if hold:
            await asyncio.sleep(hold)
        x1, y1 = points[-1]
        await m("mouseReleased", x1, y1, buttons=0, clickCount=1)

    async def finger(self, points):
        t = lambda typ, pts: self.cdp.send("Input.dispatchTouchEvent", {"type": typ, "touchPoints": pts})
        x0, y0 = points[0]
        await t("touchStart", [{"x": x0, "y": y0}])
        for x, y in points[1:]:
            await t("touchMove", [{"x": x, "y": y}])
            await asyncio.sleep(0.016)
        await t("touchEnd", [])

    async def two_fingers(self, start, end, steps=8):
        t = lambda typ, pts: self.cdp.send("Input.dispatchTouchEvent", {"type": typ, "touchPoints": pts})
        pts = lambda f: [
            {"x": a + (b - a) * f, "y": c + (d - c) * f, "id": i} for i, ((a, c), (b, d)) in enumerate(zip(start, end))
        ]
        await t("touchStart", pts(0))
        for k in range(1, steps + 1):
            await t("touchMove", pts(k / steps))
            await asyncio.sleep(0.016)
        await t("touchEnd", [])


def line(a, b, n=12):
    return [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n + 1)]


async def page_box(page, n=1):
    return await page.locator(f'.page-wrapper[data-page-number="{n}"]').bounding_box()


async def spot(page, x, y):
    """The page and page fraction under a point of the screen."""
    return await page.evaluate(
        """([x, y]) => {
        for (const w of document.querySelectorAll('.page-wrapper')) {
            const r = w.getBoundingClientRect();
            if (y >= r.top && y <= r.bottom) return [Number(w.dataset.pageNumber), (x - r.left) / r.width, (y - r.top) / r.height];
        }
        return null; }""",
        [x, y],
    )


async def main():
    guard(PORT)
    item, att, long_item, long_att = setup_library()
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", DATA],
        cwd=APP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ipad = p.devices["iPad Pro 11"]
            errors = []

            # ---- a tablet without a pen: fingers draw ---------------------------------------
            ctx0 = await browser.new_context(**ipad)
            page = await ctx0.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            await open_viewer(page, viewer(item, att))
            inp = Input(await ctx0.new_cdp_session(page))
            meta = await page.evaluate("document.querySelector('meta[name=viewport]')?.content || ''")
            check("the viewer has a viewport meta tag", "width=device-width" in meta, meta)
            check("… which keeps Safari from zooming the page when a field gets focus", "maximum-scale=1" in meta, meta)
            sizes = await page.eval_on_selector_all(
                ".tool-btn, #anno-undo, #anno-redo", "bs => bs.map(b => b.getBoundingClientRect().height)"
            )
            check(
                "toolbar buttons are finger-sized (≥ 40 px)",
                sizes and min(sizes) >= 40,
                f"{min(sizes) if sizes else None}px",
            )
            check(
                "Undo and Redo buttons are in the toolbar",
                await page.locator("#anno-undo").count() == 1 and await page.locator("#anno-redo").count() == 1,
            )
            await shot(page, "01-toolbar")
            layout = []
            for name in ("select", "highlight", "text", "eraser"):
                await tool(page, name)
                layout.append(
                    await page.evaluate("""(() => { const r = s => document.querySelector(s).getBoundingClientRect();
                    return [Math.round(r('#toolbar').height), Math.round(r('#page-num-text').left), Math.round(r('.tool-btn[data-tool="draw"]').left)]; })()""")
                )
            check("choosing a tool does not move the toolbar around", all(x == layout[0] for x in layout), str(layout))
            check("… which stays one row in portrait", layout[0][0] < 60, f"{layout[0][0]}px")
            await tool(page, "select")
            box = await page_box(page)
            await tool(page, "draw")
            await inp.finger(line((box["x"] + 100, box["y"] + 120), (box["x"] + 300, box["y"] + 180)))
            ok = await wait_for(lambda: asyncio.to_thread(lambda: any(a["type"] == "ink" for a in anns(att["id"]))))
            check("without a pen, a finger draws ink", ok)
            for a in anns(att["id"]):
                requests.delete(f"{BASE}/api/annotations/{a['id']}")
            await ctx0.close()

            # ---- with a pen ---------------------------------------------------------------
            ctx = await browser.new_context(**ipad)
            page = await ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            await open_viewer(page, viewer(item, att))
            cdp = await ctx.new_cdp_session(page)
            inp = Input(cdp)
            box = await page_box(page)
            X, Y = box["x"], box["y"]

            await tool(page, "draw")
            await inp.pen(line((X + 120, Y + 150), (X + 360, Y + 230), 20))
            ok = await wait_for(lambda: asyncio.to_thread(lambda: [a for a in anns(att["id"]) if a["type"] == "ink"]))
            ink = [a for a in anns(att["id"]) if a["type"] == "ink"]
            check("the pen draws ink", ok and len(ink[0]["path"]) >= 10, f"{len(ink[0]['path']) if ink else 0} points")
            check(
                "… and from then on the page knows a pen is used",
                await page.evaluate("document.body.classList.contains('zc-pen')"),
            )

            top0 = await page.evaluate("window.zcViewer.container.scrollTop")
            await inp.finger(line((X + 300, Y + 600), (X + 300, Y + 300), 10))
            await page.wait_for_timeout(600)
            top1 = await page.evaluate("window.zcViewer.container.scrollTop")
            n_ink = len([a for a in anns(att["id"]) if a["type"] == "ink"])
            check(
                "with the draw tool, a finger now scrolls and does not draw",
                top1 > top0 + 50 and n_ink == 1,
                f"scroll {top0}→{top1}, ink {n_ink}",
            )
            await page.evaluate("window.zcViewer.container.scrollTop = 0")
            await page.wait_for_timeout(300)
            box = await page_box(page)
            X, Y = box["x"], box["y"]

            await tool(page, "box")
            W, H = box["width"], box["height"]
            await inp.pen(line((X + W * 0.02, Y + H * 0.30), (X + W * 0.09, Y + H * 0.40), 8))  # in the margin
            ok = await wait_for(lambda: asyncio.to_thread(lambda: any(a["type"] == "box" for a in anns(att["id"]))))
            check("the pen draws a box", ok)

            await tool(page, "text")
            await inp.pen([(X + 140, Y + 520)])
            focused = await wait_for(
                lambda: page.evaluate("document.activeElement?.classList.contains('anno-text-input')"), timeout=3
            )
            await page.wait_for_timeout(400)  # the handle fades in
            check("a pen tap with the text tool opens a text box ready for typing", focused)
            handle = await page.evaluate("""(() => { const h = document.activeElement?.parentElement?.querySelector('.anno-text-handle');
                return h ? [getComputedStyle(h).opacity, h.getBoundingClientRect().width] : null; })()""")
            check(
                "… with its size handle visible and finger-sized",
                handle and float(handle[0]) > 0.5 and handle[1] >= 20,
                str(handle),
            )
            await page.keyboard.type("Written on the iPad")
            await page.evaluate("document.activeElement.blur()")
            ok = await wait_for(
                lambda: asyncio.to_thread(
                    lambda: any(a["type"] == "text" and a.get("text") == "Written on the iPad" for a in anns(att["id"]))
                )
            )
            check("… and its text is saved", ok)
            txt = next(a for a in anns(att["id"]) if a["type"] == "text")
            check(
                "text boxes are plain HTML over the page (Safari misplaces HTML inside SVG)",
                await page.evaluate(
                    f"!!document.querySelector('.anno-text-layer [data-id=\"{txt['id']}\"]') && !document.querySelector('foreignObject')"
                ),
            )
            where = lambda: (
                page.evaluate(f"""(() => {{ const t = document.querySelector('[data-id="{txt["id"]}"] .anno-text-input').getBoundingClientRect();
                const w = document.querySelector('.page-wrapper[data-page-number="1"]').getBoundingClientRect();
                return [(t.left - w.left) / w.width, (t.top - w.top) / w.height, t.height / w.height]; }})()""")
            )
            w0 = await where()
            await page.tap("#zoom-in")
            await page.tap("#zoom-in")
            await page.wait_for_timeout(500)
            w1 = await where()
            check(
                "… and stay on their spot of the page, at the same size, when zooming",
                all(abs(a - b) < 0.004 for a, b in zip(w0, w1)),
                f"{w0} → {w1}",
            )
            await page.tap("#zoom-out")
            await page.tap("#zoom-out")
            await page.wait_for_timeout(500)

            # highlight: drag the pen along a line of text
            spans = await page.evaluate("""(() => [...document.querySelectorAll('.page-wrapper[data-page-number="1"] .textLayer span')]
                .map(s => s.getBoundingClientRect()).filter(r => r.width > 120).slice(4, 6).map(r => [r.left, r.top, r.width, r.height]))()""")
            l, t, w, h = spans[0]
            await tool(page, "highlight")
            await inp.pen(line((l + 3, t + h / 2), (l + w - 3, t + h / 2), 12))
            ok = await wait_for(
                lambda: asyncio.to_thread(lambda: any(a["type"] == "highlight" for a in anns(att["id"])))
            )
            hl = [a for a in anns(att["id"]) if a["type"] == "highlight"]
            check(
                "dragging the pen over text highlights it",
                ok and len(hl[0].get("text", "")) > 10,
                repr(hl[0].get("text", "")[:40]) if hl else "",
            )
            l, t, w, h = spans[1]
            await tool(page, "underline")
            await inp.pen(line((l + 3, t + h / 2), (l + w * 0.6, t + h / 2), 8))
            ok = await wait_for(
                lambda: asyncio.to_thread(lambda: any(a["type"] == "underline" for a in anns(att["id"])))
            )
            check("… and underlines it with the underline tool", ok)
            await shot(page, "02-pen-annotations")

            # a finger selection (long press) and the Highlight button
            await tool(page, "highlight")
            spans2 = await page.evaluate("""(() => [...document.querySelectorAll('.page-wrapper[data-page-number="1"] .textLayer span')]
                .map(s => s.getBoundingClientRect()).filter(r => r.width > 120).slice(8, 9).map(r => [r.left, r.top, r.width, r.height]))()""")
            l, t, w, h = spans2[0]
            await inp.finger([(l + 5, t + h / 2)])  # the finger is the last pointer …
            await page.evaluate(
                """([x, y]) => {           // … and the long press selected this line
                const s = document.elementFromPoint(x, y); const r = document.createRange();
                r.selectNodeContents(s); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); }""",
                [l + w / 2, t + h / 2],
            )
            ok = await wait_for(
                lambda: page.evaluate("document.querySelectorAll('.zc-sel-preview rect').length > 0"), timeout=0.2
            )
            check("a finger selection is shown in the highlight colour at once (before the finger is lifted)", ok)
            ok = await wait_for(lambda: page.evaluate("!!document.querySelector('.zc-mark-pill button')"), timeout=3)
            check("a finger selection offers a Highlight button", ok)
            n_hl = len([a for a in anns(att["id"]) if a["type"] == "highlight"])
            await page.evaluate("getSelection().removeAllRanges()")  # a tap elsewhere may clear the selection
            await page.tap(".zc-mark-pill button")
            ok = await wait_for(
                lambda: asyncio.to_thread(
                    lambda: len([a for a in anns(att["id"]) if a["type"] == "highlight"]) == n_hl + 1
                )
            )
            check("… which highlights it (even if the selection was lost meanwhile)", ok)

            # select with the pen → Delete; Undo / Redo buttons
            await tool(page, "select")
            bx = next(a for a in anns(att["id"]) if a["type"] == "box")
            bl, bt = bx["rects"][0][0], bx["rects"][0][1]
            box = await page_box(page)
            await inp.pen(
                [(box["x"] + bl * box["width"] + 1, box["y"] + (bt + bx["rects"][0][3] / 2) * box["height"])]
            )  # its left edge
            ok = await wait_for(lambda: page.evaluate("!!document.querySelector('.zc-sel-pill [data-del]')"), timeout=3)
            check("tapping an annotation with the pen selects it and offers Delete", ok)
            await shot(page, "03-selected")
            await page.tap(".zc-sel-pill [data-del]")
            ok = await wait_for(lambda: asyncio.to_thread(lambda: not any(a["type"] == "box" for a in anns(att["id"]))))
            check("… and Delete removes it", ok)
            await page.tap("#anno-undo")
            ok = await wait_for(lambda: asyncio.to_thread(lambda: any(a["type"] == "box" for a in anns(att["id"]))))
            check("the Undo button brings it back", ok)
            await page.tap("#anno-redo")
            ok = await wait_for(lambda: asyncio.to_thread(lambda: not any(a["type"] == "box" for a in anns(att["id"]))))
            check("the Redo button removes it again", ok)

            # eraser: drag the pen across the ink
            await tool(page, "eraser")
            ink = next(a for a in anns(att["id"]) if a["type"] == "ink")
            mx, my = ink["path"][len(ink["path"]) // 2]
            box = await page_box(page)
            cx, cy = box["x"] + mx * box["width"], box["y"] + my * box["height"]
            await inp.pen(line((cx - 40, cy - 60), (cx + 40, cy + 60), 16))
            ok = await wait_for(lambda: asyncio.to_thread(lambda: not any(a["type"] == "ink" for a in anns(att["id"]))))
            check("dragging the eraser across a drawing removes it", ok)

            # snapshot with the pen
            await tool(page, "select")
            await page.tap("#zc-snap-btn")
            box = await page_box(page)
            await inp.pen(
                line(
                    (box["x"] + box["width"] * 0.1, box["y"] + box["height"] * 0.5),
                    (box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.65),
                    8,
                )
            )
            notes = Path(DATA) / "notes" / "papers"
            ok = await wait_for(
                lambda: asyncio.to_thread(
                    lambda: notes.exists() and any("*Figure:" in f.read_text() for f in notes.glob("*.md"))
                )
            )
            check("a snapshot can be cut with the pen", ok)

            # ---- pinch (Safari reports gesture events) --------------------------------------
            ctx2 = await browser.new_context(**ipad)
            await ctx2.add_init_script("window.ongesturechange = null;")  # make the viewer use Safari's events
            page2 = await ctx2.new_page()
            page2.on("pageerror", lambda e: errors.append(str(e)))
            await open_viewer(page2, viewer(item, att))
            cont = await page2.evaluate(
                "(() => { const r = window.zcViewer.container.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()"
            )
            px, py = cont[0] + cont[2] * 0.7, cont[1] + cont[3] * 0.4
            before = await spot(page2, px, py)
            s0 = await page2.evaluate("window.zcViewer.scale")
            await page2.evaluate(
                """([x, y]) => {
                const fire = (type, scale) => { const e = new Event(type, { cancelable: true });
                    Object.defineProperties(e, { clientX: { value: x }, clientY: { value: y }, scale: { value: scale } });
                    window.dispatchEvent(e); return e.defaultPrevented; };
                window.__prevented = fire('gesturestart', 1);
                for (let k = 1; k <= 10; k++) fire('gesturechange', 1 + k * 0.1);
                window.__during = [window.zcViewer.scale, window.zcViewer.container.style.transform];
                fire('gestureend', 2); }""",
                [px, py],
            )
            during = await page2.evaluate("window.__during")
            check(
                "while the fingers move, the pages are only scaled on screen (not laid out and drawn again)",
                during[0] == s0 and "scale(2" in during[1],
                str(during),
            )
            check(
                "… and the transform is gone once the pinch ends",
                await page2.evaluate("window.zcViewer.container.style.transform") == "",
            )
            s1 = await page2.evaluate("window.zcViewer.scale")
            after = await spot(page2, px, py)
            check("a pinch zooms the pages (Safari gesture events)", abs(s1 / s0 - 2) < 0.01, f"{s0} → {s1}")
            check(
                "… keeping the spot between the fingers in place",
                before
                and after
                and before[0] == after[0]
                and abs(before[1] - after[1]) < 0.01
                and abs(before[2] - after[2]) < 0.01,
                f"{before} → {after}",
            )
            check("… instead of zooming the browser page", await page2.evaluate("window.__prevented"))
            await ctx2.close()

            # ---- pinch with two touch pointers (other touch browsers) ----------------------------
            ctx3 = await browser.new_context(**ipad)
            page3 = await ctx3.new_page()
            page3.on("pageerror", lambda e: errors.append(str(e)))
            await open_viewer(page3, viewer(item, att))
            inp3 = Input(await ctx3.new_cdp_session(page3))
            s0 = await page3.evaluate("window.zcViewer.scale")
            cx, cy = 420, 600
            await inp3.two_fingers([(cx - 60, cy), (cx + 60, cy)], [(cx - 150, cy), (cx + 150, cy)])
            await page3.wait_for_timeout(300)
            s1 = await page3.evaluate("window.zcViewer.scale")
            check("two fingers spreading zoom in (pointer events)", s1 > s0 * 1.5, f"{s0} → {s1}")
            await ctx3.close()

            # ---- canvases on a tablet --------------------------------------------------------
            ctx4 = await browser.new_context(**ipad)
            page4 = await ctx4.new_page()
            page4.on("pageerror", lambda e: errors.append(str(e)))
            await page4.goto(viewer(long_item, long_att))
            await page4.wait_for_function("document.querySelectorAll('.page-wrapper canvas').length > 0")
            for _ in range(16):
                await page4.tap("#zoom-in")
            await page4.wait_for_timeout(2500)
            for _k in range(6):
                await page4.evaluate("window.zcViewer.container.scrollTop += 1500")
                await page4.wait_for_timeout(400)
            await page4.wait_for_timeout(1500)
            info = await page4.evaluate("""(() => { const cs = [...document.querySelectorAll('.page-wrapper canvas:not(.zc-detail)')];
                return [cs.length, Math.max(...cs.map(c => c.width * c.height))]; })()""")
            check("a tablet keeps at most 8 pages drawn", info[0] <= 8, f"{info[0]} canvases")
            check(
                "… each at most 8 million pixels (Safari's limit is 16.7)",
                info[1] <= 8e6 + 1,
                f"{info[1] / 1e6:.1f} Mpx",
            )
            await shot(page4, "04-zoomed")
            for _ in range(30):
                await page4.tap("#zoom-in")
            await page4.wait_for_timeout(3000)
            deep = await page4.evaluate("""(() => { const d = [...document.querySelectorAll('.page-wrapper canvas.zc-detail')];
                const view = window.zcViewer.container.getBoundingClientRect();
                return [window.zcViewer.scale, d.length, Math.max(0, ...d.map(c => c.width * c.height)),
                        Math.max(0, ...d.map(c => c.width / c.getBoundingClientRect().width)),
                        d.some(c => { const r = c.getBoundingClientRect(); return r.left <= view.left + 1 && r.right >= view.right - 1
                                                                            && r.top <= view.top + 1 && r.bottom >= view.bottom - 1; })]; })()""")
            check("zoom goes up to 10×", deep[0] >= 9.99, str(deep[0]))
            check(
                "… and the visible part of the page is drawn sharp (a canvas at the screen's resolution)",
                deep[1] >= 1 and deep[3] >= 1.99 and deep[4],
                str(deep),
            )
            check("… within the canvas limit", deep[2] <= 8e6 + 1, f"{deep[2] / 1e6:.1f} Mpx")
            await shot(page4, "05-deep-zoom")
            await ctx4.close()

            check("no JavaScript errors", not errors, errors[:3])
            await browser.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
