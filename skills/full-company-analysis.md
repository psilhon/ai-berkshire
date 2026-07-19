---
name: full-company-analysis
description: Use when 用户要求对单一公司做全量、完整或端到端投研，并希望自动执行全部业务契约、证据校验、报告审计与最终状态收口；仅限单公司全流程——单项研究走 investment-research、只取数走 ashare-data、行业筛选走 industry-funnel，均不路由至此。
---
<!-- owner: psilhon · review-cadence: per-release · registry-schema: v1 · maturity: governed(Phase2-gated) -->

# 全量公司分析：总控编排 Skill

对 $ARGUMENTS 执行端到端全量公司分析编排。本 Skill 是**编排层**，不自己做研究：它按注册表 `tools/full_analysis_contract.json` 调度现有业务 Skill，用确定性验收器 `tools/full_analysis_gate.py` 收口状态。

> **状态声明（必读）**：
> - 本 Skill（编排层）**不计入注册表的 20 项业务集合**；20 项集合、产物路径、谓词、角色与审计要求的唯一机器真源是 `tools/full_analysis_contract.json`，本文不维护第二份清单。
> - **Phase 2（20 项领域契约 + 真实前向评估）完成之前，本 Skill 仅限内部开发调用**，不得对用户宣称"全量分析可用"，不得把任何一次运行结果当作已验证的生产输出。
> - 所有产出仅供学习研究，不构成投资建议。

## 触发语义

以下请求都应路由到本 Skill：

- "全量分析 XX 公司"
- "完整研究 XX，跑完全部 Skill"
- "自动完成公司端到端投研"
- "对 XX 生成全套证据、估值、论文、审计和内容"

**不适用**：只做单项研究（直接用对应业务 Skill，如 `/investment-research`、`/earnings-review`）；只取数据（用 `/ashare-data` 或 `skills/financial-data.md`）；跨多家公司的行业筛选本身（用 `/industry-funnel`）。本 Skill 只服务"单一公司 + 全套流程"的场景。

## 输入

| 字段 | 必需 | 含义 |
|---|---|---|
| `company` | 是 | 公司名或证券代码 |
| `as_of` | 否 | 默认由本地 `date` 确认；绝不使用训练数据里的日期 |
| `mode` | 否 | 默认 `full`；`resume` 继续同一运行 |
| `visibility` | 否 | 默认 `private`，产物仅写入 `local/`；只有用户明确要求公开输出时才使用 `public` |
| `run_id` | resume 时是 | 由 `init` 生成；resume 必须精确选择已有运行，且 visibility 不可改变 |

不接受 `output_root`。运行根目录由验收器按可见性生成：

```text
private → <repo>/local/筛选公司/<规范公司名>/全量分析/<run_id>/
public  → <repo>/筛选公司/<规范公司名>/全量分析/<run_id>/
```

Git 不是运行前提，也不需要初始化仓库、暂存或提交。若当前目录恰好是 Git 工作树，gate 只使用只读状态做额外越界审计；没有 Git 时继续运行，并把 `workspace_audit_mode=none` 与工作区级审计缺失记为运行限制。public 模式禁止读取私人组合、原始台账或 `local/` 任何内容。

## 预检（10 步，任一失败即停，不进入分层执行）

