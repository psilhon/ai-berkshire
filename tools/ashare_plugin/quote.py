from decimal import Decimal, InvalidOperation
from typing import Dict, Optional

from . import DataResult, failure_result, success_result, with_verification
from .errors import TransportError
from .identifiers import normalize_code
from .transport import TransportClient
from .tushare_verification import safe_verify_command


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
SINA_REFERER = "https://finance.sina.com.cn"


def parse_tencent_quote(raw: str) -> Dict[str, str]:
    """Parse Tencent's GBK, tilde-separated quote payload."""
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
        "volume": fields[6],
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
        "float_cap": fields[44] if len(fields) > 44 else "-",
        "market_cap": fields[45] if len(fields) > 45 else "-",
        "pb": fields[46] if len(fields) > 46 else "-",
        "limit_up": fields[47] if len(fields) > 47 else "-",
        "limit_down": fields[48] if len(fields) > 48 else "-",
    }


def parse_sina_quote(raw: str) -> Dict[str, str]:
    """Parse Sina's GBK, comma-separated quote payload (independent source)."""
    start = raw.find('"')
    end = raw.rfind('"')
    if start < 0 or end <= start:
        return {}
    fields = raw[start + 1:end].split(",")
    if len(fields) < 32 or not fields[3] or fields[3] in ("0", "0.000", "0.00"):
        return {}
    return {
        "name": fields[0],
        "open": fields[1],
        "prev_close": fields[2],
        "price": fields[3],
        "high": fields[4],
        "low": fields[5],
        "volume": fields[8],
        "amount": fields[9],
        "date": fields[30],
        "time": fields[31],
    }


def _to_decimal(value) -> Optional[Decimal]:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def price_cross_check(
    primary_price, second_price, *, tolerance_pct: str = "1"
) -> Dict[str, Optional[str]]:
    """Compare Tencent price against an independent Sina price.

    A genuine second market chain (different publisher, different transport):
    MATCH when within tolerance, CONFLICT when beyond, UNAVAILABLE when the
    second source could not be fetched. Never raises.
    """
    base = {
        "primary_source": "tencent",
        "primary_price": None if primary_price is None else str(primary_price),
        "second_source": "sina",
        "second_price": None if second_price in (None, "") else str(second_price),
    }
    left = _to_decimal(primary_price)
    right = _to_decimal(second_price)
    tolerance = _to_decimal(tolerance_pct)
    if right is None or left is None or tolerance is None or left == 0:
        base["status"] = "UNAVAILABLE"
        base["deviation_pct"] = None
        return base
    deviation = abs(right - left) / abs(left) * Decimal("100")
    base["status"] = "MATCH" if deviation <= tolerance else "CONFLICT"
    base["deviation_pct"] = format(deviation.quantize(Decimal("0.01")), "f")
    return base


def _fetch_sina(quote_code: str, client: TransportClient) -> Optional[str]:
    """Fetch the independent Sina price; return None on any failure."""
    try:
        raw = client.get_text(
            f"https://hq.sinajs.cn/list={quote_code}",
            headers={"User-Agent": UA, "Referer": SINA_REFERER},
        )
    except Exception:
        return None
    parsed = parse_sina_quote(raw)
    return parsed.get("price") if parsed else None


def fetch_quote(code: str, *, client: Optional[TransportClient] = None) -> DataResult:
    try:
        identity = normalize_code(code)
    except ValueError as exc:
        return failure_result("identifier", "invalid_code", str(exc))

    client = client or TransportClient()
    try:
        quote = parse_tencent_quote(
            client.get_text(
                f"https://qt.gtimg.cn/q={identity.quote_code}",
                headers={"User-Agent": UA},
            )
        )
        if not quote:
            return failure_result("tencent", "parse_error", "腾讯行情响应格式无效")
        high_52w, low_52w = _fetch_52w(identity.secid, client)
    except TransportError as exc:
        return failure_result("tencent", exc.error_type, str(exc))

    warnings = []
    if high_52w is None or low_52w is None:
        warnings.append("东方财富 52 周区间不可用")
    quote["high_52w"] = high_52w
    quote["low_52w"] = low_52w

    sina_price = _fetch_sina(identity.quote_code, client)
    cross_check = price_cross_check(quote.get("price"), sina_price)
    quote["price_cross_check"] = cross_check
    if cross_check["status"] == "UNAVAILABLE":
        warnings.append("新浪独立行情不可用（价格暂为单源）")
    elif cross_check["status"] == "CONFLICT":
        warnings.append(
            f"价格双源冲突（腾讯 {cross_check['primary_price']} vs "
            f"新浪 {cross_check['second_price']}，偏差 {cross_check['deviation_pct']}%）"
        )

    result = success_result(
        quote,
        "tencent+sina+eastmoney",
        warnings=warnings,
    )
    return with_verification(
        result,
        safe_verify_command("quote", code, quote),
    )


def _fetch_52w(secid: str, client: TransportClient):
    params = {"secid": secid, "fields": "f174,f175", "invt": "2", "fltt": "2"}
    for host in ("push2delay.eastmoney.com", "push2.eastmoney.com"):
        try:
            data = client.get_json(
                f"https://{host}/api/qt/stock/get",
                params=params,
                headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
            ).get("data") or {}
            high, low = data.get("f174"), data.get("f175")
            if high not in (None, "-") and low not in (None, "-"):
                return high, low
        except (TransportError, AttributeError):
            continue
    return None, None


__all__ = [
    "fetch_quote",
    "parse_tencent_quote",
    "parse_sina_quote",
    "price_cross_check",
]
