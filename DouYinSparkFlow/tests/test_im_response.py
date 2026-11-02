import base64
import json
import unittest
from core.im_response import cdp_body_bytes, decode_send_response, decode_conversation_response, wire_fields
from core.send_receipt import parse_send_receipt, browser_receipt_is_strong
from core.ordinary_chat import check_history_message


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result) + bytes([value])


def integer(number, value):
    return varint(number << 3) + varint(value)


def blob(number, value):
    return varint((number << 3) | 2) + varint(len(value)) + value


def response(status=0, message_id=123456789, check=0):
    send = integer(1, message_id) + integer(3, status) + integer(5, check)
    return integer(1, 100) + integer(3, 0) + blob(6, blob(100, send))


def history(callback='0', restricted=False, sender=99, send_status=None, send_check=None):
    ext = blob(9, blob(1, b'im_callback_status_code') + blob(2, callback.encode()))
    if restricted:
        ext += blob(9, blob(1, b's:visible') + blob(2, b'99'))
    for key,value in ((b's:send_response_status',send_status),(b's:send_response_check_code',send_check)):
        if value is not None:ext+=blob(9,blob(1,key)+blob(2,value.encode()))
    message = integer(3, 123456789) + integer(7, sender) + blob(8, json.dumps({'text':'1'}).encode()) + ext
    return integer(1, 301) + integer(3, 0) + integer(13, 99) + blob(6, blob(301, blob(1, message)))


class ProtobufReceiptTests(unittest.TestCase):
    def test_binary_success_survives_cdp_base64_without_utf8_loss(self):
        raw=response()
        body={'body':base64.b64encode(raw).decode(),'base64Encoded':True}
        self.assertEqual(raw,cdp_body_bytes(body))
        receipt=parse_send_receipt(200,cdp_body_bytes(body))
        self.assertTrue(browser_receipt_is_strong(receipt))
        self.assertFalse(receipt['jsonParsed'])
        self.assertEqual('123456789',receipt['serverMessageId'])

    def test_self_visible_and_business_rejected_never_count_as_success(self):
        for status in range(1,6):
            receipt=parse_send_receipt(200,response(status=status))
            self.assertEqual(status,receipt['sendStatus'])
            self.assertFalse(browser_receipt_is_strong(receipt))
        self.assertFalse(parse_send_receipt(200,response(check=8101))['ok'])

    def test_unknown_json_and_non_send_binary_stay_unconfirmed(self):
        self.assertFalse(parse_send_receipt(200,b'{"decision":"fixture"}')['ok'])
        self.assertFalse(parse_send_receipt(200,integer(1,301)+blob(6,b''))['ok'])
        self.assertFalse(parse_send_receipt(500,response())['ok'])
        self.assertFalse(parse_send_receipt(200,response(message_id=0))['ok'])

    def test_schema_defaults_do_not_make_empty_bytes_a_success(self):
        self.assertFalse(parse_send_receipt(200,b'')['ok'])
        no_status=integer(1,100)+blob(6,blob(100,integer(1,123456789)))
        self.assertTrue(parse_send_receipt(200,no_status)['ok'])

    def test_malformed_and_duplicate_fields_are_rejected(self):
        for raw in (b'\x00',b'\x08\x80',b'\x32\xff',b'\xff'*12,response()+integer(1,100)):
            self.assertFalse(parse_send_receipt(200,raw)['ok'])
        with self.assertRaises(ValueError):wire_fields(b'x'*1048577)

    def test_server_history_must_match_sender_content_id_and_callback(self):
        records=decode_conversation_response(history())
        self.assertTrue(check_history_message(records,'123456789','1'))
        self.assertFalse(check_history_message(records,'other-id','1'))
        self.assertFalse(check_history_message(records,'123456789','2'))
        self.assertFalse(check_history_message(decode_conversation_response(history(sender=100)),'123456789','1'))

    def test_server_stored_self_visible_message_is_not_peer_delivery(self):
        with self.assertRaisesRegex(RuntimeError,'platform_self_visible'):
            check_history_message(decode_conversation_response(history(restricted=True)),'123456789','1')
        with self.assertRaisesRegex(RuntimeError,'platform_message_rejected'):
            check_history_message(decode_conversation_response(history(callback='8101')),'123456789','1')

    def test_auxiliary_callback_is_preserved_and_not_fabricated_as_zero(self):
        records=decode_conversation_response(history(callback='21100'))
        self.assertEqual('21100',records[0]['callbackCode'])
        self.assertTrue(check_history_message(records,'123456789','1'))
        receipt=parse_send_receipt(200,response())
        receipt.update(chatInterface='ordinary',serverHistoryVerified=True,callbackCode=records[0]['callbackCode'],peerVisibilityRestricted=False)
        self.assertTrue(browser_receipt_is_strong(receipt))
        self.assertEqual('21100',receipt['callbackCode'])
        self.assertFalse(browser_receipt_is_strong(dict(receipt,sendStatus=4)))
        self.assertFalse(browser_receipt_is_strong(dict(receipt,checkCode=1)))
        self.assertFalse(browser_receipt_is_strong(dict(receipt,peerVisibilityRestricted=True)))

    def test_explicit_history_sdk_send_status_and_check_code_still_stop(self):
        for kwargs in ({'send_status':'4'},{'send_check':'8101'}):
            with self.assertRaisesRegex(RuntimeError,'platform_message_rejected'):
                check_history_message(decode_conversation_response(history(callback='21100',**kwargs)),'123456789','1')

    def test_initial_and_incremental_history_use_the_same_message_checks(self):
        root=wire_fields(history())
        body=wire_fields(root[6][0][1])
        messages=wire_fields(body[301][0][1])
        raw_message=messages[1][0][1]
        group=blob(1,b'fixture')+blob(2,raw_message)
        initial=integer(1,2043)+integer(13,99)+blob(6,blob(2043,blob(1,group)))
        incremental=integer(1,2048)+integer(13,99)+blob(6,blob(2048,blob(1,blob(2,group))))
        for raw in (initial,incremental):
            self.assertTrue(check_history_message(decode_conversation_response(raw),'123456789','1'))

