#!/usr/bin/env python3
"""Financial Rigor Toolkit for AI Berkshire.

Command-line tool for verifying financial data accuracy during investment research.
Automatically called by Claude Code Skills at critical validation checkpoints.

Zero external dependencies — uses only Python stdlib (decimal, json, math, argparse).
Requires Python >= 3.7.

Usage (called automatically by Skills, no manual execution needed):
    python3 tools/financial_rigor.py verify-market-cap --price 510 --shares 9.11e9 --reported 4.65e12 --currency HKD
    python3 tools/financial_rigor.py verify-valuation --price 510 --eps 23.5 --bvps 120 --fcf-per-share 18 --dividend 2.4
    python3 tools/financial_rigor.py cross-validate --field revenue --values '{"年报": 7518, "Yahoo": 7500, "StockAnalysis": 7520}' --unit 亿
    python3 tools/financial_rigor.py benford --values '[1234, 2345, 3456, ...]'
    python3 tools/financial_rigor.py calc --expr '510 * 9.11e9'

Exit codes (统一语义, 供脚本/CI/Agent 判断):
    0 = 验证通过 / 计算成功
    1 = 业务验证不通过或计算失败 (市值偏差>5% / 多源不一致 / Benford不符合 / 计算错误)
    2 = 参数错误或证据不足 (非法输入 / Benford样本<50)
"""

import argparse
import json
import math
import re
import subprocess
import sys
from decimal import Decimal, Context, ROUND_HALF_EVEN, ROUND_HALF_UP, InvalidOperation
from pathlib import Path

# ---------------------------------------------------------------------------
# Exact Decimal Engine (no floating-point drift)
# ---------------------------------------------------------------------------

_CTX = Context(prec=28, rounding=ROUND_HALF_EVEN)


