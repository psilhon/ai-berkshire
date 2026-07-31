# ashare-data 分级执行升级规划

> **状态**：✅ **项目收口（2026-08-01）**。Phase 0 + Phase 1 已交付（2026-07-31）；Phase 6 注册表复验已完成；**打板三件套（limit-pool/monitor-pool/anomaly-pool）需求拉动闭环已交付（2026-07-31）**；**热度层 `ths-hot`（L2）需求拉动闭环已交付（2026-07-31）**；**L3 三件套（ird-interact/cls-telegraph/report-list）需求拉动闭环已交付（2026-08-01，零鉴权免费源，由消费方 skill 调用）**。**全部 7 个需求拉动候选已交付**（L2 三件套+热度层、L3 互动易/财联社/研报），`LEVEL_PENDING_LAYERS` 三级均清空；归级声明 L2/L3 全部"部分就位"，run-level 仍仅跑 L1 快查不代跑 L2/L3（ADR-003）。**G3「L1 行为不变」经取数契约面静态交叉验证通过（§6.1.5）；产物哈希级真机回放经用户 2026-08-01 决定，留待下次实跑某公司时顺带完成（非阻塞收尾项，不单独开重管线）。**
> **决策基线**：A+C 组合（契约先行 → 骨架先行 → 命令按需求拉动）
> **文档修订**：2026-07-31 第二版。第一版的 L0/L1 定义、run-level 定位、增量命令清单均被拷问推翻并重写，详见第 9 节 ADR。
> **数据基准日**：2026-07-31
> **来源**：本 skill 源自 GitHub `simonlin1212/a-stock-data`，本次升级以 a-stock-data V3.6.0 为增量参照
> **仅供学习研究，不构成投资建议**

---

## 1. 背景与目标

### 1.1 现状

ashare-data 是本仓库 A 股数据的唯一切入点（`tools/ashare_data.py` + `tools/ashare_plugin/`），当前形态：

- **52 个子命令**：8 个免费源命令（腾讯/东财/新浪/巨潮/深交所，curl 零依赖直连）+ 44 个 Tushare 主源命令。
- **插件架构**：`DataResult` 统一契约（`ok/data/source/fallback_used/as_of/warnings/verification`）+ `FallbackChain` 三层降级 + Tushare 双角色（交叉验证 + 专属主源）。
- **消费方式**：被 `full-company-analysis` 通过 `tools/full_analysis_contract.json` 注册表（`skill_id: ashare-data`）驱动，gate 的 `run-ashare-command` **逐条执行并冻结收据**（`commands/<cmd>.stdout` 一命令一文件）。

**核心痛点**：52 个命令是**扁平集合**，缺乏声明式的"取数广度"标签，导致：
- 编排器与消费 skill 无法用一句话声明"取到哪一档"；
- skill 文件需要 🔴 STOP 检查点防止 Agent 擅自扩大取数范围；
- 新能力（来自 a-stock-data V3.6.0）没有归属位置，加进来就是扁平集合再变长。

### 1.2 升级目标

把 ashare-data 从"扁平命令集"改造为**带分级标签的数据管线**：

1. `full-company-analysis` 的取数行为**保持现状不变**（由编排器 feeds 映射驱动，不被本次升级改写）；
2. standalone 快查有明确的轻量档（L0），人工调研有明确的扩展档（L2/L3）；
3. a-stock-data V3.6.0 的增量能力有明确归属级别与**准入门槛**（需求拉动，非批量预建）；
4. 任何时刻管线保持内部一致——本项目"完整性/正确性/内部一致性不可协商"纪律的硬性要求。

### 1.3 与 ROADMAP 的关系（正交澄清）

`docs/ROADMAP.md` P1 已规划"多档深度模式（lite/standard/deep）"。两者**正交，不冲突**：

| | lite/standard/deep | L0/L1/L2/L3 |
|---|---|---|
| **维度** | 分析**深度**（交叉验证强度、历史类比） | 取数**广度**（覆盖哪些数据层） |
| **作用层** | full-company-analysis 编排层 | ashare-data 数据管线层 |
| **关系** | 任一深度模式都可声明取数级别 | 任一取数级别都可服务于任一深度 |

本规划只处理取数广度分级，不触碰深度模式。

---

## 2. 分级模型（L0–L3）

> **根定义（ADR-001/003 确立）**：级别是**声明式契约（名词）**，描述"这一档包含哪些数据层"；级别**不是执行式封装（动词）**，不代表"一个命令打包跑完"。

```
┌──────────────────────────────────────────────────────────────┐
│  L3 · FULL（全量侦察）                                         │
│  = L2 + 一手定性 / 快讯 / 研报层（候选，需求拉动）              │
│  触发：人工 standalone 深度调研                                │
├──────────────────────────────────────────────────────────────┤
│  L2 · ENHANCED（增强信号）                                     │
│  = L1 + 涨停生态 / 监管监控 / 异动 / 热度层（候选，需求拉动）    │
│  触发：人工 standalone 情绪与治理风险扫描                       │
├──────────────────────────────────────────────────────────────┤
│  L1 · CORE（默认级别）◄── full-company-analysis                │
│  = 编排器 feeds 映射决定的全量取数（动态，非固定清单）           │
│  触发：full-company-analysis / investment-research 管线         │
├──────────────────────────────────────────────────────────────┤
│  L0 · QUICK（快查）                                            │
│  = 概览三件套 quote + valuation + financials                   │
│  触发：对话中快查 / standalone 默认行为                         │
└──────────────────────────────────────────────────────────────┘

跨级步骤：search —— 输入为公司名时自动前置定码，输入为六位代码时不触发。
          它是输入归一化，不属于任何级别。
```

