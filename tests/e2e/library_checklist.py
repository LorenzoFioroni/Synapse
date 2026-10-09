"""Browser checklist for library integration (plan M6): key rename with preview, notes in search,
trashed items, Quote/Snapshot to note from the PDF viewer.

    python tests/e2e/library_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8768 with a throw-away data folder; your library is not touched.
"""
import asyncio, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from tests.conftest import make_pdf  # noqa: E402

PORT = 8768
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-library-e2e-")
NOTES = Path(DATA) / "notes"
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


def make_pages_pdf(path: Path, texts: list[str]) -> Path:
    """A PDF with one page per text (no external deps)."""
    n = len(texts)
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [" + b" ".join(f"{3 + 2 * i} 0 R".encode() for i in range(n)) + b"] /Count " + str(n).encode() + b" >>"]
    font = 3 + 2 * n
    for i, t in enumerate(texts):
        stream = f"BT /F1 24 Tf 72 700 Td ({t}) Tj ET".encode("latin-1")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
                    f"/Resources << /Font << /F1 {font} 0 R >> >> >>".encode())
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


def read(rel):
    p = NOTES / rel
    return p.read_text() if p.exists() else ""


def setup():
    cui = requests.post(f"{BASE}/api/items", json={"authors": "Cui, Jian", "title": "Variational operators", "year": 2015}).json()
    pdf = make_pdf(Path(DATA) / "upload.pdf", "Variational results hold for open systems")
    with open(pdf, "rb") as fh:
        att = requests.post(f"{BASE}/api/items/{cui['id']}/attachments", files={"file": ("paper.pdf", fh, "application/pdf")}).json()
    key = cui["citation_key"]
    requests.post(f"{BASE}/api/notes/note", json={"item_id": cui["id"]})
    requests.put(f"{BASE}/api/notes/note", json={"path": "Review.md", "create": True,
                 "text": f"# Review\n\nMatrix product operators reach steady states [@{key}, p. 3].\n\nSee [[{key}]].\n"})
    return cui, key, att["attachments"][0]


