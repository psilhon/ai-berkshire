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
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

try:
    from tools.ashare_plugin.transport import TransportClient, TransportError
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
    from ashare_plugin.transport import TransportClient, TransportError
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

# 打板三件套（L2，东财免费源，零鉴权）——需求拉动自 quality-screen（涨停生态/治理旁证）。
# 端点实测 2026-07-31 返回真实数据；源参照 a-stock-data V3.6.0 §8.1/§8.4/§8.5。
# ⚠️ 北交所与深市同为 m=0 / 监控池 MARKET="B" 三值，市场判定按代码号段而非 m 字段。
_ZT_UT = "7eea3edcaed734bea9cbfc24409ed989"
_ZT_BASE = "https://push2ex.eastmoney.com"
_MONITOR_URL = "https://mobappconfig.securities.eastmoney.com/emcfg/stock_monitor.json"
_ANOMALY_BASE = "https://dycalchis.eastmoney.com/price-anomaly"
_ANOMALY_HQ_PARAMS = {
    "team": "h5", "product": "EastMoney", "client": "WAP",
    "version": "9001", "name": "WAP", "user": "123",
}
_MONITOR_MARKET = {"1": "SH", "0": "SZ", "B": "BJ"}
_ANOMALY_RULES = {
    1: "主板连续10个交易日内4次同向异常波动",
    2: "创业板连续10个交易日内3次同向异常波动",
    3: "科创板连续10个交易日内3次同向异常波动",
    4: "连续十个交易日内日收盘价涨跌幅偏离值累计+100%",
    5: "连续十个交易日内日收盘价涨跌幅偏离值累计-50%",
    6: "连续三十个交易日内日收盘价涨跌幅偏离值累计+200%",
    7: "连续三十个交易日内日收盘价涨跌幅偏离值累计-70%",
    8: "北交所连续10个交易日内3次同向异常波动",
    40: "连续十个交易日内日收盘价涨跌幅偏离值累计+150%",
    50: "连续十个交易日内日收盘价涨跌幅偏离值累计-60%",
    60: "连续30个交易日内日收盘价涨跌幅偏离值累计+300%",
    70: "连续30个交易日内日收盘价涨跌幅偏离值累计-75%",
}
_CN_TZ = timezone(timedelta(hours=8))

# 热度层（L2）：同花顺热榜(GET) + 东财人气榜(POST)，零依赖；Tushare ths_hot 备用。
# 端点实测 2026-07-31 返回真实数据；源参照 a-stock-data V3.6.0 §10.2。
# ⚠️ 同花顺有反爬风险，故东财人气榜作同优先级备用，Tushare 仅最后回退。
# 热度是 intraday/rolling（period=hour/day），非日期驱动；--date 仅供 Tushare 回退。
_THS_HOT_URL = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
_EM_HOT_URL = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
_EM_HOT_BODY = {
    "appId": "appId01",
    "globalId": "786e4c21-70dc-435a-93bb-38",
    "marketType": "",
    "pageNo": 1,
    "pageSize": 50,
}
_EM_ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
_EM_ULIST_UT = "f057cbcbce2a86e2866ab8877db1d059"

# L3 一手定性 / 快讯 / 研报层（需求拉动：ird-interact / cls-telegraph / report-list）。
# 互动易（巨潮）投资者提问+公司官方回复=一手定性；财联社 v1 本地签名零 key=全市场快讯；
# 东财 reportapi=研报列表（免费源，补 Tushare analyst-reports）。三者均为零鉴权免费源。
_IRM_QUERY_URL = "https://irm.cninfo.com.cn/newircs/index/queryKeyboardInfo"
_IRM_QA_URL = "https://irm.cninfo.com.cn/newircs/company/question"
_CLS_TELEGRAPH_URL = "https://www.cls.cn/v1/roll/get_roll_list"
_REPORT_API = "https://reportapi.eastmoney.com/report/list"
_CLS_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


def _cn_today() -> str:
    """北京时间的今天（YYYYMMDD），避免海外时区跨日错位。"""
    return datetime.now(_CN_TZ).strftime("%Y%m%d")


def _curl(url):
    """兼容旧调用者的文本请求入口，底层统一使用插件 transport。"""
    return _TRANSPORT.get_text(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    })


def _curl_json(url, params=None, headers=None):
    """兼容旧调用者的 JSON 请求入口，底层统一使用插件 transport。

    headers 可追加自定义请求头（如东财 push2ex 的 Referer）；缺省仅带 UA。
    """
    base_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    }
    if headers:
        base_headers.update(headers)
    return _TRANSPORT.get_json(url, params=params, headers=base_headers)


def _curl_json_post(url, data=None, headers=None, json_body: bool = True):
    """POST 请求入口，底层统一使用插件 transport。

    json_body=True（默认）：data 以 JSON 体发送，用于东财 appdata 等人气榜接口；
    json_body=False：data 以 form 编码（urlencode）发送，用于互动易等接口。
    headers 可追加自定义请求头。
    """
    base_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    }
    if headers:
        base_headers.update(headers)
    return _TRANSPORT.post_json(url, data=data, headers=base_headers, json_body=json_body)


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
    try:
        results = _search_candidates(keyword)
    except (ConnectionError, json.JSONDecodeError,
            subprocess.TimeoutExpired) as exc:
        print(f"❌ 搜索股票失败: {exc}", file=sys.stderr)
        return False

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


# === Phase 0: 三大报表 + 资金面 + 概念 + 宏观 + 更名 + 因子 ===

def _format_fin_stmt(rows, title, code):
    """通用三大报表格式化输出。"""
    if not rows:
        print(f"无数据")
        return
    print(f"{'='*60}")
    print(f"{title}: {code}")
    print(f"数据来源: Tushare，共 {len(rows)} 期")
    print(f"{'='*60}\n")
    for row in rows:
        end_date = row.get("end_date", row.get("f_ann_date", "?"))[:10]
        print(f"  --- {end_date} ---")
        for k, v in row.items():
            if k in ("ts_code", "end_date", "ann_date", "f_ann_date", "report_type", "comp_type"):
                continue
            if v is None:
                continue
            try:
                fv = float(v)
                if abs(fv) >= 1e8:
                    print(f"  {k:30s}: {fv/1e8:>12.2f}亿")
                elif abs(fv) >= 1e4:
                    print(f"  {k:30s}: {fv/1e4:>12.2f}万")
                else:
                    print(f"  {k:30s}: {fv:>12.4f}")
            except (ValueError, TypeError):
                print(f"  {k:30s}: {str(v):>12s}")
        print()


def cmd_income_stmt(code: str, years: int = 5, json_output: bool = False):
    """利润表原始数据 — Tushare income"""
    client = _get_tushare_client()
    if not client:
        return False
    ts_code = normalize_code(code).secu_code
    end_years = [str(datetime.now().year - i) + "1231" for i in range(years)]
    all_rows = []
    for ey in end_years:
        r = client.query("income", params={"ts_code": ts_code, "end_date": ey}, fields=[])
        if r.get("ok"):
            all_rows.extend(r["data"])
    if json_output:
        print(json.dumps(all_rows, ensure_ascii=False, indent=2))
    else:
        _format_fin_stmt(all_rows, "利润表", code)
    return True


def cmd_balance_sheet(code: str, years: int = 5, json_output: bool = False):
    """资产负债表 — Tushare balancesheet"""
    client = _get_tushare_client()
    if not client:
        return False
    ts_code = normalize_code(code).secu_code
    end_years = [str(datetime.now().year - i) + "1231" for i in range(years)]
    all_rows = []
    for ey in end_years:
        r = client.query("balancesheet", params={"ts_code": ts_code, "end_date": ey}, fields=[])
        if r.get("ok"):
            all_rows.extend(r["data"])
    if json_output:
        print(json.dumps(all_rows, ensure_ascii=False, indent=2))
    else:
        _format_fin_stmt(all_rows, "资产负债表", code)
    return True


def cmd_cash_flow(code: str, years: int = 5, json_output: bool = False):
    """现金流量表 — Tushare cashflow"""
    client = _get_tushare_client()
    if not client:
        return False
    ts_code = normalize_code(code).secu_code
    end_years = [str(datetime.now().year - i) + "1231" for i in range(years)]
    all_rows = []
    for ey in end_years:
        r = client.query("cashflow", params={"ts_code": ts_code, "end_date": ey}, fields=[])
        if r.get("ok"):
            all_rows.extend(r["data"])
    if json_output:
        print(json.dumps(all_rows, ensure_ascii=False, indent=2))
    else:
        _format_fin_stmt(all_rows, "现金流量表", code)
    return True


