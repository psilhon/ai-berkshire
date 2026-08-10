[![GitHub Trending](https://trendshift.io/api/badge/repositories/63696)](https://trendshift.io/repositories/63696)

# AI Berkshire - AI 时代的价值投资研究框架

> "Price is what you pay, value is what you get." — Warren Buffett
>
> 用 AI 重新定义投资研究的深度与效率。

**AI Berkshire** 是一套同时兼容 Claude Code 与 Codex 的投资研究 Skill 合集，将巴菲特、芒格、段永平、李录四位价值投资大师的方法论系统化、结构化，通过 AI Agent 实现专业级投资研究。

一个人 + Claude Code / Codex = 一个投研团队。

> 📮 **仓库是全量框架，公众号是精选。** 真正值得深研的公司，加上报告之外我自己的判断与取舍，都在微信公众号「**复利炼丹炉**」——[扫码关注 ↓](#精选研究首发于公众号)

[实盘业绩](#real-track-record) · [为什么不能直接问AI](#为什么不能直接问-ai) · [Skills 一览](#skills-一览13-个业务-skill--1-个编排-skill) · [快速开始](#快速开始) · [实战报告](#实战研究报告) · [设计理念](#设计理念) · [公众号](#精选研究首发于公众号)

---

## Real Track Record

> 不是纸上谈兵。这套框架背后是真金白银验证的投资体系。

### 2024 全年收益：+69.29%

### 2025 全年收益：+66.38%

### 与主要指数对比

| 指标 | 2024 全年 | 2025 全年 |
|------|----------|----------|
| **本框架实盘** | **+69.29%** | **+66.38%** |
| 恒生指数 | +17.67% | +27.77% |
| 标普500 | +23.31% | +16.39% |
| 沪深300 | +14.68% | +17.66% |
| 纳斯达克 | +28.64% | +20.36% |

**2024 年超额收益**：跑赢标普500 **46个百分点**，跑赢恒生指数 **52个百分点**

**2025 年超额收益**：跑赢标普500 **50个百分点**，跑赢恒生指数 **39个百分点**

**两年累计实盘收益超 146万元**，连续两年大幅跑赢全球主要指数。

> *免责声明：历史收益不代表未来表现。截图来自富途证券真实账户。*

### 精选研究首发于公众号

仓库里是完整的框架和全量报告，公众号里是**精选**——真正值得深研的公司，加上报告之外我自己的判断与取舍：

**复利炼丹炉** —— 用 AI 炼投研这颗丹。

---

## 为什么不能直接问 AI？

你当然可以直接问 Claude："帮我分析拼多多值不值得买"。你会得到一篇"一方面...另一方面..."的平衡分析，最后以"投资有风险，请自行判断"收尾。

**这种分析看起来对，但没法拿来做决策。**

AI Berkshire 解决的不是"能不能分析"的问题，而是**分析质量和决策纪律**的问题。以下是核心差异：

### 1. 强制给结论，不打太极

直接问AI，你得到的是两面讨好的"分析"。AI Berkshire 强制输出：**通过/不通过/灰色地带**，带具体价格区间和分层建议。

> 普通AI回答：*"拼多多有增长潜力但也面临竞争压力，投资者需要权衡..."*
>
> AI Berkshire 输出：

> | 策略 | 建议 | 价格区间 |
> |------|------|---------|
> | 激进型 | 当前价位可建仓20% | $95-105 |
> | 稳健型 | 等回购政策明确后建仓 | $85-95 |
> | 保守型 | 不符合10年确定性标准，观望 | — |
>
> **镜子测试**：5句话说不完整 = 不买，没有例外。

### 2. 四大师视角对抗，而非单一分析

不是"用巴菲特方法分析一下"这么简单。四个视角会产生**真实的矛盾和张力**——

以拼多多为例：
- **段永平**（商业模式）：好生意，C2M模式难以复制 → 评分 3.7/5
- **巴菲特**（财务估值）：扣现金PE仅6.3x，印钞机 → 评分 4.4/5
- **芒格**（逆向思考）：护城河比想象中浅，抖音3年做到4万亿GMV → 评分 3.5/5
- **李录**（长期确定性）：管理层文化有隐患，10年后不确定 → 评分 2.0/5

**巴菲特说"真便宜"，李录说"不确定就不买"**——这种冲突才是投资决策的真实状态。单一prompt无法制造这种多视角对抗，而这恰恰是避免盲点的关键。

### 3. 结构化反偏见机制

AI最危险的不是给错答案，而是给一个**看起来很对但经不起推敲**的答案。AI Berkshire 在流程中内置了多层"防骗"机制：

| 机制 | 解决什么问题 | 举例 |
|------|------------|------|
| **信息丰富度评级（A/B/C）** | 防止"资料多=确定性高"的幻觉 | 泡泡玛特评为B级：数据有限，推算指标标注置信度 |
| **芒格式逆向检验** | 强制思考失败场景 | "什么情况下拼多多会死？"→ 列出5大情景及概率 |
| **快速否决清单** | 8条红线一票否决 | 管理层诚信污点 → 直接否决，不管估值多便宜 |
| **反共识检查** | 避免和市场想法一样 | "聪明人为什么在做空？"→ 发现被忽视的风险 |
| **留白原则** | 宁可说"不知道" | 数据不足时标注"灰色地带"，不用推测伪装确定性 |

### 4. 金融数据的精确性

LLM心算不可靠。PE算错一个小数点、市值单位搞混港币和人民币，就可能导致错误的投资决策。

**真实案例**：分析腾讯时，不同来源的市值数据有"港币亿"和"人民币亿"两种单位。AI Berkshire 的处理方式：

```bash
# 市值手算校验：股价 × 总股本，与报告数据对比
python3 tools/financial_rigor.py verify-market-cap \
  --price 510 --shares 9.11e9 --reported 4.65e12 --currency HKD
# ✅ 验证通过, 偏差仅 0.08%
```

所有计算使用 Python `decimal.Decimal`（精确十进制），不用 `float`。关键数据至少2个独立来源交叉验证。

### 5. 可复现的研究流程

直接问AI，每次输出的格式、深度、覆盖面都不一样——今天分析腾讯有护城河评分，明天分析美团可能就忘了。

AI Berkshire 确保：**同样的输入 → 结构一致、深度一致的输出**。这意味着你可以：
- 7家公司横向对比，评分标准完全一致
- 同一家公司半年后重新分析，直接对比变化
- 团队成员之间的研究结果可以对齐

> 真实输出——7家公司用同一标准 Checklist 筛选：
>
> | 公司 | 通过? | 能力圈 | 好生意 | 护城河 | 管理层 | 安全边际 | 综合 |
> |------|:-----:|:------:|:------:|:------:|:------:|:-------:|:----:|
> | 茅台 | ✅ 通过 | ★★★★★ | ★★★★★ | ★★★★★ | ★★★☆☆ | ★★★★☆ | 4.7 |
> | 腾讯 | ✅ 通过 | ★★★★☆ | ★★★★★ | ★★★★★ | ★★★★★ | ★★★★☆ | 4.7 |
> | 英伟达 | ✅ 有条件 | ★★★★☆ | ★★★★★ | ★★★★★ | ★★★★★ | ★★★☆☆ | 4.3 |
> | 美团 | ✅ 有条件 | ★★★★☆ | ★★★★☆ | ★★★★☆ | ★★★★☆ | ★★★★☆ | 4.0 |
> | 快手 | ✅ 有条件 | ★★★☆☆ | ★★★★☆ | ★★★★☆ | ★★★★☆ | ★★★★★ | 4.0 |
> | 拼多多 | ❓ 灰色 | ★★★★☆ | ★★★★☆ | ★★★☆☆ | ★★★☆☆ | ★★★★★ | 3.8 |
> | 泡泡玛特 | ❓ 灰色 | ★★★☆☆ | ★★★★☆ | ★★★★☆ | ★★★★★ | ★★★☆☆ | 3.7 |

### 6. 多Agent并行 = 研究深度的倍增

`/investment-team` 启动4个独立Agent**同时**研究一家公司。每个Agent各自搜索网络、交叉验证数据、独立给出结论。这不是把一个prompt拆成四段——是4个"分析师"各自做了完整的研究，Team Lead再综合。

一个人直接问AI，上下文窗口是一个。4个Agent并行，等于4倍的搜索量、4倍的信息源、4个独立视角。

### 一句话总结

> **普通人问AI得到的是"看起来对的分析"，用 AI Berkshire 得到的是"可以拿来做决策的投研报告"。**

---

## 整体架构

**三层设计哲学**：
- **Skill 层**：16 个业务入口覆盖数据、快筛、公司、财报、行业、风险、论文与市场级/IPO 研究，另有 1 个受治理的全量编排入口（契约 13 单元）
- **Agent 层**：团队型 skill（如 `/investment-team`、`/earnings-review`）由 Team Lead 并行调度 4 个大师视角 Agent——各自独立搜索、独立判断、互相挑战，最后综合研判；轻量 skill 不经过这一层，直连工具快进快出
- **工具层**：精确计算、实时检索、报告抽检——保证每份报告的数据严谨性可验证

### 单公司全量分析（WorkBuddy）

无人值守的单公司全量流程只从 [`workbuddy-skills/full-company-analysis-workbuddy/SKILL.md`](workbuddy-skills/full-company-analysis-workbuddy/SKILL.md) 进入（lean 模式），由 WorkBuddy 原生 Agent 按 `tools/full_analysis_contract.json`（schema `full-analysis-contract/lean-v1`）执行 13 项业务契约。两条底线：**内容质量 + 失败显式 `mark-failed`**——已移除租约/重试/恢复/波次错峰等冗余机制，报告是唯一交付物。每单元移交前跑 `self-check`，Gate 在 `submit-result` 再做 substance 边界兜底（双层互不信任）；收口经 deep-summary → `register-summary` → `render-html`（确定性渲染，零 LLM）。Audit / Review / finalize 为可选评估层（L2-L4），不强制。每次运行的中间产物统一位于 `local/Company/<code>-<name>/<run-id>/evidence/`。

```bash
python3 scripts/full_analysis.py start \
  --company <公司名> --code <证券代码> --as-of <YYYY-MM-DD>
```

旧的 `scripts/run_full_analysis.py`、`scripts/batch_full_analysis.py` 和外部 `orchestrate.py` 已移除；不要恢复第二套 Python Agent 编排器。

---

## Skills 一览（16 个业务 Skill + 1 个编排 Skill）

### 🔬 深度研究类

| Skill | 用途 | 适合场景 |
|-------|------|---------|
| [`/investment-research`](skills/investment-research.md) | 四大师综合深度分析 | 对一家上市公司进行全方位投资研究 |
| [`/investment-team`](skills/investment-team.md) | 多Agent并行投研团队 | 4个Agent并行研究，最快速、最全面 |
| [`/management-deep-dive`](skills/management-deep-dive.md) | 管理层纵深研究 | "买股票就是买人"——当管理层是核心变量时深挖 |
| [`/a-share-prospectus-analysis`](skills/a-share-prospectus-analysis.md) | A股招股书深度分析 | 打新/IPO 研究：概念层六步 + 操作层九步精读（含步骤9 需求真实性检验/买单主体分解）+ 七缺口补强 + 同业横向对比 H1–H4 |

### 📊 财报分析类

| Skill | 用途 | 适合场景 |
|-------|------|---------|
| [`/earnings-review`](skills/earnings-review.md) | 财报精读（一手资料） | 只读原始财报，不依赖二手研报，像巴菲特一样读年报 |

### 🏭 行业筛选类

| Skill | 用途 | 适合场景 |
|-------|------|---------|
| [`/industry-research`](skills/industry-research.md) | 产业链全景扫描 | 研究一个行业的全部投资机会（按产业链环节切片） |
| [`/industry-funnel`](skills/industry-funnel.md) | 行业漏斗筛选 | 全市场 → 粗筛 ≤10 家 → 终选 3 家深度分析 |
| [`/quality-screen`](skills/quality-screen.md) | 去劣筛选（7条硬指标） | 快速排除非一流公司，支持个股/行业/指数/主题批量筛 |
| [`/bottleneck-hunter`](skills/bottleneck-hunter.md) | 供应链瓶颈猎手 | 从超级趋势出发，寻找产业链物理瓶颈和套利机会 |
| [`/investment-checklist`](skills/investment-checklist.md) | 巴菲特买入前 Checklist | 六关快速筛选，10分钟决定是否值得深入 |

### 📈 持仓管理类

| Skill | 用途 | 适合场景 |
|-------|------|---------|
| [`/thesis-tracker`](skills/thesis-tracker.md) | 投资论文追踪 | 买入后的纪律系统：持续跟踪论文是否被证伪 |
| [`/news-pulse`](skills/news-pulse.md) | 股价异动快速归因 | 股价大涨/大跌时10分钟搞清"发生了什么" |

### 🧠 思维工具类

| Skill | 用途 | 适合场景 |
|-------|------|---------|
| [`/financial-data`](skills/financial-data.md) | 财务数据获取与交叉验证规范 | 确保关键数据来自2个独立来源，误差>1%告警 |
| [`/ashare-data`](skills/ashare-data.md) | A股数据管线统一入口 | 行情/财务/公告/市场信号一键取数，标注来源与数据时间 |

### 📡 市场监测类（独立于全量契约）

| Skill | 用途 | 适合场景 |
|-------|------|---------|
| [`/a-share-market-sentiment`](skills/a-share-market-sentiment.md) | A股市场情绪监测 | "现在贪婪还是恐慌"：5 指标（两融/北向/估值分位/换手/开户发基）→ 评级 + 仓位分档 |
| [`/macro-liquidity`](skills/macro-liquidity.md) | 宏观流动性监测 | "钱够不够"：美元层（Fed 净流动性/SOFR/MOVE/日元套息）+ A股层（两融/北向/中债/Shibor）双水位 |

### 🎛 编排层

| Skill | 用途 | 适合场景 |
|-------|------|---------|
| [`/full-company-analysis-workbuddy`](skills/full-company-analysis-workbuddy.md) | 13 项单公司全量分析（lean 模式） | 需要契约调度、self-check、失败显式声明和总结报告/HTML 闭环；评估层按需触发 |

---

## 快速开始

### 成本与模型选择

深度投研类 Skill 默认会进行多轮研究、交叉验证和多 Agent 综合判断，因此 token 消耗较高，这是为了换取更完整的商业、财务、行业和风险分析。

如果是真实投资决策中高风险、高重要性的判断，维护者的观点是：最强模型通常更可能带来更好的分析 ROI，不建议只为节省模型成本而牺牲关键判断质量。轻量模型更适合做初筛、摘要或低风险问题；涉及护城河、估值、管理层和风险交叉判断时，应预期分析质量会更依赖模型能力。

想控制成本时，优先调整 workflow，而不是期待完整深度研究变得便宜：快速排除公司可先用 [`/quality-screen`](skills/quality-screen.md)，股价异动归因可用 [`/news-pulse`](skills/news-pulse.md)。只有当结果值得继续深入时，再运行 [`/investment-research`](skills/investment-research.md) 或 [`/investment-team`](skills/investment-team.md)。

### 1. 安装 AI 客户端

本仓库保留同一套 canonical workflow，并分别提供 Claude Code commands 与 Codex skills。按你使用的客户端安装即可。

Claude Code 用户：

```bash
npm install -g @anthropic-ai/claude-code
```

Codex 用户：

```bash
# macOS / Linux
curl -fsSL https://chatgpt.com/codex/install.sh | sh

# 或使用 npm
npm install -g @openai/codex

# 或使用 Homebrew
brew install --cask codex

# 验证安装
codex --version
```

Windows 用户可使用官方 PowerShell 安装命令：`powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"`。

如果 `codex --version` 能正常输出版本号，就可以继续安装本项目的 Codex skills。

#### 减少授权确认

这些 skills 会频繁调用工具，Claude Code 默认会逐次请求授权确认。这个行为来自 Claude Code 客户端权限机制，不是本仓库可以修改的默认设置。

如果你信任当前 workflow，并且在可信环境中运行，可以用 Claude Code 的跳过权限确认模式启动：

```bash
claude --dangerously-skip-permissions
```

注意：该模式会关闭 Claude Code 的工具审批保护，只应在你信任仓库、命令和工作目录的情况下使用。

### 2. 安装 Skills

Claude Code 用户安装（macOS / Linux）：

```bash
# 克隆仓库
git clone https://github.com/xbtlin/ai-berkshire.git

# 复制 skills 到 Claude Code 全局 commands 目录
cd ai-berkshire
./scripts/install-claude-commands.sh
```

Claude Code 用户安装（Windows PowerShell / Command Prompt）：

```bat
git clone https://github.com/xbtlin/ai-berkshire.git
cd ai-berkshire
.\scripts\install-claude-commands.bat
```

Codex 用户安装（macOS / Linux）：

```bash
# 克隆仓库
git clone https://github.com/xbtlin/ai-berkshire.git

# 生成并安装 Codex skills 到 ~/.codex/skills
cd ai-berkshire
./scripts/install-codex-skills.sh
```

Codex 用户安装（Windows PowerShell / Command Prompt）：

```bat
git clone https://github.com/xbtlin/ai-berkshire.git
cd ai-berkshire
.\scripts\install-codex-skills.bat
```

仓库维护两套入口：`skills/*.md` 是 Claude Code command 权威源；`codex-skills/*/SKILL.md` 是 Codex skill 包，由 `scripts/sync-codex-skills.py` 从 `skills/*.md` 自动生成，为 Codex 侧规范目标。

### 3. 使用

在 Claude Code 中直接调用：

```bash
# 深度研究
/investment-research 腾讯
/investment-team 美团
/management-deep-dive 王兴 美团

# 财报分析
/earnings-review 腾讯 2025Q4

# 行业筛选
/industry-research 核电
/industry-funnel AI算力
/quality-screen 恒生指数成分股
/bottleneck-hunter AI基础设施
/investment-checklist 茅台, 英伟达, 苹果

# 论文与风险
/thesis-tracker 拼多多
/news-pulse 腾讯

# 数据工具
/ashare-data 600519
/financial-data 腾讯

# 单公司全量分析
/full-company-analysis-workbuddy 格力电器 000651.SZ
```

在 Codex 中安装后重启 Codex，然后直接按 skill 名称描述任务，例如：

```text
使用 investment-research 研究腾讯
使用 earnings-review 分析 PDD 2025年报
使用 industry-funnel 筛选 AI算力
使用 bottleneck-hunter 扫描 AI基础设施瓶颈
使用 full-company-analysis-workbuddy 完整研究格力电器
```

如果安装了 Codex slash prompts，重启 Codex 后也可以在 `/` 菜单里搜索这些 prompt。Codex 官方的 custom prompt 入口通常显示为 `prompts:<name>`，例如：

```text
/prompts:investment-research 腾讯
```

---

## 各 Skill 详细介绍

各 Skill 的完整使用说明（触发场景、核心工作流、调用示例、适用/不适用边界）见 **`SKILLS-GUIDE.md`**——17 个 canonical（16 业务 + 1 编排）全登记，含选择建议路由表。此处仅保留一页速览：

| Skill | 一句话 | 详见 |
|---|---|---|
| `/ashare-data` | A 股行情/财务/公告/市场信号统一取数入口 | SKILLS-GUIDE |
| `/financial-data` | 财务数据获取与交叉验证规范 | SKILLS-GUIDE |
| `/quality-screen` | 七条硬指标去劣快筛 | SKILLS-GUIDE |
| `/investment-checklist` | 买入前快速检查 | SKILLS-GUIDE |
| `/investment-research` | 单公司四大师综合深研 | SKILLS-GUIDE |
| `/investment-team` | 高重要性公司 4 Agent 并行多视角 | SKILLS-GUIDE |
| `/management-deep-dive` | 管理层诚信/执行/资本配置纵深 | SKILLS-GUIDE |
| `/earnings-review` | 最新财报四大师精读 | SKILLS-GUIDE |
| `/industry-research` | 产业链全景与竞争格局 | SKILLS-GUIDE |
| `/industry-funnel` | 全市场分层筛选到少量候选 | SKILLS-GUIDE |
| `/bottleneck-hunter` | 产业链物理瓶颈挖掘 | SKILLS-GUIDE |
| `/news-pulse` | 股价异动四路快速归因 | SKILLS-GUIDE |
| `/thesis-tracker` | 买入后论文持续跟踪 | SKILLS-GUIDE |
| `/a-share-market-sentiment` | A 股情绪水位（契约外独立） | SKILLS-GUIDE |
| `/macro-liquidity` | 宏观流动性双水位（契约外独立） | SKILLS-GUIDE |
| `/a-share-prospectus-analysis` | 招股书三层框架深度分析（契约外独立） | SKILLS-GUIDE |
| `/full-company-analysis-workbuddy` | 13 单元单公司全量编排（lean-v1） | SKILLS-GUIDE |

---

## 实战研究报告

> 以下是使用本框架生成的真实投资研究报告，展示 AI 投研的实际输出效果。

| 公司 | 使用 Skill | 核心结论 | 报告链接 |
|------|-----------|---------|---------|
| 拼多多 (PDD) | `/investment-team` | 综合3.4/5，极度便宜但10年确定性不足，适合中等仓位 | [查看报告](local/reports/拼多多/) |
| 腾讯控股 (0700.HK) | `/investment-research` | 社交垄断+资本配置卓越，14x前瞻PE合理偏低 | [查看报告](local/reports/腾讯/) |
| 7家公司对比 | `/investment-checklist` | 茅台、腾讯通过；英伟达、美团、快手有条件通过；拼多多、泡泡玛特灰色 | [查看报告](local/reports/多公司对比-checklist-20260408.md) |
| 大师持仓追踪 | 自定义研究 | 巴菲特/李录/段永平最新13F持仓+PDD成本分析 | [查看报告](local/reports/大师持仓追踪-research-20260408.md) |

> *更多报告将持续添加。欢迎 PR 提交你用本框架生成的研究报告。*

---

## 设计理念

### 四大师方法论融合

**段永平 · "对的生意"**——商业模式本质，是其余三个视角的共同起点：

| 巴菲特 | 芒格 | 李录 |
|:---:|:---:|:---:|
| 护城河<br>安全边际<br>管理层 | 逆向思考<br>风险清单<br>偏误自查 | 文明趋势<br>范式转移<br>产业价值 |

四位大师不是简单的分工，而是设计来**互相挑战**的：
- 段永平说"好生意"，芒格会问"怎么会死"
- 巴菲特说"够便宜"，李录会问"10年后还在吗"
- 你得到的不是四份报告的拼接，而是四种思维方式的碰撞

### 金融严谨性工具 (`tools/financial_rigor.py`)

| 功能 | 命令 | 解决的问题 |
|------|------|-----------|
| **市值验算** | `verify-market-cap` | 股价×总股本 精确计算，检测单位错误 |
| **估值验算** | `verify-valuation` | PE/PB/ROE/FCF Yield 精确十进制计算 |
| **多源交叉验证** | `cross-validate` | N个来源的同一数据自动比对，超过容差告警 |
| **三情景估值** | `three-scenario` | 乐观/中性/悲观精确计算目标价 |
| **Benford定律检测** | `benford` | 检测财务数据首位数字分布异常 |
| **精确计算器** | `calc` | 任意财务表达式精确计算，替代LLM心算 |

**设计原则**：所有计算使用 Python `decimal.Decimal`（精确十进制），非 `float`（浮点近似）。`0.1 + 0.2 = 0.3` 在金融场景中不允许失败。

### A 股数据工具 (`tools/ashare_data.py`)

| 功能 | 命令 | 说明 |
|------|------|------|
| **实时行情** | `quote 600036` | 腾讯行情快照 |
| **近5年财务** | `financials 600036` | 东方财富核心财务数据 |
| **十年年度财务** | `history 600036 --years 10` | ROE、利润率、现金流质量和利息覆盖 |
| **历史股本变动** | `equity-history 600036` | 变动日期、总股本、增减股数和原因 |
| **估值指标** | `valuation 600036` | PE、PB、市值和52周区间 |
| **公告** | `announcements 600036 --limit 20` | 巨潮公告，按市场自动尝试备用源 |
| **市场信号** | `signals 600036 --date YYYY-MM-DD` | 龙虎榜、资金流、解禁、融资融券证据 |

`history` 只取年报；`equity-history` 使用独立股本变动表。财务主表中的 `TOTAL_SHARE` 是当前股本覆盖历史行的静态值，不能用于判断历史稀释。

A 股插件只提供带来源和时间标记的数据证据，不替代 `financial-data` 的双来源交叉验证，也不直接生成买入或卖出结论。主源失败时会显式记录备用源和数据不足原因。

---

## 未来方向

- [ ] 历史回测：AI研报 vs 实际股价表现
- [ ] 宏观经济周期分析框架
- [ ] 基于MCP的实时数据接入（Wind/Bloomberg/Yahoo Finance）

---

## 免责声明

本项目仅供学习和研究目的，不构成任何投资建议。投资有风险，决策需谨慎。请始终做好自己的尽职调查（DYOR）。

---

## License

MIT License

---

> "The best investment you can make is in yourself." — Warren Buffett
>
> AI Berkshire：让每个人都拥有自己的投研团队。

## Star History

如果这个项目对你有帮助，请给一个 Star 支持！精选公司研究与个人判断首发于微信公众号「**复利炼丹炉**」（二维码见[文首](#精选研究首发于公众号)）。
