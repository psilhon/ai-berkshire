---
name: earnings-review
description: 财报精读：基于一手原始财报对单一公司做深度解读，不依赖卖方研报。输入“公司名 季度”（如“腾讯 2025Q4”“美团 最新”），产出结构化财报解读。
owner: psilhon
category: 财报分析
maturity: stable
review-cadence: per-release
---

## Codex adapter note

This skill is generated from `skills/earnings-review.md` so Claude Code and Codex users share one canonical workflow.

- Treat `$ARGUMENTS` as the user's request in the current Codex thread.
- When the source mentions Claude-only surfaces such as Task, Agent, WebSearch, Bash, Read, or Write, use the closest Codex capability available in this session: subagents when available, web search when needed, shell commands for local tools, and normal file edits for workspace files.
- Use shared project tools from `tools/` in this repository. Prefer running commands from the repository root with paths like `python3 tools/financial_rigor.py ...`; if the current thread starts outside the repo, locate the actual checkout path first instead of assuming a fixed home-directory path.
- Before starting research, run the `date` command to confirm today's date; treat it as the baseline for "latest" data and state the data cutoff date in the report header. Never assume the current date from training data.
- Preserve the research quality rules from `AGENTS.md`: cross-check financial data, use exact arithmetic tools for valuation/math, and clearly label uncertainty and source gaps.

# 财报精读：一手资料深度解读

对 $ARGUMENTS 进行财报精读分析。

**支持输入格式**：`公司名 季度`，例如：`腾讯 2025Q4`、`PDD 2025年报`、`美团 最新`（默认读取最近一期）

> "我从不看卖方研报，只读原始财报。" —— 李录
>
> "我每天读500页。知识就是这样积累的，像复利一样。" —— 巴菲特

## 设计理念

大多数AI投研工具依赖二手信息（新闻、研报摘要、数据网站）。但巴菲特和李录的核心能力是**读一手资料**——年报、季报、电话会纪要。

二手信息的问题：
- 被筛选过——分析师选择性呈现对其观点有利的数据
- 有时滞——等别人消化完，alpha已经没了
- 缺乏语境——"收入增长15%"脱离了管理层对增长质量的讨论

本Skill直接解读一手资料，关注巴菲特和李录真正会看的内容。

## 执行流程

### 前置步骤：资料可得性评级

| 等级 | 特征 | 影响 |
|------|------|------|
| A级 | 获取到完整原文（10-K/年报/电话会纪要） | 正常执行全部步骤 |
| B级 | 仅获取到部分原文或第三方汇总 | 标注"非原始来源"，降低附注分析权重 |
| C级 | 仅有新闻报道和数据网站摘要 | 聚焦核心财务数据变化，跳过附注挖掘，标注"一手资料不足" |

🔴 STOP / 检查点：在启动后台 Agent 并行获取一手财报/电话会/股东信等原始材料前，必须先向用户确认（给出明确选项：如"确认启动取数""仅先列资料清单不抓取"），获得明确同意后再继续；未经确认不得自主派发后台 Agent 抓取外部数据。

### 第一步：获取一手资料

使用 Task 工具启动多个后台 Agent **并行**获取以下原始材料：

1. **财报原文**：从公司IR页面、SEC EDGAR（美股10-K/10-Q）、港交所披露易（港股）、巨潮资讯网（A股）获取
2. **业绩电话会纪要/录音**：从 Seeking Alpha、公司IR页面、雪球等获取
3. **管理层致股东信**（如有年报）：完整阅读
4. **投资者日/分析师日材料**（如近期有）

如果无法获取完整原文，按 `skills/financial-data.md` 规范使用标准数据源拼凑（美股：macrotrends+stockanalysis；港股：aastocks+macrotrends；A股：东方财富+巨潮资讯），但必须标注"非原始财报，来自第三方汇总"，且关键数据两源误差>1%须标记。

