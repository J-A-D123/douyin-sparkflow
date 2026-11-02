"""Print a shareable diagnostic report containing no account names or credentials."""
import argparse
import collections
import json
import platform
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig')), True
    except (OSError, ValueError):
        return default, False


def collect_report(root):
    config, config_ok = read_json(root / 'DouYinSparkFlow/config.json', {})
    accounts, accounts_ok = read_json(root / 'DouYinSparkFlow/usersData.json', [])
    if not isinstance(config, dict): config, config_ok = {}, False
    if not isinstance(accounts, list): accounts, accounts_ok = [], False
    categories = collections.Counter()
    for account in accounts:
        if not isinstance(account, dict): continue
        queue = account.get('failure_queue')
        entries = list(queue.values()) if isinstance(queue, dict) else []
        entries.append(account.get('account_failure') or {})
        for entry in entries:
            category = str(entry.get('category') or '') if isinstance(entry, dict) else ''
            if re.fullmatch(r'[a-z0-9_]{1,64}', category): categories[category] += 1
    rules = []
    cron = root / 'state/cron/root'
    if cron.is_file():
        for line in cron.read_text(encoding='utf-8-sig').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and len(line.split()) >= 6:
                rules.append(' '.join(line.split()[:5]))
    return {
        'schemaVersion': 1, 'platform': platform.system(), 'architecture': platform.machine(),
        'runtimeFilesValid': {'config': config_ok, 'accounts': accounts_ok},
        'accountCount': sum(isinstance(a, dict) for a in accounts),
        'enabledAccountCount': sum(bool(a.get('enabled', True)) for a in accounts if isinstance(a, dict)),
        'targetCount': sum(len(a['targets']) for a in accounts if isinstance(a, dict) and isinstance(a.get('targets'), list)),
        'failureCategories': dict(categories), 'cronRules': rules,
        'messageConfigured': bool(config.get('messageTemplate')), 'protocolSenderEnabled': bool(config.get('useProtocolSender')),
    }


def container_status(root):
    try:
        result = subprocess.run(['docker', 'compose', '-f', str(root / 'docker-compose.yml'), 'ps', '-a', '--format', 'json'], capture_output=True, text=True, timeout=10)
        if result.returncode: return {'available': False, 'services': []}
        raw = result.stdout.strip()
        rows = json.loads(raw) if raw.startswith('[') else [json.loads(line) for line in raw.splitlines() if line.strip()]
        return {'available': True, 'services': [{k: row.get(k) for k in ('Service', 'State', 'Health')} for row in rows]}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'available': False, 'services': []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = collect_report(ROOT)
    report['docker'] = container_status(ROOT)
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(text, encoding='utf-8')
        args.output.chmod(0o600)
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
