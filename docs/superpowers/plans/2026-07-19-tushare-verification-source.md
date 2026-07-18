# Tushare 可选交叉验证数据源 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变现有八个 A 股 CLI 命令主数据源、参数和退出码的前提下，安全接入 Tushare 作为可选独立验证源，并让 Claude Code、Codex 和 `full-company-analysis` 共享一致结果。

**Architecture:** 先把 JSON POST 改为 stdin 传输，再新增只负责 Tushare 协议的 `TushareClient`；独立的 `tushare_verification.py` 负责期间、单位、字段映射和四态判定。CLI 只在主结果成功后附加验证摘要，Tushare 未配置或失败不改变主命令成败；全量分析继续使用既有 facts/source/gate 契约，不扩展 manifest 封闭 schema。

**Tech Stack:** Python 3.8+ 标准库、`/usr/bin/curl`、`decimal.Decimal`、`unittest`、Markdown Skill 规范及现有生成脚本。

## Global Constraints

- Tushare 是可选独立验证源，不是主源或 fallback；腾讯、东方财富、巨潮现有顺序不变。
- token 只从 `TUSHARE_TOKEN` 环境变量取得；不读取 `.env`，不进入 argv、stdout、stderr、异常、日志、报告或测试快照。
- Tushare JSON 请求体必须通过 stdin 交给 `curl --data-binary @-`。
- 未配置 token 时网络调用严格为零，验证状态为 `NOT_CONFIGURED`。
- 字段状态仅为 `MATCH`、`CONFLICT`、`INSUFFICIENT`；整体另允许 `NOT_CONFIGURED`。
- 默认相对偏差阈值为 `1%`，计算使用 `Decimal`；期间、单位或报告版本不一致时不得判为 `MATCH`。
- 八个 CLI 命令不新增参数，主退出码语义不变；本次不新增 `--json`。
- 仅使用 Python 标准库和现有 curl，不增加 Tushare SDK、pandas 或其他运行时依赖。
- `skills/*.md` 是 Claude/Codex 共同规范源，修改后必须重新生成并检查 Codex 产物。
- 默认输出本地化，不改写无关报告，不执行 `git add`、commit、push、PR 或发布；每个任务以测试结果作为本地 checkpoint。
- 保留工作树中已有用户改动，不回滚、不顺手重构。

---

## File Map

| 文件 | 动作 | 单一职责 |
|---|---|---|
| `tools/ashare_plugin/transport.py` | 修改 | 以 stdin 安全发送 POST body |
| `tools/ashare_plugin/tushare.py` | 新建 | Tushare HTTP 协议、响应解码和错误分类 |
| `tools/ashare_plugin/tushare_verification.py` | 新建 | 验证 schema、Decimal 比较、期间/单位映射、八命令路由 |
| `tools/ashare_plugin/__init__.py` | 修改 | 将验证块无损附加到 `DataResult` |
| `tools/ashare_plugin/quote.py` | 修改 | 暴露腾讯行情时间，供日终同日判定 |
| `tools/ashare_data.py` | 修改 | 八个命令附加 fail-open 验证摘要 |
| `tests/test_ashare_plugin_transport.py` | 修改 | stdin 与 token 不可观察回归 |
| `tests/test_ashare_plugin_tushare.py` | 新建 | 客户端协议、权限、限流、空数据和 schema 测试 |
| `tests/test_ashare_plugin_tushare_verification.py` | 新建 | 四态、Decimal、期间、单位和八命令映射测试 |
| `tests/test_ashare_plugin_quote.py` | 修改 | 腾讯行情时间字段解析测试 |
| `tests/test_ashare_data.py` | 修改 | CLI 附加验证且退出码不回归 |
| `tests/test_full_analysis_gate.py` | 修改 | Tushare 独立链可形成双源，冲突保持 fail-closed |
| `skills/ashare-data.md` | 修改 | 配置、输出、失败和八命令验证规则 |
| `skills/financial-data.md` | 修改 | Tushare 在双源规范中的地位与冲突处置 |
| `skills/full-company-analysis.md` | 修改 | 将验证状态翻译为 facts/source/limitation 的规则 |
| `codex-skills/*/SKILL.md` | 生成 | 与三份 canonical Skill 同步 |
| `codex-prompts/*.md` | 生成检查 | 保持兼容提示词无漂移 |
| `local/筛选公司/招商银行/tushare-validation-2026-07-19.md` | 真实验证时新建 | 本地记录接口可用性、权限和字段覆盖，不含 token |

---

### Task 1: 让 JSON POST 请求体只经过 stdin

**Files:**
- Modify: `tools/ashare_plugin/transport.py:112-152`
- Modify: `tests/test_ashare_plugin_transport.py:45-73`

**Interfaces:**
- Consumes: `TransportClient.runner(args, **kwargs)` 现有可注入 runner。
- Produces: `TransportClient.post_json(url, data=None, headers=None, json_body=False) -> Any`；body 位于 runner 的 `input: bytes`，argv 只含 `--data-binary @-`。

- [ ] **Step 1: 把现有 JSON POST 测试改成 stdin 契约，并加入 token 哨兵断言**

```python
def test_post_json_sends_body_via_stdin_without_argv_leak(self):
    calls = []
    sentinel = "TUSHARE_SENTINEL_NEVER_EXPOSE"

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args, 0, stdout=b'{"ok": true}', stderr=b"")

    data = TransportClient(runner=runner).post_json(
        "https://example.test/api",
        data={"token": sentinel, "pageNum": 1},
        json_body=True,
    )

    self.assertEqual(data, {"ok": True})
    args, kwargs = calls[0]
    self.assertIn("--data-binary", args)
    self.assertEqual(args[args.index("--data-binary") + 1], "@-")
    self.assertNotIn(sentinel, " ".join(args))
    self.assertEqual(
        json.loads(kwargs["input"].decode("utf-8")),
        {"token": sentinel, "pageNum": 1},
    )
    self.assertIn("Content-Type: application/json", args)
```

- [ ] **Step 2: 运行单测并确认它因 body 仍在 argv 中而失败**