### 第二步：核心财务数据提取与验证

#### 2.1 收入与利润表

| 指标 | 本期 | 上期 | YoY变化 | 管理层指引 | 是否达标 |
|------|------|------|---------|-----------|---------|

必须覆盖：
- 总收入及分业务/分地区收入拆解
- 毛利润、毛利率变化
- 经营利润、经营利润率变化（区分GAAP和Non-GAAP）
- 净利润（注意非经常性损益的影响）
- EPS（基本 vs 稀释）

#### 2.2 现金流表（巴菲特最看重）

| 指标 | 本期 | 上期 | 变化 | 关注点 |
|------|------|------|------|--------|

必须覆盖：
- 经营性现金流 vs 净利润的比率（>100%为佳，<80%需警惕）
- 资本开支及其构成（维护性 vs 扩张性）
- 自由现金流 = 经营现金流 - 资本开支
- 回购金额、分红金额
- 现金及等价物期末余额

#### 2.3 资产负债表健康度

必须覆盖：
- 现金+短期投资 vs 有息负债
- 净现金/净负债变化趋势
- 应收账款周转天数变化（是否在放松信用条件冲收入？）
- 存货周转天数变化（是否在积压？）
- 商誉及无形资产占比（是否有减值风险？）

**数据验证**：使用 `tools/financial_rigor.py` 对关键数据进行校验：

```bash
# 收入和净利润交叉验证（至少2个来源）
python3 tools/financial_rigor.py cross-validate \
  --field revenue --values '{"公司财报": 108.3e9, "Yahoo Finance": 107.9e9}'

# 市值校验
python3 tools/financial_rigor.py verify-market-cap \
  --price 101 --shares 1.488e9 --reported 1.44e11 --currency USD

# 估值指标验算
python3 tools/financial_rigor.py verify-valuation \
  --price 101 --eps 9.6 --bvps 26.5 --fcf-per-share 10.2
```

### 第三步：管理层讨论精读（MD&A）

这是巴菲特和李录花最多时间的部分。不是看数字，是**听管理层怎么说**。

#### 3.1 管理层语气分析

逐段阅读管理层讨论/电话会发言，标注以下信号：

| 信号类型 | 具体表现 | 示例 |
|---------|---------|------|
| 🟢 **坦诚信号** | 主动承认问题、给出具体原因 | "本季度利润率下降主要因为我们在X领域的投入超出预期" |
| 🟢 **清晰信号** | 战略表述具体、有量化目标 | "我们计划在未来12个月将X业务的市场份额从15%提升到20%" |
| 🔴 **模糊信号** | 大量使用"我们相信"、"长期来看"等没有实质内容的话 | "我们对未来充满信心" |
| 🔴 **转移信号** | 回避直接问题、用其他话题带过 | 被问利润率时转谈收入增速 |
| 🔴 **归因外部化** | 把问题全归咎于宏观/行业/竞争对手 | "由于宏观环境影响..." |

#### 3.2 承诺追踪

从上一期财报/电话会中提取管理层的具体承诺，与本期实际情况对比：

| 上期承诺 | 本期兑现情况 | 评价 |
|---------|------------|------|
| "下半年利润率将恢复到X%" | 实际Y% | ✅达标 / ❌未达标 / ⚠️部分达标 |

**段永平**："看一个管理层靠不靠谱，最简单的方法就是看他以前说的话做到了没有。"

#### 3.3 关键问题识别

从电话会Q&A环节提取分析师最尖锐的问题，以及管理层的回答质量：

| 分析师问题 | 管理层回答 | 回答质量(1-5) | 是否回避 |
|-----------|-----------|:------------:|:-------:|

### 第四步：附注与隐藏信息挖掘

财报附注里藏着管理层不想让你轻易看到的信息：

#### 4.1 必查附注项

