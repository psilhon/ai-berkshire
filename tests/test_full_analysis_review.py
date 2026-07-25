"""P2 语义评审层测试：aggregate 聚合逻辑、ingest 校验、prepare 简报生成。"""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts/full_analysis.py"
REVIEW = REPO / "tools/full_analysis_review.py"
REGISTRY = REPO / "tools/full_analysis_contract.json"

sys.path.insert(0, str(REPO / "tools"))
import full_analysis_review as review  # noqa: E402


def _dimension_results(finding_dimensions=None):
    finding_dimensions = set(finding_dimensions or [])
    return [
        {"dimension": dimension, "verdict": "FINDING" if dimension in finding_dimensions else "PASS"}
        for dimension in review.REVIEW_PROTOCOL
    ]


def _make_review_result(skill_id, verdict, findings=None, *, run_id="test-run",
                        brief_digest=None, report_digest="report-digest",
                        evidence_digest="evidence-digest"):
    findings = findings or []
    brief_digest = brief_digest or review._digest({
        "run_id": run_id,
        "skill_id": skill_id,
        "report_digest": report_digest,
        "evidence_digest": evidence_digest,
        "contract": {},
        "review_protocol": review.REVIEW_PROTOCOL,
    })
    return {
        "review_schema_version": "semantic-review/v1",
        "skill_id": skill_id,
        "run_id": run_id,
        "brief_digest": brief_digest,
        "report_digest": report_digest,
        "evidence_digest": evidence_digest,
        "verdict": verdict,
        "dimensions": _dimension_results(f["dimension"] for f in findings),
        "findings": findings,
    }


def _write_review(root, skill_id, result):
    review_dir = root / "evidence/review"
    review_dir.mkdir(parents=True, exist_ok=True)
    path = review_dir / f"review-result-{skill_id}.json"
    path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return path


def _prepare_scope(root, skill_ids):
    review_dir = root / "evidence/review"
    review_dir.mkdir(parents=True, exist_ok=True)
    briefs = {}
    for skill_id in skill_ids:
        brief_digest = review._digest({
            "run_id": "test-run",
            "skill_id": skill_id,
            "report_digest": "report-digest",
            "evidence_digest": "evidence-digest",
            "contract": {},
            "review_protocol": review.REVIEW_PROTOCOL,
        })
        brief = {
            "brief_schema_version": "review-brief/v1",
            "skill_id": skill_id,
            "run_id": "test-run",
            "brief_digest": brief_digest,
            "report": {"sha256": "report-digest"},
            "evidence": {"sha256": "evidence-digest"},
            "contract": {},
            "review_protocol": review.REVIEW_PROTOCOL,
        }
        (review_dir / f"review-brief-{skill_id}.json").write_text(
            json.dumps(brief), encoding="utf-8")
        briefs[skill_id] = {
            "brief_digest": brief_digest,
            "report_digest": "report-digest",
            "evidence_digest": "evidence-digest",
        }
    (review_dir / "review-index.json").write_text(json.dumps({
        "run_id": "test-run", "scope": list(skill_ids), "briefs": briefs,
    }), encoding="utf-8")


class AggregateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "run"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_empty_review_dir_returns_code_2(self):
        summary, code = review.aggregate(self.root)
        self.assertEqual(code, 2)
        self.assertEqual(summary, {})

    def test_no_review_results_returns_code_1(self):
        (self.root / "evidence/review").mkdir(parents=True)
        summary, code = review.aggregate(self.root)
        self.assertEqual(code, 1)
        self.assertEqual(summary, {})

    def test_all_pass_returns_review_passed(self):
        _prepare_scope(self.root, ["investment-research", "earnings-review"])
        _write_review(self.root, "investment-research",
                      _make_review_result("investment-research", "PASS"))
        _write_review(self.root, "earnings-review",
                      _make_review_result("earnings-review", "PASS"))
        summary, code = review.aggregate(self.root)
        self.assertEqual(code, 0)
        self.assertEqual(summary["overall_verdict"], "REVIEW_PASSED")
        self.assertEqual(summary["skills_review_required"], [])
        self.assertEqual(summary["skills_reviewed"], 2)

    def test_any_review_required_returns_review_required(self):
        _prepare_scope(self.root, ["investment-research", "earnings-review"])
        _write_review(self.root, "investment-research",
                      _make_review_result("investment-research", "REVIEW_REQUIRED",
                                          [{"dimension": "evidence_support", "severity": "high",
                                            "description": "核心结论无证据支持",
                                            "evidence_refs": ["f.1"], "remediation": "补证"}]))
        _write_review(self.root, "earnings-review",
                      _make_review_result("earnings-review", "PASS"))
        summary, code = review.aggregate(self.root)
        self.assertEqual(code, 1)
        self.assertEqual(summary["overall_verdict"], "REVIEW_REQUIRED")
        self.assertEqual(summary["skills_review_required"], ["investment-research"])
        self.assertEqual(summary["total_findings"], 1)
        self.assertEqual(summary["severity_counts"]["high"], 1)

    def test_high_severity_finding_triggers_rework_target(self):
        _prepare_scope(self.root, ["management-deep-dive"])
        _write_review(self.root, "management-deep-dive",
                      _make_review_result("management-deep-dive", "REVIEW_REQUIRED",
                                          [{"dimension": "counter_evidence", "severity": "high",
                                            "description": "回避了管理层诚信风险",
                                            "evidence_refs": ["report:risk"], "remediation": "补反证"}]))
        summary, code = review.aggregate(self.root)
        self.assertEqual(code, 1)
        self.assertEqual(len(summary["rework_targets"]), 1)
        self.assertEqual(summary["rework_targets"][0]["skill_id"], "management-deep-dive")
        self.assertEqual(summary["rework_targets"][0]["high_severity_count"], 1)

    def test_corrupt_review_result_skipped_gracefully(self):
        _prepare_scope(self.root, ["investment-research"])
        review_dir = self.root / "evidence/review"
        review_dir.mkdir(parents=True, exist_ok=True)
        (review_dir / "review-result-bad.json").write_text("not json", encoding="utf-8")
        _write_review(self.root, "investment-research",
                      _make_review_result("investment-research", "PASS"))
        summary, code = review.aggregate(self.root)
        self.assertEqual(code, 1)
        self.assertEqual(summary["overall_verdict"], "REVIEW_REQUIRED")
        self.assertTrue(summary["invalid_results"])

    def test_severity_counts_all_levels(self):
        _prepare_scope(self.root, ["investment-research", "earnings-review"])
        _write_review(self.root, "investment-research",
                      _make_review_result("investment-research", "PASS",
                                          [{"dimension": "evidence_support", "severity": "low",
                                            "description": "minor",
                                            "evidence_refs": ["f.1"], "remediation": "澄清"}]))
        _write_review(self.root, "earnings-review",
                      _make_review_result("earnings-review", "PASS",
                                          [{"dimension": "limitations_completeness", "severity": "medium",
                                            "description": "moderate",
                                            "evidence_refs": ["report:limitations"], "remediation": "补限制"}]))
        summary, code = review.aggregate(self.root)
        self.assertEqual(summary["severity_counts"], {"high": 0, "medium": 1, "low": 1})
        self.assertEqual(summary["total_findings"], 2)

    def test_incomplete_prepared_scope_cannot_pass(self):
        _prepare_scope(self.root, ["investment-research", "earnings-review"])
        _write_review(self.root, "investment-research",
                      _make_review_result("investment-research", "PASS"))

        summary, code = review.aggregate(self.root)

        self.assertEqual(code, 1)
        self.assertEqual(summary["overall_verdict"], "REVIEW_REQUIRED")
        self.assertEqual(summary["missing_skills"], ["earnings-review"])


class IngestValidationTests(unittest.TestCase):
    def test_valid_result_passes(self):
        result = _make_review_result("investment-research", "PASS")
        errors = review._validate_review_result(result)
        self.assertEqual(errors, [])

    def test_missing_dimension_rejected(self):
        result = _make_review_result("investment-research", "PASS")
        result["dimensions"] = result["dimensions"][:-1]
        errors = review._validate_review_result(result)
        self.assertTrue(any("dimensions" in error for error in errors))

    def test_wrong_schema_version_rejected(self):
        result = _make_review_result("investment-research", "PASS")
        result["review_schema_version"] = "semantic-review/v999"
        errors = review._validate_review_result(result)
        self.assertTrue(any("review_schema_version" in e for e in errors))

    def test_invalid_verdict_rejected(self):
        result = _make_review_result("investment-research", "MAYBE")
        errors = review._validate_review_result(result)
        self.assertTrue(any("verdict" in e for e in errors))

    def test_missing_skill_id_rejected(self):
        result = _make_review_result("", "PASS")
        errors = review._validate_review_result(result)
        self.assertTrue(any("skill_id" in e for e in errors))

    def test_invalid_finding_dimension_rejected(self):
        result = _make_review_result("investment-research", "PASS",
                                     [{"dimension": "nonexistent_dim", "severity": "low",
                                       "description": "test"}])
        errors = review._validate_review_result(result)
        self.assertTrue(any("dimension" in e for e in errors))

    def test_invalid_severity_rejected(self):
        result = _make_review_result("investment-research", "PASS",
                                     [{"dimension": "evidence_support", "severity": "critical",
                                       "description": "test"}])
        errors = review._validate_review_result(result)
        self.assertTrue(any("severity" in e for e in errors))

    def test_empty_description_rejected(self):
        result = _make_review_result("investment-research", "PASS",
                                     [{"dimension": "evidence_support", "severity": "low",
                                       "description": ""}])
        errors = review._validate_review_result(result)
        self.assertTrue(any("description" in e for e in errors))


