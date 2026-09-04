#!/usr/bin/env python3
"""Unit tests for tools/equity_dcf.py — 估值标定引擎的黄金值与域检查。

背景：equity_dcf 被 investment-research 的 self-check 链依赖（「报告文本标签
须与脚本输出一致」），却长期零测试——标定逻辑错了会静默污染所有估值结论。
本套测试把 --demo 自检提升为正式用例：黄金值断言 + 域检查 + 预注册标定阈值。

黄金值来源：2026-08-30 实跑 python3 tools/equity_dcf.py --demo，关键值均经
手工复算复核（PVGO 6/0.09、base 情景 PV 逐项求和、加权 0.25/0.5/0.25）。

已修缺陷（2026-08-30）：run() 此前把原始 scenarios（无 per_share 字段）传给
run_position，触发 v=price 回退 → expected_value 恒为 0、asymmetry_ratio 恒为 None。
现 run() 改用 run_scenarios 的 per_share 明细；本文件新增回归守卫
test_run_position_uses_computed_per_share，EV 一旦回落到 0 即红。
"""

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import equity_dcf as ed  # noqa: E402


DEMO = {
    "price": 100.0, "shares": 1.0, "net_debt": 0.0,
    "wacc": 0.09, "terminal_g": 0.03,
    "scenarios": [
        {"name": "bear", "fcf": [8, 8.5, 9], "prob": 0.25},
        {"name": "base", "fcf": [10, 11, 12], "prob": 0.5},
        {"name": "bull", "fcf": [12, 14, 16], "prob": 0.25},
    ],
    "reverse": {"interim_fcf": [10, 11, 12], "steady_margins": [0.2, 0.25], "base_revenue": 100.0},
    "pvgo": {"earnings_ps": 6.0, "r": 0.09},
    "epv": {"normalized_earnings": 9.0, "coc": 0.09, "asset_series": [[2023, 9, 90]]},
    "eva": {"invested_capital": 100.0, "nopat": 15.0, "fade_years": 10},
    "montecarlo": {"base_revenue": 100.0, "years": 5, "growth_mean": 0.08,
                   "growth_std": 0.03, "margin_low": 0.18, "margin_mode": 0.22,
                   "margin_high": 0.26, "wacc_low": 0.08, "wacc_high": 0.10},
    "range_low": 85.0, "range_high": 120.0,
}


class TestCalibrate(unittest.TestCase):
    """预注册标定阈值（valuation-methods.md §9 逐字一致）。"""

    def test_five_bands(self):
        # price < lo*0.50 → 显著低估
        self.assertEqual(ed.calibrate(40, 85, 120), "显著低估")
        # lo*0.50 <= price < lo*0.85 → 低估
        self.assertEqual(ed.calibrate(60, 85, 120), "低估")
        # lo*0.85 <= price <= hi*1.15 → 合理
        self.assertEqual(ed.calibrate(100, 85, 120), "合理")
        # hi*1.15 < price <= hi*1.50 → 高估
        self.assertEqual(ed.calibrate(150, 85, 120), "高估")
        # price > hi*1.50 → 显著高估
        self.assertEqual(ed.calibrate(200, 85, 120), "显著高估")

    def test_boundaries(self):
        # 边界值含在当前档（< 才进下一档；<= 留在上一档）
        self.assertEqual(ed.calibrate(42.5, 85, 120), "低估")      # == lo*0.50 → 非显著低估
        self.assertEqual(ed.calibrate(72.25, 85, 120), "合理")     # == lo*0.85 → 非低估
        # 用同式构造边界，规避 120*1.15 的浮点表示误差
        self.assertEqual(ed.calibrate(120 * 1.15, 85, 120), "合理")   # == hi*1.15
        self.assertEqual(ed.calibrate(120 * 1.15 + 1e-6, 85, 120), "高估")
        self.assertEqual(ed.calibrate(120 * 1.50, 85, 120), "高估")   # == hi*1.50
        self.assertEqual(ed.calibrate(120 * 1.50 + 1e-6, 85, 120), "显著高估")

    def test_reversed_range_exits(self):
        with self.assertRaises(SystemExit):
            ed.calibrate(100, 120, 85)  # lo > hi → die


