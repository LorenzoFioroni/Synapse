"""Browser checklist for annotations as records: each kind is created, saved on its
own, drawn again after a reload and at another zoom; eraser, Delete, undo/redo and moving a text
box work; two PDFs of one item and two tabs on one PDF keep their own work; each tool starts with
its colour from Settings and then the one last used with it; highlights snap to whole words (not
with Alt); an area of the page can be highlighted; a selected annotation's colour can be changed;
annotations saved by the old viewer are converted and shown.

    python tests/e2e/annotations_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8777 with a throw-away data folder; your library is not touched.
"""

import asyncio
import json
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

PORT = 8777
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-annotations-e2e-")
PDF = APP / "tests" / "e2e" / "fixtures" / "twocolumn.pdf"
LEGACY = json.loads((APP / "tests" / "fixtures" / "legacy_annotations.json").read_text())["annotations"]
SHOTS = os.environ.get("SHOTS")
mod = "Meta" if sys.platform == "darwin" else "Control"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


def anns(att_id):
    return requests.get(f"{BASE}/api/attachments/{att_id}/annotations").json()["annotations"]


async def wait_for(fn, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if await fn():
            return True
        await asyncio.sleep(0.15)
    return False


async def until_count(att_id, n):
    return await wait_for(lambda: asyncio.to_thread(lambda: len(anns(att_id)) == n))


def viewer(item, att, with_att=True):
    q = {"file": f"/pdfs/{att['url']}", "paper_id": item["id"], "title": item["title"]}
    if with_att:
        q["att"] = att["id"]
    return f"{BASE}/viewer.html?{urlencode(q)}"


async def open_viewer(page, url):
    await page.goto(url)
    await page.wait_for_function("document.querySelectorAll('.page-wrapper .textLayer span').length > 20")
    await page.evaluate("window.zcAnnotations.ready")
    await page.wait_for_timeout(300)


async def drag(page, a, b, steps=10):
    await page.mouse.move(*a)
    await page.mouse.down()
    await page.mouse.move(*b, steps=steps)
    await page.mouse.up()


async def tool(page, name):
    await page.evaluate(f"""(() => {{ const b = document.querySelector('.tool-btn[data-tool="{name}"]');
        if (!b.classList.contains('active')) b.click(); }})()""")


# A run of the text layer of at least three words, and the screen points of a word's second
# letter: dragging between two of those starts and ends a selection inside words.
WORD_POINTS = """(skip) => {
  const spans = [...document.querySelectorAll('.page-wrapper[data-page-number="1"] .textLayer span')];
  const fit = spans.filter((s) => s.firstChild && /^\\S{3,}\\s+\\S+\\s+\\S{3,}/.test(s.textContent));
  const span = fit[skip];
  const text = span.firstChild.textContent;
  const words = [...text.matchAll(/\\S+/g)];
  const at = (i) => {
    const r = document.createRange();
    r.setStart(span.firstChild, i); r.setEnd(span.firstChild, i + 1);
    const b = r.getBoundingClientRect();
    return [b.left + b.width / 2, b.top + b.height / 2];
  };
  return { text, words: words.map((m) => m[0]), a: at(words[0].index + 1), b: at(words[2].index + 1) };
}"""


async def color_of(page):
    return await page.input_value("#tool-color")


async def text_lines(page, n=12):
    spans = page.locator('.page-wrapper[data-page-number="1"] .textLayer span')
    out = []
    for i in range(min(await spans.count(), 60)):
        b = await spans.nth(i).bounding_box()
        if b and b["width"] > 40:
            out.append(b)
        if len(out) >= n:
            break
    return out


def setup_library():
    """Items with PDFs, and one whose annotations were saved by the old viewer."""
    from backend import create_app
    from backend.config import Config

    s = create_app(Config.from_env(DATA), start_indexer=False).extensions["synapse"]
    made = {}
    for title, n_pdfs in (("Annotated paper", 2), ("Legacy paper", 1), ("Tools paper", 1)):
        item = s.papers.create({"title": title})
        atts = []
        for k in range(n_pdfs):
            name = f"{item['id']}_{k}.pdf"
            shutil.copy(PDF, s.config.pdf_dir / name)
            atts.append(s.papers.add_attachment(item["id"], f"part{k}.pdf", name, "pdf"))
        made[title] = (item, atts)
    with s.db.session() as c:
        c.execute("UPDATE papers SET annotations = ? WHERE id = ?", (json.dumps(LEGACY), made["Legacy paper"][0]["id"]))
        c.execute("DELETE FROM annotation_migration")
    return made


async def main():
    guard(PORT)
    made = setup_library()
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
        item, (att, att2) = made["Annotated paper"]
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context(viewport={"width": 1300, "height": 1000})
            page = await ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await open_viewer(page, viewer(item, att))
            lines = await text_lines(page)
            w1 = await page.locator('.page-wrapper[data-page-number="1"]').bounding_box()

            # ---- each kind, saved on its own
            await tool(page, "highlight")
            await drag(
                page,
                (lines[2]["x"] + 2, lines[2]["y"] + lines[2]["height"] / 2),
                (lines[3]["x"] + lines[3]["width"] - 2, lines[3]["y"] + lines[3]["height"] / 2),
            )
            ok = await until_count(att["id"], 1)
            hl = anns(att["id"])[0] if ok else {}
            check(
                "highlight: saved with the words and one rect per line",
                ok and hl.get("type") == "highlight" and len(hl.get("text", "")) > 10 and len(hl.get("rects", [])) >= 2,
                str(hl)[:160],
            )
            await tool(page, "underline")
            await drag(
                page,
                (lines[6]["x"] + 2, lines[6]["y"] + lines[6]["height"] / 2),
                (lines[6]["x"] + lines[6]["width"] - 2, lines[6]["y"] + lines[6]["height"] / 2),
            )
            check("underline saved", await until_count(att["id"], 2))
            await tool(page, "box")
            await drag(page, (w1["x"] + 100, w1["y"] + 520), (w1["x"] + 300, w1["y"] + 640))
            check("box saved", await until_count(att["id"], 3))
            await tool(page, "draw")
            await drag(page, (w1["x"] + 360, w1["y"] + 520), (w1["x"] + 520, w1["y"] + 620), steps=14)
            check("drawing saved", await until_count(att["id"], 4))
            await tool(page, "text")
            await page.mouse.click(w1["x"] + 120, w1["y"] + 720)
            await page.wait_for_timeout(150)
            await page.keyboard.type("Hello note")
            await tool(page, "select")
            await page.mouse.click(w1["x"] + 600, w1["y"] + 40)
            check("text box saved when it loses focus", await until_count(att["id"], 5))
            kinds = sorted(a["type"] for a in anns(att["id"]))
            check("all five kinds are records", kinds == ["box", "highlight", "ink", "text", "underline"], str(kinds))
            txt = next(a for a in anns(att["id"]) if a["type"] == "text")
            check("the text box keeps its text", txt["text"] == "Hello note", txt["text"])
            await shot(page, "n1-annotations")

            # ---- reload and zoom
            hl_box = await page.locator(f'[data-id="{hl["id"]}"]').bounding_box()
            await page.reload()
            await open_viewer(page, page.url)
            n = await page.evaluate("document.querySelectorAll('.anno-layer .anno').length")
            check("after a reload all five are drawn again", n == 5, str(n))
            hl_box2 = await page.locator(f'[data-id="{hl["id"]}"]').bounding_box()
            check(
                "in the same place",
                abs(hl_box["x"] - hl_box2["x"]) < 1.5 and abs(hl_box["width"] - hl_box2["width"]) < 1.5,
                f"{hl_box} {hl_box2}",
            )
            await page.click("#zoom-in")
            await page.wait_for_timeout(700)
            hl_box3 = await page.locator(f'[data-id="{hl["id"]}"]').bounding_box()
            ratio = hl_box3["width"] / hl_box2["width"]
            check("zooming scales the drawings with the page", abs(ratio - 1.75 / 1.5) < 0.03, f"{ratio:.3f}")
            await page.click("#zoom-out")
            await page.wait_for_timeout(700)

            # ---- eraser, undo, redo, Delete
            box = next(a for a in anns(att["id"]) if a["type"] == "box")
            await tool(page, "eraser")
            bb = await page.locator(f'[data-id="{box["id"]}"] rect').bounding_box()
            await page.mouse.click(bb["x"] + 1, bb["y"] + bb["height"] / 2)
            check("the eraser deletes one annotation", await until_count(att["id"], 4))
            await tool(page, "select")
            await page.keyboard.press(f"{mod}+z")
            ok = await until_count(att["id"], 5)
            check("undo brings it back with the same id", ok and box["id"] in [a["id"] for a in anns(att["id"])])
            await page.keyboard.press(f"{mod}+Shift+z")
            check("redo deletes it again", await until_count(att["id"], 4))
            await page.keyboard.press(f"{mod}+z")
            await until_count(att["id"], 5)
            hb = await page.locator(f'[data-id="{hl["id"]}"] rect').first.bounding_box()
            await page.mouse.click(hb["x"] + 3, hb["y"] + hb["height"] / 2)
            await page.keyboard.press("Delete")
            check("select + Delete removes it", await until_count(att["id"], 4))
            await page.keyboard.press(f"{mod}+z")
            check("and undo restores it", await until_count(att["id"], 5))

            # ---- move a text box by its frame
            before = txt["rects"][0][:2]
            frame = await page.locator(f'[data-id="{txt["id"]}"] .anno-text-frame').bounding_box()
            await drag(page, (frame["x"] + 2, frame["y"] + 2), (frame["x"] + 82, frame["y"] + 42))
            moved = await wait_for(
                lambda: asyncio.to_thread(
                    lambda: next(a for a in anns(att["id"]) if a["id"] == txt["id"])["rects"][0][:2] != before
                )
            )
            after = next(a for a in anns(att["id"]) if a["id"] == txt["id"])["rects"][0]
            check(
                "dragging a text box's frame moves it",
                moved and after[0] > before[0] and after[1] > before[1],
                f"{before} → {after[:2]}",
            )

            # ---- the other PDF of the same item
            p2 = await ctx.new_page()
            await open_viewer(p2, viewer(item, att2))
            n2 = await p2.evaluate("document.querySelectorAll('.anno-layer .anno').length")
            check("the item's second PDF shows none of them", n2 == 0, str(n2))
            w2 = await p2.locator('.page-wrapper[data-page-number="1"]').bounding_box()
            await tool(p2, "box")
            await drag(p2, (w2["x"] + 100, w2["y"] + 100), (w2["x"] + 200, w2["y"] + 200))
            ok = await until_count(att2["id"], 1)
            check("and keeps its own", ok and len(anns(att["id"])) == 5)
            await p2.close()

            # ---- two tabs on the same PDF
            tab_b = await ctx.new_page()
            await open_viewer(tab_b, viewer(item, att))
            wb = await tab_b.locator('.page-wrapper[data-page-number="2"]').bounding_box()
            await tab_b.evaluate("document.querySelector('.page-wrapper[data-page-number=\"2\"]').scrollIntoView()")
            await tab_b.wait_for_timeout(300)
            wb = await tab_b.locator('.page-wrapper[data-page-number="2"]').bounding_box()
            await tool(tab_b, "box")
            await drag(tab_b, (wb["x"] + 100, wb["y"] + 100), (wb["x"] + 220, wb["y"] + 180))
            await page.bring_to_front()
            await tool(page, "draw")
            await drag(page, (w1["x"] + 380, w1["y"] + 300), (w1["x"] + 480, w1["y"] + 360))
            ok = await until_count(att["id"], 7)
            check("two tabs on one PDF both keep their work", ok, str(len(anns(att["id"]))))
            await page.evaluate("window.zcAnnotations.refresh()")
            n = await page.evaluate("document.querySelectorAll('.anno-layer .anno').length")
            check("coming back to a tab shows what the other tab added", n == 7, str(n))
            await tab_b.close()

            # ---- an old viewer link without ?att=
            await open_viewer(page, viewer(item, att, with_att=False))
            check(
                "a link without the attachment id still finds it",
                await page.evaluate("window.zcAnnotations.attachment()") == att["id"]
                and await page.evaluate("document.querySelectorAll('.anno-layer .anno').length") == 7,
            )

            # ---- each tool's colour: from Settings, then the one last used with it
            titem, (tatt,) = made["Tools paper"]
            requests.put(f"{BASE}/api/settings", json={"annotation_color_underline": "#00aa00"})
            await open_viewer(page, viewer(titem, tatt))
            await tool(page, "underline")
            check(
                "a tool starts with its colour from Settings", await color_of(page) == "#00aa00", await color_of(page)
            )
            await page.evaluate(
                "(() => { const c = document.getElementById('tool-color'); c.value = '#123456';"
                " c.dispatchEvent(new Event('input')); })()"
            )
            await tool(page, "highlight")
            hl_color = await color_of(page)
            await tool(page, "underline")
            check("and keeps the colour last used with it", await color_of(page) == "#123456" and hl_color == "#ffff00")
            await page.reload()
            await open_viewer(page, page.url)
            await tool(page, "underline")
            check("also after a reload", await color_of(page) == "#123456", await color_of(page))
            requests.put(f"{BASE}/api/settings", json={"annotation_color_underline": "#0000ff"})
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
            await page.wait_for_timeout(400)
            await tool(page, "select")
            await tool(page, "underline")
            check("a new default in Settings replaces it", await color_of(page) == "#0000ff", await color_of(page))

            # ---- highlights snap to whole words, unless Alt is held
            def word(w):
                return w.strip(".,;:()[]")

            await tool(page, "highlight")
            pts = await page.evaluate(WORD_POINTS, 0)
            await drag(page, pts["a"], pts["b"])
            ok = await until_count(tatt["id"], 1)
            snapped = anns(tatt["id"])[0]["text"] if ok else ""
            want = " ".join(pts["words"][:3])
            check(
                "a highlight starting and ending inside words takes them whole",
                snapped.startswith(pts["words"][0]) and snapped.endswith(word(pts["words"][2])) and snapped in want,
                f"{snapped!r} in {want!r}",
            )
            pts2 = await page.evaluate(WORD_POINTS, 1)
            await page.keyboard.down("Alt")
            await drag(page, pts2["a"], pts2["b"])
            await page.keyboard.up("Alt")
            ok = await until_count(tatt["id"], 2)
            exact = next((a["text"] for a in anns(tatt["id"]) if a["text"] != snapped), "") if ok else ""
            check(
                "with Alt it takes exactly the characters selected",
                exact and not exact.startswith(pts2["words"][0]) and exact.startswith(pts2["words"][0][1:]),
                repr(exact),
            )

            # ---- an area of the page
            tw = await page.locator('.page-wrapper[data-page-number="1"]').bounding_box()
            await tool(page, "area")
            await drag(page, (tw["x"] + 60, tw["y"] + 300), (tw["x"] + 400, tw["y"] + 380))
            ok = await until_count(tatt["id"], 3)
            area = next((a for a in anns(tatt["id"]) if len(a["rects"]) == 1 and a["text"] not in (snapped, exact)), {})
            check(
                "an area highlight is a highlight of one rect, with the words inside it",
                ok and area.get("type") == "highlight" and len(area.get("text") or "") > 10,
                str(area)[:160],
            )

            # ---- changing the colour of a selected annotation
            await tool(page, "select")
            ab = await page.locator(f'[data-id="{area["id"]}"] rect').first.bounding_box()
            await page.mouse.click(ab["x"] + 20, ab["y"] + 20)
            await page.click('.zc-sel-pill [data-color="#5fb236"]')
            recolored = await wait_for(
                lambda: asyncio.to_thread(
                    lambda: next(a for a in anns(tatt["id"]) if a["id"] == area["id"])["color"] == "#5fb236"
                )
            )
            fill = await page.locator(f'[data-id="{area["id"]}"] rect').first.get_attribute("fill")
            check("a swatch in the selection's controls recolours it", recolored and fill == "#5fb236", str(fill))
            await shot(page, "n3-tools")
            await page.keyboard.press(f"{mod}+z")
            back = await wait_for(
                lambda: asyncio.to_thread(
                    lambda: next(a for a in anns(tatt["id"]) if a["id"] == area["id"])["color"] == area["color"]
                )
            )
            check("and undo gives it its colour back", back)

            # ---- annotations saved by the old viewer
            legacy, (latt,) = made["Legacy paper"]
            await open_viewer(page, viewer(legacy, latt))
            kinds = sorted(a["type"] for a in await page.evaluate("window.zcAnnotations.list()"))
            check(
                "old annotations were converted",
                kinds == ["box", "box", "highlight", "ink", "text", "underline"],
                str(kinds),
            )
            drawn = await page.evaluate("document.querySelectorAll('.anno-layer .anno').length")
            check("and are drawn", drawn == 6, str(drawn))
            text = await page.evaluate("document.querySelector('.anno-text-input')?.innerText")
            check(
                "the old text box keeps both lines", text and "Check this" in text and "second line" in text, repr(text)
            )
            await shot(page, "n2-legacy")
            check("no JavaScript errors", not errors, "; ".join(errors[:3]))
            await browser.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
