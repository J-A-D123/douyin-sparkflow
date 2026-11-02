"""Ordinary chat UI adapter. No signed requests or platform parameter overrides."""
import asyncio
import base64
import json
import re
import unicodedata
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit
from playwright.async_api import Error as PlaywrightError

from core.browser import get_browser, get_persistent_browser_context, douyin_network_modes
from core.im_response import decode_conversation_response, wire_fields, field
from core.send_receipt import browser_receipt_is_strong

CHAT_URL = 'https://www.douyin.com/chat'
SELF_URL = 'https://www.douyin.com/user/self'


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value)).translate({ord(c): None for c in '\u200b\u200c\u200d\ufeff'}).split())


async def check_access(page):
    problem = await page.evaluate('''()=>{
        const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'};
        const masks=Array.from(document.querySelectorAll('.login-mask,.login-guide-container,.pc-login-verification-modal')).filter(visible);
        const dialogs=Array.from(document.querySelectorAll('[role="dialog"],.semi-modal-content')).filter(visible);
        const standalone=!document.querySelector('[data-e2e="conversation-item"],[contenteditable="true"]');
        const text=[...masks,...dialogs].map(e=>e.innerText||'').join(' ')+(standalone?(document.body?.innerText||''):'');
        const challengeFrame=Array.from(document.querySelectorAll('iframe')).some(e=>{const r=e.getBoundingClientRect();return visible(e)&&r.width>150&&r.height>150&&/captcha|verify/i.test(e.src)});
        if (challengeFrame||['请完成验证','请完成下方验证','请完成下列验证','按住左边按钮拖动','安全验证','环境异常','风险提示'].some(t=>text.includes(t)))return 'verification_required';
        if (masks.length||['扫码登录','登录/注册','Log in to Douyin','Scan to Log In'].some(t=>text.includes(t)))return 'login_required';
        return null;
    }''')
    if problem:
        raise RuntimeError(problem + ': 请由本人在正常页面完成登录或验证；未继续发送。')


def navigating(exc):
    return 'Execution context was destroyed' in str(exc) or 'Cannot find context with specified id' in str(exc)


async def verify_self(page, expected_id, timeout_seconds=60):
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        url = urlsplit(page.url)
        if url.hostname != 'www.douyin.com' or not url.path.startswith('/user/'):
            raise RuntimeError('account_identity_unverified: 尚未进入普通抖音本人资料页')
        try:
            await check_access(page)
            ids = await page.evaluate(r'''()=>{
            const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'};
            return [...new Set(Array.from(document.querySelectorAll('span,div')).filter(e=>visible(e)&&e.children.length===0).map(e=>(e.innerText||'').trim().match(/^抖音号\s*[：:]\s*([A-Za-z0-9_.-]+)\s*$/)).filter(Boolean).map(m=>m[1]))];
            }''')
        except PlaywrightError as exc:
            if not navigating(exc):raise
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError('account_identity_unverified: 本人资料页导航未完成') from exc
            await asyncio.sleep(0.25)
            continue
        if len(ids) > 1:
            raise RuntimeError('account_identity_unverified: 本人资料存在多个可见抖音号')
        if len(ids) == 1:
            if ids[0] != expected_id:
                raise RuntimeError('account_identity_mismatch: 普通聊天账号与保存账号不一致')
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError('account_identity_unverified: 无法核验普通聊天的本人抖音号')
        await asyncio.sleep(0.5)


async def wait_ready(page, timeout_seconds=90):
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        try:
            await check_access(page)
            search = page.get_by_placeholder('搜索', exact=True)
            rows = page.locator('[data-e2e="conversation-item"]')
            if await search.count() == 1 and await search.is_visible() and await rows.count() > 0:
                return
        except PlaywrightError as exc:
            if not navigating(exc):raise
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError('chat_page_unavailable: 普通聊天页面没有就绪')
        await asyncio.sleep(0.5)


async def matching_rows(page, target):
    rows = page.locator('[data-e2e="conversation-item"]')
    matches = []
    for i in range(await rows.count()):
        row = rows.nth(i)
        titles = row.locator('[class~="conversationConversationItemtitle"]')
        visible = [titles.nth(j) for j in range(await titles.count()) if await titles.nth(j).is_visible()]
        if await row.is_visible() and len(visible) == 1 and normalized(await visible[0].inner_text()) == normalized(target):
            matches.append(row)
    return matches


