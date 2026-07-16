from typing import Any, Dict, List, Optional

from . import DataResult, failure_result, success_result
from .errors import TransportError
from .identifiers import normalize_code
from .transport import TransportClient


DATACENTER_URL = "https://datacenter.eastmoney.com/securities/api/data/get"


def _fetch_rows(
    report_type: str,
    secu_code: str,
    *,
    sort_column: str,
    sort_order: str = "-1",
    extra_filter: str = "",
    limit: Optional[int] = None,
    client: TransportClient,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    page = 1
    page_size = min(limit or 100, 100)
    while True:
        params = {
            "type": report_type,
            "sty": "ALL",
            "filter": f'(SECUCODE="{secu_code}"){extra_filter}',
            "p": str(page),
            "ps": str(page_size),
            "sr": sort_order,
            "st": sort_column,
            "source": "HSF10",
            "client": "PC",
        }
        data = client.get_json(DATACENTER_URL, params=params)
        if not data.get("success"):
            raise TransportError(data.get("message") or "东方财富接口返回失败", "http_error")
        result = data.get("result") or {}
        rows.extend(result.get("data") or [])
        pages = int(result.get("pages") or 1)
        if page >= pages or (limit is not None and len(rows) >= limit):
            return rows[:limit] if limit is not None else rows
        page += 1


def fetch_history(
    code: str,
    *,
    years: int = 10,
    client: Optional[TransportClient] = None,
) -> DataResult:
    try:
        identity = normalize_code(code)
    except ValueError as exc:
        return failure_result("identifier", "invalid_code", str(exc))
    if not 1 <= years <= 50:
        return failure_result("identifier", "invalid_code", "年度数量必须在 1 到 50 之间")
    try:
        rows = _fetch_rows(
            "RPT_F10_FINANCE_MAINFINADATA",
            identity.secu_code,
            sort_column="REPORT_DATE",
            extra_filter='(REPORT_TYPE="年报")',
            limit=years,
            client=client or TransportClient(),
        )
    except TransportError as exc:
        return failure_result("eastmoney", exc.error_type, str(exc))
    if not rows:
        return failure_result("eastmoney", "empty_data", f"未获取到 {identity.secu_code} 的年度财务数据")
    return success_result(rows, "eastmoney")


def fetch_financials(
    code: str,
    *,
    years: int = 5,
    client: Optional[TransportClient] = None,
) -> DataResult:
    try:
        identity = normalize_code(code)
    except ValueError as exc:
        return failure_result("identifier", "invalid_code", str(exc))
    try:
        rows = _fetch_rows(
            "RPT_F10_FINANCE_MAINFINADATA",
            identity.secu_code,
            sort_column="REPORT_DATE",
            extra_filter='(REPORT_TYPE="年报")',
            limit=years,
            client=client or TransportClient(),
        )
    except TransportError as exc:
        return failure_result("eastmoney", exc.error_type, str(exc))
    if not rows:
        return failure_result("eastmoney", "empty_data", f"未获取到 {identity.secu_code} 的财务数据")
    return success_result(rows, "eastmoney")


def fetch_equity_history(
    code: str,
    *,
    client: Optional[TransportClient] = None,
) -> DataResult:
    try:
        identity = normalize_code(code)
    except ValueError as exc:
        return failure_result("identifier", "invalid_code", str(exc))
    try:
        rows = _fetch_rows(
            "RPT_F10_EH_EQUITY",
            identity.secu_code,
            sort_column="END_DATE",
            client=client or TransportClient(),
        )
    except TransportError as exc:
        return failure_result("eastmoney", exc.error_type, str(exc))
    if not rows:
        return failure_result("eastmoney", "empty_data", f"未获取到 {identity.secu_code} 的历史股本")
    return success_result(rows, "eastmoney")


__all__ = ["fetch_equity_history", "fetch_financials", "fetch_history"]
