import importlib.util
import json
import tempfile
import unittest
import os
import re
import shlex
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'deploy' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


release = module('release_check', 'check-release.py')
doctor = module('doctor', 'doctor.py')
mac = module('mac_keepawake', 'install-macos-keepawake.py')
bundle = module('release_bundle', 'build-release.py')


class PublicationToolsTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('git'), 'source bundles use Git file selection')
    def test_bundle_excludes_ignored_credentials_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / '.gitignore').write_text('.env\noutput/\n')
            (root / '.env').write_text('TOKEN=fixture-private-credential-value\n')
            (root / 'source.txt').write_text('safe source\n')
            first = root / 'output/first.zip'
            second = root / 'output/second.zip'
            self.assertEqual(bundle.build_bundle(root, first), bundle.build_bundle(root, second))
            with zipfile.ZipFile(first) as archive:
                self.assertEqual({'SparkFlow/.gitignore', 'SparkFlow/source.txt', 'SparkFlow/SOURCE_MANIFEST.json'}, set(archive.namelist()))
            (root / 'source.txt').write_text('fixture-private-credential-value')
            with self.assertRaisesRegex(RuntimeError, 'local credential'):
                bundle.build_bundle(root, second)

    def test_malformed_diagnostic_fields_do_not_crash_or_leak_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / 'DouYinSparkFlow'
            app.mkdir()
            (app / 'usersData.json').write_text(json.dumps([{'failure_queue': ['private data'], 'targets': 123}]))
            report = doctor.collect_report(root)
            self.assertEqual(0, report['targetCount'])
            self.assertNotIn('private data', json.dumps(report))
    def test_runtime_files_are_rejected_but_examples_are_allowed(self):
        for name in ('.env', 'state/cron/root', 'DouYinSparkFlow/usersData.json', 'debug.har', 'logs/run.txt'):
            self.assertTrue(release.private_path(name), name)
        for name in ('.env.example', 'DouYinSparkFlow/usersData.example.json', 'DouYinSparkFlow/config.example.json'):
            self.assertFalse(release.private_path(name), name)

    def test_local_credential_in_source_is_reported_without_its_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = b'fixture-private-value-not-for-publication'
            (root / 'source.py').write_bytes(secret)
            findings = release.check_files(root, ['source.py'], [secret])
            self.assertEqual(1, len(findings))
            self.assertNotIn(secret.decode(), json.dumps(findings))

    def test_private_key_content_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'source.txt').write_text('-' * 5 + 'BEGIN PRIVATE KEY' + '-' * 5 + '\n')
            self.assertEqual('credential-shaped content', release.check_files(root, ['source.txt'])[0]['reason'])

    def test_diagnostic_report_has_no_names_messages_cookies_or_error_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / 'DouYinSparkFlow'
            app.mkdir()
            (app / 'config.json').write_text(json.dumps({'messageTemplate': 'private message'}))
            (app / 'usersData.json').write_text(json.dumps([{'username': 'private name', 'targets': ['private friend'], 'cookies': [{'value': 'private cookie'}], 'failure_queue': {'private friend': {'category': 'send_unconfirmed', 'reason': 'private error'}}}]))
            report = json.dumps(doctor.collect_report(root))
            for text in ('private message', 'private name', 'private friend', 'private cookie', 'private error'):
                self.assertNotIn(text, report)
            self.assertIn('send_unconfirmed', report)

    def test_keepawake_helper_uses_no_display_wake_flag(self):
        body = (ROOT / 'DouYinSparkFlow/scripts/keep_awake_macos.sh').read_text()
        self.assertIn('AC Power', body)
        self.assertIn('douyin-scheduler', body)
        self.assertIn('caffeinate -i', body)
        self.assertNotIn('caffeinate -d', body)
        plist = mac.build_plist(ROOT, '/fixture/docker')
        self.assertEqual('/fixture/docker', plist['ProgramArguments'][-1])

    def test_ci_uses_no_account_secrets_or_live_douyin_probe(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        self.assertIn("USER_DATA: '[]'", workflow)
        self.assertNotIn('secrets.', workflow)
        self.assertNotIn('creator.douyin.com', workflow)
        self.assertNotIn('pull_request_target', workflow)

    @unittest.skipUnless(os.name != 'nt' and shutil.which('bash'), 'server installer requires Bash')
    def test_server_installer_uses_requested_time_and_preserves_existing_cron(self):
        source = (ROOT / 'deploy/install-server.sh').read_text()
        function = re.search(r'(?ms)^write_default_cron\(\) \{\n.*?^\}', source).group(0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'state/cron').mkdir(parents=True)
            command = 'APP_ROOT=' + shlex.quote(tmp) + '\nDEFAULT_SCHEDULE=12:34\nrun_root() { "$@"; }\n' + function + '\nwrite_default_cron\n'
            subprocess.run(['bash', '-c', command], check=True, capture_output=True)
            path = root / 'state/cron/root'
            self.assertTrue(path.read_text().startswith('34 12 * * * '))
            path.write_text('existing user schedule\n')
            subprocess.run(['bash', '-c', command], check=True, capture_output=True)
            self.assertEqual('existing user schedule\n', path.read_text())
