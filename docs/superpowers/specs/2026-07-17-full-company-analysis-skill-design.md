# 全量公司分析 Skill 设计

> 日期：2026-07-17  
> 状态：评审修订版，待实施  
> 名称：`full-company-analysis`

## 1. 背景

招商银行端到端验证说明仓库内的 20 个业务 Skill 可以组合为一次完整公司研究，但原始执行仍依赖人工维护步骤、目录、状态和审计计数。实际运行暴露了以下可重复问题：

1. Skill 在项目中存在，不等于已经安装或被当前会话加载。
2. 数据主体、A/H 股、集团/本公司和年度/季度口径容易在复用中丢失。
3. Skill 文档中的 CLI 示例可能落后于实际工具参数。
4. 多角色研究可能因平台能力或当前授权不足而顺序降级，不能伪称独立并行。
5. 不适用的 Skill 需要负向验收，不能静默跳过或伪造输入。
6. 报告审计器可能误提取日期、评分和链接数字，也可能提取到 0 个样本点。
7. 底层 JSON、报告和汇总计数可能在多次写入后不一致。
8. 现有 Skill 的默认保存目录不统一；仅声明一个 `output_root` 不能保证产物实际落入该目录。
9. 仅检查 manifest schema 和文件存在性，可以被“空壳报告 + 假 manifest”绕过。

新 Skill 必须将这些失败模式转成显式工作契约和机器检查，并准确说明机器检查能证明什么、不能证明什么。

## 2. 保证边界

### 2.1 v1 可以保证

在最终 gate 通过时，可以证明：

- 当前注册表恰好定义 20 个业务工作契约，且每个契约均得到一个机器计算的最终状态；
- 声明的产物符合分配路径、文件类型、最低结构、必需章节和专项证据要求；
- 关键事实的来源记录满足结构性独立规则，冲突和缺失没有被写成强结论；
- 关键计算属于允许类型，并已由 gate 使用当前工具和结构化参数重新执行；
- 多角色、审计、不适用、限制和隐私约束均按注册表中的规则处理；
- 最终矩阵和状态计数完全由 manifest 与实际文件生成，没有人工填数。

### 2.2 v1 不能保证

Prompt 级 Skill 无法从文件系统结果中证明“某个名称的 Skill 在模型内部确实被调用”，也无法证明外部网页本身真实、LLM 的所有定性判断正确，或运行期间绝无未声明的外部文件写入。因此：

- 20 行矩阵表示 20 份**业务工作契约**的满足情况，不表示 20 次可密码学验证的函数调用；
- `DUAL_SOURCE` 证明记录的发布主体和获取链结构独立且数值一致，不证明来源页面没有造假；
- 定性判断仍由研究者/模型完成，gate 只验证其证据引用、规则、置信度和证伪条件完整；
- “可用”声明必须包含一次真实标的前向实跑；静态检查、单元测试或旧招商银行结果不能替代前向实跑；
- 未完成前向实跑时，只能称“实现完成、尚未端到端验证”，不能称“全量分析 Skill 已正常工作”。

## 3. 目标

输入一家公司的名称或证券代码后，使 Codex 在常规路径上无需用户逐步选择，自动完成：

- 公司身份、市场和上市状态确认；
- 20 份业务工作契约的适用性路由与执行；
- 财务、估值、市值和行情的多源记录与精确计算；
- 公司、财报、行业、新闻、论文和本地内容草稿；
- 不适用和缺少必要输入时的负向验收；
- 报告抽样、三态审计和 20 行最终状态矩阵；
- 失败后从原子化持久清单继续，不重做已完成且仍有效的阶段。

## 4. 非目标

- 不将新 Skill 自身计入 20 个业务工作契约。
- 不复制 20 个现有 Skill 的完整领域流程。
- 不保证外部数据源永远可用；缺数据时降级或失败，不伪造。
- 不把研究中的有据判断伪装成机械事实。
- 不自动读取私人组合、原始交易台账或凭据。
- 不支持自定义仓库外输出目录；v1 只写仓库内固定公司目录。
- 不自动 commit、push、PR、发布、发送或上传。

## 5. 使用接口

