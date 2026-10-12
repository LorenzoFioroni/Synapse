"""Browser checklist for editing notes with equations (issue #43). Not part of the pytest run.

    python tests/e2e/equations_checklist.py              (SHOTS=/some/dir to save screenshots)

Starts its own server on port 8786 with a throw-away data folder; your library is not touched.
"""
import asyncio, os, subprocess, sys, tempfile, time
from pathlib import Path
import requests
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent))
from _guard import guard  # noqa: E402

APP = Path(__file__).resolve().parents[2]
PORT = 8786
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-equations-e2e-")
SHOTS = os.environ.get("SHOTS")
results = []

NOTE = r"""# Equations

**Model.** We rewrite Eq. (2) with $\hat x=(\hat a+\hat a^\dagger)/\sqrt2$. The cavity term $\tfrac{\Delta_{cd}}{2}(\hat x^2+\hat p^2)$ equals $\Delta_{cd}\hat a^\dagger\hat a$ up to a constant. See \eqref{eq:cav}, \eqref{eq:thr} and \eqref{eq:none}.

$$
\dot\alpha=-\Big(i\Delta_{cd}+\frac\kappa2\Big)\alpha-i\eta\,T,
\qquad
T\equiv\int d\mathbf r\;w(\mathbf r)\,n(\mathbf r),
\qquad n=\langle\hat n\rangle .
\tag{1}\label{eq:cav}
$$

Untagged:

$$
E = mc^2
$$

$$
\overline{\Omega}_{d,c}=\frac{2\Delta_{ad}}{\Omega}\left[\frac{32\,(\Delta_{cd}+\kappa^2/4\Delta_{cd})}{\chi_0\pi R^2\sum_{\mathbf Q}F(a_{\mathbf Q})}\right]^{1/2}\frac{1}{\sqrt{\langle g^2\rangle_{\rm eff}}},
\qquad
\langle g^2\rangle_{\rm eff}=\mu_0^2+\frac{\sum_{\mathbf Q}\int\frac{d^2k}{(2\pi)^2}S(\mathbf k)F(a_{\mathbf Q+\mathbf k})}{\sum_{\mathbf Q}F(a_{\mathbf Q})},
\tag{9}\label{eq:thr}
$$

$$
\begin{align}
a &= b + c \label{eq:a1}\\
d &= e \notag\\
f &= g
\end{align}
$$

The end, with $x$ and \eqref{eq:a1}.
"""


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


TAGS_CLEAR = """() => [...document.querySelectorAll(SEL)].every(d => {
  const main = d.querySelector('.eq-main'), tag = d.querySelector('.katex-html > .katex-tag:not(.eq-ghost)');
  if (!tag) return true;
  const strip = (r) => [r.left, r.right];
  const [, mr] = strip(main.getBoundingClientRect()), [tl] = strip(tag.getBoundingClientRect());
  return tl >= mr - 1;
})"""

CURSOR = "(() => { const t = zcNotes.text(), h = zcNotes.cursor(); return t.slice(h - 12, h) + '|' + t.slice(h, h + 10); })()"


async def click_glyph(page, block, ch, nth=0):
    """Click the nth drawn character ch of the given rendered $$ block (0-based)."""
    pos = await page.evaluate("""([bi, ch, nth]) => {
      const blk = [...document.querySelectorAll('.cm-lp-mathblock:not(.cm-lp-mathpreview)')][bi];
      const w = document.createTreeWalker(blk.querySelector('.eq-main') || blk.querySelector('.katex-html'), NodeFilter.SHOW_TEXT);
      let k = 0;
      for (let n = w.nextNode(); n; n = w.nextNode()) {
        const i = n.data.indexOf(ch);
        if (i >= 0 && k++ === nth) { const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + 1); const b = r.getBoundingClientRect(); return [b.x + b.width * 0.3, b.y + b.height / 2]; }
      }
      return null; }""", [block, ch, nth])
    await page.mouse.click(*pos)
    await page.wait_for_timeout(250)


