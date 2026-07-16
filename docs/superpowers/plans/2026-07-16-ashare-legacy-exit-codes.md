# A 股旧命令退出码修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `quote`、`financials`、`valuation`、`search` 在成功时返回 0，无数据或已知请求失败时返回 1。

**Architecture:** 四个命令函数显式返回 `True` 或 `False`，复用现有 `main()` 的 `outcome is False -> sys.exit(1)` 映射。测试直接注入固定行情/财务/搜索响应，并单独验证 CLI 映射，不依赖网络。

**Tech Stack:** Python 3.8+ 标准库、`unittest`、`unittest.mock`。

## Global Constraints

- 只修改 `tools/ashare_data.py` 与 `tests/test_ashare_data.py`。
- 不改变成功输出、命令参数、数据源或现有新命令行为。
- 不修改报告、README、skills、CI 或用户未提交文件。
- 不执行 push、PR 或其他外部写入。

---

### Task 1: 统一旧命令成功与失败返回值

**Files:**
- Modify: `tests/test_ashare_data.py`
- Modify: `tools/ashare_data.py:229-469`

**Interfaces:**
- Consumes: `_curl`、`_curl_json`、`_parse_qq_quote`、`main()` 现有退出码映射。
- Produces: `cmd_quote`、`cmd_financials`、`cmd_valuation`、`cmd_search` 均返回 `bool`。

- [ ] **Step 1: 写失败与成功路径测试**

在 `tests/test_ashare_data.py` 增加行情响应构造器：

```python
def _quote_raw():
    fields = [""] * 50
    fields[1] = "样本公司"
    fields[2] = "600036"
    fields[3] = "10.00"
    fields[4] = "9.90"
    fields[5] = "9.95"
    fields[6] = "100"
    fields[31] = "0.10"
    fields[32] = "1.01"
    fields[33] = "10.10"
    fields[34] = "9.80"
    fields[37] = "1000"
    fields[38] = "1.20"
    fields[39] = "8.00"
    fields[44] = "80.00"
    fields[45] = "100.00"
    fields[46] = "1.10"
    return 'v_sh600036="' + "~".join(fields) + '";'
```

增加无数据测试：

```python
class TestLegacyCommandExitSemantics(unittest.TestCase):
    @mock.patch.object(ashare_data, "_curl", return_value='v_none="";')
    def test_quote_and_valuation_return_false_without_quote(self, _curl):
        with redirect_stdout(StringIO()):
            self.assertFalse(ashare_data.cmd_quote("INVALID"))
            self.assertFalse(ashare_data.cmd_valuation("INVALID"))

    @mock.patch.object(ashare_data, "_curl_json")
    @mock.patch.object(ashare_data, "_curl", return_value='v_none="";')
    def test_financials_returns_false_without_reports(self, _curl, curl_json):
        curl_json.return_value = {"success": True, "result": {"data": []}}
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertFalse(ashare_data.cmd_financials("600036"))
        self.assertEqual(curl_json.call_count, 2)

    @mock.patch.object(ashare_data, "_curl_json")
    def test_search_returns_false_without_results(self, curl_json):
        curl_json.return_value = {"QuotationCodeTable": {"Data": []}}
        with redirect_stdout(StringIO()):
            self.assertFalse(ashare_data.cmd_search("不存在"))
```

增加成功测试：

```python
    @mock.patch.object(ashare_data, "_fetch_52w", return_value=("12", "8"))
    @mock.patch.object(ashare_data, "_curl", return_value=_quote_raw())
    def test_quote_and_valuation_return_true_with_quote(self, _curl, _fetch):
        with redirect_stdout(StringIO()):
            self.assertTrue(ashare_data.cmd_quote("600036"))
            self.assertTrue(ashare_data.cmd_valuation("600036"))

    @mock.patch.object(ashare_data, "_curl_json")
    @mock.patch.object(ashare_data, "_curl", return_value='v_none="";')
    def test_financials_returns_true_with_report(self, _curl, curl_json):
        curl_json.return_value = {"success": True, "result": {"data": [{
            "REPORT_DATE": "2025-12-31", "REPORT_DATE_NAME": "2025年报",
            "TOTALOPERATEREVE": 100000000, "PARENTNETPROFIT": 10000000,
            "EPSJB": 1.0, "BPS": 5.0, "ROEJQ": 10.0,
        }]}}
        with redirect_stdout(StringIO()):
            self.assertTrue(ashare_data.cmd_financials("600036"))

    @mock.patch.object(ashare_data, "_curl_json")
    def test_search_returns_true_with_results(self, curl_json):
        curl_json.return_value = {"QuotationCodeTable": {"Data": [{
            "Code": "600036", "Name": "招商银行", "MktNum": "1",
        }]}}
        with redirect_stdout(StringIO()):
            self.assertTrue(ashare_data.cmd_search("招商银行"))
```