### 5.1 Skill 触发

Skill 名称为 `full-company-analysis`。触发语义覆盖：

- “全量分析 XX 公司”；
- “完整研究 XX，跑完全部 Skill”；
- “自动完成公司端到端投研”；
- “对 XX 生成全套证据、估值、论文、审计和内容”。

### 5.2 输入

| 字段 | 必需 | 含义 |
|---|---|---|
| `company` | 是 | 公司名或证券代码 |
| `as_of` | 否 | 默认由本地 `date` 确认，不使用训练时间 |
| `mode` | 否 | 默认 `full`；可选 `resume` 继续同一运行 |

v1 不接受 `output_root`。验收器根据仓库根目录与规范公司名生成唯一运行根目录 `local/筛选公司/<规范公司名>/`。证券名出现多个无法排除的精确候选时暂停请用户确认；已有上下文明确市场和代码时不重复询问。

## 6. 架构

### 6.1 组件

1. **总控 Skill** `skills/full-company-analysis.md`
   - 定义预检、分层顺序、继续条件、暂停条件和角色降级规则。
   - 调度前完整读取注册表指向的现有 Skill 规范，并记录其 SHA-256。
   - 只从注册表读取 Skill 集合和产物分配，不在 Skill 文本中维护第二份 20 项清单。
   - 对每个子流程给出当前任务的精确目标路径；该路径明确覆盖子 Skill 的历史默认保存路径。

2. **机器契约注册表** `tools/full_analysis_contract.json`
   - 是 20 项集合、索引、阶段、规范路径、产物模板、证据要求、适用性谓词、角色要求、审计要求和状态上限的唯一机器真源。
   - 使用 `schema_version` 和每项 `contract_version` 显式版本化。
   - 新增、删除或改名必须修改注册表并更新契约测试，不能静默进入矩阵。

3. **确定性验收器** `tools/full_analysis_gate.py`
   - `init`：解析公司、创建运行目录、快照、锁和 manifest，并从注册表分配全部产物路径。
   - `begin-skill` / `finish-skill`：原子更新执行态和尝试记录。
   - `checkpoint`：允许未完成执行态，检查当前已有记录与路径。
   - `finalize`：重放计算、执行全部契约、机器计算最终状态并生成最终矩阵。
   - `summary`：只从 gate 计算结果生成汇总，禁止调用者手工提供状态计数。
   - `contracts`：输出当前注册表的标准化摘要，供 Skill 和测试消费。

4. **测试** `tests/test_full_analysis_gate.py`
   - 使用临时仓库和伪造工件测试注册表、状态机、路径、锁、证据、重放和错误。
   - 不联网，不读取真实私人数据。

5. **生成与兼容产物**
   - `skills/full-company-analysis.md` 是编排文字规范源。
   - `tools/full_analysis_contract.json` 是机器验收规范源。
   - 生成 `codex-skills/full-company-analysis/SKILL.md` 和 `codex-prompts/full-company-analysis.md`。
   - 仅安装本次新增或明确修订的 Skill，不批量覆盖其他全局 Skill。

### 6.2 当前固定业务集合

注册表首版包含以下 20 项：

`ashare-data`、`financial-data`、`quality-screen`、`investment-checklist`、`investment-research`、`investment-team`、`management-deep-dive`、`earnings-review`、`earnings-team`、`industry-research`、`industry-funnel`、`bottleneck-hunter`、`news-pulse`、`thesis-tracker`、`thesis-drift`、`portfolio-review`、`private-company-research`、`deep-company-series`、`dyp-ask`、`wechat-article`。

上面只是设计评审快照；实现中的 Skill、gate 和测试均不得再次硬编码该列表，必须读取注册表。`full-company-analysis` 是编排层，不加入集合。

### 6.3 单项机器契约

每个注册项至少包含：

```json
{
  "index": 1,
  "name": "ashare-data",
  "contract_version": 1,
  "stage": "01-data-screen",
  "spec_source": "skills/ashare-data.md",
  "artifact_rules": [],
  "required_sections": [],
  "evidence_rules": [],
  "applicability_rule": {},
  "role_rule": {},
  "audit_rule": {},
  "legacy_output_patterns": []
}
```

