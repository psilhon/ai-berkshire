from datetime import date
from typing import Any, Dict, List, Optional

from . import DataResult, failure_result, success_result
from .errors import TransportError
from .identifiers import normalize_code
from .transport import FallbackChain, TransportClient


DATACENTER_URL = "https://datacenter.eastmoney.com/api/data/v1/get"
FFLOW_URL = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
SZSE_LHB_URL = "https://www.szse.cn/api/report/ShowReport/data"
MARGIN_REPORT = "RPTA_WEB_RZRQ_GGMX"


def _datacenter_rows(
    report_name: str,
    identity,
    client: TransportClient,
    limit: int = 50,
    *,
    filter_column: str = "SECURITY_CODE",
    sort_columns: str = "",
):
    payload = {
        "reportName": report_name,
        "columns": "ALL",
        "filter": f'({filter_column}="{identity.code}")',
        "pageNumber": "1",
        "pageSize": str(limit),
        "sortColumns": sort_columns,
        "sortTypes": "-1",
        "source": "WEB",
        "client": "WEB",
    }
    response = client.get_json(DATACENTER_URL, params=payload)
    if not response.get("success"):
        # code 9201 = 查询无记录（正常空结果）；其余（如 9501 报表/参数错误）必须显式失败
        if response.get("code") == 9201:
            return []
        raise TransportError(response.get("message") or "东方财富接口返回失败", "http_error")
    result = response.get("result") or {}
    return result.get("data") or []


def _failure_from_exception(source: str, exc: Exception) -> DataResult:
    error_type = getattr(exc, "error_type", "http_error")
    return failure_result(source, error_type, str(exc))


def _parse_flow_line(line: str) -> Dict[str, Any]:
    parts = line.split(",")
    if len(parts) < 6:
        raise ValueError("资金流字段不足")
    return {
        "date": parts[0],
        "main_net": float(parts[1]),
        "small_net": float(parts[2]),
        "mid_net": float(parts[3]),
        "large_net": float(parts[4]),
        "super_net": float(parts[5]),
        "unit": "CNY",
    }


def _eastmoney_fund_flow(identity, client: TransportClient, days: int) -> DataResult:
    response = client.get_json(
        FFLOW_URL,
        params={
            "secid": identity.secid,
            "klt": 1,
            "lmt": days,
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
        },
    )
    lines = (response.get("data") or {}).get("klines") or []
    if not lines:
        return failure_result("eastmoney", "empty_data", "东方财富资金流为空")
    try:
        rows = [_parse_flow_line(line) for line in lines]
    except (TypeError, ValueError) as exc:
        return failure_result("eastmoney", "parse_error", str(exc))
    return success_result(rows, "eastmoney")


def _sina_fund_flow(identity, client: TransportClient, days: int) -> DataResult:
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_qsfx_zjlrqs"
    response = client.get_json(
        url,
        params={"page": 1, "num": days, "sort": "opendate", "asc": 0, "daima": identity.quote_code},
    )
    if not response:
        return failure_result("sina", "empty_data", "新浪资金流为空")
    rows = [
        {
            "date": row.get("opendate", ""),
            "close": row.get("trade"),
            "net_amount": row.get("netamount"),
            "turnover": row.get("turnover"),
            "unit": "CNY",
        }
        for row in response[:days]
    ]
    return success_result(rows, "sina", fallback_used=True)


def fetch_fund_flow(
    code: str,
    *,
    days: int = 60,
    client: Optional[TransportClient] = None,
) -> DataResult:
    try:
        identity = normalize_code(code)
    except ValueError as exc:
        return failure_result("identifier", "invalid_code", str(exc))
    if not 1 <= days <= 120:
        return failure_result("identifier", "invalid_code", "资金流天数必须在 1 到 120 之间")
    client = client or TransportClient()
    return FallbackChain([
        lambda: _eastmoney_fund_flow(identity, client, days),
        lambda: _sina_fund_flow(identity, client, days),
    ]).run()


