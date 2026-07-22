---
name: investment-team
description: 投研团队：用 Team 工具创建真正的多 Agent 并行研究团队，四位投资方法并行分析同一公司。输入公司名，启动前强制 WebSearch 权限预检。极重量级。
owner: psilhon
category: 深度公司研究
maturity: stable
review-cadence: per-release
---

## Codex adapter note

This skill is generated from `skills/investment-team.md` so Claude Code and Codex users share one canonical workflow.

- Treat `$ARGUMENTS` as the user's request in the current Codex thread.
- When the source mentions Claude-only surfaces such as Task, Agent, WebSearch, Bash, Read, or Write, use the closest Codex capability available in this session: subagents when available, web search when needed, shell commands for local tools, and normal file edits for workspace files.
- Use shared project tools from `tools/` in this repository. Prefer running commands from the repository root with paths like `python3 tools/financial_rigor.py ...`; if the current thread starts outside the repo, locate the actual checkout path first instead of assuming a fixed home-directory path.
- Before starting research, run the `date` command to confirm today's date; treat it as the baseline for "latest" data and state the data cutoff date in the report header. Never assume the current date from training data.
- Preserve the research quality rules from `AGENTS.md`: cross-check financial data, use exact arithmetic tools for valuation/math, and clearly label uncertainty and source gaps.

# 投研团队：四位投资方法并行分析框架

对 $ARGUMENTS 进行团队化投资研究分析。使用 Team 工具创建真正的多Agent并行研究团队。

## 执行流程

### 第一步：展示团队框架

向用户展示以下团队结构，确认后启动：

| 角色 | 职责 | 分析框架 |
|------|------|----------|
| **team-lead**（你自己） | 统筹协调、汇总研判、输出最终报告 | 四大师综合框架 |
| **interpreter-duan** | 以段永平方法研究商业模式、用户价值与“不懂不做”边界 | 段永平视角 |
| **interpreter-buffett** | 以巴菲特方法研究财务质量、护城河与安全边际 | 巴菲特视角 |
| **interpreter-munger** | 以芒格方法研究行业竞争、多元模型与反向风险 | 芒格视角 |
| **interpreter-li** | 以李录方法研究管理层、下行风险与长期确定性 | 李录视角 |

### 第一步半：AI研究偏见评估

在创建团队前，先向用户展示该公司的"AI可研究性"评估：

**信息丰富度评级**（决定研究策略）：
| 等级 | 特征 | 研究策略调整 |
|------|------|------------|
| A级（信息充裕） | 上市多年、券商覆盖广 | 团队重点放在**反面检验**和**非共识视角**，避免输出与市场一致的"正确的废话" |
| B级（信息适中） | 上市不久、覆盖有限 | 每个Agent的推算数据必须标注置信度，team-lead汇总时标注"数据充分度" |
| C级（信息稀缺） | 冷门/新上市/新兴市场 | 团队转为"第一性原理模式"：不追求报告完整性，聚焦商业本质的几个核心问题 |

**关键提醒**：资料多≠确定性高，资料少≠确定性低。AI能输出的置信度 ≠ 投资的真实确定性。确定性来自商业模式本身，不来自资料数量。

将评级结果告知每个Agent，影响其研究方式。

### 第一步¾：WebSearch 权限预检（关键 · 避免 Agent 静默退化）

在创建团队、启动任何后台 Agent **之前**，必须先确认 WebSearch 权限已放行。

**为什么必须预检**：本 skill 用 `run_in_background: true` 启动 4 个后台子 Agent，而**后台 Agent 无法向用户弹出交互式权限确认**。若 `WebSearch` 未加入运行时权限白名单（如 `settings.local.json` 的 `permissions.allow`），子 Agent 的联网搜索会被**静默拦截**，导致其退化为仅凭训练知识（有知识截止日期）作答，却仍按框架输出一份"看起来完整、实则未联网"的伪研究——这是本 skill 最危险的失败模式（见 issue #58）。

**预检步骤**：
1. 用 Bash 检查白名单是否含 WebSearch：
   ```bash
   grep -l '"WebSearch"' .claude/settings.local.json ~/.claude/settings.local.json 2>/dev/null
   ```
2. 若两处都未命中（即未放行）→ **停下来，不要启动 Agent**，提示用户：
   > ⚠️ 检测到 WebSearch 未在权限白名单中。后台研究 Agent 无法联网，会退化成仅凭训练知识作答。请先在运行时权限白名单配置（如 `settings.local.json` 的 `permissions.allow`）中加入 `"WebSearch"`（或运行 `/permissions` 勾选），再重跑本命令。
