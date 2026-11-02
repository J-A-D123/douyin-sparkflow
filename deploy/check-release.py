"""Check the source that Git would publish, without printing credential values."""
import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_NAMES = {'.env', '.DS_Store', 'usersData.json', 'webui_settings.json', 'webui_users.json', 'host_password.txt'}
SECRET_PATTERNS = (
    re.compile(rb'(?m)^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\r?$'),
    re.compile(rb'gh[pousr]_[A-Za-z0-9]{30,}'),
    re.compile(rb'github_pat_[A-Za-z0-9_]{50,}'),
)


def private_path(path):
    p = Path(path)
    return (p.name in PRIVATE_NAMES or p.suffix in {'.log', '.har'}
            or any(part in {'state', 'logs', 'browser-profiles', 'login-profile', '.migration-backups'} for part in p.parts)
            or p.name.startswith('storage-state') and p.suffix == '.json'
            or p.name.startswith('.env.') and p.name != '.env.example'
            or str(p).replace('\\', '/') in {'DouYinSparkFlow/config.json', 'proxy/config.yaml'})


def candidate_files(root):
    result = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=root, capture_output=True, check=True)
    return sorted(set(result.stdout.decode().strip('\0').split('\0')) - {''})


def local_secrets(root):
    found = set()
    def walk(value, key=''):
        if isinstance(value, dict):
            for k, item in value.items():
                if k == 'cookies' and isinstance(item, list):
                    for cookie in item:
                        if isinstance(cookie, dict) and len(str(cookie.get('value', ''))) >= 16:
                            found.add(str(cookie['value']).encode())
                elif re.search(r'password|secret|token|webhook', k, re.I) and isinstance(item, str) and len(item) >= 16:
                    found.add(item.encode())
                else:
                    walk(item, k)
        elif isinstance(value, list):
            for item in value:
                walk(item, key)
    for name in ('usersData.json', 'webui_settings.json', 'webui_users.json'):
        path = root / 'DouYinSparkFlow' / name
        if path.is_file():
            try:
                walk(json.loads(path.read_text(encoding='utf-8')))
            except (ValueError, OSError):
                raise RuntimeError('Cannot inspect local private data; fix it before publishing')
    env = root / '.env'
    if env.is_file():
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            key, sep, value = line.partition('=')
            if sep and re.search(r'PASSWORD|SECRET|TOKEN|SUB_URL', key) and len(value.strip(' \"\'')) >= 16:
                found.add(value.strip(' \"\'').encode())
    return found


def check_files(root, files, secrets=()):
    findings = []
    for relative in files:
        path = root / relative
        if private_path(relative):
            findings.append({'path': relative, 'reason': 'private runtime file'})
            continue
        if not path.exists():
            continue
        if not path.resolve().is_relative_to(root.resolve()):
            findings.append({'path': relative, 'reason': 'symlink escapes repository'})
            continue
        data = path.read_bytes()
        if any(pattern.search(data) for pattern in SECRET_PATTERNS):
            findings.append({'path': relative, 'reason': 'credential-shaped content'})
        if any(secret in data for secret in secrets):
            findings.append({'path': relative, 'reason': 'contains a local credential'})
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    files = candidate_files(ROOT)
    findings = check_files(ROOT, files, local_secrets(ROOT))
    report = {'checked_files': len(files), 'passed': not findings, 'findings': findings}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if findings else 0


if __name__ == '__main__':
    raise SystemExit(main())