async def verify_recipient(page, target, timeout_seconds=15):
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    stable = 0
    while True:
        await check_access(page)
        state = await page.evaluate(r'''target=>{
            const norm=s=>String(s||'').normalize('NFKC').replace(/[\u200b-\u200d\ufeff]/g,'').replace(/\s+/g,' ').trim();
            const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'};
            const rows=Array.from(document.querySelectorAll('[data-e2e="conversation-item"]')).filter(visible).filter(row=>{
                const titles=Array.from(row.querySelectorAll('[class~="conversationConversationItemtitle"]')).filter(visible);
                return titles.length===1&&norm(titles[0].innerText)===norm(target);
            });
            const heads=Array.from(document.querySelectorAll('[class~="RightPanelHeadertitle"]')).filter(visible);
            const state={matchingRows:rows.length,selected:rows.length===1&&rows[0].classList.contains('conversationConversationItemcurConversation'),headerCount:heads.length,headerMatches:heads.length===1&&norm(heads[0].innerText)===norm(target)};
            state.ready=state.matchingRows===1&&state.selected&&state.headerMatches;
            return state;
        }''',target)
        if state['ready']:
            stable += 1
            if stable >= 2:return
        else:
            stable = 0
        if asyncio.get_running_loop().time() >= deadline:
            folder=Path('logs/diagnostics')
            folder.mkdir(parents=True,exist_ok=True)
            (folder/'ordinary-recipient-state.json').write_text(json.dumps(state),encoding='utf-8')
            raise RuntimeError('recipient_mismatch: 普通聊天的选中联系人与会话标题未能同时核验')
        await asyncio.sleep(0.25)


async def select_target(page, target):
    await check_access(page)
    rows = await matching_rows(page, target)
    hydrate_deadline = asyncio.get_running_loop().time() + 8
    while not rows and asyncio.get_running_loop().time() < hydrate_deadline:
        await check_access(page)
        await asyncio.sleep(0.25)
        rows = await matching_rows(page, target)
    if not rows:
        search = page.get_by_placeholder('搜索', exact=True)
        await search.fill(target)
        deadline = asyncio.get_running_loop().time() + 10
        while not rows and asyncio.get_running_loop().time() < deadline:
            await check_access(page)
            rows = await matching_rows(page, target)
            await asyncio.sleep(0.25)
    if len(rows) != 1:
        raise RuntimeError('recipient_mismatch: 未找到唯一的已配置普通聊天联系人')
    for attempt in range(2):
        rows = await matching_rows(page, target)
        if len(rows) != 1:
            raise RuntimeError('recipient_mismatch: 联系人列表发生变化，未发送')
        await rows[0].click(timeout=5000)
        try:
            await verify_recipient(page, target)
            return
        except RuntimeError as exc:
            if attempt or not str(exc).startswith('recipient_mismatch:'):
                raise
            # The site may asynchronously restore a previous conversation.
            # Reselect the same unique row once, only before typing or sending.
            await check_access(page)


async def prepare_message(page, editor, message):
    from core import tasks
    current = normalized(await tasks.read_chat_input_text(editor))
    if current and current != normalized(message):
        raise RuntimeError('Existing draft preserved; no message sent')
    await editor.click()
    if not current:
        await editor.press_sequentially(message)
    # The rich editor commits keyboard input asynchronously. Wait for the exact
    # text; never type a second copy or overwrite a different existing draft.
    deadline = asyncio.get_running_loop().time() + 5
    stable = 0
    while True:
        observed = normalized(await tasks.read_chat_input_text(editor))
        stable = stable + 1 if observed == normalized(message) else 0
        if stable >= 2:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError(f'Chat input did not accept the exact message (expectedChars={len(normalized(message))}, observedChars={len(observed)}); no send attempted')
        await asyncio.sleep(0.1)


async def trigger_send(page, editor):
    """Click the ordinary chat's own send control, bound to this editor."""
    controls = page.locator('[class~="messageMsgInputinputColumn"] [class~="e2e-send-msg-btn"]')
    visible = [controls.nth(i) for i in range(await controls.count()) if await controls.nth(i).is_visible()]
    if len(visible) != 1:
        raise RuntimeError('Ordinary chat send control is not unique; no send attempted')
    control = visible[0]
    if not await editor.evaluate('(editor,control)=>Boolean(editor.closest(".messageMsgInputinputColumn")?.contains(control))', await control.element_handle()):
        raise RuntimeError('Ordinary chat send control does not belong to the editor; no send attempted')
    if not await control.is_enabled() or await control.get_attribute('aria-disabled') == 'true':
        raise RuntimeError('Ordinary chat send control is disabled; no send attempted')
    await control.click(timeout=3000)