注册表必须为 20 项分别定义有区分度的证据，不允许 20 项共用“文件非空”这一最低条件。例如：

- `ashare-data`：要求规定的数据类别、真实命令记录、退出码、数据时间、主源/备用源与警告；
- `financial-data`：要求关键财务字段双源表、主体/期间/单位、交叉验证和精确计算重放；
- `quality-screen`：要求 7 项筛选指标、金融行业适配规则和逐项结论；
- `investment-team`：要求四个角色记录、实际执行载体数、复用/顺序降级说明和整合报告；
- `earnings-team`：要求六个角色记录、财报期间和统一复核报告；
- `news-pulse`：要求四类侦察、事件时间线、异动主因和论文重审触发结果；
- `deep-company-series`：要求 3 至 8 篇独立文章及系列索引；
- `dyp-ask`：要求方法模拟声明，不得伪称本人发言；
- 不适用项：要求命中的注册谓词、事实输入、负向验收和替代路径。

## 7. 运行数据模型

### 7.1 Manifest

`evidence/00-analysis-manifest.json` 是单次运行状态的唯一数据源，由 gate 原子写入。调用者提交证据记录，但不能直接写最终状态。核心结构包括：

```json
{
  "schema_version": 1,
  "run": {
    "run_id": "20260717T120000+0800-600036",
    "phase": "WORKING",
    "run_root": "local/筛选公司/招商银行",
    "created_at": "2026-07-17T12:00:00+08:00",
    "updated_at": "2026-07-17T12:00:00+08:00"
  },
  "company": {
    "name": "招商银行",
    "codes": ["600036.SH", "03968.HK"],
    "listing_status": "listed",
    "as_of": "2026-07-17",
    "timezone": "Asia/Shanghai"
  },
  "skills": [
    {
      "index": 1,
      "name": "ashare-data",
      "contract_version": 1,
      "spec_sha256": "...",
      "execution_state": "COMPLETE",
      "computed_status": null,
      "artifacts": [],
      "facts": [],
      "calculations": [],
      "judgments": [],
      "role_runs": [],
      "limitations": []
    }
  ]
}
```

核心对象使用封闭 schema；未知字段不得参与准出，也不得伪装为 gate 保证。允许扩展的信息只能放入显式 `annotations` 对象，最终报告必须说明这些注释未经 gate 验证。

### 7.2 执行态与最终状态

- 执行态：`PENDING` / `RUNNING` / `COMPLETE` / `BLOCKED`；
- gate 计算状态：`PASS` / `PASS_WITH_LIMITATIONS` / `NOT_APPLICABLE_PASS` / `FAIL`。

状态不由模型填写，遵守以下不变量：

- `checkpoint` 允许四种执行态，但 `computed_status` 必须为空；
- `finalize` 遇到 `PENDING` 或 `RUNNING` 时拒绝生成可发布最终报告；
- `BLOCKED` 在最终矩阵中只能被计算为 `FAIL`；
- `COMPLETE` 由证据契约计算四态之一；
- 任意 `FAIL` 仍生成标有“验收失败”的诊断矩阵，进程退出 1；只有 20 项全部非 `FAIL` 才退出 0；
- `PASS_WITH_LIMITATIONS` 只能表示工作流完成但存在披露限制，不能用于掩盖缺失必需产物、冲突或阻塞。

## 8. 输出路径闭环

### 8.1 唯一运行根目录

v1 的全部正式产物必须位于：

```text
<repo>/local/筛选公司/<规范公司名>/
```

不接受绝对产物路径、自定义仓库外根目录或 `..`。`init` 从注册表生成每项精确路径或受限路径模板，并写入 manifest；执行者不能自行改变。现有子 Skill 中的 `local/reports/...`、`~/{公司名}...` 等历史保存位置，在本总控任务中被调用者给出的精确目标路径覆盖。

### 8.2 路径验证

gate 对每个声明产物执行：

- Unicode 规范化后拒绝绝对路径、空段、`.`、`..` 和控制字符；
- `resolve()` 后必须仍位于唯一运行根目录；
- 文件必须是普通文件、非空、非符号链接；
- 拒绝硬链接数大于 1 的产物；
- 拒绝解析到外部挂载点或不同设备的产物；
- 路径必须匹配注册表分配，不接受额外声明路径冒充正式产物。

