---
name: thesis-drift
description: 投资论文漂移检测：对比两份研究报告/论文快照，分清“事实变化”与“措辞变化”。输入“公司名 旧报告 新报告”或仅“公司名”自动查找历史快照。
owner: psilhon
category: 持仓与论文管理
maturity: stable
review-cadence: per-release
---

## Codex adapter note

This skill is generated from `skills/thesis-drift.md` so Claude Code and Codex users share one canonical workflow.

- Treat `$ARGUMENTS` as the user's request in the current Codex thread.
- When the source mentions Claude-only surfaces such as Task, Agent, WebSearch, Bash, Read, or Write, use the closest Codex capability available in this session: subagents when available, web search when needed, shell commands for local tools, and normal file edits for workspace files.
- Use shared project tools from `tools/` in this repository. Prefer running commands from the repository root with paths like `python3 tools/financial_rigor.py ...`; if the current thread starts outside the repo, locate the actual checkout path first instead of assuming a fixed home-directory path.
- Before starting research, run the `date` command to confirm today's date; treat it as the baseline for "latest" data and state the data cutoff date in the report header. Never assume the current date from training data.
- Preserve the research quality rules from `AGENTS.md`: cross-check financial data, use exact arithmetic tools for valuation/math, and clearly label uncertainty and source gaps.

# 投资论文漂移检测：分清事实变化与措辞变化

对 $ARGUMENTS 执行投资论文漂移检测。

**支持输入格式**：
- `公司名 旧报告路径 新报告路径` — 指定两份研究报告或论文快照进行对比
- `公司名 local/reports/{公司名}-thesis-旧日期.md local/reports/{公司名}-thesis-新日期.md` — 对比两份带日期的论文快照
- `公司名` — 自动查找 `local/reports/{公司名}-thesis.md` 及同目录历史快照；如果没有基线则转入缺失基线处理

> "当事实改变时，我就改变想法。你呢？" —— 凯恩斯
>
> "股价波动不是论文漂移，事实变了才是。" —— AI Berkshire

## 设计理念

长期持仓最难的不是每天读新闻，而是区分三件事：
- **事实改变**：收入、利润率、竞争格局、管理层行为、资本配置发生可验证变化
- **价格改变**：市场情绪或估值倍数变化，但生意本身未变
- **措辞改变**：两份报告表达不同，但底层证据和判断没有变化

投资论文漂移检测的目标是：**只在证据变化时承认论文变化**。不能因为报告换了写法就制造漂移，也不能因为股价涨跌就误判基本面。

本 Skill 依赖 `/thesis-tracker` 输出的结构化维度：核心假设清单、红线清单、估值锚点、追踪记录表。没有这些结构时，先补齐基线，再做漂移检测。

## 执行流程

### 第一步：判断操作模式

解析 `$ARGUMENTS`：
- 如果提供两份报告路径 → 进入**指定报告对比**模式
- 如果只提供公司名 → 查找 `local/reports/{公司名}-thesis.md` 及历史快照，进入**自动快照对比**模式
- 如果只找到一份报告或没有历史基线 → 进入**缺失基线处理**模式
- 如果两份报告不是同一家公司 → 停止并要求用户确认，不做跨公司漂移判断

---

## 模式A：指定报告对比

### A1：读取并校验两份报告

读取旧报告和新报告，提取：
- 报告日期、公司名、股票代码
- 核心论文（5句话）
- 核心假设清单
- 红线清单
- 估值锚点
- 追踪记录表
- 管理层质量判断
- 竞争护城河判断
- 当前建议动作（买入 / 持有 / 观察 / 减仓 / 清仓）

如果报告缺少关键结构，先标注"结构缺失"，但仍尽量从正文中抽取证据；抽取不到的维度标为"无法判断"，不能编造结论。

### A2：证据归一化

把两份报告中的事实证据整理成同一张表：

| 维度 | 旧报告证据 | 新报告证据 | 数据来源 | 是否可验证 |
|------|-----------|-----------|---------|-----------|
| 估值锚点 | | | | |
| 核心假设 | | | | |
| 红线 | | | | |
| 管理层质量 | | | | |
| 竞争护城河 | | | | |

**只比较证据，不比较文风。** 如果新旧报告只是同义改写、排序变化、语气变化，但事实数据和判断阈值没有变化，判定为 Unchanged。

### A3：数值与估值校验

所有数值变化必须使用 `tools/financial_rigor.py` 做精确计算，禁止 LLM 心算：

```bash
python3 tools/financial_rigor.py verify-valuation \
  --price {当前价格} \
  --eps {EPS} \
  --bvps {每股净资产} \
  --fcf-per-share {每股自由现金流}
```

如需计算市值、百分比变化、目标价差异或情景估值，使用：