class TestDcfValue(unittest.TestCase):
    def test_base_scenario_handcheck(self):
        """手工复算：PV = Σ fcf_t/(1.09)^t；TV = 12*1.03/(0.09-0.03)，PV(TV) 折现 3 年。"""
        r = ed.dcf_value([10, 11, 12], 0.09, 0.03, 0.0, 1.0)
        pv_explicit = 10 / 1.09 + 11 / 1.09 ** 2 + 12 / 1.09 ** 3
        tv = 12 * 1.03 / (0.09 - 0.03)
        ev_expected = pv_explicit + tv / 1.09 ** 3
        self.assertAlmostEqual(r["per_share"], ev_expected, places=10)
        self.assertAlmostEqual(r["per_share"], 186.76879050584964, places=6)

    def test_empty_fcf_exits(self):
        with self.assertRaises(SystemExit):
            ed.dcf_value([], 0.09, 0.03, 0.0, 1.0)

    def test_wacc_not_above_g_uses_capitalized_fallback(self):
        """wacc <= g 时终值退化为 term_base/wacc（资本化公式），不 die。"""
        r = ed.dcf_value([10], 0.03, 0.05, 0.0, 1.0)
        # TV = 10/0.03；EV = 10/1.03 + TV/1.03
        ev_expected = 10 / 1.03 + (10 / 0.03) / 1.03
        self.assertAlmostEqual(r["per_share"], ev_expected, places=10)

    def test_dilution_expands_share_count(self):
        base = ed.dcf_value([10, 11, 12], 0.09, 0.03, 0.0, 100.0)
        dil = ed.dcf_value([10, 11, 12], 0.09, 0.03, 0.0, 100.0, dilution=0.02)
        # 3 年 2% 摊薄 → 股本 x1.02^3 → 每股价值按比例下降
        self.assertAlmostEqual(
            base["per_share"] / dil["per_share"], 1.02 ** 3, places=10)


class TestRunScenarios(unittest.TestCase):
    def test_weighted_golden(self):
        r = ed.run_scenarios(DEMO["scenarios"], 0.09, 0.03, 0.0, 1.0)
        self.assertAlmostEqual(r["weighted_per_share"], 190.38100047695195, places=6)
        self.assertEqual(len(r["scenarios"]), 3)

    def test_prob_sum_warning(self):
        scs = [{"name": "x", "fcf": [10], "prob": 0.5}]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ed.run_scenarios(scs, 0.09, 0.03, 0.0, 1.0)
        self.assertIn("概率和", err.getvalue())

    def test_missing_fcf_exits(self):
        with self.assertRaises(SystemExit):
            ed.run_scenarios([{"name": "x", "prob": 1.0}], 0.09, 0.03, 0.0, 1.0)

    def test_revenue_margin_path_matches_fcf(self):
        """revenue+fcf_margin 路径与等价 fcf 列表结果一致。"""
        via_rm = ed.run_scenarios(
            [{"name": "a", "revenue": [100, 110], "fcf_margin": [0.1, 0.1], "prob": 1.0}],
            0.09, 0.03, 0.0, 1.0)
        via_fcf = ed.run_scenarios(
            [{"name": "a", "fcf": [10, 11], "prob": 1.0}], 0.09, 0.03, 0.0, 1.0)
        self.assertAlmostEqual(
            via_rm["weighted_per_share"], via_fcf["weighted_per_share"], places=10)


class TestReverseDcf(unittest.TestCase):
    def test_demo_golden(self):
        rv = DEMO["reverse"]
        r = ed.reverse_dcf(100.0, 1.0, 0.0, 0.09, 0.03,
                           rv["interim_fcf"], rv["steady_margins"], rv["base_revenue"])
        self.assertAlmostEqual(r["ev"], 100.0, places=10)
        self.assertAlmostEqual(r["fcf_required"], 5.617914000000001, places=6)
        # margin 0.25 → revenue_required = fcf_required/0.25
        self.assertAlmostEqual(r["rows"][1]["revenue_required"],
                               r["fcf_required"] / 0.25, places=10)

    def test_wacc_not_above_g_exits(self):
        with self.assertRaises(SystemExit):
            ed.reverse_dcf(100, 1, 0, 0.03, 0.05, [10], [0.2], 100.0)

    def test_nonpositive_margin_exits(self):
        with self.assertRaises(SystemExit):
            ed.reverse_dcf(100, 1, 0, 0.09, 0.03, [10], [0.0], 100.0)


class TestPvgo(unittest.TestCase):
    def test_demo_exact(self):
        r = ed.pvgo_value(6.0, 100.0, 0.09)
        self.assertAlmostEqual(r["zero_growth_value"], 66.66666666666667, places=10)
        self.assertAlmostEqual(r["pvgo"], 33.33333333333333, places=10)
        self.assertAlmostEqual(r["pvgo_pct"], 1 / 3, places=10)