Run: `python3 -m unittest tests.test_ashare_plugin_transport.TestTransportClient.test_post_json_sends_body_via_stdin_without_argv_leak -v`

Expected: FAIL，报错显示 argv 中没有 `--data-binary` 或 runner kwargs 中没有 `input`。

- [ ] **Step 3: 最小修改 `post_json` 的 body 传输方式**

将 `post_json` 中构造 body 和调用 runner 的部分改为：

```python
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
```

错误路径继续只使用固定错误或 curl stderr，不拼接 `body`、`data` 或 `body_bytes`。

- [ ] **Step 4: 运行 transport 全文件测试**

Run: `python3 -m unittest tests.test_ashare_plugin_transport -v`

Expected: PASS；原 GET、fallback 和 POST 测试全部通过。

- [ ] **Step 5: 记录本地 checkpoint**

Run: `rg -n -- "--data( |\")|TUSHARE_SENTINEL_NEVER_EXPOSE" tools/ashare_plugin/transport.py tests/test_ashare_plugin_transport.py`

Expected: 生产代码只出现 `--data-binary`；哨兵只存在测试文件。

---

### Task 2: 实现最小 Tushare HTTP 客户端

**Files:**
- Create: `tools/ashare_plugin/tushare.py`
- Create: `tests/test_ashare_plugin_tushare.py`

**Interfaces:**
- Consumes: `TransportClient.post_json`、`success_result`、`failure_result`。
- Produces: `TushareClient(token: Optional[str] = None, transport: Optional[TransportClient] = None)`；只读属性 `configured: bool`；方法 `query(api_name: str, *, params: Optional[Dict[str, Any]] = None, fields: Sequence[str] = ()) -> DataResult`。

- [ ] **Step 1: 写客户端的失败测试**

```python
import os
import unittest
from unittest import mock

from tools.ashare_plugin.tushare import TushareClient


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class TestTushareClient(unittest.TestCase):
    @mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_token_returns_not_configured_without_transport_call(self):
        transport = FakeTransport({"code": 0, "data": {"fields": [], "items": []}})
        result = TushareClient(transport=transport).query("daily_basic")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "not_configured")
        self.assertEqual(transport.calls, [])

    def test_success_maps_fields_and_items_to_dict_rows(self):
        transport = FakeTransport({
            "code": 0,
            "msg": None,
            "data": {
                "fields": ["ts_code", "trade_date", "pb"],
                "items": [["600036.SH", "20260718", 1.2]],
            },
        })
        result = TushareClient(token="test-token", transport=transport).query(
            "daily_basic",
            params={"ts_code": "600036.SH"},
            fields=("ts_code", "trade_date", "pb"),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "tushare.daily_basic")
        self.assertEqual(result["data"][0]["pb"], 1.2)
        body = transport.calls[0][1]["data"]
        self.assertEqual(body["api_name"], "daily_basic")
        self.assertEqual(body["fields"], "ts_code,trade_date,pb")

    def test_permission_rate_empty_and_schema_are_distinct(self):
        cases = [
            ({"code": 2002, "msg": "无权限", "data": None}, "permission_denied"),
            ({"code": -2001, "msg": "每分钟最多访问 50 次", "data": None}, "rate_limited"),
            ({"code": 0, "msg": None, "data": {"fields": ["x"], "items": []}}, "empty_data"),
            ({"code": 0, "msg": None, "data": {"fields": ["x"], "items": [[1, 2]]}}, "schema_error"),
        ]
        for response, expected in cases:
            with self.subTest(expected=expected):
                result = TushareClient(
                    token="test-token", transport=FakeTransport(response)
                ).query("sample")
                self.assertFalse(result["ok"])
                self.assertEqual(result["error_type"], expected)
```

- [ ] **Step 2: 运行测试并确认模块尚不存在**

Run: `python3 -m unittest tests.test_ashare_plugin_tushare -v`

Expected: ERROR，`tools.ashare_plugin.tushare` 尚不存在。

- [ ] **Step 3: 实现客户端与固定错误分类**

`tools/ashare_plugin/tushare.py` 使用以下完整公开结构：

```python
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
            return failure_result("tushare", exc.error_type, "Tushare 网络请求失败")
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
```

- [ ] **Step 4: 运行客户端测试**

Run: `python3 -m unittest tests.test_ashare_plugin_tushare -v`

Expected: PASS，四类错误互不混淆，未配置时 transport 调用数为 0。

- [ ] **Step 5: 执行静态 secret 检查**

Run: `rg -n "print\(.*token|repr\(.*token|message.*token|args.*token" tools/ashare_plugin/tushare.py`

Expected: 无输出。

---

### Task 3: 冻结验证结果 schema 与 Decimal 判定

**Files:**
- Create: `tools/ashare_plugin/tushare_verification.py`
- Modify: `tools/ashare_plugin/__init__.py:1-40`
- Create: `tests/test_ashare_plugin_tushare_verification.py`

**Interfaces:**
- Consumes: `DataResult`、`Decimal`。
- Produces: `not_configured_verification() -> Dict[str, Any]`、`compare_decimal`、`compare_text`、`finalize_verification(fields, endpoints) -> Dict[str, Any]`、`with_verification(result, verification) -> DataResult`。

- [ ] **Step 1: 写四态、期间、单位、阈值和聚合测试**

