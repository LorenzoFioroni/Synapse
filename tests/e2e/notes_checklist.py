"""Browser checklist for the notes editor (plan M3). Not part of the pytest run.

    pip install playwright && playwright install chromium     # once
    python tests/e2e/notes_checklist.py

Starts its own server on port 8765 with a throw-away data folder; your library is not touched.
"""
import asyncio, os, shutil, subprocess, sys, tempfile, time
from playwright.async_api import async_playwright

APP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}/"
DATA = tempfile.mkdtemp(prefix="zc-notes-e2e-")
D = os.path.join(DATA, "notes")
def disk(name): return open(f"{D}/{name}").read()
results=[]
def check(name, cond, extra=""):
    results.append((name, bool(cond))); print(("PASS " if cond else "FAIL ")+name, extra)

async def status(page): return (await page.text_content('.note-status')).strip()
async def wait_status(page, want, t=6000):
    end=time.time()+t/1000
    while time.time()<end:
        s=await status(page)
        if want in s: return s
        await page.wait_for_timeout(100)
    return await status(page)
async def type_end(page, text):
    await page.click('.cm-content'); await page.keyboard.press('ControlOrMeta+End'); await page.keyboard.type(text)

def start_server():
    env = dict(os.environ, ZCLONE_DATA_DIR=DATA)
    return subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--no-browser"], cwd=APP, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

