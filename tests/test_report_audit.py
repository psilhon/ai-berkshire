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


class TestYearContextNotDataPoint(unittest.TestCase):
    # 缺陷回归: 表格单元格 "2023年约20.5亿美元" 把 2023 误抽成数据点，20.5亿反而丢失
    MD = (
        "| 市场 | 现状 | 预测 | 口径来源 |\n"
        "|---|---|---|---|\n"
        "| 全球数据中心液冷 | 2023年约20.5亿美元 | 2030年约80-232亿美元"
        "（不同机构口径差异大，CAGR 20%+方向一致） | TrendForce/中商/格隆汇转引 |\n"
    )

    def test_year_before_nian_not_extracted(self):
        points, _ = quiet(ra.extract_data_points, self.MD)
        values = [p["reported_value"] for p in points]
        self.assertNotIn(2023, values)
        self.assertNotIn(2030, values)

    def test_unit_value_in_same_cell_extracted(self):
        points, _ = quiet(ra.extract_data_points, self.MD)
        matched = [p for p in points if p["reported_value"] == 20.5]
        self.assertEqual(len(matched), 1, points)
        self.assertIn("亿", matched[0]["unit"])

    def test_year_like_number_with_unit_not_killed(self):
        # "营收2023亿元"：2023 后跟单位不是"年"，必须照抽
        md = "| 指标 | 金额 |\n|---|---|\n| 营收 | 2023亿元 |\n"
        points, _ = quiet(ra.extract_data_points, md)
        self.assertEqual([p["reported_value"] for p in points], [2023.0])


class TestNegativeNumbers(unittest.TestCase):
    def test_negative_table_value_keeps_sign(self):
        md = "| 指标 | 数值 |\n|---|---|\n| 净利润 | -13.5亿 |\n"
        points, _ = quiet(ra.extract_data_points, md)
        self.assertEqual([p["reported_value"] for p in points], [-13.5])

    def test_negative_wan_value_keeps_unit(self):
        # 缺陷回归: "-3016万" 保留了负号但单位"万"丢失，数量级差 1e4
        md = "| 指标 | 数值 | 说明 |\n|---|---|---|\n| 信用减值损失 | -3016万 | 坏账计提增加 |\n"
        points, _ = quiet(ra.extract_data_points, md)
        self.assertEqual(len(points), 1, points)
        self.assertEqual(points[0]["reported_value"], -3016.0)
        self.assertEqual(points[0]["unit"], "万")

    def test_negative_wan_kv_keeps_unit(self):
        # 负数单位捕获与正数同一路径：KV 行同样成立
        points, _ = quiet(ra.extract_data_points, "信用减值损失：-3016万\n")
        self.assertEqual([(p["reported_value"], p["unit"]) for p in points],
                         [(-3016.0, "万")])

    def test_negative_kv_value_extracted(self):
        points, _ = quiet(ra.extract_data_points, "经营现金流：-25.8亿元\n")
        self.assertEqual([p["reported_value"] for p in points], [-25.8])

    def test_range_values_not_misread_as_negative(self):
        md = "| 指标 | 区间 |\n|---|---|\n| PB | 1-1.5倍 |\n"
        points, _ = quiet(ra.extract_data_points, md)
        for p in points:
            self.assertGreater(p["reported_value"], 0)

    def test_unicode_minus_sign_preserved(self):
        # 缺陷回归: U+2212 负号被正则漏掉导致符号静默翻转（−3016 抽成 +3016）
        md = "| 指标 | 数值 |\n|---|---|\n| 减值 | −3016万 |\n"
        points, _ = quiet(ra.extract_data_points, md)
        self.assertEqual([(p["reported_value"], p["unit"]) for p in points],
                         [(-3016.0, "万")])

    def test_fullwidth_minus_sign_preserved(self):
        points, _ = quiet(ra.extract_data_points, "经营现金流：－25.8亿元\n")
        self.assertEqual([p["reported_value"] for p in points], [-25.8])


