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


class EvidenceSufficiencyTests(unittest.TestCase):
    """P1 层 2：per-skill 证据充分性。以 financial-data 为例
    （required_fact_fields=[revenue], min_dual_source_facts=1, min_calculations=1）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "run"
        (self.root / "evidence/audit").mkdir(parents=True)
        (self.root / "evidence").mkdir(exist_ok=True)

    def tearDown(self):
        self.temp.cleanup()

    def _manifest_with_skill(self, skill_id, status, facts=None, sources=None, calcs=None):
        facts = facts if facts is not None else []
        sources = sources if sources is not None else []
        calcs = calcs if calcs is not None else []
        manifest = {
            "manifest_schema_version": "full-analysis-manifest/v2",
            "run": {"run_id": "run-ev", "status": "RUNNING"},
            "skills": [{"skill_id": skill_id, "status": status}],
            "facts": facts, "sources": sources, "calculations": calcs,
        }
        (self.root / "evidence/00-analysis-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return manifest

    def _compliant_skill(self):
        """financial-data 合规证据：revenue 事实挂双源 + 1 条计算。"""
        facts = [{"fact_id": "fact.revenue", "field": "revenue", "value": "100",
                  "source_ids": ["s.filing", "s.market"], "skill_id": "financial-data"}]
        sources = [
            {"source_id": "s.filing", "url": "https://example.invalid/a", "publisher": "Exchange",
             "retrieved_at": "2026-07-23", "source_type": "filing"},
            {"source_id": "s.market", "url": "https://market.invalid/b", "publisher": "Market Data",
             "retrieved_at": "2026-07-23", "source_type": "web"},
        ]
        calcs = [{"calculation_id": "calc.1", "operation": "verify", "args": {},
                  "expected": {"replayed": True, "outcome": "PASS"},
                  "skill_id": "financial-data"}]
        return facts, sources, calcs

    def audit(self):
        return subprocess.run([sys.executable, str(AUDIT), "--run-root", self.root,
                               "--registry", str(REPO / "tools/full_analysis_contract.json")],
                              capture_output=True, text=True)

    def test_compliant_skill_passes(self):
        facts, sources, calcs = self._compliant_skill()
        self._manifest_with_skill("financial-data", "PASS", facts, sources, calcs)
        result = self.audit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((self.root / "evidence/audit/audit-result.json").read_text())
        self.assertEqual(report["status"], "PASS")
        per = report["evidence"]["per_skill"]
        self.assertEqual(len(per), 1)
        self.assertEqual(per[0]["skill_id"], "financial-data")
        self.assertEqual(per[0]["fact_count"], 1)
        self.assertEqual(per[0]["violations"], [])

    def test_zero_evidence_required_skill_fails(self):
        self._manifest_with_skill("financial-data", "PASS")  # 零事实零计算
        result = self.audit()
        self.assertNotEqual(result.returncode, 0)
        report = json.loads((self.root / "evidence/audit/audit-result.json").read_text())
        self.assertEqual(report["status"], "FAIL")
        codes = [item["code"] for item in report["errors"]]
        self.assertIn("no_skill_evidence", codes)

    def test_missing_required_fact_field_fails(self):
        # 有事实但 field 不是 revenue（缺 required_fact_fields）
        facts = [{"fact_id": "f.1", "field": "other", "value": "1",
                  "source_ids": ["s.filing"], "skill_id": "financial-data"}]
        sources = [{"source_id": "s.filing", "url": "https://example.invalid/a",
                    "retrieved_at": "2026-07-23", "source_type": "filing"}]
        calcs = [{"calculation_id": "calc.1", "operation": "verify", "args": {}, "skill_id": "financial-data"}]
        self._manifest_with_skill("financial-data", "PASS", facts, sources, calcs)
        result = self.audit()
        self.assertNotEqual(result.returncode, 0)
        report = json.loads((self.root / "evidence/audit/audit-result.json").read_text())
        codes = [item["code"] for item in report["errors"]]
        self.assertIn("missing_required_fact_fields", codes)

    def test_insufficient_dual_source_facts_fails(self):
        # revenue 事实但只挂单源（min_dual_source_facts=1 要求至少 1 条双源）
        facts = [{"fact_id": "fact.revenue", "field": "revenue", "value": "100",
                  "source_ids": ["s.filing"], "skill_id": "financial-data"}]
        sources = [{"source_id": "s.filing", "url": "https://example.invalid/a",
                    "retrieved_at": "2026-07-23", "source_type": "filing"}]
        calcs = [{"calculation_id": "calc.1", "operation": "verify", "args": {}, "skill_id": "financial-data"}]
        self._manifest_with_skill("financial-data", "PASS", facts, sources, calcs)
        result = self.audit()
        self.assertNotEqual(result.returncode, 0)
        report = json.loads((self.root / "evidence/audit/audit-result.json").read_text())
        codes = [item["code"] for item in report["errors"]]
        self.assertIn("insufficient_dual_source_facts", codes)

    def test_insufficient_calculations_fails(self):
        # 有双源 revenue 事实但零计算（min_calculations=1）
        facts = [{"fact_id": "fact.revenue", "field": "revenue", "value": "100",
                  "source_ids": ["s.filing", "s.market"], "skill_id": "financial-data"}]
        sources = [
            {"source_id": "s.filing", "url": "https://example.invalid/a", "retrieved_at": "2026-07-23", "source_type": "filing"},
            {"source_id": "s.market", "url": "https://example.invalid/b", "retrieved_at": "2026-07-23", "source_type": "web"},
        ]
        self._manifest_with_skill("financial-data", "PASS", facts, sources, [])
        result = self.audit()
        self.assertNotEqual(result.returncode, 0)
        report = json.loads((self.root / "evidence/audit/audit-result.json").read_text())
        codes = [item["code"] for item in report["errors"]]
        self.assertIn("insufficient_calculations", codes)

    def test_na_skill_not_required_to_have_evidence(self):
        # N/A 单元零证据不应产生违规
        self._manifest_with_skill("financial-data", "NOT_APPLICABLE")
        result = self.audit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((self.root / "evidence/audit/audit-result.json").read_text())
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["evidence"]["skills_checked"], 0)

    def test_scorecard_written(self):
        facts, sources, calcs = self._compliant_skill()
        self._manifest_with_skill("financial-data", "PASS", facts, sources, calcs)
        self.audit()
        scorecard_path = self.root / "evidence/quality-scorecard.json"
        self.assertTrue(scorecard_path.exists())
        scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
        self.assertEqual(scorecard["scorecard_schema_version"], "quality-scorecard/v1")
        self.assertEqual(scorecard["claim_source_coverage"], 1.0)
        self.assertEqual(scorecard["evidence_sufficiency"]["violation_count"], 0)


class CompleteEvidenceRuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "run"
        (self.root / "evidence/audit").mkdir(parents=True)
        self.registry = Path(self.temp.name) / "registry.json"
        self.rules = [
            {"kind": "min_facts", "n": 1},
            {"kind": "required_fact_fields", "values": ["revenue"]},
            {"kind": "min_dual_source_facts", "n": 1},
            {"kind": "min_calculations", "n": 1},
            {"kind": "required_judgment_rule_ids", "values": ["thesis"]},
            {"kind": "min_judgments_with_falsification", "n": 1},
            {"kind": "min_role_runs", "n": 2},
            {"kind": "min_command_receipts", "n": 2},
            {"kind": "required_command_operations", "values": ["quote", "financials"]},
            {
                "kind": "conditional_command_operations",
                "capability": "tushare_configured",
                "values": [{"op": "valuation", "feeds": "synthetic", "layer": 1}],
                "min_satisfied_ratio": 1.0,
            },
        ]
        self.registry.write_text(json.dumps({
            "skills": [{"skill_id": "synthetic", "evidence_rules": self.rules}],
        }), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def manifest(self):
        role_runs = []
        for role_id in ("analyst-a", "analyst-b"):
            path = self.root / "evidence/roles/synthetic/attempt-1" / f"role-{role_id}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{role_id} verified memo", encoding="utf-8")
            payload = path.read_bytes()
            role_runs.append({
                "role_id": role_id,
                "status": "PASS",
                "skill_id": "synthetic",
                "attempt_id": "attempt-1",
                "artifact_path": str(path.relative_to(self.root)),
                "bytes": len(payload),
                "sha256": __import__("hashlib").sha256(payload).hexdigest(),
                "verified_by_gate": True,
            })
        return {
            "manifest_schema_version": "full-analysis-manifest/v2",
            "run": {"run_id": "run-complete", "status": "RUNNING"},
            "skills": [{"skill_id": "synthetic", "status": "PASS", "limitations": []}],
            "facts": [{
                "fact_id": "fact.revenue", "field": "revenue", "value": "100",
                "source_ids": ["source.a", "source.b"], "skill_id": "synthetic",
            }],
            "sources": [
                {"source_id": "source.a", "publisher": "Exchange A"},
                {"source_id": "source.b", "publisher": "Publisher B"},
            ],
            "calculations": [{
                "calculation_id": "calculation.synthetic.1", "operation": "verify",
                "args": {}, "expected": {"replayed": True, "outcome": "PASS"},
                "skill_id": "synthetic",
            }],
            "judgments": [{
                "judgment_id": "judgment.synthetic.1", "rule_id": "thesis",
                "conclusion": "结论", "falsification": ["若收入下降则失效"],
                "skill_id": "synthetic",
            }],
            "role_runs": role_runs,
            "command_receipts": [
                {"receipt_id": "receipt.quote", "operation": "quote", "status": "PASS",
                 "skill_id": "synthetic"},
                {"receipt_id": "receipt.financials", "operation": "financials", "status": "PASS",
                 "skill_id": "synthetic"},
                {"receipt_id": "receipt.valuation", "operation": "valuation", "status": "PASS",
                 "skill_id": "synthetic"},
            ],
            "capabilities": {"tushare_configured": True},
        }

    def audit(self, manifest):
        (self.root / "evidence/00-analysis-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(AUDIT), "--run-root", self.root,
             "--registry", self.registry],
            capture_output=True, text=True,
        )

    def error_codes(self):
        report = json.loads((self.root / "evidence/audit/audit-result.json").read_text())
        return {item["code"] for item in report["errors"]}

    def test_all_registered_rule_kinds_are_enforced(self):
        manifest = self.manifest()
        manifest["judgments"] = []
        manifest["role_runs"] = []
        manifest["command_receipts"] = []

        result = self.audit(manifest)

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue({
            "missing_required_judgment_rules",
            "insufficient_judgments_with_falsification",
            "insufficient_role_runs",
            "insufficient_command_receipts",
            "missing_required_command_operations",
            "insufficient_conditional_command_operations",
        }.issubset(self.error_codes()))

    def test_bare_calculation_does_not_satisfy_minimum(self):
        manifest = self.manifest()
        manifest["calculations"][0].pop("expected")

        result = self.audit(manifest)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("insufficient_calculations", self.error_codes())

    def test_dual_source_requires_independent_publishers(self):
        manifest = self.manifest()
        manifest["sources"][1]["publisher"] = "Exchange A"

        result = self.audit(manifest)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("insufficient_dual_source_facts", self.error_codes())

    def test_role_runs_require_gate_verified_artifacts(self):
        manifest = self.manifest()
        manifest["role_runs"] = [
            {"role_id": "analyst-a", "status": "PASS", "skill_id": "synthetic"},
            {"role_id": "analyst-b", "status": "PASS", "skill_id": "synthetic"},
        ]

        result = self.audit(manifest)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("insufficient_role_runs", self.error_codes())

    def test_complete_evidence_for_every_rule_kind_passes(self):
        result = self.audit(self.manifest())

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
