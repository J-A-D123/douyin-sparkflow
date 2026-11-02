from __future__ import annotations

from datetime import datetime
from core.send_receipt import browser_receipt_is_strong


def parse_sent_at(raw_value, local_tz):
    if not raw_value:
        return None
    raw = str(raw_value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=local_tz)
    return parsed.astimezone(local_tz)


def _receipt_is_strong(receipt):
    return browser_receipt_is_strong(receipt)


def history_entry_is_strong_confirmed_today(entry, now):
    entry = dict(entry or {})
    sent_at = parse_sent_at(entry.get("sentAt"), now.tzinfo)
    if not sent_at or sent_at.date() != now.date() or bool(entry.get("needsVerification")):
        return False
    receipt = entry.get('serverReceipt') or {}
    if entry.get('confirmationSource') == 'cdp_message_send_receipt' or any(key in receipt for key in ('jsonParsed', 'jsonKeys', 'jsonOk', 'decisionPresent')):
        return browser_receipt_is_strong(receipt)
    if entry.get('confirmationSource') == 'browser_visible_count_increased':
        return False
    if entry.get("status") == "confirmed" and entry.get("confirmationLevel") == "strong":
        return True
    return _receipt_is_strong(entry.get("serverReceipt"))


def target_is_strong_confirmed_today(account, target_name, now):
    history = dict(account.get("message_history") or {})
    return history_entry_is_strong_confirmed_today(history.get(target_name), now)


def target_dispatch_reserved_today(account, target_name, now):
    reservation = dict((account.get('dispatch_reservations') or {}).get(target_name) or {})
    attempted_at = parse_sent_at(reservation.get('attemptedAt'), now.tzinfo)
    return bool(attempted_at and attempted_at.date() == now.date())


def history_entry_is_today(entry, now):
    sent_at = parse_sent_at(dict(entry or {}).get("sentAt"), now.tzinfo)
    return bool(sent_at and sent_at.date() == now.date())
