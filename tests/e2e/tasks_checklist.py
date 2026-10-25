"""Browser checklist for tasks: task lines in a note (drawn as a checkbox and title, ticking, the
popup to add a new or an existing task, the task popup, ticking a parent), read mode, the side
panel, and the Tasks page (views, search, quick add, the detail pane, keyboard, delete and Undo,
changes from elsewhere).

    python tests/e2e/tasks_checklist.py

Starts its own server on port 8795 with a throw-away data folder; your library is not touched.
"""

import asyncio
import re
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from tests.e2e._guard import guard  # noqa: E402

PORT = 8795
BASE = f"http://127.0.0.1:{PORT}"
DATA = tempfile.mkdtemp(prefix="zc-tasks-e2e-")
MOD = "Meta" if sys.platform == "darwin" else "Control"
PATH = "Notes/Plan.md"
OTHER = "Notes/Other.md"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


async def wait_for(page, js, t=6000):
    end = time.time() + t / 1000
    while time.time() < end:
        if await page.evaluate(js):
            return True
        await page.wait_for_timeout(100)
    return False


def until(fn, t=6.0):
    end = time.time() + t
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.15)
    return fn()


async def main():
    guard(PORT)
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT), "--no-browser", "--data-dir", DATA],
        cwd=APP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(240):
            try:
                requests.get(BASE + "/api/settings", timeout=1)
                break
            except requests.RequestException:
                time.sleep(0.25)
        s = requests.Session()
        s.headers["Origin"] = BASE

        def task(title, **kw):
            return s.post(BASE + "/api/tasks", json={"title": title, **kw}).json()["task"]["id"]

        def get(tid):
            return s.get(f"{BASE}/api/tasks/{tid}").json()["task"]

        def all_tasks():
            return s.get(BASE + "/api/tasks").json()

        def note_text():
            return s.get(BASE + "/api/notes/note", params={"path": PATH}).json()["text"]

        soon = (date.today() + timedelta(days=3)).isoformat()
        plot = task("Plot $\\chi$ vs $\\varepsilon$", due=soon)
        ising = task("Discuss the Ising model", body="Start from [[Other]].\n\n$$\nJ = \\mu\\eta\n$$")
        sub1 = task("Reproduce the profile", parent_id=ising)
        task("Simulate $N = 100$", parent_id=sub1)
        admin = task("Send the plots to Alex")
        long = task(
            "If that's the case, show a plot that demonstrates that there is little advantage and only at the "
            "boundaries of the Ising phase in the weakly coupled regime"
        )
        text = (
            "# Plan\n\nIntro line.\n\n## TODOs\n"
            f"{{{{task:{plot}}}}}\n- {{{{task:{ising}}}}}\n {{{{task:{long}}}}}\n\n"
            "## Plain\n- [ ] a plain checkbox\n\nWrite the outlook section\n"
        )
        s.post(BASE + "/api/notes/note", json={"path": PATH, "text": text})
        s.post(BASE + "/api/notes/note", json={"path": OTHER, "text": "# Other\n\nSome words.\n"})

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1400, "height": 950})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(BASE)
            await page.wait_for_timeout(1200)
            await page.evaluate("localStorage.clear()")

            await page.wait_for_selector('#sidebar [data-navigation="tasks.main"]')
            check(
                "Tasks is listed in the sidebar's list, with Library and Notes",
                await page.evaluate("""() => {
                const task = document.querySelector('#sidebar [data-navigation=\"tasks.main\"]');
                return task && !task.hidden && !!task.closest('.tool-list')
                    && !!document.querySelector('#sidebar .tool-list [data-view=\"library\"]');
            }"""),
            )
            check("Tasks has no toolbar workspace button", await page.locator("#toolbar [data-ws]").count() == 0)
            await page.click('#sidebar [data-navigation="tasks.main"]')
            await page.wait_for_selector("#tasks-view:not([hidden])")
            check(
                "The sidebar opens Tasks and marks it active",
                await page.locator('#sidebar [data-navigation="tasks.main"]').evaluate(
                    "el => el.classList.contains('active')"
                ),
            )

            # ---- a note shows its tasks
            await page.keyboard.press(f"{MOD}+Shift+L")
            await page.wait_for_selector(".notes-tree")
            await page.locator(".notes-tree >> text=Plan").first.click()
            drawn = await wait_for(page, "document.querySelectorAll('.cm-lp-taskref').length === 3")
            check("A note draws its task lines as tasks", drawn)
            src = await page.evaluate("document.querySelector('.note-editor .cm-content').innerText")
            check("No {{task:…}} source shows in the live preview", "{{task:" not in src)
            check(
                "Titles are rendered (math)",
                await page.locator(".cm-lp-taskref .katex").count() >= 1,
            )
            check(
                "A task line in a list has no bullet",
                await page.evaluate(
                    "[...document.querySelectorAll('.cm-line')].find(l => l.querySelector('.cm-lp-taskref') && l.textContent.includes('Ising')) ?.querySelector('.cm-lp-bullet') === null"
                ),
            )
            check("Its sub-task progress shows", "0/1" in await page.locator(".cm-lp-taskref").nth(1).inner_text())
            check(
                "A plain checkbox stays a checkbox",
                await page.locator("input.cm-lp-task").count() == 1 and len(all_tasks()["tasks"]) == 6,
            )

            # ---- a long title on an indented line: the box stays on its first line, no gap above it
            geo = await page.evaluate(
                f"""(() => {{ const w = [...document.querySelectorAll('.cm-lp-taskref')].find(e => e.dataset.task === '{long}');
                  if (!w) return null; const line = w.closest('.cm-line'), box = w.querySelector('.task-check'), title = w.querySelector('.task-title');
                  const lr = line.getBoundingClientRect(), wr = w.getBoundingClientRect(), br = box.getBoundingClientRect(), tr = title.getClientRects()[0];
                  return [Math.round(lr.height - wr.height), Math.round((br.top + br.bottom) / 2 - (tr.top + tr.bottom) / 2), Math.round(wr.height / tr.height)]; }})()"""
            )
            check("A wrapped task on an indented line takes no extra space", geo and abs(geo[0]) <= 3, str(geo))
            check(
                "…and its box is centred on the first line of the title",
                geo and abs(geo[1]) <= 2 and geo[2] >= 2,
                str(geo),
            )

            # ---- every box in one column: task lines with or without a leading space or a list marker, checkboxes
            lefts = await page.evaluate(
                "[...document.querySelectorAll('.cm-lp-taskref .task-check, input.cm-lp-task')]"
                ".map(e => Math.round(e.getBoundingClientRect().left))"
            )
            check("All boxes line up (task lines and checkboxes)", len(lefts) == 4 and len(set(lefts)) == 1, str(lefts))

            # ---- ticking from the note changes the task, not the note
            before = note_text()
            await page.locator(".cm-lp-taskref .task-check").first.click()
            done = until(lambda: get(plot)["status"] == "done")
            check("Clicking the box ticks the task", done)
            await page.wait_for_timeout(1800)
            check("…and leaves the note's text as it was", note_text() == before)
            await page.locator(".cm-lp-taskref .task-check").first.click()
            check("Clicking again reopens it", until(lambda: get(plot)["status"] == "open"))

            # ---- ticking a parent asks about its open sub-tasks
            await page.locator(".cm-lp-taskref .task-check").nth(1).click()
            asked = await wait_for(page, "!!document.querySelector('.dialog')")
            check("Ticking a parent with open sub-tasks asks first", asked)
            if asked:
                check("…naming how many", "2 open sub-tasks" in await page.locator(".dialog").inner_text())
                await page.locator(".dialog button", has_text="Mark all").click()
                ok = until(lambda: get(ising)["status"] == "done" and get(sub1)["status"] == "done")
                check("Mark all closes the sub-tasks too", ok)
                s.patch(f"{BASE}/api/tasks/{ising}", json={"status": "open", "with_subtasks": True})

            # ---- the popup of a task
            await page.wait_for_timeout(500)
            await page.locator(".cm-lp-taskref .task-title").nth(1).click()
            pop = await wait_for(page, "!!document.querySelector('.task-popup-edit .task-form')")
            check("Clicking a task's title opens it in a popup", pop)
            await wait_for(page, "!!document.querySelector('.task-popup .tf-body .cm-content')")
            body_text = await page.locator(".task-popup .tf-body .cm-content").inner_text()
            check(
                "The popup shows its details (rendered math)",
                await page.locator(".task-popup .tf-body .katex").count() >= 1,
                body_text[:60],
            )
            check(
                "…and its sub-tasks", "Reproduce the profile" in await page.locator(".task-popup .tf-subs").inner_text()
            )
            await page.locator(".task-popup .tf-title").click()
            await page.locator(".task-popup .tf-title-input").fill("Discuss the Ising model at finite coupling")
            await page.keyboard.press("Enter")
            check(
                "Editing the title in the popup saves the task",
                until(lambda: get(ising)["title"] == "Discuss the Ising model at finite coupling"),
            )
            await page.locator(".task-popup .tf-add-sub input").fill("Check convergence fri")
            await page.keyboard.press("Enter")
            made = until(lambda: [t for t in all_tasks()["tasks"] if t["title"] == "Check convergence"])
            check(
                "A sub-task added in the popup gets its date from the words",
                bool(made) and made[0]["due"] and made[0]["parent_id"] == ising,
            )
            await page.keyboard.press("Escape")
            closed = await wait_for(page, "!document.querySelector('.task-popup')")
            check("Esc closes the popup", closed)

            # ---- adding a task from the note: the line's text becomes the task
            line = page.locator(".cm-line", has_text="Write the outlook section")
            await line.click()
            await page.keyboard.press("End")
            await page.keyboard.press(f"{MOD}+Enter")
            ok = await wait_for(page, "!!document.querySelector('.task-popup-new')")
            check(f"{MOD}+Enter opens the New task popup", ok)
            title = await page.locator(".tn-title-input").input_value()
            check("…with the line's text as its title", title == "Write the outlook section", title)
            await page.locator(".tn-due-input").fill("tomorrow")
            hint = await page.locator(".tn-due-hint").inner_text()
            check("The date field reads words", "Tomorrow" in hint, hint)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(800)
            new = [t for t in all_tasks()["tasks"] if t["title"] == "Write the outlook section"]
            check(
                "Enter makes the task, with its date",
                len(new) == 1 and new[0]["due"] == (date.today() + timedelta(days=1)).isoformat(),
            )
            ed = await page.evaluate("window.zcNotes.text()")
            check(
                "…and puts its line where the text was",
                new and f"{{{{task:{new[0]['id']}}}}}" in ed and "Write the outlook section" not in ed,
            )

            # ---- a checkbox line becomes a task line without its "- [ ]"
            await page.locator(".cm-line", has_text="a plain checkbox").click()
            await page.keyboard.press("End")
            await page.keyboard.press(f"{MOD}+Enter")
            await page.wait_for_selector(".task-popup-new")
            title = await page.locator(".tn-title-input").input_value()
            check("A checkbox line's text is the title", title == "a plain checkbox", title)
            await page.keyboard.press("Enter")
            made3 = until(lambda: [t for t in all_tasks()["tasks"] if t["title"] == "a plain checkbox"])
            if made3:  # the editor writes the line once the task exists
                await wait_for(page, f"window.zcNotes.text().includes('{{{{task:{made3[0]['id']}}}}}')")
            ed = await page.evaluate("window.zcNotes.text()")
            line = next((x for x in ed.split("\n") if made3 and f"{{{{task:{made3[0]['id']}}}}}" in x), None)
            check(
                "…and its line is the task alone, without the list marker",
                line == f"{{{{task:{made3[0]['id']}}}}}" if made3 else False,
                repr(line),
            )
            await page.keyboard.press(f"{MOD}+End")

            # ---- …or one you already have
            await page.keyboard.press("End")
            await page.keyboard.press("Enter")
            await page.keyboard.press(f"{MOD}+Enter")
            await page.wait_for_selector(".task-popup-new")
            await page.keyboard.type("alex")
            shown = await wait_for(page, "document.querySelectorAll('.tn-match').length === 1")
            check("Typing offers matching tasks you already have", shown)
            n = len(all_tasks()["tasks"])
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(500)
            ed = await page.evaluate("window.zcNotes.text()")
            check(
                "Picking one adds its line, without making a task",
                f"{{{{task:{admin}}}}}" in ed and len(all_tasks()["tasks"]) == n,
            )
            saved = until(lambda: f"{{{{task:{admin}}}}}" in note_text())
            check("The note is saved with the new lines", saved)
            listed = until(lambda: any(r["path"] == PATH for r in all_tasks()["refs"].get(admin, [])))
            check("The index knows which note shows the task", listed)

            # ---- "{{task" offers your tasks, like "[@" offers papers
            await page.keyboard.press("End")
            await page.keyboard.press("Enter")
            await page.keyboard.type("{{task ising", delay=40)
            listed = await wait_for(
                page,
                "[...document.querySelectorAll('.cm-tooltip-autocomplete li')].some(li => li.innerText.includes('Ising'))",
            )
            check("Typing {{task shows matching tasks", listed)
            await page.wait_for_timeout(250)  # Enter picks only once the list has been open a moment
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(300)
            ed = await page.evaluate("window.zcNotes.text()")
            check("…and Enter writes that task's line", ed.rstrip().endswith(f"{{{{task:{ising}}}}}"), repr(ed[-40:]))
            await page.keyboard.press("End")
            await page.keyboard.press("Enter")
            await page.keyboard.type("{{task: Email the referee", delay=30)
            await wait_for(
                page,
                "[...document.querySelectorAll('.cm-tooltip-autocomplete li')]"
                ".some(li => li.innerText.includes('New task “Email the referee”'))",
            )
            await page.wait_for_timeout(250)
            await page.keyboard.press("Enter")
            made2 = until(lambda: [t for t in all_tasks()["tasks"] if t["title"] == "Email the referee"])
            check("…or makes a new task from the words typed", bool(made2))
            ok = await wait_for(
                page, f"window.zcNotes.text().includes('{{{{task:{made2[0]['id'] if made2 else 'none'}}}}}')"
            )
            check("…and writes its line", ok)
            await page.keyboard.press("End")
            await page.keyboard.press("Enter")
            await page.keyboard.type("{{ not a task", delay=30)
            await page.wait_for_timeout(500)
            check("A plain {{ offers nothing", await page.locator(".cm-tooltip-autocomplete").count() == 0)
            await page.keyboard.press("End")
            await page.keyboard.press("Shift+Home")
            await page.keyboard.press("Backspace")
            await page.keyboard.press("Backspace")

            # ---- the side panel
            side = await wait_for(
                page, "(document.querySelector('.ns-tasks')?.innerText || '').includes('Send the plots')", 8000
            )
            check("The side panel lists the note's tasks", side)

            # ---- the cursor steps over a task line; Backspace takes it out in one go
            content = page.locator(".note-editor .cm-content")

            async def to_last_task():
                await content.press(f"{MOD}+End")
                t = await page.evaluate("window.zcNotes.text()")
                for _ in range(len(t) - len(t.rstrip("\n"))):
                    await page.keyboard.press("ArrowLeft")  # to the end of the last task's line
                return t, re.findall(r"\{\{task:([a-z0-9]+)\}\}", t)[-1]

            def exists(tid):
                return any(t["id"] == tid for t in all_tasks()["tasks"])

            def toast_says(words):
                return f"[...document.querySelectorAll('.toast')].some(t => t.innerText.includes('{words}'))"

            before, last = await to_last_task()  # "Email the referee", made here a moment ago
            await page.keyboard.press("Backspace")
            await page.wait_for_timeout(300)
            ed = await page.evaluate("window.zcNotes.text()")
            check(
                "Backspace removes a task line in one go",
                ed.count(f"{{{{task:{last}}}}}") == before.count(f"{{{{task:{last}}}}}") - 1
                and len(before) - len(ed) == len(f"{{{{task:{last}}}}}"),
                f"{ed[-60:]!r}",
            )

            # ---- a task made here whose line goes was a slip: to the trash, and back with its line
            check("A task just made in the note goes to the trash with its line", until(lambda: not exists(last), 8))
            check("…saying so, with Undo", await wait_for(page, toast_says("with its line")))
            await content.press(f"{MOD}+z")
            check("…and comes back with its line (Undo in the note)", until(lambda: exists(last)))
            await page.wait_for_timeout(2500)  # saved with the line back
            check("…and stays", exists(last) and f"{{{{task:{last}}}}}" in note_text())

            await to_last_task()
            await page.keyboard.press("Backspace")
            until(lambda: not exists(last), 8)
            await page.locator(".toast", has_text="with its line").last.locator(".toast-action").click()
            check("The toast's Undo keeps the task, out of the note", until(lambda: exists(last)))
            await content.press(f"{MOD}+End")
            await page.keyboard.type("\nzz")
            until(lambda: "zz" in note_text(), 8)
            await page.wait_for_timeout(800)
            check("…for good: the next save leaves it alone", exists(last))
            for _ in range(3):
                await page.keyboard.press("Backspace")

            # ---- a task that was there before stays when its line goes
            _, shown_twice = await to_last_task()  # the Ising task, also on an earlier line
            await page.keyboard.press("Backspace")
            _, older = await to_last_task()  # "Send the plots", made before the note was opened
            await page.keyboard.press("Backspace")
            until(lambda: f"{{{{task:{older}}}}}" not in note_text(), 8)
            await page.wait_for_timeout(800)
            check("A task made before stays when its line goes", exists(older) and exists(shown_twice))
            await content.press(f"{MOD}+z")
            await content.press(f"{MOD}+z")
            until(lambda: f"{{{{task:{older}}}}}" in note_text(), 8)

            # ---- moving a new task to another note: take its line out here, put it there
            await content.press(f"{MOD}+End")
            await page.keyboard.press("Enter")
            await page.keyboard.type("Ask about the cluster")
            await page.keyboard.press(f"{MOD}+Enter")
            await page.wait_for_selector(".task-popup-new")
            await page.keyboard.press("Enter")
            moved = until(lambda: [t["id"] for t in all_tasks()["tasks"] if t["title"] == "Ask about the cluster"])
            moved = moved[0] if moved else "none"
            await wait_for(page, f"window.zcNotes.text().includes('{{{{task:{moved}}}}}')")
            await to_last_task()
            await page.keyboard.press("Backspace")
            await page.locator(".notes-tree >> text=Other").first.click()
            await wait_for(page, f"window.zcNotes.path() === '{OTHER}'")
            check("Leaving the note saves it, and the task goes with its line", until(lambda: not exists(moved), 8))
            await content.press(f"{MOD}+End")
            await page.keyboard.press("Enter")
            await page.keyboard.insert_text(f"{{{{task:{moved}}}}}")
            await page.keyboard.press("Escape")
            check("Putting its line in another note brings it back", until(lambda: exists(moved), 8))
            check(
                "…shown in that note",
                until(lambda: any(r["path"] == OTHER for r in all_tasks()["refs"].get(moved, [])), 8),
            )
            await page.locator(".notes-tree >> text=Plan").first.click()
            await wait_for(page, f"window.zcNotes.path() === '{PATH}'")
            await page.wait_for_timeout(400)

            # ---- read mode
            await page.locator("[data-mode=read]").click()
            await page.wait_for_timeout(600)
            check("Read mode draws tasks too", await page.locator(".note-read .task-ref").count() >= 3)
            lefts = await page.evaluate(
                "[...document.querySelectorAll('.note-read .task-ref .task-check, .note-read input.task-box')]"
                ".map(e => Math.round(e.getBoundingClientRect().left))"
            )
            check("…with every box in one column", len(set(lefts)) == 1, str(lefts))
            await page.locator(".note-read .task-ref .task-check").first.click()
            check("…and ticks them", until(lambda: get(plot)["status"] == "done"))
            await page.locator("[data-mode=live]").click()

            # ---- the Tasks page
            await page.keyboard.press(f"{MOD}+Shift+K")
            ok = await wait_for(page, "document.body.dataset.workspace === 'tasks'")
            check(f"{MOD}+Shift+K opens Tasks", ok)
            await page.wait_for_selector(".tv-row")
            rows = await page.locator(".tv-row").count()
            check("Open tasks are listed, sub-tasks under their parent", rows >= 6, f"({rows} rows)")
            groups = await page.locator(".tv-group").all_inner_texts()
            check("Grouped by note", any("Plan" in g for g in groups), str(groups))
            count = await page.locator('.side-item[data-navigation="tasks.main"] .count').inner_text()
            check("The sidebar counts open tasks", count.strip().split()[-1].isdigit(), count)

            await page.locator(".tv-search-input").fill("due:week")
            await page.wait_for_timeout(300)
            titles = await page.locator(".tv-row .tv-title").all_inner_texts()
            check(
                "Search with a filter (due:week)",
                any("Check convergence" in t for t in titles) and not any("Send the plots" in t for t in titles),
                str(titles),
            )
            await page.locator(".tv-search-input").fill("")

            await page.locator(".tv-quick-input").fill("Book the room next week")
            await page.keyboard.press("Enter")
            made = until(lambda: [t for t in all_tasks()["tasks"] if t["title"] == "Book the room"])
            check("Quick add makes a task, reading the date at the end", bool(made) and made[0]["due"])
            sel = await wait_for(
                page, "(document.querySelector('.tv-detail .tf-title')?.innerText || '').includes('Book the room')"
            )
            check("…and selects it, with its details on the right", sel)

            # keyboard: Space ticks, Tab makes it a sub-task of the row above, Delete with Undo
            row = page.locator(".tv-row", has_text="Book the room")
            await row.click()
            await page.keyboard.press(" ")
            check("Space ticks the selected task", until(lambda: get(made[0]["id"])["status"] == "done"))
            check(
                "…which stays in the list, struck through",
                await page.locator(".tv-row.done", has_text="Book the room").count() == 1,
            )
            await page.keyboard.press(" ")
            check("Space again reopens it", until(lambda: get(made[0]["id"])["status"] == "open"))
            await page.keyboard.press("Delete")
            gone = until(lambda: not any(t["id"] == made[0]["id"] for t in all_tasks()["tasks"]))
            check("Delete moves it to the trash", gone)
            # its own toast: one from earlier (a task gone with its line) may still be showing
            undo = await wait_for(
                page, "[...document.querySelectorAll('.toast')].some(t => t.innerText.includes('Deleted 1 task'))"
            )
            if undo:
                await page.locator(".toast", has_text="Deleted 1 task").locator(".toast-action").click()
            check(
                "Undo brings it back",
                until(lambda: any(t["id"] == made[0]["id"] for t in all_tasks()["tasks"])),
                await page.evaluate("[...document.querySelectorAll('.toast')].map(t => t.innerText).join(' | ')"),
            )

            # a change made elsewhere (another window, or Claude) shows up
            other = task("Made in another window")
            seen = await wait_for(
                page,
                "[...document.querySelectorAll('.tv-row .tv-title')].some(e => e.innerText.includes('Made in another window'))",
                12000,
            )
            check("A task made elsewhere appears without a reload", seen)

            # Not in a note view; a note's tasks
            await page.locator("[data-tv-view=unfiled]").click()
            titles = await page.locator(".tv-row .tv-title").all_inner_texts()
            check(
                "“Not in a note” lists tasks no note shows",
                any("Made in another window" in t for t in titles) and not any("Ising" in t for t in titles),
            )
            await page.locator("[data-tv-note]", has_text="Plan").click()
            titles = await page.locator(".tv-row .tv-title").all_inner_texts()
            check(
                "A note in the views column lists its tasks",
                any("Ising" in t for t in titles) and not any("another window" in t for t in titles),
            )
            s.post(BASE + "/api/tasks/delete", json={"ids": [other]})

            # The plugin can leave and return without changing records or duplicating handlers.
            before_off = len(all_tasks()["tasks"])
            for cycle in range(2):
                s.post(BASE + "/api/plugin-host/tasks/disable").raise_for_status()
                off = await wait_for(
                    page,
                    "!document.querySelector('#tasks-view') && !document.querySelector('#sidebar [data-navigation=\"tasks.main\"]')",
                    12000,
                )
                check(f"off ({cycle + 1}): Tasks page and navigation disappear", off)
                check(
                    "off: legacy and plugin task routes are unavailable",
                    s.get(BASE + "/api/tasks").status_code == 404
                    and s.get(BASE + "/api/plugins/tasks/").status_code == 404,
                )
                await page.click('#sidebar [data-act="open-notes"]')
                await page.locator(".notes-tree >> text=Plan").first.click()
                await page.click('[data-mode="live"]')
                await page.wait_for_timeout(400)
                check(
                    "off: task IDs remain in notes, with no task controls",
                    await page.locator(".cm-content .cm-lp-chip.taskref").count() > 0
                    and await page.locator(".cm-content .task-ref").count() == 0
                    and await page.locator(".note-add-task").is_hidden(),
                )
                s.post(BASE + "/api/plugin-host/tasks/enable").raise_for_status()
                on = await wait_for(
                    page,
                    "!!document.querySelector('#tasks-view') && !!document.querySelector('#sidebar [data-navigation=\"tasks.main\"]')"
                    " && !document.querySelector('.note-add-task').hidden",
                    12000,
                )
                check(
                    f"on ({cycle + 1}): one page and note controls return",
                    on
                    and await page.locator("#tasks-view").count() == 1
                    and await page.locator(".note-add-task").count() == 1,
                )
                check("on: all tasks survive", len(all_tasks()["tasks"]) == before_off)
            await page.click('[data-mode="read"]')
            await page.wait_for_timeout(400)
            patches = []
            page.on(
                "request",
                lambda req: patches.append(req.url) if req.method == "PATCH" and "/api/tasks/" in req.url else None,
            )
            before_status = get(plot)["status"]
            await page.locator(f'.note-read .task-ref[data-task="{plot}"] .task-check').click()
            changed = until(lambda: get(plot)["status"] != before_status)
            await page.wait_for_timeout(300)
            check("one click after repeated toggles sends one task change", changed and len(patches) == 1, patches)

            check("No JavaScript errors", not errors, "; ".join(errors[:3]))
            await browser.close()
    finally:
        srv.terminate()
    print(f"\n{sum(results)}/{len(results)} passed")
    return all(results)


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