def cmd_money_flow(code: str, trade_date: str = None):
    """个股资金流向 — Tushare moneyflow"""
    client = _get_tushare_client()
    if not client:
        return False
    ts_code = normalize_code(code).secu_code
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    r = client.query("moneyflow", params={"ts_code": ts_code, "trade_date": trade_date}, fields=[])
    if not r.get("ok"):
        print(f"❌ Tushare moneyflow 查询失败: {r.get('message', '未知')}")
        return False
    print(f"{'='*60}")
    print(f"资金流向: {code} ({trade_date})")
    print(f"数据来源: Tushare moneyflow")
    print(f"{'='*60}\n")
    for d in r["data"]:
        print(f"  小单买入: {_fmt_yi(d.get('buy_sm_amount'))}  小单卖出: {_fmt_yi(d.get('sell_sm_amount'))}")
        print(f"  中单买入: {_fmt_yi(d.get('buy_md_amount'))}  中单卖出: {_fmt_yi(d.get('sell_md_amount'))}")
        print(f"  大单买入: {_fmt_yi(d.get('buy_lg_amount'))}  大单卖出: {_fmt_yi(d.get('sell_lg_amount'))}")
        print(f"  超大单买入: {_fmt_yi(d.get('buy_elg_amount'))}  超大单卖出: {_fmt_yi(d.get('sell_elg_amount'))}")
        net_mf = float(d.get('net_mf_amount', 0) or 0)
        direction = "主力净流入" if net_mf > 0 else "主力净流出"
        print(f"  {direction}: {_fmt_yi(abs(net_mf))}")
    return True


def cmd_factors(code: str, trade_date: str = None):
    """量化因子 — Tushare stk_factor_pro"""
    client = _get_tushare_client()
    if not client:
        return False
    ts_code = normalize_code(code).secu_code
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    r = client.query("stk_factor_pro", params={"ts_code": ts_code, "trade_date": trade_date}, fields=[])
    if not r.get("ok"):
        print(f"❌ Tushare stk_factor_pro 查询失败: {r.get('message', '未知')}")
        return False
    d = r["data"][0]
    print(f"{'='*60}")
    print(f"量化因子: {code} ({trade_date})")
    print(f"数据来源: Tushare stk_factor_pro")
    print(f"{'='*60}\n")
    for k, v in d.items():
        if k in ("ts_code", "trade_date"):
            continue
        if v is None:
            continue
        print(f"  {k:25s}: {v}")


def cmd_sector_peers(code: str, json_output: bool = False):
    """同花顺概念成分股 — Tushare ths_member"""
    client = _get_tushare_client()
    if not client:
        return False
    r = client.query("ths_member", params={"ts_code": code}, fields=[])
    if not r.get("ok"):
        print(f"❌ Tushare ths_member 查询失败: {r.get('message', '未知')}")
        return False
    if json_output:
        print(json.dumps(r["data"], ensure_ascii=False, indent=2))
    else:
        print(f"{'='*60}")
        print(f"同花顺概念成分股: {code}")
        print(f"数据来源: Tushare ths_member，共 {len(r['data'])} 只")
        print(f"{'='*60}\n")
        for row in r["data"]:
            print(f"  {row.get('ts_code','?')}  {row.get('name','?')}  {row.get('con_code','')}")
    return True


def cmd_macro(indicator: str, period: str = None):
    """宏观经济指标 — Tushare cn_gdp/cn_cpi/cn_m/shibor"""
    client = _get_tushare_client()
    if not client:
        return False
    api_map = {
        "gdp": ("cn_gdp", {}),
        "cpi": ("cn_cpi", {"m": period or datetime.now().strftime("%Y%m")}),
        "m2": ("cn_m", {"m": period or datetime.now().strftime("%Y%m")}),
        "shibor": ("shibor", {"date": period or datetime.now().strftime("%Y%m%d")}),
    }
    api_name, params = api_map[indicator]
    r = client.query(api_name, params=params, fields=[])
    if not r.get("ok"):
        print(f"❌ Tushare {api_name} 查询失败: {r.get('message', '未知')}")
        return False
    print(f"{'='*60}")
    print(f"宏观经济: {indicator.upper()}")
    print(f"数据来源: Tushare {api_name}")
    print(f"{'='*60}\n")
    for row in r["data"][:5]:
        for k, v in row.items():
            if v is not None and str(v).strip():
                print(f"  {k}: {v}")
        print()
    return True


def cmd_name_history(code: str):
    """历史更名 — Tushare namechange"""
    client = _get_tushare_client()
    if not client:
        return False
    ts_code = normalize_code(code).secu_code
    r = client.query("namechange", params={"ts_code": ts_code}, fields=[])
    if not r.get("ok"):
        print(f"❌ Tushare namechange 查询失败: {r.get('message', '未知')}")
        return False
    print(f"{'='*60}")
    print(f"历史名称变更: {code}")
    print(f"数据来源: Tushare namechange，共 {len(r['data'])} 条")
    print(f"{'='*60}\n")
    for row in r["data"]:
        print(f"  {row.get('start_date','?')[:10]} ~ {row.get('end_date','?')[:10] if row.get('end_date') else '至今'}  "
              f"{row.get('name','?')}")
    return True


# === Tier 1 高价值 API（基于 Tushare 完整数据审计） ===

def cmd_limit_price(code: str, trade_date: str = None):
    """涨跌停价格 — Tushare stk_limit"""
    client = _get_tushare_client()
    if not client:
        return False
    ts_code = normalize_code(code).secu_code
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    r = client.query("stk_limit", params={"ts_code": ts_code, "trade_date": trade_date}, fields=[])
    if not r.get("ok"):
        print(f"❌ stk_limit 查询失败: {r.get('message', '未知')}")
        return False
    d = r["data"][0]
    print(f"{'='*60}")
    print(f"涨跌停价格: {code} ({trade_date})")
    print(f"数据来源: Tushare stk_limit")
    print(f"{'='*60}\n")
    up = float(d.get("up_limit", 0))
    dn = float(d.get("down_limit", 0))
    close = float(d.get("close", 0)) if d.get("close") else 0
    print(f"  涨停价: {up:.2f}  跌停价: {dn:.2f}")
    if close:
        pct_to_up = (up - close) / close * 100
        pct_to_dn = (dn - close) / close * 100
        print(f"  收盘价: {close:.2f}  距涨停: {pct_to_up:+.2f}%  距跌停: {pct_to_dn:+.2f}%")
    return True


def cmd_suspend(trade_date: str = None):
    """停复牌信息 — Tushare suspend_d"""
    client = _get_tushare_client()
    if not client:
        return False
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    r = client.query("suspend_d", params={"trade_date": trade_date}, fields=[])
    if not r.get("ok"):
        print(f"❌ suspend_d 查询失败: {r.get('message', '未知')}")
        return False
    print(f"{'='*60}")
    print(f"停复牌信息: {trade_date}")
    print(f"数据来源: Tushare suspend_d，共 {len(r['data'])} 条")
    print(f"{'='*60}\n")
    for d in r["data"][:30]:
        print(f"  {d.get('ts_code','?')}  {d.get('name','?')}  类型={d.get('suspend_type','?')}  "
              f"{d.get('suspend_timing','')}")
    if len(r["data"]) > 30:
        print(f"  ... 共 {len(r['data'])} 条，仅显示前 30")
    return True


def cmd_weekly(code: str, trade_date: str = None):
    """周线行情 — Tushare weekly"""
    client = _get_tushare_client()
    if not client:
        return False
    ts_code = normalize_code(code).secu_code
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    r = client.query("weekly", params={"ts_code": ts_code, "trade_date": trade_date, "fields": "ts_code,trade_date,open,high,low,close,vol,amount"}, fields=[])
    if not r.get("ok"):
        print(f"❌ weekly 查询失败: {r.get('message', '未知')}")
        return False
    d = r["data"][0]
    print(f"{'='*60}")
    print(f"周线行情: {code} (周止 {trade_date})")
    print(f"数据来源: Tushare weekly")
    print(f"{'='*60}\n")
    for k, v in d.items():
        if k in ("ts_code",):
            continue
        try:
            fv = float(v)
            print(f"  {k:15s}: {fv}")
        except (ValueError, TypeError):
            print(f"  {k:15s}: {v}")


def cmd_monthly(code: str, trade_date: str = None):
    """月线行情 — Tushare monthly"""
    client = _get_tushare_client()
    if not client:
        return False
    ts_code = normalize_code(code).secu_code
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    r = client.query("monthly", params={"ts_code": ts_code, "trade_date": trade_date}, fields=[])
    if not r.get("ok"):
        print(f"❌ monthly 查询失败: {r.get('message', '未知')}")
        return False
    d = r["data"][0]
    print(f"{'='*60}")
    print(f"月线行情: {code} (月止 {trade_date})")
    print(f"数据来源: Tushare monthly")
    print(f"{'='*60}\n")
    for k, v in d.items():
        if k in ("ts_code",):
            continue
        try:
            fv = float(v)
            print(f"  {k:15s}: {fv}")
        except (ValueError, TypeError):
            print(f"  {k:15s}: {v}")


def cmd_broker_recommend(month: str = None):
    """券商月度金股 — Tushare broker_recommend"""
    client = _get_tushare_client()
    if not client:
        return False
    if not month:
        month = datetime.now().strftime("%Y%m")
    r = client.query("broker_recommend", params={"month": month}, fields=[])
    if not r.get("ok"):
        print(f"❌ broker_recommend 查询失败: {r.get('message', '未知')}")
        return False
    print(f"{'='*60}")
    print(f"券商月度金股推荐: {month}")
    print(f"数据来源: Tushare broker_recommend，共 {len(r['data'])} 条")
    print(f"{'='*60}\n")
    for d in r["data"][:30]:
        print(f"  {d.get('ts_code','?')}  {d.get('name','?')}  "
              f"券商={d.get('broker','?')}  月度={d.get('month','')}")
    if len(r["data"]) > 30:
        print(f"  ... 共 {len(r['data'])} 条")
    return True


