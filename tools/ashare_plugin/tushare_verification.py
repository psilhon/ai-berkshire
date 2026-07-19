"""Normalize and compare optional Tushare verification results."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional

from .identifiers import normalize_code
from .tushare import TushareClient


FIELD_STATUSES = {"MATCH", "CONFLICT", "INSUFFICIENT"}

MARKET_PRECEDENCE_FIELDS = {
    "quote": {"close", "market_cap", "float_cap", "pe", "pb", "turnover_rate"},
    "valuation": {"close", "market_cap", "float_cap", "pe", "pb", "turnover_rate"},
    "equity-history": {"total_shares"},
}

COMMAND_APIS = {
    "quote": ("daily_basic",),
    "valuation": ("daily_basic",),
    "financials": ("income", "balancesheet", "cashflow", "fina_indicator"),
    "history": ("income", "balancesheet", "cashflow", "fina_indicator"),
    "equity-history": ("daily_basic", "share_float"),
    "search": ("stock_basic",),
    "signals": ("moneyflow", "top_list", "margin_detail", "share_float"),
    "announcements": ("anns_d",),
    # Phase 1: 10,000-point Tushare commands
    "pe-band": ("daily_basic",),
    "research-visits": ("stk_surv",),
    "insider-trades": ("stk_holdertrade",),
    # Phase 2: enhancement commands
    "consensus": ("forecast",),
    "shareholders": ("top10_holders",),
    "dividend": ("dividend",),
    "management": ("stk_rewards",),
    # P1: Industry benchmark
    "industry-pe": ("sw_daily", "stock_basic", "index_classify"),
    # P2: News + disclosure
    "news": ("major_news",),
    "disclosure-calendar": ("disclosure_date",),
    # P3: HK stock
    "hk-quote": ("hk_daily", "hk_basic"),
    "ah-cross-check": ("hk_daily", "daily_basic"),
}

API_FIELDS = {
    "daily_basic": (
        "ts_code", "trade_date", "close", "turnover_rate", "pe", "pb",
        "total_share", "float_share", "total_mv", "circ_mv",
    ),
    "income": (
        "ts_code", "ann_date", "f_ann_date", "end_date", "report_type",
        "update_flag", "total_revenue", "n_income_attr_p", "basic_eps",
    ),
    "balancesheet": (
        "ts_code", "ann_date", "f_ann_date", "end_date", "report_type",
        "update_flag", "total_assets", "total_liab",
    ),
    "cashflow": (
        "ts_code", "ann_date", "f_ann_date", "end_date", "report_type",
        "update_flag", "n_cashflow_act",
    ),
    "fina_indicator": (
        "ts_code", "ann_date", "end_date", "update_flag", "roe",
        "grossprofit_margin", "netprofit_margin", "debt_to_assets",
    ),
    "share_float": ("ts_code", "ann_date", "float_date", "float_share", "float_ratio"),
    "stock_basic": ("ts_code", "symbol", "name", "market", "list_status", "list_date"),
    "moneyflow": ("ts_code", "trade_date", "net_mf_amount"),
    "top_list": ("trade_date", "ts_code", "name", "net_amount", "reason"),
    "margin_detail": ("trade_date", "ts_code", "rzye", "rqyl", "rzmre", "rqmcl"),
    "anns_d": ("ann_date", "ts_code", "name", "title", "url"),
    # Phase 1: 10,000-point Tushare APIs
    "stk_surv": (
        "ts_code", "name", "surv_date", "fund_visitors",
        "rece_place", "rece_mode", "rece_org", "org_type",
        "comp_rece", "content",
    ),
    "stk_holdertrade": (
        "ts_code", "ann_date", "holder_name", "holder_type",
        "in_de", "change_vol", "change_ratio", "after_hold",
        "avg_price", "hold_float_ratio",
    ),
    # Phase 2: enhancement APIs
    "forecast": (
        "ts_code", "ann_date", "end_date", "type",
        "p_change_min", "p_change_max", "net_profit_min",
        "net_profit_max", "last_parent_net", "first_ann_date",
        "summary", "change_reason", "update_flag",
    ),
    "top10_holders": (
        "ts_code", "ann_date", "end_date", "holder_name",
        "hold_num", "hold_ratio", "hold_float_ratio",
        "change_ratio", "holder_type",
    ),
    "dividend": (
        "ts_code", "end_date", "ann_date", "div_proc",
        "stk_div", "cash_div", "cash_div_tax",
        "record_date", "ex_div_date", "base_share",
    ),
    "stk_rewards": (
        "ts_code", "ann_date", "end_date", "name",
        "title", "reward", "hold_vol",
    ),
    "sw_daily": (
        "ts_code", "trade_date", "open", "high", "low",
        "close", "change", "pct_change", "vol", "amount",
        "pe", "pb", "float_mv", "total_mv",
    ),
    "index_classify": (
        "index_code", "industry_name", "level", "parent_code",
    ),
    # P2/P3: Independent paid permission APIs
    "major_news": (
        "title", "pub_time", "src", "url",
    ),
    "disclosure_date": (
        "ts_code", "ann_date", "end_date", "pre_date", "actual_date",
    ),
    "hk_daily": (
        "ts_code", "trade_date", "open", "close", "high", "low",
        "pre_close", "change", "pct_change", "vol", "amount",
    ),
    "hk_basic": (
        "ts_code", "name", "list_date", "list_status", "industry",
    ),
}


def _decimal(value: Any) -> Optional[Decimal]:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def not_configured_verification() -> Dict[str, Any]:
    return {
        "provider": "tushare",
        "configured": False,
        "status": "NOT_CONFIGURED",
        "as_of": None,
        "warnings": ["未配置 TUSHARE_TOKEN；未发起 Tushare 请求"],
        "fields": [],
        "endpoints": [],
    }


def _field(
    name: str,
    status: str,
    primary_value: Any,
    verification_value: Any,
    primary_source: str,
    verification_source: str,
    period: str,
    unit: str,
    *,
    deviation_pct: Optional[str] = None,
    error_type: Optional[str] = None,
) -> Dict[str, Any]:
    result = {
        "field": name,
        "status": status,
        "primary_value": None if primary_value is None else str(primary_value),
        "verification_value": (
            None if verification_value is None else str(verification_value)
        ),
        "primary_source": primary_source,
        "verification_source": verification_source,
        "period": period,
        "unit": unit,
        "deviation_pct": deviation_pct,
    }
    if error_type is not None:
        result["error_type"] = error_type
    return result


def compare_decimal(
    name: str,
    primary_value: Any,
    verification_value: Any,
    *,
    primary_source: str,
    verification_source: str,
    primary_period: str,
    verification_period: str,
    primary_unit: str,
    verification_unit: str,
    tolerance_pct: str = "1",
) -> Dict[str, Any]:
    if primary_period != verification_period:
        return _field(
            name,
            "INSUFFICIENT",
            primary_value,
            verification_value,
            primary_source,
            verification_source,
            primary_period,
            primary_unit,
            error_type="period_mismatch",
        )
    if primary_unit != verification_unit:
        return _field(
            name,
            "INSUFFICIENT",
            primary_value,
            verification_value,
            primary_source,
            verification_source,
            primary_period,
            primary_unit,
            error_type="unit_mismatch",
        )

    left = _decimal(primary_value)
    right = _decimal(verification_value)
    tolerance = _decimal(tolerance_pct)
    if left is None or right is None or tolerance is None:
        return _field(
            name,
            "INSUFFICIENT",
            primary_value,
            verification_value,
            primary_source,
            verification_source,
            primary_period,
            primary_unit,
            error_type="missing_value",
        )

    if left == 0:
        if right != 0:
            return _field(
                name,
                "INSUFFICIENT",
                primary_value,
                verification_value,
                primary_source,
                verification_source,
                primary_period,
                primary_unit,
                error_type="zero_denominator",
            )
        deviation = Decimal("0")
    else:
        deviation = abs(right - left) / abs(left) * Decimal("100")

    status = "MATCH" if deviation <= tolerance else "CONFLICT"
    return _field(
        name,
        status,
        primary_value,
        verification_value,
        primary_source,
        verification_source,
        primary_period,
        primary_unit,
        deviation_pct=format(deviation.quantize(Decimal("0.01")), "f"),
    )


def compare_text(
    name: str,
    primary_value: Any,
    verification_value: Any,
    *,
    primary_source: str,
    verification_source: str,
    period: str,
) -> Dict[str, Any]:
    if primary_value in (None, "") or verification_value in (None, ""):
        return _field(
            name,
            "INSUFFICIENT",
            primary_value,
            verification_value,
            primary_source,
            verification_source,
            period,
            "text",
            error_type="missing_value",
        )

    status = (
        "MATCH"
        if str(primary_value).strip() == str(verification_value).strip()
        else "CONFLICT"
    )
    return _field(
        name,
        status,
        primary_value,
        verification_value,
        primary_source,
        verification_source,
        period,
        "text",
    )


def finalize_verification(
    fields: Iterable[Dict[str, Any]],
    endpoints: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    field_rows = list(fields)
    endpoint_rows = list(endpoints)
    statuses = {row.get("status") for row in field_rows}
    if not statuses.issubset(FIELD_STATUSES):
        raise ValueError("验证字段包含未知状态")
    if "CONFLICT" in statuses:
        status = "CONFLICT"
    elif "MATCH" in statuses:
        status = "MATCH"
    else:
        status = "INSUFFICIENT"

    warnings = [
        f"{row['api_name']}: {row['error_type']}"
        for row in endpoint_rows
        if not row.get("ok")
    ]
    return {
        "provider": "tushare",
        "configured": True,
        "status": status,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "warnings": warnings,
        "fields": field_rows,
        "endpoints": endpoint_rows,
    }


def apply_market_precedence(
    command: str, verification: Dict[str, Any]
) -> Dict[str, Any]:
    """Select Tushare only for comparable conflicting market fields."""
    eligible_fields = MARKET_PRECEDENCE_FIELDS.get(command, set())
    resolved = dict(verification)
    fields = []
    for field in verification.get("fields", []):
        effective = dict(field)
        apply_precedence = (
            effective.get("field") in eligible_fields
            and effective.get("status") == "CONFLICT"
            and str(effective.get("verification_source") or "").startswith("tushare.")
            and effective.get("verification_value") is not None
        )
        effective["effective_value"] = (
            effective.get("verification_value")
            if apply_precedence else effective.get("primary_value")
        )
        effective["effective_source"] = (
            effective.get("verification_source")
            if apply_precedence else effective.get("primary_source")
        )
        effective["precedence_applied"] = apply_precedence
        fields.append(effective)
    resolved["fields"] = fields
    return resolved


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _endpoint(api_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "api_name": api_name,
        "ok": bool(result.get("ok")),
        "source": result.get("source", f"tushare.{api_name}"),
        "error_type": (
            None if result.get("ok") else result.get("error_type", "upstream_error")
        ),
        "row_count": len(result.get("data") or []) if result.get("ok") else 0,
    }


def _latest_period_row(
    rows: List[Dict[str, Any]], period: str
) -> Optional[Dict[str, Any]]:
    matches = [
        row for row in rows
        if _digits(row.get("end_date")) == period
        and str(row.get("report_type") or "1") == "1"
    ]
    if not matches:
        return None
    return max(
        matches,
        key=lambda row: (
            str(row.get("update_flag") or "") == "1",
            _digits(row.get("f_ann_date") or row.get("ann_date")),
        ),
    )


def _params(
    api_name: str,
    command: str,
    ts_code: str,
    subject: str,
    primary_data: Any,
    trade_date: Optional[str],
) -> Dict[str, Any]:
    if api_name == "stock_basic":
        return {"name": subject}

    result = {"ts_code": ts_code}
    if api_name == "daily_basic":
        requested_date = ""
        if command in {"quote", "valuation"} and isinstance(primary_data, dict):
            requested_date = _digits(primary_data.get("quote_time"))
        elif command == "equity-history" and isinstance(primary_data, list) and primary_data:
            latest = max(primary_data, key=lambda row: _digits(row.get("END_DATE")))
            requested_date = _digits(latest.get("END_DATE"))
        if requested_date:
            result["trade_date"] = requested_date
    if api_name in {"moneyflow", "top_list", "margin_detail"} and trade_date:
        result["trade_date"] = _digits(trade_date)
    if api_name == "anns_d" and trade_date:
        result["ann_date"] = _digits(trade_date)
    return result


def _market_fields(
    command: str, primary: Dict[str, Any], rows: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    primary_period = _digits(primary.get("quote_time"))
    selected = max(rows, key=lambda row: _digits(row.get("trade_date"))) if rows else {}
    verification_period = _digits(selected.get("trade_date"))
    mappings = [
        ("close", "price", "close", "CNY", Decimal("1"), Decimal("1")),
        ("market_cap", "market_cap", "total_mv", "CNY_10K", Decimal("10000"), Decimal("1")),
        ("float_cap", "float_cap", "circ_mv", "CNY_10K", Decimal("10000"), Decimal("1")),
    ]
    if command in {"quote", "valuation"}:
        mappings.extend([
            ("pe", "pe", "pe", "multiple", Decimal("1"), Decimal("1")),
            ("pb", "pb", "pb", "multiple", Decimal("1"), Decimal("1")),
            ("turnover_rate", "turnover_rate", "turnover_rate", "percent", Decimal("1"), Decimal("1")),
        ])

    fields = []
    for name, primary_key, verify_key, unit, left_scale, right_scale in mappings:
        left = _decimal(primary.get(primary_key))
        right = _decimal(selected.get(verify_key))
        fields.append(compare_decimal(
            name,
            None if left is None else left * left_scale,
            None if right is None else right * right_scale,
            primary_source="tencent",
            verification_source="tushare.daily_basic",
            primary_period=primary_period,
            verification_period=verification_period,
            primary_unit=unit,
            verification_unit=unit,
        ))
    return fields


def _financial_fields(
    primary_rows: List[Dict[str, Any]], results: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    mappings = (
        ("revenue", "TOTALOPERATEREVE", "income", "total_revenue", "CNY"),
        ("net_profit", "PARENTNETPROFIT", "income", "n_income_attr_p", "CNY"),
        ("basic_eps", "EPSJB", "income", "basic_eps", "CNY_PER_SHARE"),
        ("roe", "ROEJQ", "fina_indicator", "roe", "percent"),
        ("gross_margin", "XSMLL", "fina_indicator", "grossprofit_margin", "percent"),
        ("net_margin", "XSJLL", "fina_indicator", "netprofit_margin", "percent"),
        ("operating_cash_flow", "NETCASH_OPERATE_PK", "cashflow", "n_cashflow_act", "CNY"),
    )
    fields = []
    for primary in primary_rows:
        period = _digits(primary.get("REPORT_DATE"))
        for name, primary_key, api_name, verify_key, unit in mappings:
            if primary.get(primary_key) is None:
                continue
            result = results[api_name]
            rows = result.get("data") or []
            row = _latest_period_row(rows, period) if result.get("ok") else None
            verification_period = period
            if row is None and rows:
                verification_period = _digits(rows[0].get("end_date"))
            fields.append(compare_decimal(
                name,
                primary.get(primary_key),
                None if row is None else row.get(verify_key),
                primary_source="eastmoney",
                verification_source=f"tushare.{api_name}",
                primary_period=period,
                verification_period=verification_period,
                primary_unit=unit,
                verification_unit=unit,
            ))
    return fields


def _search_fields(
    primary_rows: List[Dict[str, Any]], rows: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    by_code = {str(row.get("symbol") or ""): row for row in rows}
    fields = []
    for primary in primary_rows:
        code = str(primary.get("Code") or "")
        row = by_code.get(code)
        fields.append(compare_text(
            f"security_name:{code}",
            primary.get("Name"),
            None if row is None else row.get("name"),
            primary_source="eastmoney",
            verification_source="tushare.stock_basic",
            period="current",
        ))
    return fields


def _equity_fields(
    primary_rows: List[Dict[str, Any]], results: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    if not primary_rows:
        return []
    primary = max(primary_rows, key=lambda row: _digits(row.get("END_DATE")))
    period = _digits(primary.get("END_DATE"))
    daily_rows = results["daily_basic"].get("data") or []
    row = next(
        (item for item in daily_rows if _digits(item.get("trade_date")) == period),
        None,
    )
    left = _decimal(primary.get("TOTAL_SHARES"))
    return [compare_decimal(
        "total_shares",
        None if left is None else left / Decimal("10000"),
        None if row is None else row.get("total_share"),
        primary_source="eastmoney",
        verification_source="tushare.daily_basic",
        primary_period=period,
        verification_period=period if row is not None else "",
        primary_unit="SHARES_10K",
        verification_unit="SHARES_10K",
    )]


def _signal_fields(
    primary: Dict[str, Any], results: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    block = primary.get("dragon_tiger") or {}
    primary_rows = (block.get("data") or []) if block.get("ok") else []
    verify_rows = (
        (results["top_list"].get("data") or [])
        if results["top_list"].get("ok") else []
    )
    fields = []
    for row in primary_rows:
        period = _digits(row.get("date"))
        matched = next(
            (item for item in verify_rows if _digits(item.get("trade_date")) == period),
            None,
        )
        fields.append(compare_decimal(
            "dragon_tiger_net_buy",
            row.get("net_buy"),
            None if matched is None else matched.get("net_amount"),
            primary_source=block.get("source", "eastmoney"),
            verification_source="tushare.top_list",
            primary_period=period,
            verification_period=period if matched is not None else "",
            primary_unit="CNY",
            verification_unit="CNY",
        ))
    return fields


def _pe_band_fields(
    primary: Dict[str, Any], results: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Cross-check daily_basic PE/PB snapshot against Tencent quote."""
    daily_rows = results["daily_basic"].get("data") or []
    if not daily_rows:
        return []
    # Use the most recent row from daily_basic
    latest = max(daily_rows, key=lambda row: _digits(row.get("trade_date")))
    fields = []
    for name, primary_key, verify_key, unit in (
        ("pe", "current_pe_qq", "pe", "multiple"),
        ("pb", "current_pb_qq", "pb", "multiple"),
    ):
        fields.append(compare_decimal(
            name,
            primary.get(primary_key),
            latest.get(verify_key),
            primary_source="tencent",
            verification_source="tushare.daily_basic",
            primary_period=_digits(primary.get("quote_date", "")),
            verification_period=_digits(latest.get("trade_date")),
            primary_unit=unit,
            verification_unit=unit,
        ))
    return fields