class CliIntegrationTests(unittest.TestCase):
    """通过 CLI 测试 prepare → ingest → summarize 全流程。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.run_root = self.root / "local/test/TEST-测试/20260725-test"

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)],
                              cwd=self.root, capture_output=True, text=True)

    def _setup_run_with_artifact(self, skill_id):
        """创建一个最小 run 结构，含 manifest + 正式产物 + 归因证据。"""
        evidence_dir = self.run_root / "evidence"
        evidence_dir.mkdir(parents=True)
        manifest = {
            "manifest_schema_version": "full-analysis-manifest/v2",
            "run": {"run_id": "test-run", "status": "RUNNING"},
            "skills": [{
                "skill_id": skill_id, "status": "PASS",
                "artifact_records": [{
                    "artifact_id": f"artifact.{skill_id}",
                    "path": f"01-data-screen/{skill_id}.md",
                    "bytes": 100, "sha256": "abc", "formal": True, "accepted": True,
                }],
            }],
            "facts": [{"fact_id": "f.1", "field": "revenue", "value": "100",
                       "source_ids": ["s.1"], "skill_id": skill_id}],
            "sources": [{"source_id": "s.1", "url": "https://example.invalid/a",
                         "retrieved_at": "2026-07-25", "source_type": "filing"}],
            "calculations": [],
        }
        (evidence_dir / "00-analysis-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        artifact_dir = self.run_root / "01-data-screen"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / f"{skill_id}.md").write_text("# 测试报告\n内容", encoding="utf-8")

    def test_prepare_ingest_summarize_full_flow(self):
        self._setup_run_with_artifact("investment-research")

        # prepare
        prep = self.cli("review", "prepare", "--run-root", self.run_root,
                        "--scope", "investment-research")
        self.assertEqual(prep.returncode, 0, prep.stdout + prep.stderr)
        prep_out = json.loads(prep.stdout)
        self.assertEqual(prep_out["count"], 1)
        self.assertEqual(prep_out["prepared"][0]["skill_id"], "investment-research")

        # 验证简报文件存在
        brief_path = self.run_root / "evidence/review/review-brief-investment-research.json"
        self.assertTrue(brief_path.exists())
        brief = json.loads(brief_path.read_text(encoding="utf-8"))
        self.assertEqual(brief["brief_schema_version"], "review-brief/v1")
        self.assertIn("review_protocol", brief)
        self.assertEqual(brief["evidence"]["fact_count"], 1)
        self.assertIn("brief_digest", brief)
        self.assertIn("sha256", brief["report"])
        self.assertIn("sha256", brief["evidence"])

        # 模拟评审子 Agent 产出
        review_result = _make_review_result(
            "investment-research", "REVIEW_REQUIRED",
            [{"dimension": "evidence_support", "severity": "high",
              "description": "核心结论中的营收增速断言无归因事实支持",
              "evidence_refs": ["f.1"], "remediation": "补充证据或降低结论强度"}],
            brief_digest=brief["brief_digest"],
            report_digest=brief["report"]["sha256"],
            evidence_digest=brief["evidence"]["sha256"],
        )
        review_file = self.run_root / "evidence/review/submitted-review.json"
        review_file.write_text(json.dumps(review_result, ensure_ascii=False), encoding="utf-8")

        # ingest
        ing = self.cli("review", "ingest", "--run-root", self.run_root,
                       "--review", review_file)
        self.assertEqual(ing.returncode, 0, ing.stdout + ing.stderr)
        ing_out = json.loads(ing.stdout)
        self.assertEqual(ing_out["verdict"], "REVIEW_REQUIRED")

        # summarize
        summ = self.cli("review", "summarize", "--run-root", self.run_root)
        self.assertEqual(summ.returncode, 1)  # REVIEW_REQUIRED → exit 1
        summ_out = json.loads(summ.stdout)
        self.assertEqual(summ_out["overall_verdict"], "REVIEW_REQUIRED")
        self.assertIn("investment-research", summ_out["skills_review_required"])

        # 验证 summary 文件
        summary_path = self.run_root / "evidence/review/semantic-review-summary.json"
        self.assertTrue(summary_path.exists())

    def test_prepare_includes_registered_delivery_summary_and_global_evidence(self):
        self._setup_run_with_artifact("investment-research")
        manifest_path = self.run_root / "evidence/00-analysis-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        summary_path = self.run_root / "测试公司-全量分析-总结报告.md"
        summary_path.write_text("# 核心结论速览\n总结内容", encoding="utf-8")
        summary_digest = hashlib.sha256(summary_path.read_bytes()).hexdigest()
        manifest["delivery"] = {"summary": {
            "artifact_id": "artifact.delivery-summary",
            "path": summary_path.name,
            "bytes": summary_path.stat().st_size,
            "sha256": summary_digest,
            "formal": True,
            "accepted": True,
        }}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False),
                                 encoding="utf-8")

        prep = self.cli(
            "review", "prepare", "--run-root", self.run_root,
            "--scope", "delivery-summary",
        )

        self.assertEqual(prep.returncode, 0, prep.stdout + prep.stderr)
        brief = json.loads(
            (self.run_root /
             "evidence/review/review-brief-delivery-summary.json").read_text())
        self.assertEqual(brief["skill_id"], "delivery-summary")
        self.assertEqual(brief["report"]["sha256"], summary_digest)
        self.assertEqual(brief["evidence"]["fact_count"], 1)

    def test_default_prepare_automatically_reviews_not_applicable_reports(self):
        self._setup_run_with_artifact("quality-screen")
        manifest_path = self.run_root / "evidence/00-analysis-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["skills"][0]["status"] = "NOT_APPLICABLE"
        manifest["skills"][0]["not_applicable"] = {
            "predicate": "has_comparable_financial_history",
            "fact_id": "f.1",
            "alternative": None,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False),
                                 encoding="utf-8")

        prep = self.cli(
            "review", "prepare", "--run-root", self.run_root,
        )

        self.assertEqual(prep.returncode, 0, prep.stdout + prep.stderr)
        output = json.loads(prep.stdout)
        self.assertEqual(
            [item["skill_id"] for item in output["prepared"]],
            ["quality-screen"],
        )
        self.assertTrue(
            (self.run_root /
             "evidence/review/review-brief-quality-screen.json").is_file())

    def test_ingest_rejects_invalid_schema(self):
        self._setup_run_with_artifact("investment-research")
        bad_review = {"review_schema_version": "wrong", "skill_id": "x", "verdict": "MAYBE", "findings": "not-list"}
        bad_file = self.run_root / "bad-review.json"
        bad_file.write_text(json.dumps(bad_review), encoding="utf-8")
        ing = self.cli("review", "ingest", "--run-root", self.run_root, "--review", bad_file)
        self.assertNotEqual(ing.returncode, 0)

    def test_ingest_rejects_foreign_run_and_stale_brief(self):
        self._setup_run_with_artifact("investment-research")
        prep = self.cli("review", "prepare", "--run-root", self.run_root,
                        "--scope", "investment-research")
        self.assertEqual(prep.returncode, 0, prep.stdout + prep.stderr)
        brief = json.loads(
            (self.run_root / "evidence/review/review-brief-investment-research.json").read_text())
        result = _make_review_result(
            "investment-research", "PASS",
            run_id="foreign-run",
            brief_digest="stale-digest",
            report_digest=brief["report"]["sha256"],
            evidence_digest=brief["evidence"]["sha256"],
        )
        submitted = self.run_root / "evidence/review/foreign-review.json"
        submitted.write_text(json.dumps(result), encoding="utf-8")

        ingested = self.cli("review", "ingest", "--run-root", self.run_root,
                            "--review", submitted)

        self.assertNotEqual(ingested.returncode, 0)

    def test_summarize_invalidates_review_after_report_changes(self):
        skill_id = "investment-research"
        self._setup_run_with_artifact(skill_id)
        prep = self.cli("review", "prepare", "--run-root", self.run_root,
                        "--scope", skill_id)
        self.assertEqual(prep.returncode, 0, prep.stdout + prep.stderr)
        brief = json.loads(
            (self.run_root / f"evidence/review/review-brief-{skill_id}.json").read_text())
        result = _make_review_result(
            skill_id, "PASS",
            brief_digest=brief["brief_digest"],
            report_digest=brief["report"]["sha256"],
            evidence_digest=brief["evidence"]["sha256"],
        )
        submitted = self.run_root / "evidence/review/submitted-pass.json"
        submitted.write_text(json.dumps(result), encoding="utf-8")
        ingested = self.cli("review", "ingest", "--run-root", self.run_root,
                            "--review", submitted)
        self.assertEqual(ingested.returncode, 0, ingested.stdout + ingested.stderr)
        (self.run_root / f"01-data-screen/{skill_id}.md").write_text(
            "# 已返工\n新内容", encoding="utf-8")

        summarized = self.cli("review", "summarize", "--run-root", self.run_root)

        self.assertEqual(summarized.returncode, 1)
        output = json.loads(summarized.stdout)
        self.assertEqual(output["overall_verdict"], "REVIEW_REQUIRED")


if __name__ == "__main__":
    unittest.main()
