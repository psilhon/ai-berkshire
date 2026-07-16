# Berkshire A 股数据插件 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Berkshire 现有 A 股工具升级为可复用、可测试、可降级的数据插件，覆盖行情、财务、公告、龙虎榜/资金流/解禁/融资融券和备用源，同时保持现有 CLI 兼容。

**Architecture:** `tools/ashare_plugin/` 提供标准库实现的 transport、代码归一化、统一结果契约和按能力拆分的 provider；`tools/ashare_data.py` 只保留 CLI 适配。主源失败时由 service 层按固定策略调用兼容备胎，并将来源、时间、警告和错误显式返回。

**Tech Stack:** Python 3.8+、Python 标准库、`/usr/bin/curl`、`unittest`、现有 Berkshire `scripts/check.sh`。

## Global Constraints

- 不新增外部 Python 运行时依赖。
- 所有网络请求必须经过 `tools/ashare_plugin/transport.py`。
- 测试不得依赖实时网络。
- 失败不得静默返回空数据；CLI 失败必须使用非零退出码。
- 不关闭 HTTPS 证书校验。
- 保留现有 `quote`、`financials`、`valuation`、`search`、`history`、`equity-history` 命令。
- 不修改无关报告、生成物和用户已有工作树改动。

---

### Task 1: 建立统一结果契约和代码归一化

**Files:**
- Create: `tools/ashare_plugin/__init__.py`
- Create: `tools/ashare_plugin/errors.py`
- Create: `tools/ashare_plugin/identifiers.py`
- Test: `tests/test_ashare_plugin_core.py`

**Interfaces:**
- Produces `DataResult`, `success_result()`, `failure_result()`。
- Produces `normalize_code(code) -> CodeIdentity`，其中 `CodeIdentity` 至少包含 `code`、`market`、`secu_code`、`secid`、`quote_code`。

- [ ] **Step 1: Write the failing tests**

```python
def test_normalize_code_infers_markets_and_provider_formats():
    identity = normalize_code("600519")
    assert identity.code == "600519"
    assert identity.market == "SH"
    assert identity.secu_code == "600519.SH"
    assert identity.secid == "1.600519"
    assert identity.quote_code == "sh600519"

def test_invalid_code_returns_typed_error():
    with pytest.raises(InvalidCodeError):
        normalize_code("123")

def test_failure_result_is_explicit_and_serializable():
    result = failure_result("eastmoney", "rate_limited", "blocked")
    assert result["ok"] is False
    assert result["data"] is None
    assert result["error_type"] == "rate_limited"
```

Use `unittest` syntax matching the existing test suite; the assertions above define the behavior, not a requirement to add pytest.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python3 -m unittest tests.test_ashare_plugin_core -v`

Expected: FAIL because `tools.ashare_plugin` does not exist.

- [ ] **Step 3: Implement the minimal contract**

Define a `TypedDict`-compatible result shape, an `InvalidCodeError`, a frozen `CodeIdentity` dataclass, and explicit mapping for SH/SZ/BJ. Accept six-digit codes and `.SH`/`.SZ`/`.BJ` suffixes only.

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `python3 -m unittest tests.test_ashare_plugin_core -v`

Expected: PASS.

### Task 2: 抽取安全 transport 层

**Files:**
- Create: `tools/ashare_plugin/transport.py`
- Modify: `tools/ashare_plugin/errors.py`
- Test: `tests/test_ashare_plugin_transport.py`

**Interfaces:**
- Produces `TransportClient.get_json(url, params=None, headers=None) -> object`。
- Produces `TransportError` subclasses或带 `error_type` 的统一异常。
- Produces `FallbackChain(attempts).run()`，按顺序尝试 provider，并保留失败警告。

- [ ] **Step 1: Write failing tests**

```python
def test_transport_builds_query_and_uses_curl_without_proxy():
    client = TransportClient(runner=fake_runner_returning_json)
    data = client.get_json("https://example.test/api", {"code": "600519"})
    assert data == {"ok": True}
    assert "--noproxy" in fake_runner_returning_json.last_args

def test_transport_classifies_timeout():
    client = TransportClient(runner=fake_runner_timeout)
    with self.assertRaises(TransportError) as ctx:
        client.get_json("https://example.test/api")
    self.assertEqual(ctx.exception.error_type, "timeout")

def test_fallback_chain_returns_source_and_warning():
    result = FallbackChain([failed_provider, backup_provider]).run()
    self.assertTrue(result["ok"])
    self.assertEqual(result["source"], "backup")
    self.assertTrue(result["fallback_used"])
    self.assertEqual(len(result["warnings"]), 1)
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_ashare_plugin_transport -v`

Expected: FAIL because transport classes do not exist.

- [ ] **Step 3: Implement minimal transport**

Use `/usr/bin/curl`, `--noproxy '*'`, a fixed timeout, `urllib.parse.urlencode`, and JSON decoding. Do not disable certificate verification. Retry only transient transport failures a bounded number of times; do not retry 403. Make the runner injectable for tests.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python3 -m unittest tests.test_ashare_plugin_transport -v`

Expected: PASS.

### Task 3: 迁移行情与财务 provider

**Files:**
- Create: `tools/ashare_plugin/quote.py`
- Create: `tools/ashare_plugin/fundamentals.py`
- Modify: `tools/ashare_data.py`
- Test: `tests/test_ashare_plugin_quote.py`
- Test: `tests/test_ashare_plugin_fundamentals.py`

**Interfaces:**
- `fetch_quote(code, client=None) -> DataResult`
- `fetch_financials(code, years=5, client=None) -> DataResult`
- `fetch_history(code, years=10, client=None) -> DataResult`
- `fetch_equity_history(code, client=None) -> DataResult`

- [ ] **Step 1: Write failing parser and provider tests**

