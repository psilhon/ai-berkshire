# 数据源使用评估与优化方案

**评估日期**：2026-07-22 | **仅供学习研究，不构成投资建议**

---

## 一、现状概览

### 数据源总览

| 数据源 | 权重 | 命令数 | 性质 |
|--------|:----:|:------:|------|
| **Tushare Pro**（付费） | 🔴 高 | 47 | 独立结构化数据，可审计可冻结 |
| 腾讯行情 + 东方财富（免费） | 🟡 中 | 7 | 基础行情+财务，零 token 依赖 |
| WebSearch（免费） | 🟢 低 | 无限 | 非结构化，不可冻结，有知识截止 |
| financial_rigor.py（内部） | 🔴 高 | 8 子命令 | 精确计算/验算，无外部依赖 |
| report_audit.py（内部） | 🔴 高 | 3 子命令 | 报告审计，无外部依赖 |

### 命令层覆盖

| 层级 | 数量 | 说明 |
|------|:----:|------|
| 必需操作（每次必跑） | 7 | quote/financials/valuation/history/equity-history/announcements/signals |
| 条件 Tushare（token 可用时） | 47 | 三大报表/资金面/龙虎榜/券商研报/北向/融资融券/板块流/宏观等 |
| A+H 专用 | 2 | hk-quote/ah-cross-check |
| 搜索工具 | 1 | search |
| **总计** | **57** | |

---

## 二、数据使用矩阵（按 skill）

### 核心发现：42 个 Tushare 命令在合约中有 feeds 映射，但 skill 文件未引用

| Skill | 合约应引用 | 实际引用 | 缺失 | 影响程度 |
|-------|:---------:|:-------:|:----:|:-------:|
| **investment-research** | 20 | 0 | **20** | 🔴 极高 |
| **news-pulse** | 9 | 1 | **8** | 🔴 极高 |
| **earnings-review** | 4 | 0 | **4** | 🔴 高 |
| **industry-research** | 3 | 0 | **3** | 🟡 中 |
| **management-deep-dive** | 3 | 1 | 2 | 🟡 中 |
| **portfolio-review** | 2 | 0 | 2 | 🟡 中 |
| **quality-screen** | 1 | 0 | 1 | 🟢 低 |
| **financial-data** | 1 | 0 | 1 | 🟢 低 |
| **industry-funnel** | 1 | 0 | 1 | 🟢 低 |
| **thesis-tracker** | 1 | 0 | 1 | 🟢 低 |
| **thesis-drift** | 1 | 0 | 1 | 🟢 低 |

### investment-research 缺失的 20 个命令（影响最大）

| 命令 | 数据 | 对投资分析的价值 |
|------|------|----------------|
| `income-stmt` | 利润表原始数据 | 扣非利润精算，不再依赖东财聚合 |
| `balance-sheet` | 资产负债表 | 商誉/存货/应收精确值 |
| `cash-flow` | 现金流量表 | FCF 逐项精算 |
| `pe-band` | PE/PB 历史分位 | 判断当前估值在历史中的位置 |
| `insider-trades` | 高管增减持 | 管理层行为信号 |
| `shareholders` | 十大股东结构 | 股权集中度/机构持仓变化 |
| `analyst-reports` | 券商研报(目标价/评级) | 卖方一致预期 |
| `broker-recommend` | 券商月度金股 | 机构推荐池 |
| `cyq-chips` | 筹码分布 | 筹码集中度/主力成本 |
| `stk-factor` | 技术因子 | 量化指标 |
| `factors` | 技术因子(专业版) | 更全面的量化指标 |
| `unblock` | 限售解禁 | 解禁压力 |
| `block-trade` | 大宗交易 | 大资金动向 |
| `margin` | 融资融券 | 杠杆情绪 |
| `weekly/monthly` | 周月线 | 长期趋势 |
| `suspend` | 停复牌 | 流动性风险 |
| `name-history` | 历史更名 | 公司变更追踪 |
| `holder-num` | 股东户数 | 筹码集中趋势 |
| `research-visits` | 机构调研 | 机构关注度 |
| `repurchase` | 股票回购 | 股东回报 |

### news-pulse 缺失的 8 个命令

| 命令 | 数据 | 对新闻脉搏的价值 |
|------|------|----------------|
| `top-list` | 龙虎榜 | 异动归因——谁在买/卖 |
| `limit-list` | 涨跌停清单 | 市场情绪温度计 |
| `limit-price` | 涨跌停价格 | 距涨停/跌停的距离 |
| `ths-hot` | 同花顺热榜 | 市场关注度排名 |
| `money-flow` | 个股资金流向 | 主力 vs 散户方向 |
| `hsgt-flow` | 北向资金流向 | 外资方向 |
| `hsgt-top10` | 北向十大成交 | 外资重点标的 |
| `north-hold` | 北向持股趋势 | 外资中长期态度 |

---

## 三、数据源优先级体系（Tushare 加权）

### 分层优先级