```python
import unittest

from tools.ashare_plugin.tushare_verification import (
    compare_text,
    compare_decimal,
    finalize_verification,
    not_configured_verification,
)
from tools.ashare_plugin import success_result, with_verification


class TestVerificationCore(unittest.TestCase):
    def test_not_configured_has_closed_top_level_shape(self):
        result = not_configured_verification()
        self.assertEqual(set(result), {
            "provider", "configured", "status", "as_of",
            "warnings", "fields", "endpoints",
        })
        self.assertEqual(result["status"], "NOT_CONFIGURED")

    def test_decimal_threshold_is_inclusive(self):
        field = compare_decimal(
            "pb", "100", "101",
            primary_source="eastmoney",
            verification_source="tushare.daily_basic",
            primary_period="2026-07-18",
            verification_period="2026-07-18",
            primary_unit="multiple",
            verification_unit="multiple",
        )
        self.assertEqual(field["status"], "MATCH")
        self.assertEqual(field["deviation_pct"], "1.00")

    def test_period_or_unit_mismatch_is_insufficient(self):
        period = compare_decimal(
            "pb", "1", "1", primary_source="eastmoney",
            verification_source="tushare.daily_basic",
            primary_period="2026-07-18", verification_period="2026-07-17",
            primary_unit="multiple", verification_unit="multiple",
        )
        unit = compare_decimal(
            "market_cap", "1", "1", primary_source="tencent",
            verification_source="tushare.daily_basic",
            primary_period="2026-07-18", verification_period="2026-07-18",
            primary_unit="CNY", verification_unit="CNY_10K",
        )
        self.assertEqual(period["status"], "INSUFFICIENT")
        self.assertEqual(period["error_type"], "period_mismatch")
        self.assertEqual(unit["error_type"], "unit_mismatch")

    def test_conflict_precedes_match_in_aggregate(self):
        matching = compare_decimal(
            "pb", "1", "1", primary_source="a", verification_source="b",
            primary_period="2026", verification_period="2026",
            primary_unit="multiple", verification_unit="multiple",
        )
        conflict = compare_decimal(
            "pe", "10", "12", primary_source="a", verification_source="b",
            primary_period="2026", verification_period="2026",
            primary_unit="multiple", verification_unit="multiple",
        )
        result = finalize_verification([matching, conflict], [])
        self.assertEqual(result["status"], "CONFLICT")

    def test_text_difference_is_conflict(self):
        field = compare_text(
            "security_name", "招商银行", "招商银航",
            primary_source="eastmoney",
            verification_source="tushare.stock_basic",
            period="current",
        )
        self.assertEqual(field["status"], "CONFLICT")

    def test_with_verification_does_not_mutate_primary_result(self):
        primary = success_result({"pb": "1.2"}, "eastmoney")
        verification = not_configured_verification()
        combined = with_verification(primary, verification)
        self.assertNotIn("verification", primary)
        self.assertIs(combined["verification"], verification)
```

- [ ] **Step 2: 运行测试并确认核心函数不存在**

Run: `python3 -m unittest tests.test_ashare_plugin_tushare_verification.TestVerificationCore -v`

Expected: ERROR，导入失败。

- [ ] **Step 3: 实现封闭字段结果和聚合函数**

核心实现必须使用以下签名和状态优先级：

```python
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional


FIELD_STATUSES = {"MATCH", "CONFLICT", "INSUFFICIENT"}


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
        "verification_value": None if verification_value is None else str(verification_value),
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
            name, "INSUFFICIENT", primary_value, verification_value,
            primary_source, verification_source, primary_period, primary_unit,
            error_type="period_mismatch",
        )
    if primary_unit != verification_unit:
        return _field(
            name, "INSUFFICIENT", primary_value, verification_value,
            primary_source, verification_source, primary_period, primary_unit,
            error_type="unit_mismatch",
        )
    left, right, tolerance = (
        _decimal(primary_value), _decimal(verification_value), _decimal(tolerance_pct)
    )
    if left is None or right is None or tolerance is None:
        return _field(
            name, "INSUFFICIENT", primary_value, verification_value,
            primary_source, verification_source, primary_period, primary_unit,
            error_type="missing_value",
        )
    if left == 0:
        if right == 0:
            deviation = Decimal("0")
        else:
            return _field(
                name, "INSUFFICIENT", primary_value, verification_value,
                primary_source, verification_source, primary_period, primary_unit,
                error_type="zero_denominator",
            )
    else:
        deviation = abs(right - left) / abs(left) * Decimal("100")
    status = "MATCH" if deviation <= tolerance else "CONFLICT"
    return _field(
        name, status, primary_value, verification_value,
        primary_source, verification_source, primary_period, primary_unit,
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
            name, "INSUFFICIENT", primary_value, verification_value,
            primary_source, verification_source, period, "text",
            error_type="missing_value",
        )
    status = "MATCH" if str(primary_value).strip() == str(verification_value).strip() else "CONFLICT"
    return _field(
        name, status, primary_value, verification_value,
        primary_source, verification_source, period, "text",
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
        for row in endpoint_rows if not row.get("ok")
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
```

并在 `tools/ashare_plugin/__init__.py` 增加：

```python
def with_verification(result: DataResult, verification: Dict[str, Any]) -> DataResult:
    combined = dict(result)
    combined["verification"] = verification
    return combined
```

同时把 `with_verification` 加入 `__all__`。

- [ ] **Step 4: 运行核心与现有插件测试**

Run: `python3 -m unittest tests.test_ashare_plugin_tushare_verification tests.test_ashare_plugin_core -v`

Expected: PASS；现有 `DataResult` 顶层字段保持不变，只有显式调用时增加 `verification`。

- [ ] **Step 5: 记录本地 checkpoint**

Run: `python3 -m unittest tests.test_ashare_plugin_tushare tests.test_ashare_plugin_tushare_verification -v`

Expected: PASS。

---

### Task 4: 实现八命令的 API 路由和严格可比性适配

**Files:**
- Modify: `tools/ashare_plugin/tushare_verification.py`
- Modify: `tests/test_ashare_plugin_tushare_verification.py`

**Interfaces:**
- Consumes: `TushareClient.query`、Task 3 比较函数、现有命令的主数据字典或列表。
- Produces: `verify_command(command: str, subject: str, primary_data: Any, *, trade_date: Optional[str] = None, client: Optional[TushareClient] = None) -> Dict[str, Any]`。

- [ ] **Step 1: 写八命令路由、同日、市值单位和财报修订版测试**

测试文件增加一个记录调用的 fake client：

```python
class FakeTushareClient:
    configured = True

    def __init__(self, results):
        self.results = results
        self.calls = []

    def query(self, api_name, *, params=None, fields=()):
        self.calls.append((api_name, dict(params or {}), tuple(fields)))
        return self.results[api_name]
```

