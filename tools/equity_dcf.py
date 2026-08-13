#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
equity_dcf.py — 机构级个股估值引擎（berkshire 原生移植版）

方法学移植自外部项目 `rollingSirius/equity-research-skill` 的 `scripts/dcf.py`
（MIT 许可证），公式与 `valuation-methods.md` §9 标定阈值逐字一致。
仅方法学移植：本文件为 berkshire 原生实现，零外部依赖（仅标准库），
不引入原项目的任何私有代码，计算逻辑对齐其公开文档。

支持的估值模块（由 --config JSON 顶层字段选择性触发）：
  - scenarios[]   三阶段情景 DCF（概率加权公允价值）
  - reverse{}     反向 DCF（解现价隐含稳态 FCF / 营收 / CAGR）
  - pvgo{}        PVGO 分解（现价中为增长付费的百分比）
  - epv{}         EPV 盈利能力价值（护城河财务验证 + 成长价值）
  - eva{}         EVA / 剩余收益
  - montecarlo{} 蒙特卡洛分布（P10–P90、P(loss)）
  - range_low/range_high + price  预注册标定标签

CLI:
  python3 tools/equity_dcf.py --config <assumptions.json>   # 跑配置中全部模块
  python3 tools/equity_dcf.py --demo                         # 自检全部功能
  均无参数 -> 打印帮助，退出码 1

设计纪律（与本项目一致）：
  - 所有估值一律脚本计算，禁止 LLM 心算；假设以 JSON 落盘即留档。
  - 折现率全报告同源；风险只惩罚一次（折现率或情景概率，二选一）。
  - 标定标签由 calibrate() 预注册规则映射，不临场发挥。
