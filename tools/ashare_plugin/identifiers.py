from dataclasses import dataclass

from .errors import InvalidCodeError


@dataclass(frozen=True)
class CodeIdentity:
    code: str
    market: str

    @property
    def secu_code(self) -> str:
        return f"{self.code}.{self.market}"

    @property
    def secid(self) -> str:
        return f"{'1' if self.market == 'SH' else '0'}.{self.code}"

    @property
    def quote_code(self) -> str:
        return f"{self.market.lower()}{self.code}"


def normalize_code(value: str) -> CodeIdentity:
    """Normalize a six-digit A-share code with an optional market suffix."""
    if not isinstance(value, str):
        raise InvalidCodeError(f"无效 A 股代码: {value}")

    raw = value.strip().upper()
    parts = raw.rsplit(".", 1)
    code = parts[0]
    if len(code) != 6 or not code.isdigit():
        raise InvalidCodeError(f"无效 A 股代码: {value}")

    explicit_market = parts[1] if len(parts) == 2 else None
    if explicit_market is not None and explicit_market not in {"SH", "SZ", "BJ"}:
        raise InvalidCodeError(f"无效市场后缀: {value}")

    if explicit_market:
        market = explicit_market
    elif code.startswith(("6", "9", "5")):
        market = "SH"
    elif code.startswith(("4", "8")):
        market = "BJ"
    elif code.startswith(("0", "1", "2", "3")):
        market = "SZ"
    else:
        raise InvalidCodeError(f"无法判断 A 股市场: {value}")

    return CodeIdentity(code=code, market=market)


__all__ = ["CodeIdentity", "normalize_code"]