async def main():
    guard(PORT)
    env = dict(os.environ, SYNAPSE_DATA_DIR=DATA)
    srv = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--no-browser"], cwd=APP, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    try:
        requests.post(f"{BASE}/api/notes/note", json={"path": "Eq.md", "title": "Eq", "text": NOTE}).raise_for_status()
        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(viewport={"width": 1440, "height": 1500})
            page = await ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(BASE + "/")
            await page.wait_for_timeout(800)
            await page.click('[data-act="open-notes"]')
            await page.click('.nt-row.note[data-path="Eq.md"]')
            await page.wait_for_selector('.cm-lp-mathblock')
            await page.wait_for_timeout(800)

            # ---- numbers (live preview)
            tags = await page.evaluate("[...document.querySelectorAll('.cm-lp-mathblock .katex-html > .katex-tag:not(.eq-ghost)')].map(t => t.textContent.replace(/\\s|\\u200b/g, ''))")
            check("live: \\tag numbers and align rows numbered like LaTeX", tags == ["(1)", "(9)", "(1)(2)"], tags)
            check("live: numbers never overlap their formula", await page.evaluate(TAGS_CLEAR.replace("SEL", "'.cm-lp-mathblock .katex-display'")))
            fit = await page.evaluate("(() => { const b = [...document.querySelectorAll('.cm-lp-mathblock')][2]; return [b.scrollWidth <= b.clientWidth + 1, b.querySelector('.katex-display').style.getPropertyValue('--eq-scale')]; })()")
            check("live: a too-wide equation is scaled down to fit", fit[0] and fit[1] and 0.75 <= float(fit[1]) < 1, fit)
            refs = await page.locator('.cm-lp-chip.eqref').all_text_contents()
            check("live: \\eqref in the text shows the numbers", refs == ["(1)", "(9)", "(??)", "(1)"], refs)
            check("live: a missing label is marked", await page.locator('.cm-lp-chip.eqref.missing').count() == 1)
            await shot(page, "1-live")

            # ---- editing: the cursor's paragraph keeps its formulas rendered
            para = page.locator('.cm-line', has_text='up to a constant')
            box = await para.bounding_box()
            await page.mouse.click(box["x"] + box["width"] - 30, box["y"] + box["height"] - 10)
            await page.wait_for_timeout(250)
            check("live: typing in a paragraph leaves its formulas rendered",
                  await para.locator('.cm-lp-math').count() == 3 and await page.locator('.cm-lp-mathsrc').count() == 0)
            bb = await para.locator('.cm-lp-math .katex-html').nth(1).bounding_box()
            await page.mouse.click(bb["x"] + bb["width"] * 0.55, bb["y"] + bb["height"] / 2)
            await page.wait_for_timeout(250)
            src = await page.locator('.cm-lp-mathsrc').all_text_contents()
            check("live: clicking a formula opens only that one", len(src) == 1 and "tfrac" in src[0], src)

            # ---- clicks inside a rendered $$ block land on the clicked symbol
            for block, ch, nth, want in [(2, "Δ", 0, "\\frac{2|\\Delta_{a"), (2, "κ", 0, "|\\kappa^2/4"), (2, "μ", 0, "|\\mu_0^2"),
                                         (0, "η", 0, "-i|\\eta"), (1, "m", 0, "E = |mc^2")]:
                await click_glyph(page, block, ch, nth)
                at = await page.evaluate(CURSOR)
                check(f"live: clicking {ch} in a formula puts the cursor there", want in at, at)
                await page.click('.note-crumbs')   # leave the editor: all rendered again
                await page.wait_for_timeout(200)
            await click_glyph(page, 2, "Δ", 0)
            check("live: the $$ block being edited shows its source and, below, the formula",
                  await page.locator('.cm-line.cm-lp-mathsrc-block').count() >= 5 and await page.locator('.cm-lp-mathpreview .katex').count() == 1)
            await page.keyboard.type("Z")
            await page.wait_for_timeout(300)
            check("live: the formula below follows the typing",
                  "Z" in (await page.locator('.cm-lp-mathpreview .katex-html').text_content()))
            await shot(page, "2-editing")
            await page.keyboard.press("Backspace")
            await page.click('.note-crumbs')

            # ---- read view
            await page.click('[data-mode="read"]')
            await page.wait_for_selector('.note-read:not([hidden]) .math-block')
            await page.wait_for_timeout(400)
            tags = await page.evaluate("[...document.querySelectorAll('.note-read .katex-html > .katex-tag:not(.eq-ghost)')].map(t => t.textContent.replace(/\\s|\\u200b/g, ''))")
            check("read: the same numbers", tags == ["(1)", "(9)", "(1)(2)"], tags)
            check("read: numbers never overlap their formula", await page.evaluate(TAGS_CLEAR.replace("SEL", "'.note-read .katex-display'")))
            links = await page.locator('.note-read a.eqref').all_text_contents()
            check("read: \\eqref links", links == ["(1)", "(9)", "(1)"] and await page.locator('.note-read .eqref.missing').count() == 1, links)
            await page.locator('.note-read a.eqref').nth(1).click()
            await page.wait_for_timeout(700)
            check("read: clicking \\eqref goes to the equation", await page.evaluate(
                "(() => { const e = document.getElementById('eq-eq-thr').parentElement; const r = e.getBoundingClientRect(); return e.classList.contains('flash') && r.top >= 0 && r.bottom <= innerHeight; })()"))
            await page.click('[data-mode="live"]')

            # ---- width and focus mode
            col = lambda: page.evaluate("document.querySelector('.cm-content').getBoundingClientRect().width")
            w0 = await col()
            await page.click('[data-act="note-focus"]')
            await page.wait_for_timeout(400)
            hidden = await page.evaluate("['#sidebar', '.notes-tree-pane', '.notes-side'].every(s => getComputedStyle(document.querySelector(s)).display === 'none')")
            check("focus mode hides the sidebar, the notes list and the details", hidden)
            wide = await page.evaluate("(() => { const m = document.querySelector('.cm-lp-mathblock'); return [m.getBoundingClientRect().width, document.querySelector('.cm-content').getBoundingClientRect().width]; })()")
            check("focus mode: equations use more than the text column", wide[0] > wide[1] + 100, wide)
            await page.click('[data-act="note-width"]')
            await wait_for(lambda: page.evaluate("document.getElementById('notes-view').classList.contains('wide-text')"))
            await page.wait_for_timeout(300)
            w2 = await col()
            check("the width button lets the text use the whole pane", w2 > w0 + 400 and requests.get(f"{BASE}/api/settings").json()["settings"]["notes_width"] == "full", (w0, w2))
            await shot(page, "3-focus-full")
            await page.reload()
            await page.wait_for_selector('.cm-lp-mathblock')
            check("focus mode and width are kept after a reload", await page.evaluate("document.body.classList.contains('notes-focus') && document.getElementById('notes-view').classList.contains('wide-text')"))
            await page.click('.cm-content')
            await page.keyboard.press("ControlOrMeta+Backslash")
            await page.wait_for_timeout(300)
            check("Cmd/Ctrl+\\ brings the side panels back", await page.evaluate("getComputedStyle(document.querySelector('.notes-tree-pane')).display !== 'none'"))
            await page.click('[data-act="note-width"]')
            await page.wait_for_timeout(300)

            # ---- number every display equation (Settings)
            requests.put(f"{BASE}/api/settings", json={"notes_equation_numbers": "all"}).raise_for_status()
            await page.reload()
            await page.wait_for_selector('.cm-lp-mathblock')
            await page.wait_for_timeout(500)
            tags = await page.evaluate("[...document.querySelectorAll('.cm-lp-mathblock .katex-html > .katex-tag:not(.eq-ghost)')].map(t => t.textContent.replace(/\\s|\\u200b/g, ''))")
            check("setting 'every display equation' numbers the untagged one too", tags == ["(1)", "(1)", "(9)", "(2)(3)"], tags)
            refs = await page.locator('.cm-lp-chip.eqref').all_text_contents()
            check("…and \\eqref follows", refs[-1] == "(2)", refs)

            check("no JavaScript errors", not errors, errors[:3])
            await b.close()
    finally:
        srv.kill()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
