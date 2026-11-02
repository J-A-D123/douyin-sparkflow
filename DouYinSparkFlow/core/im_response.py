"""Decode observed IM response evidence only; this module sends no requests.

Wire fields verified against the public frontend schema documented in
docs/RECEIPT_FORMAT.md. Unknown or malformed data never establishes success.
"""

import base64

MAX_BODY_BYTES = 1024 * 1024


def cdp_body_bytes(body):
    raw = body.get('body') or ''
    if body.get('base64Encoded'):
        return base64.b64decode(raw, validate=True)
    return raw.encode('utf-8')


def _varint(data, position):
    value = 0
    for shift in range(0, 70, 7):
        if position >= len(data):
            raise ValueError('truncated varint')
        byte = data[position]
        position += 1
        if shift == 63 and byte > 1:
            raise ValueError('varint overflow')
        value |= (byte & 127) << shift
        if byte < 128:
            return value, position
    raise ValueError('invalid varint')


def wire_fields(data):
    if not isinstance(data, bytes) or len(data) > MAX_BODY_BYTES:
        raise ValueError('invalid response size')
    result = {}
    position = 0
    count = 0
    while position < len(data):
        count += 1
        if count > 10000:
            raise ValueError('too many fields')
        tag, position = _varint(data, position)
        number, kind = tag >> 3, tag & 7
        if not 0 < number < 2**29:
            raise ValueError('invalid field number')
        if kind == 0:
            value, position = _varint(data, position)
        elif kind in (1, 2, 5):
            if kind == 2:
                size, position = _varint(data, position)
            else:
                size = 8 if kind == 1 else 4
            end = position + size
            if end > len(data):
                raise ValueError('truncated field')
            value, position = data[position:end], end
        else:
            raise ValueError('unsupported wire type')
        result.setdefault(number, []).append((kind, value))
    return result


def field(fields, number, kind, default=None):
    values = fields.get(number, [])
    if not values:
        return default
    if len(values) != 1 or values[0][0] != kind:
        raise ValueError('ambiguous or invalid field')
    return values[0][1]


def _text(fields, number):
    raw = field(fields, number, 2, b'')
    return raw.decode('utf-8', errors='strict')[:300]


def decode_send_response(raw):
    """Accept only the known SEND_MESSAGE envelope and its nested send result."""
    outer = wire_fields(raw)
    if field(outer, 1, 0) != 100:
        raise ValueError('not a SEND_MESSAGE response')
    # The frontend schema defines omitted int32 status fields as zero.
    protocol_status = field(outer, 3, 0, 0)
    result = {'receiptFormat': 'im_protobuf', 'decodeVersion': 1,
              'protocolStatus': protocol_status, 'businessOk': False}
    if protocol_status:
        result['reason'] = _text(outer, 4) or 'IM response returned a nonzero status'
        return result
    body = wire_fields(field(outer, 6, 2, b''))
    send_raw = field(body, 100, 2)
    if send_raw is None:
        raise ValueError('missing send result')
    send = wire_fields(send_raw)
    status = field(send, 3, 0, 0)
    message_id = field(send, 1, 0, 0)
    check_code = field(send, 5, 0, 0)
    result.update(sendStatus=status, checkCode=check_code)
    if message_id:
        result['serverMessageId'] = str(message_id)
    result['businessOk'] = status == 0 and check_code == 0 and message_id > 0
    if not result['businessOk']:
        result['reason'] = _text(send, 6) or {
            1: 'UserNotInConversation', 2: 'CheckConversationNotPass',
            3: 'CheckMessageNotPass', 4: 'CheckMessageNotPassButSelfVisible',
            5: 'UserHasBeenBlock',
        }.get(status, 'Missing message ID or unknown send result')
    return result


def decode_conversation_response(raw):
    outer = wire_fields(raw)
    command = field(outer, 1, 0)
    if command not in (301, 2043, 2048) or field(outer, 3, 0, 0) != 0:
        return []
    self_id = field(outer, 13, 0, 0)
    body = wire_fields(field(outer, 6, 2, b''))
    container = field(body, command, 2)
    if container is None:
        return []
    contents = wire_fields(container)
    if command == 301:
        messages = contents.get(1, [])
    else:
        if command == 2048:
            recent = field(contents, 1, 2, b'')
            groups = wire_fields(recent).get(2, [])
        else:
            groups = contents.get(1, [])
        messages = []
        for group_kind, raw_group in groups:
            if group_kind != 2:
                raise ValueError('invalid conversation group')
            messages.extend(wire_fields(raw_group).get(2, []))
    if len(messages) > 1000:
        raise ValueError('too many messages')
    result = []
    for kind, raw_message in messages:
        if kind != 2:
            raise ValueError('invalid message wire type')
        message = wire_fields(raw_message)
        ext = {}
        for ext_kind, raw_ext in message.get(9, []):
            if ext_kind != 2:
                raise ValueError('invalid extension')
            item = wire_fields(raw_ext)
            key, value = _text(item, 1), _text(item, 2)
            if key in ext:
                raise ValueError('duplicate extension')
            ext[key] = value
        result.append({'serverMessageId': str(field(message, 3, 0, 0)),
                       'content': field(message, 8, 2, b'').decode('utf-8'),
                       'isSelf': bool(self_id) and self_id == field(message, 7, 0),
                       'callbackCode': ext.get('im_callback_status_code'),
                       'sendStatus': ext.get('s:send_response_status'),
                       'sendCheckCode': ext.get('s:send_response_check_code'),
                       'visibilityRestricted': bool(ext.get('s:visible')), 
                       'createdAt': field(message, 10, 0, 0)})
    return result
