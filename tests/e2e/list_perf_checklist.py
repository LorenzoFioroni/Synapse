"""Browser checklist for big libraries (issue #24): with 10,000 items the list opens fast, keeps
few rows in the DOM, moves the selection quickly, and small changes (editing a field, tagging,
trashing) do not reload the whole list.

    python tests/e2e/list_perf_checklist.py

Starts its own server on port 8779 with a throw-away data folder of 10,000 synthetic items (with
150-word abstracts); your library is not touched.
"""
import asyncio, random, shutil, string, subprocess, sys, tempfile, time
from pathlib import Path
import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from tests.e2e._guard import guard  # noqa: E402

PORT = 8779
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-listperf-e2e-")
N = 10_000
results = []
mod = "Meta" if sys.platform == "darwin" else "Control"


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


def build_library():
    from backend import create_app
    from backend.config import Config
    random.seed(7)
    words = ["".join(random.choices(string.ascii_lowercase, k=random.randint(3, 10))) for _ in range(3000)]
    s = create_app(Config.from_env(DATA), start_indexer=False).extensions["synapse"]
    s.settings.update({"backup_interval": "off"} if "backup_interval" in s.settings.all() else {})
    s.papers.create_many([{
        "title": " ".join(random.choices(words, k=8)).title(), "year": random.randint(1980, 2026),
        "authors": "; ".join(f"{random.choice(words).title()}, {random.choice(words).title()}" for _ in range(3)),
        "journal": "Physical Review B", "abstract": " ".join(random.choices(words, k=150)),
    } for _ in range(N)])
    first = s.papers.create({"title": "Aaa the first paper", "year": 2026, "abstract": "An abstract that must show in the pane."})
    pdf = APP / "tests" / "e2e" / "fixtures" / "twocolumn.pdf"
    for k in range(3):
        name = f"{first['id']}_{k}.pdf"
        shutil.copy(pdf, s.config.pdf_dir / name)
        s.papers.add_attachment(first["id"], f"part{k}.pdf", name, "pdf")
    return first


