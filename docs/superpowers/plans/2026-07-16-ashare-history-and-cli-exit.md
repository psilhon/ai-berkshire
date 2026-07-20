# A 股历史数据命令与雪球退出码修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 A 股十年财务和历史股本纳入正式工具命令，并修正雪球在线模式缺少用户 ID 时的成功退出。

**Architecture:** 在现有 `tools/ashare_data.py` 内增加证券代码标准化、Datacenter 分页读取和两个展示命令，保持单文件零依赖边界。测试通过 mock 接口数据验证业务逻辑，通过子进程验证 CLI 退出码；权威 skill 修改后再同步 Codex 生成物。

**Tech Stack:** Python 3.8+ 标准库、`unittest`、`unittest.mock`、Bash 检查入口。

## Global Constraints

- 不新增 Python 外部依赖。
- 不改变现有 `quote`、`financials`、`valuation`、`search` 命令的用户可见行为。
- 不使用财务主表的 `TOTAL_SHARE` 作为历史股本。
- 不修改用户现有报告、`local/reports/INDEX.md` 或 `tools/star_history_chart.py`。
- 只提交本计划列出的文件，不执行 push、PR 或其他外部写入。

---

### Task 1: 修正雪球在线模式参数退出码

**Files:**
- Modify: `tests/test_xueqiu_scraper_cli.py`
- Modify: `tools/xueqiu_scraper.py:379-410`

**Interfaces:**
- Consumes: `run_cli(*args) -> subprocess.CompletedProcess`
- Produces: 在线模式缺少 `--user-id` 时返回 2；合法在线参数才进入 Playwright 导入。

- [ ] **Step 1: 写失败测试**

```python
def test_missing_user_id_exits_two_before_playwright_import(self):
    proc = run_cli()
    self.assertEqual(proc.returncode, 2)
    self.assertIn("--user-id", proc.stderr)
    self.assertNotIn("playwright", proc.stderr)

def test_run_path_exits_with_playwright_hint(self):
    proc = run_cli("--user-id", "1247347556")
    self.assertNotEqual(proc.returncode, 0)
    self.assertIn("playwright", proc.stderr)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `python3 tests/test_xueqiu_scraper_cli.py -v`

Expected: `test_missing_user_id_exits_two_before_playwright_import` 失败，因为当前代码先导入 Playwright。

- [ ] **Step 3: 最小实现参数校验**

```python
if not args.user_id:
    print("在线模式需要 --user-id", file=sys.stderr)
    sys.exit(2)

try:
    from playwright.async_api import async_playwright
except ImportError:
    sys.exit("缺少 playwright 依赖...")
```

该校验放在 `--from-cache` 分支之后、Playwright 导入之前，并删除原有打印后 `return` 的分支。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `python3 tests/test_xueqiu_scraper_cli.py -v`

Expected: 7 项测试全部通过。

- [ ] **Step 5: 提交本任务**

```bash
git add tests/test_xueqiu_scraper_cli.py tools/xueqiu_scraper.py
git commit -m "fix: 雪球在线模式参数错误返回非零状态"
```

### Task 2: 建立 A 股代码与分页读取边界

**Files:**
- Create: `tests/test_ashare_data.py`
- Modify: `tools/ashare_data.py:1-40,220-333`

**Interfaces:**
- Produces: `_em_secu_code(code: str) -> str`
- Produces: `_positive_years(text: str) -> int`
- Produces: `_fetch_datacenter_rows(report_type: str, secu_code: str, *, sort_column: str, sort_order: str = "-1", extra_filter: str = "", limit: Optional[int] = None) -> List[dict]`

- [ ] **Step 1: 写代码标准化、参数和分页失败测试**

```python
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ashare_data

TOOL = str(ROOT / "tools" / "ashare_data.py")

def run_cli(*args):
    return subprocess.run(
        [sys.executable, TOOL, *args],
        capture_output=True, text=True, timeout=10,
    )

class TestSecurityCode(unittest.TestCase):
    def test_normalizes_shenzhen_shanghai_and_beijing(self):
        self.assertEqual(ashare_data._em_secu_code("600036"), "600036.SH")
        self.assertEqual(ashare_data._em_secu_code("000001.SZ"), "000001.SZ")
        self.assertEqual(ashare_data._em_secu_code("430047"), "430047.BJ")

    def test_rejects_invalid_code(self):
        with self.assertRaises(ValueError):
            ashare_data._em_secu_code("ABC")