```bash
python3 tools/financial_rigor.py verify-market-cap --price {价格} --shares {股本} --reported {报告市值} --currency {币种}
python3 tools/financial_rigor.py cross-validate --field {字段} --values '{JSON}' --unit {单位}
python3 tools/financial_rigor.py three-scenario --price {价格} --eps {EPS} --shares {股本亿} --growth {乐观} {中性} {悲观} --pe {乐观PE} {中性PE} {悲观PE}
python3 tools/financial_rigor.py calc --expr '{精确算式}'
```

关键财务数据必须至少两处独立来源交叉验证。来源不足、口径不一致、无法复核的数字必须标注为"低置信度 / 待核实"。

🔴 STOP / 检查点：在判定论文"Improved/Unchanged/Weakened"漂移方向与"建议动作迁移"（如 Watch→Buy、Hold→Reduce）等强结论前，必须先向用户确认（给出明确选项：如"输出完整漂移判定与动作建议""仅输出中性对比不替用户做动作迁移"），获得明确同意后再继续；未经确认不得自主替用户决定持仓动作。

### A4：逐维度判定漂移

固定使用以下维度，不要临时增减：

| 维度 | 判定重点 | Improved | Unchanged | Weakened |
|------|---------|----------|-----------|----------|
| 估值锚点 | 内在价值、PE/PB/FCF Yield、安全边际、目标价区间 | 安全边际扩大或内在价值上修且经工具验算 | 估值区间和安全边际无实质变化 | 安全边际收窄、内在价值下修或估值假设失效 |
| 核心假设清单 | 收入增速、利润率、现金流、用户/订单/产能等可验证假设 | 更多假设被新证据强化 | 假设状态与证据基本一致 | 假设边际弱化、受损或破裂 |
| 红线清单 | 诚信、监管、业务衰退、竞争突破、管理层异常动作 | 原有红线风险解除或显著下降 | 未触发且风险水平不变 | 红线被触发或触发概率上升 |
| 管理层质量 | 诚信、资本配置、回购分红、执行力、股东友好度 | 新行为提高信任度 | 行为延续旧判断 | 行为损害信任或资本配置变差 |
| 竞争护城河 | 市占率、定价权、网络效应、成本优势、替代威胁 | 护城河变宽或竞争优势被验证 | 格局无实质变化 | 护城河被削弱或竞对突破 |

每个维度只能给出三类结论：**Improved / Unchanged / Weakened**。

### A5：证据驱动规则

每个非 Unchanged 的结论必须引用导致变化的具体新证据：
- 财报行项目：例如收入增速、毛利率、经营现金流、回购金额、净现金
- 监管披露：例如 10-K/20-F、年报、中报、港交所公告、SEC filing
- 新闻事件：例如管理层变动、监管处罚、重大客户流失、竞品突破
- 价格与估值：必须说明这是"估值变化"还是"基本面变化"，不能混淆

如果找不到能解释变化的证据，必须判定为 **Unchanged** 或 **无法判断**，不能用措辞差异推断漂移。

🔴 STOP / 检查点：在对外发布/落盘完整漂移检测报告前，必须先向用户确认（给出明确选项：如"确认输出完整漂移报告""仅生成草稿供预览不落盘"），获得明确同意后再继续；未经确认不得自主落盘或外发。

### A6：输出漂移报告

#### 报告结构

```
一、对比对象与时间跨度
二、总体结论：论文是否漂移
三、维度漂移表
四、证据差异明细
五、估值与数值验算
六、建议动作迁移
七、不确定项与需补充来源
八、下次跟踪重点
```

#### 维度漂移表

| 维度 | 旧判断 | 新判断 | 漂移方向 | 触发证据 | 置信度 |
|------|-------|-------|:--------:|---------|:------:|
| 估值锚点 | | | Improved / Unchanged / Weakened | | 高/中/低 |
| 核心假设清单 | | | Improved / Unchanged / Weakened | | 高/中/低 |
| 红线清单 | | | Improved / Unchanged / Weakened | | 高/中/低 |
| 管理层质量 | | | Improved / Unchanged / Weakened | | 高/中/低 |
| 竞争护城河 | | | Improved / Unchanged / Weakened | | 高/中/低 |

**Unchanged 行的触发证据写 `—`，不要为了填表编造证据。**

#### 总体结论必须回答

1. **论文是否漂移？** 未漂移 / 正向漂移 / 负向漂移 / 证据不足无法判断
2. **漂移来自哪里？** 估值 / 基本面 / 管理层 / 竞争格局 / 红线事件
3. **是事实变化还是价格变化？** 明确拆开说明
4. **建议动作如何迁移？** 例如：Watch → Buy、Buy → Hold、Hold → Reduce、Reduce → Exit
5. **下一步需要什么证据？** 下一份财报 / 监管披露 / 管理层说明 / 竞对数据

---

## 模式B：自动快照对比

### B1：查找快照

在 `local/reports/` 中查找：
- `local/reports/{公司名}-thesis.md`
- `local/reports/{公司名}-thesis-*.md`
- `local/reports/{公司名}/` 目录下包含 `thesis`、`论文`、`追踪` 的报告

