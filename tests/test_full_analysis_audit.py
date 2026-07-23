import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
AUDIT = REPO / "tools" / "full_analysis_audit.py"


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "run"
        (self.root / "evidence/audit").mkdir(parents=True)
        (self.root / "evidence").mkdir(exist_ok=True)
        self.manifest = {
            "manifest_schema_version": "full-analysis-manifest/v2",
            "run": {"run_id": "run-1", "status": "RUNNING"},
            "facts": [
                {"fact_id": "fact.revenue", "field": "revenue", "value": "100", "source_ids": ["source.filing"]},
                {"fact_id": "fact.margin", "field": "margin", "value": "20", "source_ids": ["source.filing", "source.market"]},
            ],
            "sources": [
                {"source_id": "source.filing", "url": "https://example.invalid/a", "retrieved_at": "2026-07-23", "source_type": "filing"},
                {"source_id": "source.market", "url": "https://example.invalid/b", "retrieved_at": "2026-07-23", "source_type": "web"},
            ],
            "calculations": [
                {"calculation_id": "calculation.market-cap", "operation": "verify-market-cap", "inputs": {"price": "100"}, "expected": {"replayed": True, "outcome": "PASS"}},
            ],
        }
        (self.root / "evidence/00-analysis-manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def audit(self):
        return subprocess.run([sys.executable, str(AUDIT), "--run-root", self.root], capture_output=True, text=True)

    def test_audit_passes_traceable_facts_and_replayed_calculations(self):
        result = self.audit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((self.root / "evidence/audit/audit-result.json").read_text())
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["facts"]["checked"], 2)
        self.assertEqual(report["calculations"]["replayed"], 1)

    def test_audit_fails_loudly_when_fact_source_is_missing(self):
        self.manifest["facts"][0]["source_ids"] = ["source.missing"]
        (self.root / "evidence/00-analysis-manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        result = self.audit()
        self.assertNotEqual(result.returncode, 0)
        report = json.loads((self.root / "evidence/audit/audit-result.json").read_text())
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any(item["code"] == "missing_source" for item in report["errors"]))

    def test_audit_requires_every_calculation_to_be_replayed(self):
        self.manifest["calculations"][0]["expected"]["replayed"] = False
        (self.root / "evidence/00-analysis-manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        result = self.audit()
        self.assertNotEqual(result.returncode, 0)
        report = json.loads((self.root / "evidence/audit/audit-result.json").read_text())
        self.assertTrue(any(item["code"] == "calculation_not_replayed" for item in report["errors"]))


if __name__ == "__main__":
    unittest.main()