def exact(value) -> Decimal:
    """Convert any numeric to exact Decimal, avoiding float traps."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(str(value))


def fmt_number(d: Decimal, unit: str = "") -> str:
    """Format large numbers in human-readable form (亿/万亿/B/T)."""
    # 护栏: 超大有限 Decimal 过 float 会溢出成 inf, 直接输出科学计数法字符串
    if abs(d) > Decimal("1e15"):
        return f"{d.normalize()}{unit}"
    v = float(d)
    abs_v = abs(v)
    if unit in ("亿", "亿元", "亿港元", "亿美元"):
        if abs_v >= 10000:
            return f"{v/10000:.2f}万亿{unit[1:] if len(unit) > 1 else ''}"
        return f"{v:.2f}{unit}"
    if abs_v >= 1e12:
        return f"{v/1e12:.2f}T"
    if abs_v >= 1e9:
        return f"{v/1e9:.2f}B"
    if abs_v >= 1e6:
        return f"{v/1e6:.2f}M"
    return f"{v:,.2f}"


# ---------------------------------------------------------------------------
# 1. Market Cap Verification (股价×总股本 vs 报告市值)
# ---------------------------------------------------------------------------

def _mc_inputs(price, shares, reported_cap):
    """市值验算共用校验+计算核心（文本/JSON 两路共用，候选②去重）。"""
    p = _require_finite("股价", price)
    if p <= 0:
        raise ValueError(f"股价必须为正数, 收到 {price}")
    s = _require_finite("总股本", shares)
    if s <= 0:
        raise ValueError(f"总股本必须为正数, 收到 {shares}")
    r = _require_finite("报告市值", reported_cap)
    if r <= 0:
        raise ValueError(f"报告市值必须为正数, 收到 {reported_cap}")
    calculated = _CTX.multiply(p, s)
    # 偏差全程 Decimal 计算, 不经过 float
    deviation = _CTX.divide(abs(calculated - r), r) * 100
    return p, s, r, calculated, deviation


def verify_market_cap(price, shares, reported_cap, currency=""):
    """Verify market cap = price × shares, compare with reported value."""
    p, s, r, calculated, deviation = _mc_inputs(price, shares, reported_cap)

    print("=" * 60)
    print("市值验算 (Market Cap Verification)")
    print("=" * 60)
    print(f"  股价 (Price):       {p} {currency}")
    print(f"  总股本 (Shares):    {fmt_number(s)}")
    print(f"  计算市值:           {fmt_number(calculated)} {currency}")
    print(f"  报告市值:           {fmt_number(r)} {currency}")
    print(f"  偏差:               {deviation:.2f}%")
    print()

    if deviation > Decimal("5"):
        print(f"  ❌ 警告: 偏差 {deviation:.1f}% > 5%, 请检查:")
        print(f"     - 股本是否为最新（回购/增发）?")
        print(f"     - 单位是否一致（港币 vs 人民币 vs 美元）?")
        print(f"     - 股价是否为最新?")
        return False
    elif deviation > Decimal("1"):
        print(f"  ⚠️  偏差 {deviation:.1f}% 在可接受范围, 可能因股价波动/股本变化")
        return True
    else:
        print(f"  ✅ 验证通过, 偏差仅 {deviation:.2f}%")
        return True


# ---------------------------------------------------------------------------
# 2. Valuation Metrics Verification (估值指标验算)
# ---------------------------------------------------------------------------

def _valuation_metrics(price, eps=None, bvps=None, fcf_per_share=None,
                       dividend=None, revenue_per_share=None):
    """估值指标共用计算核心（文本/JSON 两路共用，候选②去重）。

    返回 (p, metrics, skipped)。metrics: json_key -> Decimal；
    skipped: [{"metric", "reason_code"}]。
    """
    p = _require_finite("股价", price)
    if p <= 0:
        raise ValueError(f"股价必须为正数, 收到 {price}")

    metrics, skipped = {}, {}
    if eps is not None:
        e = _require_finite("EPS", eps)
        if e > 0:
            metrics["pe"] = _CTX.divide(p, e)
            metrics["earnings_yield_pct"] = _CTX.divide(e, p) * 100
        else:
            skipped["pe"] = "eps_non_positive"
    if bvps is not None:
        b = _require_finite("每股净资产", bvps)
        if b != 0:
            metrics["pb"] = _CTX.divide(p, b)
            if eps is not None and exact(eps) != 0:
                metrics["roe_pct"] = _CTX.divide(exact(eps), b) * 100
        else:
            skipped["pb"] = "bvps_zero"
    if fcf_per_share is not None:
        f = _require_finite("每股FCF", fcf_per_share)
        if f != 0:
            metrics["p_fcf"] = _CTX.divide(p, f)
            metrics["fcf_yield_pct"] = _CTX.divide(f, p) * 100
        else:
            skipped["p_fcf"] = "fcf_zero"
    if dividend is not None:
        d = _require_finite("每股股息", dividend)
        metrics["dividend_yield_pct"] = _CTX.divide(d, p) * 100
    if revenue_per_share is not None:
        rv = _require_finite("每股营收", revenue_per_share)
        if rv != 0:
            metrics["ps"] = _CTX.divide(p, rv)
        else:
            skipped["ps"] = "revenue_zero"
    return p, metrics, skipped


def verify_valuation(price, eps=None, bvps=None, fcf_per_share=None,
                     dividend=None, revenue_per_share=None):
    """Calculate and verify key valuation ratios from raw inputs."""
    p, metrics, skipped = _valuation_metrics(price, eps, bvps, fcf_per_share,
                                             dividend, revenue_per_share)

    print("=" * 60)
    print("估值指标验算 (Valuation Verification)")
    print("=" * 60)
    print(f"  当前股价: {p}")
    print()

    results = {}

    if eps is not None:
        if "pe" in metrics:
            e = exact(eps)
            pe = metrics["pe"]
            print(f"  PE (TTM):  {p} / {e} = {pe:.2f}x")
            results["PE"] = float(pe)
            # Earnings yield
            ey = metrics["earnings_yield_pct"]
            print(f"  盈利收益率: {ey:.2f}%")
        else:
            print(f"  PE: EPS ≤ 0 (亏损/不适用), 跳过 PE 与盈利收益率")

    if bvps is not None:
        if "pb" in metrics:
            b = exact(bvps)
            pb = metrics["pb"]
            print(f"  PB:        {p} / {b} = {pb:.2f}x")
            results["PB"] = float(pb)
            if "roe_pct" in metrics:
                roe = metrics["roe_pct"]
                print(f"  ROE:       {exact(eps)} / {b} = {roe:.2f}%")
                results["ROE"] = float(roe)
        else:
            print(f"  PB: 每股净资产为0, 无法计算, 跳过")

    if fcf_per_share is not None:
        if "p_fcf" in metrics:
            f = exact(fcf_per_share)
            pfcf = metrics["p_fcf"]
            fcf_yield = metrics["fcf_yield_pct"]
            print(f"  P/FCF:     {p} / {f} = {pfcf:.2f}x")
            print(f"  FCF Yield: {fcf_yield:.2f}%")
            results["P_FCF"] = float(pfcf)
            results["FCF_Yield"] = float(fcf_yield)
        else:
            print(f"  P/FCF: FCF为0, 无法计算, 跳过")

    if dividend is not None:
        d = exact(dividend)
        div_yield = metrics["dividend_yield_pct"]
        print(f"  股息率:    {d} / {p} = {div_yield:.2f}%")
        results["Dividend_Yield"] = float(div_yield)

    if revenue_per_share is not None:
        if "ps" in metrics:
            rv = exact(revenue_per_share)
            ps = metrics["ps"]
            print(f"  PS:        {p} / {rv} = {ps:.2f}x")
            results["PS"] = float(ps)
        else:
            print(f"  PS: 每股营收为0, 无法计算, 跳过")

    print()
    print("  ✅ 以上指标均使用精确十进制计算, 无浮点误差")
    return results


# ---------------------------------------------------------------------------
# 3. Cross-Source Data Validation (多源交叉验证)
# ---------------------------------------------------------------------------

def _cross_validate_core(field_name, source_values: dict, tolerance_pct):
    """交叉验证共用计算核心（文本/JSON 两路共用，候选②去重）。

    返回 (values, tol, median, rows, all_ok)；
    rows: [{"source", "value", "deviation_pct", "within"}]（Decimal/bool 原生值）。
    """
    if len(source_values) < 2:
        raise ValueError(
            f"交叉验证至少需要 2 个独立来源, 收到 {len(source_values)} 个"
            f"（项目规则: 关键数据至少 2 个独立来源交叉验证）")

    values = {k: _require_finite(f"来源[{k}]", v) for k, v in source_values.items()}
    tol = _require_finite("容差", tolerance_pct)

    # Find median as reference — 全程 Decimal, 不经过 float
    sorted_vals = sorted(values.values())
    n = len(sorted_vals)
    if n % 2 == 1:
        median = sorted_vals[n // 2]
    else:
        median = _CTX.divide(_CTX.add(sorted_vals[n//2-1], sorted_vals[n//2]), Decimal("2"))

    if median == 0:
        raise ValueError(f"{field_name} 的中位数为 0, 无法计算相对偏差")

    rows, all_ok = [], True
    for src, val in values.items():
        dev = _CTX.divide(abs(val - median), abs(median)) * 100
        within = dev <= tol
        if not within:
            all_ok = False
        rows.append({"source": src, "value": val,
                     "deviation_pct": dev, "within": within})
    return values, tol, median, rows, all_ok


def cross_validate(field_name, source_values: dict, unit="", tolerance_pct=2.0):
    """Compare a data point across multiple sources, flag discrepancies."""
    values, tol, median, rows, all_ok = _cross_validate_core(
        field_name, source_values, tolerance_pct)

    print("=" * 60)
    print(f"交叉验证: {field_name} (Cross-Validation)")
    print("=" * 60)

    print(f"  数据来源数: {len(values)}")
    print(f"  参考中位数: {fmt_number(median)} {unit}")
    print()

    for row in rows:
        status = "✅" if row["within"] else "❌"
        print(f"  {status} {row['source']:20s}: {fmt_number(row['value'])} {unit}"
              f"  (偏差 {row['deviation_pct']:.2f}%)")

    print()
    if all_ok:
        print(f"  ✅ 所有来源偏差 ≤ {tol}%, 数据一致")
    else:
        print(f"  ⚠️  存在来源偏差 > {tol}%, 请核实差异原因")
        print(f"     建议: 优先采用公司年报/交易所数据")

    # Consensus value (Decimal)
    consensus = median
    print(f"\n  共识值 (加权中位数): {fmt_number(consensus)} {unit}")
    return {"consensus": consensus, "all_consistent": all_ok}


# ---------------------------------------------------------------------------
# 4. Benford's Law Quick Check (财务数据造假检测)
# ---------------------------------------------------------------------------

_BENFORD = {d: math.log10(1 + 1/d) for d in range(1, 10)}


def _benford_core(values: list):
    """Benford 共用计算核心（文本/JSON 两路共用，候选②去重）。

    返回 (sample_size, stats)。sample_size 为有效首位数个数；
    stats 为 None 表示样本不足（<50）；否则含 quantize 后的 mad/chi2、
    符合度代码、is_conforming、observed 分布与 counts。
    """
    # Extract leading significant digits — Decimal 全程:
    # 大数不过 float 不溢出; 也避免 int(10**log10(v)) 的浮点截位错误 (如 8 → 7)
    digits = []
    for v in values:
        d = v if isinstance(v, Decimal) else Decimal(str(v))
        if not d.is_finite() or d == 0:
            continue
        digits.append(d.as_tuple().digits[0])

    n = len(digits)
    if n < 50:
        return n, None

    counts = {}
    for d in digits:
        counts[d] = counts.get(d, 0) + 1
    observed = {d: counts.get(d, 0) / n for d in range(1, 10)}

    # MAD (Nigrini's Mean Absolute Deviation)
    mad = sum(abs(observed.get(d, 0) - _BENFORD[d]) for d in range(1, 10)) / 9

    # Chi-square
    chi2 = sum((counts.get(d, 0) - _BENFORD[d] * n) ** 2 / (_BENFORD[d] * n) for d in range(1, 10))

    # 量化到 6 位, 消除跨平台 libm ULP 边界翻转 (v1.4 §10.2)
    mad_q = Decimal(str(mad)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
    chi2_q = Decimal(str(chi2)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
    if mad_q < Decimal("0.006"):
        conformity = "CLOSE"
    elif mad_q < Decimal("0.012"):
        conformity = "ACCEPTABLE"
    elif mad_q < Decimal("0.015"):
        conformity = "MARGINAL"
    else:
        conformity = "NONCONFORMING"
    is_conforming = mad_q < Decimal("0.015")
    stats = {"mad": mad_q, "chi2": chi2_q, "conformity": conformity,
             "is_conforming": is_conforming, "observed": observed, "counts": counts}
    return n, stats


_BENFORD_LABELS = {
    "CLOSE": "Close (高度符合)",
    "ACCEPTABLE": "Acceptable (可接受)",
    "MARGINAL": "Marginally Acceptable (边缘)",
    "NONCONFORMING": "Nonconforming (不符合 ⚠️)",
}


def benford_check(values: list):
    """Quick Benford's Law check on a list of financial values."""
    print("=" * 60)
    print("Benford定律检测 (Financial Data Fabrication Check)")
    print("=" * 60)

    n, stats = _benford_core(values)
    if stats is None:
        print(f"  ⚠️  样本量不足: {n} < 50, Benford分析不可靠")
        return None

    mad = stats["mad"]
    chi2 = stats["chi2"]
    conformity = _BENFORD_LABELS[stats["conformity"]]
    observed = stats["observed"]

    print(f"  样本量:    {n}")
    print(f"  MAD:       {mad:.6f}")
    print(f"  Chi-sq:    {chi2:.2f}")
    print(f"  符合度:    {conformity}")
    print()

    # Digit distribution table
    print(f"  {'首位数':>6} {'观测':>8} {'Benford期望':>12} {'偏差':>8}")
    print(f"  {'-'*6} {'-'*8} {'-'*12} {'-'*8}")
    for d in range(1, 10):
        obs = observed.get(d, 0)
        exp = _BENFORD[d]
        dev = obs - exp
        flag = " ⚠️" if abs(dev) > 0.03 else ""
        print(f"  {d:>6d} {obs:>8.3f} {exp:>12.3f} {dev:>+8.3f}{flag}")

    print()
    is_ok = stats["is_conforming"]
    if is_ok:
        print("  ✅ 数据首位数字分布符合Benford定律")
    else:
        print("  ❌ 数据首位数字分布异常, 可能存在人为调整")
        print("     提示: 不符合Benford定律不一定是造假, 但值得进一步调查")

    return {"mad": mad, "chi2": chi2, "conformity": conformity, "is_conforming": is_ok}


