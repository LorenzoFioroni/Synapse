"""Pending passkeys and updates with deferred browser/service stubs; never installs code or uses keys."""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.e2e._portable import browser_type, screenshot, server  # noqa: E402


async def main():
    with server(8811) as base:
        async with async_playwright() as p:
            browser = await browser_type(p).launch()
            page = await browser.new_page()
            await page.goto(base)
            await page.wait_for_selector("#toasts", state="attached")
            await page.evaluate("""async () => {
                const {api, ApiError} = await import('/js/api.js');
                window.testApi = api;
                const {asAdmin} = await import('/js/views/settings/confirm.js');
                api.confirmOptions = async () => ({challenge:'AQID', rpId:'localhost', userVerification:'required'});
                api.confirm = async () => { window.confirmed = true; };
                Object.defineProperty(navigator, 'credentials', {configurable:true, value:{get: () => new Promise((resolve,reject) => {window.credentialResolve=resolve; window.credentialReject=reject;})}});
                window.adminTest = () => {
                    window.attempts=0; window.confirmed=false; window.outcome=undefined;
                    asAdmin(async () => { if (++window.attempts === 1) throw new ApiError('Confirm',403,{confirm:true}); return 'done'; })
                      .then(v => window.outcome=v).catch(e => window.outcome=e.message);
                };
                window.adminTest();
            }""")
            pending = page.locator(".operation-status")
            await pending.wait_for()
            assert "password manager" in await pending.inner_text()
            await page.evaluate("window.credentialReject(new DOMException('Cancelled','NotAllowedError'))")
            await page.wait_for_function("() => window.outcome === null")
            assert await pending.count() == 0
            assert await page.evaluate("window.attempts === 1 && !window.confirmed")
            print("PASS cancelled passkey clears feedback and does not retry administrative action")
            await page.evaluate("window.adminTest()")
            await pending.wait_for()
            await page.evaluate("window.credentialReject(new Error('Authenticator unavailable'))")
            await page.wait_for_function("() => window.outcome === 'Authenticator unavailable'")
            assert await pending.count() == 0
            print("PASS failed passkey clears feedback")
            await page.evaluate("window.adminTest()")
            await pending.wait_for()
            await page.evaluate(
                "window.credentialResolve({id:'test', response:{clientDataJSON:new ArrayBuffer(0), authenticatorData:new ArrayBuffer(0), signature:new ArrayBuffer(0)}})"
            )
            await page.wait_for_function("() => window.outcome === 'done'")
            assert await pending.count() == 0
            assert await page.evaluate("window.attempts === 2 && window.confirmed")
            print("PASS successful passkey confirms with server before retry and clears feedback")
            await page.evaluate("""async () => {
                const {api} = await import('/js/api.js');
                const {bindPasskeys, refreshPasskeys} = await import('/js/views/settings/passkeys.js');
                api.passkeys=async () => ({this_computer:true,can_confirm:true,rp_id:'localhost',passkeys:[]});
                api.passkeyOptions=async () => ({rp:{name:'Test'},user:{id:'AQID',name:'Test',displayName:'Test'},challenge:'AQID',pubKeyCredParams:[]});
                navigator.credentials.create=() => new Promise((resolve,reject) => {window.enrollReject=reject;});
                const view=document.createElement('section');
                view.innerHTML='<div data-passkeys-desc></div><div data-passkey-control></div><div data-passkey-list></div>';
                document.body.append(view);
                bindPasskeys(view); await refreshPasskeys(view);
            }""")
            await page.locator("[data-passkey-add]").click()
            await page.get_by_role("button", name="Continue", exact=True).click()
            await pending.wait_for()
            assert "new passkey" in await pending.inner_text()
            await page.evaluate("window.enrollReject(new DOMException('Cancelled','NotAllowedError'))")
            await pending.wait_for(state="detached")
            assert await page.locator("[data-passkey-add]").is_enabled()
            print("PASS enrollment explains browser prompt and cancellation clears feedback")
            await page.evaluate("""async () => {
                const {api} = await import('/js/api.js');
                const {bindUpdates, refreshUpdates} = await import('/js/views/settings/updates.js');
                const view = document.createElement('section');
                view.innerHTML='<div data-update-lead></div><div data-update-desc></div><div data-update-body></div><button data-update-check>Check for Updates</button>';
                document.body.append(view);
                window.updateState={source:{url:'https://example.com',branch:'main',repo:'example/Synapse'},current:{commit:'old',short:'old'},latest:{commit:'new',short:'new'},available:true,can_install:true,behind:1,commits:[]};
                api.updates=async () => window.updateState;
                api.installUpdate=() => new Promise((resolve,reject) => {window.installResolve=resolve; window.installReject=reject;});
                bindUpdates(view); await refreshUpdates(view);
                window.updateView=view;
            }""")
            # The existing confirmation remains necessary before any install request.
            install = page.locator("[data-update-install]")
            await install.click()
            await page.get_by_role("button", name="Cancel", exact=True).click()
            assert await pending.count() == 0
            assert await page.evaluate("!window.installResolve")
            await install.click()
            await page.get_by_role("button", name="Update", exact=True).click()
            await pending.wait_for()
            assert "backup" in await pending.inner_text()
            assert await install.is_disabled()
            await page.evaluate("window.installReject(new Error('Backup failed'))")
            await page.wait_for_function("() => !document.querySelector('[data-update-install]').disabled")
            assert await pending.count() == 0
            print("PASS update confirmation is preserved; preparation feedback clears after backup failure")
            await install.click()
            await page.get_by_role("button", name="Update", exact=True).click()
            await pending.wait_for()
            await page.evaluate("""() => {
                const started={state:'running',commit:'new',message:'Building test update'};
                window.updateState.install=started;
                window.installResolve({...window.updateState,started});
            }""")
            await page.wait_for_function(
                "() => document.querySelector('[data-update-body]').textContent.includes('Building test update')"
            )
            assert await pending.count() == 1
            await pending.evaluate("el => Promise.all(el.getAnimations().map(animation => animation.finished))")
            await screenshot(page, "operation-feedback.png")
            await page.evaluate("() => {window.testApi.updates = async () => {throw new Error('Restarting')};}")
            await page.wait_for_function(
                "() => document.querySelector('.operation-status').textContent.includes('reconnect')"
            )
            await page.evaluate(
                "() => {window.testApi.updates = async () => ({...window.updateState,install:{state:'failed',commit:'new',message:'Test update failed'}});}"
            )
            await pending.wait_for(state="detached")
            assert "Test update failed" in await page.locator("[data-update-body]").inner_text()
            print("PASS installing/reconnecting feedback persists and clears on terminal failure")
            await page.evaluate(
                "() => {window.testApi.updates=() => new Promise((resolve,reject) => {window.checkReject=reject;});}"
            )
            check = page.locator("[data-update-check]")
            await check.click()
            await pending.wait_for()
            assert await check.is_disabled()
            await page.evaluate("window.checkReject(new Error('Offline'))")
            await pending.wait_for(state="detached")
            assert await check.is_enabled()
            assert "Offline" in await page.locator("[data-update-desc]").inner_text()
            print("PASS checking feedback clears and button recovers after network failure")
            await browser.close()
    print("7/7 passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
