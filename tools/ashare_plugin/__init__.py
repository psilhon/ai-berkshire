"""Reusable A-share data providers for Berkshire research workflows."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


DataResult = Dict[str, Any]


def success_result(
    data: Any,
    source: str,
    *,
    as_of: Optional[str] = None,
    fallback_used: bool = False,
    warnings: Optional[List[str]] = None,
) -> DataResult:
    """Build a successful, source-attributed data result."""
    return {
        "ok": True,
        "data": data,
        "source": source,
        "fallback_used": fallback_used,
        "as_of": as_of or datetime.now(timezone.utc).isoformat(),
        "warnings": list(warnings or []),
    }


def failure_result(
    source: str,
    error_type: str,
    message: str,
    *,
    fallback_used: bool = False,
    warnings: Optional[List[str]] = None,
) -> DataResult:
    """Build an explicit failure result; never use an empty payload as failure."""
    return {
        "ok": False,
        "data": None,
        "source": source,
        "fallback_used": fallback_used,
        "as_of": None,
        "warnings": list(warnings or []),
        "error_type": error_type,
        "message": message,
    }


__all__ = ["DataResult", "failure_result", "success_result"]