并增加以下断言：

```python
def _ok(source, rows):
    return {
        "ok": True, "data": rows, "source": source,
        "fallback_used": False, "as_of": "2026-07-19T00:00:00+00:00",
        "warnings": [],
    }


class TestCommandVerification(unittest.TestCase):
    def test_market_value_converts_tencent_yi_to_tushare_ten_thousand_cny(self):
        client = FakeTushareClient({
            "daily_basic": _ok("tushare.daily_basic", [{
                "ts_code": "600036.SH", "trade_date": "20260718",
                "close": 10, "pe": 8, "pb": 1.1, "turnover_rate": 1.2,
                "total_mv": 1000000, "circ_mv": 800000,
            }]),
        })
        result = verify_command("valuation", "600036", {
            "quote_time": "20260718150000", "price": "10",
            "pe": "8", "pb": "1.1", "turnover_rate": "1.2",
            "market_cap": "100", "float_cap": "80",
        }, client=client)
        self.assertEqual(result["status"], "MATCH")
        market_cap = next(x for x in result["fields"] if x["field"] == "market_cap")
        self.assertEqual(market_cap["primary_value"], "1000000")
        self.assertEqual(market_cap["unit"], "CNY_10K")

    def test_market_date_mismatch_never_matches(self):
        client = FakeTushareClient({
            "daily_basic": _ok("tushare.daily_basic", [{
                "ts_code": "600036.SH", "trade_date": "20260717", "close": 10,
            }]),
        })
        result = verify_command("quote", "600036", {
            "quote_time": "20260718113000", "price": "10",
        }, client=client)
        self.assertEqual(result["status"], "INSUFFICIENT")
        self.assertEqual(result["fields"][0]["error_type"], "period_mismatch")

    def test_financials_selects_same_period_latest_update(self):
        client = FakeTushareClient({
            "income": _ok("tushare.income", [
                {"end_date": "20251231", "f_ann_date": "20260320", "report_type": "1", "update_flag": "0", "total_revenue": 99},
                {"end_date": "20251231", "f_ann_date": "20260401", "report_type": "1", "update_flag": "1", "total_revenue": 100},
            ]),
            "balancesheet": _ok("tushare.balancesheet", [{"end_date": "20251231", "update_flag": "1"}]),
            "cashflow": _ok("tushare.cashflow", [{"end_date": "20251231", "update_flag": "1", "n_cashflow_act": 20}]),
            "fina_indicator": _ok("tushare.fina_indicator", [{"end_date": "20251231", "update_flag": "1", "roe": 10}]),
        })
        result = verify_command("financials", "600036", [{
            "REPORT_DATE": "2025-12-31", "REPORT_DATE_NAME": "2025年报",
            "TOTALOPERATEREVE": 100, "ROEJQ": 10,
        }], client=client)
        revenue = next(x for x in result["fields"] if x["field"] == "revenue")
        self.assertEqual(revenue["verification_value"], "100")
        self.assertEqual(revenue["status"], "MATCH")

    def test_every_command_has_an_explicit_route(self):
        self.assertEqual(set(COMMAND_APIS), {
            "quote", "valuation", "financials", "history",
            "equity-history", "search", "signals", "announcements",
        })
```

- [ ] **Step 2: 运行路由测试并确认 `verify_command` 尚不存在**

Run: `python3 -m unittest tests.test_ashare_plugin_tushare_verification.TestCommandVerification -v`

Expected: ERROR，无法导入 `verify_command` 或 `COMMAND_APIS`。

- [ ] **Step 3: 增加明确的 endpoint 与字段映射常量**

在 `tushare_verification.py` 中加入：

```python
COMMAND_APIS = {
    "quote": ("daily_basic",),
    "valuation": ("daily_basic",),
    "financials": ("income", "balancesheet", "cashflow", "fina_indicator"),
    "history": ("income", "balancesheet", "cashflow", "fina_indicator"),
    "equity-history": ("daily_basic", "share_float"),
    "search": ("stock_basic",),
    "signals": ("moneyflow", "top_list", "margin_detail", "share_float"),
    "announcements": ("anns_d",),
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
}
```

- [ ] **Step 4: 实现 endpoint 查询、严格选行和字段适配**

实现以下公开路由与内部规则：