def _eastmoney_dragon_tiger(identity, trade_date: str, client: TransportClient) -> DataResult:
    rows = _datacenter_rows("RPT_DAILYBILLBOARD_DETAILS", identity, client)
    if not rows:
        return failure_result("eastmoney", "empty_data", "东方财富龙虎榜为空")
    normalized = [
        {
            "code": row.get("SECURITY_CODE") or identity.code,
            "name": row.get("SECURITY_NAME_ABBR") or "",
            "net_buy": row.get("NET_BUY_AMT") or row.get("NET_BUY") or 0,
            "date": str(row.get("TRADE_DATE") or trade_date)[:10],
            "reason": row.get("EXPLANATION") or row.get("REASON") or "",
        }
        for row in rows
    ]
    return success_result(normalized, "eastmoney")


def _szse_dragon_tiger(identity, trade_date: str, client: TransportClient) -> DataResult:
    response = client.get_json(
        SZSE_LHB_URL,
        params={
            "SHOWTYPE": "JSON",
            "CATALOGID": "1842_xxpl",
            "TABKEY": "tab1",
            "txtStart": trade_date,
            "txtEnd": trade_date,
        },
    )
    rows = (response.get("data") or []) if isinstance(response, dict) else []
    rows = [
        {
            "code": row.get("zqdm") or identity.code,
            "name": row.get("zqjc") or "",
            "net_buy": row.get("cjje") or 0,
            "date": trade_date,
            "reason": row.get("plyy") or "",
        }
        for row in rows
    ]
    if not rows:
        return failure_result("szse", "empty_data", "深交所龙虎榜为空")
    return success_result(rows, "szse", fallback_used=True)


def fetch_dragon_tiger(
    code: str,
    *,
    trade_date: Optional[str] = None,
    client: Optional[TransportClient] = None,
) -> DataResult:
    try:
        identity = normalize_code(code)
    except ValueError as exc:
        return failure_result("identifier", "invalid_code", str(exc))
    trade_date = trade_date or date.today().isoformat()
    client = client or TransportClient()
    backup = lambda: _szse_dragon_tiger(identity, trade_date, client) if identity.market == "SZ" else failure_result("sse", "empty_data", "上交所备用源需要后续解析")
    return FallbackChain([
        lambda: _eastmoney_dragon_tiger(identity, trade_date, client),
        backup,
    ]).run()


def fetch_lockup(
    code: str,
    *,
    trade_date: Optional[str] = None,
    forward_days: int = 90,
    client: Optional[TransportClient] = None,
) -> DataResult:
    try:
        identity = normalize_code(code)
        rows = _datacenter_rows("RPT_LIFT_STAGE", identity, client or TransportClient())
    except ValueError as exc:
        return failure_result("identifier", "invalid_code", str(exc))
    except Exception as exc:
        return _failure_from_exception("eastmoney", exc)
    if not rows:
        return failure_result("eastmoney", "empty_data", "未找到解禁记录")
    return success_result(rows, "eastmoney")


def fetch_margin(code: str, *, client: Optional[TransportClient] = None) -> DataResult:
    try:
        identity = normalize_code(code)
        rows = _datacenter_rows(
            MARGIN_REPORT,
            identity,
            client or TransportClient(),
            filter_column="SCODE",
            sort_columns="DATE",
        )
    except ValueError as exc:
        return failure_result("identifier", "invalid_code", str(exc))
    except Exception as exc:
        return _failure_from_exception("eastmoney", exc)
    if not rows:
        return failure_result("eastmoney", "empty_data", "未找到融资融券记录")
    return success_result(rows, "eastmoney")


def fetch_signals(
    code: str,
    *,
    trade_date: Optional[str] = None,
    client: Optional[TransportClient] = None,
) -> DataResult:
    client = client or TransportClient()
    parts = {
        "fund_flow": fetch_fund_flow(code, client=client),
        "dragon_tiger": fetch_dragon_tiger(code, trade_date=trade_date, client=client),
        "lockup": fetch_lockup(code, trade_date=trade_date, client=client),
        "margin": fetch_margin(code, client=client),
    }
    warnings = []
    for name, result in parts.items():
        if not result.get("ok"):
            warnings.append(f"{name}: {result.get('message', '数据不足')}")
    ok = any(result.get("ok") for result in parts.values())
    if not ok:
        return failure_result("multiple", "all_sources_failed", "市场信号均不可用", warnings=warnings)
    return success_result(parts, "multiple", warnings=warnings)


__all__ = [
    "fetch_dragon_tiger",
    "fetch_fund_flow",
    "fetch_lockup",
    "fetch_margin",
    "fetch_signals",
]
