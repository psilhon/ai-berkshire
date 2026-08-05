import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
AUDIT = REPO / "tools" / "full_analysis_audit.py"
LEAN_CONTRACT = REPO / "tools" / "full_analysis_contract.json"
sys.path.insert(0, str(REPO / "tools"))
import full_analysis_audit as audit_module  # noqa: E402


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

    def test_replay_expands_multi_value_financial_rigor_arguments(self):
        calculations = [{
            "calculation_id": "calculation.three-scenario",
            "operation": "three-scenario",
            "args": {
                "price": "39.20",
                "eps": "5.70",
                "shares": "252.20",
                "growth": ["0.08", "0.04", "-0.03"],
                "pe": ["8", "6.5", "5"],
                "years": 3,
                "currency": "CNY",
            },
        }]

        changed = audit_module._replay_calculation_requests(calculations)

        self.assertTrue(changed)
        self.assertEqual(calculations[0]["expected"]["replayed"], True)
        self.assertEqual(calculations[0]["expected"]["outcome"], "PASS")

    def test_replay_cross_validate_conflict_counts_as_replayed(self):
        # E5: cross-validate 来源偏差>容差时 rc=1（验证未过但工具执行成功），
        # 应记为 outcome=CONFLICT/replayed=True，不再误判"未重放"。
        calculations = [{
            "calculation_id": "calculation.cross-conflict",
            "operation": "cross-validate",
            "args": {"field": "share", "values": {"a": 70, "b": 100}, "unit": "%"},
        }]
        changed = audit_module._replay_calculation_requests(calculations)
        self.assertTrue(changed)
        exp = calculations[0]["expected"]
        self.assertEqual(exp["replayed"], True)
        self.assertEqual(exp["outcome"], "CONFLICT")
        # CONFLICT 计入"已重放"集合
        self.assertIn(calculations[0], audit_module._replayed_calculations({"calculations": calculations}))

    def test_replay_does_not_trust_stale_expected_result(self):
        calculations = [{
            "calculation_id": "calculation.three-scenario",
            "operation": "three-scenario",
            "args": {
                "price": "39.20",
                "eps": "5.70",
                "shares": "252.20",
                "growth": ["0.08", "0.04", "-0.03"],
                "pe": ["8", "6.5", "5"],
                "years": 3,
                "currency": "CNY",
            },
            "expected": {"replayed": False, "outcome": "FAIL"},
        }]

        audit_module._replay_calculation_requests(calculations)

        self.assertEqual(calculations[0]["expected"]["replayed"], True)
        self.assertEqual(calculations[0]["expected"]["outcome"], "PASS")


class ConditionalReceiptTests(unittest.TestCase):
    def test_unavailable_receipt_with_limitations_is_exempt(self):
        rule = {
            "capability": "tushare_configured",
            "values": [{"op": "valuation"}, {"op": "weekly"}],
            "min_satisfied_ratio": 1.0,
            "tolerate_missing_with_limitation": True,
            "tolerate_failed_with_limitation": True,
        }
        ev = {
            "command_receipts": [
                {"operation": "valuation", "status": "PASS"},
                {
                    "operation": "weekly",
                    "status": "UNAVAILABLE",
                    "reason": "empty_data: upstream returned no rows",
                },
            ]
        }
        context = {
            "capabilities": {"tushare_configured": True},
            "limitations": [],
        }

        violations = audit_module._eval_conditional_command_operations(
            "synthetic", rule, ev, context,
        )

        self.assertEqual(violations, [])


class EvidenceSufficiencyTests(unittest.TestCase):
    """lean 契约（full-analysis-contract/lean-v1）下的 per-skill 证据校验。

    lean 已移除 evidence_rules（required_fact_fields / min_dual_source_facts /
    min_calculations 等），故本层在真实契约下只剩两条 Audit 仍执行的判定：
      - 产出报告（PASS / PASS_WITH_LIMITATIONS）的单元不得零事实（no_skill_evidence）；
      - 层 1 可追溯性（来源存在、计算已重放、id 不重复）。
    报告实质地板（as_of / 来源 / 免责）与 artifact min_bytes 由 Gate 执行
    （full_analysis_gate._substance_errors 与 artifact 检查），已在
    tests/test_full_analysis_gate_v2.py 覆盖，Audit 不重复判定。
    """

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
                               "--registry", str(LEAN_CONTRACT)],
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

    def test_lean_contract_registers_no_evidence_rules(self):
        # lean 契约不再声明 evidence_rules，Audit 的规则引擎在真实契约下无规则可执行；
        # per_skill.required_rules 必须为 0（若契约回退带回规则，此断言会立刻炸出来）。
        contract = json.loads(LEAN_CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(contract["schema_version"], "full-analysis-contract/lean-v1")
        for skill in contract["skills"]:
            self.assertNotIn("evidence_rules", skill, skill["skill_id"])

        facts, sources, calcs = self._compliant_skill()
        self._manifest_with_skill("financial-data", "PASS", facts, sources, calcs)
        result = self.audit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((self.root / "evidence/audit/audit-result.json").read_text())
        self.assertEqual(report["evidence"]["per_skill"][0]["required_rules"], 0)

    def test_single_source_fact_without_calculation_passes_under_lean(self):
        # 这份证据在 v2 会同时触发 missing_required_fact_fields /
        # insufficient_dual_source_facts / insufficient_calculations；
        # lean 移除 evidence_rules 后，只要事实可追溯即通过。
        facts = [{"fact_id": "f.1", "field": "other", "value": "1",
                  "source_ids": ["s.filing"], "skill_id": "financial-data"}]
        sources = [{"source_id": "s.filing", "url": "https://example.invalid/a",
                    "retrieved_at": "2026-07-23", "source_type": "filing"}]
        self._manifest_with_skill("financial-data", "PASS", facts, sources, [])
        result = self.audit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((self.root / "evidence/audit/audit-result.json").read_text())
        self.assertEqual(report["status"], "PASS")
        codes = {item["code"] for item in report["errors"]}
        self.assertEqual(codes & {
            "missing_required_fact_fields",
            "insufficient_dual_source_facts",
            "insufficient_calculations",
        }, set())

    def test_audit_does_not_judge_report_substance_or_artifact_bytes(self):
        # 分层边界：实质三锚（as_of / 来源 / 免责）与 artifact min_bytes 归 Gate；
        # 报告即使是空壳、远低于 min_bytes=3000，Audit 也只看证据账本，不改判。
        artifact = self.root / "01-数据与快筛/02-financial-data.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("# 空壳\n", encoding="utf-8")
        facts, sources, calcs = self._compliant_skill()
        self._manifest_with_skill("financial-data", "PASS", facts, sources, calcs)
        result = self.audit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((self.root / "evidence/audit/audit-result.json").read_text())
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["evidence"]["violation_count"], 0)

    def test_untraceable_fact_still_fails_under_lean(self):
        # lean 放松了证据充分性，但层 1 可追溯性仍是硬地板：事实不挂来源即 FAIL。
        facts = [{"fact_id": "f.1", "field": "revenue", "value": "1",
                  "source_ids": [], "skill_id": "financial-data"}]
        self._manifest_with_skill("financial-data", "PASS", facts, [], [])
        result = self.audit()
        self.assertNotEqual(result.returncode, 0)
        report = json.loads((self.root / "evidence/audit/audit-result.json").read_text())
        codes = [item["code"] for item in report["errors"]]
        self.assertIn("fact_without_source", codes)

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