```python
def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _endpoint(api_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "api_name": api_name,
        "ok": bool(result.get("ok")),
        "source": result.get("source", f"tushare.{api_name}"),
        "error_type": None if result.get("ok") else result.get("error_type", "upstream_error"),
        "row_count": len(result.get("data") or []) if result.get("ok") else 0,
    }


def _latest_period_row(rows: List[Dict[str, Any]], period: str) -> Optional[Dict[str, Any]]:
    matches = [
        row for row in rows
        if _digits(row.get("end_date")) == period
        and ("report_type" not in row or str(row.get("report_type")) == "1")
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


def _params(api_name: str, command: str, ts_code: str, subject: str, primary_data: Any, trade_date: Optional[str]) -> Dict[str, Any]:
    if api_name == "stock_basic":
        return {"name": subject}
    result = {"ts_code": ts_code}
    if api_name == "daily_basic":
        if command in {"quote", "valuation"} and isinstance(primary_data, dict):
            requested_date = _digits(primary_data.get("quote_time"))
        elif command == "equity-history" and isinstance(primary_data, list) and primary_data:
            latest = max(primary_data, key=lambda row: _digits(row.get("END_DATE")))
            requested_date = _digits(latest.get("END_DATE"))
        else:
            requested_date = ""
        if requested_date:
            result["trade_date"] = requested_date
    if api_name in {"moneyflow", "top_list", "margin_detail"} and trade_date:
        result["trade_date"] = _digits(trade_date)
    if api_name == "anns_d" and trade_date:
        result["ann_date"] = _digits(trade_date)
    return result


def _market_fields(command: str, primary: Dict[str, Any], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    primary_period = _digits(primary.get("quote_time"))
    selected = max(rows, key=lambda row: _digits(row.get("trade_date"))) if rows else {}
    verification_period = _digits(selected.get("trade_date"))
    mappings = [
        ("close", "price", "close", "CNY", Decimal("1"), Decimal("1")),
        ("market_cap", "market_cap", "total_mv", "CNY_10K", Decimal("10000"), Decimal("1")),
        ("float_cap", "float_cap", "circ_mv", "CNY_10K", Decimal("10000"), Decimal("1")),
    ]
    if command == "valuation":
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


def _financial_fields(primary_rows: List[Dict[str, Any]], results: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
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
            row = _latest_period_row(result.get("data") or [], period) if result.get("ok") else None
            fields.append(compare_decimal(
                name,
                primary.get(primary_key),
                None if row is None else row.get(verify_key),
                primary_source="eastmoney",
                verification_source=f"tushare.{api_name}",
                primary_period=period,
                verification_period=period if row is not None else "",
                primary_unit=unit,
                verification_unit=unit,
            ))
    return fields


def _search_fields(primary_rows: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_code = {str(row.get("symbol") or ""): row for row in rows}
    fields = []
    for primary in primary_rows:
        code = str(primary.get("Code") or "")
        row = by_code.get(code)
        fields.append(compare_text(
            f"security_name:{code}", primary.get("Name"), None if row is None else row.get("name"),
            primary_source="eastmoney", verification_source="tushare.stock_basic", period="current",
        ))
    return fields


def _equity_fields(primary_rows: List[Dict[str, Any]], results: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not primary_rows:
        return []
    primary = max(primary_rows, key=lambda row: _digits(row.get("END_DATE")))
    period = _digits(primary.get("END_DATE"))
    daily_rows = results["daily_basic"].get("data") or []
    row = next((item for item in daily_rows if _digits(item.get("trade_date")) == period), None)
    left = _decimal(primary.get("TOTAL_SHARES"))
    return [compare_decimal(
        "total_shares",
        None if left is None else left / Decimal("10000"),
        None if row is None else row.get("total_share"),
        primary_source="eastmoney", verification_source="tushare.daily_basic",
        primary_period=period, verification_period=period if row is not None else "",
        primary_unit="SHARES_10K", verification_unit="SHARES_10K",
    )]


def _signal_fields(primary: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    block = primary.get("dragon_tiger") or {}
    primary_rows = (block.get("data") or []) if block.get("ok") else []
    verify_rows = (results["top_list"].get("data") or []) if results["top_list"].get("ok") else []
    fields = []
    for row in primary_rows:
        period = _digits(row.get("date"))
        matched = next((item for item in verify_rows if _digits(item.get("trade_date")) == period), None)
        fields.append(compare_decimal(
            "dragon_tiger_net_buy", row.get("net_buy"), None if matched is None else matched.get("net_amount"),
            primary_source=block.get("source", "eastmoney"), verification_source="tushare.top_list",
            primary_period=period, verification_period=period if matched is not None else "",
            primary_unit="CNY", verification_unit="CNY",
        ))
    return fields


def _announcement_fields(primary: Dict[str, Any], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    tushare_keys = {(_digits(row.get("ann_date")), str(row.get("title") or "").strip()) for row in rows}
    fields = []
    for index, row in enumerate(primary.get("data") or []):
        period = _digits(row.get("date"))
        title = str(row.get("title") or "").strip()
        matched = title if (period, title) in tushare_keys else None
        fields.append(compare_text(
            f"announcement:{index + 1}", title, matched,
            primary_source=primary.get("source", "unknown"),
            verification_source="tushare.anns_d", period=period,
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
            params=_params(api_name, command, ts_code, subject, primary_data, trade_date),
            fields=API_FIELDS[api_name],
        )
        results[api_name] = result
        endpoints.append(_endpoint(api_name, result))
    if command in {"quote", "valuation"}:
        fields = _market_fields(command, primary_data, results["daily_basic"].get("data") or [])
    elif command in {"financials", "history"}:
        fields = _financial_fields(primary_data, results)
    elif command == "equity-history":
        fields = _equity_fields(primary_data, results)
    elif command == "search":
        fields = _search_fields(primary_data, results["stock_basic"].get("data") or [])
    elif command == "signals":
        fields = _signal_fields(primary_data, results)
    else:
        fields = _announcement_fields(primary_data, results["anns_d"].get("data") or [])
    return finalize_verification(fields, endpoints)
```

在文件 imports 中加入 `normalize_code`、`TushareClient`、`Decimal` 已有引用所需类型，并把 `COMMAND_APIS` 与 `verify_command` 加入 `__all__`。

- [ ] **Step 5: 运行映射测试并修正纯字段错误**

Run: `python3 -m unittest tests.test_ashare_plugin_tushare_verification -v`

Expected: PASS；同日市值匹配、日期错位不足、修订版选择和八命令集合全部通过。

- [ ] **Step 6: 增加部分权限失败测试**

```python
def test_partial_endpoint_failure_keeps_successful_financial_fields(self):
    client = FakeTushareClient({
        "income": _ok("tushare.income", [{
            "end_date": "20251231", "report_type": "1", "update_flag": "1", "total_revenue": 100,
        }]),
        "balancesheet": {"ok": False, "data": None, "source": "tushare", "error_type": "permission_denied", "warnings": []},
        "cashflow": {"ok": False, "data": None, "source": "tushare", "error_type": "empty_data", "warnings": []},
        "fina_indicator": _ok("tushare.fina_indicator", [{"end_date": "20251231", "update_flag": "1"}]),
    })
    result = verify_command("financials", "600036", [{
        "REPORT_DATE": "2025-12-31", "TOTALOPERATEREVE": 100,
    }], client=client)
    self.assertEqual(result["status"], "MATCH")
    self.assertIn("balancesheet: permission_denied", result["warnings"])
```

Run: `python3 -m unittest tests.test_ashare_plugin_tushare_verification -v`

Expected: PASS；成功字段保持 `MATCH`，失败 endpoint 单独列警告。

---

### Task 5: 将验证层接入八个 CLI 命令且保持 fail-open

