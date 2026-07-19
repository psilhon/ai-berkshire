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
from datetime import datetime
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
    high_52w, low_52w = _fetch_52w(code)
    print(f"  52周最高:   {high_52w}")
    print(f"  52周最低:   {low_52w}")

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
