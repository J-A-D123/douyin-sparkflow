"""Explicitly install/uninstall the optional AC-only SparkFlow sleep helper."""
import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = 'com.sparkflow.keepawake'


def build_plist(root, docker):
    return {
        'Label': LABEL,
        'ProgramArguments': ['/bin/sh', str(root / 'DouYinSparkFlow/scripts/keep_awake_macos.sh'), docker],
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 30,
        'ProcessType': 'Background',
        'StandardOutPath': str(root / 'state/logs/keep-awake.log'),
        'StandardErrorPath': str(root / 'state/logs/keep-awake-error.log'),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'uninstall', 'status'], default='status', nargs='?')
    action = parser.parse_args().action
    if sys.platform != 'darwin':
        parser.error('This optional helper is for macOS only')
    plist_path = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    domain = 'gui/' + str(os.getuid())
    target = domain + '/' + LABEL
    if action == 'status':
        result = subprocess.run(['launchctl', 'print', target], capture_output=True, text=True)
        print('installed and loaded' if result.returncode == 0 else 'not loaded')
        return 0
    subprocess.run(['launchctl', 'bootout', target], capture_output=True)
    if action == 'uninstall':
        plist_path.unlink(missing_ok=True)
        print('Uninstalled; no power preferences were changed')
        return 0
    docker = shutil.which('docker')
    if not docker:
        raise SystemExit('Docker CLI was not found')
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / 'state/logs').mkdir(parents=True, exist_ok=True)
    with plist_path.open('wb') as output:
        plistlib.dump(build_plist(ROOT, docker), output)
    plist_path.chmod(0o600)
    result = subprocess.run(['launchctl', 'bootstrap', domain, str(plist_path)], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit('LaunchAgent could not be started: ' + result.stderr.strip())
    print('Installed: AC power + running scheduler; display sleep remains enabled')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