### 2.1 各级命令集

| 级别 | 命令集 | 定义方式 |
|------|--------|---------|
| **L0 QUICK** | `quote` `valuation` `financials` | **静态清单**。等于 ashare-data skill 现有的 standalone 默认行为 |
| **L1 CORE** | 由编排器 feeds 映射动态决定（实测区间 12–27 条，稳定含 `quote` `valuation` `financials` `history` `equity-history` `signals` `announcements` `ratios` `mainbz` `managers` `peers`） | **动态**。full-company-analysis 跑什么，L1 就是什么 |
| **L2 ENHANCED** | L1 全部 + 涨停生态/监管监控/异动/热度层（见 §3 候选清单） | **候选**。命令随消费方需求拉动交付 |
| **L3 FULL** | L2 全部 + 一手定性/快讯/研报层（见 §3 候选清单） | **候选**。同上 |

**级别是包含关系，非互斥**：L1 ⊇ L0 的数据层，L2 ⊇ L1，L3 ⊇ L2。

### 2.2 L1 CORE 的正确理解（回归基准）

第一版文档把 L1 定义成一张"7 个免费源命令"的固定清单，**该定义已被真实运行数据证伪**：

| 证据 | 实际执行命令数 | 关键发现 |
|------|:--:|---------|
| 风华高科 `000636.SZ` 真实运行 | 12 | 含 `ratios` `mainbz` `managers` `peers` |
| 雅克科技 `002409.SZ` 真实运行 | 27 | 含上述 4 条 + `audit` `pledge` `north-hold` 等 15 条 |

两个结论：
1. 原 L1 清单**漏了两次运行都出现的 4 条命令**——按原清单实施会导致 full-company-analysis **静默降级**（丢掉财务比率全景、主营构成、管理层、可比公司池）。
2. "现状"**不是一个固定点**——取数范围由编排器 feeds 映射按公司动态决定，无法逐字节 diff。

这与 `skills/ashare-data.md` 第 200 行 🔴 规则一致：**全量分析语境下取数范围由编排器决定，包括全部模块。**

**因此 L1 的回归基准不是"命令清单 diff 为空"，而是**：

> **feeds 映射规则不变 ⇒ 命令集不变。** 本次升级不修改编排器 feeds 映射的任何逻辑，L1 的取数行为因此在结构上不可能改变。

---

## 3. 增量能力评估（来自 a-stock-data V3.6.0）

### 3.1 候选命令清单（7 个，需求拉动；打板三件套已交付，余 4 个待拉动）

> **准入门槛（ADR-004）**：每个新命令必须与它的**具体消费方 skill 接线**一起交付。没有真实消费方的命令不建。本表是能力储备清单与触发条件，不是本次升级的交付任务。

| 候选命令 | 级别 | 数据源 | 复杂度 | 价值 | 触发条件（谁提出需求才启动） |
|--------|:--:|--------|:--:|------|------------------|
| `limit-pool` | L2 | 东财 push2ex | 中 | 涨停/炸板/跌停/昨涨停生态，市场情绪证据 | ✅ **已交付 2026-07-31**（quality-screen 接入，作 L2 情绪旁证） |
| `monitor-pool` | L2 | 东财 push2ex | 低 | 交易所重点监控名单，治理红线信号 | ✅ **已交付 2026-07-31**（quality-screen 接入，作 L2 治理旁证） |
| `anomaly-pool` | L2 | 东财 push2ex（list+count） | 中 | 严重异常波动 + 12 条规则码 | ✅ **已交付 2026-07-31**（quality-screen 接入，作 L2 治理旁证） |
| `ths-hot` | L2 | 同花顺热榜(GET) → 东财人气榜(POST)（零依赖）；Tushare `ths_hot` 备用 | 低 | 市场热度人气榜（排名/人气值/概念标签/排名变化）；同花顺反爬，故东财同优先级备用 | ✅ **已交付 2026-07-31**（news-pulse 接入，作 L2 热度旁证；quality-screen 同步为四件套旁证；curl 优先 + Tushare 备用） |
| `ird-interact` | L3 | 巨潮互动易 | 低 | 投资者提问 + 公司官方回复，一手定性证据 | ✅ **已交付 2026-08-01**（management-deep-dive 接入，作 L3 一手定性旁证；两步 POST + 本地零鉴权） |
| `cls-telegraph` | L3 | 财联社 v1（本地签名） | 中 | 全市场实时快讯 | ✅ **已交付 2026-08-01**（news-pulse 接入，作 L3 快讯旁证；本地签名 md5(sha1) 零 key） |
| `report-list` | L3 | 东财 reportapi | 中 | 个股/行业研报列表 + 评级 | ✅ **已交付 2026-08-01**（investment-research 接入，作 L3 研报旁证，补 Tushare `analyst-reports` 免费源） |

