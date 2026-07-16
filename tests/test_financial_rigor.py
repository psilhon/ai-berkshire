#!/usr/bin/env python3
"""Unit tests for tools/financial_rigor.py — exact Decimal arithmetic guarantees."""

import contextlib
import io
import json
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import financial_rigor as fr  # noqa: E402


def quiet(fn, *args, **kwargs):
    """Run fn with stdout suppressed, return its return value."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


class TestExactCalc(unittest.TestCase):
    def test_float_trap_0_1_plus_0_2(self):
        # 修复前: eval 浮点得 0.30000000000000004
        result = quiet(fr.exact_calc, "0.1 + 0.2")
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal("0.3"))

    def test_scientific_notation(self):
        result = quiet(fr.exact_calc, "510 * 9.11e9")
        self.assertEqual(result, Decimal("4646100000000"))

    def test_division_exact(self):
        result = quiet(fr.exact_calc, "1 / 8")
        self.assertEqual(result, Decimal("0.125"))

    def test_parentheses(self):
        result = quiet(fr.exact_calc, "(2 + 3) * 4.5")
        self.assertEqual(result, Decimal("22.5"))

    def test_negative_and_precision(self):
        result = quiet(fr.exact_calc, "-1.1 + 3.3")
        self.assertEqual(result, Decimal("2.2"))

    def test_rejects_disallowed_characters(self):
        self.assertIsNone(quiet(fr.exact_calc, "__import__('os')"))
        self.assertIsNone(quiet(fr.exact_calc, "a + 1"))

    def test_malformed_expression_returns_none(self):
        self.assertIsNone(quiet(fr.exact_calc, "(1 + 2"))
        self.assertIsNone(quiet(fr.exact_calc, "1 */ 2"))


class TestThreeScenarioValidation(unittest.TestCase):
    BASE = dict(current_price=100, current_eps=5, shares_billion=10,
                pe_optimistic=20, pe_neutral=15, pe_pessimistic=10)

    def test_growth_as_percent_number_rejected(self):
        # 人类惯性输入 20 (想表达 20%) 必须被拒绝并提示小数单位
        with self.assertRaises(ValueError) as ctx:
            quiet(fr.three_scenario_valuation,
                  growth_optimistic=20, growth_neutral=0.08,
                  growth_pessimistic=0.0, **self.BASE)
        self.assertIn("0.20", str(ctx.exception))

    def test_growth_below_negative_one_rejected(self):
        with self.assertRaises(ValueError):
            quiet(fr.three_scenario_valuation,
                  growth_optimistic=0.1, growth_neutral=0.0,
                  growth_pessimistic=-1.5, **self.BASE)

    def test_growth_boundaries_accepted(self):
        result = quiet(fr.three_scenario_valuation,
                       growth_optimistic=2.0, growth_neutral=0.0,
                       growth_pessimistic=-0.99, **self.BASE)
        self.assertEqual(len(result), 3)


class TestThreeScenarioComputation(unittest.TestCase):
    def test_exact_numbers_and_implied_mcap(self):
        # EPS 5 × 1.1^3 = 6.655; 目标价 6.655×20 = 133.1; 隐含市值 133.1×10亿股 = 1331亿
        result = quiet(fr.three_scenario_valuation,
                       current_price=100, current_eps=5, shares_billion=10,
                       growth_optimistic=0.10, growth_neutral=0.05,
                       growth_pessimistic=0.0,
                       pe_optimistic=20, pe_neutral=15, pe_pessimistic=10,
                       years=3)
        bull = result[0]
        self.assertEqual(bull["future_eps"], Decimal("6.655"))
        self.assertEqual(bull["target_price"], Decimal("133.1"))
        self.assertEqual(bull["implied_mcap"], Decimal("1331"))
        bear = result[2]
        self.assertEqual(bear["future_eps"], Decimal("5"))
        self.assertEqual(bear["target_price"], Decimal("50"))
        self.assertEqual(bear["implied_mcap"], Decimal("500"))

    def test_shares_actually_used(self):
        a = quiet(fr.three_scenario_valuation,
                  current_price=100, current_eps=5, shares_billion=10,
                  growth_optimistic=0.1, growth_neutral=0.05,
                  growth_pessimistic=0.0,
                  pe_optimistic=20, pe_neutral=15, pe_pessimistic=10)
        b = quiet(fr.three_scenario_valuation,
                  current_price=100, current_eps=5, shares_billion=20,
                  growth_optimistic=0.1, growth_neutral=0.05,
                  growth_pessimistic=0.0,
                  pe_optimistic=20, pe_neutral=15, pe_pessimistic=10)
        self.assertEqual(b[0]["implied_mcap"], a[0]["implied_mcap"] * 2)


class TestExistingBehaviorStillWorks(unittest.TestCase):
    def test_verify_market_cap_pass(self):
        self.assertTrue(quiet(fr.verify_market_cap, 510, 9.11e9, 4.65e12, "HKD"))

    def test_verify_market_cap_unit_confusion_fails(self):
        # 港币/人民币单位混淆量级偏差必须报 False
        self.assertFalse(quiet(fr.verify_market_cap, 510, 9.11e9, 4.2e12 * 0.9, "HKD"))

    def test_verify_valuation_pe(self):
        results = quiet(fr.verify_valuation, 100, eps=5)
        self.assertAlmostEqual(results["PE"], 20.0)

    def test_decimal_cli_arg_parser(self):
        # CLI 数值入口应以 Decimal 解析，绕过 float
        self.assertEqual(fr.decimal_arg("0.1"), Decimal("0.1"))
        self.assertEqual(fr.decimal_arg("9.11e9"), Decimal("9.11e9"))
        with self.assertRaises(Exception):
            fr.decimal_arg("abc")

    def test_decimal_arg_rejects_non_finite(self):
        # nan/inf 是合法 Decimal，但作为金融输入必须拒绝，否则静默产出 NaN 结果
        for bad in ("nan", "inf", "-inf", "Infinity"):
            with self.assertRaises(Exception, msg=bad):
                fr.decimal_arg(bad)


class TestThreeScenarioRobustness(unittest.TestCase):
    BASE = dict(growth_optimistic=0.1, growth_neutral=0.05, growth_pessimistic=0.0,
                pe_optimistic=20, pe_neutral=15, pe_pessimistic=10)

    def test_zero_price_rejected_cleanly(self):
        # 修复前: ZeroDivisionError 裸 traceback
        with self.assertRaises(ValueError):
            quiet(fr.three_scenario_valuation, current_price=0, current_eps=5,
                  shares_billion=10, **self.BASE)

    def test_negative_price_rejected(self):
        with self.assertRaises(ValueError):
            quiet(fr.three_scenario_valuation, current_price=-100, current_eps=5,
                  shares_billion=10, **self.BASE)

    def test_nan_growth_rejected_as_value_error(self):
        # 修复前: Decimal NaN 比较抛 InvalidOperation 裸 traceback
        with self.assertRaises(ValueError):
            quiet(fr.three_scenario_valuation, current_price=100, current_eps=5,
                  shares_billion=10, growth_optimistic=Decimal("nan"),
                  growth_neutral=0.05, growth_pessimistic=0.0,
                  pe_optimistic=20, pe_neutral=15, pe_pessimistic=10)

    def test_nan_eps_rejected(self):
        with self.assertRaises(ValueError):
            quiet(fr.three_scenario_valuation, current_price=100,
                  current_eps=Decimal("nan"), shares_billion=10, **self.BASE)

    def test_non_positive_years_rejected(self):
        # 修复前: years=-3 打印"-3年"却静默按 0 年复利
        for bad_years in (0, -3):
            with self.assertRaises(ValueError, msg=bad_years):
                quiet(fr.three_scenario_valuation, current_price=100, current_eps=5,
                      shares_billion=10, years=bad_years, **self.BASE)


class TestVerifyMarketCapFailClosed(unittest.TestCase):
    def test_zero_reported_rejected(self):
        # 修复前: reported=0 时偏差被静默置 0, 错误显示"✅ 验证通过"
        with self.assertRaises(ValueError):
            quiet(fr.verify_market_cap, 1, 1000, 0)

    def test_zero_shares_rejected(self):
        with self.assertRaises(ValueError):
            quiet(fr.verify_market_cap, 510, 0, 4.65e12)

    def test_negative_price_rejected(self):
        with self.assertRaises(ValueError):
            quiet(fr.verify_market_cap, -510, 9.11e9, 4.65e12)

    def test_deviation_exact_zero_on_matching_inputs(self):
        # 510 × 9.11e9 恰等于 4646100000000, Decimal 偏差应精确为 0
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = fr.verify_market_cap(510, 9.11e9, 4646100000000)
        self.assertTrue(ok)
        self.assertIn("偏差仅 0.00%", buf.getvalue())


class TestVerifyValuationFailClosed(unittest.TestCase):
    def test_zero_price_rejected_cleanly(self):
        # 修复前: price=0 抛 decimal.DivisionByZero 裸 traceback
        with self.assertRaises(ValueError):
            quiet(fr.verify_valuation, 0, eps=5)

    def test_negative_price_rejected(self):
        with self.assertRaises(ValueError):
            quiet(fr.verify_valuation, -100, eps=5)

    def test_negative_eps_skips_pe_without_error(self):
        # 亏损公司: 不抛异常, 但不产出 PE
        results = quiet(fr.verify_valuation, 100, eps=-5)
        self.assertNotIn("PE", results)


class TestCrossValidateFailClosed(unittest.TestCase):
    def test_empty_sources_rejected(self):
        # 修复前: 空 dict → IndexError 裸 traceback
        with self.assertRaises(ValueError):
            quiet(fr.cross_validate, "revenue", {})

    def test_single_source_rejected(self):
        # 项目双源规则: 关键数据至少 2 个独立来源交叉验证
        with self.assertRaises(ValueError):
            quiet(fr.cross_validate, "revenue", {"年报": 7518})

    def test_huge_decimal_no_float_overflow(self):
        # 修复前: 1e999 转 float 变 inf, 偏差 nan 仍返回成功
        big = Decimal("1e999")
        result = quiet(fr.cross_validate, "market_cap",
                       {"a": big, "b": big, "c": big})
        self.assertEqual(result["consensus"], big)
        self.assertTrue(result["all_consistent"])

    def test_median_exact_decimal_no_float_drift(self):
        # 中位数全程 Decimal: 0.1/0.2/0.3 的中位数应恰为 Decimal('0.2')
        result = quiet(fr.cross_validate, "eps",
                       {"a": Decimal("0.1"), "b": Decimal("0.3"),
                        "c": Decimal("0.2")})
        self.assertEqual(result["consensus"], Decimal("0.2"))

    def test_zero_median_rejected(self):
        # 修复前: 中位数为 0 时偏差被静默置 0
        with self.assertRaises(ValueError):
            quiet(fr.cross_validate, "x", {"a": 0, "b": 0})


class _CLICase(unittest.TestCase):
    TOOL = Path(__file__).resolve().parents[1] / "tools" / "financial_rigor.py"

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(self.TOOL), *args],
                              capture_output=True, text=True)


class TestCLIFailClosed(_CLICase):
    """风险清单原始复现命令: exit code 2 且无裸 traceback。"""

    def assert_clean_param_error(self, proc):
        self.assertEqual(proc.returncode, 2, msg=proc.stderr)
        self.assertNotIn("Traceback", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    def test_market_cap_zero_reported(self):
        self.assert_clean_param_error(self.run_cli(
            "verify-market-cap", "--price", "1", "--shares", "1000",
            "--reported", "0"))

    def test_valuation_zero_price(self):
        self.assert_clean_param_error(self.run_cli(
            "verify-valuation", "--price", "0", "--eps", "5"))

    def test_cross_validate_empty_dict(self):
        self.assert_clean_param_error(self.run_cli(
            "cross-validate", "--field", "revenue", "--values", "{}"))

    def test_cross_validate_single_source(self):
        self.assert_clean_param_error(self.run_cli(
            "cross-validate", "--field", "revenue",
            "--values", '{"年报": 7518}'))

    def test_cross_validate_array_rejected(self):
        # 非 dict JSON（数组）不允许裸 AttributeError traceback
        self.assert_clean_param_error(self.run_cli(
            "cross-validate", "--field", "revenue", "--values", "[100, 200]"))

    def test_cross_validate_non_numeric_value_rejected(self):
        # 字符串/bool 值不允许裸 InvalidOperation traceback；bool 是 int 子类需显式排除
        self.assert_clean_param_error(self.run_cli(
            "cross-validate", "--field", "revenue",
            "--values", '{"a": "abc", "b": 100}'))
        self.assert_clean_param_error(self.run_cli(
            "cross-validate", "--field", "revenue",
            "--values", '{"a": true, "b": 100}'))


def _benford_values(scale=1):
    """构造精确符合 Benford 分布的 100 个值 (首位数字 d 出现 round(100*log10(1+1/d)) 次)。"""
    counts = {1: 30, 2: 18, 3: 12, 4: 10, 5: 8, 6: 7, 7: 6, 8: 5, 9: 4}
    return [d * scale for d, c in counts.items() for _ in range(c)]


class TestBenfordRobustness(unittest.TestCase):
    def test_huge_decimal_no_float_overflow(self):
        # 修复前: 首位数字抽取过 float, 1e999 溢出 OverflowError 裸 traceback
        result = quiet(fr.benford_check, [Decimal("1e999")] * 60)
        self.assertIsNotNone(result)
        self.assertFalse(result["is_conforming"])

    def test_fractional_leading_digit_extraction(self):
        # 首位有效数字须跳过前导零: 0.003 的首位是 3, 缩放不改变分布判定
        result = quiet(fr.benford_check, [v / 1000 for v in _benford_values()])
        self.assertTrue(result["is_conforming"])

    def test_insufficient_sample_returns_none(self):
        self.assertIsNone(quiet(fr.benford_check, [1, 2, 3]))


class TestCLIExitCodeSemantics(_CLICase):
    """统一退出码语义: 0 验证通过 / 1 业务不通过或计算失败 / 2 参数或证据不足。"""

    def test_market_cap_pass_exits_zero(self):
        proc = self.run_cli("verify-market-cap", "--price", "510",
                            "--shares", "9.11e9", "--reported", "4646100000000")
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)

    def test_market_cap_50pct_deviation_exits_one(self):
        # 修复前: 输出❌警告但退出码 0, 脚本会把业务失败误认为成功
        proc = self.run_cli("verify-market-cap", "--price", "1",
                            "--shares", "100", "--reported", "200")
        self.assertEqual(proc.returncode, 1, msg=proc.stdout)

    def test_cross_validate_consistent_exits_zero(self):
        proc = self.run_cli("cross-validate", "--field", "revenue",
                            "--values", '{"a": 100, "b": 100}')
        self.assertEqual(proc.returncode, 0, msg=proc.stdout)

    def test_cross_validate_inconsistent_exits_one(self):
        # 修复前: 两来源 100/200 输出数据不一致但退出码 0
        proc = self.run_cli("cross-validate", "--field", "revenue",
                            "--values", '{"a": 100, "b": 200}')
        self.assertEqual(proc.returncode, 1, msg=proc.stdout)

    def test_calc_ok_exits_zero(self):
        proc = self.run_cli("calc", "--expr", "1 + 2")
        self.assertEqual(proc.returncode, 0)

    def test_calc_division_by_zero_exits_one(self):
        # 修复前: 输出"计算错误"但退出码 0
        proc = self.run_cli("calc", "--expr", "1 / 0")
        self.assertEqual(proc.returncode, 1, msg=proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    def test_no_subcommand_exits_two_with_usage(self):
        # 修复前: 裸调用打印 help 到 stdout 且退出码 0 — 零操作却报"成功"
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 2, msg=proc.stdout)
        self.assertIn("usage", proc.stderr)


class TestCLIBenfordInput(_CLICase):
    """benford 与其他子命令同口径: 输入校验干净报错 + 三态退出码。"""

    def assert_clean_param_error(self, proc):
        self.assertEqual(proc.returncode, 2, msg=proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    def test_json_object_rejected(self):
        # 修复前: 传 JSON 对象裸 ValueError traceback
        self.assert_clean_param_error(self.run_cli(
            "benford", "--values", '{"a": 1}'))

    def test_non_numeric_element_rejected(self):
        self.assert_clean_param_error(self.run_cli(
            "benford", "--values", '[100, "abc", true]'))

    def test_invalid_json_rejected(self):
        self.assert_clean_param_error(self.run_cli(
            "benford", "--values", "not json"))

    def test_huge_number_no_overflow(self):
        # 修复前: 1e999 过 float 溢出 OverflowError 裸 traceback
        proc = self.run_cli("benford", "--values", "[1e999]")
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(proc.returncode, 2)  # 样本不足 → 证据不足

    def test_insufficient_sample_exits_two(self):
        # 修复前: 提示"不可靠"但退出码 0
        proc = self.run_cli("benford", "--values", "[1, 2, 3]")
        self.assertEqual(proc.returncode, 2, msg=proc.stdout)

    def test_conforming_exits_zero(self):
        proc = self.run_cli("benford", "--values", json.dumps(_benford_values()))
        self.assertEqual(proc.returncode, 0, msg=proc.stdout)

    def test_nonconforming_exits_one(self):
        proc = self.run_cli("benford", "--values", json.dumps([9] * 60))
        self.assertEqual(proc.returncode, 1, msg=proc.stdout)


if __name__ == "__main__":
    unittest.main()