def cmd_cyq_chips(code: str, trade_date: str = None):
    """每日筹码分布 — Tushare cyq_perf"""
    client = _get_tushare_client()
    if not client:
        return False
    ts_code = normalize_code(code).secu_code
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    r = client.query("cyq_perf", params={"ts_code": ts_code, "trade_date": trade_date}, fields=[])
    if not r.get("ok"):
        print(f"❌ cyq_perf 查询失败: {r.get('message', '未知')}")
        return False
    d = r["data"][0]
    print(f"{'='*60}")
    print(f"每日筹码分布: {code} ({trade_date})")
    print(f"数据来源: Tushare cyq_perf")
    print(f"{'='*60}\n")
    for k, v in d.items():
        if k in ("ts_code", "trade_date"):
            continue
        try:
            fv = float(v)
            if abs(fv) >= 1e8:
                print(f"  {k:20s}: {fv/1e8:>10.2f}亿")
            elif abs(fv) >= 1e4:
                print(f"  {k:20s}: {fv/1e4:>10.2f}万")
            else:
                print(f"  {k:20s}: {fv:>10.4f}")
        except (ValueError, TypeError):
            print(f"  {k:20s}: {v}")
    return True


def cmd_limit_list(trade_date: str = None):
    """涨跌停数据 — Tushare limit_list_d"""
    client = _get_tushare_client()
    if not client:
        return False
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    r = client.query("limit_list_d", params={"trade_date": trade_date}, fields=[])
    if not r.get("ok"):
        print(f"❌ limit_list_d 查询失败: {r.get('message', '未知')}")
        return False
    print(f"{'='*60}")
    print(f"涨跌停数据: {trade_date}")
    print(f"数据来源: Tushare limit_list_d，共 {len(r['data'])} 条")
    print(f"{'='*60}\n")
    for d in r["data"][:30]:
        is_up = d.get("limit") == "U"
        is_dt = d.get("limit") == "D"
        emoji = "🟥" if is_up else ("🟩" if is_dt else "⬜")
        print(f"  {emoji} {d.get('ts_code','?')}  {d.get('name','?')}  "
              f"涨{d.get('pct_chg','?')}%  成交{d.get('amount','?')}")
    if len(r["data"]) > 30:
        print(f"  ... 共 {len(r['data'])} 条")
    return True


def cmd_top_list(trade_date: str = None):
    """龙虎榜 — Tushare top_list"""
    client = _get_tushare_client()
    if not client:
        return False
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    r = client.query("top_list", params={"trade_date": trade_date}, fields=[])
    if not r.get("ok"):
        print(f"❌ top_list 查询失败: {r.get('message', '未知')}")
        return False
    print(f"{'='*60}")
    print(f"龙虎榜: {trade_date}")
    print(f"数据来源: Tushare top_list，共 {len(r['data'])} 条")
    print(f"{'='*60}\n")
    for d in r["data"][:20]:
        print(f"  {d.get('ts_code','?')}  {d.get('name','?')}  "
              f"净买={d.get('net_amount','?')}万  涨跌幅={d.get('pct_change','?')}%")
    if len(r["data"]) > 20:
        print(f"  ... 共 {len(r['data'])} 条")
    return True


def cmd_unblock(code: str, end_date: str = None, limit: int = 10):
    """限售股解禁 — Tushare share_float"""
    client = _get_tushare_client()
    if not client:
        return False
    ts_code = normalize_code(code).secu_code
    if not end_date:
        end_date = datetime.now().strftime("%Y%m%d")
    r = client.query("share_float", params={"ts_code": ts_code, "end_date": end_date}, fields=[])
    if not r.get("ok"):
        print(f"❌ share_float 查询失败: {r.get('message', '未知')}")
        return False
    print(f"{'='*60}")
    print(f"限售股解禁: {code}")
    print(f"数据来源: Tushare share_float，共 {len(r['data'])} 条")
    print(f"{'='*60}\n")
    for d in r["data"][:limit]:
        print(f"  {d.get('float_date','?')}  解禁{d.get('float_share','?')}股  "
              f"占比{d.get('float_ratio','?')}%  类型={d.get('share_type','?')}")
    if len(r["data"]) > limit:
        print(f"  ... 共 {len(r['data'])} 条，仅显示前 {limit}")
    return True


def cmd_block_trade(code: str, trade_date: str = None):
    """大宗交易 — Tushare block_trade"""
    client = _get_tushare_client()
    if not client:
        return False
    ts_code = normalize_code(code).secu_code
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    r = client.query("block_trade", params={"ts_code": ts_code, "trade_date": trade_date}, fields=[])
    if not r.get("ok"):
        print(f"❌ block_trade 查询失败: {r.get('message', '未知')}")
        return False
    if not r["data"]:
        print(f"无大宗交易")
        return True
    print(f"{'='*60}")
    print(f"大宗交易: {code} ({trade_date})")
    print(f"数据来源: Tushare block_trade")
    print(f"{'='*60}\n")
    for d in r["data"]:
        print(f"  {d.get('trade_date','?')}  价{d.get('price','?')}  "
              f"量{d.get('vol','?')}万  买方={d.get('buyer','?','')}  卖方={d.get('seller','?','')}")
    return True


def _ths_hot_list(period: str = "hour") -> list:
    """同花顺热榜（GET，零依赖）：名称+人气值+概念标签+排名变化。

    失败或空返回 []（非抛异常），交由调用方回退东财 / Tushare。
    """
    try:
        obj = _curl_json(
            _THS_HOT_URL,
            params={"stock_type": "a", "type": period, "list_type": "normal"},
            headers={"Referer": "https://q.10jqka.com.cn/"},
        )
    except TransportError:
        return []
    if not isinstance(obj, dict):
        return []
    lst = ((obj.get("data") or {}).get("stock_list")) or []
    out = []
    for it in lst:
        tag = it.get("tag") or {}
        out.append({
            "rank": it.get("order"),
            "code": it.get("code"),
            "name": it.get("name"),
            "heat": it.get("rate"),
            "pct": it.get("rise_and_fall"),
            "rank_chg": it.get("hot_rank_chg"),
            "concepts": (tag.get("concept_tag") or [])[:5],
            "tag": tag.get("popularity_tag", ""),
        })
    return out


def _em_hot_rank(top: int = 50) -> list:
    """东财人气榜（POST，零依赖）：排名+排名变化+名称/价格。

    仅返回带前缀代码，需再走 push2 ulist.np 补名称/价格。
    失败或空返回 []（非抛异常）。
    """
    try:
        obj = _curl_json_post(_EM_HOT_URL, data={**_EM_HOT_BODY, "pageSize": top})
    except TransportError:
        return []
    if not isinstance(obj, dict):
        return []
    data = obj.get("data") or []
    if not data:
        return []
    secids = [
        ("0." if it["sc"].startswith("SZ") else "1.") + it["sc"][2:]
        for it in data
    ]
    try:
        u = _curl_json(
            _EM_ULIST_URL,
            params={
                "ut": _EM_ULIST_UT, "fltt": 2, "invt": 2,
                "fields": "f14,f3,f12,f2", "secids": ",".join(secids),
            },
            headers={"Referer": "https://quote.eastmoney.com/"},
        )
    except TransportError:
        u = None
    diff = (((u or {}).get("data") or {}).get("diff")) or [] if isinstance(u, dict) else []
    if isinstance(diff, dict):
        diff = list(diff.values())
    nm = {
        x["f12"]: (x.get("f14"), x.get("f2"), x.get("f3"))
        for x in diff if isinstance(x, dict) and "f12" in x
    }
    out = []
    for it in data:
        code = it["sc"][2:]
        name, price, pct = nm.get(code, ("", None, None))
        out.append({
            "rank": it.get("rk"), "code": code, "name": name,
            "pct": pct, "rank_chg": it.get("hisRc"),
        })
    return out


def _ths_hot_tushare(trade_date: str = None) -> bool:
    """Tushare ths_hot 回退（plan §3.1 备用）：需 TUSHARE_TOKEN。"""
    client = _get_tushare_client()
    if not client:
        return False
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    r = client.query("ths_hot", params={"trade_date": trade_date}, fields=[])
    if not r.get("ok"):
        print(f"❌ ths_hot 查询失败: {r.get('message', '未知')}")
        return False
    print(f"{'='*60}")
    print(f"市场热度榜（回退 Tushare ths_hot）: {trade_date}")
    print(f"数据来源: Tushare ths_hot，共 {len(r['data'])} 条")
    print(f"{'='*60}\n")
    for d in r["data"][:50]:
        print(f"  {d.get('ts_code','?')}  {d.get('name','?')}  排名={d.get('rank','?')}  "
              f"涨{d.get('pct_change','?')}%  热度={d.get('hot_value','')}")
    if len(r["data"]) > 50:
        print(f"  ... 共 {len(r['data'])} 条")
    return True