# ---------------------------------------------------------------------------
# 5. Exact Calculator (精确计算器)
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


# round(EXPR, N) 预处理：允许一层括号内层、无逗号。Decimal 本身精确，round 仅量化显示位数。
_ROUND_RE = re.compile(r"round\(\s*((?:[^()]|\([^()]*\))*?)\s*,\s*(\d+)\s*\)")
_CALC_ALLOWED = set("0123456789.+-*/() eE")


def _expand_round(expr: str) -> str:
    """把 round(EXPR, N) 展开为 quantize 后的字面量（ROUND_HALF_UP 四舍五入）。

    仅接受白名单字符的内层表达式；循环展开以支持嵌套 round。非 round 部分原样保留。
    """
    def _repl(m):
        inner = m.group(1).strip()
        if not all(c in _CALC_ALLOWED for c in inner.replace(" ", "")):
            raise ValueError(f"round 内层含非法字符: {inner}")
        dec_inner = _NUMBER_RE.sub(r"Decimal('\g<0>')", inner)
        value = eval(dec_inner, {"__builtins__": {}}, {"Decimal": Decimal})  # noqa: S307
        n = int(m.group(2))
        return value.quantize(Decimal("1e-%d" % n), rounding=ROUND_HALF_UP).to_eng_string()
    prev = None
    while prev != expr:
        prev = expr
        expr = _ROUND_RE.sub(_repl, expr)
    return expr