class TestEpvMoat(unittest.TestCase):
    def test_verdict_bands(self):
        self.assertEqual(ed.moat_verdict(1.6), "护城河稳定（EPV/净资产 >> 1 且长期稳定）")
        self.assertEqual(ed.moat_verdict(1.2), "弱护城河")
        self.assertEqual(ed.moat_verdict(1.0), "无护城河（EPV ≈ 净资产）")
        self.assertEqual(ed.moat_verdict(0.8), "毁灭价值（EPV < 净资产）")

    def test_epv_nopat_adjusts_net_debt(self):
        r = ed.epv_value(9.0, 0.09, basis="NOPAT", net_debt=20.0, excess_cash=5.0)
        self.assertAlmostEqual(r["ev"], 100.0, places=10)
        self.assertAlmostEqual(r["equity"], 85.0, places=10)


class TestEva(unittest.TestCase):
    def test_demo_golden(self):
        r = ed.run_eva(100.0, 15.0, 0.09, 1.0, fade_years=10)
        self.assertAlmostEqual(r["roic"], 0.15, places=10)
        self.assertAlmostEqual(r["spread0"], 0.06, places=10)
        self.assertAlmostEqual(r["per_share"], 123.27852872210678, places=6)

    def test_nonpositive_capital_exits(self):
        with self.assertRaises(SystemExit):
            ed.run_eva(0.0, 15.0, 0.09, 1.0)


class TestMonteCarlo(unittest.TestCase):
    def test_seeded_deterministic_golden(self):
        """seed=42 → 黄金分位；同 seed 两次运行逐位一致。"""
        mc = DEMO["montecarlo"]
        r1 = ed.run_montecarlo(mc, 0.09, 0.03, 0.0, 1.0)
        r2 = ed.run_montecarlo(mc, 0.09, 0.03, 0.0, 1.0)
        self.assertEqual(r1, r2)
        self.assertAlmostEqual(r1["P50"], 464.09653454154534, places=6)
        self.assertAlmostEqual(r1["P90"], 591.9335556305936, places=6)
        self.assertAlmostEqual(r1["P_loss"], 0.5, places=10)

    def test_percentiles_monotonic(self):
        r = ed.run_montecarlo(DEMO["montecarlo"], 0.09, 0.03, 0.0, 1.0)
        self.assertLess(r["P10"], r["P25"])
        self.assertLess(r["P25"], r["P50"])
        self.assertLess(r["P50"], r["P75"])
        self.assertLess(r["P75"], r["P90"])


class TestRunPosition(unittest.TestCase):
    def test_run_position_direct(self):
        """run_position 的正确用法：情景已含 per_share（如 three_scenario 明细）。"""
        r = ed.run_position(100.0, [
            {"prob": 0.25, "per_share": 80.0},
            {"prob": 0.5, "per_share": 100.0},
            {"prob": 0.25, "per_share": 160.0},
        ])
        # EV = 0.25*(-0.2) + 0.5*0 + 0.25*0.6 = 0.10
        self.assertAlmostEqual(r["expected_value"], 0.10, places=10)
        # 上行 60% / 下行 20% → 赔率 3.0
        self.assertAlmostEqual(r["asymmetry_ratio"], 3.0, places=10)

    def test_run_pipeline_golden(self):
        """run() 全链路：与 --demo 黄金输出一致（position 已修复，非恒零）。"""
        out = ed.run(DEMO)
        self.assertAlmostEqual(out["weighted_fair_value"], 190.38100047695195, places=6)
        self.assertEqual(out["calibration"], "合理")
        self.assertEqual(out["epv_moat"]["verdict"], "弱护城河")
        # 修复后：run() 改用 run_scenarios 的 per_share 明细，EV = 加权公允值/现价 - 1
        # = 190.38100047695195 / 100 - 1 ≈ 0.90381（+90.4% 期望回报）。
        self.assertAlmostEqual(out["position"]["expected_value"], 0.9038100047695197, places=10)
        # 三情景全在现价之上 → 无下行参照，赔率按定义为 None（非缺陷）
        self.assertIsNone(out["position"]["asymmetry_ratio"])

    def test_run_position_uses_computed_per_share(self):
        """回归守卫：run() 不得再把原始情景（无 per_share）传给 run_position。

        修复前该路径触发 v=price 回退，expected_value 恒为 0——估值引擎静默归零。
        本用例把「恒零」钉成红色：任何时候 EV 回落到 0 即代表回退。
        """
        out = ed.run(DEMO)
        self.assertNotEqual(out["position"]["expected_value"], 0.0)
        self.assertGreater(out["position"]["expected_value"], 0.0)


class TestCli(unittest.TestCase):
    def test_demo_cli_exit_zero_and_labels(self):
        p = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "equity_dcf.py"),
             "--demo"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn("calibrate 阈值自检通过", p.stdout)
        payload = json.loads(p.stdout[: p.stdout.index("\n[demo]")])
        self.assertAlmostEqual(payload["weighted_fair_value"], 190.38100047695195, places=6)


if __name__ == "__main__":
    unittest.main()