- [ ] **关联交易**：与大股东/关联方的交易条款是否公允？
- [ ] **股权激励**：期权/RSU的稀释效应有多大？行权价是多少？
- [ ] **或有负债**：诉讼、担保、承诺等表外风险
- [ ] **会计政策变更**：是否改变了收入确认方式、折旧年限等？
- [ ] **分部信息**：不同业务的利润率差异，是否有"好业务补贴坏业务"
- [ ] **客户/供应商集中度**：前五大客户/供应商占比

#### 4.2 异常信号检测

- [ ] 应收账款增速 > 收入增速（可能在塞渠道）
- [ ] 存货增速 > 收入增速（可能在积压）
- [ ] 经营现金流 < 净利润且差距扩大（利润质量存疑）
- [ ] 资本化开支突然增加（可能在美化利润）
- [ ] 非经常性收益占比突然上升

### 第五步：与历史数据对比

#### 5.1 趋势分析

将本期关键指标放入至少4个季度（或3年年报）的时间序列中：

| 指标 | Q-4 | Q-3 | Q-2 | Q-1 | 本期 | 趋势判断 |
|------|-----|-----|-----|-----|------|---------|

重点关注：
- 利润率是在改善还是恶化？
- 收入增速是在加速还是减速？
- 现金流质量是在提升还是下降？
- 资本开支强度是在增加还是减少？

#### 5.2 与管理层指引对比

| 指标 | 管理层此前指引 | 实际结果 | 偏差 | 解读 |
|------|--------------|---------|------|------|

🔴 STOP / 检查点：在输出"加仓/持有/减仓"等强结论与对投资论文的影响判定前，必须先向用户确认（给出明确选项：如"输出完整结论""仅输出中性事实分析不荐股"），获得明确同意后再继续；未经确认不得自主替用户决定投资方向。

### 第六步：输出精读报告

#### 报告结构

```
一、核心数据速览（一页表格）
二、本期最重要的3个变化（不超过500字）
三、管理层语气与承诺追踪
四、附注中的隐藏信息
五、关键问题（电话会Q&A精选）
六、与投资论文的关系（如有持仓）
七、结论：这份财报改变了什么？
```

#### 结论必须明确回答

1. **这份财报是超预期、符合预期、还是低于预期？**（不能说"基本符合"然后列一堆两面话）
2. **对投资论文的影响**：强化 / 无影响 / 削弱 / 破裂
3. **需要关注的下一个催化剂是什么？**
4. **如果你已持有，该加仓/持有/减仓？**

🔴 STOP / 检查点：在将精读报告写入 `local/reports/{公司名}-earnings-{期间}.md` 前，必须先向用户确认（给出明确选项：如"确认写入并标注来源""仅对话内呈现不落盘"），获得明确同意后再继续；未经确认不得自主落盘。

### 第七步：保存报告

将报告写入 `local/reports/{公司名}-earnings-{期间}.md`，例如 `local/reports/腾讯-earnings-2025Q4.md`

🔴 STOP / 检查点：在跑 `report_audit.py` 准出并对外发布报告前，必须先向用户确认（给出明确选项：如"确认发布""仅生成草稿待审阅"），获得明确同意后再继续；未经确认不得自主发布。

### 第八步：数据抽检（准出流程）

报告写入后，执行数据抽检，通过方可发布：

```bash
# Step 1 — 提取抽检清单
python3 tools/report_audit.py extract \
  --report local/reports/{公司名}-earnings-{期间}.md

# Step 2 — 对清单每项从两个独立信源取数（参见 skills/financial-data.md）

# Step 3 — 输出三态判决（准出/证据不足/打回）
python3 tools/report_audit.py verdict \
  --results '<填好的JSON>' \
  --report {报告文件名}
```

**【准出】** 全部抽检点双源核验通过 → 可发布；**【证据不足】** 有未核验/单一来源/两源冲突点 → 补齐第二来源后重跑；**【打回】** 有不通过 → 修正后重审。

## 关键原则

