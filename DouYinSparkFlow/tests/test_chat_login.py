import asyncio
import unittest
from unittest.mock import patch

from playwright.async_api import async_playwright

from core.login import wait_for_logged_in_identity
from core.friends import collect_friend_names
from login_desktop_server import LoginDesktopManager


HOME = "https://creator.douyin.com/"
CHAT = "https://www.douyin.com/chat"
CARD = '<div class="header-new"><div>本人测试账号</div><div></div><div class="unique_id-new">抖音号：12345678901</div><div>签名</div></div>'


class ChatLoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.manager = LoginDesktopManager()
        self.manager.context = self.context
        self.manager.page = self.page

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def show(self, body, url=HOME):
        await self.page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body))
        await self.page.goto(url)

    async def session(self):
        await self.context.add_cookies([{"name": "sessionid", "value": "fixture-only", "domain": ".douyin.com", "path": "/", "secure": True}])

    async def test_account_card_survives_extra_layout_children(self):
        await self.show(CARD)
        self.assertEqual(("12345678901", "本人测试账号"), await wait_for_logged_in_identity(self.page, timeout_ms=0))

    async def test_unrelated_numbers_cannot_become_account_identity(self):
        await self.show('<div>粉丝 12345678901</div>')
        with self.assertRaises(RuntimeError):
            await wait_for_logged_in_identity(self.page, timeout_ms=0)

    async def test_hidden_stale_account_is_ignored(self):
        stale = CARD.replace("12345678901", "98765432100")
        await self.show(CARD + '<div style="display:none">' + stale + '</div>')
        self.assertEqual("12345678901", (await wait_for_logged_in_identity(self.page, timeout_ms=0))[0])

    async def test_ambiguous_visible_account_cards_are_rejected(self):
        await self.show(CARD + CARD.replace("12345678901", "98765432100"))
        with self.assertRaises(RuntimeError):
            await wait_for_logged_in_identity(self.page, timeout_ms=0)

    async def test_matching_markup_on_other_site_is_rejected(self):
        await self.show(CARD, "https://example.com/")
        with self.assertRaises(RuntimeError):
            await wait_for_logged_in_identity(self.page, timeout_ms=0)

    async def test_late_login_page_stops_sender_identity_wait(self):
        await self.show('<div>加载中</div><script>setTimeout(()=>{document.body.innerHTML="<div>我是创作者</div><div>抖音创作者的一站式创作与服务平台</div>"},100)</script>')
        with self.assertRaisesRegex(RuntimeError, 'login_required'):
            await asyncio.wait_for(wait_for_logged_in_identity(self.page, timeout_ms=60000, fail_on_access_problem=True), timeout=2)

    async def test_late_verification_is_not_generic_identity_timeout(self):
        await self.show('<div>加载中</div><script>setTimeout(()=>{document.body.innerHTML="<div role=dialog>请完成下方验证</div>"},100)</script>')
        with self.assertRaisesRegex(RuntimeError, 'verification_required'):
            await asyncio.wait_for(wait_for_logged_in_identity(self.page, timeout_ms=60000, fail_on_access_problem=True), timeout=2)

    async def test_interactive_login_may_wait_for_owner_to_finish(self):
        await self.show('<div>扫码登录</div>')
        async def complete():
            await asyncio.sleep(0.1)
            await self.page.set_content(CARD)
        completed=asyncio.create_task(complete())
        result=await wait_for_logged_in_identity(self.page, timeout_ms=2000)
        await completed
        self.assertEqual('12345678901', result[0])

    async def test_chat_shell_without_session_is_not_logged_in(self):
        await self.show('<input placeholder="搜索">', CHAT)
        self.assertFalse(await self.manager._chat_session_ready(self.page))

    async def test_session_without_chat_shell_is_not_logged_in(self):
        await self.session()
        await self.show('<div>正在加载</div>', CHAT)
        self.assertFalse(await self.manager._chat_session_ready(self.page))

    async def test_login_dialog_blocks_even_when_old_shell_and_cookie_exist(self):
        await self.session()
        await self.show('<input placeholder="搜索"><div>Log in to Douyin</div>', CHAT)
        self.assertFalse(await self.manager._chat_session_ready(self.page))

    async def test_logged_in_chat_is_recognized_without_inventing_identity(self):
        await self.session()
        await self.show('<input placeholder="搜索">', CHAT)
        result = await self.manager.status()
        self.assertTrue(result["logged_in"])
        self.assertEqual("", result["unique_id"])

    async def test_export_verifies_real_self_card_after_chat_login(self):
        await self.session()
        async def respond(route):
            body = '<input placeholder="搜索">' if route.request.url == CHAT else CARD
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
        await self.page.route("**/*", respond)
        await self.page.goto(CHAT)
        with patch("login_desktop_server.STOP_AFTER_EXPORT_SECONDS", 0):
            result = await self.manager.export()
        self.assertEqual("12345678901", result["unique_id"])
        self.assertEqual("本人测试账号", result["username"])
        self.assertTrue(result["cookies"])
        self.assertEqual(HOME, self.page.url)

    async def test_single_page_friend_list_does_not_wait_for_missing_scroll_container(self):
        await self.show('<div id="sub-app"><button role="tab">朋友私信</button><ul><li role="listitem"><span class="item-header-name-new">指定好友</span></li></ul></div>')
        names = await asyncio.wait_for(collect_friend_names(self.page), timeout=3)
        self.assertEqual(["指定好友"], names)


if __name__ == "__main__":
    unittest.main()
