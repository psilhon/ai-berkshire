import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "tools" / "full_analysis_gate.py"
REGISTRY = REPO / "tools" / "full_analysis_contract.json"


def run_gate(root, *args):
    return subprocess.run(
        [sys.executable, str(GATE), *map(str, args)],
        cwd=root,
        capture_output=True,
        text=True,
    )


class GateV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.run_root = self.root / "local/company/000651.SZ-格力电器/20260723-120000-ab12"

    def tearDown(self):
        self.temp.cleanup()

    def init(self):
        result = run_gate(
            self.root, "init", "--registry", REGISTRY, "--repo-root", self.root,
            "--company", "格力电器", "--code", "000651.SZ", "--as-of", "2026-07-23",
            "--platform", "workbuddy",
            "--run-root", self.run_root,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_init_creates_canonical_manifest_and_uniform_intermediate_dirs(self):
        self.init()
        manifest_path = self.run_root / "evidence/00-analysis-manifest.json"
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_schema_version"], "full-analysis-manifest/v2")
        self.assertEqual(manifest["run"]["status"], "RUNNING")
        self.assertEqual(manifest["company"]["code"], "000651.SZ")
        self.assertEqual(len(manifest["skills"]), 20)
        self.assertTrue((self.run_root / "evidence/attempts").is_dir())
        self.assertTrue((self.run_root / "evidence/work-packets").is_dir())
        self.assertTrue((self.run_root / "05-内容生产").is_dir())
        self.assertFalse((self.run_root / "manifest.json").exists())

    def test_ingest_promotes_attempt_artifact_and_updates_skill_atomically(self):
        self.init()
        attempt_dir = self.run_root / "evidence/attempts/ashare-data/attempt-01"
        attempt_dir.mkdir(parents=True)
        source = attempt_dir / "result.md"
        source.write_text("# 数据截止日\n## 核心结论\n内容\n", encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        result_bundle = attempt_dir / "result.json"
        result_bundle.write_text(json.dumps({
            "schema_version": "result-schema/v1",
            "run_id": json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())["run"]["run_id"],
            "work_unit_id": "wu-ashare-data",
            "attempt_id": "attempt-01",
            "agent_job_id": "job-01",
            "lease_nonce": "lease-01",
            "skill_id": "ashare-data",
            "role_id": None,
            "status": "PASS",
            "artifact_records": [{
                "artifact_id": "artifact.ashare-data",
                "path": str(source.relative_to(self.run_root)),
                "bytes": source.stat().st_size,
                "sha256": digest,
                "formal": False,
                "accepted": False,
            }],
            "fact_updates": [], "source_records": [], "calculation_requests": [],
            "judgments": [], "limitations": [], "pwl_candidates": [],
            "started_at": "2026-07-23T12:00:00+08:00",
            "completed_at": "2026-07-23T12:01:00+08:00", "error": None,
        }, ensure_ascii=False), encoding="utf-8")
        ingested = run_gate(self.root, "ingest-result", "--run-root", self.run_root,
                            "--registry", REGISTRY, "--result", result_bundle)
        self.assertEqual(ingested.returncode, 0, ingested.stdout + ingested.stderr)
        formal = self.run_root / "01-数据与快筛/01-ashare-data.md"
        self.assertTrue(formal.is_file())
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        skill = next(item for item in manifest["skills"] if item["skill_id"] == "ashare-data")
        self.assertEqual(skill["status"], "PASS")
        self.assertEqual(skill["artifact_records"][0]["sha256"], digest)

    def test_finalize_rejects_incomplete_run_loudly(self):
        self.init()
        result = run_gate(self.root, "finalize", "--run-root", self.run_root,
                          "--registry", REGISTRY)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PENDING", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
