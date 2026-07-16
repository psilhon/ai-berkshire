# Project Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复北交所 920 代码路由，给五个独立工具建立离线行为回归测试，并让 CI 显式验证 Python 3.8、3.11、3.14。

**Architecture:** 保留现有单文件工具结构，以 `_em_secu_code()` 作为 A 股市场判断的唯一来源，其他协议格式从标准化代码转换。测试继续使用标准库 `unittest`，网络与文件写入均通过 mock 或临时目录隔离；CI 复用现有 `scripts/check.sh`。

**Tech Stack:** Python 3.8+ 标准库、`unittest`、GitHub Actions、Bash。

## Global Constraints

- 不新增 Python 或系统运行时依赖。
- 不修改研究报告、`reports/INDEX.md`、现有未跟踪文件或 `tools/star_history_chart.py` 的内容/权限。
- 不 push、不创建 PR、不发布；只允许本地提交。
- 所有网络相关测试必须离线运行。
- 生产代码变更必须先有能复现问题的失败测试。
- 最终必须运行 `bash scripts/check.sh` 和 `git diff --check`。

---

### Task 1: 北交所 920 市场路由

**Files:**
- Modify: `tests/test_ashare_data.py`
- Modify: `tools/ashare_data.py:49-70,119-128,166-171,318-334,459-492`

**Interfaces:**
- Consumes: `_em_secu_code(code: str) -> str` 的现有标准化契约。
- Produces: `_qq_code("920185") == "bj920185"`、`_em_secid("920185") == "0.920185"`，以及财务查询中的 `920185.BJ`。

- [ ] **Step 1: 为四条 920 路由和搜索标签写失败测试**

在 `TestSecurityCode` 中增加：

```python
def test_normalizes_new_beijing_920_code(self):
    self.assertEqual(ashare_data._em_secu_code("920185"), "920185.BJ")
    self.assertEqual(ashare_data._qq_code("920185"), "bj920185")
    self.assertEqual(ashare_data._em_secid("920185"), "0.920185")
```

在 `TestLegacyCommandExitSemantics` 中增加财务过滤和搜索标签测试：

```python
@mock.patch.object(ashare_data, "_curl_json")
@mock.patch.object(ashare_data, "_curl", return_value='v_none="";')
def test_financials_uses_beijing_suffix_for_920_code(self, _curl, curl_json):
    curl_json.return_value = {
        "success": True,
        "result": {"data": [{
            "REPORT_DATE": "2025-12-31",
            "REPORT_DATE_NAME": "2025年报",
        }]},
    }

    with redirect_stdout(StringIO()):
        self.assertTrue(ashare_data.cmd_financials("920185"))

    query = curl_json.call_args.args[1]
    self.assertIn('SECUCODE="920185.BJ"', query["filter"])

@mock.patch.object(ashare_data, "_curl_json")
def test_search_labels_market_zero_as_beijing(self, curl_json):
    curl_json.return_value = {
        "QuotationCodeTable": {"Data": [{
            "Code": "920185",
            "Name": "贝特瑞",
            "MktNum": "0",
        }]},
    }

    with redirect_stdout(StringIO()) as output:
        self.assertTrue(ashare_data.cmd_search("贝特瑞"))

    self.assertIn("920185 贝特瑞 [北]", output.getvalue())
```

- [ ] **Step 2: 运行新增测试并确认按预期失败**

Run: `python3 -m unittest tests.test_ashare_data.TestSecurityCode.test_normalizes_new_beijing_920_code tests.test_ashare_data.TestLegacyCommandExitSemantics.test_financials_uses_beijing_suffix_for_920_code tests.test_ashare_data.TestLegacyCommandExitSemantics.test_search_labels_market_zero_as_beijing -v`

Expected: FAIL；现有结果分别为 `920185.SH`、`sh920185`、`1.920185`，财务过滤使用 `.SH`，搜索标签为空。

- [ ] **Step 3: 以 `_em_secu_code` 为唯一市场判断实施最小修复**

把无后缀代码的判断顺序改为：

```python
elif code_clean.startswith("920"):
    market = "BJ"
elif code_clean.startswith(("6", "9", "5")):
    market = "SH"
```

让腾讯行情和东方财富 `secid` 从标准化结果转换：

```python
def _qq_code(code: str) -> str:
    secu_code = _em_secu_code(code)
    code_clean, market = secu_code.rsplit(".", 1)
    return f"{market.lower()}{code_clean}"


def _em_secid(code: str) -> str:
    secu_code = _em_secu_code(code)
    code_clean, market = secu_code.rsplit(".", 1)
    prefix = "1" if market == "SH" else "0"
    return f"{prefix}.{code_clean}"
```

