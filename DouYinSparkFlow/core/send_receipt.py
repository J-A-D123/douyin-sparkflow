"""Conservative interpretation of observed browser send responses."""
import hashlib
import json
from core.im_response import decode_send_response


def json_send_success(data):
    if not isinstance(data, dict) or 'decision' in data:
        return None
    checks = []
    for obj in (data, data.get('data')):
        if not isinstance(obj, dict):
            continue
        if 'decision' in obj:
            return None
        for key in ('status_code', 'err_no', 'errno', 'error_code', 'code'):
            if key in obj:
                value = obj[key]
                checks.append(type(value) is int and value == 0 or type(value) is str and value == '0')
    return all(checks) if checks else None


def browser_receipt_is_strong(receipt):
    if not isinstance(receipt, dict) or receipt.get('decisionPresent') or 'decision' in (receipt.get('jsonKeys') or []):
        return False
    if receipt.get('chatInterface') == 'ordinary' and not (
            receipt.get('serverHistoryVerified') is True and receipt.get('callbackCode') != '8101'
            and receipt.get('peerVisibilityRestricted') is False
            and receipt.get('historySendStatus') in (None,'0')
            and receipt.get('historyCheckCode') in (None,'0')):
        return False
    status = receipt.get('httpStatus')
    business_ok = receipt.get('jsonOk') is True or (
        receipt.get('receiptFormat') == 'im_protobuf' and receipt.get('decodeVersion') == 1
        and receipt.get('businessOk') is True and receipt.get('protocolStatus') == 0
        and receipt.get('sendStatus') == 0 and receipt.get('checkCode') == 0)
    message_id = receipt.get('serverMessageId') or receipt.get('messageId')
    return (type(status) in (int, float) and 200 <= status < 300
            and receipt.get('call') == 'message_send'
            and receipt.get('ok') is True and business_ok
            and bool(message_id) and str(message_id) != '0')


def parse_send_receipt(http_status, raw):
    receipt = {'call': 'message_send', 'httpStatus': http_status, 'ok': False,
               'bodyLen': len(raw), 'bodySha256': hashlib.sha256(raw).hexdigest()}
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        data = None
    receipt['jsonParsed'] = isinstance(data, dict)
    if isinstance(data, dict):
        receipt['jsonKeys'] = sorted(data)[:20]
        receipt['jsonOk'] = json_send_success(data)
        for obj in (data, data.get('data')):
            if not isinstance(obj, dict):
                continue
            if 'decision' in obj:
                receipt['decisionPresent'] = True
            for source, dest in (('server_message_id', 'serverMessageId'), ('message_id', 'messageId'), ('msg_id', 'messageId')):
                if obj.get(source):receipt[dest] = str(obj[source])[:120]
    elif data is None:
        try:
            receipt.update(decode_send_response(raw))
        except (ValueError, UnicodeDecodeError):
            receipt['reason'] = 'Unknown or malformed response; verify manually'
    receipt['ok'] = True
    receipt['ok'] = browser_receipt_is_strong(receipt)
    if not receipt['ok']:
        receipt.setdefault('reason', 'Response does not confirm a successful send; verify manually')
    return receipt