```
Tier 0 — Tushare 结构化数据（最高权重，可审计可冻结）
    ├── 三大报表（income/balancesheet/cashflow）→ 替代东财聚合数据
    ├── 财务比率（fina_indicator/ratios）→ 独立交叉验证
    ├── 估值历史（pe-band/daily_basic）→ 历史分位
    └── 资金/筹码/龙虎榜 → A 股特有情绪指标

Tier 1 — 腾讯行情 + 东方财富（基础层，零依赖）
    ├── 实时行情（quote）→ 价格双源验证
    ├── 核心财务（financials）→ 近 5 年摘要
    └── 估值指标（valuation）→ PE/PB/股息率

Tier 2 — WebSearch（补充层，不可冻结）
    ├── 行业报告/新闻 → 定性信息
    ├── 管理层公开信息 → 履历/采访
    └── 竞争对手动态 → 非结构化情报

Tier 3 — 内部工具（计算层，确定性）
    ├── financial_rigor.py → 精确 Decimal 计算
    └── report_audit.py → 报告质量门
```

### 数据冲突解决规则

| 冲突场景 | 解决规则 |
|---------|---------|
| Tushare PE vs 东财 PE | **以 Tushare daily_basic 为准**（独立源） |
| Tushare 利润表 vs 东财 financials | **以 Tushare income 为准**（原始报表），东财仅做快速概览 |
| Tushare 股东数据 vs WebSearch | **以 Tushare top10_holders 为准**（可审计） |
| Tushare 高管薪酬 vs WebSearch | **以 Tushare stk_rewards 为准**（官方披露） |
| 任何 Tushare 数据 vs WebSearch | **Tushare 优先**，WebSearch 仅补充 Tushare 无覆盖的维度 |

---

## 四、优化方案

### 方案 1：更新 skill 文件引用 Tushare 命令（🔴 最高优先级）

**问题**：42 个 Tushare 命令在合约中有 feeds 映射，但 skill 文件没引用它们。Agent 执行 skill 时不知道该调哪些 Tushare 命令。

**修复**：在每个 skill 的 SKILL.md 中增加「Tushare 数据引用」章节，列出该 skill 应调用的命令及用途。

**优先修复顺序**（按影响程度）：

1. **investment-research**（缺 20 个）— 核心研究 skill，缺失最多
2. **news-pulse**（缺 8 个）— 异动归因完全依赖资金面数据
3. **earnings-review**（缺 4 个）— 财报精读缺三大报表原始数据
4. **industry-research**（缺 3 个）— 行业研究缺概念板块和资金流
5. 其余 7 个 skill（各缺 1-2 个）

### 方案 2：建立数据引用模板（🟡 中优先级）

在 ashare-data skill 中定义标准化的「Tushare 数据引用块」模板，每个 consuming skill 复制粘贴即可：

```markdown
## Tushare 数据引用（付费数据源，优先级最高）

| 命令 | 用途 | 在本 skill 中的使用方式 |
|------|------|----------------------|
| `income-stmt` | 利润表原始数据 | 验证扣非利润、营业利润拆解 |
| `pe-band` | PE 历史分位 | 判断当前估值的贵贱 |
| ... | ... | ... |
```

### 方案 3：数据源声明章节标准化（🟢 低优先级）

每个 skill 产物报告的头部增加数据源声明：

```markdown
**数据来源优先级**：Tushare（可审计） > 东财/腾讯（基础层） > WebSearch（补充）
**Tushare 命令使用**：income-stmt / balance-sheet / cash-flow / pe-band / ...
```

### 方案 4：tushare-enrich 自动执行时机（🟡 中优先级）

当前 `tushare-enrich` 需要手动调用。优化为：
- 在 `finish-skill --state COMPLETE` 后自动触发 `tushare-enrich`（对该 skill feeds 的 Tushare 命令）
- 或在 `reference-freeze` 前强制跑一轮完整 `tushare-enrich`

---

## 五、实施路径

| 阶段 | 内容 | 预计改动 |
|------|------|---------|
| **Phase 1** | 更新 investment-research + news-pulse + earnings-review 的 SKILL.md | 3 个文件，每个 +30 行 |
| **Phase 2** | 更新 industry-research + management-deep-dive + portfolio-review | 3 个文件，每个 +15 行 |
| **Phase 3** | 更新剩余 5 个 skill（各补 1-2 行引用） | 5 个文件，每个 +5 行 |
| **Phase 4** | 在 ashare-data skill 中增加数据源优先级体系说明 | 1 个文件 +20 行 |
| **Phase 5** | orchestrate.py finish-skill 后自动 tushare-enrich | 1 个文件改动 |

---

## 六、结论

当前项目的数据管线存在**"管道已铺好但水龙头没开"**的问题：
- 57 个命令已经实现（管道铺好）
- 47 个 Tushare 命令已在合约中定义 feeds 映射（管道接好）
- 但 42 个命令在 consuming skill 中完全没被引用（水龙头没开）

**核心修复**：更新 11 个 skill 文件，显式引用它们应使用的 Tushare 命令。这是投入产出比最高的单一改动——不需要改代码，只需要在 SKILL.md 中加几行文字告诉 Agent "你应该调用这些命令"。
