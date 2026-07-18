"""Network transport and source fallback primitives for A-share providers."""

import json
import subprocess
import time
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urlencode

from . import DataResult, failure_result
from .errors import TransportError


Runner = Callable[..., subprocess.CompletedProcess]


class TransportClient:
    def __init__(
        self,
        *,
        runner: Optional[Runner] = None,
        timeout: int = 15,
        retries: int = 1,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.runner = runner or subprocess.run
        self.timeout = timeout
        self.retries = max(0, retries)
        self.sleep = sleep

    def get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        query_url = f"{url}?{urlencode(params)}" if params else url
        args = [
            "/usr/bin/curl",
            "-sS",
            "--noproxy",
            "*",
            "--max-time",
            str(self.timeout),
            "-G",
            query_url,
        ]
        for name, value in (headers or {}).items():
            args.extend(["-H", f"{name}: {value}"])

        for attempt in range(self.retries + 1):
            try:
                result = self.runner(args, capture_output=True, timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                raise TransportError("request timeout", "timeout") from exc
            except OSError as exc:
                raise TransportError(f"curl unavailable: {exc}", "http_error") from exc

            stderr = (result.stderr or b"").decode("utf-8", "replace")
            body = result.stdout or b""
            if result.returncode == 0 and body.strip():
                try:
                    return json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise TransportError("invalid JSON response", "parse_error") from exc

            transient = any(code in stderr for code in ("429", "500", "502", "503", "504"))
            if transient and attempt < self.retries:
                self.sleep(0.2 * (attempt + 1))
                continue

            error_type = "rate_limited" if "429" in stderr or "403" in stderr else "http_error"
            message = stderr.strip() or "empty HTTP response"
            raise TransportError(message, error_type)

        raise TransportError("request failed", "http_error")

    def get_text(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> str:
        query_url = f"{url}?{urlencode(params)}" if params else url
        args = [
            "/usr/bin/curl",
            "-sS",
            "--noproxy",
            "*",
            "--max-time",
            str(self.timeout),
            "-G",
            query_url,
        ]
        for name, value in (headers or {}).items():
            args.extend(["-H", f"{name}: {value}"])
        try:
            result = self.runner(args, capture_output=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise TransportError("request timeout", "timeout") from exc
        except OSError as exc:
            raise TransportError(f"curl unavailable: {exc}", "http_error") from exc
        if result.returncode != 0 or not (result.stdout or b"").strip():
            stderr = (result.stderr or b"").decode("utf-8", "replace").strip()
            error_type = "rate_limited" if "429" in stderr or "403" in stderr else "http_error"
            raise TransportError(stderr or "empty HTTP response", error_type)
        raw = result.stdout
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("gbk", "replace")

    def post_json(
        self,
        url: str,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        json_body: bool = False,
    ) -> Any:
        args = [
            "/usr/bin/curl",
            "-sS",
            "--noproxy",
            "*",
            "--max-time",
            str(self.timeout),
            "-X",
            "POST",
        ]
        request_headers = dict(headers or {})
        if json_body:
            request_headers.setdefault("Content-Type", "application/json")
        for name, value in request_headers.items():
            args.extend(["-H", f"{name}: {value}"])
        body_bytes = None
        if data is not None:
            body = (
                json.dumps(data, ensure_ascii=False)
                if json_body else urlencode(data)
            )
            body_bytes = body.encode("utf-8")
            args.extend(["--data-binary", "@-"])
        args.append(url)
        try:
            result = self.runner(
                args,
                input=body_bytes,
                capture_output=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise TransportError("request timeout", "timeout") from exc
        except OSError as exc:
            raise TransportError(f"curl unavailable: {exc}", "http_error") from exc
        stderr = (result.stderr or b"").decode("utf-8", "replace")
        if result.returncode != 0 or not (result.stdout or b"").strip():
            error_type = "rate_limited" if "429" in stderr or "403" in stderr else "http_error"
            raise TransportError(stderr.strip() or "empty HTTP response", error_type)
        try:
            return json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TransportError("invalid JSON response", "parse_error") from exc


class FallbackChain:
    """Try providers in order and preserve the reason each earlier source failed."""

    def __init__(self, attempts: Iterable[Callable[[], DataResult]]):
        self.attempts = list(attempts)

    def run(self) -> DataResult:
        failures: List[str] = []
        last_source = "unknown"
        for index, attempt in enumerate(self.attempts):
            try:
                result = attempt()
            except Exception as exc:
                failures.append(f"provider-{index + 1}: {exc}")
                continue
            last_source = result.get("source", last_source)
            if result.get("ok"):
                if index:
                    result = dict(result)
                    result["fallback_used"] = True
                    result["warnings"] = failures + list(result.get("warnings", []))
                return result
            failures.append(
                f"{last_source}: {result.get('message') or result.get('error_type', 'failed')}"
            )

        return failure_result(
            last_source,
            "all_sources_failed",
            "所有配置数据源均未返回可用数据",
            fallback_used=len(self.attempts) > 1,
            warnings=failures,
        )


__all__ = ["FallbackChain", "TransportClient", "TransportError"]
