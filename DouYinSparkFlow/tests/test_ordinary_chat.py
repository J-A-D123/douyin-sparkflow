import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch,AsyncMock
from playwright.async_api import async_playwright,Error as PlaywrightError
from core import tasks
from core import ordinary_chat as chat
from core.send_receipt import browser_receipt_is_strong
from tests.test_im_response import response


class OrdinaryChatBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw=await async_playwright().start()
        self.browser=await self.pw.chromium.launch(headless=True,args=['--no-sandbox'])
        self.page=await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close();await self.pw.stop()

    async def test_standalone_platform_puzzle_requires_owner(self):
        await self.page.set_content('<div>请完成下列验证后继续</div>')
        with self.assertRaisesRegex(RuntimeError,'verification_required'):
            await chat.check_access(self.page)

    async def test_visible_challenge_frame_is_not_an_identity_timeout(self):
        await self.page.route('https://verify.example/captcha',lambda route:route.fulfill(body='fixture'))
        await self.page.set_content('<iframe src="https://verify.example/captcha" style="width:500px;height:400px"></iframe>')
        with self.assertRaisesRegex(RuntimeError,'verification_required'):
            await chat.check_access(self.page)

    async def test_duplicate_names_are_rejected_without_clicking(self):
        row='<div data-e2e="conversation-item" onclick="window.clicked=true"><div class="conversationConversationItemtitle">friend</div></div>'
        await self.page.set_content(row*2)
        with self.assertRaisesRegex(RuntimeError,'recipient_mismatch'):
            await chat.select_target(self.page,'friend')
        self.assertFalse(await self.page.evaluate('Boolean(window.clicked)'))

    async def test_sender_self_profile_requires_visible_matching_label(self):
        await self.page.route('https://www.douyin.com/user/self',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body='<span>抖音号：fixture123</span>'))
        await self.page.goto(chat.SELF_URL)
        await chat.verify_self(self.page,'fixture123',timeout_seconds=0)
        with self.assertRaisesRegex(RuntimeError,'account_identity_mismatch'):
            await chat.verify_self(self.page,'other123',timeout_seconds=0)

    async def test_profile_navigation_race_is_bounded_and_rechecked(self):
        await self.page.route('https://www.douyin.com/user/self',lambda route:route.fulfill(body='fixture'))
        await self.page.goto(chat.SELF_URL)
        evaluate=AsyncMock(side_effect=[PlaywrightError('Execution context was destroyed'),None,['fixture123']])
        with patch.object(self.page,'evaluate',evaluate):
            await chat.verify_self(self.page,'fixture123',timeout_seconds=2)

    async def test_search_shell_does_not_imply_conversations_loaded(self):
        await self.page.set_content('<input placeholder="搜索"><script>setTimeout(()=>{const e=document.createElement("div");e.dataset.e2e="conversation-item";document.body.append(e)},100)</script>')
        await chat.wait_ready(self.page,timeout_seconds=2)
        self.assertEqual(1,await self.page.locator('[data-e2e="conversation-item"]').count())

    async def test_header_and_selected_row_must_both_match(self):
        await self.page.set_content('<div data-e2e="conversation-item" class="conversationConversationItemcurConversation"><div class="conversationConversationItemtitle">friend</div></div><div class="RightPanelHeadertitle">friend</div>')
        await chat.verify_recipient(self.page,'friend')

    async def test_editor_waits_for_async_commit_without_retyping(self):
        await self.page.set_content('''<div contenteditable="true" oninput="window.inputs=(window.inputs||0)+1;const value=this.innerText;this.innerText='';setTimeout(()=>this.innerText=value,150)"></div>''')
        editor=self.page.locator('[contenteditable]')
        await chat.prepare_message(self.page,editor,'1')
        self.assertEqual('1',await editor.inner_text())
        self.assertEqual(1,await self.page.evaluate('window.inputs'))

    async def test_different_draft_is_preserved(self):
        await self.page.set_content('<div contenteditable="true">private existing draft</div>')
        editor=self.page.locator('[contenteditable]')
        with self.assertRaisesRegex(RuntimeError,'Existing draft preserved'):
            await chat.prepare_message(self.page,editor,'1')
        self.assertEqual('private existing draft',await editor.inner_text())

    async def test_ordinary_pipeline_checks_binary_receipt_and_server_readback(self):
        html='''<input placeholder="搜索"><div data-e2e="conversation-item" class="conversationConversationItemcurConversation"><div class="conversationConversationItemtitle">friend</div></div><div class="RightPanelHeadertitle">friend</div><div class="messageMsgInputinputColumn"><div contenteditable="true" oninput="window.model=this.innerText;document.querySelector('.e2e-send-msg-btn').classList.add('messageMsgInputpublishRedBtn')"></div><svg width="32" height="32" class="e2e-send-msg-btn" onclick="fetch('https://imapi.douyin.com/v1/message/send',{method:'POST',body:window.model});document.querySelector('[contenteditable]').innerText=''">发送</svg></div>'''
        await self.page.route(chat.CHAT_URL,lambda route:route.fulfill(content_type='text/html; charset=utf-8',body=html))
        requests=[]
        async def network(route):
            requests.append(route.request.post_data)
            await route.fulfill(content_type='application/x-protobuf',headers={'Access-Control-Allow-Origin':'*'},body=response())
        await self.page.route('https://imapi.douyin.com/v1/message/send',network)
        browser_open=False
        @asynccontextmanager
        async def account(*args):
            nonlocal browser_open
            browser_open=True
            try:yield self.page.context,self.page,'direct'
            finally:browser_open=False
        user={'unique_id':'fixture','targets':['friend'],'cookies':[]}
        for history_ok in (True,False):
            requests.clear()
            async def verify_history(*args,**kwargs):
                self.assertFalse(browser_open,'Sender must close before verifier starts')
                self.assertGreater(kwargs['minimum_created_at_ms'],0)
                return history_ok
            with patch.object(chat,'account_page',account),patch.object(chat,'readback',new=AsyncMock(side_effect=verify_history)),patch.object(tasks,'refresh_stored_cookies_from_profile',new=AsyncMock()),patch.object(tasks,'build_message',return_value='1'),patch.object(tasks,'_persist_dispatch_reservation') as reserved,patch.object(tasks,'_persist_browser_send_success') as success,patch.object(tasks,'_persist_browser_send_failure') as failure,patch.object(tasks,'_sleep_with_log',new=AsyncMock()):
                await chat.run_accounts({},[user])
                self.assertEqual(['1'],requests)
                reserved.assert_called_once()
                self.assertEqual(1 if history_ok else 0,success.call_count)
                self.assertEqual(0 if history_ok else 1,failure.call_count)
                if history_ok:
                    self.assertTrue(success.call_args.kwargs['server_receipt']['serverHistoryVerified'])

    async def test_an_old_matching_server_id_cannot_confirm_a_new_attempt(self):
        item={'serverMessageId':'fixture-id','isSelf':True,'content':'{"text":"1"}','callbackCode':'0','visibilityRestricted':False,'createdAt':1000}
        self.assertFalse(chat.check_history_message([item],'fixture-id','1',minimum_created_at_ms=2000))
        self.assertTrue(chat.check_history_message([dict(item,createdAt=2001)],'fixture-id','1',minimum_created_at_ms=2000))

    async def test_ordinary_send_uses_its_visible_svg_control_once(self):
        await self.page.set_content('''<div class="messageMsgInputinputColumn"><div contenteditable="true" onkeydown="window.keydown=true">1</div><svg width="32" height="32" class="e2e-send-msg-btn" onclick="window.sends=(window.sends||0)+1"></svg></div>''')
        await chat.trigger_send(self.page,self.page.locator('[contenteditable]'))
        self.assertEqual(1,await self.page.evaluate('window.sends'))
        self.assertFalse(await self.page.evaluate('Boolean(window.keydown)'))

    async def test_ordinary_send_rejects_control_in_another_editor(self):
        await self.page.set_content('''<div contenteditable="true">1</div><div class="messageMsgInputinputColumn"><svg width="32" height="32" class="e2e-send-msg-btn" onclick="window.sent=true"></svg></div>''')
        with self.assertRaisesRegex(RuntimeError,'does not belong'):
            await chat.trigger_send(self.page,self.page.locator('[contenteditable]'))
        self.assertFalse(await self.page.evaluate('Boolean(window.sent)'))

    async def test_dom_text_without_editor_model_acknowledgement_is_rejected(self):
        await self.page.set_content('''<div class="messageMsgInputinputColumn"><div contenteditable="true">1</div><svg class="e2e-send-msg-btn"></svg></div>''')
        with self.assertRaisesRegex(RuntimeError,'editor_not_ready'):
            await chat.verify_prepared(self.page,self.page.locator('[contenteditable]'),'1',timeout_seconds=0)
        self.assertEqual('editor_not_ready',tasks.classify_browser_failure('send_flow',RuntimeError('editor_not_ready: fixture')))
        await self.page.locator('.e2e-send-msg-btn').evaluate("e=>e.classList.add('messageMsgInputpublishRedBtn')")
        await chat.verify_prepared(self.page,self.page.locator('[contenteditable]'),'1',timeout_seconds=0)

    async def test_prepare_check_never_reserves_or_clicks_send(self):
        @asynccontextmanager
        async def account(*args):yield self.page.context,self.page,'direct'
        html='''<input placeholder="搜索"><div data-e2e="conversation-item" class="conversationConversationItemcurConversation"><div class="conversationConversationItemtitle">friend</div></div><div class="RightPanelHeadertitle">friend</div><div class="messageMsgInputinputColumn"><div contenteditable="true">1</div><svg class="e2e-send-msg-btn messageMsgInputpublishRedBtn" onclick="window.sent=true"></svg></div>'''
        await self.page.route(chat.CHAT_URL,lambda r:r.fulfill(content_type='text/html; charset=utf-8',body=html))
        with patch.object(chat,'account_page',account),patch.object(tasks,'build_message',return_value='1'),patch.object(tasks,'_persist_dispatch_reservation') as reservation:
            result=await chat.check_prepared({'targets':['friend']},{})
        self.assertEqual(0,result['sendActions'])
        reservation.assert_not_called()
        self.assertFalse(await self.page.evaluate('Boolean(window.sent)'))

    async def test_contact_name_hydration_does_not_trigger_a_premature_search(self):
        await self.page.set_content('<input placeholder="搜索" oninput="window.searched=true"><div data-e2e="conversation-item" onclick="this.classList.add(\'conversationConversationItemcurConversation\')"><div id="title" class="conversationConversationItemtitle">loading</div></div><div class="RightPanelHeadertitle">friend</div><script>setTimeout(()=>document.querySelector("#title").textContent="friend",100)</script>')
        await chat.select_target(self.page,'friend')
        self.assertFalse(await self.page.evaluate('Boolean(window.searched)'))

    async def test_async_restore_is_reselected_only_before_typing(self):
        await self.page.set_content('''<div data-e2e="conversation-item" onclick="window.clicks=(window.clicks||0)+1;this.classList.add('conversationConversationItemcurConversation');document.querySelector('.RightPanelHeadertitle').textContent='friend';if(window.clicks===1)setTimeout(()=>{this.classList.remove('conversationConversationItemcurConversation');document.querySelector('.RightPanelHeadertitle').textContent='previous'},10)"><div class="conversationConversationItemtitle">friend</div></div><div class="RightPanelHeadertitle">previous</div>''')
        original=chat.verify_recipient
        async def bounded(page,target):await original(page,target,timeout_seconds=1)
        with patch.object(chat,'verify_recipient',bounded):
            await chat.select_target(self.page,'friend')
        self.assertEqual(2,await self.page.evaluate('window.clicks'))

    async def test_real_cdp_observer_decodes_binary_ui_response(self):
        html='<button onclick="fetch(\'https://imapi.douyin.com/v1/message/send\',{method:\'POST\',body:\'fixture\'})">发送</button>'
        await self.page.route(chat.CHAT_URL,lambda route:route.fulfill(content_type='text/html; charset=utf-8',body=html))
        def fulfill_for(code):
            async def fulfill(route):
                await route.fulfill(content_type='application/x-protobuf',headers={'Access-Control-Allow-Origin':'*'},body=response(status=code))
            return fulfill
        for status in (0,4):
            await self.page.route('https://imapi.douyin.com/v1/message/send',fulfill_for(status))
            await self.page.goto(chat.CHAT_URL)
            observer=await tasks.start_im_send_observer(self.page,'fixture','fixture')
            await self.page.get_by_role('button',name='发送').click()
            result=await observer(extra_wait_seconds=3)
            self.assertTrue(result['send_request_seen'])
            self.assertTrue(result['send_response_seen'])
            self.assertEqual(status==0,browser_receipt_is_strong(result['send_receipt']))
            self.assertEqual(status,result['send_receipt']['sendStatus'])
            await self.page.unroute('https://imapi.douyin.com/v1/message/send')