Cover Tencent GBK quote parsing, malformed quote response, datacenter pagination, API failure, and the existing `history`/`equity-history` output requirements.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_ashare_plugin_quote tests.test_ashare_plugin_fundamentals -v`

Expected: FAIL because provider modules do not exist.

- [ ] **Step 3: Move the existing behavior behind provider interfaces**

Reuse proven parsing and endpoint parameters from `tools/ashare_data.py`; replace direct `_curl` helpers with `TransportClient`. Preserve output field names and pagination behavior. Return `DataResult` internally.

- [ ] **Step 4: Adapt the existing CLI commands**

Make the six existing commands call provider functions and translate failed `DataResult` values into readable stderr plus exit code `1`; preserve argument validation exit code `2`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `python3 -m unittest tests.test_ashare_plugin_quote tests.test_ashare_plugin_fundamentals tests.test_ashare_data -v`

Expected: PASS.

### Task 4: 实现公告 provider 与备用源

**Files:**
- Create: `tools/ashare_plugin/disclosures.py`
- Modify: `tools/ashare_data.py`
- Test: `tests/test_ashare_plugin_disclosures.py`

**Interfaces:**
- `fetch_announcements(code, limit=20, client=None) -> DataResult`
- `fetch_official_announcements(code, limit=20, client=None) -> DataResult`

- [ ] **Step 1: Write failing tests**

Test the CNINFO response normalization, SZSE fallback for Shenzhen codes, Eastmoney PDF fallback for Shanghai codes, and all-source failure returning `all_sources_failed` rather than `[]`.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_ashare_plugin_disclosures -v`

Expected: FAIL because disclosure provider does not exist.

- [ ] **Step 3: Implement primary and fallback paths**

Use the existing dynamic CNINFO stock-to-org mapping logic where applicable. Keep `pdf`, `title`, `date`, `type`, and `source` fields stable. Try the exchange backup only when the primary call has a typed failure or empty response.

- [ ] **Step 4: Add the `announcements` CLI command**

Use `--limit` validation, print source and fallback status, and return nonzero on `all_sources_failed`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `python3 -m unittest tests.test_ashare_plugin_disclosures -v`

Expected: PASS.

### Task 5: 实现市场信号 provider 与备用源

**Files:**
- Create: `tools/ashare_plugin/market_signals.py`
- Modify: `tools/ashare_data.py`
- Test: `tests/test_ashare_plugin_signals.py`

**Interfaces:**
- `fetch_signals(code, trade_date=None, client=None) -> DataResult`
- `fetch_dragon_tiger(code, trade_date=None, client=None) -> DataResult`
- `fetch_fund_flow(code, days=60, client=None) -> DataResult`
- `fetch_lockup(code, trade_date=None, forward_days=90, client=None) -> DataResult`
- `fetch_margin(code, client=None) -> DataResult`

- [ ] **Step 1: Write failing tests**

Test field normalization for primary data, fallback from Eastmoney to SSE/SZSE or Sina, explicit units in normalized output, and fail-closed behavior when both primary and backup fail.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_ashare_plugin_signals -v`

Expected: FAIL because signal provider does not exist.

- [ ] **Step 3: Implement primary endpoints**

Use Eastmoney datacenter/push2 queries through `TransportClient`, preserving explicit page sizes and converting numeric fields without silently turning malformed values into zero.

- [ ] **Step 4: Implement only the confirmed backups**

Use official exchange endpoints for Dragon Tiger Board and Sina for daily fund flow. Include `source`, `fallback_used`, and warnings in every result.

- [ ] **Step 5: Add the `signals` CLI command**

Print a concise research-facing summary, never a raw API payload. Return nonzero for invalid code or all-source failure.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run: `python3 -m unittest tests.test_ashare_plugin_signals -v`

Expected: PASS.

### Task 6: 接入估值与研究规范

**Files:**
- Modify: `tools/ashare_data.py`
- Modify: `skills/financial-data.md`
- Modify: `README.md`
- Test: `tests/test_ashare_data.py`

**Interfaces:**
- Existing `valuation` output remains compatible.
- Documentation names the plugin as an optional A-share data provider and explains primary/backup source labels.

- [ ] **Step 1: Write failing compatibility tests**

Add tests that `valuation` still emits current price, PE, PB, market cap and source metadata, while missing forecast data is reported as insufficient rather than inferred.

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_ashare_data -v`

Expected: FAIL for the new source metadata/insufficient-data assertions.

- [ ] **Step 3: Implement minimal CLI and documentation integration**

Keep valuation formulas and financial cross-validation rules in Berkshire; do not copy `a-stock-data`'s fixed target PE or PEG judgment rules into the data plugin. Document that the plugin supplies evidence, not an investment verdict.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python3 -m unittest tests.test_ashare_data -v`

Expected: PASS.

### Task 7: 全量验证与手工 smoke test 说明

**Files:**
- Modify: `scripts/check.sh` only if the new tests need explicit discovery support
- Modify: `README.md` if command examples need completion
- Test: all files under `tests/`

- [ ] **Step 1: Run the full unit test suite**

Run: `python3 -m unittest discover -s tests`

Expected: all tests pass with zero failures and errors.

- [ ] **Step 2: Run the repository check**

Run: `bash scripts/check.sh`

Expected: unit tests, generated artifact sync checks and report index check all pass.

- [ ] **Step 3: Run non-mutating CLI discovery checks**

Run:

```bash
python3 tools/ashare_data.py --help
python3 tools/ashare_data.py signals --help
python3 tools/ashare_data.py announcements --help
```

Expected: all commands exit `0` and show the expected options.

- [ ] **Step 4: Record external smoke test as optional**

Do not make external calls part of the required check. If run manually, record only endpoint success/failure and source selection, not raw response bodies.

