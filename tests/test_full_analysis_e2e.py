import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts/full_analysis.py"
GATE = REPO / "tools/full_analysis_gate.py"
REGISTRY = REPO / "tools/full_analysis_contract.json"


class FullAnalysisE2ETests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.run_root = self.root / "local/company/000651.SZ-格力电器/20260723-120000-e2e"

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)], cwd=self.root, capture_output=True, text=True)

    def test_single_company_canary_closes_all_twenty_units(self):
        started = self.cli("start", "--registry", REGISTRY, "--repo-root", self.root,
                           "--company", "格力电器", "--code", "000651.SZ", "--as-of", "2026-07-23",
                           "--run-root", self.run_root)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        run_id = manifest["run"]["run_id"]
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        by_id = {item["skill_id"]: item for item in registry["skills"]}
        for _ in range(20):
            leased = self.cli("next-work", "--run-root", self.run_root)
            self.assertEqual(leased.returncode, 0, leased.stdout + leased.stderr)
            lease = json.loads(leased.stdout)
            self.assertEqual(lease["status"], "LEASED")
            started_job = self.cli("job-started", "--run-root", self.run_root,
                                   "--work-unit-id", lease["work_unit_id"], "--attempt-id", lease["attempt_id"],
                                   "--lease-nonce", lease["lease_nonce"], "--agent-job-id", f"job-{lease['attempt_id']}")
            self.assertEqual(started_job.returncode, 0, started_job.stdout + started_job.stderr)
            skill_id = lease["skill_id"]
            attempt_dir = self.run_root / "evidence/attempts" / skill_id / lease["attempt_id"]
            attempt_dir.mkdir(parents=True, exist_ok=True)
            artifact = attempt_dir / "report.md"
            artifact.write_text(f"# {skill_id}\n\nsynthetic canary artifact\n", encoding="utf-8")
            bundle = {
                "schema_version": "result-schema/v1", "run_id": run_id,
                "work_unit_id": lease["work_unit_id"], "attempt_id": lease["attempt_id"],
                "agent_job_id": f"job-{lease['attempt_id']}", "lease_nonce": lease["lease_nonce"],
                "skill_id": skill_id, "role_id": None, "status": "PASS",
                "artifact_records": [{"artifact_id": by_id[skill_id]["artifact"]["artifact_id"],
                                      "path": str(artifact.relative_to(self.run_root)), "bytes": artifact.stat().st_size,
                                      "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(), "formal": False, "accepted": False}],
                "fact_updates": [], "source_records": [], "calculation_requests": [], "judgments": [],
                "limitations": [], "pwl_candidates": [], "started_at": "2026-07-23T12:00:00+08:00",
                "completed_at": "2026-07-23T12:01:00+08:00", "error": None,
            }
            result_path = attempt_dir / "result.json"
            result_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
            submitted = self.cli("submit-result", "--run-root", self.run_root, "--registry", REGISTRY, "--result", result_path)
            self.assertEqual(submitted.returncode, 0, submitted.stdout + submitted.stderr)
        audit = self.cli("audit", "--run-root", self.run_root)
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        finalized = subprocess.run([sys.executable, str(GATE), "finalize", "--run-root", str(self.run_root), "--registry", str(REGISTRY)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(finalized.returncode, 0, finalized.stdout + finalized.stderr)
        final_manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        self.assertEqual(final_manifest["run"]["status"], "APPROVED")


if __name__ == "__main__":
    unittest.main()
