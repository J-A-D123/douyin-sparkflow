import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import login_desktop_server as server
from utils.login_api_auth import ensure_login_api_token, read_login_api_token


class LoginApiAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'token'
        self.env = patch.dict(os.environ, {'SPARKFLOW_LOGIN_DESKTOP_API_TOKEN_FILE': str(self.path)}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.token = ensure_login_api_token()
        self.client = TestClient(server.app)

    def test_token_is_persistent_and_private(self):
        self.assertEqual(self.token, ensure_login_api_token())
        self.assertGreaterEqual(len(self.token), 32)
        if os.name != 'nt':
            self.assertEqual(0o600, self.path.stat().st_mode & 0o777)

    def test_health_requires_no_token(self):
        self.assertEqual(200, self.client.get('/health').status_code)

    def test_account_state_requires_token(self):
        self.assertEqual(401, self.client.get('/status').status_code)
        self.assertEqual(200, self.client.get('/status', headers={'X-SparkFlow-Token': self.token}).status_code)

    def test_wrong_token_is_rejected(self):
        self.assertEqual(401, self.client.get('/status', headers={'X-SparkFlow-Token': 'wrong'}).status_code)

    def test_debug_is_disabled_by_default(self):
        with patch.dict(os.environ, {'SPARKFLOW_ENABLE_LOGIN_DEBUG': '0'}):
            self.assertEqual(404, self.client.get('/debug/snapshot', headers={'X-SparkFlow-Token': self.token}).status_code)

    def test_invalid_token_file_fails_closed(self):
        self.path.write_text('short')
        self.assertEqual(503, self.client.get('/status').status_code)
        with self.assertRaises(RuntimeError):
            read_login_api_token()
