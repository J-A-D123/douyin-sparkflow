import asyncio
import unittest
from datetime import datetime
from unittest.mock import patch

from playwright.async_api import async_playwright
from core import tasks
from core.chat_page import activate_friends_tab, ensure_chat_access, wait_for_chat_ready
from webui import ops
from webui import app as app_module
from utils.config import normalize_unique_id
from utils import process_lock
import tempfile
from pathlib import Path


class DeliveryStateTests(unittest.TestCase):
    def test_list_error_has_actionable_label_without_destroying_diagnostic(self):
        reason = 'Page.wait_for_selector: Timeout 30000ms exceeded.'
        item = ops._finalize_target_status({'category': 'friend_list_timeout', 'reason': reason}, datetime.now(tasks._schedule_timezone()))
        self.assertEqual('好友列表加载超时', item['categoryLabel'])
        self.assertIn('重新登录', item['displayReason'])
        self.assertEqual(reason, item['reason'])

    def test_dom_recovery_cannot_override_missing_or_rejected_server_observation(self):
        self.assertFalse(tasks._observation_allows_confirmation(None))
        self.assertFalse(tasks._observation_allows_confirmation({'enabled': False}))
        complete = {'enabled': True, 'send_request_seen': True, 'send_response_seen': True, 'send_receipt': {'ok': True, 'httpStatus': 200, 'call': 'message_send', 'jsonOk': True, 'serverMessageId': 'fixture-id'}}
        self.assertTrue(tasks._observation_allows_confirmation(complete))
        for field in ('send_request_seen', 'send_response_seen'):
            self.assertFalse(tasks._observation_allows_confirmation(dict(complete, **{field: False})))
        self.assertFalse(tasks._observation_allows_confirmation(dict(complete, send_receipt={'ok': False})))

    def test_unobserved_request_is_uncertain_instead_of_known_not_sent(self):
        self.assertEqual('send_unconfirmed', tasks.classify_browser_failure('send_flow', RuntimeError('server send request was not observed')))

    @unittest.skipUnless(process_lock.SUPPORTED, 'POSIX container lock test')
    def test_kernel_lock_overrides_pid_from_other_container_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'logs/task.run.lock'
            path.parent.mkdir()
            with path.open('w+') as handle:
                handle.write('pid=99999999\n')
                handle.flush()
                self.assertTrue(process_lock.try_lock(handle))
                with patch.object(ops, 'repo_root', return_value=root), patch.object(ops, '_pid_is_alive', return_value=False):
                    self.assertTrue(ops.task_run_lock_status()['running'])
                process_lock.unlock(handle)
            self.assertFalse(process_lock.is_locked(path))
    def test_alphanumeric_handles_do_not_collapse_to_same_digits(self):
        self.assertEqual('alice123', normalize_unique_id('抖音号：alice123'))
        self.assertNotEqual(normalize_unique_id('alice123'), normalize_unique_id('bob123'))

    def test_same_nickname_does_not_match_a_different_account_id(self):
        self.assertIsNone(tasks._find_matching_account([{'unique_id': 'alice123', 'username': 'same'}], {'unique_id': 'bob123', 'username': 'same'}))

    def test_relogin_rejects_another_account_without_modifying_targets(self):
        original = {'unique_id': 'alice123', 'username': 'same', 'account_ref': 'fixture-ref', 'targets': ['friend']}
        with patch.object(app_module, 'get_userData', return_value=[original]):
            with self.assertRaisesRegex(RuntimeError, '扫码账号与原账号不一致'):
                app_module.save_exported_login_result({'unique_id': 'bob123', 'username': 'same', 'cookies': [{'fixture': True}]}, relogin_account_ref='fixture-ref')
        self.assertEqual('alice123', original['unique_id'])
        self.assertEqual(['friend'], original['targets'])

    def test_fixed_daily_job_skips_confirmed_and_reserved_targets(self):
        now = datetime.now(tasks._schedule_timezone())
        account = {'targets': ['sent', 'uncertain', 'new'],
                   'message_history': {'sent': {'sentAt': now.isoformat(), 'status': 'confirmed', 'confirmationLevel': 'strong'}},
                   'dispatch_reservations': {'uncertain': {'attemptedAt': now.isoformat()}}}
        with patch.dict('os.environ', {}, clear=True):
            result = tasks._prepare_active_users_for_run({'dailySendWindow': {'enabled': False}}, [account])
        self.assertEqual(['new'], result[0]['targets'])

    def test_reserved_send_is_shown_as_needing_verification(self):
        now = datetime.now(tasks._schedule_timezone())
        account = {'targets': ['friend'], 'dispatch_reservations': {'friend': {'attemptedAt': now.isoformat()}}}
        status = ops._build_target_status(account, 'friend', now, {'enabled': False})
        self.assertEqual('unconfirmed', status['status'])

    def test_unsent_batch_retry_skips_uncertain_dispatch(self):
        now = datetime.now(tasks._schedule_timezone())
        account = {'targets': ['uncertain', 'new'], 'dispatch_reservations': {'uncertain': {'attemptedAt': now.isoformat()}}}
        retry, skipped = tasks._pending_unsent_targets(account, now)
        self.assertEqual(['new'], retry)
        self.assertEqual(['uncertain(needs_verification)'], skipped)

    def test_stopped_scheduler_does_not_advertise_future_delivery(self):
        value = ops.schedule_with_runtime({'label': '12:00', 'nextTriggerAt': 'tomorrow'}, [{'Names': 'douyin-scheduler', 'State': 'exited'}])
        self.assertFalse(value['schedulerRunning'])
        self.assertEqual('', value['nextTriggerAt'])
        self.assertEqual('已暂停', value['runtimeDisplay'])


class DeliveryBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True, args=['--no-sandbox'])
        self.page = await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def show(self, html):
        await self.page.set_content('<meta charset="utf-8">' + html)

    async def test_sender_preserves_login_and_verification_failure_categories(self):
        from unittest.mock import AsyncMock
        await self.show('<div id="sub-app"></div>')
        for category in ('login_required', 'verification_required'):
            with patch.object(tasks, 'wait_for_logged_in_identity', new=AsyncMock(side_effect=RuntimeError(category + ': fixture'))):
                with self.assertRaisesRegex(RuntimeError, '^' + category):
                    await tasks.verify_sender_identity(self.page, {'unique_id': 'fixture'})

    async def test_logs_open_at_latest_record_and_offer_jump(self):
        from fastapi.testclient import TestClient
        text='2026-11-01 15:50:00 old record\n' + 'detail\n'*160 + '2026-11-02 14:14:33 latest record\n'
        with patch.object(app_module,'current_user',return_value='admin'), patch.object(app_module,'read_log_tail',return_value=text):
            response=TestClient(app_module.app).get('/ops/logs')
        self.assertEqual(200,response.status_code)
        await self.page.set_content(response.text)
        self.assertIn('2026-11-02 14:14:33',await self.page.locator('#logs-record-time').inner_text())
        self.assertGreater(await self.page.locator('#logs-code').evaluate('(e)=>e.scrollTop'),0)
        await self.page.locator('#logs-code').evaluate('(e)=>e.scrollTop=0')
        await self.page.locator('#logs-latest-btn').click()
        self.assertGreater(await self.page.locator('#logs-code').evaluate('(e)=>e.scrollTop'),0)

    async def test_delayed_micro_app_waits_for_actual_controls(self):
        await self.show('<div id="root">加载中</div><script>setTimeout(()=>{document.body.innerHTML=\'<div id="sub-app"><button role="tab">朋友私信</button></div>\'},200)</script>')
        await wait_for_chat_ready(self.page, timeout_seconds=2)
        self.assertTrue(await self.page.get_by_role('tab', name='朋友私信').is_visible())

    async def test_loading_shell_alone_is_not_chat_readiness(self):
        await self.show('<div id="sub-app">加载中，请稍候</div>')
        with self.assertRaisesRegex(RuntimeError, 'chat_page_unavailable'):
            await wait_for_chat_ready(self.page, timeout_seconds=0)

    async def test_late_verification_stops_readiness_wait(self):
        await self.show('<div id="root">加载中</div><script>setTimeout(()=>{document.body.innerHTML=\'<div role="dialog">请完成下方验证</div>\'},100)</script>')
        with self.assertRaisesRegex(RuntimeError, 'verification_required'):
            await wait_for_chat_ready(self.page, timeout_seconds=2)

    async def test_creator_landing_without_login_words_stops_before_old_xpath(self):
        await self.show('<div id="root">抖音创作者的一站式创作与服务平台<div>我是创作者</div><button id="old" onclick="window.clicked=true">继续</button></div>')
        with self.assertRaisesRegex(RuntimeError, 'login_required'):
            await asyncio.wait_for(tasks._open_friends_tab(self.page, 'fixture', '#old', ['#sub-app li']), timeout=2)
        self.assertFalse(await self.page.evaluate('Boolean(window.clicked)'))

    async def test_existing_other_inbox_rows_do_not_skip_friend_tab(self):
        await self.show('<div id="sub-app"><button role="tab" onclick="window.clicked=true">朋友私信</button><li class="semi-list-item"><span class="item-header-name-x">other inbox</span></li></div>')
        await tasks._open_friends_tab(self.page, 'fixture', '#missing', ['#sub-app li'])
        self.assertTrue(await self.page.evaluate('Boolean(window.clicked)'))

    async def test_wrong_sender_identity_stops_before_chat(self):
        from unittest.mock import AsyncMock
        await self.show('<div id="sub-app"></div>')
        with patch.object(tasks, 'wait_for_logged_in_identity', new=AsyncMock(return_value=('other-account', 'fixture'))):
            with self.assertRaisesRegex(RuntimeError, 'account_identity_mismatch'):
                await tasks.verify_sender_identity(self.page, {'unique_id': 'expected-account'})

    async def test_unloaded_application_has_explicit_error_without_xpath_wait(self):
        await self.show('<div id="root"></div>')
        with self.assertRaisesRegex(RuntimeError, 'chat_page_unavailable'):
            await activate_friends_tab(self.page, fallback_selector='#old-missing', timeout_seconds=0)

    async def test_old_xpath_pointing_at_unrelated_control_is_not_clicked(self):
        await self.show('<div id="sub-app"><button id="old" onclick="window.clicked=true">删除账号</button></div>')
        with self.assertRaisesRegex(RuntimeError, 'friend_list_unavailable'):
            await activate_friends_tab(self.page, fallback_selector='#old', timeout_seconds=0)
        self.assertFalse(await self.page.evaluate('Boolean(window.clicked)'))

    async def test_delayed_semantic_friend_tab_survives_wrapper_changes(self):
        await self.show('<div id="sub-app"></div><script>setTimeout(()=>{document.querySelector("#sub-app").innerHTML=\'<section><button role="tab" onclick="window.clicked=true">朋友私信</button></section>\'},100)</script>')
        await activate_friends_tab(self.page, fallback_selector='#old-missing', timeout_seconds=2)
        self.assertTrue(await self.page.evaluate('Boolean(window.clicked)'))

    async def test_verification_dialog_stops_before_automatic_dismissal(self):
        await self.show('<div id="sub-app"><button role="tab">朋友私信</button></div><div role="dialog">请完成下方验证<button onclick="window.dismissed=true">确认</button></div>')
        with self.assertRaisesRegex(RuntimeError, 'verification_required'):
            await tasks._open_friends_tab(self.page, 'fixture', '#unused', ['#sub-app li'])
        self.assertFalse(await self.page.evaluate('Boolean(window.dismissed)'))

    async def test_chat_message_discussing_login_is_not_a_login_prompt(self):
        await self.show('<div id="sub-app"><p>朋友说：请用手机扫码登录</p><button role="tab">朋友私信</button></div>')
        await ensure_chat_access(self.page)
        await tasks.ensure_not_login_required(self.page, 'fixture', 'test')

    async def test_old_identical_message_moving_is_not_new_delivery(self):
        await self.show('<div class="box-item-old is-me-old"><pre>1</pre></div><div contenteditable="true"></div>')
        editor = self.page.locator('[contenteditable]')
        before = await tasks.snapshot_last_own_message(self.page)
        await self.page.locator('.box-item-old').evaluate('(e) => e.style.marginTop="100px"')
        ok, _ = await tasks.confirm_message_sent(self.page, editor, '1', before_snapshot=before, before_count=1, timeout_seconds=0)
        self.assertFalse(ok)

    async def test_new_identical_message_requires_count_increase(self):
        await self.show('<div class="box-item-old is-me-old"><pre>1</pre></div><div class="box-item-new is-me-new"><pre>1</pre></div><div contenteditable="true"></div>')
        ok, _ = await tasks.confirm_message_sent(self.page, self.page.locator('[contenteditable]'), '1', before_count=1, timeout_seconds=0)
        self.assertTrue(ok)

    async def test_historical_failure_does_not_reject_new_clean_message(self):
        await self.show('<div class="box-item-old is-me-old"><pre>old</pre><span class="retry">重新发送</span></div><div class="box-item-new is-me-new"><pre>1</pre></div><div contenteditable="true"></div>')
        ok, _ = await tasks.confirm_message_sent(self.page, self.page.locator('[contenteditable]'), '1', before_count=0, timeout_seconds=0)
        self.assertTrue(ok)

    async def test_missing_baseline_never_infers_success(self):
        await self.show('<div class="box-item-old is-me-old"><pre>1</pre></div><div contenteditable="true"></div>')
        ok, _ = await tasks.confirm_message_sent(self.page, self.page.locator('[contenteditable]'), '1', timeout_seconds=0)
        self.assertFalse(ok)

    async def test_existing_draft_is_preserved(self):
        await self.show('<div contenteditable="true">private draft</div>')
        editor = self.page.locator('[contenteditable]')
        with self.assertRaisesRegex(RuntimeError, 'draft preserved'):
            await tasks.prepare_chat_message(self.page, editor, '1')
        self.assertEqual('private draft', await editor.inner_text())

    async def test_editor_is_bound_to_visible_conversation_header(self):
        await self.show('<section><header><span>friend</span></header><div contenteditable="true"></div></section>')
        await tasks.ensure_chat_recipient(self.page.locator('[contenteditable]'), 'friend', timeout_seconds=0)

    async def test_message_content_cannot_replace_recipient_header(self):
        await self.show('<section><header>other person</header><div class="box-item-one">friend</div><div contenteditable="true"></div></section>')
        with self.assertRaisesRegex(RuntimeError, 'recipient_mismatch'):
            await tasks.ensure_chat_recipient(self.page.locator('[contenteditable]'), 'friend', timeout_seconds=0)

    async def test_hidden_editor_is_not_selected(self):
        await self.show('<div contenteditable="true" style="display:none"></div><div class="chat-input-new"><div id="visible" contenteditable="true"></div></div>')
        editor, _ = await tasks.locate_chat_input(self.page)
        self.assertEqual('visible', await editor.get_attribute('id'))

    async def test_explicit_send_control_and_local_render(self):
        await self.show('<div contenteditable="true"></div><button onclick="const e=document.querySelector(\'[contenteditable]\'); const d=document.createElement(\'div\'); d.className=\'box-item-new is-me-new\'; d.innerHTML=\'<pre>1</pre>\'; document.body.append(d); e.textContent=\'\'">发送</button>')
        editor, _ = await tasks.locate_chat_input(self.page)
        await tasks.prepare_chat_message(self.page, editor, '1')
        await tasks.trigger_chat_send(self.page, editor)
        ok, _ = await tasks.confirm_message_sent(self.page, editor, '1', before_count=0, timeout_seconds=0)
        self.assertTrue(ok)

    async def test_disabled_send_button_does_not_fall_back_to_enter(self):
        await self.show('<div contenteditable="true">1</div><button disabled>发送</button>')
        with self.assertRaisesRegex(RuntimeError, 'disabled'):
            await tasks.trigger_chat_send(self.page, self.page.locator('[contenteditable]'))

    async def test_hidden_cached_friend_row_is_skipped(self):
        html = '<div id="sub-app"><button role="tab">朋友私信</button><li class="semi-list-item" style="display:none"><span class="item-header-name-x">friend</span></li><li class="semi-list-item"><span class="item-header-name-x">friend</span></li></div>'
        await self.show(html)
        scan = {'maxScanSeconds': 5, 'idleScanSeconds': 3, 'scrollStepPx': 400, 'scrollDelaySeconds': 0.01}
        with patch.object(tasks, '_persist_friend_index'):
            generator = tasks.scroll_and_select_user(self.page, {}, 'fixture', ['friend'], scan)
            self.assertEqual('friend', await asyncio.wait_for(generator.__anext__(), timeout=5))
            await generator.aclose()


if __name__ == '__main__':
    unittest.main()