def _tushare_primary_fields(
    results: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Verification for commands where Tushare IS the primary source.

    No field-level cross-comparison — Tushare is the sole authoritative
    source for these data types (institutional surveys, insider trades,
    consensus estimates, etc.). Endpoint health is reported separately
    via the endpoints array.
    """
    fields = []
    for api_name, result in results.items():
        row_count = len(result.get("data") or []) if result.get("ok") else 0
        fields.append(_field(
            f"{api_name}:row_count",
            "MATCH" if result.get("ok") else "INSUFFICIENT",
            row_count,
            row_count,
            primary_source=f"tushare.{api_name}",
            verification_source=f"tushare.{api_name}",
            period="current",
            unit="rows",
            error_type=None if result.get("ok") else result.get("error_type"),
        ))
    return fields


def _announcement_fields(
    primary: Dict[str, Any], rows: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    tushare_keys = {
        (_digits(row.get("ann_date")), str(row.get("title") or "").strip())
        for row in rows
    }
    fields = []
    for index, row in enumerate(primary.get("data") or []):
        period = _digits(row.get("date"))
        title = str(row.get("title") or "").strip()
        matched = title if (period, title) in tushare_keys else None
        fields.append(compare_text(
            f"announcement:{index + 1}",
            title,
            matched,
            primary_source=primary.get("source", "unknown"),
            verification_source="tushare.anns_d",
            period=period,
        ))
    return fields


def verify_command(
    command: str,
    subject: str,
    primary_data: Any,
    *,
    trade_date: Optional[str] = None,
    client: Optional[Any] = None,
) -> Dict[str, Any]:
    if command not in COMMAND_APIS:
        raise ValueError(f"不支持的验证命令: {command}")
    api_client = client or TushareClient()
    if not api_client.configured:
        return not_configured_verification()

    ts_code = "" if command == "search" else normalize_code(subject).secu_code
    results = {}
    endpoints = []
    for api_name in COMMAND_APIS[command]:
        result = api_client.query(
            api_name,
            params=_params(
                api_name, command, ts_code, subject, primary_data, trade_date
            ),
            fields=API_FIELDS[api_name],
        )
        results[api_name] = result
        endpoints.append(_endpoint(api_name, result))

    if command in {"quote", "valuation"}:
        fields = _market_fields(
            command, primary_data, results["daily_basic"].get("data") or []
        )
    elif command in {"financials", "history"}:
        fields = _financial_fields(primary_data, results)
    elif command == "equity-history":
        fields = _equity_fields(primary_data, results)
    elif command == "search":
        fields = _search_fields(
            primary_data, results["stock_basic"].get("data") or []
        )
    elif command == "signals":
        fields = _signal_fields(primary_data, results)
    elif command in {"announcements", "anns_d"}:
        fields = _announcement_fields(
            primary_data, results["anns_d"].get("data") or []
        )
    elif command == "pe-band":
        fields = _pe_band_fields(primary_data, results)
    elif command in {
        "research-visits", "insider-trades", "consensus",
        "shareholders", "dividend", "management",
        "industry-pe",
        "news", "disclosure-calendar", "hk-quote", "ah-cross-check",
    }:
        fields = _tushare_primary_fields(results)
    else:
        fields = _announcement_fields(
            primary_data, results["anns_d"].get("data") or []
        )
    return finalize_verification(fields, endpoints)


def safe_verify_command(
    command: str,
    subject: str,
    primary_data: Any,
    *,
    trade_date: Optional[str] = None,
    client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Verify via Tushare without ever breaking on failure.

    Returns a degraded INSUFFICIENT verification dict when any exception
    occurs, so that verification failure never corrupts the primary result.
    """
    try:
        return verify_command(
            command, subject, primary_data, trade_date=trade_date, client=client
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


__all__ = [
    "API_FIELDS",
    "COMMAND_APIS",
    "FIELD_STATUSES",
    "MARKET_PRECEDENCE_FIELDS",
    "apply_market_precedence",
    "compare_decimal",
    "compare_text",
    "finalize_verification",
    "not_configured_verification",
    "safe_verify_command",
    "verify_command",
]