3. 命中 → 正常继续。

### 第二步：创建团队

使用 TeamCreate 创建团队：
- team_name: `{公司名}-research`（英文小写，如 `meituan-research`）
- agent_type: `team-lead`

### 第三步：创建4个任务

使用 TaskCreate 创建以下4个任务（每个都要有 subject、description、activeForm）：

#### 任务1：段永平视角（商业模式分析）
- subject: `分析{公司名}商业模式、护城河与用户价值`
- description 包含：
  1. 商业模式本质：核心生意定义、收入结构拆解
  2. 平台/产品飞轮效应如何运转
  3. 护城河分析：品牌/转换成本/网络效应/规模效应/技术壁垒，逐一验证
  4. 用户/客户价值：为各方创造了什么独特价值
  5. 业务矩阵与协同效应
  6. 段永平"好生意"标准评估：差异化、定价权、可持续竞争优势
  7. 要求搜索最新财报、行业报告等公开信息

#### 任务2：巴菲特视角（财务与估值分析）
- subject: `分析{公司名}财务数据、盈利能力与估值`
- description 包含：
  1. 近3-5年营收、净利润、经营利润趋势
  2. 盈利能力指标：ROE、ROA、毛利率、经营利润率
  3. 现金流分析：经营性现金流、自由现金流、资本开支
  4. 资产负债表健康度：现金储备、负债率、流动性
  5. 估值分析：PE/PS/PB/EV等，与历史及同业对比
  6. 安全边际评估：内在价值 vs 当前股价
  7. **金融严谨性验证（必须使用Bash调用工具，禁止心算）**：
     - 市值验算：`python3 tools/financial_rigor.py verify-market-cap --price {价格} --shares {股本} --reported {报告市值} --currency {币种}`
     - 估值验算：`python3 tools/financial_rigor.py verify-valuation --price {价格} --eps {EPS} --bvps {每股净资产}`
     - 关键数据交叉验证：`python3 tools/financial_rigor.py cross-validate --field {字段} --values '{JSON}' --unit {单位}`
     - 三情景估值：`python3 tools/financial_rigor.py three-scenario --price {价格} --eps {EPS} --shares {股本亿} --growth {乐观} {中性} {悲观} --pe {乐观PE} {中性PE} {悲观PE}`
     - 将工具输出结果直接嵌入报告中作为验证记录

#### 任务3：芒格视角（行业与竞争分析）
- subject: `分析{行业}行业格局与{公司名}竞争态势`
- description 包含：
  1. 行业规模与增长：市场规模、增速、渗透率
  2. 竞争格局：主要对手市场份额、竞争策略对比
  3. 核心竞争者威胁评估：逐个分析主要竞争对手
  4. 各细分赛道格局
  5. 行业趋势：技术变革、政策影响、新进入者
  6. 产业链分析：上中下游价值分配
  7. 要求搜索最新行业数据和竞争动态

#### 任务4：李录视角（风险与管理层评估）
- subject: `评估{公司名}投资风险与管理层质量`
- description 包含：
  1. 管理层评估：CEO能力圈、诚信度、战略眼光、资本配置能力、历史决策质量
  2. 监管风险：当前及潜在监管影响
  3. 竞争风险：各竞争对手威胁程度评估
  4. 业务风险：新业务亏损、扩张不确定性
  5. 宏观风险：经济周期、行业周期影响
  6. 治理结构：股权结构、关联交易、股东回报政策
  7. 长期确定性：10年后公司会怎样？什么可能颠覆其商业模式？
  8. 要求搜索最新监管动态、管理层言论等

🔴 STOP / 检查点：在启动 4 个后台 Agent 自主联网取数前，必须先向用户确认（给出明确选项：如"确认启动团队并行研究""仅先展示任务规划不启动"），获得明确同意后再继续；未经确认不得自主执行。

### 第四步：启动4个并行Agent

使用 Task 工具同时启动4个Agent（**必须在同一条消息中并行调用**）：

每个Agent的配置：
- `subagent_type`: `general-purpose`
- `run_in_background`: `true`
- `team_name`: 对应团队名
- `name`: 对应角色名（`interpreter-duan` / `interpreter-buffett` / `interpreter-munger` / `interpreter-li`）

每个Agent的prompt模板：

