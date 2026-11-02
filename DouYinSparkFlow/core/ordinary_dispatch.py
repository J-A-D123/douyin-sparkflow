"""Authenticated dispatch to the visible desktop; no account cookies leave disk."""
import asyncio
import json
import os
import urllib.request
import urllib.error
from pathlib import Path
from utils.login_api_auth import read_login_api_token


def select_saved_accounts(payload, saved_accounts):
    if not isinstance(payload, dict) or set(payload) != {'accounts'}:
        raise ValueError('Invalid request fields')
    selected = []
    seen = set()
    requests = payload.get('accounts')
    if not isinstance(requests, list) or not 0 < len(requests) <= 100:
        raise ValueError('Invalid account selection')
    for request in requests:
        if not isinstance(request, dict):raise ValueError('Invalid account')
        if set(request) != {'account_ref', 'unique_id', 'targets'}:raise ValueError('Invalid account fields')
        ref = request.get('account_ref')
        matches = [a for a in saved_accounts if a.get('account_ref') == ref and ref]
        if len(matches) != 1 or ref in seen:raise ValueError('Unknown or duplicate account')
        seen.add(ref)
        account = matches[0]
        targets = request.get('targets')
        if (not account.get('enabled', True) or account.get('unique_id') != request.get('unique_id')
                or not isinstance(targets, list) or not targets or any(not isinstance(t, str) for t in targets)
                or len(set(targets)) != len(targets) or any(t not in (account.get('targets') or []) for t in targets)):
            raise ValueError('Selection is outside the saved account configuration')
        selected.append(dict(account, targets=list(targets)))
    return selected


def _call(path, payload, timeout):
    base = os.getenv('SPARKFLOW_LOGIN_DESKTOP_API_URL', 'http://127.0.0.1:18090').rstrip('/')
    request = urllib.request.Request(base + path, data=json.dumps(payload).encode(), method='POST',
        headers={'Content-Type':'application/json', 'X-SparkFlow-Token':read_login_api_token()})
    if path in ('/ordinary/run','/ordinary/prepare-check'):
        lines = Path('logs/task.run.lock').read_text().splitlines()
        leases = [line.split('=',1)[1] for line in lines if line.startswith('lease=')]
        if len(leases) != 1 or 'purpose=manual_verification' in lines:
            raise RuntimeError('No exclusive send task lease; refusing dispatch')
        request.add_header('X-SparkFlow-Task-Lease', leases[0])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get('detail', '')
        except (ValueError, AttributeError):
            detail = ''
        if isinstance(detail, str) and detail.startswith(('verification_required:', 'login_required:', 'account_identity_unverified:', 'account_identity_mismatch:', 'editor_not_ready:', 'recipient_mismatch:', 'Chat input did not accept', 'Existing draft preserved')):
            raise RuntimeError(detail) from exc
        raise RuntimeError('普通聊天工作区正忙或无法接收任务；未重发，请检查登录桌面。') from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError('普通聊天工作区不可用，请检查登录桌面服务；不要重复发信。') from exc


async def run_visible_accounts(users):
    payload = {'accounts':[{'account_ref':u.get('account_ref'),'unique_id':u.get('unique_id'),'targets':u['targets']} for u in users]}
    timeout = max(600, len(users)*180 + sum(len(u['targets']) for u in users)*180)
    return await asyncio.to_thread(_call, '/ordinary/run', payload, timeout)


async def fetch_visible_contacts(account):
    payload = {'account_ref':account.get('account_ref'),'unique_id':account.get('unique_id')}
    result = await asyncio.to_thread(_call, '/ordinary/contacts', payload, 180)
    return result['contacts']