1. 运行本地 `date` 和 `uname -m`：确认今天日期、时区与 arm64 环境；`as_of` 以此为基线。
2. 以包含 `tools/full_analysis_gate.py` 与注册表的项目目录作为 `repo_root`；不依赖 Git 定位。若能确认当前目录是 Git 工作树，只启用只读工作区审计。
3. 读取注册表 `tools/full_analysis_contract.json`，并**完整读取注册表 `spec_source` 指向的全部 20 个 Skill 规范**，逐个记录 SHA-256（含注册表本身摘要）。
4. 识别 `platform=codex|claude_code` 与实际角色能力（子代理工具是否存在、当前授权是否允许）。`path_enforcement_level` 恒记 `MONITORED`：**当前 Codex 与 Claude Code 均没有按运行根收窄的 per-run 写沙箱，MONITORED 是唯一事实模式，只能通过精确路径指令、cwd 与审计 fail-closed 侦测越界，不得声称"已预防所有越界写"**。`SANDBOXED` 仅为未来注册适配器通过当次 canary 后保留，当前不得选择、推导或展示。
5. 解析 `visibility`：没有明确指示时直接使用 `private`；只有用户明确要求公开输出时使用 `public`。private 固定写入 `local/`；public 必须确认运行根不在 `local/` 且不含 portfolio/私人台账类输入。
6. 计算注册表与每个规范的 SHA-256 写入 manifest；resume 时若摘要变化，受影响项重新置为 `PENDING` 并记录原因。
7. 以公司名输入时定位证券代码、上市状态与法律主体；出现多个无法排除的精确候选时暂停请用户确认，已有上下文明确市场和代码时不重复询问。
8. 确认 Layer 1/2 将产出行业 scope 所需的最新完整财年分部收入事实；不得预先手填 `company.industry`。Layer 2 完成后由 gate 根据这些事实和分部占比计算。
9. 工具预检：`python3 tools/financial_rigor.py --help` 各子命令可用；`tools/financial_rigor_result_schema.json` 存在且与 `--json` 输出字段一致；`--json` 缺失或字段/参数不兼容时失败。
10. 运行 `python3 tools/full_analysis_gate.py init ...`：创建运行根、全部产物路径分配、manifest、legacy watchlist 与运行锁；Git 可用时额外建立只读状态基线，不可用时记录 `workspace_audit_mode=none` 并继续；`review_mode` 初始如实记为 `self_review`。

## 分层执行（六层）

| 层 | 阶段目录（注册表 `stage`） | 前置条件 |
|---|---|---|
| 1. 数据与快筛 | `01-数据与快筛` | 标的身份已确认 |
| 2. 公司与财报 | `02-公司与财报` | 数据底座已生成 |
| 3. 行业与机会 | `03-行业与机会` | 公司和行业定义已稳定（预检第 8 步） |
| 4. 论文与边界 | `04-论文与组合` | 核心研究结论已形成 |
| 5. 内容生产 | `05-内容生产` | 引用数据已冻结 |
| 6. 审计与收口 | `tools/report_audit.py` + `full_analysis_gate.py finalize` | 无 PENDING/RUNNING 项 |

每层包含哪些工作契约、各自的产物路径/谓词/角色要求，一律运行 `python3 tools/full_analysis_gate.py contracts` 从注册表读取，**不要依赖本文或记忆中的清单**。

### 波次与并行（跨层执行形状）

六层由三个硬 barrier 切成执行波：**波间等 barrier、波内尽量并行**。三个 barrier 不可跨——(1) 预检标的身份（进 Layer 1 前）；(2) `set-industry`（Layer 2 分部事实闭环后才算行业 scope，进 Layer 3 前）；(3) 引用冻结（研究结论定稿后才产内容，进 Layer 5 前）。

- **层内并行**：同层内互不依赖、只依赖上游层的契约应 fan-out，不必顺序。可并行组：Layer 1 `quality-screen` ∥ `investment-checklist`（数据取齐后）；Layer 2 `investment-research` ∥ `management-deep-dive` ∥ `earnings-review`（三条互不依赖的基线）；Layer 3 `industry-research` ∥ `industry-funnel` ∥ `bottleneck-hunter` ∥ `news-pulse`；Layer 5 三个内容契约。**并行受平台角色能力约束**——能开子代理才真并行，否则按「多角色能力与降级」顺序执行、记 `execution_mode=sequential_single_context`，不得声称并行。
- **基线先于增强**：`investment-research`→`investment-team`、`earnings-review`→`earnings-team` 是层内顺序边，增强永远在基线产物冻结后才起（细则见 Layer 2 先后与去重）。
- **依赖精度点**：`industry-*` 真正依赖的是 Layer 2 的**分部收入事实**子分支，不依赖 `investment-research` 的论文结论——原则上可与 research 并行；但仍排在 `set-industry` barrier 之后，以保证行业 scope 由确定性分部事实算出、不被半成品污染。**不要为抢并行把行业层提到 barrier 前。**
- **advisory 预取**：Layer 1 就把跨层要用的 Tushare advisory 命令取好（如 `peers` 供 Layer 3 候选池），下游就近引用、不重复取数（映射见 Layer 1 取数口径）。

### 执行纪律（每项、每层）