def _calc_eval(expr: str):
    """calc 共用求值核心（文本/JSON 两路共用，候选②去重）。

    返回 (value, expanded, err)：value 为 Decimal 或 None；
    expanded 为展开 round 后的表达式（展开失败时为 None）；
    err 为 None 或 {"code", "message"}，code ∈ {"unsafe_expression", "calc_error"}。
    """
    try:
        expr = _expand_round(expr)
    except ValueError as e:
        return None, None, {"code": "unsafe_expression", "message": str(e)}
    if not all(c in _CALC_ALLOWED for c in expr.replace(" ", "")):
        return None, expr, {"code": "unsafe_expression", "message": "表达式含非法字符"}
    try:
        # Wrap each numeric literal (incl. scientific notation) in Decimal(...)
        # so evaluation never touches binary floats
        dec_expr = _NUMBER_RE.sub(r"Decimal('\g<0>')", expr)
        return exact(eval(dec_expr, {"__builtins__": {}}, {"Decimal": Decimal})), expr, None
    except Exception as e:  # noqa: BLE001 — 计算错误一律降级为 ERROR
        return None, expr, {"code": "calc_error", "message": str(e)}


def exact_calc(expr: str):
    """Evaluate a financial expression with exact decimal arithmetic.

    Supports: +, -, *, /, (), numbers (including scientific notation).
    """
    print("=" * 60)
    print("精确计算 (Exact Calculator)")
    print("=" * 60)

    value, expanded, err = _calc_eval(expr)
    if err:
        # round 展开失败打印原因；整体白名单不过打印展开后表达式（与历史输出一致）
        detail = expanded if (err["code"] == "unsafe_expression"
                              and expanded is not None) else err["message"]
        print(f"  ❌ 不安全的表达式: {detail}" if err["code"] == "unsafe_expression"
              else f"  ❌ 计算错误: {err['message']}")
        return None

    d_result = value
    print(f"  表达式: {expanded}")
    print(f"  结果:   {fmt_number(d_result)}")
    print(f"  精确值: {d_result}")
    return d_result


# ---------------------------------------------------------------------------
# 6. Three-Scenario Valuation (三情景估值)
# ---------------------------------------------------------------------------

def _require_finite(name: str, value) -> Decimal:
    """Reject NaN/Infinity — 金融输入必须是有限数，否则结果是无声的垃圾。"""
    d = exact(value)
    if not d.is_finite():
        raise ValueError(f"{name}必须是有限数值, 收到 {value}")
    return d


def _validate_growth(name: str, growth) -> Decimal:
    """Growth must be a decimal fraction: 0.20 means +20%/yr. Reject unit mistakes."""
    g = _require_finite(f"{name}增速", growth)
    if g > 2 or g <= -1:
        raise ValueError(
            f"{name}增速 {growth} 超出合理区间 (-1, 2]。"
            f"增长率必须用小数表示: 20% 请输入 0.20, 而不是 20")
    return g


def three_scenario_valuation(current_price, current_eps, shares_billion,
                             growth_optimistic, growth_neutral, growth_pessimistic,
                             pe_optimistic, pe_neutral, pe_pessimistic,
                             years=3, currency=""):
    """Calculate three-scenario target prices with exact arithmetic.

    shares_billion 的单位是亿股, 隐含市值输出单位即为亿(对应币种)。
    Returns a list of per-scenario dicts (bull, base, bear).
    """
    print("=" * 60)
    print("三情景估值模型 (Three-Scenario Valuation)")
    print("=" * 60)

    p = _require_finite("当前股价", current_price)
    if p <= 0:
        raise ValueError(f"当前股价必须为正数, 收到 {current_price}")
    eps = _require_finite("当前EPS", current_eps)
    shares = _require_finite("总股本", shares_billion)
    if shares <= 0:
        raise ValueError(f"总股本必须为正数, 收到 {shares_billion}")
    if years < 1:
        raise ValueError(f"预测期 years 必须 ≥ 1, 收到 {years}")

    scenarios = [
        ("乐观 (Bull)", _validate_growth("乐观", growth_optimistic),
         _require_finite("乐观PE", pe_optimistic)),
        ("中性 (Base)", _validate_growth("中性", growth_neutral),
         _require_finite("中性PE", pe_neutral)),
        ("悲观 (Bear)", _validate_growth("悲观", growth_pessimistic),
         _require_finite("悲观PE", pe_pessimistic)),
    ]

    print(f"  当前股价: {p} {currency}")
    print(f"  当前EPS:  {eps}")
    print(f"  总股本:   {shares}亿股")
    print(f"  预测期:   {years}年")
    print()
    print(f"  {'情景':12} {'年增速':>8} {'目标PE':>8} {'目标EPS':>10} {'目标股价':>10} {'隐含市值(亿)':>12} {'涨跌幅':>8}")
    print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*10} {'-'*10} {'-'*12} {'-'*8}")

    results = []
    for name, g, pe in scenarios:
        target_pe = exact(pe)
        # Future EPS = current EPS × (1 + growth)^years
        future_eps = eps
        for _ in range(years):
            future_eps = _CTX.multiply(future_eps, _CTX.add(Decimal("1"), g))
        target_price = _CTX.multiply(future_eps, target_pe)
        implied_mcap = _CTX.multiply(target_price, shares)
        change = float(target_price - p) / float(p) * 100

        print(f"  {name:12} {float(g)*100:>7.0f}% {float(target_pe):>7.0f}x "
              f"{float(future_eps):>10.2f} {float(target_price):>9.1f} "
              f"{float(implied_mcap):>11,.0f} {change:>+7.1f}%")

        results.append({
            "name": name,
            "growth": g,
            "pe": target_pe,
            "future_eps": future_eps,
            "target_price": target_price,
            "implied_mcap": implied_mcap,
            "change_pct": change,
        })

    print()
    print("  ✅ 所有计算使用精确十进制, 结果可审计复现")
    return results