class TestDatacenterPagination(unittest.TestCase):
    @mock.patch.object(ashare_data, "_curl_json")
    def test_reads_all_pages_without_silent_truncation(self, curl_json):
        curl_json.side_effect = [
            {"success": True, "result": {"pages": 2, "data": [{"id": 1}]}},
            {"success": True, "result": {"pages": 2, "data": [{"id": 2}]}},
        ]
        rows = ashare_data._fetch_datacenter_rows(
            "REPORT", "600036.SH", sort_column="END_DATE"
        )
        self.assertEqual(rows, [{"id": 1}, {"id": 2}])
        self.assertEqual(curl_json.call_count, 2)

    @mock.patch.object(ashare_data, "_curl_json")
    def test_api_failure_is_loud(self, curl_json):
        curl_json.return_value = {"success": False, "message": "bad field"}
        with self.assertRaisesRegex(ConnectionError, "bad field"):
            ashare_data._fetch_datacenter_rows(
                "REPORT", "600036.SH", sort_column="END_DATE"
            )
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `python3 tests/test_ashare_data.py -v`

Expected: 因三个内部函数尚不存在而失败。

- [ ] **Step 3: 实现最小公共边界**

```python
def _em_secu_code(code: str) -> str:
    raw = code.strip().upper()
    parts = raw.rsplit(".", 1)
    code_clean = parts[0]
    if len(code_clean) != 6 or not code_clean.isdigit():
        raise ValueError(f"无效 A 股代码: {code}")
    if len(parts) == 2:
        market = parts[1]
        if market not in {"SH", "SZ", "BJ"}:
            raise ValueError(f"无效市场后缀: {market}")
    elif code_clean.startswith(("6", "9", "5")):
        market = "SH"
    elif code_clean.startswith(("4", "8")):
        market = "BJ"
    elif code_clean.startswith(("0", "1", "2", "3")):
        market = "SZ"
    else:
        raise ValueError(f"无法判断 A 股市场: {code}")
    return f"{code_clean}.{market}"

def _positive_years(text: str) -> int:
    value = int(text)
    if not 1 <= value <= 50:
        raise argparse.ArgumentTypeError("--years 必须在 1 到 50 之间")
    return value

def _fetch_datacenter_rows(report_type, secu_code, *, sort_column,
                           sort_order="-1", extra_filter="", limit=None):
    rows = []
    page = 1
    page_size = min(limit or 100, 100)
    while True:
        data = _curl_json(_DATACENTER_URL, {
            "type": report_type, "sty": "ALL",
            "filter": f'(SECUCODE="{secu_code}"){extra_filter}',
            "p": str(page), "ps": str(page_size),
            "sr": sort_order, "st": sort_column,
            "source": "HSF10", "client": "PC",
        })
        if not data.get("success"):
            raise ConnectionError(data.get("message") or "东方财富接口返回失败")
        result = data.get("result") or {}
        rows.extend(result.get("data") or [])
        pages = int(result.get("pages") or 1)
        if page >= pages or (limit is not None and len(rows) >= limit):
            return rows[:limit] if limit is not None else rows
        page += 1
```

- [ ] **Step 4: 运行公共边界测试并确认 GREEN**

Run: `python3 tests/test_ashare_data.py -v`

Expected: 代码标准化、参数范围、分页与接口失败测试全部通过。

- [ ] **Step 5: 提交本任务**

```bash
git add tests/test_ashare_data.py tools/ashare_data.py
git commit -m "feat: 增加A股数据分页读取边界"
```

### Task 3: 增加十年财务命令

**Files:**
- Modify: `tests/test_ashare_data.py`
- Modify: `tools/ashare_data.py`

**Interfaces:**
- Consumes: `_em_secu_code`、`_fetch_datacenter_rows`
- Produces: `cmd_history(code: str, years: int = 10) -> bool`
- Produces: CLI `history CODE --years N`

- [ ] **Step 1: 写 history 输出和 CLI 失败测试**

```python
class TestHistoryCommand(unittest.TestCase):
    @mock.patch.object(ashare_data, "_fetch_datacenter_rows")
    def test_outputs_auditable_metrics_without_total_share(self, fetch):
        fetch.return_value = [{
            "REPORT_YEAR": "2025", "SECURITY_NAME_ABBR": "样本公司",
            "ROEJQ": 12.3, "XSMLL": 45.6, "XSJLL": 18.9,
            "NCO_NETPROFIT": 1.2, "INTSTCOVRATE": 8.5,
            "NETCASH_OPERATE_PK": 1230000000,
            "TOTAL_SHARE": 999999999,
        }]
        with redirect_stdout(StringIO()) as output:
            ok = ashare_data.cmd_history("600036", 10)
        text = output.getvalue()
        self.assertTrue(ok)
        self.assertIn("2025", text)
        self.assertIn("ROE", text)
        self.assertIn("12.30%", text)
        self.assertIn("经营现金流", text)
        self.assertNotIn("999999999", text)
        self.assertIn('(REPORT_TYPE="年报")', fetch.call_args.kwargs["extra_filter"])

def test_history_rejects_years_outside_range(self):
    proc = run_cli("history", "600036", "--years", "0")
    self.assertEqual(proc.returncode, 2)
```

