import unittest
from unittest.mock import patch,AsyncMock,Mock
from fastapi.testclient import TestClient
from core.ordinary_dispatch import select_saved_accounts
from core import ordinary_chat
from utils import config,process_lock
import login_desktop_server as server


class OrdinaryDispatchTests(unittest.TestCase):
    def setUp(self):
        self.account={'account_ref':'fixture-ref','unique_id':'fixture-id','targets':['friend','other'],'enabled':True,'cookies':[]}
        self.selection={'accounts':[{'account_ref':'fixture-ref','unique_id':'fixture-id','targets':['friend']}]}

    def test_only_saved_enabled_account_and_target_subset_are_accepted(self):
        result=select_saved_accounts(self.selection,[self.account])
        self.assertEqual(['friend'],result[0]['targets'])
        for wrong in ({'account_ref':'unknown'}, {'unique_id':'another-id'}, {'targets':['stranger']}, {'targets':['friend','friend']}, {'message':'arbitrary'}):
            request=dict(self.selection['accounts'][0],**wrong)
            with self.assertRaises(ValueError):select_saved_accounts({'accounts':[request]},[self.account])
        with self.assertRaises(ValueError):select_saved_accounts(self.selection,[dict(self.account,enabled=False)])

    def test_desktop_run_requires_authentication_and_exclusive_send_lease(self):
        client=TestClient(server.app)
        token='fixture-internal-token-long-enough-for-tests'
        with patch.object(server,'read_login_api_token',return_value=token),patch.object(process_lock,'is_locked',return_value=False),patch.object(ordinary_chat,'run_accounts',new=AsyncMock()) as run:
            self.assertEqual(401,client.post('/ordinary/run',json=self.selection).status_code)
            self.assertEqual(409,client.post('/ordinary/run',headers={'X-SparkFlow-Token':token},json=self.selection).status_code)
            run.assert_not_called()

    def test_wrong_or_verification_lease_cannot_send(self):
        client=TestClient(server.app)
        token='fixture-internal-token-long-enough-for-tests'
        for body in ('pid=123\nlease=fixture-lease\n','pid=123\nlease=fixture-lease\npurpose=manual_verification\n'):
            path=Mock();path.read_text.return_value=body
            with patch.object(server,'read_login_api_token',return_value=token),patch.object(server,'Path',return_value=path),patch.object(process_lock,'is_locked',return_value=True),patch.object(ordinary_chat,'run_accounts',new=AsyncMock()) as run:
                headers={'X-SparkFlow-Token':token,'X-SparkFlow-Task-Lease':'wrong' if 'purpose' not in body else 'fixture-lease'}
                self.assertEqual(409,client.post('/ordinary/run',headers=headers,json=self.selection).status_code)
                run.assert_not_called()

    def test_valid_lease_dispatches_only_selected_saved_target_in_visible_mode(self):
        client=TestClient(server.app)
        token='fixture-internal-token-long-enough-for-tests'
        path=Mock();path.read_text.return_value='pid=123\nlease=fixture-lease\n'
        with patch.object(server,'read_login_api_token',return_value=token),patch.object(server,'Path',return_value=path),patch.object(process_lock,'is_locked',return_value=True),patch.object(config,'get_userData',return_value=[self.account]),patch.object(config,'get_config',return_value={}),patch.object(ordinary_chat,'run_accounts',new=AsyncMock()) as run:
            headers={'X-SparkFlow-Token':token,'X-SparkFlow-Task-Lease':'fixture-lease'}
            self.assertEqual(200,client.post('/ordinary/run',headers=headers,json=self.selection).status_code)
            self.assertTrue(run.await_args.args[0]['_visibleSender'])
            self.assertEqual(['friend'],run.await_args.args[1][0]['targets'])

    def test_prepare_check_requires_auth_and_lease_and_never_calls_sender(self):
        client=TestClient(server.app)
        token='fixture-internal-token-long-enough-for-tests'
        path=Mock();path.read_text.return_value='pid=123\nlease=fixture-lease\n'
        with patch.object(server,'read_login_api_token',return_value=token),patch.object(server,'Path',return_value=path),patch.object(process_lock,'is_locked',return_value=True),patch.object(config,'get_userData',return_value=[self.account]),patch.object(config,'get_config',return_value={}),patch.object(ordinary_chat,'check_prepared',new=AsyncMock(return_value={'prepared':True,'sendActions':0})) as check,patch.object(ordinary_chat,'run_accounts',new=AsyncMock()) as run:
            self.assertEqual(401,client.post('/ordinary/prepare-check',json=self.selection).status_code)
            self.assertEqual(409,client.post('/ordinary/prepare-check',headers={'X-SparkFlow-Token':token},json=self.selection).status_code)
            headers={'X-SparkFlow-Token':token,'X-SparkFlow-Task-Lease':'fixture-lease'}
            response=client.post('/ordinary/prepare-check',headers=headers,json=self.selection)
            self.assertEqual(200,response.status_code)
            self.assertEqual(0,response.json()['sendActions'])
            self.assertTrue(check.await_args.args[1]['_visibleSender'])
            run.assert_not_called()
