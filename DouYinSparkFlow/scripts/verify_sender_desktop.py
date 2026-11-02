"""Open the same sender profile for its owner to complete platform verification.

No automated puzzle interaction and no message sending are performed.
Run in login-desktop so the browser is visible through local noVNC.
"""
import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from core import tasks
from core.browser import get_persistent_browser_context, douyin_network_modes
from core.ordinary_chat import SELF_URL, verify_self
from utils.config import get_config, get_userData


async def run(account_ref=None, timeout_seconds=600):
    tasks.logger.disabled = True
    users = get_userData(force_reload=True)
    if account_ref:
        users = [u for u in users if u.get('account_ref') == account_ref]
    if len(users) != 1:
        raise RuntimeError('Select exactly one saved account with --account-ref')
    user = users[0]
    profile = tasks._normalize_persistent_profile_config(get_config(force_reload=True))
    state = Path('logs/sender-verification-state.json')
    def report(phase):
        payload = {'phase': phase, 'sendActions': 0}
        state.write_text(json.dumps(payload), encoding='utf-8')
        print(json.dumps(payload), flush=True)
    pw = context = None
    with tasks.task_run_lock() as lock:
        with lock.open('a') as info:info.write('purpose=manual_verification\n')
        handle, path = await tasks._acquire_browser_account_lock(user, 'owner-verification')
        try:
            pw, context, _ = await get_persistent_browser_context(tasks._account_profile_name(user), GUI=True, root=profile['root'], network_mode=douyin_network_modes()[0])
            await context.add_cookies(user['cookies'])
            async def no_messages(route):
                if re.search(r'message[/_]send|send[/_]message|message/send', urlsplit(route.request.url).path, re.I):
                    await route.abort()
                else:
                    await route.continue_()
            await context.route('**/*', no_messages)
            page = await context.new_page()
            try:await page.goto(SELF_URL, wait_until='commit', timeout=30000)
            except PlaywrightError:pass
            await page.bring_to_front()
            report('waiting_for_owner')
            deadline = time.monotonic() + timeout_seconds
            last_phase = 'waiting_for_owner'
            while time.monotonic() < deadline:
                try:
                    await verify_self(page, user['unique_id'], timeout_seconds=0)
                    await tasks.refresh_stored_cookies_from_profile(context, user, 'owner-verification')
                    report('verified_and_saved')
                    await asyncio.sleep(3)
                    return
                except RuntimeError as exc:
                    phase = 'verification_required' if 'verification_required' in str(exc) else 'waiting_for_owner'
                    if 'account_identity_mismatch' in str(exc):phase = 'wrong_account'
                    if phase != last_phase:report(phase);last_phase = phase
                except PlaywrightError:
                    if page.is_closed():report('closed_without_verification');return
                await asyncio.sleep(1)
            report('timed_out_without_verification')
        finally:
            if context:await context.close()
            if pw:await pw.stop()
            tasks._release_browser_account_lock(handle, path, 'owner-verification')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account-ref')
    args = parser.parse_args()
    asyncio.run(run(args.account_ref))
