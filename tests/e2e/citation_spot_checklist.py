"""Browser checklist for precise PDF citations: a quote's or a snapshot's citation
opens the PDF at the exact spot — not just the top of the page — and flashes it for a moment.

    python tests/e2e/citation_spot_checklist.py        (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8791 with a throw-away data folder; your library is not touched.
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
sys.path.insert(0, str(APP))
from tests.e2e._guard import guard  # noqa: E402

PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-spot-e2e-")
NOTES = Path(DATA) / "notes"
SHOTS = os.environ.get("SHOTS")
results = []

FILLER = "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod"
# page 2: filler, then near the bottom the sentence we quote, broken over two lines with a hyphen
PAGE2 = (
    [FILLER] * 34
    + ["We find that energy transport is super-", "diffusive at infinite temperature in", "this chain."]
    + [FILLER] * 4
)


def make_lines_pdf(path: Path, pages: list[list[str]]) -> Path:
    """A letter-size PDF, one line of 11 pt text every 16 pt from the top of each page."""
    n = len(pages)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{3 + 2 * i} 0 R".encode() for i in range(n))
        + b"] /Count "
        + str(n).encode()
        + b" >>",
    ]
    font = 3 + 2 * n
    for i, lines in enumerate(pages):
        ops = "BT /F1 11 Tf 16 TL 72 740 Td " + " ".join(f"({t}) Tj T*" for t in lines) + " ET"
        stream = ops.encode("latin-1")
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font} 0 R >> >> >>".encode()
        )
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


async def wait_for(fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if await fn():
            return True
        await asyncio.sleep(0.15)
    return False


FLASH = """() => {
  const c = document.getElementById('pdf-container').getBoundingClientRect();
  return [...document.querySelectorAll('.zc-flash')].map((f) => {
    const r = f.getBoundingClientRect(), w = f.closest('.page-wrapper'), wr = w.getBoundingClientRect();
    return { page: +w.dataset.pageNumber, top: (r.top - wr.top) / wr.height, left: (r.left - wr.left) / wr.width,
             width: r.width / wr.width, height: r.height / wr.height, visible: r.top >= c.top && r.bottom <= c.bottom };
  });
}"""


async def main():
    guard(PORT)
    env = dict(os.environ, SYNAPSE_DATA_DIR=DATA)
    # PDFs open in browser tabs here, as these checks expect; tabs_checklist.py covers the app's own tabs
    (Path(DATA) / "settings.json").write_text('{"pdf_open_in": "browser"}', encoding="utf-8")
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser"],
        cwd=APP,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.5)
    try:
        item = requests.post(
            f"{BASE}/api/items", json={"authors": "Ljubotina, Marko", "title": "Superdiffusive transport", "year": 2023}
        ).json()
        key = item["citation_key"]
        pdf = make_lines_pdf(Path(DATA) / "paper.pdf", [["Page one", FILLER], PAGE2, ["Page three"]])
        with open(pdf, "rb") as fh:
            att = requests.post(
                f"{BASE}/api/items/{item['id']}/attachments", files={"file": ("paper.pdf", fh, "application/pdf")}
            ).json()["attachments"][0]
        viewer_url = f"{BASE}/viewer.html?file=/pdfs/{att['url']}&paper_id={item['id']}&title=x"

        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(viewport={"width": 1300, "height": 900})
            errors = []
            ctx.on("weberror", lambda e: errors.append(str(e.error)))

            # ---- make a quote and a snapshot from the viewer (they go to the paper note)
            v = await ctx.new_page()
            await v.goto(viewer_url + "&page=2")
            await v.wait_for_function(
                "() => (window.zcViewerBridge && document.querySelector('.page-wrapper[data-page-number=\"2\"] .textLayer span'))",
                timeout=15000,
            )
            await v.evaluate("""() => {
                const spans = [...document.querySelectorAll('.page-wrapper[data-page-number="2"] .textLayer span')];
                const a = spans.find((s) => s.textContent.startsWith('We find')), z = spans.find((s) => s.textContent.startsWith('diffusive'));
                const r = document.createRange(); r.setStart(a.firstChild, 0); r.setEnd(z.firstChild, z.textContent.length);
                const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
                document.getElementById('pdf-container').dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
            }""")
            await v.click(".zc-pill [data-q]")
            await wait_for(
                lambda: asyncio.sleep(
                    0,
                    (NOTES / "papers").exists()
                    and any("We find" in f.read_text() for f in (NOTES / "papers").glob("*.md")),
                )
            )
            # snapshot: a region in the lower half of page 2
            w2 = v.locator('.page-wrapper[data-page-number="2"]')
            await w2.scroll_into_view_if_needed()
            await v.click("#zc-snap-btn")
            box = await w2.bounding_box()
            x0, y0 = box["x"] + box["width"] * 0.1, box["y"] + box["height"] * 0.55
            await v.mouse.move(x0, y0)
            await v.mouse.down()
            await v.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.70, steps=5)
            await v.mouse.up()
            note = next((NOTES / "papers").glob("*.md"))
            await wait_for(lambda: asyncio.sleep(0, "*Figure:" in note.read_text()))
            text = note.read_text()
            check(
                "quote and snapshot are in the paper note, as before (no extra markup)",
                f"> We find that energy transport is superdiffusive at infinite temperature in [@{key}, p. 2]" in text
                and f"*Figure: [@{key}, p. 2]*" in text,
                text[-300:],
            )
            img = next((NOTES / "attachments").glob("*.png"))
            from subprocess import run

            region = run(
                [
                    "node",
                    "-e",
                    f"""
                import('{APP.as_posix()}/frontend/js/views/notes/citeTarget.js').then((m) => {{
                  const b = new Uint8Array(require('fs').readFileSync({str(img)!r}));
                  console.log(JSON.stringify(m.readRegion(b)));
                }})""",
                ],
                capture_output=True,
                text=True,
            ).stdout.strip()
            import json

            reg = json.loads(region or "null")
            check(
                "the snapshot PNG remembers its page and region",
                reg
                and reg["page"] == 2
                and abs(reg["rect"][0] - 0.1) < 0.02
                and abs(reg["rect"][1] - 0.55) < 0.02
                and abs(reg["rect"][2] - 0.5) < 0.02
                and abs(reg["rect"][3] - 0.15) < 0.02,
                region,
            )
            await v.close()

            # ---- read mode: click the quote's citation
            page = await ctx.new_page()
            page.on("console", lambda m: print("  console:", m.text) if m.type in ("error", "warning") else None)
            await page.goto(BASE + "/")
            await page.click('[data-act="open-notes"]')
            await page.click(f'.nt-row.note[data-path="{note.relative_to(NOTES).as_posix()}"]')
            await page.wait_for_selector(".cm-content")
            await page.click('[data-mode="read"]')
            await page.wait_for_selector(".note-read blockquote .cite")
            async with ctx.expect_page() as pop:
                await page.click(".note-read blockquote .cite")
            vq = await pop.value
            await vq.wait_for_load_state()
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(vq.url).query)
            check(
                "the viewer is opened at the quoted words",
                qs.get("q") == ["We find that energy transport is superdiffusive at infinite temperature in"]
                and qs.get("page") == ["2"],
                qs.get("q"),
            )
            await vq.wait_for_selector(".zc-flash", timeout=15000)
            f = await vq.evaluate(FLASH)
            # the quote is on lines 35–36 of 41: about 74–77 % down the page
            check(
                "the quote is flashed where it is on the page, as one box around both lines",
                len(f) == 1 and f[0]["page"] == 2 and 0.70 < f[0]["top"] < 0.78 and 0.035 < f[0]["height"] < 0.06,
                f,
            )
            check("…and scrolled into view (not the top of the page)", all(x["visible"] for x in f), f)
            await shot(vq, "s1-quote-flash")
            await vq.wait_for_timeout(3300)
            check("the flash goes away by itself", await vq.locator(".zc-flash").count() == 0)
            check(
                "nothing is saved as an annotation",
                requests.get(f"{BASE}/api/items/{item['id']}/annotations").json()["annotations"] == [],
            )
            await vq.close()

            # ---- read mode: the snapshot's citation
            async with ctx.expect_page() as pop:
                await page.click(".note-read :is(p, figure):has(img) .cite")
            vs = await pop.value
            await vs.wait_for_selector(".zc-flash", timeout=15000)
            rect = parse_qs(urlparse(vs.url).query).get("rect", [""])[0]
            check(
                "a snapshot's citation opens at its region",
                rect.count(",") == 3 and abs(float(rect.split(",")[0]) - 0.1) < 0.02,
                rect,
            )
            f = await vs.evaluate(FLASH)
            check(
                "…and flashes that region",
                len(f) == 1
                and f[0]["page"] == 2
                and abs(f[0]["top"] - 0.55) < 0.02
                and abs(f[0]["height"] - 0.15) < 0.02
                and f[0]["visible"],
                f,
            )
            await shot(vs, "s2-snapshot-flash")
            await vs.close()

            # ---- editor (live preview): Cmd/Ctrl+click on the quote's citation
            await page.click('[data-mode="live"]')
            await page.wait_for_selector(".cm-lp-chip.cite")
            mod = "Meta" if sys.platform == "darwin" else "Control"
            async with ctx.expect_page() as pop:
                await page.locator('.cm-line:has-text("We find") .cm-lp-chip.cite').click(modifiers=[mod])
            ve = await pop.value
            await ve.wait_for_selector(".zc-flash", timeout=15000)
            check("in the editor too", "q=We+find+that" in ve.url and await ve.locator(".zc-flash").count() == 1)
            await ve.close()

            # ---- hover card "PDF p. 2" button (on the editor's chip)
            await page.click(".cm-content h1, .cm-line >> nth=0")  # cursor elsewhere, editor focused
            await page.mouse.move(5, 5)
            await page.locator('.cm-line:has-text("We find") .cm-lp-chip.cite').hover()
            await page.wait_for_selector('.cite-card:not([hidden]) [data-cc="pdf"]')
            async with ctx.expect_page() as pop:
                await page.click('.cite-card [data-cc="pdf"]')
            vc = await pop.value
            await vc.wait_for_selector(".zc-flash", timeout=15000)
            check("and from the citation card's PDF button", "q=We+find+that" in vc.url)
            await vc.close()

            # ---- an ordinary citation still opens at the top of the page, without a flash
            requests.put(
                f"{BASE}/api/notes/note",
                json={"path": "Plain.md", "create": True, "text": f"Plain [@{key}, p. 2] cite.\n"},
            )
            await page.goto(BASE + "/")
            await page.click('[data-act="open-notes"]')
            await page.click('.nt-row.note[data-path="Plain.md"]')
            await page.click('[data-mode="read"]')
            async with ctx.expect_page() as pop:
                await page.click(".note-read .cite")
            vp = await pop.value
            await vp.wait_for_selector('.page-wrapper[data-page-number="2"] canvas', timeout=15000)
            await vp.wait_for_timeout(800)
            top = await vp.evaluate("""() => document.querySelector('.page-wrapper[data-page-number="2"]').getBoundingClientRect().top
                - document.getElementById('pdf-container').getBoundingClientRect().top""")
            check(
                "a plain citation opens at the top of its page, no flash",
                abs(top) < 40 and await vp.locator(".zc-flash").count() == 0 and "q=" not in vp.url,
                top,
            )
            check("no JavaScript errors", not errors, errors[:3])
            await b.close()
    finally:
        srv.terminate()
        shutil.rmtree(DATA, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
