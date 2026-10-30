"""CSP enforcement in a real browser; uses only a throw-away library and local server."""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[2]
PORT = 8817
BASE = f"http://127.0.0.1:{PORT}"
results = []


def check(name, condition, extra=""):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + name, extra)


async def main():
    data = tempfile.mkdtemp(prefix="synapse-csp-")
    server = subprocess.Popen(
        [sys.executable, "server.py", "--no-browser", "--port", str(PORT)],
        cwd=APP,
        env={**os.environ, "SYNAPSE_DATA_DIR": data},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                if requests.get(BASE, timeout=1).ok:
                    break
            except requests.RequestException:
                pass
            await asyncio.sleep(0.1)
        requests.put(BASE + "/api/settings", json={"references_auto": False}, timeout=5).raise_for_status()
        requests.post(
            BASE + "/api/notes/note",
            json={"title": "CSP probe", "text": "# CSP probe\n\n$x^2$\n\n```mermaid\ngraph TD\nA --> B\n```\n"},
            timeout=5,
        ).raise_for_status()
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            context = await browser.new_context()
            await context.add_init_script("""
                localStorage.setItem('zc.themeHint', JSON.stringify('dark'));
                window.cspViolations = [];
                document.addEventListener('securitypolicyviolation', e => {
                    window.cspViolations.push({directive: e.effectiveDirective, blocked: e.blockedURI});
                });
            """)
            page = await context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            ready = asyncio.Event()

            async def defer_main(route):
                await ready.wait()
                await route.continue_()

            await page.route("**/js/main.js", defer_main)
            await page.goto(BASE, wait_until="commit")
            await page.wait_for_selector("#startup-screen")
            check(
                "cached theme runs from a local blocking script",
                await page.locator("html").get_attribute("data-theme") == "dark",
            )
            ready.set()
            await page.wait_for_function("() => window.zcPlugins?.loaded().includes('tasks')")
            await page.click('[data-act="open-notes"]')
            await page.click('.nt-row.note[data-path="CSP probe.md"]')
            await page.wait_for_selector(".cm-lp-math .katex")
            await page.wait_for_selector(".cm-lp-mermaid svg")
            check("notes math, Mermaid and plugin modules load", True)
            check("normal app has no CSP violations", not await page.evaluate("window.cspViolations"))
            check("normal app has no page errors", not errors, errors)

            async def serve_probes(route):
                await route.fulfill(path=APP / "tests/e2e/fixtures/csp-probes.js", content_type="text/javascript")

            await page.route("**/csp-probes.js", serve_probes)
            await page.add_script_tag(url=BASE + "/csp-probes.js")
            await page.wait_for_function("() => Boolean(window.cspProbes)")
            probes = await page.evaluate("window.cspProbes")
            check("WebAssembly compilation is allowed for PDF decoders", probes["wasmAllowed"])
            directives = {v["directive"] for v in probes["violations"]}
            check(
                "inline scripts and event handlers cannot execute",
                not probes["injected"] and "script-src-attr" in directives,
            )
            check("JavaScript eval remains blocked", probes["evalBlocked"] and "script-src" in directives)
            check(
                "foreign scripts are blocked",
                any(v["blocked"].startswith("http://localhost:") for v in probes["violations"]),
            )
            check("blob workers are blocked", "worker-src" in directives)
            check("objects are blocked", "object-src" in directives)
            check("base URL cannot be replaced", probes["baseUnchanged"] and "base-uri" in directives)
            await browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(data, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _guard import guard  # noqa: E402

guard(PORT)
raise SystemExit(asyncio.run(main()))