async def verify_prepared(page, editor, message, timeout_seconds=5):
    """Visible text alone is insufficient: the site's editor must enable send."""
    from core import tasks
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        exact = normalized(await tasks.read_chat_input_text(editor)) == normalized(message)
        ready = await editor.evaluate('''editor=>{
            const box=editor.closest('.messageMsgInputinputColumn');
            const controls=box?.querySelectorAll('.e2e-send-msg-btn')||[];
            return controls.length===1&&controls[0].classList.contains('messageMsgInputpublishRedBtn');
        }''')
        if exact and ready:return
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError(f'editor_not_ready: 页面编辑器尚未确认待发送正文 (textMatches={exact}, sendControlReady={ready})；未执行发送')
        await asyncio.sleep(0.1)


def check_history_message(messages, message_id, text, minimum_created_at_ms=None):
    for item in messages:
        if item['serverMessageId'] != str(message_id):
            continue
        if minimum_created_at_ms is not None and item.get('createdAt',0) < minimum_created_at_ms:
            return False
        if item.get('visibilityRestricted'):
            raise RuntimeError('platform_self_visible: 服务器消息存在可见范围限制；停止发送')
        # This extension is not SendMessageResponseBody.status/check_code.
        # Preserve it verbatim; never fabricate zero or interpret every
        # unmapped extension as a business rejection. The observed self-only
        # callback remains blocked, as do explicit SDK result/visibility flags.
        if item.get('callbackCode') == '8101':
            raise RuntimeError('platform_message_rejected: 服务器业务回调未通过；停止发送')
        if item.get('sendStatus') not in (None,'0') or item.get('sendCheckCode') not in (None,'0'):
            raise RuntimeError('platform_message_rejected: 历史发送结果明确未通过；停止发送')
        try:
            content = json.loads(item['content'])
        except (ValueError, TypeError):
            return False
        return (item.get('isSelf') is True
                and isinstance(content, dict) and content.get('text') == text)
    return False


async def readback(user, target, message_id, text, network_mode, diagnostics=None, *, GUI=False, minimum_created_at_ms=None):
    """A fresh context reads normal UI history; no optimistic local cache is trusted."""
    pw, browser = await get_browser(GUI=GUI, network_mode=network_mode)
    context = await browser.new_context(service_workers='block')
    await context.add_cookies(user['cookies'])
    page = await context.new_page()
    messages, jobs = [], []
    async def receive(response):
        url = urlsplit(response.url)
        if url.hostname == 'imapi.douyin.com' and '/message/' in url.path and '/send' not in url.path:
            try:
                raw = await response.body()
                if diagnostics is not None:
                    diagnostics.setdefault('commands', []).append(field(wire_fields(raw), 1, 0))
                messages.extend(decode_conversation_response(raw))
                if diagnostics is not None:
                    diagnostics['messageCount'] = len(messages)
            except (ValueError, UnicodeDecodeError):
                pass
    page.on('response', lambda response: jobs.append(asyncio.create_task(receive(response))))
    async def readonly(route):
        if re.search(r'message[/_]send|send[/_]message|message/send', urlsplit(route.request.url).path, re.I):
            await route.abort()
        else:
            await route.continue_()
    await context.route('**/*', readonly)
    try:
        await page.goto(SELF_URL, wait_until='commit', timeout=30000)
        await verify_self(page, user['unique_id'])
        await page.goto(CHAT_URL, wait_until='commit', timeout=30000)
        await wait_ready(page)
        if diagnostics is not None:
            diagnostics['conversationRows'] = await page.locator('[data-e2e="conversation-item"]').count()
            diagnostics['matchingRows'] = len(await matching_rows(page, target))
            diagnostics['titles'] = await page.locator('[data-e2e="conversation-item"]').evaluate_all('''(rows,target)=>rows.map(row=>({titles:Array.from(row.querySelectorAll('[class*="conversationConversationItemtitle"]')).map(e=>({cls:e.className,chars:(e.innerText||'').length,matches:(e.innerText||'').trim()===target}))}))''', target)
        deadline = asyncio.get_running_loop().time() + 25
        selected = False
        while True:
            if check_history_message(messages, message_id, text, minimum_created_at_ms):
                if diagnostics is not None:
                    matched=next(item for item in messages if item['serverMessageId']==str(message_id))
                    diagnostics['verifiedMessage']={key:matched.get(key) for key in ('callbackCode','sendStatus','sendCheckCode','createdAt','visibilityRestricted')}
                return True
            # Initial server sync already contains recent messages. Its exact
            # server ID can be verified even while contact names hydrate.
            if not selected and len(await matching_rows(page, target)) == 1:
                await select_target(page, target)
                selected = True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.5)
    finally:
        await context.close()
        await asyncio.gather(*jobs, return_exceptions=True)
        await browser.close()
        await pw.stop()