`cmd_financials` 使用相同标准化结果：

```python
secu_code = _em_secu_code(code)
code_clean, _market = secu_code.rsplit(".", 1)
```

两个过滤条件均直接使用 `secu_code`。搜索标签映射增加 `"0": "北"`：

```python
mkt_label = {"0": "北", "1": "沪", "2": "深", "3": "北"}.get(
    str(market), ""
)
```

- [ ] **Step 4: 运行 A 股测试并确认通过**

Run: `python3 -m unittest tests.test_ashare_data -v`

Expected: 全部通过，0 failures，0 errors。

- [ ] **Step 5: 提交 920 路由修复**

```bash
git add tools/ashare_data.py tests/test_ashare_data.py
git commit -m "fix: 修复北交所920代码路由"
```

---

### Task 2: 动量回测工具行为测试

**Files:**
- Create: `tests/test_momentum_backtest.py`
- Create: `tests/test_momentum_backtest_v2.py`

**Interfaces:**
- Consumes: `compute_momentum_signals(prices)`, `find_latest_fundamental(ticker, signal_date)`, `verify_value(ticker, fund_data, prev_fund_data=None)`。
- Consumes: `load_prices_from_json(filepath)`, `scan_momentum(prices)`, `find_fund(ticker, date)`, `verify(fund, prev_fund)`。
- Produces: 两个旧回测脚本的确定性行为回归保护；不修改生产代码。

- [ ] **Step 1: 编写 v1 动量、财报选择和价值评分测试**

创建 `tests/test_momentum_backtest.py`，用下列辅助数据构造 61 个交易日：

```python
def make_prices(trigger=True):
    prices = []
    for i in range(61):
        prices.append({
            "date": f"2025-01-{i + 1:02d}",
            "close": 100.0,
            "high": 100.0,
            "volume": 1000 if trigger and i >= 56 else 100,
        })
    prices[-1]["close"] = 120.0
    return prices
```

测试断言：触发数据返回一个信号且 `vol_ratio > 1.8`；等量数据不返回信号；`find_latest_fundamental("NVDA", "2023-05-25")` 返回 `2023-05-24`；高增长样本得到 5/5。

- [ ] **Step 2: 运行 v1 测试并记录现有行为**

Run: `python3 -m unittest tests.test_momentum_backtest -v`

Expected: 全部通过；这是对既有纯逻辑的行为锁定，不要求生产代码变化。

- [ ] **Step 3: 编写 v2 JSON、动量、财报关系和价值评分测试**

创建 `tests/test_momentum_backtest_v2.py`：

```python
payload = {
    "chart": {"result": [{
        "timestamp": [0, 86400],
        "indicators": {"quote": [{
            "close": [10.0, 11.0],
            "volume": [100, 200],
            "high": [10.5, 11.5],
        }]},
    }]},
}
```

使用 `tempfile.TemporaryDirectory()` 写入 fixture，断言两行均被解析；复用 61 日辅助数据断言一个动量信号；断言 `find_fund("NVDA", "2023-05-25")` 返回 2023-05-24 和前一季度；断言 `verify()` 返回的分数等于布尔检查之和。

- [ ] **Step 4: 运行 v2 测试并记录现有行为**

Run: `python3 -m unittest tests.test_momentum_backtest_v2 -v`

Expected: 全部通过；不访问 `/tmp/AMD_prices.json`、`/tmp/MU_prices.json` 或实时网络。

- [ ] **Step 5: 提交回测测试**

```bash
git add tests/test_momentum_backtest.py tests/test_momentum_backtest_v2.py
git commit -m "test: 覆盖动量回测核心逻辑"
```

---

### Task 3: Morningstar 排序与空数据修复

**Files:**
- Create: `tests/test_morningstar_fair_value.py`
- Modify: `tools/morningstar_fair_value.py:40-146`

**Interfaces:**
- Consumes: `extract_ticker(tenforeid: str) -> str`。
- Produces: `rank_stocks(rows: list) -> list`；过滤无有效现价/公允价值记录并按 `upside_pct` 降序返回。
- Produces: `main()` 在零条有效股票时仍输出 0% 摘要并正常写出空 CSV。

- [ ] **Step 1: 为排序函数写失败测试**

创建 `tests/test_morningstar_fair_value.py`，断言：

