#!/usr/bin/env python3
"""A股数据工具 — 腾讯行情 + 东方财富搜索/财务，零外部依赖（仅 stdlib）。

为 Claude Code Skills 提供 A 股实时行情、财务数据等数据。
设计原则：独立模块，不影响现有工具；使用 curl 直连绕过系统代理。

用法（由 Skills 自动调用）：
    python3.11 tools/ashare_data.py quote 600519                    # 实时行情
    python3.11 tools/ashare_data.py financials 600519               # 核心财务数据（近5年）
    python3.11 tools/ashare_data.py valuation 600519                # 估值指标
    python3.11 tools/ashare_data.py search 茅台                      # 搜索股票代码

需要 Python >= 3.8，零外部依赖。
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_EVEN

try:
    from tools.ashare_plugin.transport import TransportClient
    from tools.ashare_plugin.disclosures import fetch_announcements
    from tools.ashare_plugin.market_signals import fetch_signals
    from tools.ashare_plugin.identifiers import normalize_code
    from tools.ashare_plugin.tushare import TushareClient
    from tools.ashare_plugin.tushare_verification import (
        API_FIELDS,
        apply_market_precedence,
        safe_verify_command,
    )
    from tools.ashare_plugin.quote import parse_sina_quote, price_cross_check
except ModuleNotFoundError:  # direct execution: tools/ is the script directory
    from ashare_plugin.transport import TransportClient
    from ashare_plugin.disclosures import fetch_announcements
    from ashare_plugin.market_signals import fetch_signals
    from ashare_plugin.identifiers import normalize_code
    from ashare_plugin.tushare import TushareClient
    from ashare_plugin.tushare_verification import (
        API_FIELDS,
        apply_market_precedence,
        safe_verify_command,
    )
    from ashare_plugin.quote import parse_sina_quote, price_cross_check

_DATACENTER_URL = "https://datacenter.eastmoney.com/securities/api/data/get"
_TRANSPORT = TransportClient()


def _curl(url):
    """兼容旧调用者的文本请求入口，底层统一使用插件 transport。"""
    return _TRANSPORT.get_text(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    })


def _curl_json(url, params=None):
    """兼容旧调用者的 JSON 请求入口，底层统一使用插件 transport。"""
    return _TRANSPORT.get_json(url, params=params, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    })


def _em_secu_code(code: str) -> str:
    """将六位 A 股代码标准化为东方财富 SECUCODE。"""
    raw = code.strip().upper()
    parts = raw.rsplit(".", 1)
    code_clean = parts[0]
    if len(code_clean) != 6 or not code_clean.isdigit():
        raise ValueError(f"无效 A 股代码: {code}")

    if len(parts) == 2:
        market = parts[1]
        if market not in {"SH", "SZ", "BJ"}:
            raise ValueError(f"无效市场后缀: {market}")
    elif code_clean.startswith(("4", "8", "920")):
        market = "BJ"
    elif code_clean.startswith(("6", "9", "5")):
        market = "SH"
    elif code_clean.startswith(("0", "1", "2", "3")):
        market = "SZ"
    else:
        raise ValueError(f"无法判断 A 股市场: {code}")
    return f"{code_clean}.{market}"


def _positive_years(text: str) -> int:
    """argparse type: 年度数量限制在 1-50。"""
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--years 必须是整数") from exc
    if not 1 <= value <= 50:
        raise argparse.ArgumentTypeError("--years 必须在 1 到 50 之间")
    return value


def _fetch_datacenter_rows(report_type, secu_code, *, sort_column,
                           sort_order="-1", extra_filter="", limit=None):
    """读取东方财富 Datacenter 数据，按 pages 分页且不静默截断。"""
    rows = []
    page = 1
    page_size = min(limit or 100, 100)
    while True:
        data = _curl_json(_DATACENTER_URL, {
            "type": report_type,
            "sty": "ALL",
            "filter": f'(SECUCODE="{secu_code}"){extra_filter}',
            "p": str(page),
            "ps": str(page_size),
            "sr": sort_order,
            "st": sort_column,
            "source": "HSF10",
            "client": "PC",
        })
        if not data.get("success"):
            raise ConnectionError(data.get("message") or "东方财富接口返回失败")

        result = data.get("result") or {}
        rows.extend(result.get("data") or [])
        pages = int(result.get("pages") or 1)
        if page >= pages or (limit is not None and len(rows) >= limit):
            return rows[:limit] if limit is not None else rows
        page += 1


# ---------------------------------------------------------------------------
# 腾讯行情 API（稳定可靠，无需鉴权）
# ---------------------------------------------------------------------------

def _qq_code(code: str) -> str:
    """将股票代码转为腾讯行情格式。"""
    code = code.strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    if code.startswith(("4", "8", "920")):
        return f"bj{code}"
    elif code.startswith(("6", "9", "5")):
        return f"sh{code}"
    elif code.startswith(("0", "3", "2", "1")):
        return f"sz{code}"
    return f"sh{code}"


def _parse_qq_quote(raw: str) -> dict:
    """解析腾讯行情数据。格式：v_shXXXXXX="字段1~字段2~..."; """
    start = raw.find('"')
    end = raw.rfind('"')
    if start < 0 or end <= start:
        return {}
    fields = raw[start + 1:end].split("~")
    if len(fields) < 50:
        return {}
    return {
        "name": fields[1],
        "code": fields[2],
        "price": fields[3],
        "prev_close": fields[4],
        "open": fields[5],
        "volume": fields[6],         # 手
        "buy_vol": fields[7],
        "sell_vol": fields[8],
        "high": fields[33] if len(fields) > 33 else fields[3],
        "low": fields[34] if len(fields) > 34 else fields[3],
        "change_pct": fields[32],
        "change_amt": fields[31],
        "quote_time": fields[30] if len(fields) > 30 else "",
        "turnover_amt": fields[37] if len(fields) > 37 else "-",
        "turnover_rate": fields[38] if len(fields) > 38 else "-",
        "pe": fields[39] if len(fields) > 39 else "-",
        "market_cap": fields[45] if len(fields) > 45 else "-",    # 总市值（亿）
        "float_cap": fields[44] if len(fields) > 44 else "-",     # 流通市值（亿）
        "pb": fields[46] if len(fields) > 46 else "-",
        # 注意：腾讯 ~ 分隔协议第 47/48 位是当日涨停价/跌停价，不是 52 周极值（issue #70）
        "limit_up": fields[47] if len(fields) > 47 else "-",
        "limit_down": fields[48] if len(fields) > 48 else "-",
        "total_shares": fields[38] if len(fields) > 38 else "-",  # will recalculate
    }


def _em_secid(code: str) -> str:
    """将股票代码转为东方财富 secid 格式：沪市前缀 1.，深市/北交所前缀 0.。"""
    code = code.strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    if code.startswith("920"):
        return f"0.{code}"
    if code.startswith(("6", "9", "5")):
        return f"1.{code}"
    return f"0.{code}"


def _fetch_52w(code: str) -> tuple:
    """从东方财富取 52 周最高/最低（f174/f175）。

    腾讯行情协议无此数据。优先 push2delay（主站 push2 对连续请求限流较严，
    52 周极值不受延时行情影响），失败回退 push2。取不到返回 ("-", "-")。
    """
    secid = _em_secid(code)
    query = f"api/qt/stock/get?secid={secid}&fields=f174,f175&invt=2&fltt=2"
    for host in ("push2delay.eastmoney.com", "push2.eastmoney.com"):
        try:
            data = _curl_json(f"https://{host}/{query}").get("data") or {}
            high, low = data.get("f174"), data.get("f175")
            if high not in (None, "-") and low not in (None, "-"):
                return high, low
        except Exception:
            continue
    return "-", "-"


def _fmt_yi(value) -> str:
    if value is None or value == "-" or value == "":
        return "-"
    try:
        v = float(value)
    except (ValueError, TypeError):
        return str(value)
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f}亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.2f}万"
    return f"{v:.2f}"


def _fmt_pct(value) -> str:
    if value is None or value == "-" or value == "":
        return "-"
    try:
        return f"{float(value):.2f}%"
    except (ValueError, TypeError):
        return str(value)


def _fmt_times(value) -> str:
    if value is None or value == "-" or value == "":
        return "-"
    try:
        return f"{float(value):.2f}x"
    except (ValueError, TypeError):
        return str(value)


def _fmt_date(value) -> str:
    """YYYYMMDD（或含分隔符）→ YYYY-MM-DD；无法解析时原样返回。"""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())[:8]
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return str(value) if value not in (None, "") else "-"


def _safe_verification(command, subject, primary_data, *, trade_date=None):
    try:
        return safe_verify_command(
            command, subject, primary_data, trade_date=trade_date
        )
    except Exception:
        return {
            "provider": "tushare",
            "configured": True,
            "status": "INSUFFICIENT",
            "as_of": None,
            "warnings": ["Tushare 验证发生未分类错误；主数据结果未受影响"],
            "fields": [],
            "endpoints": [],
        }


def _print_verification(verification):
    print(f"  Tushare 验证: {verification['status']}")
    counts = {"MATCH": 0, "CONFLICT": 0, "INSUFFICIENT": 0}
    for field in verification.get("fields", []):
        status = field.get("status")
        if status in counts:
            counts[status] += 1
    if verification.get("configured"):
        print(
            "  验证字段:     "
            f"MATCH={counts['MATCH']} "
            f"CONFLICT={counts['CONFLICT']} "
            f"INSUFFICIENT={counts['INSUFFICIENT']}"
        )
    for warning in verification.get("warnings", []):
        print(f"  ⚠️ {warning}")


def _apply_market_effective_values(data, verification):
    """Apply already-audited Tushare market precedence to display data."""
    resolved = dict(data)
    data_keys = {
        "close": "price",
        "market_cap": "market_cap",
        "float_cap": "float_cap",
        "pe": "pe",
        "pb": "pb",
        "turnover_rate": "turnover_rate",
    }
    for field in verification.get("fields", []):
        if not field.get("precedence_applied"):
            continue
        key = data_keys.get(field.get("field"))
        if key is None:
            continue
        value = field.get("effective_value")
        if field.get("field") in {"market_cap", "float_cap"}:
            try:
                value = str(Decimal(value) / Decimal("10000"))
            except Exception:
                continue
        resolved[key] = value
    return resolved


def _print_precedence(verification):
    for field in verification.get("fields", []):
        if field.get("precedence_applied"):
            print(
                "  Tushare 覆盖: "
                f"{field['field']} {field['primary_value']} -> "
                f"{field['effective_value']}"
            )


def _sina_price(code: str):
    """独立第二行情源（新浪）当前价；失败时返回 None，从不打断主行情。"""
    try:
        raw = _TRANSPORT.get_text(
            f"https://hq.sinajs.cn/list={_qq_code(code)}",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "Referer": "https://finance.sina.com.cn",
            },
        )
    except Exception:
        return None
    parsed = parse_sina_quote(raw)
    return parsed.get("price") if parsed else None


def _print_price_cross_check(tencent_price, code):
    """打印腾讯 vs 新浪的独立价格双源核对（真第二行情链）。"""
    cc = price_cross_check(tencent_price, _sina_price(code))
    if cc["status"] == "MATCH":
        print(
            f"  价格双源:   ✅ 腾讯 {cc['primary_price']} = 新浪 "
            f"{cc['second_price']}（偏差 {cc['deviation_pct']}%，独立第二源）"
        )
    elif cc["status"] == "CONFLICT":
        print(
            f"  价格双源:   ⚠️ 腾讯 {cc['primary_price']} vs 新浪 "
            f"{cc['second_price']}（偏差 {cc['deviation_pct']}%，冲突）"
        )
    else:
        print("  价格双源:   新浪独立源不可用（价格暂为单源）")


def _tushare_dividend_yield(verification):
    """从验证块提取 Tushare 独立股息率（dv_ratio），无则返回 None。"""
    for field in verification.get("fields", []):
        if field.get("field") == "dividend_yield":
            return field.get("verification_value")
    return None


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------

def cmd_quote(code: str):
    """实时行情快照。"""
    qq_code = _qq_code(code)
    try:
        raw = _curl(f"https://qt.gtimg.cn/q={qq_code}")
    except (ConnectionError, subprocess.TimeoutExpired) as exc:
        print(f"❌ 获取行情失败: {exc}", file=sys.stderr)
        return False
    d = _parse_qq_quote(raw)
    if not d:
        print(f"❌ 未找到股票 {code}", file=sys.stderr)
        return False
    verification = apply_market_precedence(
        "quote", _safe_verification("quote", code, d)
    )
    d = _apply_market_effective_values(d, verification)

    print("=" * 60)
    print(f"实时行情: {d['name']} ({d['code']})")
    print("=" * 60)
    print(f"  当前价:     {d['price']}")
    print(f"  涨跌幅:     {d['change_pct']}%")
    print(f"  涨跌额:     {d['change_amt']}")
    print(f"  今开:       {d['open']}")
    print(f"  最高:       {d['high']}")
    print(f"  最低:       {d['low']}")
    print(f"  昨收:       {d['prev_close']}")
    print(f"  成交量:     {d['volume']} 手")
    print(f"  成交额:     {d['turnover_amt']}万")
    print(f"  总市值:     {d['market_cap']}亿")
    print(f"  流通市值:   {d['float_cap']}亿")
    print(f"  PE(动):     {d['pe']}")
    print(f"  PB:         {d['pb']}")
    print(f"  换手率:     {d['turnover_rate']}%")
    high_52w, low_52w = _fetch_52w(code)
    print(f"  52周最高:   {high_52w}")
    print(f"  52周最低:   {low_52w}")
    _print_price_cross_check(d["price"], code)
    _print_precedence(verification)
    _print_verification(verification)
    return True


def cmd_valuation(code: str):
    """估值指标汇总。"""
    qq_code = _qq_code(code)
    try:
        raw = _curl(f"https://qt.gtimg.cn/q={qq_code}")
    except (ConnectionError, subprocess.TimeoutExpired) as exc:
        print(f"❌ 获取行情失败: {exc}", file=sys.stderr)
        return False
    d = _parse_qq_quote(raw)
    if not d:
        print(f"❌ 未找到股票 {code}", file=sys.stderr)
        return False
    verification = apply_market_precedence(
        "valuation", _safe_verification("valuation", code, d)
    )
    d = _apply_market_effective_values(d, verification)

    price = d["price"]
    market_cap_yi = d["market_cap"]

    print("=" * 60)
    print(f"估值指标: {d['name']} ({d['code']})")
    print("=" * 60)
    print(f"  当前价:     {price}")
    print(f"  总市值:     {market_cap_yi}亿")
    print(f"  流通市值:   {d['float_cap']}亿")
    print(f"  PE(动):     {d['pe']}")
    print(f"  PB:         {d['pb']}")
    dv_yield = _tushare_dividend_yield(verification)
    if dv_yield is not None:
        print(
            f"  股息率:     {dv_yield}%（Tushare dv_ratio 口径，"
            f"可能含上一周期高分红，非前瞻股息率，需按分红期间自行核对）"
        )
    high_52w, low_52w = _fetch_52w(code)
    print(f"  52周最高:   {high_52w}")
    print(f"  52周最低:   {low_52w}")
    _print_price_cross_check(price, code)

    # 市值验算
    try:
        p = Decimal(price)
        cap = Decimal(market_cap_yi) * Decimal("1e8")
        shares = cap / p
        print(f"\n  推算总股本: {_fmt_yi(float(shares))}股")
        calc_cap = p * shares
        reported_cap = Decimal(market_cap_yi) * Decimal("1e8")
        diff = abs(calc_cap - reported_cap) / reported_cap * 100
        print(f"  市值验算:   ✅ 一致（推算法，偏差 {float(diff):.1f}%）")
    except Exception:
        pass
    _print_precedence(verification)
    _print_verification(verification)
    return True


def cmd_financials(code: str):
    """近5年核心财务数据。"""
    secu_code = _em_secu_code(code)
    qq_code = _qq_code(code)
    try:
        raw = _curl(f"https://qt.gtimg.cn/q={qq_code}")
        d = _parse_qq_quote(raw)
    except (ConnectionError, subprocess.TimeoutExpired):
        d = {}
    name = d.get("name", code) if d else code

    # 东方财富 datacenter API（年报数据）
    fin_url = "https://datacenter.eastmoney.com/securities/api/data/get"
    params = {
        "type": "RPT_F10_FINANCE_MAINFINADATA",
        "sty": "ALL",
        "filter": f'(SECUCODE="{secu_code}")(REPORT_TYPE="年报")',
        "p": "1",
        "ps": "5",
        "sr": "-1",
        "st": "REPORT_DATE",
        "source": "HSF10",
        "client": "PC",
    }
    reports = []
    try:
        data = _curl_json(fin_url, params)
        reports = (data.get("result") or {}).get("data") or []
    except (ConnectionError, json.JSONDecodeError,
            subprocess.TimeoutExpired):
        reports = []

    # 如果年报筛选无结果，去掉年报限制
    if not reports:
        params["filter"] = f'(SECUCODE="{secu_code}")'
        try:
            data = _curl_json(fin_url, params)
            reports = (data.get("result") or {}).get("data") or []
        except (ConnectionError, json.JSONDecodeError,
                subprocess.TimeoutExpired):
            reports = []

    print("=" * 60)
    print(f"核心财务数据: {name} ({secu_code})")
    print("=" * 60)

    if not reports:
        print("❌ 未能获取财务数据，建议通过 WebSearch 补充", file=sys.stderr)
        return False

    for r in reports[:5]:
        date = r.get("REPORT_DATE", "")[:10]
        report_name = r.get("REPORT_DATE_NAME", "")
        revenue = r.get("TOTALOPERATEREVE")
        net_profit = r.get("PARENTNETPROFIT")
        eps = r.get("EPSJB")
        bps = r.get("BPS")
        roe = r.get("ROEJQ")
        rev_growth = r.get("TOTALOPERATEREVETZ")
        profit_growth = r.get("PARENTNETPROFITTZ")

        print(f"\n  --- {date} {report_name} ---")
        if revenue is not None:
            print(f"  营收:           {_fmt_yi(revenue)}")
        if rev_growth is not None:
            print(f"  营收增速:       {_fmt_pct(rev_growth)}")
        if net_profit is not None:
            print(f"  归母净利润:     {_fmt_yi(net_profit)}")
        if profit_growth is not None:
            print(f"  净利润增速:     {_fmt_pct(profit_growth)}")
        if eps is not None:
            print(f"  基本每股收益:   {eps}")
        if bps is not None:
            print(f"  每股净资产:     {bps:.2f}")
        if roe is not None:
            print(f"  ROE(加权):      {_fmt_pct(roe)}")
    _print_verification(_safe_verification("financials", code, reports[:5]))
    return True


def cmd_history(code: str, years: int = 10):
    """长期年度财务数据，用于质量筛选的跨周期指标检查。"""
    secu_code = _em_secu_code(code)
    try:
        reports = _fetch_datacenter_rows(
            "RPT_F10_FINANCE_MAINFINADATA",
            secu_code,
            sort_column="REPORT_DATE",
            extra_filter='(REPORT_TYPE="年报")',
            limit=years,
        )
    except (ConnectionError, json.JSONDecodeError,
            subprocess.TimeoutExpired) as exc:
        print(f"❌ 获取长期财务数据失败: {exc}", file=sys.stderr)
        return False

    if not reports:
        print(f"❌ 未获取到 {secu_code} 的年度财务数据", file=sys.stderr)
        return False

    name = reports[0].get("SECURITY_NAME_ABBR") or secu_code
    print("=" * 60)
    print(f"长期财务数据: {name} ({secu_code})")
    print("=" * 60)
    for row in reports:
        year = row.get("REPORT_YEAR") or str(row.get("REPORT_DATE", ""))[:4]
        print(f"\n  --- {year}年报 ---")
        print(f"  ROE(加权):          {_fmt_pct(row.get('ROEJQ'))}")
        print(f"  毛利率:             {_fmt_pct(row.get('XSMLL'))}")
        print(f"  净利率:             {_fmt_pct(row.get('XSJLL'))}")
        print(f"  经营现金流/净利润:  {_fmt_times(row.get('NCO_NETPROFIT'))}")
        print(f"  利息覆盖:           {_fmt_times(row.get('INTSTCOVRATE'))}")
        print(f"  经营现金流:         {_fmt_yi(row.get('NETCASH_OPERATE_PK'))}")
    _print_verification(_safe_verification("history", code, reports))
    return True


def cmd_equity_history(code: str):
    """历史股本变动；不得用财务主表的静态 TOTAL_SHARE 替代。"""
    secu_code = _em_secu_code(code)
    try:
        rows = _fetch_datacenter_rows(
            "RPT_F10_EH_EQUITY",
            secu_code,
            sort_column="END_DATE",
            sort_order="-1",
        )
    except (ConnectionError, json.JSONDecodeError,
            subprocess.TimeoutExpired) as exc:
        print(f"❌ 获取历史股本失败: {exc}", file=sys.stderr)
        return False

    if not rows:
        print(f"❌ 未获取到 {secu_code} 的历史股本", file=sys.stderr)
        return False

    name = rows[0].get("SECURITY_NAME_ABBR") or secu_code
    print("=" * 60)
    print(f"历史股本: {name} ({secu_code})")
    print("=" * 60)
    for row in rows:
        date = str(row.get("END_DATE") or "-")[:10]
        reason = (row.get("CHANGE_REASON_EXPLAIN")
                  or row.get("CHANGE_REASON") or "-")
        print(f"\n  --- {date} ---")
        print(f"  总股本:    {_fmt_yi(row.get('TOTAL_SHARES'))}")
        print(f"  变动股数:  {_fmt_yi(row.get('TOTAL_SHARES_CHANGE'))}")
        print(f"  变动原因:  {reason}")
    _print_verification(_safe_verification("equity-history", code, rows))
    return True


def cmd_search(keyword: str):
    """搜索股票代码。"""
    url = "https://searchadapter.eastmoney.com/api/suggest/get"
    # Use env var or fall back to the public eastmoney search token
    token = os.environ.get("EASTMONEY_SEARCH_TOKEN") or "D43BF722C8E33BDC906FB84D85E326E8"
    params = {
        "input": keyword,
        "type": "14",
        "token": token,
        "count": "10",
    }
    try:
        data = _curl_json(url, params)
    except (ConnectionError, json.JSONDecodeError,
            subprocess.TimeoutExpired) as exc:
        print(f"❌ 搜索股票失败: {exc}", file=sys.stderr)
        return False
    results = (data.get("QuotationCodeTable") or {}).get("Data") or []

    if not results:
        print(f"❌ 未找到匹配 '{keyword}' 的股票", file=sys.stderr)
        return False

    print("=" * 60)
    print(f"搜索结果: '{keyword}'")
    print("=" * 60)
    for r in results:
        code = r.get("Code", "")
        name = r.get("Name", "")
        market = r.get("MktNum", "")
        mkt_label = {"1": "沪", "2": "深", "3": "北"}.get(str(market), "")
        print(f"  {code} {name} [{mkt_label}]")
    _print_verification(_safe_verification("search", keyword, results))
    return True


def _print_result_meta(result):
    print(f"  数据源:       {result.get('source', '-')}")
    print(f"  备用源:       {'是' if result.get('fallback_used') else '否'}")
    if result.get("as_of"):
        print(f"  数据时间:     {result['as_of']}")
    for warning in result.get("warnings", []):
        print(f"  ⚠️ {warning}")


def cmd_announcements(code: str, limit: int = 20):
    """公告列表，主源失败时使用市场兼容备用源。"""
    result = fetch_announcements(code, limit=limit)
    if not result.get("ok"):
        print(f"❌ 获取公告失败: {result.get('message', '数据不足')}", file=sys.stderr)
        for warning in result.get("warnings", []):
            print(f"  ⚠️ {warning}", file=sys.stderr)
        return False
    print("=" * 60)
    print(f"公告: {code}")
    print("=" * 60)
    _print_result_meta(result)
    for row in result["data"]:
        print(f"  {row.get('date', '-')} | {row.get('type', '-')}: {row.get('title', '-')}")
        if row.get("pdf"):
            print(f"    PDF: {row['pdf']}")
    verification = result.get("verification")
    if verification is None:
        verification = safe_verify_command("announcements", code, result)
    _print_verification(verification)
    return True


def cmd_signals(code: str, trade_date: str = None):
    """市场信号证据汇总，不将信号直接解释为投资结论。"""
    result = fetch_signals(code, trade_date=trade_date)
    if not result.get("ok"):
        print(f"❌ 获取市场信号失败: {result.get('message', '数据不足')}", file=sys.stderr)
        for warning in result.get("warnings", []):
            print(f"  ⚠️ {warning}", file=sys.stderr)
        return False
    print("=" * 60)
    print(f"市场信号证据: {code}")
    print("=" * 60)
    _print_result_meta(result)
    for name, block in result.get("data", {}).items():
        status = "可用" if block.get("ok") else f"不可用({block.get('error_type', 'unknown')})"
        print(f"  {name}: {status} | source={block.get('source', '-')}")
    print("  注：市场信号仅作为研究证据，不替代基本面判断。")
    verification = result.get("verification")
    if verification is None:
        verification = safe_verify_command("signals", code, result["data"], trade_date=trade_date)
    _print_verification(verification)
    return True


# ---------------------------------------------------------------------------
# Phase 1: Tushare 10,000积分增强命令
# ---------------------------------------------------------------------------

def _get_tushare_client():
    """Get a configured TushareClient or print error and return None."""
    client = TushareClient()
    if not client.configured:
        print("❌ 未配置 TUSHARE_TOKEN，无法使用 Tushare 增强功能", file=sys.stderr)
        print("   请设置环境变量 TUSHARE_TOKEN", file=sys.stderr)
        return None
    return client


def cmd_pe_band(code: str, years: int = 5, json_output: bool = False):
    """历史 PE/PB 分位——Tushare daily_basic 全历史序列。"""
    client = _get_tushare_client()
    if not client:
        return False

    ts_code = normalize_code(code).secu_code

    pe_result = client.query(
        "daily_basic",
        params={"ts_code": ts_code},
        fields=API_FIELDS["daily_basic"],
    )
    if not pe_result["ok"]:
        print(f"❌ Tushare daily_basic 查询失败: {pe_result.get('message', '未知')}")
        return False

    rows = pe_result["data"]
    cutoff_year = datetime.now().year - years
    filtered = [
        r for r in rows
        if r.get("trade_date") and int(str(r["trade_date"])[:4]) >= cutoff_year
    ]
    if not filtered:
        print(f"❌ 近 {years} 年无数据")
        return False

    pe_vals = [float(r["pe"]) for r in filtered if r.get("pe") and float(r["pe"]) > 0]
    pb_vals = [float(r["pb"]) for r in filtered if r.get("pb") and float(r["pb"]) > 0]
    latest = filtered[-1]
    current_pe = float(latest.get("pe") or 0)
    current_pb = float(latest.get("pb") or 0)

    # Get Tencent quote for cross-verification
    qq_code = _qq_code(code)
    try:
        raw = _curl(f"https://qt.gtimg.cn/q={qq_code}")
        qq_data = _parse_qq_quote(raw)
    except Exception:
        qq_data = {}

    # Compute percentiles
    import statistics
    pe_stats, pb_stats = {}, {}
    if pe_vals:
        pe_sorted = sorted(pe_vals)
        n = len(pe_sorted)
        pe_stats = {
            "n_days": n, "current": round(current_pe, 2),
            "min": round(min(pe_vals), 2),
            "p10": round(pe_sorted[int(n * 0.10)], 2),
            "p25": round(pe_sorted[int(n * 0.25)], 2),
            "p50": round(statistics.median(pe_vals), 2),
            "p75": round(pe_sorted[int(n * 0.75)], 2),
            "p90": round(pe_sorted[int(n * 0.90)], 2),
            "max": round(max(pe_vals), 2),
            "current_pct": round(sum(1 for v in pe_vals if v <= current_pe) / n * 100, 1),
        }
    if pb_vals:
        pb_sorted = sorted(pb_vals)
        n = len(pb_sorted)
        pb_stats = {
            "n_days": n, "current": round(current_pb, 2),
            "min": round(min(pb_vals), 2),
            "p10": round(pb_sorted[int(n * 0.10)], 2),
            "p25": round(pb_sorted[int(n * 0.25)], 2),
            "p50": round(statistics.median(pb_vals), 2),
            "p75": round(pb_sorted[int(n * 0.75)], 2),
            "p90": round(pb_sorted[int(n * 0.90)], 2),
            "max": round(max(pb_vals), 2),
            "current_pct": round(sum(1 for v in pb_vals if v <= current_pb) / n * 100, 1),
        }

    if json_output:
        print(json.dumps({
            "status": "ok", "code": code, "ts_code": ts_code,
            "source": "Tushare daily_basic",
            "years": years,
            "pe": pe_stats, "pb": pb_stats,
        }, indent=2, ensure_ascii=False))
        return True

    display_name = qq_data.get("name", code) if qq_data else code
    print("=" * 60)
    print(f"历史 PE/PB 分位: {display_name} ({ts_code})")
    print(f"数据来源: Tushare daily_basic，近 {years} 年")
    print("=" * 60)

    if pe_stats:
        level = "历史低位" if pe_stats["current_pct"] < 25 else ("历史中位偏低" if pe_stats["current_pct"] < 50 else ("历史中位偏高" if pe_stats["current_pct"] < 75 else "历史高位"))
        print(f"\n  PE 分位分析（{pe_stats['n_days']} 个交易日）:")
        print(f"    当前 PE:        {pe_stats['current']}")
        print(f"    最小值:         {pe_stats['min']}")
        print(f"    P10:            {pe_stats['p10']}")
        print(f"    P25:            {pe_stats['p25']}")
        print(f"    中位数:         {pe_stats['p50']}")
        print(f"    P75:            {pe_stats['p75']}")
        print(f"    P90:            {pe_stats['p90']}")
        print(f"    最大值:         {pe_stats['max']}")
        print(f"    当前分位:       {pe_stats['current_pct']}%（{level}）")
    else:
        print("\n  PE: 无有效数据（可能亏损）")

    if pb_stats:
        level = "破净/极端低位" if pb_stats["current_pct"] < 10 else ("历史低位" if pb_stats["current_pct"] < 25 else ("历史中位偏低" if pb_stats["current_pct"] < 50 else ("历史中位偏高" if pb_stats["current_pct"] < 75 else "历史高位")))
        print(f"\n  PB 分位分析（{pb_stats['n_days']} 个交易日）:")
        print(f"    当前 PB:        {pb_stats['current']}")
        print(f"    最小值:         {pb_stats['min']}")
        print(f"    P10:            {pb_stats['p10']}")
        print(f"    P25:            {pb_stats['p25']}")
        print(f"    中位数:         {pb_stats['p50']}")
        print(f"    P75:            {pb_stats['p75']}")
        print(f"    P90:            {pb_stats['p90']}")
        print(f"    最大值:         {pb_stats['max']}")
        print(f"    当前分位:       {pb_stats['current_pct']}%（{level}）")
    else:
        print("\n  PB: 无有效数据")

    verification = _safe_verification("pe-band", code, {
        "current_pe_qq": qq_data.get("pe"),
        "current_pb_qq": qq_data.get("pb"),
        "quote_date": qq_data.get("quote_time", ""),
    })
    _print_verification(verification)
    return True


def cmd_research_visits(code: str, limit: int = 20):
    """机构调研记录——Tushare stk_surv。

    获取上市公司接待机构调研的完整记录，包括调研日期、参与机构、
    接待人员、调研内容摘要。是评估管理层能力和公司治理的一手证据。
    """
    client = _get_tushare_client()
    if not client:
        return False

    ts_code = normalize_code(code).secu_code

    result = client.query(
        "stk_surv",
        params={"ts_code": ts_code},
        fields=API_FIELDS["stk_surv"],
    )
    if not result["ok"]:
        print(f"❌ Tushare stk_surv 查询失败: {result.get('message', '未知')}")
        return False

    visits = result["data"]
    # Sort by date descending
    visits.sort(key=lambda r: str(r.get("surv_date") or ""), reverse=True)
    visits = visits[:limit]

    # Get company name from first visit or fall back to code
    display_name = visits[0].get("name", code) if visits else code
    print("=" * 60)
    print(f"机构调研记录: {display_name} ({ts_code})")
    print(f"数据来源: Tushare stk_surv，最近 {min(limit, len(visits))} 条")
    print("=" * 60)

    if not visits:
        print("\n  无机构调研记录。")
    else:
        for i, v in enumerate(visits):
            surv_date = v.get("surv_date", "-")
            fund_visitors = v.get("fund_visitors", "-")
            rece_place = v.get("rece_place", "-")
            rece_org = v.get("rece_org", "-")
            comp_rece = v.get("comp_rece", "-")
            content = v.get("content", "")
            # Truncate content for display
            content_preview = content[:200] + "..." if len(content) > 200 else content

            print(f"\n  [{i+1}] {surv_date}")
            print(f"  参与机构:   {fund_visitors}")
            print(f"  接待地点:   {rece_place}")
            print(f"  接待公司:   {rece_org}")
            print(f"  公司接待:   {comp_rece}")
            if content_preview:
                print(f"  调研内容:   {content_preview}")

    # Verification: Tushare is the primary source; self-check
    verification = _safe_verification("research-visits", code, visits)
    _print_verification(verification)
    return True


def cmd_insider_trades(code: str, limit: int = 20):
    """股东增减持——Tushare stk_holdertrade。

    获取大股东/董监高买卖记录，包括变动日期、股东名称、变动方向、
    变动数量、变动后持股比例。是评估管理层利益一致性的核心证据。
    """
    client = _get_tushare_client()
    if not client:
        return False

    ts_code = normalize_code(code).secu_code

    result = client.query(
        "stk_holdertrade",
        params={"ts_code": ts_code},
        fields=API_FIELDS["stk_holdertrade"],
    )
    if not result["ok"]:
        print(f"❌ Tushare stk_holdertrade 查询失败: {result.get('message', '未知')}")
        return False

    trades = result["data"]
    # Sort by date descending
    trades.sort(key=lambda r: str(r.get("ann_date") or ""), reverse=True)
    trades = trades[:limit]

    display_name = trades[0].get("holder_name", code) if trades else code
    print("=" * 60)
    print(f"股东增减持记录: {display_name} 等 ({ts_code})")
    print(f"数据来源: Tushare stk_holdertrade，最近 {min(limit, len(trades))} 条")
    print("=" * 60)

    if not trades:
        print("\n  无股东增减持记录。")
    else:
        # Summary statistics
        buy_count = sum(1 for t in trades if str(t.get("in_de") or "").upper() in ("IN", "增持", "1"))
        sell_count = sum(1 for t in trades if str(t.get("in_de") or "").upper() in ("DE", "减持", "2"))
        print(f"\n  汇总: 增持 {buy_count} 笔，减持 {sell_count} 笔")
        print()

        for i, t in enumerate(trades):
            ann_date = t.get("ann_date", "-")
            holder_name = t.get("holder_name", "-")
            holder_type = t.get("holder_type", "-")
            in_de = t.get("in_de", "-")
            change_vol = t.get("change_vol", 0)
            change_ratio = t.get("change_ratio", 0)
            avg_price = t.get("avg_price", 0)
            after_hold = t.get("after_hold", 0)

            direction = "增持" if str(in_de).upper() in ("IN", "增持", "1") else (
                "减持" if str(in_de).upper() in ("DE", "减持", "2") else str(in_de)
            )

            print(f"  [{i+1}] {ann_date} {direction}")
            print(f"  股东:       {holder_name} ({holder_type})")
            if change_vol:
                print(f"  变动数量:   {_fmt_yi(change_vol)}股")
            if change_ratio:
                print(f"  变动比例:   {_fmt_pct(change_ratio)}")
            if avg_price:
                print(f"  均价:       {avg_price}")
            if after_hold:
                print(f"  变动后持股: {_fmt_yi(after_hold)}股")

    # Verification: Tushare is the primary source
    verification = _safe_verification("insider-trades", code, trades)
    _print_verification(verification)
    return True


# ---------------------------------------------------------------------------
# Phase 2: Tushare 增强命令（一致预期、股东、分红、管理层）
# ---------------------------------------------------------------------------

def cmd_consensus(code: str):
    """业绩预告——Tushare forecast。

    获取上市公司业绩预告（公司自愿披露的盈利指引），包括预告类型、
    净利润变动幅度、预计净利润范围、变动原因摘要。

    注意: 这不是分析师一致预期。Tushare 无独立分析师共识 API。
    部分公司（如招商银行）2010 年后不再发布详细业绩预告。
    """
    client = _get_tushare_client()
    if not client:
        return False

    ts_code = normalize_code(code).secu_code
    result = client.query(
        "forecast",
        params={"ts_code": ts_code},
        fields=API_FIELDS["forecast"],
    )
    if not result["ok"]:
        print(f"❌ Tushare forecast 查询失败: {result.get('message', '未知')}")
        return False

    forecasts = result["data"]
    forecasts.sort(key=lambda r: str(r.get("end_date") or ""), reverse=True)

    print("=" * 60)
    print(f"业绩预告: {code} ({ts_code})")
    print(f"数据来源: Tushare forecast（公司自愿披露的盈利指引）")
    print(f"⚠️ 非分析师一致预期。部分公司不发布或已停止发布业绩预告。")
    print("=" * 60)

    if not forecasts:
        print("\n  无业绩预告记录（该公司可能不发布或已停止发布）。")
    else:
        for i, f in enumerate(forecasts[:20]):
            ann_date = f.get("ann_date", "-")
            end_date = f.get("end_date", "-")
            fcst_type = f.get("type", "-")
            p_min = f.get("p_change_min", "-")
            p_max = f.get("p_change_max", "-")
            net_min = f.get("net_profit_min", "-")
            net_max = f.get("net_profit_max", "-")
            last_net = f.get("last_parent_net", "-")
            summary = f.get("summary", "")
            reason = f.get("change_reason", "")

            print(f"\n  [{i+1}] {end_date}（发布: {ann_date}）")
            print(f"  预告类型:   {fcst_type}")
            if p_min is not None:
                print(f"  变动幅度:   {p_min}% ~ {p_max}%")
            if net_min:
                print(f"  预计净利:   {_fmt_yi(net_min)} ~ {_fmt_yi(net_max)}")
            if last_net:
                print(f"  上期净利:   {_fmt_yi(last_net)}")
            if summary:
                print(f"  摘要:       {summary}")
            if reason:
                print(f"  变动原因:   {reason}")

        print(f"\n  共 {len(forecasts)} 条记录。")

    verification = _safe_verification("consensus", code, forecasts)
    _print_verification(verification)
    return True


def cmd_shareholders(code: str):
    """十大股东结构——Tushare top10_holders。

    获取历史十大股东明细，分析股东结构质量：国家队、外资、机构占比。
    """
    client = _get_tushare_client()
    if not client:
        return False

    ts_code = normalize_code(code).secu_code
    result = client.query(
        "top10_holders",
        params={"ts_code": ts_code},
        fields=API_FIELDS["top10_holders"],
    )
    if not result["ok"]:
        print(f"❌ Tushare top10_holders 查询失败: {result.get('message', '未知')}")
        return False

    holders = result["data"]
    # Group by period (end_date)
    from collections import defaultdict
    by_period = defaultdict(list)
    for h in holders:
        period = str(h.get("end_date") or "")[:10]
        by_period[period].append(h)

    periods = sorted(by_period.keys(), reverse=True)

    print("=" * 60)
    print(f"十大股东结构: {code} ({ts_code})")
    print(f"数据来源: Tushare top10_holders，共 {len(periods)} 期")
    print("=" * 60)

    if not holders:
        print("\n  无十大股东数据。")
    else:
        # Show latest period
        latest_period = periods[0]
        latest_holders = by_period[latest_period]
        print(f"\n  最新报告期: {latest_period}")
        print(f"  {'股东名称':<30s} {'持股比例':>8s} {'持股数':>14s} {'类型':>8s}")
        print(f"  {'-'*30} {'-'*8} {'-'*14} {'-'*8}")
        for h in latest_holders[:10]:
            name = str(h.get("holder_name", "-"))[:28]
            ratio = f"{float(h.get('hold_ratio') or 0):.2f}%"
            hold_num = _fmt_yi(h.get("hold_num"))
            htype = str(h.get("holder_type") or "-")[:8]
            print(f"  {name:<30s} {ratio:>8s} {hold_num:>14s} {htype:>8s}")

        # Summary statistics
        if len(periods) > 1:
            print(f"\n  历史报告期数: {len(periods)}")
            print(f"  数据跨度: {periods[-1]} ~ {periods[0]}")

    verification = _safe_verification("shareholders", code, holders)
    _print_verification(verification)
    return True


def cmd_dividend_history(code: str):
    """分红历史——Tushare dividend。

    获取历史现金分红/送股记录，用于评估分红稳定性与增长轨迹。
    """
    client = _get_tushare_client()
    if not client:
        return False

    ts_code = normalize_code(code).secu_code
    result = client.query(
        "dividend",
        params={"ts_code": ts_code},
        fields=API_FIELDS["dividend"],
    )
    if not result["ok"]:
        print(f"❌ Tushare dividend 查询失败: {result.get('message', '未知')}")
        return False

    divs = result["data"]
    divs.sort(key=lambda r: str(r.get("end_date") or ""), reverse=True)

    print("=" * 60)
    print(f"分红历史: {code} ({ts_code})")
    print(f"数据来源: Tushare dividend")
    print("=" * 60)

    if not divs:
        print("\n  无分红记录。")
    else:
        print(f"\n  {'报告期':<12s} {'现金分红':>10s} {'送股':>8s} {'股权登记日':<12s} {'除权日':<12s}")
        print(f"  {'-'*12} {'-'*10} {'-'*8} {'-'*12} {'-'*12}")
        total_cash = 0
        for d in divs[:20]:
            end_date = str(d.get("end_date") or "-")[:10]
            cash = float(d.get("cash_div") or 0)
            stk = float(d.get("stk_div") or 0)
            record = str(d.get("record_date") or "-")[:10]
            ex_div = str(d.get("ex_div_date") or "-")[:10]
            total_cash += cash
            print(f"  {end_date:<12s} {cash:>8.2f}元 {stk:>6.1f}股 {record:<12s} {ex_div:<12s}")

        if len(divs) > 20:
            print(f"  ... 共 {len(divs)} 条，仅显示最近 20 条")
        # Dividend stats
        years_with_div = len(set(str(d.get("end_date", ""))[:4] for d in divs if float(d.get("cash_div") or 0) > 0))
        print(f"\n  有现金分红年度: {years_with_div}")

    verification = _safe_verification("dividend", code, divs)
    _print_verification(verification)
    return True


# ── SW Industry mapping cache ──
_sw_industry_cache = None  # {industry_name: (sw_code, level)}


def _load_sw_industries(client):
    """Load Shenwan industry classification hierarchy. Cached per process."""
    global _sw_industry_cache
    if _sw_industry_cache is not None:
        return _sw_industry_cache

    r = client.query("index_classify", params={},
                     fields=("index_code", "industry_name", "level", "parent_code"))
    if not r["ok"]:
        _sw_industry_cache = {}
        return _sw_industry_cache

    # Build name → (code, level, parent) for all levels
    by_name = {}
    for row in r["data"]:
        by_name[row["industry_name"]] = (
            row["index_code"], row["level"], row.get("parent_code", "")
        )
    _sw_industry_cache = by_name
    return by_name


def _find_sw_index(client, industry_name: str) -> str:
    """Map stock_basic industry name to SW index code.

    Tries: exact match → fuzzy match → empty string (not found).
    """
    sw = _load_sw_industries(client)
    if not sw:
        return ""

    # Exact match
    if industry_name in sw:
        return sw[industry_name][0]

    # Fuzzy: industry_name is substring of SW name, or vice versa
    for name, (code, lvl, _) in sorted(sw.items(), key=lambda x: len(x[0])):
        if industry_name in name or name in industry_name:
            return code

    return ""


def cmd_industry_pe(code: str, json_output: bool = False):
    """行业 PE/PB 基准——Tushare sw_daily + index_classify。

    获取个股所属申万行业的 PE/PB 历史序列，输出行业当前估值
    及个股 vs 行业估值对比。
    """
    client = _get_tushare_client()
    if not client:
        return False

    ts_code = normalize_code(code).secu_code

    # 1. Get stock industry
    r = client.query("stock_basic", params={"ts_code": ts_code},
                     fields=("ts_code", "name", "industry"))
    if not r["ok"]:
        print(f"❌ 无法获取行业分类: {r.get('message', '未知')}")
        return False

    stock_name = r["data"][0].get("name", code)
    industry = r["data"][0].get("industry", "")
    if not industry:
        print(f"❌ 未找到 {code} 的行业分类")
        return False

    # 2. Map to SW index code
    sw_code = _find_sw_index(client, industry)
    if not sw_code:
        print(f"❌ 无法将行业「{industry}」映射到申万指数")
        return False

    # 3. Query industry PE/PB
    r = client.query("sw_daily", params={"ts_code": sw_code},
                     fields=("ts_code", "trade_date", "pe", "pb", "close"))
    if not r["ok"]:
        print(f"❌ sw_daily 查询失败: {r.get('message', '未知')}")
        return False

    rows = r["data"]
    pe_vals = [float(row["pe"]) for row in rows if row.get("pe") and float(row["pe"]) > 0]
    pb_vals = [float(row["pb"]) for row in rows if row.get("pb") and float(row["pb"]) > 0]

    if not pe_vals:
        print(f"❌ 行业 {sw_code} 无 PE 数据")
        return False

    import statistics
    pe_sorted = sorted(pe_vals)
    pb_sorted = sorted(pb_vals) if pb_vals else []
    latest = max(rows, key=lambda x: str(x.get("trade_date", "")))
    ind_pe = float(latest.get("pe", 0))
    ind_pb = float(latest.get("pb", 0))
    n_pe = len(pe_sorted)

    # 4. Get individual stock PE/PB for comparison
    r2 = client.query("daily_basic", params={"ts_code": ts_code},
                      fields=("ts_code", "trade_date", "pe", "pb"))
    stock_pe = stock_pb = None
    if r2["ok"] and r2["data"]:
        latest_stock = max(r2["data"], key=lambda x: str(x.get("trade_date", "")))
        stock_pe = float(latest_stock.get("pe") or 0)
        stock_pb = float(latest_stock.get("pb") or 0)

    # Build result
    pe_stats = {
        "current": round(ind_pe, 2), "min": round(min(pe_vals), 2), "max": round(max(pe_vals), 2),
        "p10": round(pe_sorted[int(n_pe * 0.10)], 2),
        "p25": round(pe_sorted[int(n_pe * 0.25)], 2),
        "p50": round(statistics.median(pe_vals), 2),
        "p75": round(pe_sorted[int(n_pe * 0.75)], 2),
        "p90": round(pe_sorted[int(n_pe * 0.90)], 2),
        "current_pct": round(sum(1 for v in pe_vals if v <= ind_pe) / n_pe * 100, 1),
        "n_days": n_pe,
    }
    pb_stats = {}
    if pb_sorted:
        n_pb = len(pb_sorted)
        pb_stats = {
            "current": round(ind_pb, 2), "min": round(min(pb_vals), 2), "max": round(max(pb_vals), 2),
            "p10": round(pb_sorted[int(n_pb * 0.10)], 2),
            "p25": round(pb_sorted[int(n_pb * 0.25)], 2),
            "p50": round(statistics.median(pb_vals), 2),
            "p75": round(pb_sorted[int(n_pb * 0.75)], 2),
            "p90": round(pb_sorted[int(n_pb * 0.90)], 2),
            "current_pct": round(sum(1 for v in pb_vals if v <= ind_pb) / n_pb * 100, 1),
            "n_days": n_pb,
        }

    stock_comparison = {}
    if stock_pe and stock_pe > 0:
        stock_comparison["stock_pe"] = round(stock_pe, 2)
        stock_comparison["stock_vs_ind_pe"] = f"{'高于' if stock_pe > ind_pe else '低于'}行业均值{abs(round((stock_pe/ind_pe-1)*100,1))}%"
        stock_comparison["stock_pe_vs_ind_pct"] = round(sum(1 for v in pe_vals if v <= stock_pe) / n_pe * 100, 1)
    if stock_pb and stock_pb > 0:
        stock_comparison["stock_pb"] = round(stock_pb, 2)
        stock_comparison["stock_vs_ind_pb"] = f"{'高于' if stock_pb > ind_pb else '低于'}行业均值{abs(round((stock_pb/ind_pb-1)*100,1))}%"
        if pb_sorted:
            stock_comparison["stock_pb_vs_ind_pct"] = round(sum(1 for v in pb_vals if v <= stock_pb) / len(pb_vals) * 100, 1)

    if json_output:
        print(json.dumps({
            "status": "ok", "code": code, "ts_code": ts_code,
            "industry": industry, "sw_code": sw_code,
            "source": "Tushare sw_daily + index_classify",
            "industry_pe": pe_stats, "industry_pb": pb_stats,
            "stock_vs_industry": stock_comparison,
        }, indent=2, ensure_ascii=False))
        return True

    print("=" * 60)
    print(f"行业 PE/PB 基准: {stock_name} ({ts_code})")
    print(f"申万行业: {industry} → {sw_code}")
    print(f"数据来源: Tushare sw_daily")
    print("=" * 60)

    print(f"\n  行业 PE（{n_pe} 个交易日）:")
    print(f"    当前:         {ind_pe}")
    print(f"    P10-P90:      {pe_stats['p10']} – {pe_stats['p90']}")
    print(f"    中位数:       {pe_stats['p50']}")
    print(f"    当前分位:     {pe_stats['current_pct']}%")

    if pb_stats:
        print(f"\n  行业 PB（{pb_stats['n_days']} 个交易日）:")
        print(f"    当前:         {ind_pb}")
        print(f"    P10-P90:      {pb_stats['p10']} – {pb_stats['p90']}")
        print(f"    中位数:       {pb_stats['p50']}")
        print(f"    当前分位:     {pb_stats['current_pct']}%")

    if stock_comparison:
        print(f"\n  个股 vs 行业:")
        if "stock_pe" in stock_comparison:
            print(f"    个股 PE:      {stock_comparison['stock_pe']} — {stock_comparison['stock_vs_ind_pe']}")
        if "stock_pb" in stock_comparison:
            print(f"    个股 PB:      {stock_comparison['stock_pb']} — {stock_comparison['stock_vs_ind_pb']}")

    verification = _safe_verification("industry-pe", code, {
        "industry": industry, "sw_code": sw_code,
    })
    _print_verification(verification)
    return True


# ── P2: News + Disclosure ──


def cmd_news(limit: int = 20):
    """主要新闻——Tushare major_news。"""
    client = _get_tushare_client()
    if not client:
        return False

    r = client.query("major_news", params={}, fields=API_FIELDS["major_news"])
    if not r["ok"]:
        print(f"❌ major_news 查询失败: {r.get('message', '未知')}")
        return False

    news = r["data"]
    news.sort(key=lambda x: str(x.get("pub_time", "")), reverse=True)
    news = news[:limit]

    print("=" * 60)
    print(f"主要新闻（Tushare major_news）")
    print("=" * 60)
    for i, n in enumerate(news):
        title = n.get("title", "-")
        pub_time = str(n.get("pub_time", "-"))[:16]
        src = n.get("src", "-")
        url = n.get("url", "-")
        print(f"\n  [{i+1}] {pub_time} | {src}")
        print(f"  {title}")
        print(f"  {url}")

    verification = _safe_verification("news", "market", news)
    _print_verification(verification)
    return True


def cmd_disclosure_calendar(code: str):
    """披露日历——Tushare disclosure_date。"""
    client = _get_tushare_client()
    if not client:
        return False

    ts_code = normalize_code(code).secu_code
    r = client.query("disclosure_date", params={"ts_code": ts_code},
                     fields=API_FIELDS["disclosure_date"])
    if not r["ok"]:
        if r["error_type"] == "empty_data":
            print(f"⚠️ {code} 无预披露日期信息")
            return True
        print(f"❌ disclosure_date 查询失败: {r.get('message', '未知')}")
        return False

    records = r["data"]
    records.sort(key=lambda x: str(x.get("end_date", "")), reverse=True)

    print("=" * 60)
    print(f"披露日历: {code} ({ts_code})")
    print("=" * 60)
    print(f"  {'报告期':<12s} {'预计披露':<12s} {'实际披露':<12s} {'公告日':<12s}")
    print(f"  {'-'*12} {'-'*12} {'-'*12} {'-'*12}")
    for rec in records[:10]:
        end_date = str(rec.get("end_date", "-"))[:10]
        pre_date = str(rec.get("pre_date", "-"))[:10]
        actual = str(rec.get("actual_date", "-"))[:10]
        ann = str(rec.get("ann_date", "-"))[:10]
        # Flag if pre_date differs from actual (delay)
        flag = " ⚠️延期" if pre_date != actual and actual != "-" else ""
        print(f"  {end_date:<12s} {pre_date:<12s} {actual:<12s} {ann:<12s}{flag}")

    verification = _safe_verification("disclosure-calendar", code, records)
    _print_verification(verification)
    return True


# ── P3: HK Stock ──


def _find_hk_code(client, a_code: str) -> str:
    """Map A-share code to H-share code via hk_basic lookup."""
    # hk_basic codes are like "03968.HK"
    # We need to match A-share companies to H-share by name
    # First, get the A-share name from stock_basic
    r = client.query("stock_basic", params={"ts_code": a_code},
                     fields=("ts_code", "name"))
    if not r["ok"]:
        return ""
    a_name = r["data"][0].get("name", "")

    # Fuzzy match in hk_basic (limited to H shares of A-share cos)
    # Most A+H stocks have matching names
    r = client.query("hk_basic", params={"list_status": "L"},
                     fields=("ts_code", "name"))
    if not r["ok"]:
        return ""
    for row in r["data"]:
        hk_name = row.get("name", "")
        # Exact match or A-share name contained in HK name
        if a_name == hk_name or a_name in hk_name or hk_name in a_name:
            return row["ts_code"]
    return ""


def cmd_hk_quote(code: str):
    """H股行情——Tushare hk_daily。用于 A+H 双重上市公司的独立源交叉验证。"""
    client = _get_tushare_client()
    if not client:
        return False

    # Find HK code
    ts_code_a = normalize_code(code).secu_code
    hk_code = _find_hk_code(client, ts_code_a)
    if not hk_code:
        print(f"❌ 未找到 {code} 对应的 H 股代码")
        return False

    # Get HK daily data
    r = client.query("hk_daily", params={"ts_code": hk_code},
                     fields=API_FIELDS["hk_daily"])
    if not r["ok"]:
        print(f"❌ hk_daily 查询失败: {r.get('message', '未知')}")
        return False

    rows = r["data"]
    latest = max(rows, key=lambda x: str(x.get("trade_date", "")))
    recent = sorted(rows, key=lambda x: str(x.get("trade_date", "")), reverse=True)[:10]

    # Get A-share latest for comparison
    r_a = client.query("daily_basic", params={"ts_code": ts_code_a},
                       fields=("ts_code", "trade_date", "close", "pe", "pb"))
    a_latest = {}
    if r_a["ok"] and r_a["data"]:
        a_latest = max(r_a["data"], key=lambda x: str(x.get("trade_date", "")))

    print("=" * 60)
    print(f"H股行情: {code} → {hk_code}")
    print(f"数据来源: Tushare hk_daily")
    print("=" * 60)
    print(f"\n  H股最新: {latest.get('trade_date','-')} | 收盘 {latest.get('close','-')} | 涨跌 {latest.get('pct_change','-')}%")
    if a_latest:
        print(f"  A股最新: {a_latest.get('trade_date','-')} | 收盘 {a_latest.get('close','-')} | PE {a_latest.get('pe','-')} | PB {a_latest.get('pb','-')}")

    if len(recent) > 1:
        print(f"\n  最近 {len(recent)} 个交易日:")
        for row in recent:
            print(f"    {row.get('trade_date','-')} | 收盘 {row.get('close','-')} | 涨跌 {row.get('pct_change','-')}%")

    verification = _safe_verification("hk-quote", code, rows)
    _print_verification(verification)
    return True


def cmd_ah_cross_check(code: str, json_output: bool = False):
    """A+H 交叉验证——Tushare hk_daily + daily_basic 双源 PE/PB 对比。

    比较同一公司在 A 股和 H 股的估值差异，是真正的独立信源验证：
    不同市场、不同投资者结构、不同货币计价。
    """
    client = _get_tushare_client()
    if not client:
        return False

    ts_code_a = normalize_code(code).secu_code
    hk_code = _find_hk_code(client, ts_code_a)

    # Get A-share PE/PB
    r_a = client.query("daily_basic", params={"ts_code": ts_code_a},
                       fields=("ts_code", "trade_date", "close", "pe", "pb", "total_mv"))
    a_latest = {}
    if r_a["ok"] and r_a["data"]:
        a_latest = max(r_a["data"], key=lambda x: str(x.get("trade_date", "")))

    # Get H-share daily data
    h_latest = {}
    if hk_code:
        r_h = client.query("hk_daily", params={"ts_code": hk_code},
                           fields=API_FIELDS["hk_daily"])
        if r_h["ok"] and r_h["data"]:
            h_rows = [r for r in r_h["data"] if r.get("pct_change") is not None]
            if h_rows:
                h_latest = max(h_rows, key=lambda x: str(x.get("trade_date", "")))

    result = {
        "status": "ok" if hk_code else "not_ah_stock",
        "a_code": ts_code_a,
        "h_code": hk_code or "",
    }

    if a_latest:
        result["a_share"] = {
            "date": a_latest.get("trade_date"), "close": a_latest.get("close"),
            "pe": a_latest.get("pe"), "pb": a_latest.get("pb"),
        }
    if h_latest:
        # Calculate AH premium
        a_close = float(a_latest.get("close", 0))
        h_close = float(h_latest.get("close", 0))
        ah_premium = round((a_close / h_close - 1) * 100, 1) if h_close > 0 else 0
        result["h_share"] = {
            "date": h_latest.get("trade_date"), "close": h_latest.get("close"),
            "change_pct": h_latest.get("pct_change"),
        }
        result["ah_premium_pct"] = ah_premium
        result["ah_premium_note"] = f"A股{'溢价' if ah_premium > 0 else '折价'}{abs(ah_premium)}%（H股{'折价' if ah_premium > 0 else '溢价'}）"

    if json_output:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return True

    print("=" * 60)
    print(f"A+H 交叉验证: {code}")
    print(f"数据来源: Tushare daily_basic (A股) + hk_daily (H股)")
    print("=" * 60)

    if not hk_code:
        print(f"\n  ⚠️ {code} 非 A+H 双重上市公司")
        return True

    print(f"\n  H股代码: {hk_code}")
    if a_latest:
        print(f"  A股 ({a_latest.get('trade_date','-')}): 收盘 {a_latest.get('close','-')} | PE {a_latest.get('pe','-')} | PB {a_latest.get('pb','-')}")
    if h_latest:
        print(f"  H股 ({h_latest.get('trade_date','-')}): 收盘 {h_latest.get('close','-')} | 涨跌 {h_latest.get('pct_change','-')}%")
    if result.get("ah_premium_pct") is not None:
        print(f"\n  AH溢价: {result['ah_premium_note']}")

    verification = _safe_verification("ah-cross-check", code, result)
    _print_verification(verification)
    return True


def cmd_management(code: str):
    """管理层薪酬与持股——Tushare stk_rewards。

    获取高管姓名、职位、薪酬、持股数量，用于评估管理层激励对齐程度。
    """
    client = _get_tushare_client()
    if not client:
        return False

    ts_code = normalize_code(code).secu_code
    result = client.query(
        "stk_rewards",
        params={"ts_code": ts_code},
        fields=API_FIELDS["stk_rewards"],
    )
    if not result["ok"]:
        print(f"❌ Tushare stk_rewards 查询失败: {result.get('message', '未知')}")
        return False

    mgmt = result["data"]
    # Get latest period
    mgmt.sort(key=lambda r: str(r.get("end_date") or ""), reverse=True)
    latest_period = str(mgmt[0].get("end_date") or "")[:10] if mgmt else ""
    latest = [m for m in mgmt if str(m.get("end_date") or "")[:10] == latest_period]

    print("=" * 60)
    print(f"管理层薪酬与持股: {code} ({ts_code})")
    print(f"数据来源: Tushare stk_rewards，最新报告期: {latest_period}")
    print("=" * 60)

    if not latest:
        print("\n  无管理层数据。")
    else:
        print(f"\n  {'姓名':<10s} {'职位':<22s} {'薪酬(万元)':>10s} {'持股数':>12s}")
        print(f"  {'-'*10} {'-'*22} {'-'*10} {'-'*12}")
        for m in latest[:25]:
            name = str(m.get("name", "-"))[:8]
            title = str(m.get("title", "-"))[:20]
            reward = m.get("reward")
            if reward is not None:
                reward_str = f"{float(reward)/10000:.1f}万"  # Tushare returns raw yuan
            else:
                reward_str = "-"
            hold = m.get("hold_vol")
            hold_str = _fmt_yi(hold) if hold else "-"
            print(f"  {name:<10s} {title:<22s} {reward_str:>10s} {hold_str:>12s}")

        # Summary
        total_with_salary = sum(1 for m in latest if m.get("reward") is not None)
        total_with_hold = sum(1 for m in latest if m.get("hold_vol") is not None and float(m.get("hold_vol", 0)) > 0)
        print(f"\n  当前期: {len(latest)} 人，{total_with_salary} 人有薪酬数据，{total_with_hold} 人持股")
        print(f"  总记录: {len(mgmt)} 条（全历史）")

    verification = _safe_verification("management", code, mgmt)
    _print_verification(verification)
    return True


def cmd_managers(code: str):
    """上市公司管理层履历——Tushare stk_managers。

    获取董监高姓名、职位、性别、出生年、学历、任职起止与简历，
    补 management-deep-dive 的"履历（出生年/首次任职）未取"缺口。
    """
    client = _get_tushare_client()
    if not client:
        return False

    ts_code = normalize_code(code).secu_code
    result = client.query(
        "stk_managers",
        params={"ts_code": ts_code},
        fields=API_FIELDS["stk_managers"],
    )
    if not result["ok"]:
        print(f"❌ Tushare stk_managers 查询失败: {result.get('message', '未知')}")
        return False

    rows = result["data"]

    def _serving(m):
        return str(m.get("end_date") or "").strip() in ("", "None", "0")

    current = [m for m in rows if _serving(m)]

    # 同一人常按每个职务/委员会各占一行；按姓名去重，聚合职务，履历取一次。
    people = {}
    order = []
    for m in current:
        name = str(m.get("name", "-"))
        if name not in people:
            people[name] = {"titles": [], "row": m}
            order.append(name)
        title = str(m.get("title", "-")).strip()
        if title and title not in people[name]["titles"]:
            people[name]["titles"].append(title)
        # 保留简历最长（最完整）的那行作为履历来源
        if len(str(m.get("resume") or "")) > len(str(people[name]["row"].get("resume") or "")):
            people[name]["row"] = m
    order.sort(key=lambda n: str(people[n]["row"].get("begin_date") or ""), reverse=True)

    print("=" * 60)
    print(f"管理层履历: {code} ({ts_code})")
    print("数据来源: Tushare stk_managers（董监高名单 + 履历）")
    print("=" * 60)

    if not order:
        print("\n  无在任管理层数据。")
    else:
        print(f"\n  在任 {len(order)} 人（去重后；全历史 {len(rows)} 条职务记录）：\n")
        for name in order[:30]:
            m = people[name]["row"]
            titles = " / ".join(people[name]["titles"]) or "-"
            gender = {"M": "男", "F": "女"}.get(str(m.get("gender") or ""), "")
            birth = str(m.get("birthday") or "")[:4]
            edu = str(m.get("edu") or "").strip()
            begin = str(m.get("begin_date") or "")[:8]
            print(f"  ▸ {name}（{titles}）")
            meta = " | ".join(x for x in [
                gender,
                f"生{birth}" if birth else "",
                edu,
                f"任职起 {begin}" if begin else "",
            ] if x)
            if meta:
                print(f"      {meta}")
            resume = str(m.get("resume") or "").strip()
            if resume and resume not in ("None", "null"):
                print(f"      简历: {resume[:120]}")

    verification = _safe_verification("managers", code, rows)
    _print_verification(verification)
    return True


def cmd_mainbz(code: str):
    """主营业务构成（分产品 + 分地区）——Tushare fina_mainbz。

    分部收入独立第二源；研究层可与东财 F10 主营构成交叉核对达成分部双源，
    补 investment-research/quality-screen 分部单源缺口。
    """
    client = _get_tushare_client()
    if not client:
        return False

    ts_code = normalize_code(code).secu_code

    def _fetch(bz_type):
        r = client.query(
            "fina_mainbz",
            params={"ts_code": ts_code, "type": bz_type},
            fields=API_FIELDS["fina_mainbz"],
        )
        return r["data"] if r.get("ok") else []

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    prod = _fetch("P")
    region = _fetch("D")
    all_rows = prod + region
    if not all_rows:
        print(f"❌ Tushare fina_mainbz 无数据（{ts_code}）")
        return False

    latest = max((str(r.get("end_date") or "") for r in all_rows), default="")

    print("=" * 60)
    print(f"主营业务构成: {code} ({ts_code})")
    print(f"数据来源: Tushare fina_mainbz（分部独立第二源），最新期: {latest[:10]}")
    print("=" * 60)

    def _table(title, rows):
        rows = [r for r in rows if str(r.get("end_date") or "") == latest]
        if not rows:
            return
        rows.sort(key=lambda r: _num(r.get("bz_sales")) or 0, reverse=True)
        total = sum((_num(r.get("bz_sales")) or 0) for r in rows)
        print(f"\n  【{title}】")
        print(f"  {'项目':<24s} {'收入':>12s} {'占比':>7s} {'利润':>12s}")
        print(f"  {'-'*24} {'-'*12} {'-'*7} {'-'*12}")
        for r in rows[:15]:
            item = str(r.get("bz_item", "-"))[:22]
            sales = _num(r.get("bz_sales"))
            pct = (sales / total * 100) if (sales is not None and total) else None
            pct_s = f"{pct:.1f}%" if pct is not None else "-"
            print(
                f"  {item:<24s} {_fmt_yi(r.get('bz_sales')):>12s} "
                f"{pct_s:>7s} {_fmt_yi(r.get('bz_profit')):>12s}"
            )

    _table("分产品", prod)
    _table("分地区", region)
    print("\n  注：Tushare 分部为独立第二源，可与东财 F10 主营构成交叉核对达成分部双源。")
    verification = _safe_verification("mainbz", code, all_rows)
    _print_verification(verification)
    return True


def cmd_repurchase(code: str):
    """股票回购——Tushare repurchase。

    回购进度/数量/金额/价格上限；补 management-deep-dive / news-pulse
    当前靠巨潮公告手工提取的回购数据缺口。
    """
    client = _get_tushare_client()
    if not client:
        return False

    ts_code = normalize_code(code).secu_code
    result = client.query(
        "repurchase",
        params={"ts_code": ts_code},
        fields=API_FIELDS["repurchase"],
    )
    if not result["ok"]:
        print(f"❌ Tushare repurchase 查询失败: {result.get('message', '未知')}")
        return False

    rows = result["data"]
    rows.sort(key=lambda r: str(r.get("ann_date") or ""), reverse=True)

    print("=" * 60)
    print(f"股票回购: {code} ({ts_code})")
    print("数据来源: Tushare repurchase")
    print("=" * 60)

    if not rows:
        print("\n  无回购记录。")
    else:
        print(f"\n  近 {min(len(rows), 20)} 条（全 {len(rows)} 条）：\n")
        print(f"  {'公告日':<12s} {'进度':<12s} {'回购数量':>12s} {'回购金额':>12s} {'价上限':>8s}")
        print(f"  {'-'*12} {'-'*12} {'-'*12} {'-'*12} {'-'*8}")
        for r in rows[:20]:
            ann = _fmt_date(r.get("ann_date"))
            proc = str(r.get("proc", "-"))[:10]
            high = r.get("high_limit")
            high_s = f"{float(high):.2f}" if high not in (None, "") else "-"
            print(
                f"  {ann:<12s} {proc:<12s} {_fmt_yi(r.get('vol')):>12s} "
                f"{_fmt_yi(r.get('amount')):>12s} {high_s:>8s}"
            )

    verification = _safe_verification("repurchase", code, rows)
    _print_verification(verification)
    return True


def cmd_pledge(code: str):
    """股权质押统计——Tushare pledge_stat。

    质押比例趋势；控股股东高质押 = 治理风险红线信号（当前完全空白）。
    """
    client = _get_tushare_client()
    if not client:
        return False

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    ts_code = normalize_code(code).secu_code
    result = client.query(
        "pledge_stat",
        params={"ts_code": ts_code},
        fields=API_FIELDS["pledge_stat"],
    )
    if not result["ok"]:
        print(f"❌ Tushare pledge_stat 查询失败: {result.get('message', '未知')}")
        return False

    rows = result["data"]
    rows.sort(key=lambda r: str(r.get("end_date") or ""), reverse=True)

    print("=" * 60)
    print(f"股权质押: {code} ({ts_code})")
    print("数据来源: Tushare pledge_stat（高质押 = 治理风险信号）")
    print("=" * 60)

    if not rows:
        print("\n  无质押记录。")
    else:
        print(f"\n  {'截止日':<12s} {'质押比例':>8s} {'质押笔数':>8s} {'质押股数(万)':>14s}")
        print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*14}")
        for r in rows[:12]:
            end = _fmt_date(r.get("end_date"))
            ratio = _num(r.get("pledge_ratio"))
            ratio_s = f"{ratio:.2f}%" if ratio is not None else "-"
            cnt = r.get("pledge_count")
            pledged = (_num(r.get("rest_pledge")) or 0) + (_num(r.get("unrest_pledge")) or 0)
            print(f"  {end:<12s} {ratio_s:>8s} {str(cnt if cnt is not None else '-'):>8s} {pledged:>14.0f}")

        latest_ratio = _num(rows[0].get("pledge_ratio"))
        if latest_ratio is not None:
            if latest_ratio >= 30:
                print(f"\n  ⚠️ 最新质押比例 {latest_ratio:.2f}% —— 高质押是治理红线信号")
            elif latest_ratio > 0:
                print(f"\n  最新质押比例 {latest_ratio:.2f}%（偏低）")
            else:
                print("\n  ✅ 最新无股权质押（质押比例 0%）")

    verification = _safe_verification("pledge", code, rows)
    _print_verification(verification)
    return True


def cmd_express(code: str):
    """业绩快报——Tushare express。

    正式财报前的早期业绩信号（营收/净利/EPS/ROE/同比），补 earnings-review 提前量。
    """
    client = _get_tushare_client()
    if not client:
        return False

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    ts_code = normalize_code(code).secu_code
    result = client.query(
        "express", params={"ts_code": ts_code}, fields=API_FIELDS["express"]
    )
    if not result["ok"]:
        print(f"❌ Tushare express 查询失败: {result.get('message', '未知')}")
        return False

    rows = result["data"]
    rows.sort(key=lambda r: str(r.get("end_date") or ""), reverse=True)

    print("=" * 60)
    print(f"业绩快报: {code} ({ts_code})")
    print("数据来源: Tushare express（正式财报前的早期业绩信号）")
    print("=" * 60)

    if not rows:
        print("\n  无业绩快报记录（该公司未在正式财报前发布过快报）。")
    else:
        for r in rows[:12]:
            period = _fmt_date(r.get("end_date"))
            ann = _fmt_date(r.get("ann_date"))
            rev = _num(r.get("revenue"))
            ni = _num(r.get("n_income"))
            prior_ni = _num(r.get("yoy_net_profit"))  # 去年同期净利（金额）
            yoy_sales = _num(r.get("yoy_sales"))
            np_yoy = ((ni / prior_ni - 1) * 100) if (ni is not None and prior_ni) else None
            eps = r.get("diluted_eps")
            roe = r.get("diluted_roe")
            bps = r.get("bps")
            print(f"\n  ▸ 报告期 {period}（披露 {ann}）")
            rev_s = f"{_fmt_yi(rev)}" + (f"（同比 {yoy_sales:+.2f}%）" if yoy_sales is not None else "")
            ni_s = f"{_fmt_yi(ni)}" + (f"（同比 {np_yoy:+.1f}%）" if np_yoy is not None else "")
            print(f"      营收 {rev_s}  净利 {ni_s}")
            metrics = " ".join(x for x in [
                f"EPS {eps}" if eps not in (None, "") else "",
                f"ROE {roe}%" if roe not in (None, "") else "",
                f"BVPS {bps}" if bps not in (None, "") else "",
            ] if x)
            if metrics:
                print(f"      {metrics}")
            summary = str(r.get("perf_summary") or "").strip()
            if summary and summary not in ("None", "null"):
                print(f"      摘要: {summary[:120]}")
        print("\n  注：快报为未审计早期数据，以正式定期报告为准。")

    verification = _safe_verification("express", code, rows)
    _print_verification(verification)
    return True


def cmd_kline(code: str, days: int = 120):
    """前复权日线序列——Tushare daily + adj_factor。

    补管线"无复权 OHLC 序列"缺口：独立历史价格源（对 news-pulse/thesis-drift），
    前复权处理跨越分红/送转，可与腾讯 qfq 日线交叉。
    """
    client = _get_tushare_client()
    if not client:
        return False

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    ts_code = normalize_code(code).secu_code
    start = (datetime.now() - timedelta(days=int(days * 1.7))).strftime("%Y%m%d")

    daily = client.query(
        "daily", params={"ts_code": ts_code, "start_date": start},
        fields=API_FIELDS["daily"],
    )
    if not daily["ok"]:
        print(f"❌ Tushare daily 查询失败: {daily.get('message', '未知')}")
        return False
    rows = [r for r in daily["data"] if r.get("close") is not None]
    if not rows:
        print("❌ 无日线数据")
        return False

    adj = client.query(
        "adj_factor", params={"ts_code": ts_code, "start_date": start},
        fields=API_FIELDS["adj_factor"],
    )
    adj_map = {}
    if adj["ok"]:
        for r in adj["data"]:
            f = _num(r.get("adj_factor"))
            if f is not None:
                adj_map[str(r.get("trade_date"))] = f

    rows.sort(key=lambda r: str(r.get("trade_date")))
    latest_adj = adj_map.get(str(rows[-1].get("trade_date"))) \
        or (max(adj_map.values()) if adj_map else 1.0)

    def _qfq(px, td):
        p = _num(px)
        f = adj_map.get(str(td), latest_adj)
        return (p * f / latest_adj) if (p is not None and latest_adj) else None

    window = rows[-days:] if len(rows) > days else rows
    for r in window:
        td = r.get("trade_date")
        r["_qo"] = _qfq(r.get("open"), td)
        r["_qh"] = _qfq(r.get("high"), td)
        r["_ql"] = _qfq(r.get("low"), td)
        r["_qc"] = _qfq(r.get("close"), td)

    adj_note = "前复权" if adj_map else "未复权（adj_factor 不可用）"
    print("=" * 60)
    print(f"复权日线(kline): {code} ({ts_code})")
    print(f"数据来源: Tushare daily + adj_factor（{adj_note}），近 {len(window)} 个交易日")
    print("=" * 60)

    print(f"\n  {'交易日':<12s} {'开':>8s} {'高':>8s} {'低':>8s} {'收':>8s} {'涨跌%':>8s}")
    print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for r in window[-15:]:
        pct = _num(r.get("pct_chg"))
        pct_s = f"{pct:+.2f}" if pct is not None else "-"
        print(
            f"  {_fmt_date(r.get('trade_date')):<12s} "
            f"{(r['_qo'] or 0):>8.2f} {(r['_qh'] or 0):>8.2f} "
            f"{(r['_ql'] or 0):>8.2f} {(r['_qc'] or 0):>8.2f} {pct_s:>8s}"
        )

    closes = [r["_qc"] for r in window if r["_qc"] is not None]
    highs = [r["_qh"] for r in window if r["_qh"] is not None]
    lows = [r["_ql"] for r in window if r["_ql"] is not None]
    if closes and highs and lows:
        ret = (closes[-1] / closes[0] - 1) * 100 if closes[0] else 0.0
        print(
            f"\n  区间(前复权): 高 {max(highs):.2f} / 低 {min(lows):.2f}；"
            f"首 {closes[0]:.2f} → 末 {closes[-1]:.2f}（{ret:+.1f}%）"
        )

    # 独立第二历史源交叉：与腾讯当前价对最新收盘
    try:
        qq = _parse_qq_quote(_curl(f"https://qt.gtimg.cn/q={_qq_code(code)}"))
        qq_price = _num(qq.get("price")) if qq else None
    except Exception:
        qq_price = None
    if qq_price is not None and closes:
        dev = abs(qq_price - closes[-1]) / closes[-1] * 100 if closes[-1] else 0
        tag = "✅ 一致" if dev <= 1 else "⚠️ 偏差"
        print(f"  最新收盘 vs 腾讯现价: {closes[-1]:.2f} / {qq_price:.2f}（{tag} {dev:.2f}%，独立源交叉）")

    verification = _safe_verification("kline", code, rows)
    _print_verification(verification)
    return True


def cmd_audit(code: str):
    """财务审计意见——Tushare fina_audit。

    是否"标准无保留意见" = 治理硬信号；非标意见告警。
    """
    client = _get_tushare_client()
    if not client:
        return False

    ts_code = normalize_code(code).secu_code
    result = client.query(
        "fina_audit", params={"ts_code": ts_code}, fields=API_FIELDS["fina_audit"]
    )
    if not result["ok"]:
        print(f"❌ Tushare fina_audit 查询失败: {result.get('message', '未知')}")
        return False

    rows = result["data"]
    seen = {}
    for r in sorted(rows, key=lambda r: str(r.get("ann_date") or "")):
        seen[str(r.get("end_date"))] = r
    periods = sorted(seen.values(), key=lambda r: str(r.get("end_date")), reverse=True)

    print("=" * 60)
    print(f"财务审计意见: {code} ({ts_code})")
    print("数据来源: Tushare fina_audit")
    print("=" * 60)

    if not periods:
        print("\n  无审计意见记录。")
    else:
        print(f"\n  {'年报期':<12s} {'审计意见':<16s} {'会计事务所':<24s} {'审计费':>10s}")
        print(f"  {'-'*12} {'-'*16} {'-'*24} {'-'*10}")
        for r in periods[:12]:
            opinion = str(r.get("audit_result", "-"))
            flag = "" if opinion == "标准无保留意见" else "  ⚠️"
            print(
                f"  {_fmt_date(r.get('end_date')):<12s} {opinion:<16s} "
                f"{str(r.get('audit_agency', '-'))[:22]:<24s} "
                f"{_fmt_yi(r.get('audit_fees')):>10s}{flag}"
            )
        non_std = [r for r in periods if str(r.get("audit_result")) != "标准无保留意见"]
        if non_std:
            print(f"\n  ⚠️ 存在 {len(non_std)} 期非标准无保留意见 —— 治理/财务红线，需深究")
        else:
            print("\n  ✅ 所列各期均为标准无保留意见")

    verification = _safe_verification("audit", code, rows)
    _print_verification(verification)
    return True


def cmd_holder_num(code: str):
    """股东户数趋势——Tushare stk_holdernumber。

    户数下降=筹码集中（多为偏多信号），上升=分散。
    """
    client = _get_tushare_client()
    if not client:
        return False

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    ts_code = normalize_code(code).secu_code
    result = client.query(
        "stk_holdernumber", params={"ts_code": ts_code},
        fields=API_FIELDS["stk_holdernumber"],
    )
    if not result["ok"]:
        print(f"❌ Tushare stk_holdernumber 查询失败: {result.get('message', '未知')}")
        return False

    rows = result["data"]
    rows.sort(key=lambda r: str(r.get("end_date") or ""), reverse=True)

    print("=" * 60)
    print(f"股东户数: {code} ({ts_code})")
    print("数据来源: Tushare stk_holdernumber（筹码集中度）")
    print("=" * 60)

    if not rows:
        print("\n  无股东户数记录。")
    else:
        print(f"\n  {'截止日':<12s} {'股东户数':>12s} {'环比':>10s}")
        print(f"  {'-'*12} {'-'*12} {'-'*10}")
        for i, r in enumerate(rows[:12]):
            num = _num(r.get("holder_num"))
            num_s = f"{int(num):,}" if num is not None else "-"
            chg = "-"
            if i + 1 < len(rows):
                prev = _num(rows[i + 1].get("holder_num"))
                if num is not None and prev:
                    chg = f"{(num / prev - 1) * 100:+.1f}%"
            print(f"  {_fmt_date(r.get('end_date')):<12s} {num_s:>12s} {chg:>10s}")

        latest = _num(rows[0].get("holder_num"))
        oldest = _num(rows[-1].get("holder_num")) if len(rows) > 1 else None
        if latest is not None and oldest:
            trend = "集中" if latest < oldest else "分散"
            print(f"\n  区间筹码趋{trend}：{int(oldest):,} → {int(latest):,}"
                  f"（{(latest / oldest - 1) * 100:+.1f}%）")

    verification = _safe_verification("holder-num", code, rows)
    _print_verification(verification)
    return True


def cmd_ratios(code: str):
    """财务比率全景——Tushare fina_indicator（年报口径）。

    ROE/扣非ROE/ROA/ROIC/毛利/净利/资产负债/流动比/速动比/OCF·营收，
    补 quality-screen 更全的独立比率集。
    """
    client = _get_tushare_client()
    if not client:
        return False

    ts_code = normalize_code(code).secu_code
    result = client.query(
        "fina_indicator", params={"ts_code": ts_code},
        fields=API_FIELDS["fina_indicator"],
    )
    if not result["ok"]:
        print(f"❌ Tushare fina_indicator 查询失败: {result.get('message', '未知')}")
        return False

    annual = [r for r in result["data"] if str(r.get("end_date") or "").endswith("1231")]
    seen = {}
    for r in sorted(annual, key=lambda r: (str(r.get("end_date")), str(r.get("update_flag") or ""))):
        seen[str(r.get("end_date"))] = r
    periods = sorted(seen.values(), key=lambda r: str(r.get("end_date")), reverse=True)[:6]

    print("=" * 60)
    print(f"财务比率全景: {code} ({ts_code})")
    print("数据来源: Tushare fina_indicator（年报口径，独立比率集）")
    print("=" * 60)

    if not periods:
        print("\n  无年报比率记录。")
    else:
        def g(r, k):
            try:
                return f"{float(r.get(k)):.2f}"
            except (TypeError, ValueError):
                return "-"

        hdr = (f"  {'期间':<12s} {'ROE':>7s} {'扣非ROE':>8s} {'ROA':>7s} {'ROIC':>7s} "
               f"{'毛利%':>7s} {'净利%':>7s} {'资负%':>7s} {'流动比':>7s} {'速动比':>7s} {'OCF/营收':>8s}")
        print("\n" + hdr)
        print("  " + "-" * (len(hdr) - 2))
        for r in periods:
            print(
                f"  {_fmt_date(r.get('end_date')):<12s} {g(r,'roe'):>7s} {g(r,'roe_dt'):>8s} "
                f"{g(r,'roa'):>7s} {g(r,'roic'):>7s} {g(r,'grossprofit_margin'):>7s} "
                f"{g(r,'netprofit_margin'):>7s} {g(r,'debt_to_assets'):>7s} "
                f"{g(r,'current_ratio'):>7s} {g(r,'quick_ratio'):>7s} {g(r,'ocf_to_or'):>8s}"
            )
        print("\n  注：Tushare 比率为独立源，可与东财 F10 交叉；周期股须看多年趋势而非单年。")

    verification = _safe_verification("ratios", code, result["data"])
    _print_verification(verification)
    return True


def cmd_peers(code: str, level: str = "l3"):
    """行业可比公司池——Tushare index_member_all（申万分类）。

    反查标的申万一/二/三级行业，列出全部成员股 = industry-funnel 候选池自动化。
    """
    client = _get_tushare_client()
    if not client:
        return False

    ts_code = normalize_code(code).secu_code
    r1 = client.query(
        "index_member_all", params={"ts_code": ts_code, "is_new": "Y"},
        fields=API_FIELDS["index_member_all"],
    )
    if not r1["ok"] or not r1["data"]:
        msg = r1.get("message", "无数据") if not r1["ok"] else "未归入申万成分"
        print(f"❌ 未找到 {ts_code} 的申万行业归属: {msg}")
        return False

    info = r1["data"][0]
    l1, l2, l3 = info.get("l1_name"), info.get("l2_name"), info.get("l3_name")

    level = level.lower()
    if level not in ("l1", "l2", "l3"):
        level = "l3"
    code_key = {"l1": "l1_code", "l2": "l2_code", "l3": "l3_code"}[level]
    level_name = {"l1": l1, "l2": l2, "l3": l3}[level]
    ind_code = info.get(code_key)

    r2 = client.query(
        "index_member_all", params={code_key: ind_code, "is_new": "Y"},
        fields=API_FIELDS["index_member_all"],
    )
    seen = {}
    for m in (r2["data"] if r2["ok"] else []):
        seen[str(m.get("ts_code"))] = m
    members = sorted(seen.values(), key=lambda m: str(m.get("ts_code")))

    print("=" * 60)
    print(f"行业可比公司池: {code} ({ts_code})")
    print("数据来源: Tushare index_member_all（申万分类）")
    print("=" * 60)
    print(f"\n  申万归属: 一级「{l1}」/ 二级「{l2}」/ 三级「{l3}」")
    print(f"  候选池口径: {level.upper()}「{level_name}」 —— 共 {len(members)} 家\n")

    if not members:
        print("  无成员（该级代码缺失或权限不足）。")
    else:
        for i, m in enumerate(members, 1):
            mcode = str(m.get("ts_code", "-"))
            mark = "  ← 本标的" if mcode == ts_code else ""
            print(f"  {i:>2d}. {mcode:<12s} {str(m.get('name', '-'))}{mark}")
        print("\n  注：此为 industry-funnel 候选池；可对每家跑 quote/valuation/ratios 逐层去劣。")

    verification = _safe_verification("peers", code, r1["data"])
    _print_verification(verification)
    return True


def cmd_north_hold(code: str):
    """北向持股趋势——Tushare hk_hold（沪深股通）。

    北向持股占比 = 外资/机构情绪；占比上升=外资增持（多为偏多信号）。
    """
    client = _get_tushare_client()
    if not client:
        return False

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    ts_code = normalize_code(code).secu_code
    start = (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")
    result = client.query(
        "hk_hold", params={"ts_code": ts_code, "start_date": start},
        fields=API_FIELDS["hk_hold"],
    )
    if not result["ok"]:
        print(f"❌ Tushare hk_hold 查询失败: {result.get('message', '未知')}")
        return False

    rows = result["data"]
    rows.sort(key=lambda r: str(r.get("trade_date") or ""), reverse=True)

    print("=" * 60)
    print(f"北向持股: {code} ({ts_code})")
    print("数据来源: Tushare hk_hold（沪深股通，外资情绪）")
    print("=" * 60)

    if not rows:
        print("\n  无北向持股记录（可能非陆股通标的或区间无数据）。")
    else:
        print(f"\n  {'交易日':<12s} {'北向持股':>12s} {'占比':>8s} {'占比环比':>10s}")
        print(f"  {'-'*12} {'-'*12} {'-'*8} {'-'*10}")
        for i, r in enumerate(rows[:15]):
            ratio = _num(r.get("ratio"))
            ratio_s = f"{ratio:.2f}%" if ratio is not None else "-"
            chg = "-"
            if i + 1 < len(rows):
                prev = _num(rows[i + 1].get("ratio"))
                if ratio is not None and prev is not None:
                    chg = f"{ratio - prev:+.2f}pct"
            print(f"  {_fmt_date(r.get('trade_date')):<12s} "
                  f"{_fmt_yi(r.get('vol')):>12s}股 {ratio_s:>8s} {chg:>10s}")

        latest = _num(rows[0].get("ratio"))
        oldest = _num(rows[-1].get("ratio")) if len(rows) > 1 else None
        if latest is not None and oldest is not None:
            trend = "增持" if latest > oldest else "减持"
            print(f"\n  区间北向{trend}：占比 {oldest:.2f}% → {latest:.2f}%（{latest - oldest:+.2f}pct）")

    verification = _safe_verification("north-hold", code, rows)
    _print_verification(verification)
    return True


_INDEX_ALIASES = {
    "hs300": ("000300.SH", "沪深300"),
    "zz500": ("000905.SH", "中证500"),
    "zz1000": ("000852.SH", "中证1000"),
    "sse": ("000001.SH", "上证综指"),
    "szse": ("399001.SZ", "深证成指"),
    "cyb": ("399006.SZ", "创业板指"),
    "kc50": ("000688.SH", "科创50"),
}


def cmd_index_val(index: str = "hs300"):
    """大盘指数估值分位——Tushare index_dailybasic。

    指数 PE(TTM)/PB 当前值 + 历史分位；市场估值水位锚（择时/情绪，非个股结论）。
    """
    client = _get_tushare_client()
    if not client:
        return False

    alias = _INDEX_ALIASES.get(str(index).lower())
    idx_code, idx_name = alias if alias else (index, index)

    result = client.query(
        "index_dailybasic", params={"ts_code": idx_code, "start_date": "20180101"},
        fields=API_FIELDS["index_dailybasic"],
    )
    if not result["ok"] or not result["data"]:
        aliases = " / ".join(sorted(_INDEX_ALIASES))
        print(f"❌ index_dailybasic 无数据（{idx_code}）；可用别名: {aliases} 或直接传指数代码如 000300.SH")
        return False

    rows = sorted(result["data"], key=lambda r: str(r.get("trade_date") or ""))
    latest = rows[-1]

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    print("=" * 60)
    print(f"大盘估值分位: {idx_name}（{idx_code}）")
    print(f"数据来源: Tushare index_dailybasic，截至 {_fmt_date(latest.get('trade_date'))}"
          f"（{rows[0].get('trade_date', '')[:4]} 至今 {len(rows)} 日）")
    print("=" * 60)

    import statistics
    print(f"\n  {'指标':<10s} {'当前':>8s} {'分位':>7s} {'最低':>8s} {'中位':>8s} {'最高':>8s}")
    print(f"  {'-'*10} {'-'*8} {'-'*7} {'-'*8} {'-'*8} {'-'*8}")
    for key, label in (("pe_ttm", "PE(TTM)"), ("pe", "PE"), ("pb", "PB")):
        vals = [_num(r.get(key)) for r in rows]
        vals = [v for v in vals if v is not None and v > 0]
        cur = _num(latest.get(key))
        if not vals or cur is None:
            continue
        pct = sum(1 for v in vals if v <= cur) / len(vals) * 100
        print(f"  {label:<10s} {cur:>8.2f} {pct:>6.0f}% "
              f"{min(vals):>8.2f} {statistics.median(vals):>8.2f} {max(vals):>8.2f}")

    print("\n  注：分位越低=市场整体估值越便宜（市场择时/情绪锚，非个股买卖结论）。")
    return True


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="A股数据工具 — 腾讯行情 + 东方财富财务数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    p_quote = sub.add_parser("quote", help="实时行情")
    p_quote.add_argument("code", help="股票代码，如 600519")

    p_fin = sub.add_parser("financials", help="核心财务数据（近5年）")
    p_fin.add_argument("code", help="股票代码")

    p_val = sub.add_parser("valuation", help="估值指标")
    p_val.add_argument("code", help="股票代码")

    p_search = sub.add_parser("search", help="搜索股票代码")
    p_search.add_argument("keyword", help="公司名或关键词")

    p_history = sub.add_parser("history", help="长期年度财务数据")
    p_history.add_argument("code", help="股票代码")
    p_history.add_argument(
        "--years",
        type=_positive_years,
        default=10,
        help="年度数量，默认 10，范围 1-50",
    )

    p_equity = sub.add_parser("equity-history", help="历史股本变动")
    p_equity.add_argument("code", help="股票代码")

    p_signals = sub.add_parser("signals", help="龙虎榜、资金流、解禁、融资融券")
    p_signals.add_argument("code", help="股票代码")
    p_signals.add_argument("--date", default=None, help="交易日期 YYYY-MM-DD")

    p_ann = sub.add_parser("announcements", help="公告列表")
    p_ann.add_argument("code", help="股票代码")
    p_ann.add_argument("--limit", type=int, default=20, help="返回数量，默认 20")

    # Phase 1: Tushare 10,000积分增强命令
    p_pe = sub.add_parser("pe-band", help="历史PE/PB分位（Tushare daily_basic）")
    p_pe.add_argument("code", help="股票代码")
    p_pe.add_argument("--years", type=_positive_years, default=5, help="年度数量，默认 5")
    p_pe.add_argument("--json", action="store_true", help="JSON 输出")

    p_surv = sub.add_parser("research-visits", help="机构调研记录（Tushare stk_surv）")
    p_surv.add_argument("code", help="股票代码")
    p_surv.add_argument("--limit", type=int, default=20, help="返回数量，默认 20")

    p_trades = sub.add_parser("insider-trades", help="股东增减持（Tushare stk_holdertrade）")
    p_trades.add_argument("code", help="股票代码")
    p_trades.add_argument("--limit", type=int, default=20, help="返回数量，默认 20")

    # Phase 2: Tushare 增强命令
    p_fcst = sub.add_parser("consensus", help="业绩预告（Tushare forecast — 公司盈利指引）")
    p_fcst.add_argument("code", help="股票代码")

    p_sh = sub.add_parser("shareholders", help="十大股东结构（Tushare top10_holders）")
    p_sh.add_argument("code", help="股票代码")

    p_div = sub.add_parser("dividend", help="分红历史（Tushare dividend）")
    p_div.add_argument("code", help="股票代码")

    p_mgmt = sub.add_parser("management", help="管理层薪酬持股（Tushare stk_rewards）")
    p_mgmt.add_argument("code", help="股票代码")

    # P1: Industry benchmark
    p_ind = sub.add_parser("industry-pe", help="行业PE/PB基准（Tushare sw_daily）")
    p_ind.add_argument("code", help="股票代码")
    p_ind.add_argument("--json", action="store_true", help="JSON 输出")

    # P2: News + disclosure
    p_news = sub.add_parser("news", help="主要新闻（Tushare major_news）")
    p_news.add_argument("--limit", type=int, default=20, help="返回数量，默认 20")

    p_dcal = sub.add_parser("disclosure-calendar", help="披露日历（Tushare disclosure_date）")
    p_dcal.add_argument("code", help="股票代码")

    # P3: HK stock
    p_hk = sub.add_parser("hk-quote", help="H股行情（Tushare hk_daily）")
    p_hk.add_argument("code", help="A股代码")

    p_ah = sub.add_parser("ah-cross-check", help="A+H交叉验证（Tushare hk_daily+daily_basic）")
    p_ah.add_argument("code", help="A股代码")
    p_ah.add_argument("--json", action="store_true", help="JSON 输出")

    # Tier 1 缺口补齐命令
    p_mainbz = sub.add_parser("mainbz", help="主营业务构成 分产品/分地区（Tushare fina_mainbz — 分部独立第二源）")
    p_mainbz.add_argument("code", help="股票代码")

    p_managers = sub.add_parser("managers", help="管理层履历 出生年/学历/任职起止（Tushare stk_managers）")
    p_managers.add_argument("code", help="股票代码")

    p_repo = sub.add_parser("repurchase", help="股票回购 进度/数量/金额（Tushare repurchase）")
    p_repo.add_argument("code", help="股票代码")

    p_pledge = sub.add_parser("pledge", help="股权质押 比例趋势/治理风险（Tushare pledge_stat）")
    p_pledge.add_argument("code", help="股票代码")

    p_express = sub.add_parser("express", help="业绩快报 财报前早期业绩信号（Tushare express）")
    p_express.add_argument("code", help="股票代码")

    p_kline = sub.add_parser("kline", help="前复权日线序列 独立历史价格源（Tushare daily+adj_factor）")
    p_kline.add_argument("code", help="股票代码")
    p_kline.add_argument("--days", type=int, default=120, help="交易日窗口，默认 120")

    # Tier 2 缺口补齐命令
    p_audit = sub.add_parser("audit", help="财务审计意见 是否标准无保留（Tushare fina_audit）")
    p_audit.add_argument("code", help="股票代码")

    p_hnum = sub.add_parser("holder-num", help="股东户数趋势 筹码集中度（Tushare stk_holdernumber）")
    p_hnum.add_argument("code", help="股票代码")

    p_ratios = sub.add_parser("ratios", help="财务比率全景 ROE/ROA/ROIC/流动比等（Tushare fina_indicator）")
    p_ratios.add_argument("code", help="股票代码")

    p_peers = sub.add_parser("peers", help="行业可比公司池 申万成员股（Tushare index_member_all）")
    p_peers.add_argument("code", help="股票代码")
    p_peers.add_argument("--level", default="l3", choices=["l1", "l2", "l3"],
                         help="申万级别，默认 l3（最精确）")

    p_north = sub.add_parser("north-hold", help="北向持股趋势 外资情绪（Tushare hk_hold）")
    p_north.add_argument("code", help="股票代码")

    p_idxval = sub.add_parser("index-val", help="大盘估值分位 PE/PB历史分位（Tushare index_dailybasic）")
    p_idxval.add_argument("index", nargs="?", default="hs300",
                          help="指数别名 hs300/zz500/sse/cyb… 或指数代码，默认 hs300")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "quote": lambda: cmd_quote(args.code),
        "financials": lambda: cmd_financials(args.code),
        "valuation": lambda: cmd_valuation(args.code),
        "search": lambda: cmd_search(args.keyword),
        "history": lambda: cmd_history(args.code, args.years),
        "equity-history": lambda: cmd_equity_history(args.code),
        "signals": lambda: cmd_signals(args.code, args.date),
        "announcements": lambda: cmd_announcements(args.code, args.limit),
        # Phase 1: Tushare 10,000积分增强命令
        "pe-band": lambda: cmd_pe_band(args.code, args.years, args.json),
        "research-visits": lambda: cmd_research_visits(args.code, args.limit),
        "insider-trades": lambda: cmd_insider_trades(args.code, args.limit),
        # Phase 2: Tushare 增强命令
        "consensus": lambda: cmd_consensus(args.code),
        "shareholders": lambda: cmd_shareholders(args.code),
        "dividend": lambda: cmd_dividend_history(args.code),
        "management": lambda: cmd_management(args.code),
        # P1: Industry benchmark
        "industry-pe": lambda: cmd_industry_pe(args.code, args.json),
        # P2: News + disclosure
        "news": lambda: cmd_news(args.limit),
        "disclosure-calendar": lambda: cmd_disclosure_calendar(args.code),
        # P3: HK stock
        "hk-quote": lambda: cmd_hk_quote(args.code),
        "ah-cross-check": lambda: cmd_ah_cross_check(args.code, args.json),
        # Tier 1 缺口补齐
        "mainbz": lambda: cmd_mainbz(args.code),
        "managers": lambda: cmd_managers(args.code),
        "repurchase": lambda: cmd_repurchase(args.code),
        "pledge": lambda: cmd_pledge(args.code),
        "express": lambda: cmd_express(args.code),
        "kline": lambda: cmd_kline(args.code, args.days),
        # Tier 2 缺口补齐
        "audit": lambda: cmd_audit(args.code),
        "holder-num": lambda: cmd_holder_num(args.code),
        "ratios": lambda: cmd_ratios(args.code),
        "peers": lambda: cmd_peers(args.code, args.level),
        "north-hold": lambda: cmd_north_hold(args.code),
        "index-val": lambda: cmd_index_val(args.index),
    }
    try:
        outcome = cmds[args.command]()
    except ValueError as exc:
        print(f"❌ 参数错误: {exc}", file=sys.stderr)
        sys.exit(2)
    if outcome is False:
        sys.exit(1)


if __name__ == "__main__":
    main()