async def main():
    env = dict(os.environ, ZCLONE_DATA_DIR=DATA)
    srv = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--no-browser"], cwd=APP, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    try:
        cui, key, att = setup()
        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(viewport={"width": 1440, "height": 950})
            page = await ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(BASE + "/")
            await page.wait_for_timeout(800)

            # ---- key rename: cancel, then confirm
            await page.click(f'.row[data-id="{cui["id"]}"]')
            field = page.locator('[data-field="citation_key"]')
            await field.click()
            await page.keyboard.press('ControlOrMeta+a')
            await page.keyboard.type('cui2015')
            await page.keyboard.press('Enter')
            await page.wait_for_selector('.dialog')
            dlg = await page.text_content('.dialog')
            check("rename preview lists the notes and the file rename", "Review" in dlg and "papers/cui2015.md" in dlg and "[@cui2015, p. 3]" in dlg, dlg[:200])
            await shot(page, "1-rename-preview")
            await page.click('.dialog button:has-text("Cancel")')
            await page.wait_for_timeout(500)
            item = requests.get(f"{BASE}/api/items/{cui['id']}").json()
            check("cancel keeps the key and the notes", item["citation_key"] == key and f"[@{key}, p. 3]" in read("Review.md")
                  and await field.text_content() == key)
            await field.click()
            await page.keyboard.press('ControlOrMeta+a')
            await page.keyboard.type('cui2015')
            await page.keyboard.press('Enter')
            await page.wait_for_selector('.dialog')
            await page.click('.dialog button:has-text("Change key")')
            await page.wait_for_timeout(800)
            review = read("Review.md")
            check("confirm rewrites the notes", "[@cui2015, p. 3]" in review and "[[cui2015]]" in review and key not in review, review)
            check("paper note renamed with its front-matter", (NOTES / "papers/cui2015.md").exists() and "paper: cui2015" in read("papers/cui2015.md"))
            check("backup made", len(list((Path(DATA) / "backups").glob("notes-*.tar.gz"))) == 1)
            key = "cui2015"

            # ---- library search through notes
            await page.fill('#search-input', 'steady states')
            await page.click('[data-a="mode"]')
            await page.click('.menu >> text=All Fields')
            await page.wait_for_timeout(800)
            rows = await page.locator('.items-body .row[data-id]').count()
            snip = await page.locator('.items-body .row.snippet').all_text_contents()
            check("library search finds the item through a note", rows == 1 and any("Note" in s and "steady" in s for s in snip), snip)
            await shot(page, "2-search")
            await page.fill('#search-input', '')
            await page.keyboard.press('Escape')

            # ---- notes filter: text matches
            await page.click('[data-act="open-notes"]')
            await page.fill('.notes-filter input', 'operators')
            await wait_for(lambda: page.locator('.nt-hit').count())
            hits = await page.locator('.nt-hit .nm').all_text_contents()
            check("notes filter shows text matches", "Review" in hits, hits)
            await page.fill('.notes-filter input', '')

            # ---- trashed item: chip struck through
            requests.post(f"{BASE}/api/items/trash", json={"ids": [cui["id"]]})
            await page.click('.nt-row.note[data-path="Review.md"]')
            await page.click('[data-mode="live"]')
            await page.keyboard.press('ControlOrMeta+End')
            await wait_for(lambda: page.locator('.cm-lp-chip.cite.trashed').count())
            check("citations of a trashed item are struck through", await page.locator('.cm-lp-chip.cite.trashed').count() == 1)
            requests.post(f"{BASE}/api/items/restore", json={"ids": [cui["id"]]})

            # ---- viewer: quote into the open note
            viewer = await ctx.new_page()
            viewer.on("pageerror", lambda e: errors.append("viewer: " + str(e)))
            await viewer.goto(f"{BASE}/viewer.html?file=/pdfs/{att['url']}&paper_id={cui['id']}&title=x")
            await viewer.wait_for_function("window.zcViewerBridge && document.querySelector('.textLayer span')", timeout=15000)
            await viewer.evaluate("""() => {
                const span = [...document.querySelectorAll('.textLayer span')].find(s => s.textContent.includes('Variational'));
                const r = document.createRange(); r.selectNodeContents(span);
                const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
                document.getElementById('pdf-container').dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
            }""")
            await viewer.wait_for_selector('.zc-pill [data-q]')
            await shot(viewer, "3-quote-pill")
            await viewer.click('.zc-pill [data-q]')
            await viewer.wait_for_selector('.zc-menu')
            menu = await viewer.text_content('.zc-menu')
            check("viewer offers the open note and the paper note", "Review" in menu and f"@{key}" in menu, menu)
            await viewer.click('.zc-menu button:has-text("Review")')
            await page.wait_for_timeout(2600)
            want = f"> Variational results hold for open systems [@{key}, p. 1]"
            check("quote inserted into the open note (and saved)", want in (await page.evaluate("window.zcNotes.text()")) and want in read("Review.md"))

            # ---- viewer: quote into the paper note
            await viewer.evaluate("""() => {
                const span = [...document.querySelectorAll('.textLayer span')].find(s => s.textContent.includes('Variational'));
                const r = document.createRange(); r.selectNodeContents(span);
                const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
                document.getElementById('pdf-container').dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
            }""")
            await viewer.click('.zc-pill [data-q]')
            await viewer.wait_for_selector('.zc-menu')
            await viewer.click('.zc-menu button:has-text("Paper note")')
            await viewer.wait_for_timeout(800)
            check("quote appended to the paper note", want in read("papers/cui2015.md"))

            # ---- viewer: snapshot
            await viewer.click('#zc-snap-btn')
            box = await viewer.locator('.page-wrapper').first.bounding_box()
            await viewer.mouse.move(box["x"] + 40, box["y"] + 40)
            await viewer.mouse.down()
            await viewer.mouse.move(box["x"] + 300, box["y"] + 200, steps=5)
            await viewer.mouse.up()
            await viewer.wait_for_selector('.zc-menu')
            await viewer.click('.zc-menu button:has-text("Paper note")')
            await viewer.wait_for_timeout(1000)
            paper = read("papers/cui2015.md")
            imgs = list((NOTES / "attachments").glob("*cui2015-p1*.png"))
            check("snapshot saved as an image and added with its citation", imgs and f"](attachments/{imgs[0].name})" in paper
                  and f"*Figure: [@{key}, p. 1]*" in paper, paper[-200:])
            if imgs:
                from struct import unpack
                w, h = unpack(">II", imgs[0].read_bytes()[16:24])
                check("snapshot has the selected size (≥ canvas resolution)", w >= 250 and h >= 150, (w, h))
            # ---- bugs.md #2: a link to a page opens the PDF at that page
            pdf3 = make_pages_pdf(Path(DATA) / "three.pdf", ["Page one", "Page two", "Page three"])
            with open(pdf3, "rb") as fh:
                att3 = requests.post(f"{BASE}/api/items/{cui['id']}/attachments", files={"file": ("three.pdf", fh, "application/pdf")}).json()["attachments"][0]
            v3 = await ctx.new_page()
            await v3.goto(f"{BASE}/viewer.html?file=/pdfs/{att3['url']}&paper_id={cui['id']}&title=x&page=3")
            await v3.wait_for_selector('.page-wrapper[data-page-number="3"] canvas', timeout=15000)
            await v3.wait_for_timeout(600)
            top = await v3.evaluate("""() => {
                const c = document.getElementById('pdf-container').getBoundingClientRect();
                const w = document.querySelector('.page-wrapper[data-page-number="3"]').getBoundingClientRect();
                return Math.abs(w.top - c.top); }""")
            num = await v3.text_content('#page-num-text')
            check("viewer opens at the requested page", top < 40 and num.strip().startswith("3"), (top, num))
            await v3.goto(f"{BASE}/viewer.html?file=/pdfs/{att3['url']}&paper_id={cui['id']}&title=x")
            await v3.wait_for_selector('.page-wrapper[data-page-number="3"] canvas', timeout=15000)
            st = await v3.evaluate("document.getElementById('pdf-container').scrollTop")
            check("without page= it stays at the top", st < 5, st)
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