**已修正的错误**：第一版把 `sector-flow` 列为"新增命令"，实为**误判**——`sector-flow` 已存在于现有 44 个 Tushare 命令中（`moneyflow_ths` / `moneyflow_dc`），且 `industry-research.md` 第 287 行已在引用。若未来需要给它补免费源（东财 push2），单立小任务，不属于"新增命令"。

### 3.2 不引入（与零依赖原则 / 价值投资范式冲突）

| 能力 | 不引入理由 |
|------|-----------|
| mootdx TCP 直连 | 违反零依赖；需 pip；海外不可用；上游库已停更 |
| ETF 期权层 | 交易工具非研究工具，与价值投资范式无关 |
| iwencai NL 搜索 | 需 API Key，违反零鉴权；WebSearch 已覆盖 |
| 百度K线 | Tushare `kline` 已是独立历史价源；百度 ResultCode 不稳定 |
| stockstats 技术指标 | 引入外部依赖；价值投资不依赖技术指标 |

### 3.3 复用 a-stock-data V3.6.0 的修正经验（候选命令实施时适用）

- **北交所 920 号段迁移**：新命令复用 `ashare_plugin/identifiers.py` 的 `normalize_code()`；老号段（43/83/87）先 `search` 拿现行 920 码。
- **东财 push2ex 风控**：打板类命令共用一套风控，复用 `TransportClient` 重试 + 指数退避。
- **北交所市场标识**：监控池/异动池接口中北交所与深市同为 `m=0`，需按 V3.6 修正市场判定（MARKET="B"）。

---

## 4. 实现机制（双层落地：CLI 层 + Skill 文件层）

> 注：此处"双层"指实现载体（CLI 与 skill 文件），与第 9 节改造路线的 A+C 组合是不同维度，勿混淆。

### 4.1 CLI 层：`run-level` 元命令（**仅服务 standalone 快查**）

```bash
python3 tools/ashare_data.py run-level 600519 --level quick     # L0 概览三件套
python3 tools/ashare_data.py run-level 600519 --level enhanced  # L2（候选命令就位后）
python3 tools/ashare_data.py run-level 600519 --level full      # L3（候选命令就位后）
```

**硬边界（ADR-003）**：

- `run-level` **不进 full-company-analysis 主管线**。主管线维持 gate 逐条 `run-ashare-command` 冻结，**命令级血缘一条不丢**。
- 不提供 `--level core`。L1 由编排器 feeds 映射驱动，把它封装成一个固定清单的元命令，等于把动态决策焊死，与 ADR-001 冲突。
- `run-level` 内部仍是逐条调用子命令，**每条命令的成败与警告必须逐条呈现**，不得聚合成模糊的"统一报告"（保护 `signals` 的部分成功语义）。
- 每个子命令仍可独立调用（向后兼容）。

被拒绝的设计及理由见 ADR-003：让 `run-level` 进主管线会造成血缘塌缩（N 条可追溯命令 → 1 个黑箱）、部分成功语义丢失、与 L1 动态定义矛盾。

### 4.2 Skill 文件层：分级声明表

`skills/ashare-data.md` 增加 L0–L3 声明表（含"L1 由编排器决定"的说明与 `search` 跨级定位），供编排器与 Agent 参考。消费 skill 在调用时声明所需级别。

**此处是分级定义的唯一权威源（single source of truth）。**

### 4.3 注册表层：只放指针

`tools/full_analysis_contract.json` 中 ashare-data 注册块增加 `"default_level": "core"` —— **只是一个级别标签指针，不重复任何命令清单**。编排器读到 `core` 后，去 `spec_source`（已指向 `skills/ashare-data.md`）读语义。

注册块当前位置：`/skills[0]`（`skill_id: ashare-data`，`artifact.min_bytes: 3000`，`audit_policy: advisory`），升级不改动这些既有字段。

---

## 5. 可验证目标（pass/fail 二元判定）

| # | 目标 | Pass 判定 |
|---|------|----------|
| **G1** | 分级契约落地 | `skills/ashare-data.md` 含 L0–L3 声明表（含 L1 动态说明 + `search` 跨级定位）；`full_analysis_contract.json` 中 ashare-data 含 `default_level: "core"` 且不含重复命令清单；`bash scripts/check.sh` 全绿 |
| **G2** | `run-level` 快查可用 | `run-level 600519 --level quick` 退出码 0，输出等价于 `quote`+`valuation`+`financials` 手工串跑；`--level core` 被拒绝并给出明确提示 |
| **G3** | L1 行为不变（回归基准） | 编排器 feeds 映射代码零改动（git diff 为空）；**取数契约层面已通过静态交叉验证（见 §6.1.5）：契约引用 51 op 全在 `cmds`、新增 13 命令均在契约面外、缺失=0**；产物哈希级回放留待下次真实 full-company-analysis 实跑顺带确认 |
| **G4** | 候选命令治理 | §3.1 候选清单含 7 条命令、每条有明确触发条件；`sector-flow` 已从新增清单移除；无任何未接线命令被合入代码 |
| **G5** | 三副本同步 | `skills/ashare-data.md` = `codex-skills/*/SKILL.md` = `~/.workbuddy/skills/*/SKILL.md`；`python3 scripts/sync-codex-skills.py --check` 幂等通过 |

