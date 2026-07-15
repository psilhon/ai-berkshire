#!/usr/bin/env python3
"""Unit tests for tools/report_audit.py — fail-closed verdict + extraction filters."""

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import report_audit as ra  # noqa: E402


def quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def item(i, reported, v1=None, v2=None, label="营收", unit="亿"):
    d = {"id": i, "label": label, "reported_value": reported, "unit": unit,
         "fetched_value": v1, "fetched_source": "src1" if v1 is not None else "",
         "fetched_value2": v2, "fetched_source2": "src2" if v2 is not None else ""}
    return d


def dual_pass(i, value=100):
    return item(i, value, v1=value, v2=value)


class TestVerdictFailClosed(unittest.TestCase):
    def test_empty_results_is_insufficient(self):
        # 修复前: 空数组输出【准出】
        out = quiet(ra.render_verdict, [])
        self.assertEqual(out["verdict"], "INSUFFICIENT")

    def test_all_dual_source_pass_is_pass(self):
        out = quiet(ra.render_verdict, [dual_pass(1), dual_pass(2), dual_pass(3)])
        self.assertEqual(out["verdict"], "PASS")

    def test_single_source_only_is_insufficient(self):
        # 只有一个来源即使全对也不达双源标准
        out = quiet(ra.render_verdict,
                    [item(1, 100, v1=100), item(2, 100, v1=100), item(3, 100, v1=100)])
        self.assertEqual(out["verdict"], "INSUFFICIENT")
        self.assertEqual(out["single_source_count"], 3)

    def test_skipped_item_blocks_pass(self):
        out = quiet(ra.render_verdict,
                    [dual_pass(1), dual_pass(2), dual_pass(3), item(4, 100)])
        self.assertEqual(out["verdict"], "INSUFFICIENT")
        self.assertEqual(out["skipped_count"], 1)

    def test_below_min_sample_is_insufficient(self):
        out = quiet(ra.render_verdict, [dual_pass(1), dual_pass(2)])
        self.assertEqual(out["verdict"], "INSUFFICIENT")

    def test_hard_mismatch_is_fail(self):
        bad = item(1, 100, v1=50, v2=50)
        out = quiet(ra.render_verdict, [bad, dual_pass(2), dual_pass(3)])
        self.assertEqual(out["verdict"], "FAIL")

    def test_single_source_mismatch_is_fail(self):
        out = quiet(ra.render_verdict,
                    [item(1, 100, v1=50), dual_pass(2), dual_pass(3)])
        self.assertEqual(out["verdict"], "FAIL")

    def test_source_conflict_is_insufficient_not_pass(self):
        # 修复前: 一对一错仅警告仍可准出
        conflict = item(1, 100, v1=100, v2=50)
        out = quiet(ra.render_verdict, [conflict, dual_pass(2), dual_pass(3)])
        self.assertEqual(out["verdict"], "INSUFFICIENT")
        self.assertEqual(out["warn_count"], 1)

    def test_fail_wins_over_insufficient(self):
        out = quiet(ra.render_verdict, [item(1, 100, v1=50, v2=40), item(2, 100)])
        self.assertEqual(out["verdict"], "FAIL")


class TestExtractionYearHandling(unittest.TestCase):
    MD = (
        "# 财务历史\n\n"
        "| 年份 | 营收(亿) | 净利润(亿) |\n"
        "|------|---------|-----------|\n"
        "| 2024 | 1500 | 300 |\n"
        "| 2025 | 1800 | 360 |\n"
    )

    def test_year_keyed_rows_are_extracted(self):
        # 修复前: 行标签是纯年份 → 整行数据被丢弃
        points, _ = quiet(ra.extract_data_points, self.MD)
        values = [p["reported_value"] for p in points]
        self.assertIn(1500, values)
        self.assertIn(360, values)

    def test_year_values_are_not_data_points(self):
        points, stats = quiet(ra.extract_data_points, self.MD)
        values = [p["reported_value"] for p in points]
        self.assertNotIn(2024, values)
        self.assertNotIn(2025, values)

    def test_year_rows_carry_period_context(self):
        points, _ = quiet(ra.extract_data_points, self.MD)
        labels = [p["label"] for p in points if p["reported_value"] == 1500]
        self.assertTrue(any("2024" in l for l in labels), labels)


class TestExtractionQualitativeFilter(unittest.TestCase):
    MD = (
        "| 策略 | 建议仓位 | 逻辑 |\n"
        "|------|---------|------|\n"
        "| 激进型 | 20% | 涨30%空间 |\n\n"
        "| 指标 | 数值 |\n"
        "|------|-----|\n"
        "| 毛利率 | 56% |\n"
    )

    def test_qualitative_columns_filtered(self):
        points, stats = quiet(ra.extract_data_points, self.MD)
        labels = [p["label"] for p in points]
        self.assertFalse(any("逻辑" in l for l in labels), labels)
        self.assertFalse(any("建议" in l for l in labels), labels)

    def test_real_metrics_survive(self):
        points, _ = quiet(ra.extract_data_points, self.MD)
        self.assertTrue(any(p["reported_value"] == 56 for p in points))

    def test_filtered_counts_reported(self):
        _, stats = quiet(ra.extract_data_points, self.MD)
        self.assertGreater(stats["filtered_qualitative"], 0)


class TestBareYearValueFilter(unittest.TestCase):
    def test_bare_year_kv_line_filtered(self):
        points, stats = quiet(ra.extract_data_points, "预测年份：2030\n营收：1500亿元\n")
        values = [p["reported_value"] for p in points]
        self.assertNotIn(2030, values)
        self.assertIn(1500, values)

    def test_year_range_value_with_unit_header_kept(self):
        # 年份过滤只针对时间语境；单位在列头时 1900-2105 区间的真实数值不能误杀
        md = "| 指标 | 金额(亿元) |\n|---|---|\n| 净利润 | 2024 |\n"
        points, _ = quiet(ra.extract_data_points, md)
        self.assertEqual([p["reported_value"] for p in points], [2024.0])


class TestNegativeNumbers(unittest.TestCase):
    def test_negative_table_value_keeps_sign(self):
        md = "| 指标 | 数值 |\n|---|---|\n| 净利润 | -13.5亿 |\n"
        points, _ = quiet(ra.extract_data_points, md)
        self.assertEqual([p["reported_value"] for p in points], [-13.5])

    def test_negative_kv_value_extracted(self):
        points, _ = quiet(ra.extract_data_points, "经营现金流：-25.8亿元\n")
        self.assertEqual([p["reported_value"] for p in points], [-25.8])

    def test_range_values_not_misread_as_negative(self):
        md = "| 指标 | 区间 |\n|---|---|\n| PB | 1-1.5倍 |\n"
        points, _ = quiet(ra.extract_data_points, md)
        for p in points:
            self.assertGreater(p["reported_value"], 0)


class TestCodeFenceExcluded(unittest.TestCase):
    def test_fenced_table_not_extracted(self):
        md = "```\n| 示例指标 | 数值 |\n|---|---|\n| 模板营收 | 999亿 |\n```\n\n真实营收：1500亿元\n"
        points, _ = quiet(ra.extract_data_points, md)
        values = [p["reported_value"] for p in points]
        self.assertNotIn(999, values)
        self.assertIn(1500, values)


if __name__ == "__main__":
    unittest.main()