- 每项执行前必须 `python3 tools/full_analysis_gate.py begin-skill ...`，完成后必须 `finish-skill`；跳过任一步的产物不被承认。
- 每层结束后运行 `python3 tools/full_analysis_gate.py checkpoint`。普通适用项校验已声明产物的路径、类型、大小与章节；带合法 `not_applicable` limitation 的项改为校验谓词、输入事实、负向验收产物与替代路径，不再要求普通产物，且该判定必须与 `finalize` 一致。
- Layer 2 结束后，把完整分部占比、最新完整财年期间和已登记 source fact ID 写入封闭 JSON，再运行 `python3 tools/full_analysis_gate.py set-industry --run-root <run_root> --industry-file <json>`；调用者不得填写 `scope_type/labels`，它们由 gate 按最大分部 ≥50% 或累计 ≥80% 计算。
- 收口前运行 `python3 tools/full_analysis_gate.py set-review-mode --run-root <run_root> --mode self_review|user|independent_context` 记录实际复核模式；`independent_context` 只有 manifest 已记录至少 2 个独立上下文时才会被接受。
- 失败时**只修复本层或其依赖层**，不重写不相关的报告。
- 后序产物必须通过 artifact ID 引用前序产物，禁止覆盖或复用同一目标路径。

### Layer 1 取数口径

**基础集（零依赖，必取）：** A 股标的的 `ashare-data` 在全量分析语境下不止默认概览三件套：另取 `history`、`equity-history`、`announcements`、`signals`，逐命令记录参数、退出码、来源与数据时间；部分成功不得吞警告。非 A 股命中注册表谓词后走 `financial-data` 替代路径并落负向验收。

**Tushare 增强集（配置 `TUSHARE_TOKEN` 时取，advisory；未配置则逐命令记 `NOT_CONFIGURED` limitation，不阻断）：** `ashare-data` 的 Tushare-primary 独立主数据源，取到即写入共用证据底座，供下游就近引用与双源核对。**"命令 → 消费 skill → 采集层"的完整清单是注册表 `advisory_command_operations.values` 的唯一机器真源**（`op`/`feeds`/`layer` 三字段；跑 `python3 tools/full_analysis_gate.py contracts` 或直接读注册表取用），本文不复述；各命令语义见 `skills/ashare-data.md`。两个承载编排语义的要点：`mainbz` 是分部收入的独立第二源（喂 financial-data 做分部双源核对），`peers` 是申万成员候选池（Layer 1 预取、Layer 3 喂 industry-funnel 候选池自动化）。大盘级估值分位用 `index-val` 作背景锚，不入公司证据底座、不登记 advisory。

Tushare `MATCH` 计第二源、`CONFLICT` 两值并记不合并、`INSUFFICIENT/NOT_CONFIGURED` 只记限制（口径见证据口径段）。

**行情价格双源：** `quote`/`valuation` 已内置腾讯 + 新浪独立第二价源核对，`价格双源` 行的 `MATCH/CONFLICT/UNAVAILABLE` 逐一如实记录，不因价格双源自动把 PE/PB/市值升级为逐字段双源。

**Web 取数兜底纪律（一等公民，非临场救火）：** `WebSearch`/`WebFetch` 后端不可用是常见情形，不得因此判流程失败。命中时按序降级：(1) `curl` 直连东财 datacenter/F10 与巨潮 cninfo 原始 PDF；(2) Browser pane 直接抓取官方页面（如上海航运交易所 SCFI/CCFI、Alphaliner）；(3) 仓库 `tools/ashare_data.py` + Tushare。所得仍为真实公开只读数据，但须在 limitations 显式记"通用新闻检索路径不可用"（券商一致预期等据此置 unverified），不得静默当作已核。

### Layer 2 先后与去重（强制）

- `investment-research` 是**唯一基线**；`investment-team` 只在拥有**至少 2 个独立上下文**时做反证与增量整合（`interpreter-duan` / `interpreter-buffett` / `interpreter-munger` / `interpreter-li` 分别从段永平、巴菲特、芒格、李录方法出发找反证），不重做基线、不重复全量取数。其最终报告必须逐章保留“段永平视角、巴菲特视角、芒格视角、李录视角、四视角对照表、分歧仲裁、综合结论”，不得用通用业务维度替代或合并四位命名视角。独立上下文不足 2 时，`investment-team` 以 `NOT_APPLICABLE_PASS` 收口并引用基线，不得顺序生成同质报告冒充。
- `earnings-review` 是**唯一事实底稿**；`earnings-team` 只消费冻结底稿做多视角解释、编辑与读者复核。独立上下文不足 2 时同理 N/A 并引用底稿。
- 交叉验证一律用当前 CLI 形态：`python3 tools/financial_rigor.py cross-validate --field {字段} --values '{"来源1": {值1}, "来源2": {值2}}'`（`--metric`/`--sources`/裸数字数组是已失效形态）。