---

## 6. 实施路线图

> **关键护栏**：Phase 1 必须先于任何候选命令实现。骨架未立起来之前，一个新命令都不写。

### 6.1 本次升级的硬交付（Phase 0/1/5/6）

| Phase | 内容 | 产出目标 | 风险 |
|:--:|------|---------|------|
| **0** | 契约先行：本规划文档 + skill 分级声明表 + 注册表 `default_level` 指针 | G1 | 极低（零代码） |
| **1** | 分级骨架：`run-level` 元命令（quick/enhanced/full 三档，拒绝 core）+ L0 静态清单落地 | G2 + G3 | 低（不触碰主管线） |
| **5** | 文档 + 三副本同步 | G5 | 低 |
| **6** | 注册表复验 + full-company-analysis 回放全回归 | 全目标复验 | 低 |

### 6.1.1 实施记录（2026-07-31）

| 目标 | 结论 | 取证 |
|---|:--:|------|
| **G1** 分级契约落地 | ✅ | `skills/ashare-data.md` 新增「取数级别声明（L0–L3）」章节（唯一权威源，含 L1 动态说明 + `search` 跨级定位 + 两条硬约束）；`full_analysis_contract.json` `/skills[0]` 增 `"default_level": "core"`，diff 恰为 1 行、无命令清单；`bash scripts/check.sh` ✅ 全部检查通过 |
| **G2** `run-level` 快查可用 | ✅ | `run-level 600519 --level quick` 退出码 0，`quote`/`valuation`/`financials` 三条原始输出逐字命中；`--level core` 退出码 2 并给出引导文案 |
| **G3** L1 行为不变 | ✅ | 编排器 feeds 映射零改动。本次仅触碰 `tools/ashare_data.py`（纯新增）、`skills/ashare-data.md`、`tests/test_ashare_data.py`、`full_analysis_contract.json`（+1 行）与生成副本；工作区中 `full_analysis_gate.py` / `full_analysis_review.py` / `scripts/full_analysis.py` 为 07-27~07-30 的既有未提交改动，diff 中零 `feeds`/`ashare` 相关行 |
| **G4** 候选命令治理 | ✅ | §3.1 候选 7 条各带触发条件；`sector-flow` 已移除；本次**零新命令合入** |
| **G5** 三副本同步 | ✅ | `sync-codex-skills.py --check` 幂等通过；用户副本经 `~/.workbuddy/berkshire-skill-sync/sync.py` 重生成；三副本正文差异仅为生成器固有适配前言与 `$ARGUMENTS` 替换，无内容漂移 |
| **Phase 6** 注册表复验 | ✅ | `full_analysis_contract.json` 解析合法、`/skills[0].skill_id=ashare-data`、`default_level=core`、`spec_source→skills/ashare-data.md`（唯一权威源）；diff 恰 1 行无 feeds 漂移；`unittest discover` **Ran 516 tests … OK**；`bash scripts/check.sh` 全部检查通过（注册表 v2 13 项契约合法 / 14 skill frontmatter 合规 / codex 同步一致 / 报告索引一致）；`sync-codex-skills.py --check` 幂等；**本步零新增代码改动**（git status 仅含 Phase 1 交付项 + 07-27~07-30 既有未提交项）。回放全回归（真实 full-company-analysis 实跑复验 L1 feeds 映射）留待下次触发 |

**实现要点**：`run-level` 逐条调用子命令、逐条呈现成败（保护 `signals` 类部分成功语义），单条异常不中断其余命令；任一条失败退 1 并提示按"数据不足"处理。L2/L3 当前已就位命令集等于 L0，输出显式列出"尚未就位的候选层"，不静默冒充已覆盖。`cmd_search` 与跨级定码统一收敛到 `_search_candidates()`，公司名多候选时**拒绝代选**。新增 17 个单元测试，`tests/test_ashare_data.py` 共 92 项全绿。

### 6.1.2 需求拉动闭环交付（打板三件套，2026-07-31）

> 首个 ADR-004 需求拉动闭环：消费方 `quality-screen` 提出"纳入涨停生态/监管旁证"需求 → 命令实现 → 归入 L2 声明 → 消费方接线 → 单测 → 三副本同步，一次闭环、不留半成品。