- [ ] **Step 2: 运行 history 测试并确认 RED**

Run: `python3 tests/test_ashare_data.py -v`

Expected: `cmd_history` 和 `history` 子命令不存在导致失败。

- [ ] **Step 3: 实现 history 命令**

```python
def _fmt_times(value) -> str:
    if value in (None, "", "-"):
        return "-"
    return f"{float(value):.2f}x"

def cmd_history(code: str, years: int = 10):
    secu_code = _em_secu_code(code)
    try:
        reports = _fetch_datacenter_rows(
            "RPT_F10_FINANCE_MAINFINADATA", secu_code,
            sort_column="REPORT_DATE", extra_filter='(REPORT_TYPE="年报")',
            limit=years,
        )
    except (ConnectionError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"❌ 获取长期财务数据失败: {exc}", file=sys.stderr)
        return False
    if not reports:
        print(f"❌ 未获取到 {secu_code} 的年度财务数据", file=sys.stderr)
        return False
    name = reports[0].get("SECURITY_NAME_ABBR") or secu_code
    print("=" * 60)
    print(f"长期财务数据: {name} ({secu_code})")
    print("=" * 60)
    for row in reports:
        year = row.get("REPORT_YEAR") or str(row.get("REPORT_DATE", ""))[:4]
        print(f"\n  --- {year}年报 ---")
        print(f"  ROE(加权):          {_fmt_pct(row.get('ROEJQ'))}")
        print(f"  毛利率:             {_fmt_pct(row.get('XSMLL'))}")
        print(f"  净利率:             {_fmt_pct(row.get('XSJLL'))}")
        print(f"  经营现金流/净利润:  {_fmt_times(row.get('NCO_NETPROFIT'))}")
        print(f"  利息覆盖:           {_fmt_times(row.get('INTSTCOVRATE'))}")
        print(f"  经营现金流:         {_fmt_yi(row.get('NETCASH_OPERATE_PK'))}")
    return True
```

在 `main()` 注册：

```python
p_history = sub.add_parser("history", help="长期年度财务数据")
p_history.add_argument("code", help="股票代码")
p_history.add_argument("--years", type=_positive_years, default=10,
                       help="年度数量，默认 10，范围 1-50")
```

命令映射增加 `"history": lambda: cmd_history(args.code, args.years)`，并在映射执行后对返回 `False` 的命令 `sys.exit(1)`。

- [ ] **Step 4: 运行 history 测试并确认 GREEN**

Run: `python3 tests/test_ashare_data.py -v`

Expected: history 字段、年报过滤、禁用 `TOTAL_SHARE`、非法年份和空数据测试通过。

- [ ] **Step 5: 提交本任务**

```bash
git add tests/test_ashare_data.py tools/ashare_data.py
git commit -m "feat: 增加A股长期财务命令"
```

### Task 4: 增加历史股本命令

**Files:**
- Modify: `tests/test_ashare_data.py`
- Modify: `tools/ashare_data.py`

**Interfaces:**
- Consumes: `_em_secu_code`、`_fetch_datacenter_rows`
- Produces: `cmd_equity_history(code: str) -> bool`
- Produces: CLI `equity-history CODE`

- [ ] **Step 1: 写股本历史字段、排序和分页失败测试**

```python
class TestEquityHistoryCommand(unittest.TestCase):
    @mock.patch.object(ashare_data, "_fetch_datacenter_rows")
    def test_outputs_date_shares_change_and_reason(self, fetch):
        fetch.return_value = [{
            "END_DATE": "2025-06-30 00:00:00",
            "SECURITY_NAME_ABBR": "样本公司",
            "TOTAL_SHARES": 1200000000,
            "TOTAL_SHARES_CHANGE": -10000000,
            "CHANGE_REASON": "股份回购",
        }]
        with redirect_stdout(StringIO()) as output:
            ok = ashare_data.cmd_equity_history("430047")
        self.assertTrue(ok)
        self.assertIn("2025-06-30", output.getvalue())
        self.assertIn("12.00亿", output.getvalue())
        self.assertIn("-1000.00万", output.getvalue())
        self.assertIn("股份回购", output.getvalue())
        self.assertEqual(fetch.call_args.args[1], "430047.BJ")
        self.assertEqual(fetch.call_args.kwargs["sort_column"], "END_DATE")
        self.assertEqual(fetch.call_args.kwargs["sort_order"], "-1")
        self.assertIsNone(fetch.call_args.kwargs.get("limit"))
```