**Files:**
- Modify: `tools/ashare_data.py:16-33, 129-161, 229-539`
- Modify: `tools/ashare_plugin/quote.py:13-38`
- Modify: `tests/test_ashare_data.py:29-47, 208-390`
- Modify: `tests/test_ashare_plugin_quote.py`

**Interfaces:**
- Consumes: `verify_command`。
- Produces: `_safe_verification -> Dict[str, Any]` 与 `_print_verification -> None`；所有成功命令打印 `Tushare 验证: <status>`，主返回值仍为 `True`/`False`。

- [ ] **Step 1: 冻结腾讯行情时间字段**

把两个 quote 测试 fixture 的 `fields[30]` 设为 `"20260718150000"`，并断言两个解析器返回：

```python
self.assertEqual(parsed["quote_time"], "20260718150000")
```

Run: `python3 -m unittest tests.test_ashare_plugin_quote tests.test_ashare_data -v`

Expected: FAIL，当前解析结果没有 `quote_time`。

- [ ] **Step 2: 在两个腾讯解析器中加入同一字段**

在 `tools/ashare_data.py::_parse_qq_quote` 与 `tools/ashare_plugin/quote.py::parse_tencent_quote` 的返回字典中加入：

```python
"quote_time": fields[30] if len(fields) > 30 else "",
```

Run: `python3 -m unittest tests.test_ashare_plugin_quote tests.test_ashare_data -v`

Expected: PASS。

- [ ] **Step 3: 写 CLI 未配置、匹配、异常和退出码测试**

`tests/test_ashare_data.py` 增加：

```python
class TestTushareCliVerification(unittest.TestCase):
    @mock.patch.object(ashare_data, "verify_command")
    def test_successful_quote_prints_verification_without_changing_success(self, verify):
        verify.return_value = {
            "provider": "tushare", "configured": True, "status": "MATCH",
            "as_of": "2026-07-19T00:00:00+00:00", "warnings": [],
            "fields": [{"field": "pb", "status": "MATCH"}], "endpoints": [],
        }
        with mock.patch.object(ashare_data, "_curl", return_value=_quote_raw()), \
                mock.patch.object(ashare_data, "_fetch_52w", return_value=("12", "8")), \
                redirect_stdout(StringIO()) as output:
            ok = ashare_data.cmd_quote("600036")
        self.assertTrue(ok)
        self.assertIn("Tushare 验证: MATCH", output.getvalue())

    @mock.patch.object(ashare_data, "verify_command", side_effect=RuntimeError("hidden"))
    def test_verification_exception_is_visible_but_does_not_fail_primary(self, _verify):
        with mock.patch.object(ashare_data, "_curl", return_value=_quote_raw()), \
                mock.patch.object(ashare_data, "_fetch_52w", return_value=("12", "8")), \
                redirect_stdout(StringIO()) as output:
            ok = ashare_data.cmd_quote("600036")
        self.assertTrue(ok)
        self.assertIn("Tushare 验证: INSUFFICIENT", output.getvalue())
        self.assertNotIn("hidden", output.getvalue())

    @mock.patch.object(ashare_data, "verify_command")
    def test_not_configured_is_explicit(self, verify):
        verify.return_value = {
            "provider": "tushare", "configured": False,
            "status": "NOT_CONFIGURED", "as_of": None,
            "warnings": ["未配置 TUSHARE_TOKEN；未发起 Tushare 请求"],
            "fields": [], "endpoints": [],
        }
        with mock.patch.object(ashare_data, "_curl", return_value=_quote_raw()), \
                mock.patch.object(ashare_data, "_fetch_52w", return_value=("12", "8")), \
                redirect_stdout(StringIO()) as output:
            ok = ashare_data.cmd_quote("600036")
        self.assertTrue(ok)
        self.assertIn("NOT_CONFIGURED", output.getvalue())
```

- [ ] **Step 4: 实现安全调用和摘要打印助手**

在两组 direct/package imports 中导入 `verify_command`，并新增：

```python
def _safe_verification(command, subject, primary_data, *, trade_date=None):
    try:
        return verify_command(
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
```

- [ ] **Step 5: 在八个命令的主数据打印完成后调用同一助手**

逐个命令使用下列精确调用；只有主结果成功后执行：

```python
_print_verification(_safe_verification("quote", code, d))
_print_verification(_safe_verification("valuation", code, d))
_print_verification(_safe_verification("financials", code, reports[:5]))
_print_verification(_safe_verification("history", code, reports))
_print_verification(_safe_verification("equity-history", code, rows))
_print_verification(_safe_verification("search", keyword, results))
_print_verification(
    _safe_verification("signals", code, result["data"], trade_date=trade_date)
)
_print_verification(
    _safe_verification("announcements", code, result)
)
```

每个调用均放在原 `return True` 前；任何原 `return False` 路径不变。

- [ ] **Step 6: 参数化验证八个路由名都从 CLI 发出**

在现有各成功测试中 patch `ashare_data.verify_command` 为 `NOT_CONFIGURED` 结果，并逐个断言 `call_args.args[0]` 等于对应命令名。`signals` 额外断言 `trade_date` 被原样传入，`search` 断言 subject 是关键词而非猜测代码。

Run: `python3 -m unittest tests.test_ashare_data -v`

Expected: PASS；八个命令成功语义不变，验证异常不泄露异常文本。

- [ ] **Step 7: 验证 CLI help 与退出码没有变化**

Run: `python3 tools/ashare_data.py --help`

Expected: 仍列出 `quote financials valuation search history equity-history signals announcements`，没有新增参数。

Run: `python3 tools/ashare_data.py history 600036 --years 0`

Expected: exit 2，stderr 含 `--years 必须在 1 到 50 之间`。

---

### Task 6: 将 Tushare 状态映射进既有全量分析证据契约

**Files:**
- Modify: `tests/test_full_analysis_gate.py:965-995`
- Modify: `skills/full-company-analysis.md`
- Do not modify: `tools/full_analysis_gate.py`
- Do not modify: `tools/full_analysis_contract.json`