```python
rows = [
    {"TenforeId": "US.XNAS.AAPL", "Name": "Apple", "ClosePrice": 100,
     "FairValueEstimate": 120},
    {"TenforeId": "US.XNYS.BRK", "Name": "Berkshire", "ClosePrice": 100,
     "FairValueEstimate": 150},
    {"TenforeId": "US.XNAS.ZERO", "Name": "Zero", "ClosePrice": 0,
     "FairValueEstimate": 10},
]
stocks = morningstar_fair_value.rank_stocks(rows)
self.assertEqual([s["ticker"] for s in stocks], ["BRK", "AAPL"])
self.assertEqual([s["upside_pct"] for s in stocks], [50.0, 20.0])
```

同时断言 `extract_ticker(None) == ""`、`extract_ticker("AAPL") == "AAPL"`。

- [ ] **Step 2: 运行排序测试并确认因函数缺失而失败**

Run: `python3 -m unittest tests.test_morningstar_fair_value.TestRankStocks -v`

Expected: ERROR/FAIL，提示模块没有 `rank_stocks`。

- [ ] **Step 3: 提取最小排序函数并让主流程复用**

把 `main()` 中“计算潜在涨幅 + 排序”的循环迁入：

```python
def rank_stocks(rows):
    stocks = []
    for row in rows:
        fair_value = row.get("FairValueEstimate")
        close_price = row.get("ClosePrice")
        if not fair_value or not close_price or close_price <= 0:
            continue
        upside = (fair_value - close_price) / close_price * 100
        stocks.append({
            "ticker": extract_ticker(row.get("TenforeId", "")),
            "name": row.get("Name", ""),
            "close_price": round(close_price, 2),
            "fair_value": round(fair_value, 2),
            "upside_pct": round(upside, 1),
            "star_rating": row.get("StarRatingM255", ""),
            "moat": row.get("EconomicMoat", ""),
            "uncertainty": row.get("AssessmentOfFairValueUncertainty", ""),
            "sector": row.get("SectorName", ""),
            "industry": row.get("IndustryName", ""),
        })
    return sorted(stocks, key=lambda stock: stock["upside_pct"], reverse=True)
```

`main()` 改为 `stocks = rank_stocks(all_rows)`。

- [ ] **Step 4: 运行排序测试并确认通过**

Run: `python3 -m unittest tests.test_morningstar_fair_value.TestRankStocks -v`

Expected: 全部通过。

- [ ] **Step 5: 为零有效记录写失败回归测试**

mock `fetch_page()` 返回 `{"total": 0, "rows": []}`，将 `OUTPUT_DIR` 指向临时目录，并捕获标准输出后调用 `main()`：

```python
@mock.patch.object(morningstar_fair_value, "fetch_page",
                   return_value={"total": 0, "rows": []})
def test_main_handles_no_valid_stocks(self, _fetch):
    with tempfile.TemporaryDirectory() as tmpdir, \
            mock.patch.object(morningstar_fair_value, "OUTPUT_DIR", tmpdir), \
            redirect_stdout(StringIO()) as output:
        morningstar_fair_value.main()
    self.assertIn("低估股票: 0 只 (0%)", output.getvalue())
    self.assertIn("高估股票: 0 只 (0%)", output.getvalue())
```

- [ ] **Step 6: 运行空数据测试并确认除零失败**

Run: `python3 -m unittest tests.test_morningstar_fair_value.TestEmptyResult -v`

Expected: ERROR，`ZeroDivisionError` 来自 `len(stocks)` 为 0。

- [ ] **Step 7: 用安全分母修复摘要**

```python
stock_count = len(stocks)
undervalued_pct = len(undervalued) / stock_count * 100 if stock_count else 0
overvalued_pct = len(overvalued) / stock_count * 100 if stock_count else 0
print(f"     低估股票: {len(undervalued)} 只 ({undervalued_pct:.0f}%)")
print(f"     高估股票: {len(overvalued)} 只 ({overvalued_pct:.0f}%)")
```

- [ ] **Step 8: 运行 Morningstar 全部测试并确认通过**

Run: `python3 -m unittest tests.test_morningstar_fair_value -v`

Expected: 全部通过，0 failures，0 errors。

- [ ] **Step 9: 提交 Morningstar 修复**

```bash
git add tools/morningstar_fair_value.py tests/test_morningstar_fair_value.py
git commit -m "fix: 处理晨星筛选空数据"
```

---

### Task 4: Star 图表与选股器行为测试

**Files:**
- Create: `tests/test_star_history_chart.py`
- Create: `tests/test_stock_screener.py`

**Interfaces:**
- Consumes: `monotone_x_path(pts)`, `linear_ticks(stop, count=5)`, `time_ticks(t0, t1, count=5)`, `time_tick_label(t)`, `number_tick_label(v, unit)`。
- Consumes: `check_momentum(prices)`, `grade_signal(momentum, value)`。
- Produces: 图表数学逻辑和信号分级的确定性回归保护；不修改两个生产文件。