# ---------------------------------------------------------------------------
# JSON 语义重放协议 (opt-in --json) — v1.4 §10.2
#
# 旧 public 函数、默认 stdout 和退出码全部保持不变; 下列构造器只服务 --json。
# 输出 envelope 与 result 字段冻结于 tools/financial_rigor_result_schema.json。
# Decimal 一律序列化为十进制字符串(禁 JSON float)。构造器与旧函数的数值等价
# 由 test_financial_rigor 的交叉核对测试锁定, 防止两条路径漂移。
# ---------------------------------------------------------------------------

_JSON_SCHEMA_VERSION = 1


def _dstr(d) -> str:
    """Decimal -> 规范化十进制字符串 (用 'f' 避免科学计数法, 不丢精度)。"""
    d = d if isinstance(d, Decimal) else exact(d)
    return format(d.normalize(), "f")


def _capture(fn, *args, **kwargs):
    """Run fn with stdout suppressed, return its value (复用 public 函数取结构化值)。"""
    import contextlib
    import io as _io
    with contextlib.redirect_stdout(_io.StringIO()):
        return fn(*args, **kwargs)


# 判决原语单一真源（2026-08-30 候选②）：outcome → exit_code 映射。
# 此前 (outcome, exit_code) 二元组散布各 _json_* 与文本分发共 7 处、
# WARN 与 PASS 两套"通过"词汇并存；统一自此表出。
# 语义：0=验证通过（PASS/WARN 均属通过带警告）/ 1=业务不通过（FAIL/ERROR）/
# 2=证据不足（INSUFFICIENT）。与 report_audit 三态、mk 退出码分属不同语义轴，
# 不强行跨工具合一（那是提交有效性/抽检准出，不是验算判决）。
_OUTCOME_EXIT = {"PASS": 0, "WARN": 0, "FAIL": 1, "ERROR": 1, "INSUFFICIENT": 2}


def _verdict(outcome: str) -> tuple[str, int]:
    """outcome → (outcome, exit_code)。exit_code 不允许本地另写。"""
    return outcome, _OUTCOME_EXIT[outcome]


def _envelope(operation, inputs, result, outcome, exit_code, warnings=None, errors=None):
    is_pass = True if outcome == "PASS" else False if outcome == "FAIL" else None
    return {
        "schema_version": _JSON_SCHEMA_VERSION,
        "operation": operation,
        "inputs": inputs,
        "result": result,
        "outcome": outcome,
        "is_pass": is_pass,
        "exit_code": exit_code,
        "warnings": warnings or [],
        "errors": errors or [],
    }


def _json_market_cap(price, shares, reported_cap, currency=""):
    p, s, r, calculated, deviation = _mc_inputs(price, shares, reported_cap)
    if deviation > Decimal("5"):
        band = "FAIL"
    elif deviation > Decimal("1"):
        band = "WARN"
    else:
        band = "PASS"
    # band 是偏差分带；outcome 是判决原语——WARN 属于带警告的通过（PASS）
    outcome, exit_code = _verdict("FAIL" if band == "FAIL" else "PASS")
    result = {
        "calculated_market_cap": _dstr(calculated),
        "reported_market_cap": _dstr(r),
        "deviation_pct": _dstr(deviation),
        "band": band,
    }
    inputs = {"price": _dstr(p), "shares": _dstr(s),
              "reported": _dstr(r), "currency": currency}
    return _envelope("verify-market-cap", inputs, result, outcome, exit_code)


def _json_valuation(price, eps=None, bvps=None, fcf_per_share=None,
                    dividend=None, revenue_per_share=None):
    p, metrics, skipped = _valuation_metrics(price, eps, bvps, fcf_per_share,
                                             dividend, revenue_per_share)
    outcome, exit_code = _verdict("PASS" if metrics else "INSUFFICIENT")
    result = {
        "metrics": {k: _dstr(v) for k, v in metrics.items()},
        "skipped": [{"metric": m, "reason_code": rc} for m, rc in skipped.items()],
    }
    given = [("price", price), ("eps", eps), ("bvps", bvps),
             ("fcf_per_share", fcf_per_share), ("dividend", dividend),
             ("revenue_per_share", revenue_per_share)]
    inputs = {k: _dstr(exact(v)) for k, v in given if v is not None}
    return _envelope("verify-valuation", inputs, result, outcome, exit_code)


