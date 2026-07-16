# A 股历史数据命令与雪球退出码修复设计

## 背景

`quality-screen` 已要求 A 股研究优先使用仓库数据管线，但十年财务数据和历史股本仍需研究者临时调用东方财富接口。这使同一筛选流程难以稳定复现。与此同时，雪球在线模式缺少 `--user-id` 时只打印提示并正常返回，自动化调用会把参数错误误判为成功。

## 目标

1. 在现有 `tools/ashare_data.py` 中提供正式、可复现的长期财务和历史股本命令。
2. 保持 Python 标准库实现，不增加依赖，不改变现有 `quote`、`financials`、`valuation`、`search` 命令。
3. 修正雪球在线模式缺少 `--user-id` 时的退出码，并确保参数校验不依赖 Playwright。
4. 用无网络单元测试覆盖数据请求、输出、分页、错误和 CLI 退出语义。

## 命令接口

### `history`

```bash
python3 tools/ashare_data.py history 600036 --years 10
```

- `--years` 默认为 10，只接受 1–50 的整数。
- 只请求年度报告，不在无结果时混入季度报告。
- 按报告日期由新到旧输出最多 N 年。
- 每年输出：ROE、毛利率、净利率、经营现金流/净利润、利息覆盖倍数、经营现金流。
- 缺失字段显示 `-`，不推测或填造数据。
- `TOTAL_SHARE` 不作为历史股本输出；接口会把当前股本覆盖到历史年度，容易造成错误结论。

### `equity-history`

```bash
python3 tools/ashare_data.py equity-history 600036
```

- 使用 `RPT_F10_EH_EQUITY`。
- 用真实存在的 `END_DATE` 字段倒序请求并处理全部分页。
- 每条输出：变动日期、总股本、股本增减和变动原因。
- 同时支持 `.SH`、`.SZ`、`.BJ` 后缀和六位裸代码。
- 不使用财务主表中的 `TOTAL_SHARE` 代替历史股本。

## 数据流与边界

`ashare_data.py` 增加三个内部边界：

1. 证券代码标准化：把裸代码或带后缀代码转换成东方财富 `SECUCODE`。
2. Datacenter 分页读取：检查接口 `success`，按 `pages` 拉齐全部页；接口失败或空数据必须明确返回失败，禁止静默截断。
3. 展示层：命令函数只负责选择字段和生成现有风格的人类可读文本。

本次不新增 JSON 输出、不计算自由现金流，也不重构其他 A 股命令。长期财务主表没有可靠资本开支字段，因此只输出可核验的经营现金流；自由现金流仍需来自现金流量表或第二数据源，不能用不明字段代替。

## 退出语义

- `ashare_data.py`：成功且有数据为 0；接口失败或无数据为 1；非法 `--years` 等参数错误由 `argparse` 返回 2。
- `xueqiu_scraper.py`：在线模式缺少 `--user-id` 时向 stderr 输出提示并返回 2；该检查发生在 Playwright 导入之前。
- 雪球离线 `--from-cache` 模式继续允许省略 `--user-id`。

## 测试策略

新增 `tests/test_ashare_data.py`，通过 mock `_curl_json` 提供固定接口响应，覆盖：

- 沪深北代码标准化；
- 十年财务请求只包含年报过滤条件；
- 历史数据字段映射、缺失值和数量限制；
- 历史股本使用 `END_DATE`、倒序和分页；
- 接口失败、无数据和非法年份返回非成功状态；
- CLI 子命令可发现且参数正确转发。

扩展 `tests/test_xueqiu_scraper_cli.py`：

- 无参数在线调用返回 2 且提示 `--user-id`；
- 传入 `--user-id` 后，未安装 Playwright 仍返回依赖提示；
- 离线缓存行为保持不变。

最后运行具体测试、生成物同步命令和 `bash scripts/check.sh`。

## 文档与生成物

- README 增加 A 股历史数据工具说明。
- `skills/quality-screen.md` 用正式命令替换临时 scratchpad 指引。
- 运行 `scripts/sync-codex-skills.py` 和 `scripts/sync-codex-prompts.py`，确保生成物与权威 skill 源一致。

## 验收条件

1. 两个新命令出现在 `ashare_data.py --help` 中。
2. 固定测试数据能够输出预期十年指标和完整股本变动分页。
3. 测试断言历史财务命令不把 `TOTAL_SHARE` 当成历史股本。
4. 雪球在线模式缺少 `--user-id` 的退出码为 2。
5. `bash scripts/check.sh` 全部通过，且不改动用户现有报告和 `tools/star_history_chart.py`。