class LeanFullRunAuditTests(unittest.TestCase):
    """完整 lean 交付（13 单元报告 + manifest）应整体通过 Audit。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "run"
        (self.root / "evidence/audit").mkdir(parents=True)
        self.contract = json.loads(LEAN_CONTRACT.read_text(encoding="utf-8"))

    def tearDown(self):
        self.temp.cleanup()

    def _write_report(self, skill: dict) -> dict:
        """按 lean 实质要求写一份合规报告：含数据截止日、来源、免责，并满足 min_bytes。"""
        path = self.root / skill["artifact"]["formal_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        body = (
            f"# {skill['skill_id']}\n\n"
            "## 核心结论\n\n数据截止日 2026-07-23。来源：交易所公告与公司年报。\n"
            + "结论正文，覆盖经营质量、竞争格局与估值区间的判断。\n" * 60
            + "\n## 限制与缺口\n\n数据缺口已逐条登记。\n\n"
            "## 声明\n\n本报告仅供学习研究，不构成投资建议。\n"
        )
        while len(body.encode("utf-8")) < skill["artifact"].get("min_bytes", 0):
            body += "补充分析段落，说明关键假设及其证伪条件。\n"
        path.write_text(body, encoding="utf-8")
        payload = path.read_bytes()
        return {
            "skill_id": skill["skill_id"],
            "path": skill["artifact"]["formal_path"],
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    def _compliant_run(self) -> dict:
        skills, facts, artifacts = [], [], []
        for index, skill in enumerate(self.contract["skills"], start=1):
            skill_id = skill["skill_id"]
            skills.append({"skill_id": skill_id, "status": "PASS", "limitations": []})
            facts.append({
                "fact_id": f"fact.{index}", "field": "revenue", "value": "100",
                "source_ids": ["s.filing"], "skill_id": skill_id,
            })
            artifacts.append(self._write_report(skill))
        manifest = {
            "manifest_schema_version": self.contract["manifest_schema_version"],
            "run": {"run_id": "run-lean", "status": "RUNNING", "as_of": "2026-07-23"},
            "skills": skills,
            "artifacts": artifacts,
            "facts": facts,
            "sources": [{"source_id": "s.filing", "url": "https://example.invalid/a",
                         "publisher": "Exchange", "retrieved_at": "2026-07-23",
                         "source_type": "filing"}],
            "calculations": [{
                "calculation_id": "calc.1", "operation": "verify", "args": {},
                "expected": {"replayed": True, "outcome": "PASS"},
                "skill_id": "financial-data",
            }],
            "limitations": [],
        }
        (self.root / "evidence/00-analysis-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return manifest

    def audit(self):
        return subprocess.run(
            [sys.executable, str(AUDIT), "--run-root", self.root,
             "--registry", str(LEAN_CONTRACT)],
            capture_output=True, text=True,
        )

    def test_fully_compliant_lean_run_passes(self):
        manifest = self._compliant_run()

        result = self.audit()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((self.root / "evidence/audit/audit-result.json").read_text())
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["evidence"]["skills_checked"], len(manifest["skills"]))
        self.assertEqual(report["evidence"]["violation_count"], 0)
        scorecard = json.loads(
            (self.root / "evidence/quality-scorecard.json").read_text(encoding="utf-8"))
        self.assertEqual(scorecard["claim_source_coverage"], 1.0)
        self.assertEqual(scorecard["evidence_sufficiency"]["skills_with_violations"], [])

    def test_one_skill_without_evidence_fails_the_whole_run(self):
        manifest = self._compliant_run()
        orphan = manifest["skills"][-1]["skill_id"]
        manifest["facts"] = [f for f in manifest["facts"] if f["skill_id"] != orphan]
        (self.root / "evidence/00-analysis-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        result = self.audit()

        self.assertNotEqual(result.returncode, 0)
        report = json.loads((self.root / "evidence/audit/audit-result.json").read_text())
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(
            [(item["code"], item["skill_id"]) for item in report["errors"]],
            [("no_skill_evidence", orphan)])


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
