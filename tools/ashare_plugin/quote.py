from typing import Any, Dict, Optional

from . import DataResult, failure_result, success_result
from .errors import TransportError
from .identifiers import normalize_code
from .transport import TransportClient


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


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
        "turnover_amt": fields[37] if len(fields) > 37 else "-",
        "turnover_rate": fields[38] if len(fields) > 38 else "-",
        "pe": fields[39] if len(fields) > 39 else "-",
        "float_cap": fields[44] if len(fields) > 44 else "-",
        "market_cap": fields[45] if len(fields) > 45 else "-",
        "pb": fields[46] if len(fields) > 46 else "-",
        "limit_up": fields[47] if len(fields) > 47 else "-",
        "limit_down": fields[48] if len(fields) > 48 else "-",
    }


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
    return success_result(
        quote,
        "tencent+eastmoney",
        warnings=warnings,
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


__all__ = ["fetch_quote", "parse_tencent_quote"]
