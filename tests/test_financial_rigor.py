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


class TestDefaultCLIGolden(_CLICase):
    """A2 兼容基线: 默认(非 --json) CLI 的 stdout 与退出码逐字节冻结,
    保护后续 --json 改动不污染既有人类输出路径 (§10.2.1 step2)。"""

    GOLDEN = Path(__file__).resolve().parent / "golden" / "financial_rigor"

    CASES = {
        "verify-market-cap": ["verify-market-cap", "--price", "41.20", "--shares",
                              "25219845601", "--reported", "1039057636761.20",
                              "--currency", "CNY"],
        "verify-valuation": ["verify-valuation", "--price", "100", "--eps", "5",
                             "--bvps", "40", "--fcf-per-share", "8",
                             "--dividend", "2", "--revenue-per-share", "50"],
        "cross-validate": ["cross-validate", "--field", "revenue", "--values",
                           '{"公司财报":108300000000,"第二来源":107900000000}',
                           "--unit", "CNY"],
        "benford": ["benford", "--values", json.dumps(_benford_values())],
        "calc": ["calc", "--expr", "510 * 9.11e9"],
        "three-scenario": ["three-scenario", "--price", "100", "--eps", "5",
                           "--shares", "10", "--growth", "0.10", "0.05", "0.0",
                           "--pe", "20", "15", "10", "--years", "3"],
    }

    def test_default_stdout_and_exit_byte_stable(self):
        exit_codes = json.loads(
            (self.GOLDEN / "exit_codes.json").read_text(encoding="utf-8"))
        for name, args in self.CASES.items():
            with self.subTest(command=name):
                proc = self.run_cli(*args)
                golden = (self.GOLDEN / f"{name}.stdout.txt").read_text(
                    encoding="utf-8")
                self.assertEqual(proc.stdout, golden,
                                 msg=f"{name} 默认 stdout 漂移")
                self.assertEqual(proc.returncode, exit_codes[name],
                                 msg=f"{name} 退出码漂移")
                self.assertEqual(proc.stderr, "", msg=f"{name} 写了 stderr")


