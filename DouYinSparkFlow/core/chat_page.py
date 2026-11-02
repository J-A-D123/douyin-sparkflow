"""Recognize access screens before interacting with Creator's friend chat."""
import asyncio

FRIEND_LABELS = ('朋友私信', '好友私信', '朋友', '好友', '互关')


async def wait_for_chat_ready(page, timeout_seconds=120):
    """Wait for usable chat controls, independently of document/analytics load."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        await ensure_chat_access(page)
        app = page.locator('#sub-app')
        if await app.is_visible():
            rows = app.locator('[class*="item-header-name-"], li[class*="semi-list-item"]')
            for index in range(min(await rows.count(), 5)):
                if await rows.nth(index).is_visible():
                    return
            for label in FRIEND_LABELS:
                locator = app.get_by_text(label, exact=True)
                for index in range(min(await locator.count(), 5)):
                    if await locator.nth(index).is_visible():
                        return
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError('chat_page_unavailable: 等待私信应用可用超时，未执行发送。请检查网络、登录及页面加载状态。')
        await asyncio.sleep(0.5)


async def chat_access_problem(page):
    """Return a fixed diagnostic; never include page/chat text in the result."""
    return await page.evaluate('''() => {
        const visible = el => {
            if (!el) return false;
            const r=el.getBoundingClientRect(), s=getComputedStyle(el);
            return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden';
        };
        const app=visible(document.querySelector('#sub-app'));
        const dialogs=Array.from(document.querySelectorAll('[role="dialog"], .semi-modal-content, .pc-login-verification-modal')).filter(visible);
        const scope=app ? dialogs.map(el=>el.innerText||'').join(' ') : (document.body?.innerText||'');
        const verification=['请完成下方验证', '请完成下列验证', '按住左边按钮拖动', '拖动滑块', '安全验证', '请完成验证', '请通过验证', '身份验证', '环境异常', '风险提示'];
        if (verification.some(s=>scope.includes(s)) || Array.from(document.querySelectorAll('iframe[src*="captcha"], iframe[src*="verify"]')).some(visible))
            return {category:'verification_required', message:'页面要求安全验证，请由本人在登录工作区完成验证；自动发送已停止。'};
        const masks=Array.from(document.querySelectorAll('.login-mask, .login-guide-container, .login-img-code-wrapper')).some(visible);
        const login=['扫码登录', '验证码登录', '密码登录', '登录/注册', '请先登录', 'Log in to Douyin', 'Scan to Log In'];
        const landing=!app && scope.includes('我是创作者') && scope.includes('一站式创作与服务平台');
        if (masks || login.some(s=>scope.includes(s)) || landing)
            return {category:'login_required', message:'当前进入创作者中心介绍或登录页，未加载私信应用。请在现有账号上重新登录并保存，再核验好友列表。'};
        return null;
    }''')


async def ensure_chat_access(page, *, require_app=False):
    problem = await chat_access_problem(page)
    if problem:
        raise RuntimeError(problem['category'] + ': ' + problem['message'])
    if require_app and not await page.locator('#sub-app').is_visible():
        raise RuntimeError('chat_page_unavailable: 私信应用尚未加载，未执行发送。请检查登录、网络及当前页面入口。')


async def activate_friends_tab(page, *, fallback_selector=None, timeout_seconds=10):
    """Use visible tab labels; an old positional XPath alone is not identity."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        await ensure_chat_access(page)
        app = page.locator('#sub-app')
        candidates = []
        for label in FRIEND_LABELS:
            scope = page if label in FRIEND_LABELS[:2] else app
            candidates.extend((scope.get_by_role('tab', name=label, exact=True),
                               scope.get_by_role('button', name=label, exact=True),
                               scope.get_by_text(label, exact=True)))
        if fallback_selector:
            candidates.append(page.locator(fallback_selector))
        for locator in candidates:
            for index in range(min(await locator.count(), 5)):
                item = locator.nth(index)
                if not await item.is_visible():
                    continue
                if (await item.inner_text(timeout=1000)).strip() not in FRIEND_LABELS:
                    continue
                await item.click(timeout=3000)
                return
        if asyncio.get_running_loop().time() >= deadline:
            await ensure_chat_access(page, require_app=True)
            raise RuntimeError('friend_list_unavailable: 当前私信页面未找到朋友私信入口；未执行发送。')
        await asyncio.sleep(0.25)