async def main():
    srv=start_server(); time.sleep(1.5)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width":1440,"height":900})
        errs=[]
        def watch(pg, tag):
            pg.on("pageerror", lambda e: errs.append(f"{tag} pageerror: {e}"))
            pg.on("console", lambda m: errs.append(f"{tag} {m.type}: {m.text}") if m.type=="error" else None)
        A = await ctx.new_page(); watch(A,"A")
        await A.goto(BASE); await A.wait_for_timeout(1200)
        await A.click('[data-act="open-notes"]'); await A.wait_for_timeout(500)
        await A.click('#notes-view [data-act="new-note"]')
        await A.fill('.dialog input', 'First idea'); await A.keyboard.press('Enter')
        await A.wait_for_selector('.cm-content'); await A.wait_for_timeout(300)
        await type_end(A, "Some text citing [@smith2021, p. 4].\n\n## Section A\n\n- item one\n")
        await wait_status(A, "Saved")
        await A.click('.nt-row.note[data-path="First idea.md"]'); await A.wait_for_timeout(800)
        await A.reload(); await A.wait_for_timeout(1500)
        # 1. reload restores notes workspace + open note
        check("reload reopens the last note", await A.is_visible('#notes-view') and "First idea" in (await A.text_content('.note-crumbs')))
        # 2. front-matter styled as meta, not as a heading
        fm_meta = await A.locator('.cm-line .cmt-meta').count()
        first_lines_heading = await A.evaluate("[...document.querySelectorAll('.cm-line')].slice(0,7).some(l=>l.querySelector('.cmt-h'))")
        check("front-matter shown as metadata, not as a heading", fm_meta>0 and not first_lines_heading, f"meta spans={fm_meta}")
        
        # 3. two tabs, conflict -> keep mine
        B = await ctx.new_page(); watch(B,"B")
        await B.goto(BASE); await B.wait_for_timeout(1200)
        await type_end(A, "AAA from tab A")
        check("tab A saves", "Saved" in await wait_status(A, "Saved"))
        await type_end(B, "BBB from tab B")
        await B.wait_for_selector('.dialog', timeout=6000)
        title = await B.text_content('.dialog-title')
        diff_add = await B.locator('.diff .d-add').count(); diff_del = await B.locator('.diff .d-del').count()
        check("tab B gets the conflict dialog with a diff", "changed elsewhere" in title and diff_add>0 and diff_del>0, f"{title} +{diff_add} -{diff_del}")
        check("nothing overwritten before choosing", "AAA from tab A" in disk("First idea.md") and "BBB" not in disk("First idea.md"))
        await B.click('.dialog button:has-text("Keep my version")')
        await wait_status(B, "Saved")
        d = disk("First idea.md")
        check("keep mine writes tab B's text", "BBB from tab B" in d and "AAA" not in d)
        # A picks it up on focus
        await A.bring_to_front(); await A.evaluate("window.dispatchEvent(new Event('focus'))"); await A.wait_for_timeout(800)
        a_text = await A.evaluate("document.querySelector('.cm-content').innerText")
        check("tab A picks up the change when focused", "BBB from tab B" in a_text and "AAA" not in a_text)

        # 3b. conflict -> take theirs
        await type_end(B, " +B2")
        await wait_status(B, "Saved")
        await type_end(A, " +A2")
        await A.wait_for_selector('.dialog', timeout=6000)
        await A.click('.dialog button:has-text("Use the saved file")')
        await A.wait_for_timeout(500)
        a_text = await A.evaluate("document.querySelector('.cm-content').innerText")
        check("take theirs loads the file into the editor", "+B2" in a_text and "+A2" not in a_text and "+A2" not in disk("First idea.md"))
        check("status back to saved", "Saved" in await status(A))
        await B.close()

        # 4. rename via double-click
        await A.dblclick('.nt-row.note[data-path="First idea.md"]')
        await A.fill('.dialog input', 'Renamed idea'); await A.keyboard.press('Enter'); await A.wait_for_timeout(800)
        check("rename moves the file and keeps it open", os.path.exists(f"{D}/Renamed idea.md") and not os.path.exists(f"{D}/First idea.md")
              and "Renamed idea" in await A.text_content('.note-crumbs'))
        await type_end(A, "\nafter rename")
        await wait_status(A, "Saved")
        check("editing after rename saves to the new file", "after rename" in disk("Renamed idea.md"))

        # 5. folder + move via context menu
        await A.click('#notes-view [data-act="new-folder"]'); await A.fill('.dialog input', 'Projects'); await A.keyboard.press('Enter'); await A.wait_for_timeout(500)
        await A.click('.nt-row.note[data-path="Renamed idea.md"]', button="right")
        await A.click('.menu >> text=Move to folder…'); await A.fill('.dialog input', 'Projects'); await A.keyboard.press('Enter'); await A.wait_for_timeout(800)
        check("move to folder", os.path.exists(f"{D}/Projects/Renamed idea.md") and "Projects" in await A.text_content('.note-crumbs'))
        # new note while inside a folder goes into that folder
        await A.keyboard.press('ControlOrMeta+n'); await A.fill('.dialog input', 'Second'); await A.keyboard.press('Enter'); await A.wait_for_timeout(800)
        check("Ctrl+N creates the note in the current folder", os.path.exists(f"{D}/Projects/Second.md"))

        # 6. trash + undo
        await A.click('.nt-row.note[data-path="Projects/Second.md"]', button="right")
        await A.click('.menu >> text=Move to Trash'); await A.wait_for_timeout(600)
        check("trash moves the file to .trash", not os.path.exists(f"{D}/Projects/Second.md") and any("Second.md" in f for _,_,fs in os.walk(f"{D}/.trash") for f in fs))
        await A.click('.toast-action:has-text("Undo")'); await A.wait_for_timeout(800)
        check("undo restores it", os.path.exists(f"{D}/Projects/Second.md"))

        # 7. server down while typing: draft kept, saved after restart (in a new tab)
        await A.click('.nt-row.note[data-path="Projects/Renamed idea.md"]'); await A.wait_for_timeout(600)
        srv.terminate(); srv.wait()
        await type_end(A, "\ntyped while the server was down")
        st = await wait_status(A, "retrying", 5000)
        check("server down -> 'not saved yet' status", "retrying" in st, st)
        await A.close()
        srv=start_server(); time.sleep(1.5)
        C = await ctx.new_page(); watch(C,"C")
        await C.goto(BASE); await C.wait_for_timeout(1500)
        c_text = await C.evaluate("document.querySelector('.cm-content')?.innerText || ''")
        await wait_status(C, "Saved", 6000)
        check("reopening restores the unsaved text and saves it", "typed while the server was down" in c_text and "typed while the server was down" in disk("Projects/Renamed idea.md"))

        # 8. back to the library and dark theme
        await C.click('[data-view="library"]'); await C.wait_for_timeout(500)
        check("library view comes back", await C.is_visible('#items-pane') and not await C.is_visible('#notes-view'))
        await C.click('[data-act="open-notes"]'); await C.wait_for_timeout(600)
        print("CONSOLE ERRORS:", [e for e in errs if "Failed to load resource" not in e])
        await b.close()
    srv.terminate()
    shutil.rmtree(DATA, ignore_errors=True)
    print(f"\n{sum(r for _,r in results)}/{len(results)} passed")
    sys.exit(0 if all(r for _, r in results) else 1)

asyncio.run(main())
