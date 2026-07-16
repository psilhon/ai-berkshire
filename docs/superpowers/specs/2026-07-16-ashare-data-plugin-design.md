# Berkshire A 股数据插件设计

## 目标

在不引入 `mootdx`、`pandas`、`stockstats` 等额外依赖的前提下，把 `a-stock-data` 中对 Berkshire 投资研究真正有用的 A 股数据能力，收敛为 Berkshire 内部可复用、可测试、可降级的数据插件。

插件服务于研究工作流，不负责交易执行，也不把打板、热榜、人气榜、ETF 期权和互动易作为第一版能力。

## 范围

第一版包含五组能力：

1. 行情与历史价格：腾讯实时行情、东方财富 52 周高低点。
2. 财务与股本：复用现有财务、年度历史和股本历史能力。
3. 公告与新闻：巨潮公告，并支持交易所/东方财富备用源。
4. 市场信号：龙虎榜、资金流、解禁、融资融券。
5. 备用源与错误报告：交易所官方、新浪等明确的降级路径。

第一版不包含：打板池、涨停原因、热榜、人气榜、ETF 期权、互动易、同花顺 NLP 研报搜索和 mootdx TCP 行情。

## 设计原则

- 兼容现有 `tools/ashare_data.py` 的 CLI 命令，不破坏既有调用者。
- 使用 Python 标准库和现有 `/usr/bin/curl` 路线，不增加运行时依赖。
- 所有网络访问经过统一 transport 层，禁止端点自行绕过超时、错误分类和降级策略。
- 数据源失败必须显式暴露，不能用空列表伪装成功。
- 只有配置过且语义兼容的备用源才能自动降级。
- 研究数据必须携带来源、数据截止时间和警告信息。
- 不关闭 HTTPS 证书校验，不把原始凭据写入仓库或输出。
- 网络测试与单元测试分离，默认测试不依赖实时外部接口。

## 目录与职责

```text
tools/ashare_plugin/
  __init__.py
  errors.py          # 结果状态、数据源和错误类型
  transport.py       # curl、超时、JSON 解析、有限重试
  identifiers.py     # A 股代码与市场归一化
  quote.py           # 实时行情与 52 周区间
  fundamentals.py    # 财务、年度历史、股本历史
  market_signals.py  # 龙虎榜、资金流、解禁、融资融券
  disclosures.py     # 公告主源与备用源
```

`tools/ashare_data.py` 保留为 CLI 适配器，负责参数解析和人类可读输出；业务逻辑迁入上述插件模块。

## 统一结果契约

所有公开插件函数返回 `DataResult`，实现上可以使用 `TypedDict` 或等价的标准库数据结构：

```python
{
    "ok": True,
    "data": {...},
    "source": "tencent",
    "fallback_used": False,
    "as_of": "2026-07-16T15:00:00+08:00",
    "warnings": [],
}
```

失败结果必须保持同一结构：

```python
{
    "ok": False,
    "data": None,
    "source": "eastmoney",
    "fallback_used": True,
    "as_of": None,
    "warnings": ["主源返回 403，已尝试新浪备用源"],
    "error_type": "rate_limited",
    "message": "所有配置数据源均未返回可用数据",
}
```

允许的错误类型至少包括：`invalid_code`、`timeout`、`http_error`、`parse_error`、`rate_limited`、`empty_data` 和 `all_sources_failed`。

## 数据源与降级

| 能力 | 主源 | 备用源 | 降级条件 |
|---|---|---|---|
| 实时行情 | 腾讯 | 暂无 | 主源失败则显式失败 |
| 财务/年度历史/股本 | 东方财富 | 巨潮原始披露仅作后续核验 | 返回错误或空结果 |
| 龙虎榜 | 东方财富 | 上交所/深交所官方 | HTTP 错误、解析错误或空结果 |
| 资金流 | 东方财富 | 新浪 | HTTP 错误、解析错误或空结果 |
| 解禁/融资融券 | 东方财富 | 暂无 | 显式返回数据不足 |
| 公告 | 巨潮 | 深交所官方；沪市东方财富公告 PDF | 按市场选择兼容备用源 |

主源和备用源必须在结果中标识，调用方不得把备用源数据当作主源数据静默使用。

## CLI 兼容与新增入口

现有命令保持不变：

```text
quote CODE
financials CODE
valuation CODE
search KEYWORD
history CODE [--years N]
equity-history CODE
```

新增命令：

```text
signals CODE [--date YYYY-MM-DD]
announcements CODE [--limit N]
```

CLI 对失败结果使用非零退出码，并输出来源、截止时间、降级状态和数据不足原因；不打印完整响应体，避免将上游页面或潜在敏感头部带入日志。

## 测试策略

测试先于实现，分为三层：

1. 纯函数测试：代码归一化、腾讯字段解析、金额和百分比解析、错误分类。
2. 传输层 mock 测试：超时、HTTP 错误、JSON 解析失败、有限重试和备用源切换。
3. CLI 回归测试：现有六个命令输出和退出码不回归；新增 `signals`、`announcements` 在 mock 数据下可运行。

默认不访问实时网络。必要的真实接口 smoke test 作为手工命令，不纳入 `scripts/check.sh` 的强制路径。

## 验收标准

- `python3 -m unittest discover -s tests` 通过。
- `bash scripts/check.sh` 通过。
- 现有 `ashare_data.py` 六个 CLI 命令继续可发现并保持成功/失败退出语义。
- 新增 `signals` 和 `announcements` 在主源失败时能切换到已定义备胎，或返回明确的 `all_sources_failed`。
- 所有新数据结果携带来源和 `as_of`/警告信息。
- 代码中不存在绕过 transport 层的网络请求。
- 不新增外部 Python 运行时依赖。

## 非目标

- 不构建完整 A 股 SDK。
- 不迁移 `a-stock-data` 的全部 43 个端点。
- 不引入短线交易信号作为 Berkshire 的投资结论。
- 不自动发布报告、发送外部消息或写入远程系统。
