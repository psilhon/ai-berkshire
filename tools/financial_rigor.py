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
import sys
from decimal import Decimal, Context, ROUND_HALF_EVEN, InvalidOperation

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

def verify_market_cap(price, shares, reported_cap, currency=""):
    """Verify market cap = price × shares, compare with reported value."""
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

def verify_valuation(price, eps=None, bvps=None, fcf_per_share=None,
                     dividend=None, revenue_per_share=None):
    """Calculate and verify key valuation ratios from raw inputs."""
    p = _require_finite("股价", price)
    if p <= 0:
        raise ValueError(f"股价必须为正数, 收到 {price}")

    print("=" * 60)
    print("估值指标验算 (Valuation Verification)")
    print("=" * 60)
    print(f"  当前股价: {p}")
    print()

    results = {}

    if eps is not None:
        e = _require_finite("EPS", eps)
        if e > 0:
            pe = _CTX.divide(p, e)
            print(f"  PE (TTM):  {p} / {e} = {pe:.2f}x")
            results["PE"] = float(pe)
            # Earnings yield
            ey = _CTX.divide(e, p) * 100
            print(f"  盈利收益率: {ey:.2f}%")
        else:
            print(f"  PE: EPS ≤ 0 (亏损/不适用), 跳过 PE 与盈利收益率")

    if bvps is not None:
        b = _require_finite("每股净资产", bvps)
        if b != 0:
            pb = _CTX.divide(p, b)
            print(f"  PB:        {p} / {b} = {pb:.2f}x")
            results["PB"] = float(pb)
            if eps is not None and exact(eps) != 0:
                roe = _CTX.divide(exact(eps), b) * 100
                print(f"  ROE:       {exact(eps)} / {b} = {roe:.2f}%")
                results["ROE"] = float(roe)
        else:
            print(f"  PB: 每股净资产为0, 无法计算, 跳过")

    if fcf_per_share is not None:
        f = _require_finite("每股FCF", fcf_per_share)
        if f != 0:
            fcf_yield = _CTX.divide(f, p) * 100
            pfcf = _CTX.divide(p, f)
            print(f"  P/FCF:     {p} / {f} = {pfcf:.2f}x")
            print(f"  FCF Yield: {fcf_yield:.2f}%")
            results["P_FCF"] = float(pfcf)
            results["FCF_Yield"] = float(fcf_yield)
        else:
            print(f"  P/FCF: FCF为0, 无法计算, 跳过")

    if dividend is not None:
        d = _require_finite("每股股息", dividend)
        div_yield = _CTX.divide(d, p) * 100
        print(f"  股息率:    {d} / {p} = {div_yield:.2f}%")
        results["Dividend_Yield"] = float(div_yield)

    if revenue_per_share is not None:
        r = _require_finite("每股营收", revenue_per_share)
        if r != 0:
            ps = _CTX.divide(p, r)
            print(f"  PS:        {p} / {r} = {ps:.2f}x")
            results["PS"] = float(ps)
        else:
            print(f"  PS: 每股营收为0, 无法计算, 跳过")

    print()
    print("  ✅ 以上指标均使用精确十进制计算, 无浮点误差")
    return results


# ---------------------------------------------------------------------------
# 3. Cross-Source Data Validation (多源交叉验证)
# ---------------------------------------------------------------------------

def cross_validate(field_name, source_values: dict, unit="", tolerance_pct=2.0):
    """Compare a data point across multiple sources, flag discrepancies."""
    if len(source_values) < 2:
        raise ValueError(
            f"交叉验证至少需要 2 个独立来源, 收到 {len(source_values)} 个"
            f"（项目规则: 关键数据至少 2 个独立来源交叉验证）")

    values = {k: _require_finite(f"来源[{k}]", v) for k, v in source_values.items()}
    tol = _require_finite("容差", tolerance_pct)

    print("=" * 60)
    print(f"交叉验证: {field_name} (Cross-Validation)")
    print("=" * 60)

    sources = list(values.keys())
    nums = list(values.values())

    # Find median as reference — 全程 Decimal, 不经过 float
    sorted_vals = sorted(nums)
    n = len(sorted_vals)
    if n % 2 == 1:
        median = sorted_vals[n // 2]
    else:
        median = _CTX.divide(_CTX.add(sorted_vals[n//2-1], sorted_vals[n//2]), Decimal("2"))

    if median == 0:
        raise ValueError(f"{field_name} 的中位数为 0, 无法计算相对偏差")

    print(f"  数据来源数: {len(sources)}")
    print(f"  参考中位数: {fmt_number(median)} {unit}")
    print()

    all_ok = True
    for src, val in values.items():
        dev = _CTX.divide(abs(val - median), abs(median)) * 100
        status = "✅" if dev <= tol else "❌"
        if dev > tol:
            all_ok = False
        print(f"  {status} {src:20s}: {fmt_number(val)} {unit}  (偏差 {dev:.2f}%)")

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


def benford_check(values: list):
    """Quick Benford's Law check on a list of financial values."""
    print("=" * 60)
    print("Benford定律检测 (Financial Data Fabrication Check)")
    print("=" * 60)

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
        print(f"  ⚠️  样本量不足: {n} < 50, Benford分析不可靠")
        return None

    # Observed distribution
    counts = {}
    for d in digits:
        counts[d] = counts.get(d, 0) + 1
    observed = {d: counts.get(d, 0) / n for d in range(1, 10)}

    # MAD (Nigrini's Mean Absolute Deviation)
    mad = sum(abs(observed.get(d, 0) - _BENFORD[d]) for d in range(1, 10)) / 9

    # Chi-square
    chi2 = sum((counts.get(d, 0) - _BENFORD[d] * n) ** 2 / (_BENFORD[d] * n) for d in range(1, 10))

    # Conformity
    if mad < 0.006:
        conformity = "Close (高度符合)"
    elif mad < 0.012:
        conformity = "Acceptable (可接受)"
    elif mad < 0.015:
        conformity = "Marginally Acceptable (边缘)"
    else:
        conformity = "Nonconforming (不符合 ⚠️)"

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
    is_ok = mad < 0.015
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


def exact_calc(expr: str):
    """Evaluate a financial expression with exact decimal arithmetic.

    Supports: +, -, *, /, (), numbers (including scientific notation).
    """
    print("=" * 60)
    print("精确计算 (Exact Calculator)")
    print("=" * 60)

    # Safe evaluation: only allow numbers and arithmetic
    allowed = set("0123456789.+-*/() eE")
    if not all(c in allowed for c in expr.replace(" ", "")):
        print(f"  ❌ 不安全的表达式: {expr}")
        return None

    try:
        # Wrap each numeric literal (incl. scientific notation) in Decimal(...)
        # so evaluation never touches binary floats
        dec_expr = _NUMBER_RE.sub(r"Decimal('\g<0>')", expr)
        result = eval(dec_expr, {"__builtins__": {}}, {"Decimal": Decimal})
        d_result = exact(result)
        print(f"  表达式: {expr}")
        print(f"  结果:   {fmt_number(d_result)}")
        print(f"  精确值: {d_result}")
        return d_result
    except Exception as e:
        print(f"  ❌ 计算错误: {e}")
        return None


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


def main():
    parser = argparse.ArgumentParser(
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

    sub = parser.add_subparsers(dest="command")

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

    args = parser.parse_args()

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
            verify_valuation(args.price, args.eps, args.bvps, args.fcf_per_share,
                             args.dividend, args.revenue_per_share)
        except ValueError as e:
            print(f"❌ 参数错误: {e}")
            sys.exit(2)
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