| 目标 | 结论 | 取证 |
|---|:--:|------|
| **命令实现** | ✅ | `tools/ashare_data.py` 新增 `cmd_limit_pool`/`cmd_monitor_pool`/`cmd_anomaly_pool` 三命令 + `_zt_pool`/`_fmt_zt_time`/`_anomaly_market` 辅助；接入 `cmds` 字典（`--date` 非 `--code`，全市场级）；实测涨停池 99 / 监控池 13 / 异动池 7 条真实数据 |
| **L2 归级** | ✅ | `skills/ashare-data.md` L2 行改"部分就位→三件套已建"；子命令总览表新增三行；`LEVEL_PENDING_LAYERS` 仅留热度层（L2）+ 一手定性/快讯/研报层（L3），三件套已移出待建 |
| **消费方接线** | ✅ | `skills/quality-screen.md` 三处：主取数块追加三命令（标注旁证不判决）/ 批量单 Agent 任务第 5 步（批次级调 1 次避风控）/ 新增「情绪与治理旁证（免费源·东财）」子节 |
| **单测门禁** | ✅ | `tests/test_ashare_data.py` 新增 `TestLimitPoolCommand`/`TestMonitorPoolCommand`/`TestAnomalyPoolCommand`/`TestAnomalyMarketHelper`/`TestZtTriadCli` 共 19 项：mock `_curl_json` 覆盖成功/空/异常三态 + 可发现性 + 参数断言 + 北交所号段判定 + 规则码映射 |
| **三副本同步** | ✅ | `sync-codex-skills.py --check` 幂等；用户副本经 `~/.workbuddy/berkshire-skill-sync/sync.py` 重生成 |
| **全回归** | ✅ | `unittest discover` 全绿（516 + 新增 19）；`bash scripts/check.sh` 全部检查通过 |

**实现要点**：三件套为全市场级（`--date` 非 `--code`），不进 `run-level` 逐股链（ADR-003）；只作情绪/治理旁证，不参与 quality-screen 7 条硬指标判决；北交所与深市同为 `m=0`、监控池 `MARKET="B"` 三值，市场判定按代码号段（920/43/83/87→BJ）；`anomaly-pool` 须带 `team=h5` 否则 `unknow team`；`result!=0` 冒泡非静默吞。`TransportError` 显式捕获（非 `ConnectionError`）。

### 6.1.3 需求拉动闭环交付（热度层 `ths-hot`，2026-07-31）

> 第二个 ADR-004 需求拉动闭环：消费方 `news-pulse`（§3.1 指定）要求"热度量化旁证" → 命令实现（curl 优先 + Tushare 备用）→ 归入 L2 声明（热度层从 `LEVEL_PENDING_LAYERS` 移除，L2 全层就位）→ 消费方接线（news-pulse + quality-screen）→ 单测 → 三副本同步，一次闭环、不留半成品。

| 目标 | 结论 | 取证 |
|---|:--:|------|
| **命令实现** | ✅ | `cmd_ths_hot` 重写为 curl 优先：同花顺热榜(GET `_THS_HOT_URL`) → 东财人气榜(POST `_EM_HOT_URL` + `ulist.np` 补名称/价格) → Tushare `ths_hot` 回退；新增 `_curl_json_post`/`_ths_hot_list`/`_em_hot_rank`/`_ths_hot_tushare`；argparse 加 `--period`(hour/day)/`--top`；实测同花顺热榜 100 条真实数据（人气值/概念/标签/排名变化） |
| **L2 归级** | ✅ | `LEVEL_PENDING_LAYERS` 移除"热度层"（`enhanced`/`full` 均清空该层）；`skills/ashare-data.md` L2 行改"部分就位→已就位（三件套 + 热度层）"；子命令总览表新增 `ths-hot` 行；数据陷阱加第 12 条 |
| **消费方接线** | ✅ | `news-pulse.md` 把 `ths-hot` 引用落地为具体调用（L2 热度旁证，零依赖）；`quality-screen.md` 旁证块扩为四件套（`ths-hot` 热度旁证）+ 主取数块追加调用 + 批量第 5 步说明 |
| **单测门禁** | ✅ | `tests/test_ashare_data.py` 新增 `TestThsHotCommand`/`TestEmHotRankHelper`/`TestThsHotCli` 共 N 项：mock `_curl_json`(同花顺 GET) + `_curl_json_post`(东财 POST)，覆盖同花顺成功/东财回退/双源失败→Tushare/无 token/可发现性/参数断言 |
| **三副本同步** | ✅ | `sync-codex-skills.py --check` 幂等；用户副本经 `~/.workbuddy/berkshire-skill-sync/sync.py` 重生成 |
| **全回归** | ✅ | `unittest discover` 全绿；`bash scripts/check.sh` 全部检查通过 |

**实现要点**：热度是 intraday/rolling（`--period hour/day`），非日期驱动，`--date` 仅 Tushare 回退用；同花顺 `rise_and_fall` 已是百分点单位（如 2.08 即 +2.08%），勿再 ×100；东财人气榜仅返回带前缀代码，须再走 `push2 ulist.np` 补名称/价格，`diff` 偶有 dict（按序号为键）已 `list(values())` 归一化；热度层只作 L2 热度旁证，不参与 quality-screen 7 条硬指标判决。

### 6.1.4 需求拉动闭环交付（L3 三件套：ird-interact / cls-telegraph / report-list，2026-08-01）

> 第三个（收尾）ADR-004 需求拉动闭环，一次完成 L3 全层：三个消费方（management-deep-dive / news-pulse / investment-research）各提出需求 → 命令实现（均为零鉴权免费源）→ 归入 L3 声明（三件套就位）→ 消费方接线 → 单测 → 三副本同步，不留半成品。