### 多角色能力与降级

- 只有平台工具存在、当前用户授权允许且对应 Skill 允许时，才创建真实独立角色会话；实际独立上下文数如实记录，不根据平台名称假设。
- 不允许子代理时由主会话按角色顺序完成，记录 `execution_mode=sequential_single_context`，不得声称并行或独立复核；功能状态按产物计算，保障等级降为 `SINGLE_CONTEXT`。
- 角色独立性只影响 assurance 轴，不得把"平台没开子代理"写成业务 PWL。

## 强制路径指令（每次调度子 Skill 时逐字执行）

调度每个子 Skill 时，必须把注册表为该项分配的**精确目标路径**写进指令，并附带以下声明：

> 本次运行的输出目标路径是 `<run_root>/<注册表分配路径>`。**此路径覆盖你自身规范内的任何历史默认保存路径（`reports/...` 或 `~/...`）**；不得写入任何其他位置。

同时：

- 子流程所有 shell 命令以 `cwd=run_root` 执行，注入 `FULL_ANALYSIS_OUTPUT_ROOT` 与当前 artifact path；相对 `reports/...` 因 cwd 落入运行根。
- **不重绑 HOME**——那会改变凭据、配置和工具缓存查找路径，不能当安全边界；home 绝对路径越界交给 legacy watchlist 侦测（watchlist 变化立即终止该 Skill 并 FAIL，不等 finalize）。
- `reports/INDEX.md` 不在 allowlist：全程只允许 `python3 scripts/build_report_index.py --check`，不得重写索引。
- Skill 不要求 Git，也不执行 `git init/add/commit`；未经当前对话明确授权不执行 push、PR、发布或任何外部写入，内容生产只出本地草稿。

## 自动继续与暂停

**自动继续**（记录后不打断用户）：

- 单源但无冲突：标记证据不足，相关结论与该项状态上限 `PASS_WITH_LIMITATIONS`；
- 主源失败但备用源成功：记录降级后继续；
- 不适用：命中注册表谓词后生成负向验收文件（写入谓词 ID、输入事实、替代路径），"当前没时间/没数据"不是不适用谓词；
- 未提供私人组合：`portfolio-review` 自动生成 N/A 负向验收，不读取或猜测台账；
- 审计为 `INSUFFICIENT` 且该报告非必需准出对象：保留限制继续，不能写成审计 PASS；
- 多角色能力受限：按上文 N/A 或顺序执行规则继续，并更新 assurance。

**暂停并请求用户**（仅以下四种）：

1. 公司名对应多个无法排除的精确实体或证券；
2. 用户已明确要求实际组合优化，但组合输入不完整或互相冲突；
3. 执行需要外部写入、发布、提交表单或不可逆操作；
4. 两个高信源存在会改变路由或核心结论的实质冲突。

## 状态语义（调用者不填状态）

- 单项与总体状态**全部由 `finalize` 机器计算**；调用者（含本编排层）不能提交状态、计数或 assurance，`summary` 只从 gate 计算结果生成。
- `checkpoint` 允许 `PENDING/RUNNING/COMPLETE/BLOCKED` 执行态但 `computed_status` 必须为空；`finalize` 遇到 PENDING/RUNNING 拒绝出可发布报告；`BLOCKED` 只能算 `FAIL`。
- 最终报告必须**并列呈现三轴**，不能只展示一个容易误读的"PASS"：
  - `completion_status`：`COMPLETE` / `INCOMPLETE`；
  - `validation_result`：`PASS` / `PASS_WITH_LIMITATIONS` / `FAIL`（任一单项 FAIL → 总体 FAIL、退出 1）；
  - `assurance_level`：`INDEPENDENT` / `MIXED` / `SINGLE_CONTEXT`，另记 `review_mode=independent_context|user|self_review`。