```
你是{公司名}投研团队中的"{角色中文名}"，负责从{大师名}投资视角分析{公司名}。

请完成任务 #{任务编号}：{任务subject}

具体要求：
{任务description的内容}

**研究方法**：
- 使用 WebSearch 搜索最新公开信息（财报、行业报告、新闻）
- **财务数据必须来自两个独立来源**，按 `skills/financial-data.md` 规范执行（美股：macrotrends+stockanalysis；港股：aastocks+macrotrends；A股：东方财富+巨潮资讯），两源误差>1%须标记
- 确保数据准确，关键数据标注来源
- 分析要深入，不流于表面
- **联网失败禁止伪装**：若 WebSearch 被拦截/不可用，禁止用训练知识冒充联网结果。必须在报告顶部醒目标注「⚠️ 本报告未能联网，基于训练知识（截止日期 X），置信度降级」，并如实告知 team-lead，由其决定是否中止研究

**输出要求**：
- 报告要详尽，使用Markdown表格呈现关键数据
- 每个分析维度要有明确结论和评分
- 报告末尾要有该维度的总体结论
- 报告标题和发送给 team-lead 的消息必须明确标注对应的命名视角，不得只写“商业/财务/行业/风险分析”

**完成后**：
1. 使用 TaskUpdate 将任务 #{任务编号} 标记为 completed
2. 通过 SendMessage 把完整分析报告发送给 team-lead（type: "message", recipient: "team-lead"）
```

### 第五步：接收报告并跟踪进度

- 向用户实时展示进度表（哪些Agent已完成、哪些仍在研究中）
- 每收到一份报告，更新进度并展示该报告的核心要点（3-5条）
- 等待全部4份报告到齐

### 第六步：关闭团队成员

全部报告收到后，向4个Agent发送 shutdown_request（使用 SendMessage，type: "shutdown_request"）。

🔴 STOP / 检查点：在输出"买入/观望/回避"等强投资建议与分层操作建议前，必须先向用户确认（给出明确选项：如"输出完整投资结论与价格区间""仅输出四视角中性分析不荐股"），获得明确同意后再继续；未经确认不得自主替用户决定投资方向。

### 第七步：汇总最终报告

综合4份分析报告，输出以下结构的最终报告。以下四个命名视角、对照表、分歧仲裁和综合结论均为强制章节，任何一项缺失都不得视为完成：

---

#### 1. 一句话结论
> 用一段话（50-100字）概括是否值得投资及核心逻辑

#### 2. 核心数据速览
关键财务和经营指标表格（近2年对比）

#### 3. 段永平视角
围绕商业模式、用户价值、差异化、定价权和“不懂不做”边界，列出支持证据、反面证据、结论与置信度。

#### 4. 巴菲特视角
围绕财务质量、护城河、资本配置、内在价值和安全边际，列出支持证据、反面证据、结论与置信度。

#### 5. 芒格视角
围绕行业竞争、多元思维模型、激励机制、反向思考和可能导致失败的因素，列出支持证据、反面证据、结论与置信度。

#### 6. 李录视角
围绕管理层诚信与能力、资本永久损失风险、长期确定性和下行保护，列出支持证据、反面证据、结论与置信度。

#### 7. 四视角对照表
| 视角 | 核心问题 | 支持证据 | 反面证据 | 判断 | 置信度 |
|------|----------|----------|----------|------|--------|
| 段永平 | 这是不是一门好生意，是否在能力圈内？ | | | | |
| 巴菲特 | 价值是否可估，价格是否有安全边际？ | | | | |
| 芒格 | 哪些多元模型与反向因素可能改变结论？ | | | | |
| 李录 | 如何避免资本永久损失，十年确定性多高？ | | | | |

#### 8. 分歧仲裁
逐项列出四个视角之间的实质分歧、各自证据、证据质量和 team-lead 的仲裁结果。不得以多数表决代替证据判断；无法仲裁时明确保留分歧及其对结论的影响。

#### 9. 综合结论
- 综合评分与一句话判断
- Bull vs Bear 核心论点
- 巴菲特买入前 Checklist
- 生意质量、管理层、估值、时机的定性判断
- 分层操作建议与价格区间（若证据不足则明确不提供）
- 加仓/减仓信号及关键待验证问题
- 100-200字最终总结

---

🔴 STOP / 检查点：在将完整最终报告写入用户文件系统或对外发布/外发前，必须先向用户确认（给出明确选项：如"确认写入~/{公司名}投资研究报告_YYYYMMDD.md""仅生成草稿供预览不落盘"），获得明确同意后再继续；未经确认不得自主落盘或外发。

### 第八步：保存报告

将完整最终报告写入 `~/{公司名}投资研究报告_{日期}.md`（日期格式 YYYYMMDD）。

### 第九步：数据抽检（准出流程）