class TestYearSkipCounted(unittest.TestCase):
    """年份跳过不静默：整格仅年份/KV 年份行必须计入 stats['filtered_year']。"""

    def test_year_only_table_cell_counted(self):
        md = "| 指标 | 数值 |\n|---|---|\n| 成立年份 | 1999年 |\n"
        points, stats = quiet(ra.extract_data_points, md)
        self.assertEqual(points, [])
        self.assertEqual(stats["filtered_year"], 1)

    def test_year_kv_line_counted(self):
        points, stats = quiet(ra.extract_data_points, "成立时间：1999年\n")
        self.assertEqual(points, [])
        self.assertEqual(stats["filtered_year"], 1)

    def test_mixed_cell_not_double_counted(self):
        # 同格既有年份又有真实量：真实量照抽，整格不算"仅年份"
        md = "| 市场 | 现状 |\n|---|---|\n| 液冷 | 2023年约20.5亿美元 |\n"
        points, stats = quiet(ra.extract_data_points, md)
        self.assertEqual([p["reported_value"] for p in points], [20.5])
        self.assertEqual(stats["filtered_year"], 0)


class TestExplanationColumnFilter(unittest.TestCase):
    # 缺陷回归: 说明列（验算方式/来源/口径）里的中间数被误抽为数据点，
    # 如 "工具精确计算（TTM归母4.83亿）" 额外抽出 "PE · 验算方式 = 4.83亿"
    MD = (
        "| 指标 | 数值 | 验算方式 |\n|---|---|---|\n"
        "| PE（TTM） | 195.56倍 | 工具精确计算（TTM归母4.83亿） |\n\n"
        "| 指标 | 数值 | 口径 |\n|---|---|---|\n"
        "| 营收 | 1500亿 | 2024财年合并报表口径 |\n\n"
        "| 指标 | 数值 | 数据来源 |\n|---|---|---|\n"
        "| 毛利率 | 56% | 年报第12页 |\n"
    )

    def test_explanation_columns_not_extracted(self):
        points, _ = quiet(ra.extract_data_points, self.MD)
        values = [p["reported_value"] for p in points]
        self.assertNotIn(4.83, values)
        self.assertNotIn(2024, values)
        self.assertNotIn(12, values)

    def test_metric_columns_survive(self):
        points, _ = quiet(ra.extract_data_points, self.MD)
        values = [p["reported_value"] for p in points]
        self.assertIn(195.56, values)
        self.assertIn(1500, values)
        self.assertIn(56, values)

    def test_koujing_suffix_metric_column_survives(self):
        # 缺陷回归: 「指标名（XX口径）」是本仓库口径标注惯例, 是真实待核验指标列,
        # 子串匹配会把整列数据（如快手审计口径 DAU/GMV）误杀出抽检池
        md = ("| 指标 | 数值（审计口径） |\n|---|---|\n"
              "| 广告收入 | 815亿元 |\n| 电商GMV | 1.6万亿 |\n")
        points, _ = quiet(ra.extract_data_points, md)
        values = [p["reported_value"] for p in points]
        self.assertIn(815, values)
        self.assertIn(1.6, values)

    def test_prefix_explanation_header_still_killed(self):
        # 前缀形态的真说明列（口径说明）仍要跳过
        md = "| 指标 | 数值 | 口径说明 |\n|---|---|---|\n| 营收 | 1500亿 | 2024财年合并口径 |\n"
        points, _ = quiet(ra.extract_data_points, md)
        values = [p["reported_value"] for p in points]
        self.assertIn(1500, values)
        self.assertNotIn(2024, values)


class TestColumnSkipCounted(unittest.TestCase):
    """增速/说明列跳过不静默：数量计入 stats['filtered_column']（失败要大声）。"""

    def test_skipped_columns_counted(self):
        md = ("| 指标 | 数值 | 同比增速 | 数据来源 |\n|---|---|---|---|\n"
              "| 营收 | 1500亿 | 12% | 年报第12页 |\n")
        points, stats = quiet(ra.extract_data_points, md)
        self.assertEqual([p["reported_value"] for p in points], [1500.0])
        self.assertEqual(stats["filtered_column"], 2)


class TestCodeFenceExcluded(unittest.TestCase):
    def test_fenced_table_not_extracted(self):
        md = "```\n| 示例指标 | 数值 |\n|---|---|\n| 模板营收 | 999亿 |\n```\n\n真实营收：1500亿元\n"
        points, _ = quiet(ra.extract_data_points, md)
        values = [p["reported_value"] for p in points]
        self.assertNotIn(999, values)
        self.assertIn(1500, values)


if __name__ == "__main__":
    unittest.main()
