import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_full_analysis_e2e import build_compliant_evidence, build_compliant_report


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

    def lease_and_start(self):
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        started = self.cli(
            "job-started", "--run-root", self.run_root,
            "--work-unit-id", leased["work_unit_id"],
            "--attempt-id", leased["attempt_id"],
            "--lease-nonce", leased["lease_nonce"],
            "--agent-job-id", f"job-{leased['attempt_id']}",
        )
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        return leased

    def write_result(self, leased, *, attempt_id=None, lease_nonce=None, agent_job_id=None):
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        skill = next(s for s in registry["skills"] if s["skill_id"] == leased["skill_id"])
        attempt_id = attempt_id or leased["attempt_id"]
        attempt_dir = self.run_root / "evidence/attempts" / leased["skill_id"] / attempt_id
        attempt_dir.mkdir(parents=True, exist_ok=True)
        artifact = attempt_dir / "report.md"
        artifact.write_text(build_compliant_report(REGISTRY, leased["skill_id"]), encoding="utf-8")
        (facts, sources, calculations, judgments, role_runs,
         command_receipts, capability_records) = build_compliant_evidence(
            REGISTRY, leased["skill_id"])
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        bundle = {
            "schema_version": "result-schema/v1",
            "run_id": manifest["run"]["run_id"],
            "work_unit_id": leased["work_unit_id"],
            "attempt_id": attempt_id,
            "agent_job_id": agent_job_id or f"job-{leased['attempt_id']}",
            "lease_nonce": lease_nonce or leased["lease_nonce"],
            "skill_id": leased["skill_id"],
            "role_id": None,
            "status": "PASS",
            "artifact_records": [{
                "artifact_id": skill["artifact"]["artifact_id"],
                "path": str(artifact.relative_to(self.run_root)),
                "bytes": artifact.stat().st_size,
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                "formal": False,
                "accepted": False,
            }],
            "fact_updates": facts,
            "source_records": sources,
            "calculation_requests": calculations,
            "judgments": judgments,
            "role_runs": role_runs,
            "command_receipts": command_receipts,
            "capability_records": capability_records,
            "limitations": [],
            "pwl_candidates": [],
            "started_at": "2026-07-25T12:00:00+08:00",
            "completed_at": "2026-07-25T12:01:00+08:00",
            "error": None,
        }
        path = attempt_dir / "result.json"
        path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        return path

    def test_start_initializes_budget_and_counts_preflight_once(self):
        self.start()
        state = self.state()
        self.assertEqual(state["budget"]["hard_max"], 33)
        self.assertEqual(state["budget"]["stop_dispatch_at"], 30)
        self.assertEqual(state["budget"]["used"], 1)
        self.assertEqual(state["budget"]["preflight_count"], 1)
        self.assertEqual(len(state["work_units"]), 13)
        authorization = state["authorization"]
        self.assertEqual(
            authorization["profile"],
            "full-analysis-internal/v1",
        )
        self.assertIn("read_only_external_research",
                      authorization["granted"])
        self.assertIn("external_publish", authorization["denied"])

    def test_next_work_injects_bounded_unattended_authorization(self):
        self.start()

        leased = self.cli("next-work", "--run-root", self.run_root)

        self.assertEqual(leased.returncode, 0, leased.stdout + leased.stderr)
        payload = json.loads(leased.stdout)
        self.assertEqual(
            payload["authorization"]["profile"],
            "full-analysis-internal/v1",
        )
        methodology = payload["methodology_text"]
        self.assertIn("本次 run 的启动请求已满足", methodology)
        self.assertIn("只读外部研究", methodology)
        self.assertIn("run_root", methodology)
        self.assertIn("不得据此执行 push、PR、publish、send", methodology)

    def test_next_work_and_job_started_enforce_four_concurrent_leases(self):
        self.start()
        leases = [self.cli("next-work", "--run-root", self.run_root) for _ in range(4)]
        fifth = self.cli("next-work", "--run-root", self.run_root)
        for lease in leases:
            self.assertEqual(lease.returncode, 0)
            self.assertEqual(json.loads(lease.stdout)["status"], "LEASED")
        self.assertEqual(json.loads(fifth.stdout)["status"], "NO_WORK")
        a = json.loads(leases[0].stdout)
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
        state["budget"]["used"] = state["budget"]["hard_max"]
        path.write_text(json.dumps(state), encoding="utf-8")
        result = self.cli("next-work", "--run-root", self.run_root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(state["budget"]["hard_max"]), result.stdout + result.stderr)
        self.assertTrue((self.run_root / "PARTIAL_REPORT.md").is_file())
        self.assertTrue((self.run_root / "SUMMARY.md").is_file())

    def test_cleanup_requires_dry_run_and_resume_marks_old_attempt_abandoned(self):
        self.start()
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.cli("job-started", "--run-root", self.run_root,
                 "--work-unit-id", leased["work_unit_id"], "--attempt-id", leased["attempt_id"],
                 "--lease-nonce", leased["lease_nonce"], "--agent-job-id", "job-1")
        denied = self.cli("cleanup", "--run-root", self.run_root)
        self.assertNotEqual(denied.returncode, 0)
        preview = self.cli("cleanup", "--run-root", self.run_root, "--dry-run")
        self.assertEqual(preview.returncode, 0)
        resumed = self.cli("resume", "--run-root", self.run_root)
        self.assertEqual(resumed.returncode, 0)
        state = self.state()
        unit = next(x for x in state["work_units"] if x["work_unit_id"] == leased["work_unit_id"])
        self.assertIn(leased["attempt_id"], unit["abandoned_attempts"])

    def test_resume_recovers_valid_orphan_before_abandoning_live_lease(self):
        self.start()
        leased = self.lease_and_start()
        self.write_result(leased)

        resumed = self.cli("resume", "--run-root", self.run_root)

        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        payload = json.loads(resumed.stdout)
        self.assertIn(leased["work_unit_id"], payload["recovered"])
        unit = next(
            item for item in self.state()["work_units"]
            if item["work_unit_id"] == leased["work_unit_id"]
        )
        self.assertEqual(unit["status"], "DONE")
        self.assertNotIn(
            leased["attempt_id"], unit.get("abandoned_attempts", []))
        manifest = json.loads(
            (self.run_root / "evidence/00-analysis-manifest.json").read_text())
        skill = next(
            item for item in manifest["skills"]
            if item["skill_id"] == leased["skill_id"]
        )
        self.assertEqual(skill["status"], "PASS")

    def test_concurrent_job_started_updates_are_serialized_without_lost_budget(self):
        self.start()
        leases = [
            json.loads(
                self.cli("next-work", "--run-root", self.run_root).stdout)
            for _ in range(4)
        ]
        processes = []
        for index, leased in enumerate(leases):
            processes.append(subprocess.Popen(
                [
                    sys.executable, str(CLI), "job-started",
                    "--run-root", str(self.run_root),
                    "--work-unit-id", leased["work_unit_id"],
                    "--attempt-id", leased["attempt_id"],
                    "--lease-nonce", leased["lease_nonce"],
                    "--agent-job-id", f"job-concurrent-{index}",
                ],
                cwd=self.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ))
        completed = [process.communicate(timeout=20) for process in processes]

        for process, (stdout, stderr) in zip(processes, completed):
            self.assertEqual(process.returncode, 0, stdout + stderr)
        state = self.state()
        self.assertEqual(state["budget"]["used"], 5)
        self.assertEqual(
            sum(unit["status"] == "RUNNING" for unit in state["work_units"]),
            4,
        )
        self.assertTrue(
            (self.run_root / "evidence/locks/runtime-state.lock").is_file())

    def test_submit_result_rejects_bundle_not_bound_to_current_lease_before_gate(self):
        self.start()
        leased = self.lease_and_start()
        result_path = self.write_result(
            leased,
            attempt_id="attempt-stale",
            lease_nonce="foreign-nonce",
            agent_job_id="foreign-job",
        )

        submitted = self.cli(
            "submit-result", "--run-root", self.run_root,
            "--registry", REGISTRY, "--result", result_path,
        )

        self.assertNotEqual(submitted.returncode, 0)
        unit = next(x for x in self.state()["work_units"] if x["work_unit_id"] == leased["work_unit_id"])
        self.assertEqual(unit["status"], "RUNNING")
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        item = next(x for x in manifest["skills"] if x["skill_id"] == leased["skill_id"])
        self.assertEqual(item["status"], "PENDING")
        self.assertFalse((self.run_root / "01-数据与快筛/01-ashare-data.md").exists())

    def test_expired_orphan_result_is_validated_instead_of_marked_done_from_status_only(self):
        self.start()
        leased = self.lease_and_start()
        attempt_dir = self.run_root / "evidence/attempts" / leased["skill_id"] / leased["attempt_id"]
        (attempt_dir / "result.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        state_path = self.run_root / "evidence/runtime-state.json"
        state = self.state()
        unit = next(x for x in state["work_units"] if x["work_unit_id"] == leased["work_unit_id"])
        unit["lease"]["expires_at"] = "2000-01-01T00:00:00+08:00"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        advanced = self.cli("next-work", "--run-root", self.run_root)

        self.assertEqual(advanced.returncode, 0, advanced.stdout + advanced.stderr)
        unit = next(x for x in self.state()["work_units"] if x["work_unit_id"] == leased["work_unit_id"])
        self.assertEqual(unit["status"], "RETRY_WAIT")
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        item = next(x for x in manifest["skills"] if x["skill_id"] == leased["skill_id"])
        self.assertEqual(item["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