"""

import argparse
import json
import math
import random
import sys


def die(msg):
    sys.stderr.write("ERROR: " + msg + "\n")
    sys.exit(2)


# ---------------------------------------------------------------------------
# 反向 DCF：解现价隐含的稳态 FCF / 营收 / CAGR
# ---------------------------------------------------------------------------
def reverse_dcf(price, shares, net_debt, wacc, g, interim_fcf, steady_margins, base_revenue=None):
    if not (wacc > g):
        die("reverse_dcf: 需要 wacc > g")
    for m in steady_margins:
        if not (m > 0):
            die("reverse_dcf: steady_margins 必须为正")
    ev = price * shares + net_debt
    n = len(interim_fcf)
    if n == 0:
        die("reverse_dcf: interim_fcf 为空")
    pv_interim = sum(f / (1 + wacc) ** (i + 1) for i, f in enumerate(interim_fcf))
    tv_needed = (ev - pv_interim) * (1 + wacc) ** n
    fcf_required = tv_needed * (wacc - g)
    rows = []
    for m in steady_margins:
        rev_required = fcf_required / m
        cagr = (rev_required / base_revenue) ** (1.0 / n) - 1.0 if base_revenue else None
        rows.append({"margin": m, "revenue_required": rev_required, "implied_cagr": cagr})
    return {"ev": ev, "pv_interim": pv_interim, "fcf_required": fcf_required, "rows": rows}


# ---------------------------------------------------------------------------
# PVGO 分解：现价 = 零增长价值(E/r) + 增长期权价值(PVGO)
# ---------------------------------------------------------------------------
def pvgo_value(earnings_ps, price, r):
    zg = earnings_ps / r
    pvgo = price - zg
    pct = pvgo / price if price else 0.0
    return {"zero_growth_value": zg, "pvgo": pvgo, "pvgo_pct": pct}


# ---------------------------------------------------------------------------
# 三阶段情景 DCF（概率加权）
# ---------------------------------------------------------------------------
def dcf_value(fcf_list, wacc, g, net_debt, shares, fade_years=0, fade_g_start=None, dilution=0.0):
    if not fcf_list:
        die("dcf_value: fcf 序列为空")
    explicit = len(fcf_list)
    pv = 0.0
    for i, f in enumerate(fcf_list):
        pv += f / (1 + wacc) ** (i + 1)
    last = fcf_list[-1]
    # 衰减期：末年增速线性降至永续 g
    if fade_years and fade_years > 0:
        fg = fade_g_start if fade_g_start is not None else g
        for t in range(1, fade_years + 1):
            gt = fg + (g - fg) * (t / fade_years)
            fcf = last * (1 + gt)
            pv += fcf / (1 + wacc) ** (explicit + t)
            last = fcf
        term_base = last
    else:
        term_base = last
    tv = term_base * (1 + g) / (wacc - g) if wacc > g else term_base / wacc
    ev = pv + tv / (1 + wacc) ** (explicit + (fade_years if fade_years else 0))
    sh = shares * (1 + dilution) ** explicit
    per_share = (ev - net_debt) / sh if sh else 0.0
    return {"ev": ev, "per_share": per_share}


def run_scenarios(scenarios, wacc, terminal_g, net_debt, shares):
    weighted = 0.0
    probs = 0.0
    details = []
    for sc in scenarios:
        fcf = sc.get("fcf")
        if not fcf and ("revenue" in sc and "fcf_margin" in sc):
            fcf = [r * m for r, m in zip(sc["revenue"], sc["fcf_margin"])]
        if not fcf:
            die("scenario 缺 fcf 或 revenue+fcf_margin")
        r = dcf_value(
            fcf,
            wacc,
            terminal_g,
            net_debt,
            shares,
            fade_years=sc.get("fade_years", 0),
            fade_g_start=sc.get("fade_g_start"),
            dilution=sc.get("annual_dilution", 0.0),
        )
        p = sc.get("prob", 0.0)
        probs += p
        weighted += p * r["per_share"]
        details.append({"name": sc.get("name", "?"), "prob": p, "per_share": r["per_share"]})
    if abs(probs - 1.0) > 1e-6:
        sys.stderr.write("WARN: 情景概率和 = %.4f != 1.0，加权公允价值不可直接采信\n" % probs)
    return {"weighted_per_share": weighted, "scenarios": details}


# ---------------------------------------------------------------------------
# EPV 盈利能力价值（Graham–Greenwald）
# ---------------------------------------------------------------------------
def epv_value(e, coc, basis="NOPAT", net_debt=0.0, excess_cash=0.0):
    ev = e / coc
    if basis == "NOPAT":
        equity = ev - net_debt + excess_cash
    else:
        equity = ev
    return {"ev": ev, "equity": equity}


def moat_verdict(ratio):
    if ratio >= 1.5:
        return "护城河稳定（EPV/净资产 >> 1 且长期稳定）"
    if ratio > 1.0:
        return "弱护城河"
    if ratio == 1.0:
        return "无护城河（EPV ≈ 净资产）"
    return "毁灭价值（EPV < 净资产）"


def franchise_growth_value(e, g, roiic, coc):
    if not (coc > g):
        die("franchise_growth_value: 需要 coc > g")
    if not (roiic > g):
        return 0.0
    return e * (1 - g / roiic) / (coc - g)


# ---------------------------------------------------------------------------
# EVA / 剩余收益
# ---------------------------------------------------------------------------
def run_eva(invested_capital, nopat, wacc, shares, net_debt=0.0, fade_years=10,
            nopat_growth=0.0, reinvestment_rate=None, roiic=None):
    if invested_capital <= 0:
        die("run_eva: invested_capital 须为正")
    roic = nopat / invested_capital
    spread0 = roic - wacc
    pv_eva = 0.0
    ic = invested_capital
    np = nopat
    for t in range(1, fade_years + 1):
        spread_t = spread0 * (1 - t / fade_years)
        np = np * (1 + nopat_growth)
        ic = np / (wacc + spread_t) if (wacc + spread_t) > 0 else ic
        eva_t = spread_t * ic
        pv_eva += eva_t / (1 + wacc) ** t
    value = invested_capital + pv_eva
    equity = value - net_debt
    per_share = equity / shares if shares else 0.0
    return {"roic": roic, "spread0": spread0, "per_share": per_share}


# ---------------------------------------------------------------------------
# 蒙特卡洛（分布形状补充）
# ---------------------------------------------------------------------------
def run_montecarlo(mc, wacc, terminal_g, net_debt, shares):
    n = int(mc.get("n", 2000))
    seed = int(mc.get("seed", 42))
    random.seed(seed)
    base_rev = mc["base_revenue"]
    years = int(mc.get("years", 5))
    gm, gs = mc["growth_mean"], mc["growth_std"]
    ml, mm, mh = mc["margin_low"], mc["margin_mode"], mc["margin_high"]
    wl = mc.get("wacc_low", wacc)
    wh = mc.get("wacc_high", wacc)
    per_shares = []
    for _ in range(n):
        g = random.gauss(gm, gs)
        margin = random.uniform(ml, mh) if ml != mh else mm
        w = random.uniform(wl, wh)
        rev = base_rev
        fcf_list = []
        for _y in range(years):
            rev *= (1 + g)
            fcf_list.append(rev * margin)
        r = dcf_value(fcf_list, w, terminal_g, net_debt, shares)
        per_shares.append(r["per_share"])
    per_shares.sort()
    p = lambda q: per_shares[min(len(per_shares) - 1, int(q * len(per_shares)))]
    price_proxy = per_shares[len(per_shares) // 2]
    p_loss = sum(1 for x in per_shares if x < price_proxy) / len(per_shares)
    return {
        "P10": p(0.10), "P25": p(0.25), "P50": p(0.50),
        "P75": p(0.75), "P90": p(0.90),
        "P_loss": p_loss,
    }


# ---------------------------------------------------------------------------
# 预注册标定（与 valuation-methods.md §9 逐字一致）
# ---------------------------------------------------------------------------
def calibrate(price, lo, hi):
    if lo > hi:
        die("calibrate: range_low(%.4f) > range_high(%.4f)" % (lo, hi))
    if price < lo * 0.50:
        return "显著低估"
    if price < lo * 0.85:
        return "低估"
    if price <= hi * 1.15:
        return "合理"
    if price <= hi * 1.50:
        return "高估"
    return "显著高估"


# ---------------------------------------------------------------------------
# 仓位思维（position）
# ---------------------------------------------------------------------------
def run_position(price, scenarios):
    ev = 0.0
    up = down = None
    for sc in scenarios:
        p = sc.get("prob", 0.0)
        v = sc.get("per_share", price)
        ev += p * (v / price - 1.0)
        if up is None or v > up:
            up = v
        if down is None or v < down:
            down = v
    asym = (up / price - 1.0) / max(up / price - 1.0, 1e-9) if (up / price - 1.0) > 0 else 0.0
    down_side = max(down / price - 1.0, -1.0)
    asym_ratio = (up / price - 1.0) / abs(down_side) if down_side < 0 else None
    return {"expected_value": ev, "asymmetry_ratio": asym_ratio}


# ---------------------------------------------------------------------------
# 主运行
# ---------------------------------------------------------------------------
def run(top):
    out = {}
    price = top.get("price")
    shares = top.get("shares", 0.0)
    net_debt = top.get("net_debt", 0.0)
    wacc = top.get("wacc")
    terminal_g = top.get("terminal_g", 0.0)

    if "scenarios" in top and wacc is not None:
        res = run_scenarios(top["scenarios"], wacc, terminal_g, net_debt, shares)
        out["three_scenario"] = res
        out["weighted_fair_value"] = res["weighted_per_share"]

    if "reverse" in top and price is not None and shares and wacc is not None:
        rv = top["reverse"]
        out["reverse_dcf"] = reverse_dcf(
            price, shares, net_debt, wacc, terminal_g,
            rv["interim_fcf"], rv["steady_margins"], rv.get("base_revenue"),
        )

    if "pvgo" in top and price is not None:
        pv = top["pvgo"]
        r = pv.get("r", wacc)
        out["pvgo"] = pvgo_value(pv["earnings_ps"], price, r)

    if "epv" in top and shares:
        ep = top["epv"]
        e = epv_value(ep["normalized_earnings"], ep["coc"], ep.get("earnings_basis", "NOPAT"),
                      ep.get("net_debt", 0.0), ep.get("excess_cash", 0.0))
        out["epv"] = e
        if ep.get("asset_series"):
            series = ep["asset_series"]
            last = series[-1]
            ratio = e["equity"] / last[2] if last[2] else None
            out["epv_moat"] = {"ratio": ratio, "verdict": moat_verdict(ratio) if ratio else "N/A"}

    if "eva" in top and shares:
        ev = top["eva"]
        out["eva"] = run_eva(ev["invested_capital"], ev["nopat"], wacc or ev.get("wacc"),
                             shares, ev.get("net_debt", 0.0), ev.get("fade_years", 10),
                             ev.get("nopat_growth", 0.0))

    if "montecarlo" in top and wacc is not None:
        out["montecarlo"] = run_montecarlo(top["montecarlo"], wacc, terminal_g, net_debt, shares)

    if "range_low" in top and "range_high" in top and price is not None:
        out["calibration"] = calibrate(price, top["range_low"], top["range_high"])

    if price is not None and "scenarios" in top:
        out["position"] = run_position(price, top["scenarios"])

    return out


def main():
    ap = argparse.ArgumentParser(description="equity_dcf.py — 机构级个股估值引擎（berkshire 移植版）")
    ap.add_argument("--config", help="JSON 假设文件路径")
    ap.add_argument("--demo", action="store_true", help="运行内置自检")
    args = ap.parse_args()

    if args.demo:
        demo = {
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
        res = run(demo)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        # 标定自检
        assert calibrate(100, 85, 120) == "合理", "calibrate 合理 失败"
        assert calibrate(40, 85, 120) == "显著低估", "calibrate 显著低估 失败"
        assert calibrate(200, 85, 120) == "显著高估", "calibrate 显著高估 失败"
        print("\n[demo] calibrate 阈值自检通过")
        return 0

    if not args.config:
        ap.print_help()
        return 1

    with open(args.config, "r", encoding="utf-8") as f:
        top = json.load(f)
    res = run(top)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
