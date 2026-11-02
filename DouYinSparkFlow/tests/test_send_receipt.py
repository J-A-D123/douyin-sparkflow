import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from core.send_receipt import browser_receipt_is_strong, json_send_success
from core.send_state import history_entry_is_strong_confirmed_today
from core import tasks


class SendReceiptTests(unittest.TestCase):
    def test_decision_response_is_not_send_success(self):
        self.assertIsNone(json_send_success({'decision': 'fixture'}))
        self.assertIsNone(json_send_success({'code': 0, 'data': {'decision': 'fixture'}}))

    def test_missing_null_boolean_or_nested_failure_is_not_success(self):
        self.assertIsNone(json_send_success({'message': 'success'}))
        for data in ({'code': None}, {'code': False}, {'code': 0, 'data': {'error_code': 1}}):
            self.assertFalse(json_send_success(data))
        self.assertTrue(json_send_success({'status_code': 0, 'data': {'code': '0'}}))

    def test_http_200_and_old_ok_flag_are_insufficient(self):
        self.assertFalse(browser_receipt_is_strong({'ok': True, 'httpStatus': 200, 'call': 'message_send', 'jsonKeys': ['decision']}))
        self.assertFalse(browser_receipt_is_strong({'ok': True, 'httpStatus': 200, 'call': 'message_send', 'jsonOk': True}))

    def test_positive_business_receipt_requires_message_identity(self):
        receipt = {'ok': True, 'httpStatus': 200, 'call': 'message_send', 'jsonOk': True, 'serverMessageId': 'fixture'}
        self.assertTrue(browser_receipt_is_strong(receipt))
        self.assertFalse(browser_receipt_is_strong(dict(receipt, httpStatus=500)))
        self.assertFalse(browser_receipt_is_strong(dict(receipt, decisionPresent=True)))

    def test_previously_marked_strong_decision_receipt_is_downgraded(self):
        now = datetime.now(timezone.utc)
        entry = {'sentAt': now.isoformat(), 'status': 'confirmed', 'confirmationLevel': 'strong', 'confirmationSource': 'cdp_message_send_receipt', 'serverReceipt': {'ok': True, 'httpStatus': 200, 'call': 'message_send', 'jsonKeys': ['decision']}}
        self.assertFalse(history_entry_is_strong_confirmed_today(entry, now))
        self.assertFalse(history_entry_is_strong_confirmed_today(dict(entry, confirmationSource=''), now))
        user = {'targets': ['friend'], 'message_history': {'friend': entry}}
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual([], tasks._prepare_active_users_for_run({'dailySendWindow': {'enabled': False}}, [user]))
        self.assertEqual([], tasks._pending_unsent_targets(user, now)[0])

    def test_dom_only_persistence_cannot_be_promoted_to_success(self):
        with self.assertRaisesRegex(RuntimeError, 'confirmation is insufficient'):
            tasks._persist_browser_send_success({}, 'friend', '1', datetime.now(timezone.utc).isoformat())