**Interfaces:**
- Consumes: gate 现有 fact source 字段 `publisher_id`、`acquisition_chain_id`、`observed_value`、`period`、`unit`。
- Produces: `MATCH` 可记录为第二独立 source；`CONFLICT` 由现有 `classify_fact` 算为 `CONFLICT`；`INSUFFICIENT`/`NOT_CONFIGURED` 只进入 limitation，不伪造第二源。

- [ ] **Step 1: 增加 Tushare 独立链与冲突回归测试**

```python
def test_tushare_match_is_an_independent_second_chain(self):
    fact = _fact("f-tushare", [
        _src("东方财富", "eastmoney-http", "100"),
        _src("Tushare", "tushare-api", "100.5"),
    ], value="100", tol="1")
    self.assertEqual(gate_mod.classify_fact(fact), "DUAL_SOURCE")

def test_tushare_conflict_remains_fail_closed(self):
    fact = _fact("f-tushare-conflict", [
        _src("东方财富", "eastmoney-http", "100"),
        _src("Tushare", "tushare-api", "102"),
    ], value="100", tol="1")
    self.assertEqual(gate_mod.classify_fact(fact), "CONFLICT")
```

- [ ] **Step 2: 运行 gate 定向测试**

Run: `python3 -m unittest tests.test_full_analysis_gate -v`

Expected: PASS，证明现有 gate 已满足新来源分类，无需 schema 变更。

- [ ] **Step 3: 在总控 Skill 固化翻译规则**

在 `skills/full-company-analysis.md` 的数据证据阶段加入以下规则：

```markdown
### Tushare 可选验证状态映射

- `MATCH`：仅当 subject、period、unit 一致时，可把 Tushare 记录为第二个 source；`publisher_id=Tushare`、`acquisition_chain_id=tushare-api`，不得复制东方财富获取链 ID。
- `CONFLICT`：同时记录两侧 `observed_value`，相关 fact 必须由 gate 计算为 `CONFLICT`，完成第三源或原始披露仲裁前不得写成已验证结论。
- `INSUFFICIENT`：只在 limitations 中记录接口、期间、单位或权限缺口，不添加空 source。
- `NOT_CONFIGURED`：记录 `tushare_not_configured` limitation，不得声称 Tushare 双源核验完成。
```

- [ ] **Step 4: 检查注册表仍为 20 项且 schema 未漂移**

Run: `python3 scripts/check-full-analysis-contract.py`

Expected: PASS；Tushare 未成为第 21 个业务 Skill，manifest schema 版本不变。

---

### Task 7: 更新 canonical Skills 并同步 Claude/Codex 环境

**Files:**
- Modify: `skills/ashare-data.md`
- Modify: `skills/financial-data.md`
- Modify: `skills/full-company-analysis.md`（Task 6 已改）
- Generate: `codex-skills/ashare-data/SKILL.md`
- Generate: `codex-skills/financial-data/SKILL.md`
- Generate: `codex-skills/full-company-analysis/SKILL.md`
- Check: `codex-prompts/ashare-data.md`
- Check: `codex-prompts/financial-data.md`
- Check: `codex-prompts/full-company-analysis.md`

**Interfaces:**
- Consumes: Task 1-6 的真实行为和状态名。
- Produces: 两个平台同一触发语义、同一安全边界、同一失败解释。

- [ ] **Step 1: 更新 `ashare-data` 的环境与输出规则**

加入以下章节，并把“本管线不提供第二源”的旧表述改成条件化表述：

```markdown
## Tushare 可选交叉验证

- 工具只检查 `TUSHARE_TOKEN` 是否存在；不要在对话、命令参数、报告或仓库中粘贴 token。
- 未配置时输出 `NOT_CONFIGURED` 且不发起 Tushare 请求；现有主数据流程和退出码不受影响。
- 已配置时，八个命令自动附加 Tushare 验证摘要：`MATCH`、`CONFLICT` 或 `INSUFFICIENT`。
- Tushare 是验证源，不是腾讯/东方财富/巨潮的 fallback；接口无权限、限流或空数据时必须逐项说明。
- 实时腾讯行情只与同一交易日已更新的 Tushare 日终数据比较；日期不同必须为 `INSUFFICIENT`。
- `MATCH` 只代表对应字段在相同标的、期间、单位和报告版本下偏差不超过 1%，不代表整份报告自动完成双源核验。
```

- [ ] **Step 2: 更新 `financial-data` 的来源优先级与冲突规则**

A 股来源表改为“东方财富主数据、巨潮原始披露、Tushare 可选结构化验证”，并加入：

```markdown
### A 股 Tushare 验证边界

Tushare 可作为独立获取链验证结构化字段，但不能替代巨潮原始财报。只有 `MATCH` 字段可以计入双源事实；`CONFLICT` 必须保留两值并查第三源/原始披露；`INSUFFICIENT` 和 `NOT_CONFIGURED` 均不得计作第二来源。财务比较必须匹配主体、报告期、单位、合并口径与修订版。
```

- [ ] **Step 3: 运行生成脚本**

Run: `python3 scripts/sync-codex-skills.py`

Expected: 生成物更新；至少上述三个 Codex Skill 与 canonical 文本一致。

Run: `python3 scripts/sync-codex-prompts.py`

Expected: 成功；兼容提示词生成规则无错误。

- [ ] **Step 4: 检查生成物没有漂移**

Run: `python3 scripts/sync-codex-skills.py --check`

Expected: exit 0，输出包含 `Checked` 与 `Codex skills`。

Run: `python3 scripts/sync-codex-prompts.py --check`

Expected: exit 0，输出包含 `Checked` 与 `Codex prompts`。

- [ ] **Step 5: 以唯一备份目录安装三项受影响的本地命令/Skill**

不调用会删除上一代备份的安装脚本。先生成唯一时间戳，把六个已有目标移动到本地备份，再复制本次生成物：

