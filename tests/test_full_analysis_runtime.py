import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "full_analysis.py"
REGISTRY = REPO / "tools" / "full_analysis_contract.json"


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.run_root = self.root / "local/company/000651.SZ-格力电器/20260723-120000-ab12"

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(CLI), *map(str, args)], cwd=self.root,
            capture_output=True, text=True,
        )

    def start(self):
        result = self.cli(
            "start", "--registry", REGISTRY, "--repo-root", self.root,
            "--company", "格力电器", "--code", "000651.SZ", "--as-of", "2026-07-23",
            "--run-root", self.run_root,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def state(self):
        return json.loads((self.run_root / "evidence/runtime-state.json").read_text())

    def test_start_initializes_budget_and_counts_preflight_once(self):
        self.start()
        state = self.state()
        self.assertEqual(state["budget"]["hard_max"], 50)
        self.assertEqual(state["budget"]["stop_dispatch_at"], 45)
        self.assertEqual(state["budget"]["used"], 1)
        self.assertEqual(state["budget"]["preflight_count"], 1)
        self.assertEqual(len(state["work_units"]), 20)

    def test_next_work_and_job_started_enforce_two_concurrent_leases(self):
        self.start()
        first = self.cli("next-work", "--run-root", self.run_root)
        second = self.cli("next-work", "--run-root", self.run_root)
        third = self.cli("next-work", "--run-root", self.run_root)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(json.loads(first.stdout)["status"], "LEASED")
        self.assertEqual(json.loads(second.stdout)["status"], "LEASED")
        self.assertEqual(json.loads(third.stdout)["status"], "NO_WORK")
        a = json.loads(first.stdout)
        started = self.cli("job-started", "--run-root", self.run_root,
                           "--work-unit-id", a["work_unit_id"], "--attempt-id", a["attempt_id"],
                           "--lease-nonce", a["lease_nonce"], "--agent-job-id", "job-1")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertEqual(self.state()["budget"]["used"], 2)

    def test_rate_limit_failure_enters_global_cooldown_and_retry_backoff(self):
        self.start()
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.cli("job-started", "--run-root", self.run_root,
                 "--work-unit-id", leased["work_unit_id"], "--attempt-id", leased["attempt_id"],
                 "--lease-nonce", leased["lease_nonce"], "--agent-job-id", "job-1")
        failed = self.cli("record-failure", "--run-root", self.run_root,
                          "--work-unit-id", leased["work_unit_id"], "--attempt-id", leased["attempt_id"],
                          "--reason", "rate_limit")
        self.assertEqual(failed.returncode, 0, failed.stdout + failed.stderr)
        state = self.state()
        self.assertEqual(state["concurrency"]["max"], 1)
        self.assertTrue(state["concurrency"]["cooldown_until"])
        unit = next(x for x in state["work_units"] if x["work_unit_id"] == leased["work_unit_id"])
        self.assertEqual(unit["status"], "RETRY_WAIT")
        self.assertEqual(unit["attempts"], 1)

    def test_hard_budget_blocks_new_job_at_fifty(self):
        self.start()
        path = self.run_root / "evidence/runtime-state.json"
        state = self.state()
        state["budget"]["used"] = 50
        path.write_text(json.dumps(state), encoding="utf-8")
        result = self.cli("next-work", "--run-root", self.run_root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("50", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
