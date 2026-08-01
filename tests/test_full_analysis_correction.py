"""Task 2：correction bundle —— 确定性证据错误的定向修正（TDD 失败测试先行）。"""

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


def _submit_one(root, run_root, lease):
    """提交一个最小 PASS 单元（financial-data，含 1 calc），返回 bundle。"""
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    skill_id = lease["skill_id"]
    by_id = {s["skill_id"]: s for s in registry["skills"]}
    attempt_dir = run_root / "evidence/attempts" / skill_id / lease["attempt_id"]
    attempt_dir.mkdir(parents=True, exist_ok=True)
    artifact = attempt_dir / "report.md"
    artifact.write_text(build_compliant_report(REGISTRY, skill_id), encoding="utf-8")
    (ev_facts, ev_sources, ev_calcs, ev_judgments, ev_roles,
     ev_receipts, ev_capabilities) = build_compliant_evidence(REGISTRY, skill_id)
    manifest = json.loads((run_root / "evidence/00-analysis-manifest.json").read_text())
    bundle = {
        "schema_version": "result-schema/v1", "run_id": manifest["run"]["run_id"],
        "work_unit_id": lease["work_unit_id"], "attempt_id": lease["attempt_id"],
        "agent_job_id": f"job-{lease['attempt_id']}", "lease_nonce": lease["lease_nonce"],
        "skill_id": skill_id, "role_id": None, "status": "PASS",
        "artifact_records": [{"artifact_id": by_id[skill_id]["artifact"]["artifact_id"],
                              "path": str(artifact.relative_to(run_root)), "bytes": artifact.stat().st_size,
                              "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                              "formal": False, "accepted": False}],
        "fact_updates": ev_facts, "source_records": ev_sources,
        "calculation_requests": ev_calcs, "judgments": ev_judgments,
        "role_runs": ev_roles, "command_receipts": ev_receipts,
        "capability_records": ev_capabilities,
        "limitations": [], "pwl_candidates": [],
        "started_at": "2026-07-23T12:00:00+08:00", "completed_at": "2026-07-23T12:01:00+08:00",
        "error": None,
    }
    rp = attempt_dir / "result.json"
    rp.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    return bundle, rp


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.run_root = self.root / "local/company/000651.SZ-格力电器/20260723-120000-corr"
        started = self.cli("start", "--registry", REGISTRY, "--repo-root", self.root,
                           "--company", "格力电器", "--code", "000651.SZ",
                           "--as-of", "2026-07-23", "--run-root", self.run_root)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        # 取 financial-data 单元并提交（next-work 直到命中）
        for _ in range(13):
            leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
            if leased["skill_id"] == "financial-data":
                break
        else:
            raise AssertionError("未取到 financial-data 租约")
        self.lease = leased
        started_job = self.cli("job-started", "--run-root", self.run_root,
                               "--work-unit-id", leased["work_unit_id"],
                               "--attempt-id", leased["attempt_id"],
                               "--lease-nonce", leased["lease_nonce"],
                               "--agent-job-id", f"job-{leased['attempt_id']}")
        self.assertEqual(started_job.returncode, 0, started_job.stdout + started_job.stderr)
        self.bundle, self.result_path = _submit_one(self.root, self.run_root, leased)
        submitted = self.cli("submit-result", "--run-root", self.run_root,
                             "--registry", REGISTRY, "--result", self.result_path)
        self.assertEqual(submitted.returncode, 0, submitted.stdout + submitted.stderr)

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)], cwd=self.root,
                              capture_output=True, text=True)

    def manifest(self):
        return json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())

    def _correction(self, **over):
        calc = self.bundle["calculation_requests"][0]
        fact = self.bundle["fact_updates"][0]
        base = {
            "schema_version": "correction-bundle/v1",
            "run_id": self.bundle["run_id"],
            "skill_id": "financial-data",
            "base_attempt_id": self.bundle["attempt_id"],
            "corrections": {
                "calculation_requests": [{
                    **calc, "args": {"expr": "2 + 3"},
                }],
                "fact_updates": [{**fact, "removed": True}],
                "judgments": [],
                "command_receipts": [],
            },
        }
        return json.dumps({**base, **over}, ensure_ascii=False)

    def _apply(self, payload):
        p = self.run_root / "evidence/attempts/correction-test.json"
        p.write_text(payload, encoding="utf-8")
        return self.cli("submit-correction", "--run-root", self.run_root,
                        "--registry", REGISTRY, "--correction", p)

    def test_correction_updates_calc_and_removes_fact_without_new_attempt(self):
        before = self.manifest()
        attempts_before = len(before["skills"][next(i for i, s in enumerate(before["skills"])
                                                  if s["skill_id"] == "financial-data")]["attempts"])
        artifact_before = before["artifacts"][0]["sha256"]
        proc = self._apply(self._correction())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        after = self.manifest()
        calcs = {c["calculation_id"]: c for c in after["calculations"]}
        self.assertEqual(calcs[self.bundle["calculation_requests"][0]["calculation_id"]]["args"]["expr"], "2 + 3")
        fact_ids = {f["fact_id"] for f in after["facts"]}
        self.assertNotIn(self.bundle["fact_updates"][0]["fact_id"], fact_ids)
        attempts_after = len(next(s for s in after["skills"] if s["skill_id"] == "financial-data")["attempts"])
        self.assertEqual(attempts_after, attempts_before, "correction 不得新增 attempt")
        self.assertEqual(after["artifacts"][0]["sha256"], artifact_before, "correction 不得重写报告")
        # correction 记录保留
        corrs = after.get("corrections", [])
        self.assertTrue(corrs, "manifest 应记录 correction")
        self.assertEqual(corrs[-1]["skill_id"], "financial-data")
        self.assertEqual(corrs[-1]["base_attempt_id"], self.bundle["attempt_id"])
        self.assertIn("digest", corrs[-1])

    def test_correction_replayed_by_audit(self):
        # 只改 calc（不删 fact，避免审计 evidence 不足），验证 audit 重放新值
        calc = self.bundle["calculation_requests"][0]
        payload = json.dumps({
            "schema_version": "correction-bundle/v1",
            "run_id": self.bundle["run_id"],
            "skill_id": "financial-data",
            "base_attempt_id": self.bundle["attempt_id"],
            "corrections": {
                "calculation_requests": [{**calc, "args": {"expr": "2 + 3"}}],
                "fact_updates": [], "judgments": [], "command_receipts": [],
            },
        }, ensure_ascii=False)
        self.assertEqual(self._apply(payload).returncode, 0)
        audit = self.cli("audit", "--run-root", self.run_root, "--registry", REGISTRY)
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        report = json.loads(audit.stdout)
        self.assertEqual(report["calculations"]["replayed"], 1)
        self.assertEqual(report.get("evidence", {}).get("violation_count"), 0)

    def test_correction_rejects_unknown_id(self):
        calc = self.bundle["calculation_requests"][0]
        payload = self._correction().replace(
            calc["calculation_id"], "calculation.brand-new.999")
        proc = self._apply(payload)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("不存在", proc.stdout + proc.stderr)

    def test_correction_rejects_missing_schema_version(self):
        proc = self._apply(self._correction().replace('"schema_version": "correction-bundle/v1",', ""))
        self.assertNotEqual(proc.returncode, 0)

    def test_correction_rejects_artifact_records(self):
        payload = self._correction().replace('"corrections"', '"artifact_records": [{"path": "x"}], "corrections"')
        proc = self._apply(payload)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("禁止", proc.stdout + proc.stderr)

    def test_correction_rejects_wrong_run_id(self):
        proc = self._apply(self._correction().replace(self.bundle["run_id"], "run-wrong"))
        self.assertNotEqual(proc.returncode, 0)

    def test_correction_rejects_wrong_skill(self):
        proc = self._apply(self._correction().replace('"skill_id": "financial-data"', '"skill_id": "ashare-data"'))
        self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
