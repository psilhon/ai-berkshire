# Tushare 市场数据冲突优先级设计

## 目标

当 A 股数据管线在同一主体、期间和单位下发现市场结构化字段冲突时，采用 Tushare 值作为有效值，同时保留完整冲突证据；财务披露与公告不改变既有原始披露优先级。

## 范围

- 适用：`quote`、`valuation`，以及未来由相同字段契约接入的市场价格、估值、市值、换手率和股本字段。
- 不适用：财报、年报史、公告、重大事项、文本名称、市场信号和任一无法确认同主体/期间/单位的字段。
- 仅在字段状态为 `CONFLICT`、验证源为 `tushare.*` 且比较前置条件已满足时覆盖；`MATCH`、`INSUFFICIENT`、`NOT_CONFIGURED`、权限不足、限流和空数据均不改变主数据。

## 数据契约

每个验证字段保留 `primary_value`、`verification_value`、两个来源、期间、单位及偏差。新增有效值元数据：

- `effective_value`：冲突市场字段取 `verification_value`；其余取 `primary_value`。
- `effective_source`：冲突市场字段为对应 `tushare.*` 来源；其余为主来源。
- `precedence_applied`：仅在实际覆盖时为 `true`。

CLI 在打印行情或估值前应用这些字段；覆盖字段逐项提示“原值 → Tushare 值”，不会静默替换。

## Gate 与研究结论

全量分析 gate 接收到带 `effective_source=tushare.*` 的同口径市场事实时，可以把 Tushare 视为该事实的来源；原始冲突仍必须作为证据保留。财报、公告和重大事项仍必须以巨潮/交易所原始披露作为最终口径，不能以 Tushare 覆盖。

## 验收标准

1. 市场字段冲突的有效值和有效来源为 Tushare，原值与偏差仍可审计。
2. 期间或单位不一致的字段不覆盖。
3. 财务字段即使冲突也保持东方财富/原始披露的有效值。
4. CLI 明示每项覆盖；令牌不出现在输出中。
5. Claude 与 Codex 生成技能使用同一规范。