- **读原文，不读摘要**：尽一切可能获取一手资料
- **看变化，不看绝对值**：趋势比数字本身重要
- **听语气，不只听内容**：管理层怎么说和说了什么一样重要
- **查附注，不只看正文**：魔鬼藏在细节里
- **给结论，不做汇总**：精读的目的是形成判断，不是复述财报

---

## 反例与红线（不要做）

- 不要用卖方研报替代一手资料：核心结论须基于原始财报/电话会，第三方汇总须标"非原始来源"，关键数据两源误差>1%须标记。
- 不要编造管理层语气/承诺追踪：未获取电话会纪要时不得虚构管理层原话或承诺兑现情况。
- 不要在数据未双源核验时就下"超预期/低于预期"结论：结论必须建立在交叉验证通过的数据上。
- 不要跳过附注与隐藏信息挖掘（关联交易/股权激励/或有负债）直接给结论。
- 不要在用户未授权时把精读报告落盘或对外发布：写入 `local/reports/` 与准出发布须经 D4 检查点。

---

## 失败处理（如果 X 失败 → Y）

- 如果 后台 Agent 获取一手资料超时/拒答 → 执行：按 financial-data 规范用标准数据源（macrotrends/stockanalysis/aastocks/东财/巨潮）拼凑，标"非原始来源"降级。
- 如果 `financial_rigor.py` cross-validate / verify-market-cap 报错 → 执行：检查输入数值单位与币种后重试，不把报错当"已验证"。
- 如果 `report_audit.py` 判【证据不足】→ 执行：补齐第二来源后重跑，不强行发布。
- 如果 `report_audit.py` 判【打回】→ 执行：修正错误点后重审，不绕过审计直接发布。
- 如果 用户输入含糊（如"最新"未指定期数）→ 执行：按最近一期处理并明确告知所取期间，或请用户确认具体季度。

---

---

## Tushare 数据引用（付费数据源，优先级高于 WebSearch）

本 skill 进行财报精读时应优先使用以下 Tushare 命令获取原始财务报表数据。东财 financials 仅作快速概览，Tushare 为独立交叉验证源。

| 命令 | 数据内容 | 在本 skill 中的使用方式 | 层级 |
|------|---------|----------------------|:--:|
| `income-stmt` | 利润表原始数据（85 列，54 期） | 扣非利润、营业利润、投资收益、各项费用逐项验证 | **必须** |
| `express` | 业绩快报（正式财报前的早期信号） | 提前捕捉业绩变化方向 | 推荐 |
| `consensus` | 业绩预告（公司自愿披露的盈利指引） | 实际利润 vs 公司指引的偏差分析 | 推荐 |
| `disclosure-calendar` | 财报披露日期表 | 确认财报发布日期、避免使用未披露季度的数据 | 可选 |

> **数据源优先级**：Tushare income 原始报表 > 东财 financials 聚合数据 > WebSearch。东财和 Tushare 的利润数据存在差异时以 Tushare 为准。
> **执行方式**：在 `full-company-analysis` 流程中，`income-stmt` 通过 `tushare-enrich` 在 L2 执行前补跑。`express`/`consensus`/`disclosure-calendar` 同为条件 Tushare 操作。

## 依赖与资源清单

本 Skill 依赖以下外部工具与资源（根路径 `$BERKSHIRE_ROOT=/Users/psilhon/WorkSpace/stock/berkshire`）：

| 依赖项 | 路径 | 用途 | 可达性 |
|--------|------|------|--------|
| financial_rigor.py | `tools/financial_rigor.py` | 财务数据验证（交叉核验/市值自洽验算） | ✅ |
| report_audit.py | `tools/report_audit.py` | 报告质量门（审计/证据充分性/打回重审） | ✅ |

> **自检**：所有路径均为 `$BERKSHIRE_ROOT` 仓库内文件，已确认存在。新增依赖需同步更新本清单。
