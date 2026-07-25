"""P3 重复运行稳定性基准测试：compare 聚合逻辑、指标计算。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import full_analysis_benchmark as bench  # noqa: E402


def _make_run(root, run_id, facts=None, calculations=None, judgments=None, sources=None,
              *, company_code="000651.SZ", company_name="格力电器",
              as_of="2026-07-25", registry_sha256="registry-v1", status="APPROVED"):
    evidence_dir = root / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_schema_version": "full-analysis-manifest/v2",
        "run": {"run_id": run_id, "status": status, "as_of": as_of},
        "company": {"code": company_code, "name": company_name},
        "contract": {"registry_sha256": registry_sha256},
        "skills": [],
        "facts": facts or [],
        "sources": sources or [],
        "calculations": calculations or [],
        "judgments": judgments or [],
    }
    (evidence_dir / "00-analysis-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return root


class FactConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_identical_facts_100_percent(self):
        r1 = _make_run(self.base / "run1", "r1",
                       facts=[{"fact_id": "f.revenue", "value": "100", "source_ids": ["s1"]}])
        r2 = _make_run(self.base / "run2", "r2",
                       facts=[{"fact_id": "f.revenue", "value": "100", "source_ids": ["s1"]}])
        report, code = bench.compare([r1, r2])
        self.assertEqual(code, 0)
        self.assertEqual(report["overall_verdict"], "STABLE")
        self.assertEqual(report["metrics"]["fact_consistency"]["rate"], 1.0)

    def test_divergent_fact_detected(self):
        r1 = _make_run(self.base / "run1", "r1",
                       facts=[{"fact_id": "f.revenue", "value": "100", "source_ids": ["s1"]}])
        r2 = _make_run(self.base / "run2", "r2",
                       facts=[{"fact_id": "f.revenue", "value": "200", "source_ids": ["s1"]}])
        report, code = bench.compare([r1, r2])
        self.assertEqual(code, 1)
        self.assertEqual(report["overall_verdict"], "UNSTABLE")
        fc = report["metrics"]["fact_consistency"]
        self.assertEqual(fc["rate"], 0.0)
        self.assertEqual(fc["divergent_count"], 1)
        self.assertIn("事实一致率", report["issues"][0])

    def test_mixed_consistency(self):
        facts1 = [
            {"fact_id": "f.a", "value": "1", "source_ids": ["s1"]},
            {"fact_id": "f.b", "value": "2", "source_ids": ["s1"]},
        ]
        facts2 = [
            {"fact_id": "f.a", "value": "1", "source_ids": ["s1"]},
            {"fact_id": "f.b", "value": "CHANGED", "source_ids": ["s1"]},
        ]
        r1 = _make_run(self.base / "run1", "r1", facts=facts1)
        r2 = _make_run(self.base / "run2", "r2", facts=facts2)
        report, code = bench.compare([r1, r2])
        self.assertEqual(code, 1)
        self.assertEqual(report["metrics"]["fact_consistency"]["rate"], 0.5)

    def test_single_run_returns_none_rate(self):
        r1 = _make_run(self.base / "run1", "r1", facts=[{"fact_id": "f.a", "value": "1"}])
        report, code = bench.compare([r1])
        self.assertEqual(code, 2)  # 不足 2 个 run

    def test_fact_missing_from_one_run_is_not_counted_consistent(self):
        r1 = _make_run(self.base / "run1", "r1",
                       facts=[{"fact_id": "f.revenue", "value": "100", "source_ids": ["s1"]}])
        r2 = _make_run(self.base / "run2", "r2", facts=[])

        report, code = bench.compare([r1, r2])

        self.assertEqual(code, 1)
        metric = report["metrics"]["fact_consistency"]
        self.assertEqual(metric["rate"], 0.0)
        self.assertEqual(metric["missing_count"], 1)


class CalculationConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_identical_calculations(self):
        calcs = [{"calculation_id": "c.1", "expected": {"replayed": True, "outcome": "PASS"}}]
        r1 = _make_run(self.base / "run1", "r1", calculations=calcs)
        r2 = _make_run(self.base / "run2", "r2", calculations=calcs)
        report, code = bench.compare([r1, r2])
        self.assertEqual(report["metrics"]["calculation_consistency"]["rate"], 1.0)

    def test_divergent_calculation(self):
        r1 = _make_run(self.base / "run1", "r1",
                       calculations=[{"calculation_id": "c.1", "expected": {"outcome": "PASS"}}])
        r2 = _make_run(self.base / "run2", "r2",
                       calculations=[{"calculation_id": "c.1", "expected": {"outcome": "FAIL"}}])
        report, code = bench.compare([r1, r2])
        self.assertEqual(code, 1)
        self.assertEqual(report["metrics"]["calculation_consistency"]["rate"], 0.0)


class ConclusionDriftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_no_drift(self):
        judgments = [{"judgment_id": "j.1", "conclusion": "BUY"}]
        r1 = _make_run(self.base / "run1", "r1", judgments=judgments)
        r2 = _make_run(self.base / "run2", "r2", judgments=judgments)
        report, code = bench.compare([r1, r2])
        self.assertEqual(report["metrics"]["conclusion_drift"]["drift_count"], 0)

    def test_drift_detected(self):
        r1 = _make_run(self.base / "run1", "r1",
                       judgments=[{"judgment_id": "j.1", "conclusion": "BUY"}])
        r2 = _make_run(self.base / "run2", "r2",
                       judgments=[{"judgment_id": "j.1", "conclusion": "HOLD"}])
        report, code = bench.compare([r1, r2])
        self.assertEqual(code, 1)
        self.assertEqual(report["metrics"]["conclusion_drift"]["drift_count"], 1)
        self.assertTrue(any("结论漂移" in i for i in report["issues"]))


class ClaimSourceCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_full_coverage(self):
        facts = [{"fact_id": "f.1", "value": "x", "source_ids": ["s1"]}]
        r1 = _make_run(self.base / "run1", "r1", facts=facts)
        r2 = _make_run(self.base / "run2", "r2", facts=facts)
        report, code = bench.compare([r1, r2])
        self.assertEqual(report["metrics"]["claim_source_coverage"]["min"], 1.0)

    def test_partial_coverage_flagged(self):
        r1 = _make_run(self.base / "run1", "r1",
                       facts=[{"fact_id": "f.1", "value": "x", "source_ids": ["s1"]},
                              {"fact_id": "f.2", "value": "y", "source_ids": []}])
        r2 = _make_run(self.base / "run2", "r2",
                       facts=[{"fact_id": "f.1", "value": "x", "source_ids": ["s1"]}])
        report, code = bench.compare([r1, r2])
        self.assertEqual(code, 1)
        self.assertEqual(report["metrics"]["claim_source_coverage"]["min"], 0.5)

    def test_zero_facts_has_zero_coverage(self):
        r1 = _make_run(self.base / "run1", "r1", facts=[])
        r2 = _make_run(self.base / "run2", "r2", facts=[])

        report, code = bench.compare([r1, r2])

        self.assertEqual(code, 1)
        self.assertEqual(report["metrics"]["claim_source_coverage"]["min"], 0.0)


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_report_written_to_output_dir(self):
        r1 = _make_run(self.base / "run1", "r1", facts=[{"fact_id": "f.1", "value": "1", "source_ids": ["s"]}])
        r2 = _make_run(self.base / "run2", "r2", facts=[{"fact_id": "f.1", "value": "1", "source_ids": ["s"]}])
        out = self.base / "output"
        report, code = bench.compare([r1, r2], out)
        self.assertEqual(code, 0)
        self.assertTrue((out / "stability-report.json").exists())
        saved = json.loads((out / "stability-report.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["stability_schema_version"], "stability-report/v1")

    def test_three_way_comparison(self):
        r1 = _make_run(self.base / "run1", "r1", facts=[{"fact_id": "f.1", "value": "1", "source_ids": ["s"]}])
        r2 = _make_run(self.base / "run2", "r2", facts=[{"fact_id": "f.1", "value": "1", "source_ids": ["s"]}])
        r3 = _make_run(self.base / "run3", "r3", facts=[{"fact_id": "f.1", "value": "1", "source_ids": ["s"]}])
        report, code = bench.compare([r1, r2, r3])
        self.assertEqual(code, 0)
        self.assertEqual(report["runs_compared"], 3)
        self.assertEqual(report["overall_verdict"], "STABLE")

    def test_different_company_is_incomparable(self):
        facts = [{"fact_id": "f.1", "value": "1", "source_ids": ["s"]}]
        r1 = _make_run(self.base / "run1", "r1", facts=facts)
        r2 = _make_run(
            self.base / "run2", "r2", facts=facts,
            company_code="600000.SH", company_name="浦发银行",
        )

        report, code = bench.compare([r1, r2])

        self.assertEqual(code, 2)
        self.assertEqual(report["overall_verdict"], "INCOMPARABLE")
        self.assertTrue(any("company" in issue for issue in report["issues"]))

    def test_different_as_of_or_contract_is_incomparable(self):
        facts = [{"fact_id": "f.1", "value": "1", "source_ids": ["s"]}]
        r1 = _make_run(self.base / "run1", "r1", facts=facts)
        r2 = _make_run(
            self.base / "run2", "r2", facts=facts,
            as_of="2026-07-24", registry_sha256="registry-v2",
        )

        report, code = bench.compare([r1, r2])

        self.assertEqual(code, 2)
        self.assertEqual(report["overall_verdict"], "INCOMPARABLE")

    def test_non_approved_run_is_incomparable(self):
        facts = [{"fact_id": "f.1", "value": "1", "source_ids": ["s"]}]
        r1 = _make_run(self.base / "run1", "r1", facts=facts)
        r2 = _make_run(self.base / "run2", "r2", facts=facts, status="PARTIAL")

        report, code = bench.compare([r1, r2])

        self.assertEqual(code, 2)
        self.assertEqual(report["overall_verdict"], "INCOMPARABLE")


if __name__ == "__main__":
    unittest.main()
