#!/usr/bin/env python3
"""Unit tests for tools/financial_rigor.py — exact Decimal arithmetic guarantees."""

import contextlib
import io
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


if __name__ == "__main__":
    unittest.main()
