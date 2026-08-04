"""Task 7：rework 命令 —— 报告正文/artifact 类返工的有状态封装（TDD 失败测试先行）。"""

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


class ReworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.run_root = self.root / "local/company/000651.SZ-格力电器/20260723-120000-rw"
        started = self.cli("start", "--registry", REGISTRY, "--repo-root", self.root,
                           "--company", "格力电器", "--code", "000651.SZ",
                           "--as-of", "2026-07-23", "--run-root", self.run_root)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        # 取第一个单元并完整提交
        self.lease = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(self.lease["status"], "LEASED")
        self._complete(self.lease)

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)], cwd=self.root,
                              capture_output=True, text=True)

    def state(self):
        return json.loads((self.run_root / "evidence/runtime-state.json").read_text())

    def _complete(self, lease):
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        skill_id = lease["skill_id"]
        by_id = {s["skill_id"]: s for s in registry["skills"]}
        started = self.cli("job-started", "--run-root", self.run_root,
                           "--work-unit-id", lease["work_unit_id"],
                           "--attempt-id", lease["attempt_id"],
                           "--lease-nonce", lease["lease_nonce"],
                           "--agent-job-id", f"job-{lease['attempt_id']}")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        attempt_dir = self.run_root / "evidence/attempts" / skill_id / lease["attempt_id"]
        attempt_dir.mkdir(parents=True, exist_ok=True)
        artifact = attempt_dir / "report.md"
        artifact.write_text(build_compliant_report(REGISTRY, skill_id), encoding="utf-8")
        (ev_facts, ev_sources, ev_calcs, ev_judgments, ev_roles,
         ev_receipts, ev_capabilities) = build_compliant_evidence(
            REGISTRY, skill_id, self.run_root)
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        bundle = {
            "schema_version": "result-schema/v1", "run_id": manifest["run"]["run_id"],
            "work_unit_id": lease["work_unit_id"], "attempt_id": lease["attempt_id"],
            "agent_job_id": f"job-{lease['attempt_id']}", "lease_nonce": lease["lease_nonce"],
            "skill_id": skill_id, "role_id": None, "status": "PASS",
            "artifact_records": [{"artifact_id": by_id[skill_id]["artifact"]["artifact_id"],
                                  "path": str(artifact.relative_to(self.run_root)),
                                  "bytes": artifact.stat().st_size,
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
        submitted = self.cli("submit-result", "--run-root", self.run_root,
                             "--registry", REGISTRY, "--result", rp)
        self.assertEqual(submitted.returncode, 0, submitted.stdout + submitted.stderr)

    def _rework(self, work_unit_id, reason=""):
        return self.cli("rework", "--run-root", self.run_root,
                        "--work-unit-id", work_unit_id,
                        *(["--reason", reason] if reason else []))

    def test_rework_resets_done_unit(self):
        proc = self._rework(self.lease["work_unit_id"], reason="报告缺章节")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        st = self.state()
        unit = next(u for u in st["work_units"] if u["work_unit_id"] == self.lease["work_unit_id"])
        self.assertEqual(unit["status"], "PENDING")
        self.assertIsNone(unit.get("lease"))
        self.assertEqual(unit.get("reuse_attempt"), self.lease["attempt_id"])
        self.assertEqual(st.get("rework_count"), 1)
        events = (self.run_root / "evidence/events.jsonl").read_text().splitlines()
        last = json.loads(events[-1])
        self.assertEqual(last["type"], "rework_initiated")
        self.assertEqual(last["reason"], "报告缺章节")

    def test_rework_rejects_pending_unit(self):
        # 第二个单元从未派发 → 拒绝
        st = self.state()
        pending = next(u for u in st["work_units"] if u["status"] == "PENDING")
        proc = self._rework(pending["work_unit_id"])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("DONE/PARTIAL", proc.stdout + proc.stderr)

    def test_rework_rejects_unknown_unit(self):
        proc = self._rework("wu-no-such-unit")
        self.assertNotEqual(proc.returncode, 0)

    def test_next_work_returns_reuse_base_attempt(self):
        self._rework(self.lease["work_unit_id"])
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(leased["status"], "LEASED")
        self.assertEqual(leased["work_unit_id"], self.lease["work_unit_id"])
        self.assertEqual(leased.get("reuse_base_attempt"), self.lease["attempt_id"])
        self.assertNotEqual(leased["attempt_id"], self.lease["attempt_id"])


if __name__ == "__main__":
    unittest.main()