| 目标 | 结论 | 取证 |
|---|:--:|------|
| **命令实现** | ✅ | `cmd_ird_interact`（巨潮互动易两步 POST，orgId 取自 `queryKeyboardInfo`，第二步参数放 query string）+ `cmd_cls_telegraph`（财联社 v1，本地签名 `sign=md5(sha1(字典序 query))` 零 key）+ `cmd_report_list`（东财 reportapi，个股 qType=0 / 行业 `--industry` qType=1，分页至 TotalPage 或 limit）；`_curl_json_post` 加 `json_body` 开关（form/JSON）；实测互动易 76 条问答 / 财联社实时电报 / 茅台 3 篇研报含评级EPS / 行业研报 |
| **L3 归级** | ✅ | `LEVEL_PENDING_LAYERS["full"]` 清空（7 候选全交付）；`skills/ashare-data.md` L3 行改"候选储备→部分就位（三件套已建）"；子命令总览表新增三行；数据陷阱加第 13 条（互动易/财联社/研报各自坑） |
| **消费方接线** | ✅ | `management-deep-dive.md` 接 ird-interact（L3 一手定性，附 bash）；`news-pulse.md` 接 cls-telegraph（L3 快讯，附 bash + 签名说明）；`investment-research.md` 接 report-list（L3 研报，补 analyst-reports 免费源） |
| **单测门禁** | ✅ | `tests/test_ashare_data.py` 新增 `TestIrdInteractCommand`/`TestClsTelegraphCommand`/`TestReportListCommand` 共 N 项：mock `_curl_json`(GET) + `_curl_json_post`(form/JSON)，覆盖成功/空/异常/分页停止/签名值/参数断言/可发现性；更新 `test_full_declares_l3_pending_layer_only` → full 无 pending 层 |
| **三副本同步** | ✅ | `sync-codex-skills.py --check` 幂等；用户副本经 `~/.workbuddy/berkshire-skill-sync/sync.py` 重生成 |
| **全回归** | ✅ | `unittest discover` 全绿；`bash scripts/check.sh` 全部检查通过 |

**实现要点**：① 互动易第二步参数须放 query string（非 body）否则 HTTP 400；`pubDate` 毫秒时间戳；最新提问常未回复（`attachedContent=None`）属正常。② 财联社 `sign` 纯本地算、零 key，无 sign 返回 errno≠0。③ `reportapi` 只认纯 6 位代码（带前缀 hits=0 误判）、北交所老号段需先迁 920 码；与东财其他接口共享风控。三者均零鉴权免费源，只作 L3 旁证，不参与 quality-screen 7 条硬指标判决；不进 run-level 逐股链（ADR-003）。

### 6.1.5 G3 契约取数面静态交叉验证（2026-08-01，回放复验的轻量收尾）

> 目的：在启动"真实 full-company-analysis 重管线回放"（重、耗时）前，先做**有界、可立即完成**的静态复验——交叉核对 `full_analysis_contract.json` 中 ashare-data 契约实际引用的命令集 vs `ashare_data.py` 的 `cmds` 字典，确认本次升级（只新增命令、未改 feeds/run-level）**没有破坏管线取数契约**。

| 检查项 | 结论 | 取证 |
|---|:--:|------|
| **契约 required op 全在 cmds** | ✅ | 7 个 L1 必取命令 `quote/financials/valuation/history/equity-history/announcements/signals` 逐项命中 `cmds` |
| **契约 conditional op 全在 cmds** | ✅ | 44 个条件命令（含 `managers/audit/repurchase/pledge/consensus/north-hold/money-flow/analyst-reports/ths-hot/limit-list/top-list…`）逐项命中 |
| **契约引用 op 无缺失** | ✅ | 契约引用 op 合计 **51 个（7 required + 44 conditional），全部存在于 `cmds`（64 个命令），缺失=0** |
| **新增命令不污染契约面** | ✅ | `cmds` 独有 13 个命令（本次新增 `ird-interact/cls-telegraph/report-list` + 既有 standalone 快查 `ah-cross-check/hk-quote/index-val/kline/macro/run-level/search/limit-pool/monitor-pool/anomaly-pool`）**契约均未引用**，符合 ADR-003（L2/L3 与 run-level 不进主管线逐股链）+ ADR-004（需求拉动新增由消费方 skill 调用，不进 full-analysis 契约面） |
| **契约注册表合法** | ✅ | `check-full-analysis-contract.py`：Contract v2 13 项契约结构合法 |
| **单测无回归** | ✅ | `unittest tests.test_ashare_data`：**Ran 142 tests … OK** |

**结论**：本次 L2/L3 升级为**纯增量**——契约引用的 51 个取数 op 一个不少地存在于 `cmds`，新增的 13 个命令全部落在契约面之外，未改动任何 feeds 映射或 run-level 行为。**G3「L1 行为不变」在取数契约层面已通过静态验证**；真实 full-company-analysis 重管线回放（产物哈希级复验）仍可作为下次实跑时的顺带最终确认，非本次升级的阻塞项。

**收口决策（用户确认，2026-08-01）**：采用轻量收尾方案——**不单独开重管线做产物哈希级回放**，该项留待下次实跑某公司做 full-company-analysis 时顺带完成。理由：静态交叉验证已覆盖取数契约面（51 op 全命中、新增命令全在契约面外、feeds/run-level 零改动），真机回放属冗余的哈希级兜底。据此 **ashare-data 分级执行升级项目正式收口**。