async def main():
    guard(PORT)
    first = build_library()
    srv = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", DATA],
                           cwd=APP, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(150):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            errors, lists = [], []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("request", lambda r: lists.append(r.url) if "/api/items?" in r.url and "ids=" not in r.url else None)

            t0 = time.time()
            await page.goto(BASE)
            await page.wait_for_function("document.querySelector('.row[data-id]')", polling="raf", timeout=30000)
            ready = await page.evaluate("performance.now()") / 1000   # since the navigation started
            check("10,000 items: the list is ready in under 1 s", ready < 1.0, f"{ready:.2f} s")
            r = requests.get(BASE + "/api/items?lean=1")
            full = requests.get(BASE + "/api/items")
            check("the list payload leaves out abstracts and empty fields", len(r.content) < len(full.content) / 3,
                  f"{len(r.content) / 1e6:.1f} MB instead of {len(full.content) / 1e6:.1f} MB")
            in_dom = await page.evaluate("document.querySelectorAll('.items-body .row').length")
            check("only the rows near the viewport are in the DOM", in_dom < 200, str(in_dom))
            total_h = await page.evaluate("document.querySelector('.items-body').offsetHeight")
            check("but the list is as tall as all the rows", total_h > N * 20, str(total_h))

            # sort by title: the alphabetically first item comes first
            await page.click('.head-cell[data-col="title"]')
            await page.wait_for_timeout(300)
            top_title = await page.evaluate("document.querySelector('.items-body .row[data-id]').textContent")
            sort_dir = await page.evaluate("JSON.parse(localStorage.getItem('zc.sort') || '{}').dir")
            if sort_dir == "desc":
                await page.click('.head-cell[data-col="title"]')
                await page.wait_for_timeout(300)
                top_title = await page.evaluate("document.querySelector('.items-body .row[data-id]').textContent")
            check("sorting covers all rows, not just those in the DOM", "Aaa the first paper" in top_title, top_title[:60])

            # keyboard: select, move, measure
            await page.click(f'.row[data-id="{first["id"]}"] .c-year')
            await page.wait_for_timeout(300)
            step = await page.evaluate("""() => {
                const s = document.querySelector('.items-scroll');
                const t = performance.now();
                for (let i = 0; i < 30; i++) s.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
                void document.body.offsetHeight;
                return (performance.now() - t) / 30; }""")
            check("an arrow-key step takes under 16 ms", step < 16, f"{step:.1f} ms")
            await page.keyboard.press("End")
            await page.wait_for_timeout(300)
            last = await page.evaluate("""() => { const r = document.querySelector('.row.selected'); if (!r) return null;
                const b = r.getBoundingClientRect(), s = document.querySelector('.items-scroll').getBoundingClientRect();
                return { inView: b.top >= s.top && b.bottom <= s.bottom + 1, index: Number(r.dataset.index) }; }""")
            check("End selects the last item and shows it", last and last["inView"] and last["index"] == N, str(last))
            await page.keyboard.press("Home")
            await page.wait_for_timeout(300)

            # the item pane gets the abstract the list left out
            await page.click(f'.row[data-id="{first["id"]}"] .c-year')
            await page.wait_for_function("document.querySelector('#item-pane [data-field=\"abstract\"]')", timeout=5000)
            abstract = await page.evaluate("document.querySelector('#item-pane [data-field=\"abstract\"]').textContent")
            check("the item pane shows the abstract", "must show in the pane" in abstract, abstract[:60])

            # expanding attachments pushes the rows below down (no overlap)
            await page.keyboard.press("ArrowRight")
            await page.wait_for_timeout(300)
            gap = await page.evaluate(f"""(() => {{
                const row = document.querySelector('.row[data-id="{first['id']}"]');
                const kids = [...document.querySelectorAll('.row[data-parent="{first['id']}"]')];
                const next = kids.length ? kids[kids.length - 1].nextElementSibling : null;
                return {{ kids: kids.length, gap: next ? next.getBoundingClientRect().top - kids[kids.length - 1].getBoundingClientRect().bottom : null }}; }})()""")
            check("expanded attachment rows push the next item down", gap["kids"] == 3 and gap["gap"] is not None and abs(gap["gap"]) < 1.5, str(gap))
            await page.keyboard.press("ArrowLeft")

            # small changes: no full list reload
            lists.clear()
            await page.click('#item-pane [data-field="title"]')
            await page.keyboard.press(f"{mod}+a")
            await page.keyboard.type("Aaa renamed paper")
            t0 = time.time()
            async with page.expect_response(lambda r: r.request.method == "PATCH", timeout=15000):
                await page.keyboard.press("Enter")
            saved_in = time.time() - t0
            check("the save is answered at once, even after racing through the list", saved_in < 2, f"{saved_in:.2f} s")
            await page.wait_for_timeout(300)
            check("editing a field reloads no list", not lists, str(lists[:2]))
            saved = requests.get(f"{BASE}/api/items/{first['id']}").json()["title"]
            row_text = await page.evaluate(f"document.querySelector('.row[data-id=\"{first['id']}\"]')?.textContent")
            check("and the row shows the new title", saved == "Aaa renamed paper" and "Aaa renamed paper" in (row_text or ""), f"{saved!r} / {(row_text or '')[:40]!r}")
            r = requests.post(BASE + "/api/tags", json={"name": "keep"})
            lists.clear()
            await page.evaluate(f"import('/js/actions.js').then((A) => A.addTags(['{first['id']}'], ['keep']))")
            await page.wait_for_timeout(800)
            tags = requests.get(f"{BASE}/api/items/{first['id']}").json()["tags"]
            check("tagging reloads no list", not lists and "keep" in tags, str(lists[:2]))
            lists.clear()
            before = await page.evaluate("document.querySelector('.items-body').offsetHeight")
            await page.click(f'.row[data-id="{first["id"]}"] .c-year')
            await page.keyboard.press("Delete")
            await page.wait_for_timeout(800)
            gone = await page.evaluate(f"!document.querySelector('.row[data-id=\"{first['id']}\"]')")
            after = await page.evaluate("document.querySelector('.items-body').offsetHeight")
            check("moving to the Trash reloads no list and removes the row", not lists and gone and after < before, str(lists[:2]))
            check("no JavaScript errors", not errors, "; ".join(errors[:3]))
            await browser.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