@asynccontextmanager
async def account_page(user, config):
    from core import tasks
    profile = tasks._normalize_persistent_profile_config(config)
    handle, path = await tasks._acquire_browser_account_lock(user, 'ordinary-chat')
    pw = context = browser = None
    network_mode = douyin_network_modes()[0]
    try:
        if profile['enabled']:
            pw, context, _ = await get_persistent_browser_context(tasks._account_profile_name(user), GUI=bool(config.get('_visibleSender')), root=profile['root'], network_mode=network_mode)
        else:
            pw, browser = await get_browser(GUI=bool(config.get('_visibleSender')), network_mode=network_mode)
            context = await browser.new_context()
        context.set_default_timeout(15000)
        await context.add_cookies(user['cookies'])
        page = await context.new_page()
        await page.goto(SELF_URL, wait_until='commit', timeout=30000)
        await verify_self(page, user['unique_id'])
        yield context, page, network_mode
    finally:
        if context:await context.close()
        if browser:await browser.close()
        if pw:await pw.stop()
        tasks._release_browser_account_lock(handle, path, 'ordinary-chat')


async def run_accounts(config, users):
    from core import tasks
    for user in users:
        target, receipt, message = '', {}, ''
        stage = 'account_identity'
        try:
            for target in user['targets']:
                receipt, message = {}, ''
                stage = 'account_identity'
                tasks.logger.info('Ordinary chat preparation started')
                async with account_page(user, config) as (context, page, network_mode):
                    # Match preparation diagnostics without altering requests.
                    # Playwright routing disables HTTP cache.
                    async def fresh_resources(route):
                        await route.continue_()
                    await context.route('**/*', fresh_resources)
                    stage = 'open_chat'
                    await page.goto(CHAT_URL, wait_until='commit', timeout=30000)
                    await wait_ready(page)
                    receipt = {}
                    stage = 'select_target'
                    await select_target(page, target)
                    tasks.logger.info('Ordinary chat recipient verified')
                    stage = 'focus_editor'
                    editor, _ = await tasks.locate_chat_input(page)
                    await verify_recipient(page, target)
                    message = tasks.build_message(config=config, previous_message=str(((user.get('message_history') or {}).get(target) or {}).get('message') or ''))
                    stage = 'prepare_message'
                    await prepare_message(page, editor, message)
                    await verify_recipient(page, target)
                    await verify_prepared(page, editor, message)
                    tasks.logger.info('Ordinary chat editor acknowledged the configured message')
                    stage = 'arm_observer'
                    observer = await tasks.start_im_send_observer(page, 'ordinary-chat', 'configured-friend')
                    attempted_at = datetime.now(timezone.utc)
                    tasks._persist_dispatch_reservation(user, target, message)
                    await verify_recipient(page, target)
                    await verify_prepared(page, editor, message)
                    stage = 'send_requested'
                    tasks.logger.info('Ordinary chat requesting one UI send')
                    await trigger_send(page, editor)
                    stage = 'await_receipt'
                    summary = await observer(extra_wait_seconds=20)
                    tasks.logger.info('Ordinary chat observation request=%s response=%s',bool(summary.get('send_request_seen')),bool(summary.get('send_response_seen')))
                    receipt = summary.get('send_receipt') or {}
                    if not receipt:
                        receipt = {'ok': False, 'observation': {
                            'requestSeen': bool(summary.get('send_request_seen')),
                            'responseSeen': bool(summary.get('send_response_seen')),
                            'draftMatchesAfterClick': normalized(await tasks.read_chat_input_text(editor)) == normalized(message),
                            'observerEnabled': bool(summary.get('enabled')),
                        }}
                    if receipt.get('sendStatus') == 4:
                        raise RuntimeError('platform_self_visible: 平台只允许发送者可见；停止发送')
                    if receipt.get('protocolStatus') or receipt.get('sendStatus') or receipt.get('checkCode'):
                        raise RuntimeError('platform_message_rejected: 平台未通过发送检查；停止发送')
                    if not (summary.get('send_request_seen') and summary.get('send_response_seen') and browser_receipt_is_strong(receipt)):
                        raise RuntimeError('server send response is unconfirmed; do not repeat automatically')
                    fresh_user = dict(user, cookies=await context.cookies())
                    await tasks.refresh_stored_cookies_from_profile(context,user,'ordinary-chat')
                # Close Chromium before opening the independent verifier. Two
                # concurrent instances can exceed the desktop's PID limit.
                stage = 'server_readback'
                verification={}
                if not await readback(fresh_user, target, receipt.get('serverMessageId') or receipt.get('messageId'), message, network_mode, diagnostics=verification, GUI=bool(config.get('_visibleSender')), minimum_created_at_ms=int(attempted_at.timestamp()*1000)-60000):
                    raise RuntimeError('server send history could not verify message ID and visibility; do not repeat')
                verified_message=verification.get('verifiedMessage')or{}
                receipt.update(chatInterface='ordinary', serverHistoryVerified=True, callbackCode=verified_message.get('callbackCode'), historySendStatus=verified_message.get('sendStatus'), historyCheckCode=verified_message.get('sendCheckCode'), peerVisibilityRestricted=False)
                tasks._persist_browser_send_success(user, target, message, attempted_at.isoformat(timespec='seconds'), server_receipt=receipt)
                strategy = tasks._normalize_send_strategy(config)
                await tasks._sleep_with_log(tasks._random_delay_seconds(strategy, 'messageIntervalSecondsMin', 'messageIntervalSecondsMax'), 'Ordinary chat pacing', 'configured-account')
        except Exception as exc:
            category = tasks.classify_browser_failure('send_flow', exc)
            now = datetime.now(timezone.utc).isoformat(timespec='seconds')
            reason = f'stage={stage}; {exc}'
            if tasks._is_stop_account_category(category) or not target:
                tasks._persist_account_send_failure(user, category, reason, now, user['targets'], server_receipt=receipt)
            else:
                tasks._persist_browser_send_failure(user, target, message, category, reason, now, server_receipt=receipt)
            tasks.logger.warning('Ordinary chat stopped stage=%s category=%s; no automatic replay', stage, category)