- [ ] **Step 1: 编写 Star 图表纯函数测试**

创建 `tests/test_star_history_chart.py`，断言：

```python
self.assertEqual(star_history_chart.monotone_x_path([(0, 1)]), "M0,1")
self.assertEqual(star_history_chart.monotone_x_path([(0, 1), (2, 3)]),
                 "M0,1L2,3")
self.assertTrue(star_history_chart.monotone_x_path(
    [(0, 0), (1, 1), (2, 1)]).startswith("M0,0C"))
self.assertEqual(star_history_chart.linear_ticks(100, 5),
                 [0, 20, 40, 60, 80, 100])
self.assertEqual(star_history_chart.number_tick_label(2000, 1000), "2K")
```

另用 UTC datetime 断言月度 `time_ticks()` 严格落在区间内，并验证年首/月首/周日标签。

- [ ] **Step 2: 运行 Star 图表测试并记录现有行为**

Run: `python3 -m unittest tests.test_star_history_chart -v`

Expected: 全部通过；不得产生 `tools/star_history_chart.py` 内容或权限差异。

- [ ] **Step 3: 编写选股器动量与分级测试**

创建 `tests/test_stock_screener.py`，用 61 日辅助行情断言 `check_momentum()` 触发、短于 61 日返回 `None`。构造下列输入验证评级：

```python
triggered = {"triggered": True}
self.assertEqual(stock_screener.grade_signal(None, None)[0], "SKIP")
self.assertEqual(stock_screener.grade_signal(triggered, None)[0], "WATCH")
self.assertEqual(stock_screener.grade_signal(
    triggered, {"score": 5, "independent_pass": False})[0], "BUY_8%")
self.assertEqual(stock_screener.grade_signal(
    triggered, {"score": 4, "independent_pass": False})[0], "BUY_5%")
self.assertEqual(stock_screener.grade_signal(
    triggered, {"score": 3, "independent_pass": False})[0], "BUY_3%")
self.assertEqual(stock_screener.grade_signal(
    triggered, {"score": 2, "independent_pass": False})[0], "PASS")
```

- [ ] **Step 4: 运行选股器测试并记录现有行为**

Run: `python3 -m unittest tests.test_stock_screener -v`

Expected: 全部通过；不读写 `data/fundamentals.json` 或 `data/watchlist.json`。

- [ ] **Step 5: 提交两个工具的测试**

```bash
git add tests/test_star_history_chart.py tests/test_stock_screener.py
git commit -m "test: 覆盖图表与选股核心逻辑"
```

---

### Task 5: CI Python 版本矩阵与最终复评

**Files:**
- Modify: `.github/workflows/check.yml`

**Interfaces:**
- Consumes: `bash scripts/check.sh` 的现有统一检查入口。
- Produces: Python 3.8、3.11、3.14 三个独立 CI 任务。

- [ ] **Step 1: 更新 CI 工作流**

将 job 改为：

```yaml
jobs:
  check:
    name: Python ${{ matrix.python-version }}
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.8", "3.11", "3.14"]
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: ${{ matrix.python-version }}
      - run: bash scripts/check.sh
```

- [ ] **Step 2: 检查 YAML 关键字段**

Run: `rg -n 'fail-fast|python-version|setup-python@v6|scripts/check.sh' .github/workflows/check.yml`

Expected: 出现 `fail-fast: false`、三个 Python 版本、`setup-python@v6` 和统一检查命令。

- [ ] **Step 3: 运行完整单元测试**

Run: `python3 -m unittest discover -s tests -v`

Expected: 全部通过，0 failures，0 errors；测试数高于修改前的 125。

- [ ] **Step 4: 运行仓库统一检查**

Run: `bash scripts/check.sh`

Expected: 单元测试、19 个 Codex skills、19 个 Codex prompts、报告索引检查全部通过。

- [ ] **Step 5: 运行差异与范围审计**

Run: `git diff --check`

Expected: 无输出，退出码 0。

Run: `git status --short`

Expected: 本轮只有 `.github/workflows/check.yml` 尚未提交；主工作区原有报告和权限改动不应出现在隔离工作区。

- [ ] **Step 6: 提交 CI 更新**

```bash
git add .github/workflows/check.yml
git commit -m "ci: 增加Python版本矩阵"
```

- [ ] **Step 7: 做提交后最终复验并逐项复评**

Run: `bash scripts/check.sh && git diff --check && git status --short --branch`

Expected: 全部检查通过；工作树干净；分支只领先其基线提交。

复评必须逐项确认：920 四条路由、五工具测试覆盖、Morningstar 空数据、CI 三版本矩阵、无新增依赖、未触及报告、未远端写入。
