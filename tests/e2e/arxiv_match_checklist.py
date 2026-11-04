"""Browser checklist for the arXiv Today match: the feed ordered by match, the item pane's arXiv Match
section (tier, one bar per kind of evidence), the reason for a paper citing only widely cited
library papers, a paper counting as opened only after it was shown for a while (and the papers
above it as passed over), and the learned weights in Settings.

    python tests/e2e/arxiv_match_checklist.py      (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8823 with a throw-away data folder; your library is not touched. The
plugin gets no categories, so it never asks arXiv: the day's papers are written into its database.
"""

import asyncio
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
PORT = 8823
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-arxiv-match-e2e-")
SHOTS = os.environ.get("SHOTS")
MOD = "Meta" if sys.platform == "darwin" else "Control"
PID = "arxiv-today"
FEED_DB = Path(DATA) / "plugin-data" / PID / "feed.sqlite"
SECTION = f".ip-ext[data-plugin='{PID}']"
results = []

LIBRARY = [
    ("Universality classes of non-Hermitian random matrices", "Hamazaki, Ryusuke; Kawabata, Kohei", "10.1/rmt"),
    ("Quantum computation and quantum information", "Nielsen, Michael A.; Chuang, Isaac L.", "10.1/nc"),
]
# arxiv, title, authors, references
PAPERS = [
    ("2611.00001", "Liouvillian Kramers degeneracy", ["Kohei Kawabata"], ["10.1/rmt", "10.1/nc"]),
    ("2611.00002", "A quantum network for image reconstruction", ["Ann Other"], ["10.1/nc"]),
    ("2611.00003", "Volcanic ash in the stratosphere", ["Ray Volcano"], []),
]


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra)


async def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        await page.screenshot(path=f"{SHOTS}/{name}.png")


def api(path, method="GET", **kw):
    r = requests.request(method, BASE + path, timeout=10, headers={"Origin": BASE}, **kw)
    r.raise_for_status()
    return r.json() if r.content else None


def feed(sql, args=()):
    with sqlite3.connect(FEED_DB) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(sql, args)]


def seed():
    day = datetime.date.today().isoformat()
    with sqlite3.connect(FEED_DB) as c:
        for arxiv, title, authors, refs in PAPERS:
            c.execute(
                "INSERT INTO papers (arxiv, day, kind, title, authors, abstract, categories, refs) "
                "VALUES (?, ?, 'new', ?, ?, '', '[\"quant-ph\"]', ?)",
                (arxiv, day, title, json.dumps(authors), json.dumps([{"doi": d} for d in refs])),
            )
        # a background where everyone cites the textbook
        for i in range(60):
            c.execute("INSERT INTO bg_papers (arxiv, day, refs) VALUES (?, ?, 5)", (f"bg{i}", day))
            c.execute("INSERT INTO bg_refs (arxiv, key) VALUES (?, 'doi:10.1/nc')", (f"bg{i}",))
        c.execute("INSERT INTO days (day, fetched_at, categories, notified) VALUES (?, 'x', 'quant-ph', 1)", (day,))


async def wait_for(fn, timeout=12.0):
    end = time.time() + timeout
    while time.time() < end:
        if await fn():
            return True
        await asyncio.sleep(0.2)
    return False


async def main():
    env = dict(os.environ, SYNAPSE_DATA_DIR=DATA)
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser"],
        cwd=APP,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        try:
            requests.get(BASE, timeout=1)
            break
        except requests.RequestException:
            time.sleep(0.2)
    try:
        api(
            "/api/settings",
            "PUT",
            json={"add_fetch_references": "never", "add_find_pdf": "never", "references_auto": False},
        )
        for title, authors, doi in LIBRARY:
            api("/api/items", "POST", json={"title": title, "authors": authors, "doi": doi})
        api(f"/api/plugin-host/{PID}/enable", "POST")
        seed()
        api(f"/api/plugin-host/{PID}/settings", "PUT", json={"author_names": ["Lorenzo Fioroni"]})  # rescores
        end = time.time() + 20
        while time.time() < end and any(r["score"] is None for r in feed("SELECT score FROM papers")):
            time.sleep(0.2)
        scores = {r["arxiv"]: r for r in feed("SELECT arxiv, score, tier FROM papers")}
        check("the seeded day is scored", all(r["score"] is not None for r in scores.values()), scores)

        async with async_playwright() as p:
            b = await p.chromium.launch()
            page = await b.new_page(viewport={"width": 1400, "height": 860})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(BASE)
            await page.wait_for_function(f"() => window.zcPlugins?.loaded().includes('{PID}')", timeout=20000)
            await page.locator(f"#sidebar [data-view^='source:{PID}.feed']").first.click()
            rows = page.locator(".row[data-id]")
            await rows.first.wait_for()
            ids = [await rows.nth(i).get_attribute("data-id") for i in range(await rows.count())]
            check(
                "the feed is ordered by match",
                ids == [f"x:arxiv:{a}" for a, *_ in PAPERS],
                ids,
            )

            # a glance at the first paper (arrowing past it) does not open it
            await rows.nth(0).click()
            await page.locator(f"{SECTION} .at-head").wait_for()
            head = await page.locator(f"{SECTION} .at-head").inner_text()
            want = scores["2611.00001"]
            check(
                "the section shows the match and its tier",
                abs(int(head.split("·")[0]) - want["score"]) <= 1 and want["tier"].capitalize() in head,
                head,
            )
            check("one bar per kind of evidence", await page.locator(f"{SECTION} .at-part").count() >= 2)
            await shot(page, "arxiv-match-section")
            await rows.nth(1).click()
            await page.locator(f"{SECTION} .at-head").wait_for()
            why = await page.locator(".ip-reasons").inner_text()
            check("citing only a textbook counts little", "widely cited" in why and "counts little" in why, why)
            await page.wait_for_timeout(5000)  # shown long enough: opened
            state = {r["arxiv"]: r for r in feed("SELECT arxiv, opened_at, passed FROM papers")}
            check("a paper shown for a while counts as opened", state["2611.00002"]["opened_at"] is not None, state)
            check("a paper only glanced at does not", state["2611.00001"]["opened_at"] is None, state)
            check("the paper above it was passed over", state["2611.00001"]["passed"] == 1, state)
            check("the paper below it was not", state["2611.00003"]["passed"] == 0, state)

            await page.keyboard.press(f"{MOD}+,")
            await page.locator("[data-nav=plugins]").click()
            await page.locator(f"[data-nav='plugin-{PID}']").click()
            part = page.locator(f"#set-plugin-{PID} .at-weights")
            await part.wait_for()
            text = await page.locator(f"#set-plugin-{PID}").inner_text()
            check(
                "Settings lists the hand-set weights", "Library papers it cites" in text and "3.5" in text, text[:300]
            )
            check("Settings says nothing is learned yet", "Not learned yet" in text, text[:300])
            await page.click(f"#set-plugin-{PID} [data-now]")
            check(
                "Learn Now runs the learning job",
                await wait_for(
                    lambda: asyncio.to_thread(lambda: api(f"/api/plugins/{PID}/model")["checked_at"] is not None)
                ),
            )
            await shot(page, "arxiv-match-settings")
            check("no page errors", not errors, errors)
            await b.close()
    finally:
        srv.terminate()
        srv.wait(timeout=10)
        shutil.rmtree(DATA, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _guard import guard  # noqa: E402

guard(PORT)
asyncio.run(main())