### 8.3 越界写入侦测

`init` 记录工作树可见文件的路径、类型、大小、修改时间和内容摘要，不读取 `.env`、凭据目录或 `local/` 内容；私密/禁止前缀只记录不可逆的匿名状态摘要。每层 checkpoint 比较快照：

- 新增或修改的仓库文件若不在运行根目录且不属于本次实现明确允许的工具文件，当前运行失败；
- 注册表列出的历史外部保存模式作为 watchlist 单独检查；
- 对禁止读取目录只报告规则 ID 和“发生变化”，不打印文件名或内容；
- gate 明确披露：该检查不能证明任意仓库外位置从未被写入，因此总控 Skill 仍必须只向子流程提供注册表路径。

## 9. 自动执行流程

### 9.1 预检

1. 运行本地 `date` 和 `uname -m`，确认日期、时区和 arm64 环境。
2. 定位实际仓库根目录，读取 `AGENTS.md`、注册表和注册表指向的 20 个完整 Skill 规范。
3. 计算每个规范的 SHA-256；resume 时若摘要变化，受影响项必须重新验证，不能沿用旧 PASS。
4. 以公司名输入时，使用匹配的数据流程定位证券代码和主体。
5. 检查项目 Skill 包、工具入口和实际 CLI `--help`；文档示例与工具不一致时在执行前失败。
6. 运行 `init`，创建路径分配、manifest、快照和运行锁。

### 9.2 分层执行

| 层 | 工作契约 | 前置条件 |
|---|---|---|
| 1. 数据与快筛 | `ashare-data`、`financial-data`、`quality-screen`、`investment-checklist` | 标的身份已确认 |
| 2. 公司与财报 | `investment-research`、`investment-team`、`management-deep-dive`、`earnings-review`、`earnings-team` | 数据底座已生成 |
| 3. 行业与机会 | `industry-research`、`industry-funnel`、`bottleneck-hunter`、`news-pulse` | 公司和行业定义已稳定 |
| 4. 论文与边界 | `thesis-tracker`、`thesis-drift`、`portfolio-review`、`private-company-research` | 核心研究结论已形成 |
| 5. 内容生产 | `deep-company-series`、`dyp-ask`、`wechat-article` | 引用数据已冻结 |
| 6. 审计与收口 | `report_audit.py` + `full_analysis_gate.py finalize` | 无 PENDING/RUNNING 项 |

每项执行必须经过 `begin-skill` 和 `finish-skill`，每层结束后运行 `checkpoint`。失败时只修复本层或其依赖层，不重写不相关报告。

### 9.3 多角色能力与降级

- 只有当前平台、用户授权和对应 Skill 均允许时，才能创建真实独立角色会话。
- 不允许子代理时，可由主会话按角色顺序完成，但必须记录 `execution_mode=sequential_single_context`，不得声称真实并行或独立复核。
- 注册表为需要角色隔离的契约定义最低角色数、最低独立载体数和降级状态上限。
- 角色齐全但独立载体不足时，状态上限为 `PASS_WITH_LIMITATIONS`；缺少必需角色产物时为 `FAIL`。
- 线程分波复用必须记录角色数、实际线程数、复用关系和时间。

### 9.4 自动继续与暂停

以下情况自动继续：

- 单源但无冲突：标记证据不足，相关结论和工作契约状态上限为 `PASS_WITH_LIMITATIONS`；
- 主源失败但备用源成功：记录降级后继续；
- 不适用：命中注册表谓词后生成负向验收；
- 审计为 `INSUFFICIENT` 且该报告不是必需准出对象：保留限制继续，不能写成审计 PASS；
- 多角色能力受限但仍满足注册表的降级契约：按状态上限继续。

以下情况暂停并请求用户：

1. 公司名对应多个无法排除的精确实体或证券；
2. 用户要求组合优化，但必需的私人组合输入缺失；
3. 执行需要外部写入、发布、提交表单或不可逆操作；
4. 两个高信源存在会改变路由或核心结论的实质冲突。