def _json_cross_validate(field_name, source_values, unit="", tolerance_pct=Decimal("2.0")):
    values, tol, median, rows, all_ok = _cross_validate_core(
        field_name, source_values, tolerance_pct)
    sources = [{"source": row["source"], "value": _dstr(row["value"]),
                "deviation_pct": _dstr(row["deviation_pct"]),
                "within_tolerance": bool(row["within"])} for row in rows]
    result = {"consensus": _dstr(median), "tolerance_pct": _dstr(tol),
              "sources": sources, "all_consistent": all_ok}
    outcome, exit_code = _verdict("PASS" if all_ok else "FAIL")
    inputs = {"field": field_name, "unit": unit,
              "values": {k: _dstr(v) for k, v in values.items()}}
    return _envelope("cross-validate", inputs, result, outcome, exit_code)


def _json_benford(values):
    n, stats = _benford_core(values)
    if stats is None:
        result = {"sample_size": n, "mad": None, "chi_square": None,
                  "conformity": "INSUFFICIENT", "is_conforming": None}
        return _envelope("benford", {"count": len(values)}, result, "INSUFFICIENT", 2)
    result = {"sample_size": n, "mad": _dstr(stats["mad"]),
              "chi_square": _dstr(stats["chi2"]),
              "conformity": stats["conformity"],
              "is_conforming": stats["is_conforming"]}
    outcome, exit_code = _verdict("PASS" if stats["is_conforming"] else "FAIL")
    return _envelope("benford", {"count": len(values)}, result, outcome, exit_code)


def _json_calc(expr):
    value, expanded, err = _calc_eval(expr)
    expr_out = expanded if expanded is not None else expr
    result = {"expression": expr_out,
              "value": None if value is None else _dstr(value)}
    if err:
        return _envelope("calc", {"expr": expr_out}, result, "ERROR", 1,
                         errors=[err])
    return _envelope("calc", {"expr": expr_out}, result, *_verdict("PASS"))


def _json_three_scenario(price, eps, shares, growth, pe, years=3, currency=""):
    rows = _capture(three_scenario_valuation, price, eps, shares,
                    growth[0], growth[1], growth[2], pe[0], pe[1], pe[2], years, currency)
    p = _require_finite("当前股价", price)
    id_map = {"乐观 (Bull)": "bull", "中性 (Base)": "base", "悲观 (Bear)": "bear"}
    scenarios = []
    for row in rows:
        change = _CTX.divide(row["target_price"] - p, p) * 100  # Decimal, 非 float
        scenarios.append({
            "id": id_map[row["name"]],
            "growth": _dstr(row["growth"]),
            "pe": _dstr(row["pe"]),
            "future_eps": _dstr(row["future_eps"]),
            "target_price": _dstr(row["target_price"]),
            "implied_mcap": _dstr(row["implied_mcap"]),
            "change_pct": _dstr(change),
        })
    result = {"years": years, "currency": currency, "scenarios": scenarios}
    inputs = {"price": _dstr(exact(price)), "eps": _dstr(exact(eps)),
              "shares": _dstr(exact(shares)),
              "growth": [_dstr(exact(g)) for g in growth],
              "pe": [_dstr(exact(x)) for x in pe],
              "years": years, "currency": currency}
    return _envelope("three-scenario", inputs, result, "PASS", 0)


def _parse_values_dict(text):
    """--json 路径: 解析 cross-validate 的 --values, 非法即 ValueError (由调用方转 ERROR)。"""
    try:
        values = json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError as e:
        raise ValueError(f"--values 不是有效 JSON: {e}")
    if not isinstance(values, dict):
        raise ValueError(f"--values 必须是 JSON 对象 {{来源: 数值}}, 收到 {type(values).__name__}")
    bad = [k for k, v in values.items()
           if isinstance(v, bool) or not isinstance(v, (int, Decimal))]
    if bad:
        raise ValueError(f"--values 中这些来源的值不是数值: {', '.join(bad)}")
    return values


def _parse_values_list(text):
    """--json 路径: 解析 benford 的 --values 数组。"""
    try:
        values = json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError as e:
        raise ValueError(f"--values 不是有效 JSON: {e}")
    if not isinstance(values, list):
        raise ValueError(f"--values 必须是 JSON 数组, 收到 {type(values).__name__}")
    bad = [str(v) for v in values
           if isinstance(v, bool) or not isinstance(v, (int, Decimal))]
    if bad:
        raise ValueError(f"--values 含非数值元素: {', '.join(bad[:5])}")
    return values


def _emit_json(args):
    """--json 分发: 构造 envelope, 打印唯一 JSON 文档, 按 exit_code 退出。

    即使参数/证据错误也输出一个合法 JSON (outcome=ERROR/exit 2), 绝不裸 traceback。
    """
    cmd = args.command
    try:
        if cmd == "verify-market-cap":
            env = _json_market_cap(args.price, args.shares, args.reported, args.currency)
        elif cmd == "verify-valuation":
            env = _json_valuation(args.price, args.eps, args.bvps, args.fcf_per_share,
                                  args.dividend, args.revenue_per_share)
        elif cmd == "cross-validate":
            env = _json_cross_validate(args.field, _parse_values_dict(args.values),
                                       args.unit, args.tolerance)
        elif cmd == "benford":
            env = _json_benford(_parse_values_list(args.values))
        elif cmd == "calc":
            env = _json_calc(args.expr)
        elif cmd == "three-scenario":
            env = _json_three_scenario(args.price, args.eps, args.shares,
                                       args.growth, args.pe, args.years, args.currency)
        else:
            env = _envelope(cmd or "", {}, {}, "ERROR", 2,
                            errors=[{"code": "no_command", "message": "缺少子命令"}])
    except ValueError as e:
        env = _envelope(cmd or "", {}, {}, "ERROR", 2,
                        errors=[{"code": "param_error", "message": str(e)}])
    print(json.dumps(env, ensure_ascii=False))
    sys.exit(env["exit_code"])


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def decimal_arg(text: str) -> Decimal:
    """argparse type: parse numeric CLI input directly as Decimal, never via float.

    拒绝 NaN/Infinity——它们是合法 Decimal，但作为金融输入只会静默产出垃圾结果。
    """
    try:
        d = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"无效数值: {text}")
    if not d.is_finite():
        raise argparse.ArgumentTypeError(f"无效数值 (NaN/Infinity 不接受): {text}")
    return d