### 6.2 候选储备（原 Phase 2–4，已降级为需求拉动）

不排期、不预建。任一候选命令启动时，按下列闭环单独立项：

```
消费方 skill 提出需求
  → 命令实现（复用 TransportClient / FallbackChain / DataResult / identifiers）
  → 归入 L2 或 L3 声明表
  → 消费方 skill 接线同步改
  → 单测 + check.sh 全绿
  → sync-codex-skills.py 三副本同步
```

每个候选命令的验收采用"端到端做穿"标准：命令 → 分级归位 → skill 文档 → 消费方接线 → 单测 → check.sh，一次闭环，不留半成品。

---

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| **L1 被后人误解回固定清单** | full-company-analysis 静默降级，真实交易底座受损 | ADR-001 留档 + §2.2 证伪记录 + skill 声明表显式标注"L1 动态" |
| **`run-level` 被误用进主管线** | 命令级血缘塌缩，审计链断裂 | 不实现 `--level core`；CLI 层显式拒绝 + 提示语引导走 gate |
| 候选命令绕过消费方直接合入 | 建了没人用，扁平集合再变长 | G4 判定门禁：无接线不合入 |
| 东财 push2ex 风控 | 候选命令批量封 IP | 实施时复用 `TransportClient` 重试退避；失败降级"数据不足" |
| 同花顺 401 反爬 | `ths-hot` 不可用 | 标为可选命令；Tushare `ths_hot` 作备用 |
| 财联社签名算法变更 | `cls-telegraph` 失效 | 签名逻辑独立封装，单点可改；失败不影响主流程 |
| 北交所市场标识错标 | 监控池/异动池误判 | 复用 `normalize_code()`；按 V3.6 修正 MARKET="B" |
| 分级增加 skill 文件长度 | token 消耗 | 声明表精简；候选命令文档不进 skill 主体 |

---

## 8. 依赖与资源清单

| 依赖项 | 路径 | 升级中角色 |
|--------|------|-----------|
| ashare_data.py | `tools/ashare_data.py` | 新增 `run-level` 元命令（quick/enhanced/full） |
| ashare_plugin/ | `tools/ashare_plugin/` | 候选命令实施时复用 `TransportClient`/`FallbackChain`/`DataResult`/`identifiers` |
| ashare-data.md | `skills/ashare-data.md` | **分级定义唯一权威源**：增加 L0–L3 声明表 + `search` 跨级说明 |
| full_analysis_contract.json | `tools/full_analysis_contract.json` | ashare-data 块增 `default_level: "core"` 指针（不含命令清单） |
| sync-codex-skills.py | `scripts/sync-codex-skills.py` | 改 skill 后必跑，`--check` 验幂等 |
| check.sh | `scripts/check.sh` | 每 Phase 收尾全绿门禁 |

> 本次升级**不新增任何 pip 依赖**；候选命令全部 curl 零依赖可达。

---

## 9. 决策记录（ADR）

> 本仓库未设独立 ADR 目录，架构决议集中留档于此。每条含背景、决议、后果与推翻条件。

### ADR-001 · L1 CORE 的定义

- **背景**：第一版把 L1 定义为固定的 7 命令清单，并以"升级前后 diff 为空"作为回归基准。核查两个真实运行留痕，发现实际执行 12 条与 27 条，稳定交集含 `ratios`/`mainbz`/`managers`/`peers` —— 原清单漏项会导致静默降级；且"现状"随公司动态变化，不构成可 diff 的固定点。
- **决议**：**L1 CORE = 编排器 feeds 映射决定的全量取数**，非固定清单。分级模型管的是**新增能力的准入**，不重新框定 L1 边界。
- **后果**：G3 回归基准改为"feeds 映射规则不变 ⇒ 命令集不变"；`run-level` 不提供 `--level core`（见 ADR-003）。
- **推翻条件**：若未来 feeds 映射被静态化为一份显式清单文件，L1 方可回归"清单式"定义。

### ADR-002 · L0 的定义与 `search` 的定位

- **背景**：第一版 L0 = `quote` + `search`，现实中无任何工作流对应此组合；而 `search` 只在输入为公司名时触发，是输入归一化步骤而非数据层。
- **决议**：**L0 QUICK = 概览三件套**（`quote` + `valuation` + `financials`），等于 skill 现有 standalone 默认行为。**`search` 降级为跨级输入归一化步骤**，不属于任何级别。级别之间是**包含关系**（L1 ⊇ L0），非互斥。
- **后果**：L0 对应真实已存在的用户行为；每级定义只描述"取哪些数据"，不掺"怎么定码"。
- **推翻条件**：若 standalone 默认行为本身被改变。

### ADR-003 · `run-level` 的职责边界