## 10. 证据与“无感觉”机械约束

### 10.1 事实记录

每条关键事实记录：

- `fact_id`、字段名、主体、期间、单位、取值和允差；
- 每个来源的 `publisher_id`、`document_id` 或 URL、`source_type`、`acquisition_chain_id`、观察值和访问时间；
- gate 计算的 `DUAL_SOURCE` / `SINGLE_SOURCE` / `CONFLICT` / `UNAVAILABLE`。

`DUAL_SOURCE` 必须同时满足发布主体不同、获取链不同、期间/主体/单位可比且数值在注册允差内。同一发行人的多份报告、同一行情接口的转载、自洽重算和研究假设不得冒充第二源。来源真实性属于保证边界外，最终报告必须保留这一限制。

### 10.2 计算记录与重放

关键计算不接受任意 shell 字符串。记录必须使用 allowlist 中的结构化计算类型和参数，例如：

```json
{
  "calculation_id": "market-cap-a",
  "type": "verify-market-cap",
  "args": {
    "price": "41.20",
    "shares": "25219845601",
    "reported": "1039057636761.20",
    "currency": "CNY"
  },
  "expected_exit_code": 0,
  "stdout_sha256": "..."
}
```

`finalize` 使用当前 Python 解释器和项目内 `tools/financial_rigor.py` 重新执行，比较退出码和标准化输出摘要。参数不在 allowlist、重放不一致或工具缺失均为 `FAIL`。市值自洽重算与独立来源验证分别记录，前者不增加来源数。

### 10.3 判断记录

每个关键判断必须包含：

- `judgment_id` 与注册表中的 `rule_id`；
- 支撑它的 `fact_id` / `calculation_id` / 报告章节；
- 结论、置信度和至少一个证伪条件；
- 冲突处理和限制引用。

gate 强制以下上限：仅单源支撑的核心判断不能标为高置信；存在 `CONFLICT` 或 `UNAVAILABLE` 的必需事实时不能形成强结论；空证伪条件、悬空证据 ID 或不存在的规则均失败。gate 验证记录结构和证据关系，不替代对判断内容本身的专业复核。

### 10.4 执行收据

每项契约记录：规范摘要、attempt ID、开始/结束时间、执行模式、命令记录、角色记录和产物摘要。gate 检查这些记录与文件和注册表一致，但将其称为“执行收据”，不声称它能证明模型内部调用事件。真实前向实跑是验证总控行为的必要补充。

## 11. 状态判定

| 状态 | 机器条件 |
|---|---|
| `PASS` | 适用；全部必需产物、证据、角色和审计通过；无未解决限制或状态上限 |
| `PASS_WITH_LIMITATIONS` | 工作流完整；存在注册表允许且已披露的单源、时效、来源或平台降级；无硬冲突 |
| `NOT_APPLICABLE_PASS` | 注册谓词为真；谓词输入事实可验证；负向验收和正确替代路径均存在 |
| `FAIL` | 阻塞、缺必需项、伪造/悬空记录、计算重放失败、未解释冲突、路径/隐私越界或审计硬失败 |

调用者不能提交最终状态；`finalize` 根据规则计算。最终报告中的计数只能由 gate 输出生成。

## 12. 输出契约

```text
local/筛选公司/<公司名>/
├── 00-运行清单.md
├── 00-总体验收报告.md
├── 01-数据与快筛/
├── 02-公司与财报/
├── 03-行业与机会/
├── 04-论文与组合/
├── 05-内容生产/
├── 06-负向验收/
└── evidence/
    ├── 00-analysis-manifest.json
    ├── 01-数据来源与口径.md
    ├── 02-精确计算与交叉验证.md
    ├── 03-抽检结果.json
    ├── 03-报告审计.md
    ├── 04-验收器结果.json
    └── .full-analysis.lock
```

每个适用研究报告必须包含数据截止日、主体/期间口径、直接来源、限制和“仅供学习研究，不构成投资建议”。单项具体文件名、数量和章节以注册表分配为准。

## 13. 审计、隐私与安全

### 13.1 报告审计