_JSON_OPERATIONS = {
    "verify-market-cap", "verify-valuation", "cross-validate",
    "benford", "calc", "three-scenario",
}


class JsonAwareArgumentParser(argparse.ArgumentParser):
    """让 argparse 自身的参数错误也遵守 --json 唯一文档协议。"""

    def error(self, message):
        argv = sys.argv[1:]
        if "--json" in argv:
            operation = next((arg for arg in argv if arg in _JSON_OPERATIONS), "")
            env = _envelope(
                operation, {}, {}, "ERROR", 2,
                errors=[{"code": "argparse_error", "message": message}])
            print(json.dumps(env, ensure_ascii=False))
            raise SystemExit(2)
        super().error(message)


REPLAYABLE_OPERATIONS = {
    "verify-market-cap", "verify-valuation", "cross-validate",
    "benford", "calc", "three-scenario",
}

# three-scenario 的必需参数清单：供派发前门禁在参数错时给 Agent 一份可对照的修复提示。
# 与 main() 中 three-scenario 子解析器的 required 声明保持一致。
_REQUIRED_ARGS_HINT = {
    "three-scenario": ["--price", "--eps", "--shares", "--growth", "--pe"],
    "verify-market-cap": ["--price", "--shares", "--reported"],
    "verify-valuation": ["--price"],
    "cross-validate": ["--field", "--values"],
    "benford": ["--values"],
    "calc": ["--expr"],
}


def build_rigor_argv(operation, args, rigor_script=None):
    """把 (operation, args) 拼成可重放 financial_rigor 的 argv（不含解释器）。

    这是 Gate 派发前参数预校验与 Audit 重放的【唯一】拼装真源：两处共用，
    保证 dry-run 与真执行逐字节同参，杜绝「预校验过、audit 挂」的漂移。
    args 取值规则与历史 audit 行为一致：
    - bool True → 仅追加 flag；False → 省略；
    - list → flag 后逐个展开（three-scenario 的 nargs=3 依赖此形态）；
    - dict → JSON 编码；其余 str()。
    返回 None 表示该 operation 不可重放（调用方应跳过）。
    """
    if operation not in REPLAYABLE_OPERATIONS or not isinstance(args, dict):
        return None
    script = str(rigor_script) if rigor_script is not None else str(
        Path(__file__).resolve())
    command = [sys.executable, script, operation]
    for key, value in args.items():
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                command.append(flag)
        elif isinstance(value, list):
            command.append(flag)
            command.extend(str(item) for item in value)
        else:
            encoded = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else str(value)
            command.extend([flag, encoded])
    return command


def preflight_diagnose_params(operation, args, rigor_script=None):
    """派发前参数预校验：确定性重放一条 calculation 请求，仅拦截【参数错】。

    返回 dict（始终非 None，供调用方判断）：
    - {"ok": True}                    参数合法（含业务不通过 rc=1 —— 放行给 audit 权威判定）；
    - {"ok": False, "rc": 2, ...}     argparse/参数层错误，应在提交当下拒回 Agent；
    - {"ok": True, "skipped": True}   operation 不可重放，门禁不管（交给 audit）。

    语义对齐退出码：0 验证通过 / 1 业务不通过 / 2 参数错误（见本模块顶部协议）。
    本函数只把 rc=2 判为参数错；rc=1 是「算得通但结论不达标」，不属笔误，放行。
    """
    argv = build_rigor_argv(operation, args, rigor_script=rigor_script)
    if argv is None:
        return {"ok": True, "skipped": True}
    completed = subprocess.run(argv, capture_output=True, text=True)
    if completed.returncode == 2:
        # 只保留 argparse 的 "...: error: <具体原因>" 行，丢弃 usage 噪音，
        # 让派发前门禁的回传对 Agent 精准可操作。
        error_lines = [
            line.strip() for line in (completed.stderr or "").splitlines()
            if ": error:" in line
        ]
        return {
            "ok": False,
            "rc": 2,
            "argv": argv,
            "stderr_tail": error_lines[-2:],
            "required_hint": _REQUIRED_ARGS_HINT.get(operation, []),
        }
    return {"ok": True, "rc": completed.returncode}