- 同时展示 `workspace_audit_mode=git|none`；`none` 不阻断完成，但总体 `validation_result` 最高为 `PASS_WITH_LIMITATIONS`，不得暗示已完成工作区级越界变化审计。
- 顺序单上下文允许得到"COMPLETE + validation PASS + assurance SINGLE_CONTEXT"，但报告必须显示"缺独立验证"，不能写成"独立验证通过"。
- `PASS_WITH_LIMITATIONS` 只表示数据、证据、时效或允许的业务降级，不得用于掩盖缺失必需产物、冲突或阻塞。

## 证据口径（事实与计算）

- 每条关键事实按 gate 要求记录：`fact_id`、字段、主体、期间、单位、取值、允差，以及每个来源的 `publisher_id`、`document_id`/URL、`source_type`、`acquisition_chain_id`、观察值与访问时间。
- `DUAL_SOURCE` 必须同时满足：发布主体不同、传播链不同、期间/主体/单位可比、数值在允差内。同一发行人的多份报告、同一接口的转载、不同网站对同一底层数据的镜像、自洽重算与研究假设**都不算第二源**；任一传播链无法识别时最多记 `SINGLE_SOURCE`。
- 关键计算不接受任意 shell 字符串：只使用 allowlist 中的结构化 operation，经 `python3 tools/financial_rigor.py <op> ... --json` 产出符合 `tools/financial_rigor_result_schema.json` 的 envelope，供 finalize 语义重放。
- 计算记录的 `expected` 必须完整包含 `outcome/is_pass/exit_code/result`，其中 `result` 字段集合必须与冻结 operation schema 逐项相等；ERROR/INSUFFICIENT/FAIL 计算不能冒充成功重放。
- 报告审计记录只提交 `artifact/ratio/seed/results` 原始核验值，不提交 `verdict/sample_count`；gate 会从实际 artifact 重新提取与抽样，再调用 `report_audit.py` 的三态算法计算 verdict。

### Tushare 可选验证状态映射

- `MATCH`：仅当 subject、period、unit 一致时，可把 Tushare 记录为第二个 source；`publisher_id=Tushare`、`acquisition_chain_id=tushare-api`，不得复制东方财富获取链 ID。
- `CONFLICT`：财务、公告、重大事项和非同口径字段同时记录两侧 `observed_value`，相关 fact 必须由 gate 计算为 `CONFLICT`，完成第三源或原始披露仲裁前不得写成已验证结论。市场结构化字段（行情、估值、市值、换手率、可比较股本）在同主体、同交易日、同单位下可采用 Tushare：fact 取 Tushare 值与 `data_domain=market_structured`，原市场来源保留为 `precedence_status=superseded_by_tushare`，Tushare source 使用 `publisher_id=Tushare`、`acquisition_chain_id=tushare-api`、`source_type=market_data`；不得把该事实写成双源一致。
- `INSUFFICIENT`：只在 limitations 中记录接口、期间、单位或权限缺口，不添加空 source。
- `NOT_CONFIGURED`：记录 `tushare_not_configured` limitation，不得声称 Tushare 双源核验完成。

## 并发、恢复与退出码

- `init` 以排他创建生成运行锁；同一运行根存在活动锁时，`full` 与 `resume` 都拒绝启动第二个写者（退出 3）。同 host 的陈旧锁只能显式 `--recover-stale <run_id>` 恢复；不同 host 的锁一律退出 3 交人工核查，gate 不杀进程。
- resume 必须同时匹配 manifest run ID、visibility、platform 与运行根真实路径；不覆盖已 `COMPLETE` 的项；规范摘要或依赖事实变化的项重置 `PENDING` 并记录原因。
- gate 退出码语义：`0` 完整通过；`1` 契约/数据/产物/最终验收失败；`2` 参数、schema 或非法状态转换；`3` 锁与并发冲突。finalize 失败时保留可恢复工作态与诊断，**不生成貌似成功的最终报告**。

## 输出要求

- 产物树以注册表分配为准（`00-运行清单.md`、`00-总体验收报告.md`、五个阶段目录、`06-负向验收/`、`evidence/`）。
- 每个适用研究报告必须包含：数据截止日、主体/期间口径、直接来源、限制，以及"仅供学习研究，不构成投资建议"。
- 收口顺序固定：`report_audit.py` 抽检 → `full_analysis_gate.py finalize` → `summary`；抽检 0 样本只能 `INSUFFICIENT` 或 `FAIL`，永不 PASS。