增加请求错误和入口映射测试：

```python
    @mock.patch.object(ashare_data, "_curl", side_effect=ConnectionError("offline"))
    def test_quote_request_error_returns_false_without_traceback(self, _curl):
        with redirect_stderr(StringIO()) as error:
            self.assertFalse(ashare_data.cmd_quote("600036"))
        self.assertIn("offline", error.getvalue())

    @mock.patch.object(ashare_data, "_curl_json",
                       side_effect=ConnectionError("offline"))
    @mock.patch.object(ashare_data, "_curl",
                       side_effect=ConnectionError("offline"))
    def test_financials_request_errors_return_false(self, _curl, _curl_json):
        with redirect_stderr(StringIO()) as error:
            self.assertFalse(ashare_data.cmd_financials("600036"))
        self.assertIn("财务数据", error.getvalue())

    def test_main_maps_false_to_exit_one(self):
        with mock.patch.object(sys, "argv", [TOOL, "quote", "600036"]), \
                mock.patch.object(ashare_data, "cmd_quote", return_value=False):
            with self.assertRaises(SystemExit) as raised:
                ashare_data.main()
        self.assertEqual(raised.exception.code, 1)

    def test_main_keeps_success_at_zero(self):
        with mock.patch.object(sys, "argv", [TOOL, "quote", "600036"]), \
                mock.patch.object(ashare_data, "cmd_quote", return_value=True):
            ashare_data.main()
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `python3 tests/test_ashare_data.py -v`

Expected: 失败断言显示旧命令返回 `None`；请求错误测试显示 `ConnectionError` 未被命令函数处理。

- [ ] **Step 3: 实现最小 bool 返回与已知错误处理**

`cmd_quote` 和 `cmd_valuation` 的请求改为：

```python
try:
    raw = _curl(f"https://qt.gtimg.cn/q={qq_code}")
except (ConnectionError, subprocess.TimeoutExpired) as exc:
    print(f"❌ 获取行情失败: {exc}", file=sys.stderr)
    return False
d = _parse_qq_quote(raw)
if not d:
    print(f"❌ 未找到股票 {code}", file=sys.stderr)
    return False
# 保持原输出
return True
```

`cmd_financials` 保留两次查询，但将两处解析统一为：

```python
try:
    raw = _curl(f"https://qt.gtimg.cn/q={qq_code}")
    d = _parse_qq_quote(raw)
except (ConnectionError, subprocess.TimeoutExpired):
    d = {}
name = d.get("name", code) if d else code

try:
    data = _curl_json(fin_url, params)
    reports = (data.get("result") or {}).get("data") or []
except (ConnectionError, json.JSONDecodeError, subprocess.TimeoutExpired):
    reports = []
```

两次查询后仍无数据时向 stderr 输出并 `return False`；打印完报告后 `return True`。

`cmd_search` 改为：

```python
try:
    data = _curl_json(url, params)
except (ConnectionError, json.JSONDecodeError,
        subprocess.TimeoutExpired) as exc:
    print(f"❌ 搜索股票失败: {exc}", file=sys.stderr)
    return False
results = (data.get("QuotationCodeTable") or {}).get("Data") or []
if not results:
    print(f"❌ 未找到匹配 '{keyword}' 的股票", file=sys.stderr)
    return False
# 保持原输出
return True
```

- [ ] **Step 4: 运行定向与完整验证**

Run: `python3 tests/test_ashare_data.py -v`

Expected: 全部 A 股测试通过。

Run: `bash scripts/check.sh`

Expected: 全部单元测试、19 个 skills、19 个 prompts 和报告索引检查通过。

- [ ] **Step 5: 检查范围并提交**

```bash
git diff --check
git add tests/test_ashare_data.py tools/ashare_data.py
git commit -m "fix: 统一A股旧命令失败退出语义"
```

确认报告、`reports/INDEX.md`、`tools/star_history_chart.py` 和远端状态未被修改。