- [ ] **Step 2: 运行股本历史测试并确认 RED**

Run: `python3 tests/test_ashare_data.py -v`

Expected: `cmd_equity_history` 和 `equity-history` 子命令不存在导致失败。

- [ ] **Step 3: 实现股本历史命令**

```python
def cmd_equity_history(code: str):
    secu_code = _em_secu_code(code)
    try:
        rows = _fetch_datacenter_rows(
            "RPT_F10_EH_EQUITY", secu_code,
            sort_column="END_DATE", sort_order="-1",
        )
    except (ConnectionError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"❌ 获取历史股本失败: {exc}", file=sys.stderr)
        return False
    if not rows:
        print(f"❌ 未获取到 {secu_code} 的历史股本", file=sys.stderr)
        return False
    name = rows[0].get("SECURITY_NAME_ABBR") or secu_code
    print("=" * 60)
    print(f"历史股本: {name} ({secu_code})")
    print("=" * 60)
    for row in rows:
        date = str(row.get("END_DATE") or "-")[:10]
        reason = row.get("CHANGE_REASON_EXPLAIN") or row.get("CHANGE_REASON") or "-"
        print(f"\n  --- {date} ---")
        print(f"  总股本:    {_fmt_yi(row.get('TOTAL_SHARES'))}")
        print(f"  变动股数:  {_fmt_yi(row.get('TOTAL_SHARES_CHANGE'))}")
        print(f"  变动原因:  {reason}")
    return True
```

注册 `equity-history CODE` 子命令，并把它映射到 `cmd_equity_history(args.code)`。

- [ ] **Step 4: 运行全部 A 股测试并确认 GREEN**

Run: `python3 tests/test_ashare_data.py -v`

Expected: 所有测试通过，覆盖沪深北、历史财务、全部股本分页和失败路径。

- [ ] **Step 5: 提交本任务**

```bash
git add tests/test_ashare_data.py tools/ashare_data.py
git commit -m "feat: 增加A股历史股本命令"
```

### Task 5: 更新使用说明并完成生成物闭环

**Files:**
- Modify: `README.md`
- Modify: `skills/quality-screen.md`
- Regenerate: `codex-skills/quality-screen/SKILL.md`
- Regenerate: `codex-prompts/quality-screen.md`

**Interfaces:**
- Consumes: `history CODE --years N`、`equity-history CODE`
- Produces: 人工和 Codex 工作流都使用正式命令，不再要求临时 scratchpad。

- [ ] **Step 1: 更新权威文档**

README 增加：

```markdown
### A 股数据工具 (`tools/ashare_data.py`)

| 功能 | 命令 |
|------|------|
| 十年年度财务 | `history 600036 --years 10` |
| 历史股本变动 | `equity-history 600036` |
```

`skills/quality-screen.md` 替换临时脚本说明：

```bash
python3 tools/ashare_data.py history <代码> --years 10
python3 tools/ashare_data.py equity-history <代码>
```

保留“财务主表 `TOTAL_SHARE` 不能作为历史股本”的警告。

- [ ] **Step 2: 同步生成物**

Run: `python3 scripts/sync-codex-skills.py && python3 scripts/sync-codex-prompts.py`

Expected: 仅对应的 quality-screen 生成物更新。

- [ ] **Step 3: 运行定向验证**

Run: `python3 tests/test_ashare_data.py -v && python3 tests/test_xueqiu_scraper_cli.py -v`

Expected: 全部定向测试通过。

- [ ] **Step 4: 运行统一检查**

Run: `bash scripts/check.sh`

Expected: 全部单元测试、19 个 skills、19 个 prompts 和报告索引检查通过。

- [ ] **Step 5: 检查范围并提交**

```bash
git diff --check
git add README.md skills/quality-screen.md \
  codex-skills/quality-screen/SKILL.md codex-prompts/quality-screen.md
git commit -m "docs: 使用正式A股历史数据命令"
```

最后确认 `local/reports/INDEX.md`、用户新增报告和 `tools/star_history_chart.py` 未被本次提交包含。