def cmd_ths_hot(period: str = "hour", trade_date: str = None, top: int = 50):
    """市场热度榜（L2 热度层）。

    零依赖优先：同花顺热榜(GET) → 东财人气榜(POST)；
    两者均失败且无 Tushare token 时回退 Tushare ths_hot（plan §3.1 备用）。
    period: hour/day（仅零依赖路径使用）；trade_date 仅供 Tushare 回退；top 返回条数。
    """
    rows = _ths_hot_list(period)
    source = "同花顺热榜"
    if not rows:
        rows = _em_hot_rank(top)
        source = "东财人气榜"

    if rows:
        print(f"{'='*60}")
        print(f"市场热度榜（来源：{source}，period={period}）")
        print(f"数据来源: {source}（零依赖 curl），共 {len(rows)} 条")
        print(f"{'='*60}\n")
        for r in rows[:top]:
            pct = r.get("pct")
            pct_s = f"{pct:.2f}%" if isinstance(pct, (int, float)) else "?"
            extra = ""
            if r.get("heat") is not None:
                extra += f" 热度={r['heat']}"
            if r.get("concepts"):
                extra += f" 概念={','.join(r['concepts'][:3])}"
            if r.get("tag"):
                extra += f" [{r['tag']}]"
            print(f"  #{r.get('rank','?')} {r.get('name','?')}({r.get('code','?')}) "
                  f"涨{pct_s} 排名变={r.get('rank_chg','?')}{extra}")
        if len(rows) > top:
            print(f"  ... 共 {len(rows)} 条")
        return True

    print("[ths-hot] 零依赖源（同花顺/东财）均未返回数据，尝试 Tushare ths_hot 回退…")
    return _ths_hot_tushare(trade_date)


# === L3 一手定性 / 快讯 / 研报层（需求拉动：ird-interact / cls-telegraph / report-list）===
# 三者均为零鉴权免费源，作为独立子命令交付、由消费方 skill 调用；不进 run-level 逐股链（ADR-003）。

def _cls_sign(params: dict) -> str:
    """财联社本地签名：md5(sha1(按 key 字典序拼接的 query 串))，纯本地计算、无需任何 key。"""
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()