```bash
stamp="$(date +%Y%m%d-%H%M%S)-$$"
backup="local/skill-install-backups/$stamp"
mkdir -p "$backup/claude" "$backup/codex" "$HOME/.claude/commands" "${CODEX_HOME:-$HOME/.codex}/skills"
for name in ashare-data financial-data full-company-analysis; do
  if [ -e "$HOME/.claude/commands/$name.md" ]; then
    mv "$HOME/.claude/commands/$name.md" "$backup/claude/$name.md"
  fi
  if [ -e "${CODEX_HOME:-$HOME/.codex}/skills/$name" ]; then
    mv "${CODEX_HOME:-$HOME/.codex}/skills/$name" "$backup/codex/$name"
  fi
  cp "skills/$name.md" "$HOME/.claude/commands/$name.md"
  cp -R "codex-skills/$name" "${CODEX_HOME:-$HOME/.codex}/skills/$name"
done
```

Expected: exit 0，只更新三项；旧版本完整保存在唯一的 `local/skill-install-backups/<timestamp>/`。Codex 需要重启后重新加载更新后的 Skill。

- [ ] **Step 6: 比较本地 Claude/Codex 关键规则**

Run:

```bash
rg -n "Tushare|NOT_CONFIGURED|CONFLICT|TUSHARE_TOKEN" \
  "$HOME/.claude/commands/ashare-data.md" \
  "$HOME/.claude/commands/financial-data.md" \
  "$HOME/.claude/commands/full-company-analysis.md" \
  "${CODEX_HOME:-$HOME/.codex}/skills/ashare-data/SKILL.md" \
  "${CODEX_HOME:-$HOME/.codex}/skills/financial-data/SKILL.md" \
  "${CODEX_HOME:-$HOME/.codex}/skills/full-company-analysis/SKILL.md"
```

Expected: 两个平台均出现相同四态、token 安全和冲突处置规则；命令只搜索规则文字，不读取任何 `.env` 或 token 值。

---

### Task 8: 全仓验证与有限真实调用

**Files:**
- Verify: `tests/`、生成物和现有报告索引
- Create only when token is locally present: `local/筛选公司/招商银行/tushare-validation-2026-07-19.md`

**Interfaces:**
- Consumes: 完成后的八命令实现和用户本地 `TUSHARE_TOKEN`。
- Produces: 离线全绿证据；若 token 存在，再产出不含凭据的真实接口可用性报告。

- [ ] **Step 1: 运行所有 Tushare 与 CLI 定向测试**

Run:

```bash
python3 -m unittest \
  tests.test_ashare_plugin_transport \
  tests.test_ashare_plugin_tushare \
  tests.test_ashare_plugin_tushare_verification \
  tests.test_ashare_plugin_quote \
  tests.test_ashare_data \
  tests.test_full_analysis_gate -v
```

Expected: PASS，无网络依赖。

- [ ] **Step 2: 运行统一仓库检查**

Run: `bash scripts/check.sh`

Expected: exit 0，单元测试、Codex Skills、Codex prompts、报告索引和全量分析注册表全部通过。

- [ ] **Step 3: 用清空后的子进程环境证明未配置时调用数为零**

在 `tests/test_ashare_plugin_tushare.py` 的未配置测试已用清空环境的 `patch.dict` 和 fake transport 调用计数证明；再次单独运行：

Run: `python3 -m unittest tests.test_ashare_plugin_tushare.TestTushareClient.test_missing_token_returns_not_configured_without_transport_call -v`

Expected: PASS，transport calls 等于空列表。

- [ ] **Step 4: 只检查 token 是否存在**

Run:

```bash
python3 -c 'import os; print("TUSHARE_TOKEN: present" if os.environ.get("TUSHARE_TOKEN") else "MISSING: TUSHARE_TOKEN")'
```

Expected: 仅输出 `TUSHARE_TOKEN: present` 或 `MISSING: TUSHARE_TOKEN`，不输出长度或任何字符。

- [ ] **Step 5: token 缺失时明确结束真实验证阶段**

若输出为 `MISSING: TUSHARE_TOKEN`，记录“实现和离线验证完成；真实 Tushare 权限/字段覆盖未验证”，不创建伪真实报告，也不要求用户在聊天中提供 key。

- [ ] **Step 6: token 存在时对招商银行执行八个有限真实调用**

Run:

```bash
python3 tools/ashare_data.py quote 600036
python3 tools/ashare_data.py valuation 600036
python3 tools/ashare_data.py financials 600036
python3 tools/ashare_data.py history 600036 --years 10
python3 tools/ashare_data.py equity-history 600036
python3 tools/ashare_data.py search 招商银行
python3 tools/ashare_data.py signals 600036
python3 tools/ashare_data.py announcements 600036 --limit 20
```

Expected: 每个主命令保持原退出语义，并出现 `Tushare 验证` 摘要；权限不足或空数据按 endpoint 显示，不能静默丢弃。

- [ ] **Step 7: 写本地真实验证记录并做 secret 扫描**

报告只包含：数据截止日、命令、主退出码、Tushare endpoint 状态、可比较字段数、MATCH/CONFLICT/INSUFFICIENT 数量、权限缺口、期间/单位限制和“仅供学习研究”。不得写请求体、环境内容或 token。

Run:

```bash
rg -n "token|api[_ -]?key|TUSHARE_TOKEN=" \
  local/筛选公司/招商银行/tushare-validation-2026-07-19.md
```

Expected: 无敏感赋值或值；允许正文出现不含取值的字段名 `TUSHARE_TOKEN`，若扫描命中只允许“present/MISSING”状态描述。

- [ ] **Step 8: 最终完成判定**

只有以下条件同时成立才声明完成：

1. `bash scripts/check.sh` exit 0；
2. 未配置路径证明零请求；
3. token 哨兵不可在 argv/输出/异常中观察；
4. 八命令主参数和退出码无回归；
5. Claude/Codex 三项 Skill 已同步安装；
6. token 存在时真实报告完成；token 缺失时明确标记真实验证未执行；
7. 未执行任何 Git 或外部写入操作；唯一外部请求是用户批准后的 Tushare 只读 POST 查询。