async def check_prepared(user, config):
    """Exercise the production preparation path without requesting a send."""
    from core import tasks
    if len(user.get('targets') or []) != 1:
        raise ValueError('Prepare check requires exactly one configured target')
    async with account_page(user, config) as (context, page, _):
        async def no_send(route):
            if re.search(r'message[/_]send|send[/_]message|message/send',urlsplit(route.request.url).path,re.I):
                await route.abort()
            else:await route.continue_()
        await context.route('**/*',no_send)
        await page.goto(CHAT_URL,wait_until='commit',timeout=30000)
        await wait_ready(page)
        target=user['targets'][0]
        try:
            await select_target(page,target)
            editor,_=await tasks.locate_chat_input(page)
            await verify_recipient(page,target)
            message=tasks.build_message(config=config)
            await prepare_message(page,editor,message)
            await verify_recipient(page,target)
            await verify_prepared(page,editor,message)
        except Exception:
            # Only the explicitly invoked, authenticated preparation diagnostic
            # captures a local screenshot. Never include it in shared reports.
            try:
                folder=Path('logs/diagnostics');folder.mkdir(parents=True,exist_ok=True)
                session=await context.new_cdp_session(page)
                shot=await asyncio.wait_for(session.send('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False}),timeout=5)
                (folder/'ordinary-prepare-private.png').write_bytes(base64.b64decode(shot['data']))
                state=await page.evaluate('''()=>({ready:document.readyState,visibility:document.visibilityState,focused:document.hasFocus(),frames:document.querySelectorAll('iframe').length})''')
                (folder/'ordinary-prepare-state.json').write_text(json.dumps(state),encoding='utf-8')
            except Exception:pass
            raise
    return {'prepared':True,'sendActions':0,'draftPreserved':True}


async def fetch_conversation_names(user, config):
    async with account_page(user, config) as (_, page, __):
        await page.goto(CHAT_URL, wait_until='commit', timeout=30000)
        await wait_ready(page)
        await page.wait_for_timeout(4000)
        rows = page.locator('[data-e2e="conversation-item"]')
        names = []
        for i in range(await rows.count()):
            title = rows.nth(i).locator('[class~="conversationConversationItemtitle"]')
            for j in range(await title.count()):
                if await title.nth(j).is_visible():
                    name = (await title.nth(j).inner_text()).strip()
                    if name and name not in names:names.append(name)
        return names