- **背景**：第一版设计 `run-level --level core` 进入 full-company-analysis 主管线。撞硬事实后发现三个洞：① skill 第 110 行要求每条成功命令的 command ID 连接到 artifact，聚合成"统一报告"会把 N 条可追溯命令塌成 1 个黑箱；② `signals` 是部分成功语义（skill 第 102 行），聚合后降级信息易被糊掉；③ 静态清单与 ADR-001 的动态定义直接矛盾。
- **决议**：**`run-level` 只服务 standalone 快查**（L0/L2/L3 人工触发），不进主管线；不实现 `--level core`。主管线维持 gate 逐条 `run-ashare-command` 冻结。**"级别"是声明式契约（名词），不是执行式封装（动词）。**
- **被拒方案**：A（进主管线）—— 血缘塌缩不可接受；C（彻底砍掉 run-level）—— 快查便利性归零，无必要。
- **推翻条件**：若 gate 冻结机制改为支持"一次提交多条命令收据"，A 方案可重新评估。

### ADR-004 · 候选命令的准入模式

- **背景**：核查文档声称的 L2/L3 消费方，发现全部落空 —— quality-screen 不调用打板类命令，news-pulse 中 ashare_data.py 标注为可选且不消费财联社，industry-research 的 `sector-flow` 已是 Tushare 命令（第一版误列为新增）。叠加 ADR-001（主管线锁 L1）与 ADR-003（run-level 只服务人工快查），批量预建的 8 个命令将没有任何自动消费方。
- **决议**：**需求拉动**。L2/L3 命令不预建，每个新命令必须与消费方 skill 接线一起交付。本次硬交付收窄为 **L0/L1 骨架 + 分级契约**。原 Phase 2–4 降级为"候选清单 + 触发条件"。
- **后果**：§3.1 从"引入 8 个"改为"候选 7 个"（移除 `sector-flow`）；G4 判定从"8 命令归位"改为"候选清单治理 + 无未接线命令合入"。
- **推翻条件**：若出现一个同时消费多条 L2/L3 命令的新 skill，可批量拉动。

### ADR-005 · 分级定义的权威源

- **背景**：分级信息可能出现在 `skills/ashare-data.md`、`codex-skills/`、`~/.workbuddy/skills/`、`full_analysis_contract.json` 四处。前三者由同步脚本派生，不构成漂移源；真正的风险是 JSON 中重复定义命令清单。
- **决议**：**`skills/ashare-data.md` 是分级定义的唯一权威源**；`full_analysis_contract.json` 只放 `"default_level": "core"` 指针，不重复命令清单（其 `spec_source` 已指向 skill 文件）。
- **后果**：改级别只需改一处 + 跑 `sync-codex-skills.py`。
- **推翻条件**：若编排器改为在不读 spec_source 的情况下决策取数范围。

### ADR-006 · 决议留档形式

- **背景**：D1–D5 决议满足 ADR 三条件（架构显著、难以逆转、易被后人推翻），必须留档；但仓库无既有 ADR 体系。
- **决议**：不新建 `docs/adr/` 目录，决议集中留档于本文档第 9 节，配第 10 节术语表固化易混概念。
- **推翻条件**：当仓库出现第三份需要 ADR 的架构文档时，抽出统一 ADR 目录。

### 既有决策（第一版沿用）

| 决策点 | 结论 | 理由 |
|--------|------|------|
| 改造路线 | **A+C 组合**（契约先行 → 骨架先行 → 命令按需拉动） | 贴一致性纪律；不选 B（先乱后治冲突）/D（方法论已验证） |
| 取数广度 vs 分析深度 | **正交分层** | L0–L3 管广度，lite/standard/deep 管深度，互不干涉 |
| 增量能力取舍 | 候选 7 个 curl 可达命令；拒 mootdx/期权/iwencai/百度K线/stockstats | 零依赖原则 + 价值投资范式 |

---

## 10. 术语表

| 术语 | 定义 | 易混点 |
|------|------|--------|
| **取数广度（L0–L3）** | ashare-data 覆盖哪些数据层 | ≠ 分析深度（lite/standard/deep），后者是 full-company-analysis 编排层概念 |
| **L0 QUICK** | 概览三件套 `quote`+`valuation`+`financials`，静态清单 | ≠ 第一版的 `quote`+`search`（已作废） |
| **L1 CORE** | 编排器 feeds 映射决定的全量取数，**动态** | ≠ 任何固定命令清单；实测 12–27 条随公司变化 |
| **L2 / L3** | 增强档与全量侦察档，命令为候选储备 | 当前**无自动消费方**，仅人工 standalone 触发 |
| **`search`** | 输入为公司名时的定码步骤，**跨级** | 不是数据层，不属于任何级别 |
| **feeds 映射** | 编排器决定某公司跑哪些 ashare 命令的规则 | 本次升级**不修改**它——这是 G3 回归安全的根据 |
| **声明式契约（名词）** | 级别 = 一份"包含哪些数据层"的声明 | ≠ 执行式封装（动词），级别不等于"一个命令跑完一档" |
| **需求拉动** | 新命令必须与消费方接线同交付 | ≠ 供给推（先建能力等人用） |
| **命令级血缘** | 每条成功命令的 command ID 连接到 artifact，gate 逐条冻结收据 | 聚合输出会使其塌缩，故 `run-level` 不进主管线 |

---

*本文档为 Phase 0 契约产物，已并入 grilling 拷问的 D1–D6 决议。确认后进入 Phase 1 实施。改源 skill 后须跑 `python3 scripts/sync-codex-skills.py` 与 `bash scripts/check.sh`。*
