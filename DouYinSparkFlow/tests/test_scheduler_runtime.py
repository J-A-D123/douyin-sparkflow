import importlib.util
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('scheduler_runtime', Path(__file__).resolve().parents[1] / 'scripts/cron_runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class SchedulerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cron = Path(self.tmp.name) / 'root'
        self.cron.write_text('0 12 * * * echo fixture-only\n')
        self.launch = Mock()

    def run_at(self, hour, minute=0, day=30):
        return runner.dispatch_due_jobs(self.cron, datetime(2026, 10, day, hour, minute), launch=self.launch)

    def test_waits_until_noon(self):
        self.assertEqual(0, self.run_at(11, 59))
        self.launch.assert_not_called()

    def test_noon_dispatch_occurs_once(self):
        self.assertEqual(1, self.run_at(12))
        self.assertEqual(0, self.run_at(12))
        self.assertEqual(1, self.launch.call_count)

    def test_wake_after_noon_runs_missed_job_once(self):
        self.assertEqual(0, self.run_at(11, 48))
        self.assertEqual(1, self.run_at(19, 40))
        self.assertEqual(0, self.run_at(19, 41))

    def test_restart_keeps_same_day_reservation(self):
        self.assertEqual(1, self.run_at(12))
        another = Mock()
        self.assertEqual(0, runner.dispatch_due_jobs(self.cron, datetime(2026, 10, 30, 20), launch=another))
        another.assert_not_called()

    def test_new_day_gets_one_new_dispatch(self):
        self.assertEqual(1, self.run_at(12, day=30))
        self.assertEqual(1, self.run_at(12, day=31))

    def test_interval_job_is_not_caught_up_outside_matching_time(self):
        self.cron.write_text('*/20 10-17 * * * echo fixture-only\n')
        self.assertEqual(0, self.run_at(19, 40))
        self.assertEqual(1, self.run_at(10, 20))
        self.assertEqual(0, self.run_at(10, 20))

    def test_corrupt_state_does_not_repeat_jobs(self):
        self.cron.with_name('.sparkflow-cron-state.json').write_text('{broken')
        with self.assertRaises(RuntimeError):
            self.run_at(19)
        self.launch.assert_not_called()

    def test_failed_process_start_releases_reservation(self):
        self.launch.side_effect = OSError('fixture failure')
        with self.assertRaises(OSError):
            self.run_at(12)
        self.launch.side_effect = None
        self.assertEqual(1, self.run_at(12, 1))


if __name__ == '__main__':
    unittest.main()