选择时间最早且结构完整的文件作为旧报告，时间最新的文件作为新报告。若用户指定日期，以用户指定为准。

### B2：防止错误配对

对比前必须确认：
- 公司名或股票代码一致
- 报告日期不同
- 两份报告都包含可抽取的论文结构或研究结论

如果无法确认同一公司，停止并要求用户提供明确路径。

### B3：执行模式A

找到两份有效快照后，按模式A完整执行。

> 🔴 STOP / 检查点：在找到两份快照并确认公司一致后、正式开始对比分析前，必须先向用户确认（给出明确选项：列出找到的旧/新报告路径与日期，用户确认后才启动逐维度对比），获得明确同意后再继续；未经确认不得自行启动全套漂移分析。

---

## 模式C：缺失基线处理

如果只找到一份报告或没有找到旧快照：

1. 明确说明：**缺少可比较的历史基线，不能执行漂移检测**
2. 不要根据记忆或市场印象补造旧论文
3. 引导用户先使用 `/thesis-tracker {公司名} 建立论文` 建立结构化基线
4. 如果当前报告已足够完整，可建议将它保存为 `local/reports/{公司名}-thesis.md` 作为未来漂移检测基线

输出格式：

```
无法执行论文漂移检测：缺少历史基线。

已找到：
- 当前报告：{路径 / 未找到}
- 历史基线：未找到

建议：
1. 先运行 /thesis-tracker {公司名} 建立论文
2. 下次有新财报或重大事件后，再运行 /thesis-drift {公司名} 旧报告 新报告
```

---

## 关键原则

- **证据优先于措辞** — 同义改写不是漂移，只有事实证据变化才是漂移
- **基本面优先于股价** — 股价涨跌只影响估值锚点，不自动改变生意质量
- **数值必须验算** — 所有百分比、估值倍数、目标价差异必须用 `tools/financial_rigor.py`
- **不确定就标注不确定** — 来源缺失、口径不一致、无法复核时，不要硬判
- **红线单独处理** — 红线触发优先级高于估值便宜，不能被低 PE 掩盖
- **输出必须可复盘** — 每个 Improved / Weakened 结论都要能追溯到具体证据

---

## 反例与红线（不要做）

- 不要用措辞差异制造漂移：同义改写、排序/语气变化但事实与判断阈值未变，必须判为 Unchanged，不得因报告换了写法就声明"论文已变"。
- 不要用股价涨跌推断基本面漂移：股价波动只影响估值锚点，不自动改变生意质量，不得将"跌了"等同"论文破裂"。
- 不要硬填 Unchanged 行的触发证据：无证据就写"—"，不得为填表编造证据。
- 不要跨公司做漂移判断：两份报告不是同一家公司时必须停止并要求用户确认，不擅自对比。
- 不要在用户未授权时落盘或外发：完整漂移检测报告写入 `local/reports/` 与发布必须经过 D4 检查点获得明确同意。

---

## 失败处理（如果 X 失败 → Y）

- 如果 报告缺少关键结构（无核心假设/红线/估值锚点）→ 执行 先标注"结构缺失"，尽量从正文抽取证据，抽取不到的维度标"无法判断"，不得编造结论。
- 如果 `tools/financial_rigor.py` 验算 ❌ 偏差过大（市值单位/口径）→ 执行 排查单位/口径后重算；仍失败则标注"数据未核验"且不在漂移判定中使用。
- 如果 找不到能解释变化的证据 → 执行 必须判定为 Unchanged 或 无法判断，不得用措辞差异推断漂移。
- 如果 只有一份报告/无历史基线（模式C）→ 执行 明确说明"缺少可比较基线，不能执行漂移检测"，引导用户先 `/thesis-tracker` 建立基线，不凭记忆补造旧论文。
- 如果 用户输入含糊/两份报告公司不一致 → 执行 停止并要求用户提供明确路径/确认同一公司，不在未确认时自选配对。

---

## Tushare 数据引用

| 命令 | 数据内容 | 用途 | 层级 |
|------|---------|------|:--:|
| `kline` | 前复权日线序列（daily + adj_factor） | 独立历史价格源——漂移检测中的估值重放依赖精确的历史价格 | 推荐 |

> **数据源优先级**：Tushare kline（前复权）为独立历史价格源，用于验证报告中引用的估值数字。

---

## 依赖与资源清单

本 Skill 依赖以下外部工具与资源（根路径 `$BERKSHIRE_ROOT=/Users/psilhon/WorkSpace/stock/berkshire`）：

| 依赖项 | 路径 | 用途 | 可达性 |
|--------|------|------|--------|
| financial_rigor.py | `tools/financial_rigor.py` | 估值/计算校验 | ✅ |

> **自检**：所有路径均为 `$BERKSHIRE_ROOT` 仓库内文件，已确认存在。新增依赖需同步更新本清单。
