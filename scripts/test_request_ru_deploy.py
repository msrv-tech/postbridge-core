"""Contract tests for the cross-repository RU deployment trigger."""

import unittest
from request_ru_deploy import WORKFLOW, trigger_and_wait

SHA = "a" * 40


class DispatchTests(unittest.TestCase):
    def test_dispatch_is_immediate_and_waits_for_the_exact_returned_run(self):
        calls, sleeps = [], []
        replies = iter([{"workflow_run_id": 42}, {"status": "in_progress"},
                        {"status": "completed", "conclusion": "success"}])
        def fetch(path, method="GET", body=None):
            calls.append((path, method, body))
            return next(replies)
        self.assertEqual(trigger_and_wait(SHA, fetch, sleeps.append), 42)
        self.assertEqual(calls[0], (WORKFLOW + "/dispatches", "POST", {"ref": "main", "inputs": {"core_sha": SHA}}))
        self.assertEqual(sleeps, [10])
        self.assertTrue(all(call[0].endswith("/actions/runs/42") for call in calls[1:]))

    def test_failed_private_deployment_fails_the_core_deploy_job(self):
        replies = iter([{"workflow_run_id": 42}, {"status": "completed", "conclusion": "failure"}])
        with self.assertRaises(RuntimeError):
            trigger_and_wait(SHA, lambda *args: next(replies))

    def test_missing_run_id_and_invalid_sha_are_rejected(self):
        with self.assertRaises(RuntimeError):
            trigger_and_wait(SHA, lambda *args: {})
        with self.assertRaises(ValueError):
            trigger_and_wait("main", lambda *args: self.fail("Must validate SHA before dispatch"))


if __name__ == "__main__":
    unittest.main()
