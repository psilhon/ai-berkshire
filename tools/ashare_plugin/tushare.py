"""Minimal Tushare HTTP client used only for optional verification."""

import os
from typing import Any, Dict, Optional, Sequence

from . import DataResult, failure_result, success_result
from .errors import TransportError
from .transport import TransportClient


TUSHARE_URL = "https://api.tushare.pro"


def _api_error_type(code: Any, message: str) -> str:
    text = message.lower()
    if code == 2002 or "权限" in message or "permission" in text:
        return "permission_denied"
    if "每分钟" in message or "频率" in message or "limit" in text:
        return "rate_limited"
    return "upstream_error"


class TushareClient:
    def __init__(
        self,
        token: Optional[str] = None,
        transport: Optional[TransportClient] = None,
    ):
        self._token = token if token is not None else os.environ.get("TUSHARE_TOKEN")
        self._transport = transport or TransportClient()

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def query(
        self,
        api_name: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        fields: Sequence[str] = (),
    ) -> DataResult:
        if not self.configured:
            return failure_result(
                "tushare", "not_configured", "未配置 TUSHARE_TOKEN"
            )

        payload = {
            "api_name": api_name,
            "token": self._token,
            "params": dict(params or {}),
            "fields": ",".join(fields),
        }
        try:
            response = self._transport.post_json(
                TUSHARE_URL, data=payload, json_body=True
            )
        except TransportError as exc:
            return failure_result(
                "tushare", exc.error_type,
                f"Tushare {api_name} 请求失败: {exc}"
            )

        if not isinstance(response, dict):
            return failure_result("tushare", "schema_error", "Tushare 响应不是对象")

        code = response.get("code")
        message = str(response.get("msg") or "")
        if code != 0:
            error_type = _api_error_type(code, message)
            fixed_message = {
                "permission_denied": "Tushare 接口无访问权限",
                "rate_limited": "Tushare 接口达到访问频率限制",
                "upstream_error": "Tushare 接口返回失败",
            }[error_type]
            return failure_result("tushare", error_type, fixed_message)

        data = response.get("data")
        if not isinstance(data, dict):
            return failure_result("tushare", "schema_error", "Tushare data 不是对象")

        names = data.get("fields")
        items = data.get("items")
        if not isinstance(names, list) or not isinstance(items, list):
            return failure_result("tushare", "schema_error", "Tushare fields/items 非数组")
        if not items:
            return failure_result("tushare", "empty_data", "Tushare 返回空数据")

        rows = []
        for item in items:
            if not isinstance(item, list) or len(item) != len(names):
                return failure_result("tushare", "schema_error", "Tushare 行列数量不一致")
            rows.append(dict(zip(names, item)))
        return success_result(rows, f"tushare.{api_name}")


__all__ = ["TUSHARE_URL", "TushareClient"]