class TestJSONReplayProtocol(_CLICase):
    """B: --json 语义重放协议 (v1.4 §10.2)。envelope + 六 operation result schema;
    字段名逐项相等 schema; Decimal 走十进制字符串; 即使非 0 退出也只有一个合法 JSON。"""

    SCHEMA = json.loads(
        (Path(__file__).resolve().parents[1] / "tools"
         / "financial_rigor_result_schema.json").read_text(encoding="utf-8"))

    def run_json(self, *args):
        proc = self.run_cli(*args, "--json")
        try:
            env = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"--json 未产出合法 JSON (功能缺失?): rc={proc.returncode} "
                      f"stdout={proc.stdout[:200]!r} stderr={proc.stderr[:200]!r}")
        return env, proc.returncode

    def assert_envelope(self, env, operation, proc_rc):
        env_s = self.SCHEMA["envelope"]
        for f in env_s["required"]:
            self.assertIn(f, env, msg=f"{operation} envelope 缺字段 {f}")
        self.assertEqual(env["operation"], operation)
        self.assertEqual(env["schema_version"], self.SCHEMA["schema_version"])
        self.assertIn(env["outcome"], env_s["outcome_enum"])
        self.assertIn(env["exit_code"], env_s["exit_codes"])
        self.assertEqual(env["exit_code"], proc_rc, msg="JSON exit_code 应与进程退出码一致")
        self.assertIsInstance(env["warnings"], list)
        self.assertIsInstance(env["errors"], list)
        # is_pass 规则
        if env["outcome"] in ("PASS", "FAIL"):
            self.assertIsInstance(env["is_pass"], bool)
        else:
            self.assertIsNone(env["is_pass"])
        # 字段名逐项相等 schema (B5 防漂移)
        op_s = self.SCHEMA["operations"][operation]
        self.assertEqual(set(env["result"].keys()), set(op_s["result_required"]),
                         msg=f"{operation} result 字段名与 schema 不符")
        # decimal 字段为十进制字符串 (非 null 时)
        for f in op_s.get("decimal_string_fields", []):
            if env["result"][f] is not None:
                self.assertIsInstance(env["result"][f], str,
                                      msg=f"{operation}.{f} 应为十进制字符串")

    # ---- verify-market-cap ----
    def test_market_cap_pass(self):
        # 41.20 × 25219845601 = 1039057638761.2 (设计 §10.2 示例误写为 ...636761.20)
        env, rc = self.run_json("verify-market-cap", "--price", "41.20", "--shares",
                                "25219845601", "--reported", "1039057638761.2",
                                "--currency", "CNY")
        self.assert_envelope(env, "verify-market-cap", rc)
        self.assertEqual(env["outcome"], "PASS")
        self.assertTrue(env["is_pass"])
        self.assertEqual(rc, 0)
        r = env["result"]
        self.assertEqual(Decimal(r["calculated_market_cap"]), Decimal("1039057638761.2"))
        self.assertEqual(Decimal(r["reported_market_cap"]), Decimal("1039057638761.2"))
        self.assertEqual(Decimal(r["deviation_pct"]), Decimal("0"))
        self.assertEqual(r["band"], "PASS")

    def test_market_cap_over_5pct_fails(self):
        env, rc = self.run_json("verify-market-cap", "--price", "1",
                                "--shares", "100", "--reported", "200")
        self.assert_envelope(env, "verify-market-cap", rc)
        self.assertEqual(env["outcome"], "FAIL")
        self.assertFalse(env["is_pass"])
        self.assertEqual(rc, 1)
        self.assertEqual(env["result"]["band"], "FAIL")

    def test_market_cap_band_warn(self):
        # 偏差 2% (1-5%) -> band WARN, 仍 is_pass true
        env, rc = self.run_json("verify-market-cap", "--price", "102",
                                "--shares", "100", "--reported", "10000")
        self.assertEqual(env["result"]["band"], "WARN")
        self.assertTrue(env["is_pass"])
        self.assertEqual(rc, 0)

    # ---- verify-valuation ----
    def test_valuation_metrics(self):
        env, rc = self.run_json("verify-valuation", "--price", "100", "--eps", "5",
                                "--bvps", "40", "--dividend", "2")
        self.assert_envelope(env, "verify-valuation", rc)
        self.assertEqual(env["outcome"], "PASS")
        self.assertEqual(rc, 0)
        m = env["result"]["metrics"]
        self.assertEqual(Decimal(m["pe"]), Decimal("20"))
        self.assertEqual(Decimal(m["earnings_yield_pct"]), Decimal("5"))
        self.assertEqual(Decimal(m["pb"]), Decimal("2.5"))
        self.assertEqual(Decimal(m["roe_pct"]), Decimal("12.5"))
        self.assertEqual(Decimal(m["dividend_yield_pct"]), Decimal("2"))
        self.assertEqual(set(m.keys()) - set(self.SCHEMA["operations"]
                         ["verify-valuation"]["metrics_allowed"]), set())
        self.assertEqual(env["result"]["skipped"], [])

    def test_valuation_no_metric_insufficient(self):
        env, rc = self.run_json("verify-valuation", "--price", "100")
        self.assert_envelope(env, "verify-valuation", rc)
        self.assertEqual(env["outcome"], "INSUFFICIENT")
        self.assertIsNone(env["is_pass"])
        self.assertEqual(rc, 2)
        self.assertEqual(env["result"]["metrics"], {})
        self.assertEqual(env["result"]["skipped"], [])

    def test_valuation_negative_eps_skipped(self):
        env, rc = self.run_json("verify-valuation", "--price", "100", "--eps", "-5")
        self.assertEqual(env["outcome"], "INSUFFICIENT")
        self.assertEqual(env["result"]["metrics"], {})
        skipped = {s["metric"] for s in env["result"]["skipped"]}
        self.assertIn("pe", skipped)

    # ---- cross-validate ----
    def test_cross_validate_consistent(self):
        env, rc = self.run_json("cross-validate", "--field", "revenue",
                                "--values", '{"a": 100, "b": 100}')
        self.assert_envelope(env, "cross-validate", rc)
        self.assertEqual(env["outcome"], "PASS")
        self.assertTrue(env["result"]["all_consistent"])
        self.assertEqual(rc, 0)
        self.assertEqual(Decimal(env["result"]["consensus"]), Decimal("100"))
        self.assertEqual(Decimal(env["result"]["tolerance_pct"]), Decimal("2.0"))
        srcs = env["result"]["sources"]
        self.assertEqual({s["source"] for s in srcs}, {"a", "b"})
        for s in srcs:
            self.assertEqual(set(s.keys()), set(self.SCHEMA["operations"]
                             ["cross-validate"]["source_item_fields"]))
            self.assertIsInstance(s["value"], str)
            self.assertIsInstance(s["deviation_pct"], str)
            self.assertIsInstance(s["within_tolerance"], bool)

    def test_cross_validate_inconsistent(self):
        env, rc = self.run_json("cross-validate", "--field", "revenue",
                                "--values", '{"a": 100, "b": 200}')
        self.assert_envelope(env, "cross-validate", rc)
        self.assertEqual(env["outcome"], "FAIL")
        self.assertFalse(env["result"]["all_consistent"])
        self.assertEqual(rc, 1)

    # ---- benford ----
    def test_benford_conforming(self):
        env, rc = self.run_json("benford", "--values", json.dumps(_benford_values()))
        self.assert_envelope(env, "benford", rc)
        self.assertEqual(env["outcome"], "PASS")
        self.assertTrue(env["result"]["is_conforming"])
        self.assertEqual(rc, 0)
        self.assertEqual(env["result"]["sample_size"], 100)
        self.assertIn(env["result"]["conformity"], ["CLOSE", "ACCEPTABLE", "MARGINAL"])
        mad = env["result"]["mad"]
        self.assertEqual(Decimal(mad), Decimal(mad).quantize(Decimal("0.000001")),
                         msg="mad 必须量化到 6 位")

    def test_benford_nonconforming(self):
        env, rc = self.run_json("benford", "--values", json.dumps([9] * 60))
        self.assert_envelope(env, "benford", rc)
        self.assertEqual(env["outcome"], "FAIL")
        self.assertFalse(env["result"]["is_conforming"])
        self.assertEqual(env["result"]["conformity"], "NONCONFORMING")
        self.assertEqual(rc, 1)

    def test_benford_insufficient(self):
        env, rc = self.run_json("benford", "--values", "[1, 2, 3]")
        self.assert_envelope(env, "benford", rc)
        self.assertEqual(env["outcome"], "INSUFFICIENT")
        self.assertIsNone(env["is_pass"])
        self.assertEqual(rc, 2)
        self.assertEqual(env["result"]["sample_size"], 3)
        self.assertIsNone(env["result"]["mad"])
        self.assertIsNone(env["result"]["chi_square"])
        self.assertIsNone(env["result"]["is_conforming"])
        self.assertEqual(env["result"]["conformity"], "INSUFFICIENT")

    # ---- calc ----
    def test_calc_ok(self):
        env, rc = self.run_json("calc", "--expr", "510 * 9.11e9")
        self.assert_envelope(env, "calc", rc)
        self.assertEqual(env["outcome"], "PASS")
        self.assertEqual(rc, 0)
        self.assertEqual(env["result"]["expression"], "510 * 9.11e9")
        self.assertEqual(Decimal(env["result"]["value"]), Decimal("4646100000000"))

    def test_calc_divzero_error(self):
        env, rc = self.run_json("calc", "--expr", "1 / 0")
        self.assert_envelope(env, "calc", rc)
        self.assertEqual(env["outcome"], "ERROR")
        self.assertIsNone(env["is_pass"])
        self.assertEqual(rc, 1)
        self.assertIsNone(env["result"]["value"])

    # ---- three-scenario ----
    def test_three_scenario(self):
        env, rc = self.run_json("three-scenario", "--price", "100", "--eps", "5",
                                "--shares", "10", "--growth", "0.10", "0.05", "0.0",
                                "--pe", "20", "15", "10", "--years", "3")
        self.assert_envelope(env, "three-scenario", rc)
        self.assertEqual(env["outcome"], "PASS")
        self.assertEqual(rc, 0)
        self.assertEqual(env["result"]["years"], 3)
        scen = env["result"]["scenarios"]
        self.assertEqual([s["id"] for s in scen], ["bull", "base", "bear"])
        bull = scen[0]
        self.assertEqual(set(bull.keys()), set(self.SCHEMA["operations"]
                         ["three-scenario"]["scenario_item_fields"]))
        self.assertEqual(Decimal(bull["future_eps"]), Decimal("6.655"))
        self.assertEqual(Decimal(bull["target_price"]), Decimal("133.1"))
        self.assertEqual(Decimal(bull["implied_mcap"]), Decimal("1331"))
        for f in self.SCHEMA["operations"]["three-scenario"]["scenario_decimal_string_fields"]:
            self.assertIsInstance(bull[f], str, msg=f"scenario.{f} 应为十进制字符串")

    # ---- 跨环境确定性 (B6) ----
    def test_decimal_ops_semantics_stable_across_locale(self):
        # ASCII locale 下 --json 语义字段必须一致 (gate 不比 stdout 字节)
        env_utf8 = json.loads(subprocess.run(
            [sys.executable, str(self.TOOL), "verify-market-cap", "--price", "41.20",
             "--shares", "25219845601", "--reported", "1039057636761.20", "--json"],
            capture_output=True, text=True,
            env={"PYTHONUTF8": "1", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"}).stdout)
        env_ascii = json.loads(subprocess.run(
            [sys.executable, str(self.TOOL), "verify-market-cap", "--price", "41.20",
             "--shares", "25219845601", "--reported", "1039057636761.20", "--json"],
            capture_output=True, text=True,
            env={"PYTHONUTF8": "0", "LC_ALL": "C", "PATH": "/usr/bin:/bin"}).stdout)
        self.assertEqual(env_utf8["result"], env_ascii["result"])
        self.assertEqual(env_utf8["outcome"], env_ascii["outcome"])


class TestJSONCrossCheck(_CLICase):
    """交叉核对: --json 构造器与旧 public 函数在重叠字段上数值一致,
    机械锁死"单一真值", 防两条计算路径漂移 (design 的构造器等价性)。"""

    def test_market_cap_is_pass_matches_public_bool(self):
        for price, shares, reported in [("41.20", "25219845601", "1039057638761.2"),
                                        ("1", "100", "200")]:
            env = fr._json_market_cap(Decimal(price), Decimal(shares), Decimal(reported))
            pub = quiet(fr.verify_market_cap, Decimal(price), Decimal(shares),
                        Decimal(reported))
            self.assertEqual(env["is_pass"], pub, msg=f"{price}/{shares}/{reported}")

    def test_valuation_metrics_match_public(self):
        kw = dict(eps=Decimal("5"), bvps=Decimal("40"), fcf_per_share=Decimal("8"),
                  dividend=Decimal("2"), revenue_per_share=Decimal("50"))
        env = fr._json_valuation(Decimal("100"), **kw)
        pub = quiet(fr.verify_valuation, Decimal("100"), **kw)
        key_map = {"pe": "PE", "pb": "PB", "roe_pct": "ROE", "p_fcf": "P_FCF",
                   "fcf_yield_pct": "FCF_Yield",
                   "dividend_yield_pct": "Dividend_Yield", "ps": "PS"}
        for jk, pk in key_map.items():
            self.assertAlmostEqual(float(Decimal(env["result"]["metrics"][jk])),
                                   pub[pk], places=6, msg=f"{jk} 漂移")

    def test_cross_validate_matches_public(self):
        sv = {"a": Decimal("100"), "b": Decimal("101"), "c": Decimal("100")}
        env = fr._json_cross_validate("x", sv)
        pub = quiet(fr.cross_validate, "x", sv)
        self.assertEqual(Decimal(env["result"]["consensus"]), pub["consensus"])
        self.assertEqual(env["result"]["all_consistent"], pub["all_consistent"])

    def test_benford_matches_public(self):
        vals = _benford_values()
        env = fr._json_benford(vals)
        pub = quiet(fr.benford_check, vals)
        self.assertEqual(env["result"]["is_conforming"], pub["is_conforming"])
        self.assertEqual(Decimal(env["result"]["mad"]),
                         Decimal(str(pub["mad"])).quantize(Decimal("0.000001")))

    def test_calc_matches_public(self):
        env = fr._json_calc("510 * 9.11e9")
        self.assertEqual(Decimal(env["result"]["value"]),
                         quiet(fr.exact_calc, "510 * 9.11e9"))

    def test_three_scenario_matches_public(self):
        env = fr._json_three_scenario(
            Decimal("100"), Decimal("5"), Decimal("10"),
            [Decimal("0.10"), Decimal("0.05"), Decimal("0.0")],
            [Decimal("20"), Decimal("15"), Decimal("10")], years=3)
        pub = quiet(fr.three_scenario_valuation, Decimal("100"), Decimal("5"),
                    Decimal("10"), Decimal("0.10"), Decimal("0.05"), Decimal("0.0"),
                    Decimal("20"), Decimal("15"), Decimal("10"), 3, "")
        for jrow, prow in zip(env["result"]["scenarios"], pub):
            self.assertEqual(Decimal(jrow["future_eps"]), prow["future_eps"])
            self.assertEqual(Decimal(jrow["target_price"]), prow["target_price"])
            self.assertEqual(Decimal(jrow["implied_mcap"]), prow["implied_mcap"])

    def test_json_param_error_emits_one_error_json(self):
        # 参数错误也必须只输出一个合法 JSON (outcome=ERROR/exit 2), 不裸 traceback
        proc = self.run_cli("verify-market-cap", "--price", "1", "--shares", "0",
                            "--reported", "100", "--json")
        env = json.loads(proc.stdout)
        self.assertEqual(env["outcome"], "ERROR")
        self.assertIsNone(env["is_pass"])
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(env["exit_code"], 2)
        self.assertTrue(env["errors"])
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