def cmd_ird_interact(code: str, limit: int = 20):
    """互动易问答（L3 一手定性层）— 巨潮：投资者提问 + 公司官方回复。

    两步：① queryKeyboardInfo 按代码定 orgId(secid)；② company/question 拉问答（参数放 query string）。
    坑：第二步参数须放 query string（非 body）否则 HTTP 400；orgId 取自第一步 secid；
    最新提问常未回复（attachedContent=None）；pubDate 为毫秒时间戳。
    management-deep-dive 消费方：看公司如何回应传闻/利好的一手定性材料。
    """
    try:
        code = normalize_code(code).code
    except ValueError as exc:
        print(f"❌ 代码无效: {exc}")
        return False
    try:
        d1 = _curl_json_post(_IRM_QUERY_URL, data={"keyWord": code}, json_body=False)
    except (ConnectionError, json.JSONDecodeError, subprocess.TimeoutExpired, TransportError) as exc:
        print(f"❌ 互动易定码失败: {exc}")
        return False
    d1_list = (d1.get("data") or []) if isinstance(d1, dict) else []
    if not d1_list:
        print(f"⚠️ 互动易未检索到 {code} 的 IR 主体")
        return False
    org_id = d1_list[0].get("secid")
    params = {
        "_t": "1", "stockcode": code, "orgId": org_id, "pageSize": str(limit),
        "pageNum": "1", "keyWord": "", "startDay": "", "endDay": "",
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    try:
        d2 = _curl_json_post(f"{_IRM_QA_URL}?{qs}", data=None, json_body=False)
    except (ConnectionError, json.JSONDecodeError, subprocess.TimeoutExpired, TransportError) as exc:
        print(f"❌ 互动易问答请求失败: {exc}")
        return False
    rows = (d2.get("rows") or []) if isinstance(d2, dict) else []
    if not rows:
        print(f"⚠️ {code} 互动易当前无问答记录（部分公司回复率极低）")
        return False
    print(f"{'='*60}")
    print(f"互动易问答: {code}（共 {d2.get('total', len(rows))} 条，显示前 {len(rows)}）")
    print(f"数据来源: 巨潮互动易（一手定性：投资者提问 + 公司官方回复）")
    print(f"{'='*60}\n")
    shown = 0
    for it in rows:
        q = it.get("mainContent") or ""
        a = it.get("attachedContent")
        pd = it.get("pubDate")
        t = datetime.fromtimestamp(pd / 1000).strftime("%Y-%m-%d %H:%M") if pd else ""
        answerer = it.get("attachedAuthor") or "未回复"
        print(f"  [{t}] Q: {q[:60]}")
        if a:
            print(f"    A[{answerer}]: {a[:80]}")
        else:
            print(f"    A: （公司尚未回复）")
        shown += 1
        if shown >= limit:
            break
    return True


def cmd_cls_telegraph(top: int = 50):
    """财联社实时电报（L3 快讯层）— 全市场实时快讯，v1 API + 本地签名零 key。

    sign = md5(sha1(按 key 字典序拼接的 query 串))，纯本地算无需 key；与东财 7×24 互为独立备份。
    news-pulse 消费方：把快讯底层从 WebFetch 换成结构化取数，作 L3 快讯旁证。
    """
    params = {
        "appName": "CailianpressWeb", "os": "web", "sv": "7.7.5",
        "last_time": "", "refresh_type": "1", "rn": str(top),
    }
    sign = _cls_sign(params)
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    url = f"{_CLS_TELEGRAPH_URL}?{qs}&sign={sign}"
    try:
        data = _curl_json(url, headers={"User-Agent": _CLS_UA, "Referer": "https://www.cls.cn/"})
    except (ConnectionError, json.JSONDecodeError, subprocess.TimeoutExpired, TransportError) as exc:
        print(f"❌ 财联社电报请求失败: {exc}")
        return False
    if not isinstance(data, dict) or data.get("errno") != 0:
        err_no = data.get("errno") if isinstance(data, dict) else "?"
        err_msg = data.get("msg") if isinstance(data, dict) else ""
        print(f"❌ 财联社电报返回错误: errno={err_no} msg={err_msg}")
        return False
    rows = (data.get("data") or {}).get("roll_data", []) or []
    if not rows:
        print("⚠️ 财联社电报当前无数据")
        return False
    print(f"{'='*60}")
    print(f"财联社实时电报（全市场快讯，共 {len(rows)} 条）")
    print(f"数据来源: 财联社 v1（本地签名零 key）")
    print(f"{'='*60}\n")
    for it in rows[:top]:
        ts = it.get("ctime")
        t = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        title = it.get("title") or it.get("brief") or ""
        content = it.get("content") or it.get("brief") or ""
        print(f"  {t} | {title}")
        if content and content != title:
            print(f"      {content[:80]}")
    return True


def cmd_report_list(code: str = None, industry: str = None, limit: int = 30):
    """研报列表（L3 研报层）— 东财 reportapi 免费源，补 Tushare analyst-reports。

    code 给定 → 个股研报(qType=0)；--industry 给定 → 行业研报(qType=1)；二者须其一。
    ⚠️ reportapi 只认纯 6 位代码；北交所老号段(43/83/87)需先迁 920 码。
    investment-research 消费方：卖方一致预期交叉验证的免费源（评级/目标价/EPS）。
    """
    if industry:
        qtype, scope_code = "1", industry
        scope = f"行业研报(industry={industry})"
    elif code:
        try:
            scope_code = normalize_code(code).code
        except ValueError as exc:
            print(f"❌ 代码无效: {exc}")
            return False
        qtype, scope_code = "0", scope_code
        scope = f"个股研报({scope_code})"
    else:
        print("❌ 需指定股票代码（个股研报）或 --industry（行业研报）")
        return False

    all_rows = []
    pages = 0
    max_pages = 5
    while len(all_rows) < limit and pages < max_pages:
        pages += 1
        params = {
            "industryCode": industry if qtype == "1" else "*",
            "pageSize": str(min(limit, 100)),
            "industry": "*", "rating": "*", "ratingChange": "*",
            "beginTime": "2000-01-01", "endTime": "2030-01-01",
            "pageNo": str(pages), "fields": "", "qType": qtype,
            "orgCode": "", "code": scope_code if qtype == "0" else "",
            "rcode": "",
            "p": str(pages), "pageNum": str(pages), "pageNumber": str(pages),
        }
        try:
            d = _curl_json(_REPORT_API, params=params,
                           headers={"Referer": "https://data.eastmoney.com/"})
        except (ConnectionError, json.JSONDecodeError, subprocess.TimeoutExpired, TransportError) as exc:
            print(f"❌ 研报请求失败: {exc}")
            return False
        if not isinstance(d, dict):
            break
        rows = d.get("data") or []
        if not rows:
            break
        all_rows.extend(rows)
        if pages >= (d.get("TotalPage", 1) or 1):
            break

    if not all_rows:
        print(f"⚠️ {scope} 东财研报库无覆盖（北交所老号段需先迁 920 码）")
        return False
    show = all_rows[:limit]
    print(f"{'='*60}")
    print(f"研报列表: {scope}（共 {len(all_rows)} 篇，显示前 {len(show)}）")
    print(f"数据来源: 东财 reportapi（免费源，补 Tushare analyst-reports）")
    print(f"{'='*60}\n")
    for r in show:
        pd = (r.get("publishDate") or "")[:10]
        print(f"  [{pd}] {r.get('orgSName', '?')} | {r.get('title', '?')[:50]}")
        rating = r.get("emRatingName") or ""
        eps = r.get("predictThisYearEps") or ""
        if rating or eps:
            print(f"      评级={rating} 今年EPS预测={eps}")
    return True


def cmd_stk_factor(code: str, trade_date: str = None):
    """股票技术面因子(基础版) — Tushare stk_factor"""
    client = _get_tushare_client()
    if not client:
        return False
    ts_code = normalize_code(code).secu_code
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    r = client.query("stk_factor", params={"ts_code": ts_code, "trade_date": trade_date}, fields=[])
    if not r.get("ok"):
        print(f"❌ stk_factor 查询失败: {r.get('message', '未知')}")
        return False
    d = r["data"][0]
    print(f"{'='*60}")
    print(f"技术因子: {code} ({trade_date})")
    print(f"数据来源: Tushare stk_factor")
    print(f"{'='*60}\n")
    for k, v in d.items():
        if k in ("ts_code", "trade_date"):
            continue
        try:
            fv = float(v)
            if abs(fv) >= 1e8:
                print(f"  {k:20s}: {fv/1e8:>10.2f}亿")
            else:
                print(f"  {k:20s}: {fv:>10.4f}")
        except (ValueError, TypeError):
            print(f"  {k:20s}: {v}")
    return True


# === Tier 1b: 券商研报/北向资金/融资融券/板块资金流 ===

def cmd_analyst_reports(code: str, limit: int = 20):
    """券商研报 — Tushare report_rc"""
    client = _get_tushare_client()
    if not client:
        return False
    ts_code = normalize_code(code).secu_code
    r = client.query("report_rc", params={"ts_code": ts_code}, fields=[])
    if not r.get("ok"):
        print(f"❌ report_rc 查询失败: {r.get('message', '未知')}")
        return False
    print(f"{'='*60}")
    print(f"券商研报: {code}")
    print(f"数据来源: Tushare report_rc，共 {len(r['data'])} 篇")
    print(f"{'='*60}\n")
    for d in r["data"][:limit]:
        print(f"  [{d.get('report_date','?')[:10]}] {d.get('org_name','?')}")
        print(f"    标题: {str(d.get('report_title',''))[:60]}")
        print(f"    评级: {d.get('rating','?')}  目标价: {d.get('tp','?')}  "
              f"EPS: {d.get('eps','?')}  PE: {d.get('pe','?')}")
        print()
    if len(r["data"]) > limit:
        print(f"  ... 共 {len(r['data'])} 篇，仅显示前 {limit}")
    return True


def cmd_hsgt_flow(trade_date: str = None):
    """沪深港通资金流向 — Tushare moneyflow_hsgt"""
    client = _get_tushare_client()
    if not client:
        return False
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    r = client.query("moneyflow_hsgt", params={"trade_date": trade_date}, fields=[])
    if not r.get("ok"):
        print(f"❌ moneyflow_hsgt 查询失败: {r.get('message', '未知')}")
        return False
    d = r["data"][0]
    print(f"{'='*60}")
    print(f"沪深港通资金流向: {trade_date}")
    print(f"数据来源: Tushare moneyflow_hsgt")
    print(f"{'='*60}\n")
    north = float(d.get("north_money", 0) or 0)
    south = float(d.get("south_money", 0) or 0)
    print(f"  北向资金净流入: {north/1e4:.2f}亿元")
    print(f"  南向资金净流入: {south/1e4:.2f}亿元")
    print(f"  沪股通: {float(d.get('hgt',0))/1e4:.2f}亿  深股通: {float(d.get('sgt',0))/1e4:.2f}亿")
    print(f"  港股通(沪): {float(d.get('ggt_ss',0))/1e4:.2f}亿  港股通(深): {float(d.get('ggt_sz',0))/1e4:.2f}亿")
    return True


def cmd_hsgt_top10(trade_date: str = None):
    """沪深港通十大成交股 — Tushare hsgt_top10"""
    client = _get_tushare_client()
    if not client:
        return False
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    r = client.query("hsgt_top10", params={"trade_date": trade_date}, fields=[])
    if not r.get("ok"):
        print(f"❌ hsgt_top10 查询失败: {r.get('message', '未知')}")
        return False
    print(f"{'='*60}")
    print(f"沪深港通十大成交股: {trade_date}")
    print(f"数据来源: Tushare hsgt_top10，共 {len(r['data'])} 条")
    print(f"{'='*60}\n")
    for d in r["data"][:20]:
        amt = float(d.get("amount", 0) or 0)
        print(f"  {d.get('name','?'):10s}  净买={amt/1e4:.2f}亿  通道={d.get('channel','')}")
    return True


def cmd_sector_flow(source: str = "ths", trade_date: str = None):
    """板块资金流向 — Tushare moneyflow_ths/moneyflow_dc"""
    client = _get_tushare_client()
    if not client:
        return False
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    api = f"moneyflow_{source}"
    r = client.query(api, params={"trade_date": trade_date}, fields=[])
    if not r.get("ok"):
        print(f"❌ {api} 查询失败: {r.get('message', '未知')}")
        return False
    print(f"{'='*60}")
    print(f"板块资金流向({source.upper()}): {trade_date}")
    print(f"数据来源: Tushare {api}，共 {len(r['data'])} 个板块")
    print(f"{'='*60}\n")
    # Sort by net inflow
    sorted_data = sorted(r["data"], key=lambda x: float(x.get("net_amount", 0) or 0), reverse=True)
    print("  --- 净流入前 15 ---")
    for d in sorted_data[:15]:
        net = float(d.get("net_amount", 0) or 0)
        name = d.get("name", "?")
        print(f"  {name:20s}  净流入={net/1e4:+.2f}亿")
    print("\n  --- 净流出前 15 ---")
    for d in sorted_data[-15:]:
        net = float(d.get("net_amount", 0) or 0)
        name = d.get("name", "?")
        print(f"  {name:20s}  净流入={net/1e4:+.2f}亿")
    return True


def cmd_margin(code: str = None, trade_date: str = None):
    """融资融券 — Tushare margin/margin_detail"""
    client = _get_tushare_client()
    if not client:
        return False
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    if code:
        # 个股融资融券明细
        ts_code = normalize_code(code).secu_code
        r = client.query("margin_detail", params={"ts_code": ts_code, "trade_date": trade_date}, fields=[])
        if not r.get("ok"):
            # fallback to summary
            r = client.query("margin", params={"trade_date": trade_date}, fields=[])
        title = f"融资融券: {code} ({trade_date})"
    else:
        r = client.query("margin", params={"trade_date": trade_date}, fields=[])
        title = f"融资融券汇总: {trade_date}"
    if not r.get("ok"):
        print(f"❌ margin 查询失败: {r.get('error_type', r.get('message', '未知'))}")
        print(f"  (可能该日非交易日或无数据)")
        return True  # Not a hard error
    print(f"{'='*60}")
    print(title)
    print(f"数据来源: Tushare margin/margin_detail，共 {len(r['data'])} 条")
    print(f"{'='*60}\n")
    for d in r["data"][:15]:
        rzye = float(d.get("rzye", 0) or 0)
        rqye = float(d.get("rqye", 0) or 0)
        print(f"  {d.get('ts_code','汇总'):12s}  融资余额={rzye/1e8:.2f}亿  融券余额={rqye/1e8:.2f}亿")
    return True


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

    补管线"无复权 OHLC 序列"缺口：独立历史价格源（对 news-pulse/thesis-tracker），
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


# ===========================================================================
# 打板三件套（L2，东财免费源，零鉴权）
# limit-pool / monitor-pool / anomaly-pool —— 全市场级（--date，非逐股）。
# 端点实测 2026-07-31 返回真实数据；源参照 a-stock-data V3.6.0 §8.1/§8.4/§8.5。
# 注意：北交所与深市同为 m=0 / 监控池 MARKET="B" 三值，市场判定须按代码号段而非 m 字段。
# 三件套均为情绪/治理旁证，不参与 quality-screen 的 7 条硬指标判决。
# ===========================================================================

def _zt_pool(endpoint: str, sort: str, trade_date: str) -> list:
    """东财涨停板行情中心通用请求（push2ex）。

    endpoint: getTopicZTPool(涨停) / getTopicZBPool(炸板) / getTopicDTPool(跌停) /
              getYesterdayZTPool(昨涨停)。返回 data.pool 原始列表；
    data 为 null = 非交易日 / 参数错。失败返回 []（不抛栈）。
    """
    url = f"{_ZT_BASE}/{endpoint}"
    params = {"ut": _ZT_UT, "dpt": "wz.ztzt", "Pageindex": 0,
              "pagesize": 10000, "sort": sort, "date": trade_date}
    headers = {"Referer": "https://quote.eastmoney.com/"}
    try:
        data = _curl_json(url, params=params, headers=headers)
    except (TransportError, ConnectionError, json.JSONDecodeError,
            subprocess.TimeoutExpired) as exc:
        print(f"❌ 涨停板池 {endpoint} 请求失败: {exc}", file=sys.stderr)
        return []
    return (data.get("data") or {}).get("pool") or []


def _fmt_zt_time(t) -> str:
    """涨停板时间整数 → HH:MM:SS（92500 → 09:25:00）。"""
    s = str(t).zfill(6)
    return f"{s[0:2]}:{s[2:4]}:{s[4:6]}"


def cmd_limit_pool(trade_date: str = None):
    """涨停生态池（L2）：涨停/炸板/跌停/昨涨停四维，市场情绪证据。

    全市场级，--date YYYYMMDD（默认今天）。不参与 quality-screen 7 条硬指标判决，仅作情绪旁证。
    """
    if not trade_date:
        trade_date = _cn_today()
    blocks = [
        ("涨停池", "getTopicZTPool", "fbt:asc",
         lambda p: f"  🟥 {p['c']} {p['n']}  连板{p.get('lbc', '?')}  "
                   f"封单{p.get('fund', 0) / 1e8:.2f}亿 炸板{p.get('zbc', 0)}次 "
                   f"{p.get('hybk', '')} 首封{_fmt_zt_time(p.get('fbt', 0))}"),
        ("炸板池", "getTopicZBPool", "fbt:asc",
         lambda p: f"  🟧 {p['c']} {p['n']}  炸板{p.get('zbc', 0)}次 振幅{p.get('zf', 0)}% "
                   f"涨速{p.get('zs', 0)}% {p.get('hybk', '')}"),
        ("跌停池", "getTopicDTPool", "fund:asc",
         lambda p: f"  🟩 {p['c']} {p['n']}  封单{p.get('fund', 0) / 1e8:.2f}亿 "
                   f"连板{p.get('days', '?')} 开板{p.get('oc', 0)}次 {p.get('hybk', '')}"),
        ("昨涨停", "getYesterdayZTPool", "fbt:asc",
         lambda p: f"  ⬜ {p['c']} {p['n']}  昨涨停 {p.get('hybk', '')}"),
    ]
    print("=" * 64)
    print(f"涨停生态池: {trade_date}")
    print("=" * 64)
    total = 0
    for label, endpoint, sort, fmt in blocks:
        try:
            rows = _zt_pool(endpoint, sort, trade_date)
        except Exception as exc:
            print(f"  [{label}] 获取失败: {exc}", file=sys.stderr)
            continue
        total += len(rows)
        print(f"\n  {label}（{len(rows)} 只）")
        for p in rows[:30]:
            try:
                print(fmt(p))
            except Exception:
                continue
        if len(rows) > 30:
            print(f"  ... 共 {len(rows)} 只")
    if total == 0:
        print("❌ 未获取到涨停生态数据（非交易日或接口无返回），建议通过 WebSearch 补充",
              file=sys.stderr)
        return False
    print(f"\n  数据来源: 东方财富 push2ex（涨停/炸板/跌停/昨涨停）| 合计 {total} 条")
    print("  ⚠️ 本池为全市场情绪旁证，不参与个股去劣硬指标判决")
    return True


def cmd_monitor_pool(trade_date: str = None):
    """重点监控池（L2）：交易所风险警示/重点监控名单 + 生效时间窗。

    全市场级，静态 JSON（mobappconfig）。--date 用于过滤监控窗口（默认今天，北京时间）。
    北交所 MARKET='B' 三值，须原样保留避免错标。
    """
    if not trade_date:
        trade_date = _cn_today()
    try:
        rows = _curl_json(_MONITOR_URL,
                          headers={"Referer": "https://vipmoney.eastmoney.com/"}) or []
    except (TransportError, ConnectionError, json.JSONDecodeError,
            subprocess.TimeoutExpired) as exc:
        print(f"❌ 重点监控池请求失败: {exc}", file=sys.stderr)
        return False
    today = (f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
             if len(trade_date) == 8 else trade_date)
    active = []
    for x in rows:
        start, end = x.get("VALIDATESTARTDATE", ""), x.get("VALIDATEENDDATE", "")
        if not (start <= today <= end):
            continue
        raw_mkt = str(x.get("MARKET", "")).upper()
        active.append({
            "code": x.get("STKCODE", ""),
            "name": x.get("STKNAME", ""),
            "market": _MONITOR_MARKET.get(raw_mkt, f"?{raw_mkt}"),
            "start": start, "end": end,
        })
    print("=" * 64)
    print(f"重点监控池: {today}（生效窗口内 {len(active)} 只 / 全量 {len(rows)} 只）")
    print("=" * 64)
    if not active:
        print("  当前无处于监控窗口内的标的")
        return True
    for s in active:
        print(f"  ⚠️ {s['code']} {s['name']}({s['market']}) 监控期 {s['start']}~{s['end']}")
    print("\n  数据来源: 东方财富 mobappconfig（交易所重点监控名单）")
    print("  ⚠️ 命中重点监控=治理红线告警，仅作旁证不作判决")
    return True


def _anomaly_market(code, m, board=None) -> str:
    """异动记录 → 交易所。北交所与深市同为 m=0，按代码号段优先判定。"""
    c = str(code or "")
    if c.startswith("920") or c[:2] in ("43", "83", "87") or board == 8:
        return "BJ"
    return "SH" if m == 1 else "SZ"


def cmd_anomaly_pool(trade_date: str = None):
    """日内异动池（L2）：交易所「严重异常波动」口径的异动明细。

    全市场级（dycalchis）。须带 team=h5 固定参数，否则返回 unknow team。
    返回最近交易日异动；--date 仅用于显示比对（接口不接 date 入参）。
    """
    params = {**_ANOMALY_HQ_PARAMS, "pageSize": "200", "pageNo": "1"}
    try:
        data = _curl_json(f"{_ANOMALY_BASE}/list", params=params,
                          headers={"Referer": "https://vipmoney.eastmoney.com/"})
    except (TransportError, ConnectionError, json.JSONDecodeError,
            subprocess.TimeoutExpired) as exc:
        print(f"❌ 日内异动池请求失败: {exc}", file=sys.stderr)
        return False
    if data.get("result") != 0:
        print(f"❌ 日内异动池接口拒绝: result={data.get('result')} msg={data.get('msg')!r}",
              file=sys.stderr)
        return False
    items = []
    for x in data.get("data") or []:
        e = x.get("e")
        key = e * 10 if (x.get("s") == 6 and e in (4, 5, 6, 7)) else e
        items.append({
            "code": x.get("c"), "name": x.get("n"),
            "market": _anomaly_market(x.get("c"), x.get("m"), x.get("s")),
            "change_pct": x.get("a"), "deviation": x.get("x"),
            "days": x.get("d"), "rule_code": key,
            "rule": _ANOMALY_RULES.get(key, f"未知规则码 {key}"),
            "is_today": x.get("o") != 2,
        })
    date = str(data.get("date", ""))
    print("=" * 64)
    print(f"日内异动池: {date}")
    print("=" * 64)
    if not items:
        print("  当日无严重异常波动标的")
        return True
    for s in items[:30]:
        flag = "今日" if s["is_today"] else "历史"
        print(f"  🔥 {s['code']} {s['name']}({s['market']}) {s['change_pct']}% "
              f"偏离{s['deviation']}%/{s['days']}日 | {s['rule']} [{flag}]")
    if len(items) > 30:
        print(f"  ... 共 {len(items)} 条")
    if trade_date and trade_date != date:
        print(f"  ⚠️ 请求日期 {trade_date} 与接口返回交易日 {date} 不一致，已展示接口实际交易日")
    print("\n  数据来源: 东方财富 dycalchis（严重异常波动）")
    print("  ⚠️ 异动且在监控池=最高风险，仅作治理旁证不作判决")
    return True


# ---------------------------------------------------------------------------
# 取数级别（L0–L3）— 声明式契约
#
# 分级定义的唯一权威源是 skills/ashare-data.md「取数级别声明」；此处仅是它在
# CLI 层的可执行投影。决策记录见 docs/ashare-data-tiered-upgrade-plan.md。
#
# 两条硬约束（勿回退）：
#   1. 不提供 --level core。L1 CORE 由 full-company-analysis 编排器的 feeds
#      映射按公司动态决定（实测 12–27 条），封装成固定清单会造成静默降级。
#   2. run-level 只服务 standalone 快查，不进主管线。主管线走 gate 的
#      run-ashare-command 逐条执行并冻结收据，命令级血缘一条都不能塌缩。
# ---------------------------------------------------------------------------

# 各级“已就位”的命令集（standalone 可复现的部分）。
# L2/L3 的候选层命令尚未实现——按需求拉动交付，不预建。
LEVEL_COMMANDS = {
    "quick": ("quote", "valuation", "financials"),
    "enhanced": ("quote", "valuation", "financials"),
    "full": ("quote", "valuation", "financials"),
}

LEVEL_LABELS = {
    "quick": "L0 QUICK（快查·概览三件套）",
    "enhanced": "L2 ENHANCED（增强信号）",
    "full": "L3 FULL（全量侦察）",
}

# 各级尚未就位的候选层：如实告知，不静默冒充已覆盖。
# 全部 7 个需求拉动候选（打板三件套 + 热度层 + 互动易/财联社/研报）已作为独立子命令交付，
# 由消费方 skill 调用；run-level 仅跑 L1 快查不代跑 L2/L3（ADR-003）。
LEVEL_PENDING_LAYERS = {
    "quick": (),
    "enhanced": (),
    "full": (),
}

_CORE_REJECTION = (
    "run-level 不提供 --level core。\n"
    "  L1 CORE 由 full-company-analysis 编排器的 feeds 映射动态决定"
    "（实测 12–27 条命令，随公司变化），\n"
    "  把它封装成一份固定清单会导致取数静默降级，并使命令级血缘塌缩。\n"
    "  管线取数请走 gate 的 run-ashare-command 逐条执行并冻结收据；\n"
    "  standalone 快查请用 --level quick / enhanced / full。"
)


def _search_candidates(keyword: str):
    """东财 suggest 搜索，返回原始候选列表（供 search 与跨级定码复用）。"""
    url = "https://searchadapter.eastmoney.com/api/suggest/get"
    params = {
        "input": keyword,
        "type": "14",
        "token": os.environ.get("EASTMONEY_SEARCH_TOKEN", ""),
        "count": "10",
    }
    data = _curl_json(url, params)
    return (data.get("QuotationCodeTable") or {}).get("Data") or []


def _resolve_target(value: str):
    """跨级输入归一化：六位代码直接用；公司名先定码，多候选不代选。

    返回六位代码字符串；无法唯一确定时返回 None（调用方按失败处理）。
    """
    try:
        return normalize_code(value).code
    except ValueError:
        pass

    print(f"输入 '{value}' 不是六位代码，先执行 search 定码（跨级输入归一化步骤）…")
    try:
        candidates = _search_candidates(value)
    except (ConnectionError, json.JSONDecodeError,
            subprocess.TimeoutExpired) as exc:
        print(f"❌ 定码失败: {exc}", file=sys.stderr)
        return None

    if not candidates:
        print(f"❌ 未找到匹配 '{value}' 的股票", file=sys.stderr)
        return None
    if len(candidates) > 1:
        print(f"❌ '{value}' 匹配到 {len(candidates)} 个标的，"
              f"请指定六位代码后重跑（不代为选择）：", file=sys.stderr)
        for item in candidates:
            mkt = {"1": "沪", "2": "深", "3": "北"}.get(
                str(item.get("MktNum", "")), "")
            print(f"    {item.get('Code', '')} {item.get('Name', '')} [{mkt}]",
                  file=sys.stderr)
        return None

    only = candidates[0]
    code = str(only.get("Code", "")).strip()
    try:
        code = normalize_code(code).code
    except ValueError:
        print(f"❌ 定码结果非法: {code!r}", file=sys.stderr)
        return None
    print(f"✅ 定码: {code} {only.get('Name', '')}")
    return code


def cmd_run_level(target: str, level: str = "quick"):
    """按取数级别串跑已就位命令（仅 standalone 快查）。

    逐条执行、逐条呈现，每条命令的原始输出与成败独立保留——
    不聚合成“统一报告”，以免 signals 一类的部分成功语义被糊掉。
    """
    normalized = (level or "").strip().lower()
    if normalized == "core":
        raise ValueError(_CORE_REJECTION)
    if normalized not in LEVEL_COMMANDS:
        valid = " / ".join(LEVEL_COMMANDS)
        raise ValueError(f"未知取数级别 {level!r}，可选：{valid}（不含 core）")

    runners = {
        "quote": cmd_quote,
        "valuation": cmd_valuation,
        "financials": cmd_financials,
    }
    commands = LEVEL_COMMANDS[normalized]

    print("=" * 60)
    print(f"取数级别: {LEVEL_LABELS[normalized]}")
    print("=" * 60)
    print("⚠️ 本命令仅服务 standalone 快查，不用于 full-company-analysis 主管线。")
    print("   管线取数走 gate 的 run-ashare-command 逐条执行并冻结收据。")
    if normalized != "quick":
        print("   L1 层由编排器 feeds 映射驱动，standalone 不可复现，本命令不代跑。")
    print()

    code = _resolve_target(target)
    if code is None:
        return False

    outcomes = []
    total = len(commands)
    for index, name in enumerate(commands, start=1):
        print()
        print(f"[{index}/{total}] {name} {code}")
        print("-" * 60)
        try:
            ok = runners[name](code) is not False
        except Exception as exc:  # 单条命令异常不得中断其余命令
            print(f"❌ {name} 执行异常: {exc}", file=sys.stderr)
            ok = False
        outcomes.append((name, ok))

    print()
    print("=" * 60)
    print(f"逐条执行结果（本级已就位命令 {total} 条）")
    print("=" * 60)
    for name, ok in outcomes:
        mark = "✅" if ok else "❌"
        suffix = "" if ok else "  ← 失败，详见上方该命令原始输出"
        print(f"  {mark} {name}{suffix}")

    pending = LEVEL_PENDING_LAYERS[normalized]
    if pending:
        print()
        print("本级尚未就位的候选层（需求拉动后接入，当前未取数）：")
        for layer in pending:
            print(f"  · {layer}")

    failed = [name for name, ok in outcomes if not ok]
    if failed:
        print()
        print(f"⚠️ {len(failed)}/{total} 条命令未取到数据，"
              f"相关结论按“数据不足”处理，不得推测填充。")
        return False
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

    # Phase 0: 三大报表 — 原始财务报表独立交叉验证源
    p_income = sub.add_parser("income-stmt", help="利润表原始数据（Tushare income）")
    p_income.add_argument("code", help="股票代码")
    p_income.add_argument("--years", type=_positive_years, default=5, help="年度数量，默认 5")
    p_income.add_argument("--json", action="store_true", help="JSON 输出")

    p_bs = sub.add_parser("balance-sheet", help="资产负债表（Tushare balancesheet）")
    p_bs.add_argument("code", help="股票代码")
    p_bs.add_argument("--years", type=_positive_years, default=5, help="年度数量，默认 5")
    p_bs.add_argument("--json", action="store_true", help="JSON 输出")

    p_cf = sub.add_parser("cash-flow", help="现金流量表（Tushare cashflow）")
    p_cf.add_argument("code", help="股票代码")
    p_cf.add_argument("--years", type=_positive_years, default=5, help="年度数量，默认 5")
    p_cf.add_argument("--json", action="store_true", help="JSON 输出")

    # Phase 0b: 资金面+量化因子
    p_mf = sub.add_parser("money-flow", help="个股资金流向 主力/散户（Tushare moneyflow）")
    p_mf.add_argument("code", help="股票代码")
    p_mf.add_argument("--date", default=None, help="交易日期 YYYYMMDD，默认最近交易日")

    p_factor = sub.add_parser("factors", help="量化因子 换手率/量比/PE分位（Tushare stk_factor_pro）")
    p_factor.add_argument("code", help="股票代码")
    p_factor.add_argument("--date", default=None, help="交易日期 YYYYMMDD，默认最近交易日")

    # Phase 0c: 同花顺概念板块
    p_ths = sub.add_parser("sector-peers", help="同花顺概念成分股（Tushare ths_member）")
    p_ths.add_argument("code", help="同花顺概念代码 如 885800.TI（半导体设备）")
    p_ths.add_argument("--json", action="store_true", help="JSON 输出")

    # Phase 0d: 宏观数据
    p_macro = sub.add_parser("macro", help="宏观经济指标 GDP/CPI/M2/Shibor（Tushare cn_*）")
    p_macro.add_argument("indicator", choices=["gdp", "cpi", "m2", "shibor"],
                         help="指标: gdp/cpi/m2/shibor")
    p_macro.add_argument("--period", default=None, help="期间 YYYY/YYYYMM，默认最近")

    # Phase 0e: 历史更名
    p_nc = sub.add_parser("name-history", help="历史证券名称变更（Tushare namechange）")
    p_nc.add_argument("code", help="股票代码")

    # Tier 1: 行情扩展
    p_lp = sub.add_parser("limit-price", help="涨跌停价格（Tushare stk_limit）")
    p_lp.add_argument("code", help="股票代码")
    p_lp.add_argument("--date", default=None, help="交易日期 YYYYMMDD")

    p_sus = sub.add_parser("suspend", help="停复牌信息（Tushare suspend_d）")
    p_sus.add_argument("--date", default=None, help="交易日期 YYYYMMDD，默认最近")

    p_w = sub.add_parser("weekly", help="周线行情（Tushare weekly）")
    p_w.add_argument("code", help="股票代码")
    p_w.add_argument("--date", default=None, help="周结束日 YYYYMMDD")

    p_m = sub.add_parser("monthly", help="月线行情（Tushare monthly）")
    p_m.add_argument("code", help="股票代码")
    p_m.add_argument("--date", default=None, help="月结束日 YYYYMMDD")

    # Tier 1: 特色/特色
    p_br = sub.add_parser("broker-recommend", help="券商月度金股推荐（Tushare broker_recommend）")
    p_br.add_argument("--month", default=None, help="月份 YYYYMM")

    p_cyq = sub.add_parser("cyq-chips", help="每日筹码分布（Tushare cyq_perf）")
    p_cyq.add_argument("code", help="股票代码")
    p_cyq.add_argument("--date", default=None, help="交易日期 YYYYMMDD")

    p_sf = sub.add_parser("stk-factor", help="技术因子基础版（Tushare stk_factor）")
    p_sf.add_argument("code", help="股票代码")
    p_sf.add_argument("--date", default=None, help="交易日期 YYYYMMDD")

    # Tier 1: 打板专题
    p_ll = sub.add_parser("limit-list", help="涨跌停清单（Tushare limit_list_d）")
    p_ll.add_argument("--date", default=None, help="交易日期 YYYYMMDD")

    p_tl = sub.add_parser("top-list", help="龙虎榜（Tushare top_list）")
    p_tl.add_argument("--date", default=None, help="交易日期 YYYYMMDD")

    p_hot = sub.add_parser(
        "ths-hot",
        help="市场热度榜：同花顺热榜/东财人气榜（零依赖）+ Tushare 备用",
    )
    p_hot.add_argument(
        "--period", default="hour", choices=["hour", "day"],
        help="热度周期 hour/day（默认 hour，仅零依赖路径使用）",
    )
    p_hot.add_argument("--date", default=None, help="交易日期 YYYYMMDD（仅 Tushare 回退使用）")
    p_hot.add_argument("--top", type=int, default=50, help="返回条数，默认 50")

    # Tier 1: 打板三件套（L2，东财免费源，全市场级）
    p_lp = sub.add_parser("limit-pool", help="涨停生态池：涨停/炸板/跌停/昨涨停（东财 push2ex）")
    p_lp.add_argument("--date", default=None, help="交易日期 YYYYMMDD（默认今天）")
    p_mp = sub.add_parser("monitor-pool", help="重点监控池：风险警示/重点监控名单（东财）")
    p_mp.add_argument("--date", default=None, help="过滤监控窗口的日期 YYYYMMDD（默认今天）")
    p_ap = sub.add_parser("anomaly-pool", help="日内异动池：严重异常波动明细（东财 dycalchis）")
    p_ap.add_argument("--date", default=None, help="交易日期 YYYYMMDD（默认今天；接口返回最近交易日）")

    # Tier 1: L3 一手定性 / 快讯 / 研报层（零鉴权免费源，需求拉动：消费方 skill 调用）
    p_ird = sub.add_parser("ird-interact", help="互动易问答 投资者提问+公司回复（巨潮，L3 一手定性）")
    p_ird.add_argument("code", help="股票代码")
    p_ird.add_argument("--limit", type=int, default=20, help="返回条数，默认 20")

    p_cls = sub.add_parser("cls-telegraph", help="财联社实时电报 全市场快讯（本地签名零 key，L3 快讯）")
    p_cls.add_argument("--top", type=int, default=50, help="返回条数，默认 50")

    p_rl = sub.add_parser("report-list", help="研报列表 个股/行业（东财 reportapi，L3 研报）")
    p_rl.add_argument("code", nargs="?", default=None, help="股票代码（个股研报；缺省需配合 --industry）")
    p_rl.add_argument("--industry", default=None, help="东财行业码（如 1238=IT服务Ⅱ），指定则查行业研报 qType=1")
    p_rl.add_argument("--limit", type=int, default=30, help="返回条数，默认 30")

    # Tier 1: 参考数据
    p_ub = sub.add_parser("unblock", help="限售股解禁（Tushare share_float）")
    p_ub.add_argument("code", help="股票代码")
    p_ub.add_argument("--end-date", default=None, help="截止日期 YYYYMMDD")
    p_ub.add_argument("--limit", type=int, default=10, help="返回条数")

    p_bt = sub.add_parser("block-trade", help="大宗交易（Tushare block_trade）")
    p_bt.add_argument("code", help="股票代码")
    p_bt.add_argument("--date", default=None, help="交易日期 YYYYMMDD")

    # Tier 1b: 券商研报/北向资金/融资融券/板块资金流
    p_ar = sub.add_parser("analyst-reports", help="券商研报 目标价/评级/EPS（Tushare report_rc）")
    p_ar.add_argument("code", help="股票代码")
    p_ar.add_argument("--limit", type=int, default=20, help="返回数量，默认 20")

    p_hf = sub.add_parser("hsgt-flow", help="沪深港通资金流向 北向/南向（Tushare moneyflow_hsgt）")
    p_hf.add_argument("--date", default=None, help="交易日期 YYYYMMDD")

    p_ht = sub.add_parser("hsgt-top10", help="沪深港通十大成交股（Tushare hsgt_top10）")
    p_ht.add_argument("--date", default=None, help="交易日期 YYYYMMDD")

    p_sf = sub.add_parser("sector-flow", help="板块资金流向（Tushare moneyflow_ths/moneyflow_dc）")
    p_sf.add_argument("--source", default="ths", choices=["ths", "dc"], help="数据源 ths/dc，默认 ths")
    p_sf.add_argument("--date", default=None, help="交易日期 YYYYMMDD")

    p_mg = sub.add_parser("margin", help="融资融券 余额/明细（Tushare margin/margin_detail）")
    p_mg.add_argument("code", nargs="?", default=None, help="股票代码（缺省查全市场汇总）")
    p_mg.add_argument("--date", default=None, help="交易日期 YYYYMMDD")

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

    # 取数级别串跑（仅 standalone 快查，不进 full-company-analysis 主管线）
    p_level = sub.add_parser(
        "run-level",
        help="按取数级别串跑已就位命令（standalone 快查专用，不支持 core）",
    )
    p_level.add_argument("target", help="六位代码或公司名（公司名先 search 定码）")
    p_level.add_argument(
        "--level",
        default="quick",
        help="取数级别 quick/enhanced/full，默认 quick；"
             "不支持 core（L1 由编排器 feeds 映射动态决定）",
    )

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
        # Phase 0: 三大报表+资金面+概念+宏观+更名+因子
        "income-stmt": lambda: cmd_income_stmt(args.code, args.years, args.json),
        "balance-sheet": lambda: cmd_balance_sheet(args.code, args.years, args.json),
        "cash-flow": lambda: cmd_cash_flow(args.code, args.years, args.json),
        "money-flow": lambda: cmd_money_flow(args.code, args.date),
        "factors": lambda: cmd_factors(args.code, args.date),
        "sector-peers": lambda: cmd_sector_peers(args.code, args.json),
        "macro": lambda: cmd_macro(args.indicator, args.period),
        "name-history": lambda: cmd_name_history(args.code),
        # Tier 1: 行情
        "limit-price": lambda: cmd_limit_price(args.code, args.date),
        "suspend": lambda: cmd_suspend(args.date),
        "weekly": lambda: cmd_weekly(args.code, args.date),
        "monthly": lambda: cmd_monthly(args.code, args.date),
        # Tier 1: 特色
        "broker-recommend": lambda: cmd_broker_recommend(args.month),
        "cyq-chips": lambda: cmd_cyq_chips(args.code, args.date),
        "stk-factor": lambda: cmd_stk_factor(args.code, args.date),
        # Tier 1: 打板
        "limit-list": lambda: cmd_limit_list(args.date),
        "top-list": lambda: cmd_top_list(args.date),
        "ths-hot": lambda: cmd_ths_hot(args.period, args.date, args.top),
        # Tier 1: 打板三件套（L2，东财免费源）
        "limit-pool": lambda: cmd_limit_pool(args.date),
        "monitor-pool": lambda: cmd_monitor_pool(args.date),
        "anomaly-pool": lambda: cmd_anomaly_pool(args.date),
        # Tier 1: L3 一手定性 / 快讯 / 研报层（零鉴权免费源）
        "ird-interact": lambda: cmd_ird_interact(args.code, args.limit),
        "cls-telegraph": lambda: cmd_cls_telegraph(args.top),
        "report-list": lambda: cmd_report_list(args.code, args.industry, args.limit),
        # Tier 1: 参考
        "unblock": lambda: cmd_unblock(args.code, args.end_date, args.limit),
        "block-trade": lambda: cmd_block_trade(args.code, args.date),
        # Tier 1b: 券商研报/北向资金/融资融券/板块资金流
        "analyst-reports": lambda: cmd_analyst_reports(args.code, args.limit),
        "hsgt-flow": lambda: cmd_hsgt_flow(args.date),
        "hsgt-top10": lambda: cmd_hsgt_top10(args.date),
        "sector-flow": lambda: cmd_sector_flow(args.source, args.date),
        "margin": lambda: cmd_margin(args.code, args.date),
        # 取数级别串跑（standalone 快查专用）
        "run-level": lambda: cmd_run_level(args.target, args.level),
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