- 需审计的报告由注册表明确标记，并使用项目 `report_audit.py`。
- 抽取 0 个样本点只能为 `INSUFFICIENT` 或 `FAIL`，永远不能 PASS。
- `INSUFFICIENT` 对必需准出报告导致该项 `FAIL`；对辅助报告只能降为 `PASS_WITH_LIMITATIONS`。
- 抽检底层 JSON、Markdown 汇总和 manifest 计数由 gate 在同一 finalize 中生成并交叉核对。

### 13.2 隐私扫描

- 默认不读取 `.env`、`local/`、原始交易台账、私钥、token 或 cookie 内容。
- 扫描使用明确禁止路径、凭据赋值模式和允许词表；公开财报中的“股本/持股数量”不得因泛关键词误判为私人台账。
- 命中凭据时只输出规则 ID、key 名和文件，不输出值、长度或局部字符。
- 用户私人组合缺失时不得用公开示例伪装真实组合。

### 13.3 外部动作边界

- 内容生产只生成本地草稿。
- 未经当前对话明确授权，不执行 commit、push、PR、issue、发布、发送、上传或任何外部写入。

## 14. 并发、恢复与错误语义

### 14.1 锁与原子写入

- `init` 使用排他创建生成 `evidence/.full-analysis.lock`，包含 run ID、host、pid、开始时间和 heartbeat。
- 同一目录存在活动锁时，`full` 和 `resume` 都拒绝启动第二个写者。
- 同 host 的 pid 已不存在，或 heartbeat 超过注册阈值，才能用显式 `--recover-stale` 恢复；恢复记录原锁摘要和原因。
- manifest 和 gate 输出先写相邻临时文件、完成 fsync 后原子替换；不让半写 JSON 成为继续依据。
- resume 不覆盖 `COMPLETE` 项；规范摘要、契约版本或依赖事实变化时，将受影响项重新置为 `PENDING` 并记录原因。

### 14.2 退出码

- `0`：对应命令完整通过；
- `1`：契约、数据、产物或最终验收失败；
- `2`：参数/schema 错误或非法状态转换；
- `3`：活动锁、陈旧锁未获恢复或并发冲突。

多个验收错误一次全部列出。manifest 解析失败时不猜测修复；finalize 失败时保留可恢复工作态和诊断，不生成貌似成功的最终报告。

## 15. 测试设计

### 15.1 已观测 RED 基线

不使用新 Skill 时的招商银行实际执行是行为基线：

- 需要人工分解多个任务并多次确认；
- `ashare-data` 项目包存在但当前会话未必加载；
- `earnings-review` 与 `earnings-team` 使用了当前 CLI 不接受的 `--metric/--sources`；
- 主体口径经独立复审后才修正；
- 报告审计曾出现底层与汇总计数不一致；
- 多个现有 Skill 默认写 `local/reports/...` 或 home 目录，与单一公司目录冲突。

旧结果只证明问题存在，不证明新 Skill 已解决问题。

### 15.2 注册表与单元测试

至少覆盖：

1. 注册表 schema 合法且恰好 20 个唯一索引/名称。
2. 每项均有独立产物规则、必需章节和专项证据，不允许空壳通用契约。
3. Skill 和 gate 从注册表读取集合，不维护第二份硬编码清单。
4. `init` 生成固定仓库内根目录和恰好 20 项路径分配。
5. 绝对路径、`..`、Unicode 绕过、符号链接、硬链接、外部设备均失败。
6. 仓库内越界修改和历史外部输出 watchlist 命中失败。
7. 缺必需章节、过短空壳报告、错误文件数量和悬空产物摘要失败。
8. DUAL_SOURCE 的 publisher 或 acquisition chain 重复时降为 SINGLE_SOURCE。
9. 主体、期间或单位不可比时不得计算为 DUAL_SOURCE。
10. CONFLICT 或 UNAVAILABLE 被强结论引用时失败。
11. 非 allowlist 计算失败；允许计算的重放退出码/输出不一致失败。
12. 判断缺 rule ID、证据 ID、置信度或证伪条件失败。
13. `PASS_WITH_LIMITATIONS` 无注册限制失败。
14. `NOT_APPLICABLE_PASS` 无谓词输入、负向产物或替代路径失败。
15. 多角色数量不足、伪称并行或状态超过能力上限失败。
16. 审计 0 样本点标 PASS 失败。
17. PENDING/RUNNING 不能 finalize；BLOCKED 只能计算为 FAIL。
18. 最终矩阵非 20 行或计数不一致失败。
19. 活动锁阻止第二写者，陈旧锁恢复有审计记录。
20. 原子写中断不破坏上一版有效 manifest。
21. resume 不覆盖有效 COMPLETE；规范摘要变化会使受影响项失效。
22. 凭据扫描只报告 key 名，公开股本描述不误报私人台账。
23. 核心 schema 未知字段失败；annotations 不参与准出且被披露。
24. 汇总结果只能从 gate 计算，调用者不能提交状态或计数。