def main():
    parser = JsonAwareArgumentParser(
        description="Financial Rigor Toolkit — 金融数据严谨性验证工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s verify-market-cap --price 510 --shares 9.11e9 --reported 4.65e12 --currency HKD
  %(prog)s verify-valuation --price 510 --eps 23.5 --bvps 120
  %(prog)s cross-validate --field revenue --values '{"年报": 7518, "Yahoo": 7500}' --unit 亿
  %(prog)s benford --values '[1234, 2345, 3456, ...]'
  %(prog)s calc --expr '510 * 9.11e9'
        """)

    sub = parser.add_subparsers(dest="command",
                                parser_class=JsonAwareArgumentParser)

    # verify-market-cap
    mc = sub.add_parser("verify-market-cap", help="验算市值 = 股价 × 总股本")
    mc.add_argument("--price", type=decimal_arg, required=True)
    mc.add_argument("--shares", type=decimal_arg, required=True, help="总股本")
    mc.add_argument("--reported", type=decimal_arg, required=True, help="报告市值")
    mc.add_argument("--currency", default="", help="币种")

    # verify-valuation
    val = sub.add_parser("verify-valuation", help="验算估值指标")
    val.add_argument("--price", type=decimal_arg, required=True)
    val.add_argument("--eps", type=decimal_arg, default=None)
    val.add_argument("--bvps", type=decimal_arg, default=None, help="每股净资产")
    val.add_argument("--fcf-per-share", type=decimal_arg, default=None)
    val.add_argument("--dividend", type=decimal_arg, default=None, help="每股股息")
    val.add_argument("--revenue-per-share", type=decimal_arg, default=None)

    # cross-validate
    cv = sub.add_parser("cross-validate", help="多源交叉验证")
    cv.add_argument("--field", required=True, help="数据字段名")
    cv.add_argument("--values", required=True, help="JSON: {来源: 数值}")
    cv.add_argument("--unit", default="")
    cv.add_argument("--tolerance", type=decimal_arg, default=Decimal("2.0"), help="容差百分比")

    # benford
    bf = sub.add_parser("benford", help="Benford定律检测")
    bf.add_argument("--values", required=True, help="JSON数组")

    # calc
    ca = sub.add_parser("calc", help="精确计算")
    ca.add_argument("--expr", required=True, help="算术表达式")

    # three-scenario
    ts = sub.add_parser("three-scenario", help="三情景估值")
    ts.add_argument("--price", type=decimal_arg, required=True)
    ts.add_argument("--eps", type=decimal_arg, required=True)
    ts.add_argument("--shares", type=decimal_arg, required=True, help="总股本(亿)")
    ts.add_argument("--growth", nargs=3, type=decimal_arg, required=True,
                    help="三情景年增速 (乐观 中性 悲观), 如 0.15 0.08 0.0")
    ts.add_argument("--pe", nargs=3, type=decimal_arg, required=True,
                    help="三情景目标PE, 如 25 20 15")
    ts.add_argument("--years", type=int, default=3)
    ts.add_argument("--currency", default="")

    for _p in (mc, val, cv, bf, ca, ts):
        _p.add_argument("--json", action="store_true",
                        help="输出结构化 JSON envelope (供 gate 语义重放, 见 §10.2)")

    args = parser.parse_args()

    # --json: 结构化重放协议, 早于默认分发; 默认(非 --json)路径逐字节不变
    if getattr(args, "json", False):
        _emit_json(args)

    # 退出码统一语义: 0 验证通过 / 1 业务不通过或计算失败 / 2 参数错误或证据不足
    if args.command == "verify-market-cap":
        try:
            ok = verify_market_cap(args.price, args.shares, args.reported, args.currency)
        except ValueError as e:
            print(f"❌ 参数错误: {e}")
            sys.exit(2)
        sys.exit(0 if ok else 1)
    elif args.command == "verify-valuation":
        try:
            results = verify_valuation(args.price, args.eps, args.bvps, args.fcf_per_share,
                                       args.dividend, args.revenue_per_share)
        except ValueError as e:
            print(f"❌ 参数错误: {e}")
            sys.exit(2)
        # 恒真修复（候选②）：无任何指标可算（如只给价格）不得按成功退出——
        # 与 --json 路径 INSUFFICIENT/2 对齐（此前无论是否有指标都 exit 0）。
        sys.exit(0 if results else 2)
    elif args.command == "cross-validate":
        try:
            # parse_float=Decimal: JSON 浮点直接进 Decimal, 杜绝 1e999 → inf
            values = json.loads(args.values, parse_float=Decimal)
        except json.JSONDecodeError as e:
            print(f"❌ 参数错误: --values 不是有效 JSON: {e}")
            sys.exit(2)
        if not isinstance(values, dict):
            print(f"❌ 参数错误: --values 必须是 JSON 对象 {{来源: 数值}}, "
                  f"收到 {type(values).__name__}")
            sys.exit(2)
        # bool 是 int 子类, 必须显式排除
        bad = [k for k, v in values.items()
               if isinstance(v, bool) or not isinstance(v, (int, Decimal))]
        if bad:
            print(f"❌ 参数错误: --values 中这些来源的值不是数值: {', '.join(bad)}")
            sys.exit(2)
        try:
            outcome = cross_validate(args.field, values, args.unit, args.tolerance)
        except ValueError as e:
            print(f"❌ 参数错误: {e}")
            sys.exit(2)
        sys.exit(0 if outcome["all_consistent"] else 1)
    elif args.command == "benford":
        try:
            values = json.loads(args.values, parse_float=Decimal)
        except json.JSONDecodeError as e:
            print(f"❌ 参数错误: --values 不是有效 JSON: {e}")
            sys.exit(2)
        if not isinstance(values, list):
            print(f"❌ 参数错误: --values 必须是 JSON 数组, 收到 {type(values).__name__}")
            sys.exit(2)
        bad = [str(v) for v in values
               if isinstance(v, bool) or not isinstance(v, (int, Decimal))]
        if bad:
            print(f"❌ 参数错误: --values 含非数值元素: {', '.join(bad[:5])}")
            sys.exit(2)
        result = benford_check(values)
        if result is None:
            sys.exit(2)  # 样本不足 → 证据不足
        sys.exit(0 if result["is_conforming"] else 1)
    elif args.command == "calc":
        result = exact_calc(args.expr)
        sys.exit(0 if result is not None else 1)
    elif args.command == "three-scenario":
        try:
            three_scenario_valuation(
                args.price, args.eps, args.shares,
                args.growth[0], args.growth[1], args.growth[2],
                args.pe[0], args.pe[1], args.pe[2],
                args.years, args.currency)
        except ValueError as e:
            print(f"❌ 参数错误: {e}")
            sys.exit(2)
    else:
        # 零操作不能报"成功"——裸调用按参数错误处理
        parser.print_help(sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
