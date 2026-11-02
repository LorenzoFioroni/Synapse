"""Portable sign-in feature detection and error/cancellation UI with credential/service stubs.

Real registration/sign-in/administrative confirmation use Chromium's CDP virtual authenticator
in passkeys_checklist.py. This checklist does not claim a native OS authenticator ceremony.
"""

import asyncio
import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.e2e._portable import browser_type, screenshot, server  # noqa: E402

results = []


def check(name, condition):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + name, flush=True)


async def main():
    with server(8810) as base:
        async with async_playwright() as p:
            browser = await browser_type(p).launch()
            context = await browser.new_context(**p.devices["iPad Pro 11"])
            mode = "cancel"
            calls = []

            async def route(request):
                path = urlsplit(request.request.url).path
                if path == "/api/passkeys/login/options":
                    calls.append(path)
                    if mode == "service-error":
                        await request.fulfill(status=503, json={"error": "Passkey service unavailable"})
                    else:
                        await request.fulfill(
                            json={
                                "challenge": "AQID",
                                "rpId": "synapse.test",
                                "userVerification": "required",
                                "timeout": 1000,
                            }
                        )
                else:
                    # Serve the real built sign-in page/script under a named HTTPS browser origin.
                    response = requests.get(base + ("/signin.html" if path == "/" else path), timeout=5)
                    await request.fulfill(
                        status=401 if path == "/" else response.status_code,
                        body=response.content,
                        headers={
                            k: v
                            for k, v in response.headers.items()
                            if k.lower() in {"content-type", "content-security-policy"}
                        },
                    )

            await context.route("**/*", route)
            await context.add_init_script(
                "Object.defineProperty(window, 'PublicKeyCredential', {value: function(){}}); Object.defineProperty(navigator, 'credentials', {value: {get: async () => { throw new DOMException('Cancelled', 'NotAllowedError'); }}});"
            )
            page = await context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto("https://synapse.test/")
            await page.wait_for_selector("#go", state="visible")
            check(
                "named HTTPS offers sign-in with a credential stub", not await page.locator("#nopasskey").is_visible()
            )
            await page.locator("#go").tap()
            await page.wait_for_selector("#error", state="visible")
            check(
                "credential cancellation has actionable feedback",
                "cancelled" in await page.locator("#error").inner_text() and await page.locator("#go").is_enabled(),
            )
            mode = "service-error"
            await page.locator("#go").tap()
            await page.wait_for_function(
                "() => document.querySelector('#error').textContent.includes('service unavailable')"
            )
            check(
                "service errors restore the sign-in button", await page.locator("#go").is_enabled() and len(calls) == 2
            )
            await screenshot(page, "passkey-error.png")
            for url in ["http://synapse.test/", "https://192.0.2.1/"]:
                await page.goto(url)
                await page.wait_for_selector("#nopasskey", state="visible")
                check(url + " explains the unavailable passkey setup", not await page.locator("#passkey").is_visible())
            unsupported = await browser.new_context()
            await unsupported.route("**/*", route)
            await unsupported.add_init_script(
                "Object.defineProperty(window, 'PublicKeyCredential', {value: undefined});"
            )
            missing = await unsupported.new_page()
            missing.on("pageerror", lambda error: errors.append(str(error)))
            await missing.goto("https://synapse.test/")
            await missing.wait_for_selector("#nopasskey", state="visible")
            check("missing credential support has setup guidance", not await missing.locator("#passkey").is_visible())
            await unsupported.close()
            check("no browser exceptions", not errors)
            if errors:
                print(errors)
            await browser.close()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