### 15.3 静态与集成检查

- 对 `earnings-review`、`earnings-team` 中的 `financial_rigor.py` 示例执行参数解析测试，确保与当前 CLI 一致。
- 检查总控 Skill 明确覆盖子 Skill 历史默认保存路径。
- 检查注册表中的所有 `spec_source` 存在，摘要可计算，产物模板不冲突。
- 使用合成公司 fixture 完成一次 `init → begin/finish → checkpoint → finalize`，并验证假 manifest 不能只靠空壳文件通过。
- 使用项目生成同步检查和统一 `scripts/check.sh`。

### 15.4 真实前向评估

实现通过静态和自动测试后，必须使用一个真实标的从空目录运行一次完整流程：

- 不复用旧招商银行最终产物作为新运行产物；
- 记录真实数据源失败、角色执行模式、全部 20 项状态和总耗时；
- 人工只负责发起任务与处理真正暂停条件，不参与填 manifest、改状态或补计数；
- 再由独立评审或用户复核至少路径闭环、三项事实、三项计算和三项定性判断。

当前任务没有新的子代理授权，因此实现阶段不能把“独立子代理前向测试”写成已经完成。若只进行主会话顺序前向测试，相关多角色项按注册表状态上限降级。

## 16. 成功标准

### 16.1 实现完成

1. 编排文字只有 `skills/full-company-analysis.md` 一个规范源。
2. 机器集合与验收只有 `tools/full_analysis_contract.json` 一个注册源。
3. 验收器测试覆盖第 15.2 节全部行为，并先观测到对应 RED。
4. 两个财报 Skill 的 CLI 示例通过当前参数解析。
5. Codex Skill/Prompt 生成物与源文件同步。
6. 系统 `skill-creator` 的 `quick_validate.py` 若存在则通过；它只做包装格式检查，不替代项目门禁。
7. `python3 scripts/sync-codex-skills.py --check`、`python3 scripts/sync-codex-prompts.py --check` 和 `bash scripts/check.sh` 通过。
8. 安装副本只覆盖本次新增/修订 Skill，且与生成物逐字节一致。

### 16.2 可用性完成

除 16.1 全部通过外，还必须完成第 15.4 节真实前向评估。没有这项证据时，最终结论必须保留“尚未端到端验证”的限制。

## 17. 实施范围

计划新增或修改：

- `skills/full-company-analysis.md`
- `tools/full_analysis_contract.json`
- `tools/full_analysis_gate.py`
- `tests/test_full_analysis_gate.py`
- `skills/earnings-review.md`（只修正已失效 CLI 示例）
- `skills/earnings-team.md`（只修正已失效 CLI 示例）
- 对应的 `codex-skills/*/SKILL.md`（生成）
- 对应的 `codex-prompts/*.md`（生成）
- `~/.codex/skills/` 中本次新增/修订的本地安装副本

不修改其余 18 个业务 Skill 的领域内容，不重写共享金融工具、其他公司报告或全局规则。现有 Skill 的历史输出路径由总控的精确任务路径覆盖，并由 gate 侦测越界，不进行全仓库保存路径重构。

## 18. 部署边界

- 本地创建、生成、测试和本次相关 Skill 的本地安装属于已确认范围。
- 保持未提交；用户未明确要求时不创建 commit。
- 不执行 push、PR、issue、发布或任何外部写入。