```bash
# Step 1 — 提取抽检清单（15%随机抽样）
python3 tools/report_audit.py extract \
  --report <报告文件路径>

# Step 2 — 对清单每项从可靠信源取数（参见 skills/financial-data.md）

# Step 3 — 输出三态判决（准出/证据不足/打回）
python3 tools/report_audit.py verdict \
  --results '<填好的JSON>' \
  --report <报告文件名>
```

**【准出】** 全部抽检点双源核验通过 → 可发布；**【证据不足】** 有未核验/单一来源/两源冲突点 → 补齐第二来源后重跑；**【打回】** 有不通过 → 修正后重审。

### 第十步：清理团队

使用 TeamDelete 清理团队资源。

## 重要注意事项

1. **4个Agent必须并行启动**——在同一条消息中调用4次Task工具
2. **Agent通过SendMessage汇报**——不是文件协作，是消息通信
3. **数据准确性**——要求Agent使用WebSearch搜索最新数据，关键数据交叉验证
4. **结论要明确**——不回避给出买入/观望/回避建议和具体价格区间
5. **所有分析必须有数据支撑**——附数据来源
6. **耐心等待**——4个Agent研究需要几分钟，实时向用户更新进度
7. **反偏见意识**——team-lead在汇总时必须评估：各Agent的分析是否受限于资料充裕度？是否与市场共识过度趋同？最终报告需包含"信息丰富度评级"和"AI研究局限性声明"
8. **信息稀缺时的诚实原则**——宁可在报告中留白标注"数据不足"，也不要用推测填满框架伪装确定性
9. **四视角不可合并**——商业、财务、行业、风险是分析维度，不是最终角色名；最终报告必须分别出现段永平、巴菲特、芒格、李录四个命名章节，并完成对照与仲裁

---

## 反例与红线（不要做）

- 不要绕过 WebSearch 权限预检直接启动后台 Agent：未放行的 Agent 会静默退化为仅凭训练知识作答，输出"伪研究"——这是本 skill 最危险的失败模式。
- 不要合并四视角：最终报告必须分别出现段永平、巴菲特、芒格、李录四个命名章节，不得用"商业/财务/行业/风险"维度名替代角色名。
- 不要用多数表决代替证据仲裁：四视角有分歧时，以证据质量仲裁，无法仲裁时明确保留分歧及其对结论的影响。
- 不要用推测填满框架伪装确定性：C级信息稀缺公司应留白标注"数据不足"，不得编造数据。
- 不要在用户未授权时外发或落盘：完整最终报告写入 `~/` 与发布必须经过 D4 检查点获得明确同意。

---

## 失败处理（如果 X 失败 → Y）

- 如果 WebSearch 未加入运行时权限白名单（如 `settings.local.json` 的 `permissions.allow`）→ 执行 停下来不启动 Agent，提示用户先在白名单中加入 `"WebSearch"`（或运行 `/permissions`），再重跑；不静默降级。
- 如果 某个后台 Agent 超时/拒答/无返回 → 执行 向用户展示已收到的报告并标注缺失维度，必要时主代理补做该维度研究，不假装"完整"。
- 如果 Agent 报告"联网失败"（WebSearch 被拦截）→ 执行 要求该 Agent 在报告顶部标注「⚠️ 本报告未能联网，基于训练知识（截止日期 X），置信度降级」，由 team-lead 决定是否中止。
- 如果 `tools/financial_rigor.py` 验算 ❌ 偏差过大 → 执行 排查单位/口径错误后重算；仍失败则标注"数据未核验"且不在结论中使用。
- 如果 `tools/report_audit.py` 抽检【打回】/【证据不足】→ 执行 补齐第二来源或修正后重跑直到【准出】；不强行发布。
- 如果 用户输入含糊/多义 → 执行 列出 2-3 个可能的公司/范围解读请用户澄清，不在未确认时自选展开。

---

## 依赖与资源清单

本 Skill 依赖以下外部工具与资源（根路径 `$BERKSHIRE_ROOT=/Users/psilhon/WorkSpace/stock/berkshire`）：

| 依赖项 | 路径 | 用途 | 可达性 |
|--------|------|------|--------|
| financial_rigor.py | `tools/financial_rigor.py` | 财务数据校验、三情景建模 | ✅ |
| report_audit.py | `tools/report_audit.py` | 报告质量把关 | ✅ |
| WebSearch 权限 | 运行时配置（settings.local.json） | Agent 自主搜索能力 | ⚠️ 需运行时白名单 |

> **自检**：所有路径均为 `$BERKSHIRE_ROOT` 仓库内文件，已确认存在。新增依赖需同步更新本清单。
