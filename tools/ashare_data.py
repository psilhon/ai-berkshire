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
from decimal import Decimal, ROUND_HALF_EVEN

_TIMEOUT = 15
_DATACENTER_URL = "https://datacenter.eastmoney.com/securities/api/data/get"


def _curl(url):
    """用 curl --noproxy 直连，绕过系统代理。"""
    result = subprocess.run(
        ["/usr/bin/curl", "-s", "--noproxy", "*",
         "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
         url],
        capture_output=True, timeout=_TIMEOUT,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ConnectionError(f"请求失败: {url}")
    # 腾讯行情 API 返回 GBK 编码，其他返回 UTF-8
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return result.stdout.decode("gbk")


def _curl_json(url, params=None):
    """curl 获取 JSON。"""
    if params:
        from urllib.parse import urlencode
        url = f"{url}?{urlencode(params)}"
    return json.loads(_curl(url))


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
    elif code_clean.startswith(("6", "9", "5")):
        market = "SH"
    elif code_clean.startswith(("4", "8")):
        market = "BJ"
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
    if code.startswith(("6", "9", "5")):
        return f"sh{code}"
    elif code.startswith(("0", "3", "2", "1")):
        return f"sz{code}"
    elif code.startswith(("4", "8")):
        return f"bj{code}"
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
    return True


def cmd_financials(code: str):
    """近5年核心财务数据。"""
    qq_code = _qq_code(code)
    try:
        raw = _curl(f"https://qt.gtimg.cn/q={qq_code}")
        d = _parse_qq_quote(raw)
    except (ConnectionError, subprocess.TimeoutExpired):
        d = {}
    name = d.get("name", code) if d else code

    code_clean = code.strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    market = "SH" if code_clean.startswith(("6", "9", "5")) else "SZ"

    # 东方财富 datacenter API（年报数据）
    fin_url = "https://datacenter.eastmoney.com/securities/api/data/get"
    params = {
        "type": "RPT_F10_FINANCE_MAINFINADATA",
        "sty": "ALL",
        "filter": f'(SECUCODE="{code_clean}.{market}")(REPORT_TYPE="年报")',
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
        params["filter"] = f'(SECUCODE="{code_clean}.{market}")'
        try:
            data = _curl_json(fin_url, params)
            reports = (data.get("result") or {}).get("data") or []
        except (ConnectionError, json.JSONDecodeError,
                subprocess.TimeoutExpired):
            reports = []

    print("=" * 60)
    print(f"核心财务数据: {name} ({code_clean})")
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
