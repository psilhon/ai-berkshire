# 全量分析无人值守可靠性改造设计

> 日期：2026-07-23
> 状态：已确认，待实施
> 优先策略：先稳定全量分析主链路，再优化 21 个独立 Skill

## 1. 背景与证据

三次真实全量分析共记录 53 个症状项。表面问题包括 Tushare 不可达、
`assigned_artifacts` 类型不一致、计算 JSON schema 漂移、`set-industry`
缺 fact、`reference-freeze` 卡死、Agent 超时、429 限流、审计失败和最终产物
难以浏览。Darwin 对 21 个 Skill 的基线评估又发现，仅 5/21 的实际效果优于
无 Skill baseline，主要负迁移来自只读研究前的重复确认。

当前仓库的 `bash scripts/check.sh` 已通过 434 项测试、21 个 frontmatter 检查、
生成物同步、报告索引和 20 项契约校验，但真实运行仍需要大量人工修补。这说明
现有测试证明了组件内部行为，尚未证明真实六层生命周期可完成。

最关键的结构性证据是：真实运行依赖的
`~/.workbuddy/berkshire-skill-sync/orchestrate.py` 有 1,866 行，位于仓库外，
不受版本控制和项目测试约束。它同时维护层计划、角色、章节、证据桥接、
Tushare enrich、barrier 和 manifest 修改逻辑，形成仓库内 gate/contract 之外的
第二控制面。

## 2. 问题归并

53 个症状归并为八类根因：

1. **控制面分裂**：实际编排器在仓库外，版本和测试无法约束。
2. **状态所有权不清**：编排器绕过 gate 直接修改 manifest。
3. **契约多真源**：contract、编排器常量和 Agent prompt 分别维护章节、角色和产物。
4. **证据生产滞后**：报告先完成，facts/artifacts/calculations 再靠启发式补登记。
5. **数据生命周期错误**：`finish-skill` 后触发 enrich，但 gate 只允许
   `ashare-data=RUNNING` 时执行命令，构成确定性死锁。
6. **调度无韧性**：缺少并发控制、429 退避、租约、超时回收和自动降级。
7. **执行模式冲突**：全量模式已获用户授权，子 Skill 仍对只读研究和本地落盘重复 STOP。
8. **末端集中爆错**：缺陷直到 checkpoint/finalize 才累积为数百项错误，且缺少顶层交付入口。

## 3. 目标与成功标准

### 3.1 目标

将全量分析改造成仓库内可版本化、可测试、可恢复的产品级主链路。用户完成标的
身份确认后，系统应自动执行数据采集、20 项业务契约、审计和最终汇总，无须手工
编辑 manifest、补章节、登记 fact、重置 Agent 或替写报告。

### 3.2 最终成功标准

复用三次实跑中的三个代表性标的，执行 3 家 × 3 轮连续验收，并满足：

- 每轮人工干预次数为 0；
- 20/20 业务契约均进入非 `FAIL` 终态，无 `PENDING/RUNNING` 残留；
- 0 个 schema、类型、`AttributeError` 或过时命令形态错误；
- 所有报告自动包含数据截止日、来源层级、限制和免责声明；
- 多角色记录均对应真实上下文与真实产物，不自动捏造 role receipt；
- persistent 429、Agent 超时或数据源不可用时，在有界时间内得到诚实的
  `PASS_WITH_LIMITATIONS` 或 `FAIL`，不得永久挂起；
- 单轮硬上限 4 小时，9 轮中位数目标不超过 3 小时；
- 仓库外不再保存业务控制逻辑，只允许薄启动入口；
- hermetic 端到端测试进入 `scripts/check.sh`，真实联网 canary 独立执行。

## 4. 范围

### 4.1 本阶段包含

- 编排控制面迁入仓库；
- manifest 单写者原则；
- contract 驱动的工作包、章节、角色和产物；
- 标准 Result Bundle 与逐 Skill 原子验收；
- 分层数据窗口与自动 fact 登记；
- 429/超时/重试/降级调度；
- `full_analysis_noninteractive` 执行档；
- 自动审计、运行摘要、恢复入口和可观测指标；
- hermetic E2E、故障注入和 3×3 live canary。

### 4.2 本阶段不包含

- HTML 报告、暗色模式和图表；
- 多股票横向分析；
- 新数据供应商；
- 自动发布、push、PR、发送或交易；
- 全面重写或压缩 21 个 Skill；
- 删除历史报告或立即删除旧外置编排器。

主链路稳定前，只允许修改直接阻断主链路的 Skill 行为。独立 Skill 的 Darwin
优化作为后续阶段，避免同时改变编排、契约和全部方法论而失去归因能力。

## 5. 项目治理定位

本仓库按 `product` profile 处理：已有版本、公开仓库、稳定 CLI/Skill 行为和持续
维护需求。沿用现有 `ci` 与 `security` 能力；本次启用 `adr` 记录控制面迁移和
manifest 单写者决策。不新增 hook、发布流程、依赖或新顶层源码骨架。

## 6. 目标架构

```text
用户全量分析请求
        |
        v
仓库内 Orchestrator -------- Contract Registry（唯一契约真源）
        |                                |
        +--> 数据窗口                    +--> 工作包/章节骨架/角色/产物
        |
        +--> 有界任务队列 --> 平台 Agent Adapter --> Result Bundle
                                                    |
                                                    v
                                     Gate（manifest 唯一写入者）
                                                    |
                                                    v
                                  SUMMARY.md + 状态矩阵 + 全部报告索引
```

### 6.1 组件职责

#### 仓库内 Orchestrator

- 读取 contract，生成层计划和 work unit；
- 管理任务队列、租约、重试和 resume；
- 生成 Agent 工作包并接收 Result Bundle；
- 只能通过 gate CLI 改变业务 manifest；
- 可维护独立的调度状态文件，但该文件不能决定业务准出状态。

#### Contract Registry

- 是业务项、阶段、角色、section、artifact、数据依赖和审计策略的唯一机器真源；
- 通过 `schema_version` 迁移；
- 编排器不得再硬编码 `LAYER_PLAN`、`MULTI_ROLE` 或
  `SKILL_REQUIRED_SECTIONS` 的业务副本。

#### Gate

- 是 manifest 唯一写入者；
- 负责状态转换、证据封闭 schema、计算重放、引用冻结和最终准出；
- `finish-skill` 必须先做当前 Skill 的局部验收，失败时保持可重试状态，不能先标
  `COMPLETE` 再把数百个问题留给 finalize；
- 任何调用者都不能提交最终状态、保障等级或伪造 command/role receipt。

#### Platform Agent Adapter

- Codex、Claude Code、WorkBuddy 只实现启动 Agent、回传真实执行标识和结果；
- 不复制业务契约或写 manifest；
- 平台不支持独立 Agent 时诚实降级，不以平台名假设独立性。

## 7. Contract v2

### 7.1 稳定章节标识

`required_sections` 从裸标题字符串升级为结构化规则：

```json
{
  "section_id": "data_cutoff",
  "heading": "数据截止日",
  "required": true,
  "min_content_chars": 10,
  "applies_when": "always"
}
```

Orchestrator 从规则生成带稳定 marker 的 Markdown 骨架；Agent 可自由写正文，但不能
删除 section ID。过渡期允许旧标题 alias，完成 3×3 canary 后移除兼容分支。

### 7.2 工作包

每个 work unit 至少包含：

- `run_id`、`skill`、`attempt_id` 和截止日期；
- 精确输入 artifact/fact/data bundle；
- 唯一合法输出路径；
- contract 生成的章节骨架；
- 角色与执行模式；
- Result Bundle schema 版本；
- `full_analysis_noninteractive` 权限边界；
- 超时、重试和降级策略。

### 7.3 Result Bundle

每个 Skill 提交一个封闭结果包：

- `artifacts`：稳定 artifact ID 与路径；
- `facts`：字段、口径、期间、单位、来源和访问时间；
- `calculations`：实际运行 `financial_rigor.py --json` 的原始 envelope；
- `judgments`：规则、结论、置信度、证伪条件和引用；
- `role_runs`：平台返回的真实上下文标识、时间和真实角色产物；
- `limitations`：结构化限制；
- `audit`：需要人工/Agent 抽检时的原始核验输入。

Orchestrator 不得根据“预期角色数”自动生成 role receipt，不得把不存在的文件路径
登记为真实产物，也不得把报告文本中的任意数字启发式升级为已核事实。

## 8. 数据生命周期

### 8.1 分层数据窗口

替代“Skill 完成后补跑 enrich”的时序：

1. **基础数据窗口**：Layer 1 前执行行情、财务、估值、历史、股本和公告依赖；
2. **行业增强窗口**：Layer 2 fact 闭环、`set-industry` 后执行行业相关数据依赖；
3. **引用冻结**：Layer 1-4 局部验收通过后冻结数据与引用，Layer 5 只消费冻结输入。

`run-ashare-command` 的许可条件由数据窗口状态决定，不再绑定
`ashare-data=RUNNING`。引用冻结后禁止继续改变已消费的事实。

### 8.2 事实登记

- 结构化数据命令直接生成 command receipt 和事实候选；
- collect 阶段按封闭 schema 登记 facts，不从自由文本猜测字段；
- `set-industry` 只消费已经登记且来源可追溯的分部事实；
- 数据缺失在消费者启动前暴露，不等 barrier 才发现空 fact ID。

### 8.3 Tushare 与降级

- 环境自检执行实际 CLI smoke test，只记录配置状态，不读取或输出 token；
- 配置但不可达与未配置是不同状态；
- Tushare 失败后按腾讯/东财/巨潮等既有来源降级；
- 只有满足契约最小证据时才允许 PWL，否则 FAIL；
- 数据源优先级由报告壳统一生成，不要求每个 Agent 自行记忆。

## 9. 调度与恢复

### 9.1 状态

调度状态与业务状态分离。调度 work unit 使用：

`PENDING → DISPATCHED → RUNNING → READY_TO_INGEST → DONE`

异常分支为 `RETRY_WAIT` 和 `FAILED`。业务 manifest 仍使用 gate 现有状态，只有合法
Result Bundle 被 gate 接受后才可进入 `COMPLETE`。

### 9.2 并发、429 与超时

- Web-heavy 默认并发 2，本地计算任务可更高；
- 尊重 `Retry-After`，指数退避并加入抖动；
- 单 work unit 最多 3 次尝试；
- 每个运行中的 unit 有 lease 和最后心跳；
- lease 到期自动回收并重试，不要求人工把 RUNNING 改回 PENDING；
- persistent 429 或平台不可用时执行 contract 声明的降级策略并在有界时间内收口。

### 9.3 多角色真实性

- 角色名来自 contract；
- 每个角色对应平台返回的真实 context ID 和独立产物；
- 缺角色、角色文件不存在或上下文重复时不得生成独立性 credit；
- 平台无法完成 fan-out 时可顺序降级，但必须标记 `SINGLE_CONTEXT`，并按 contract
  封顶 PWL 或 FAIL。

## 10. 执行模式

新增 `full_analysis_noninteractive` 执行档。顶层用户已明确要求完整研究后，以下动作
不再由子 Skill 重复确认：

- 公开网页和公开数据的只读检索；
- 仓库内确定性计算；
- 启动研究 Agent；
- 向本次 run-root 的已分配路径写研究产物。

以下行为继续 HARD STOP：

- 标的身份存在无法排除的多个候选；
- 读取私人组合、原始交易台账或敏感信息；
- 外部发送、发布、push、PR 或表单提交；
- 交易操作；
- 覆盖、删除或迁移既有用户数据。

该执行档只覆盖全量编排。独立 Skill 的广泛 STOP 精简在 3×3 验收之后根据 Darwin
结果单独实施。

## 11. 逐 Skill 早验收

每个 Skill 的收口顺序固定：

1. 检查 Result Bundle schema；
2. 检查 artifact 存在、路径、section marker 和最低内容；
3. 登记 facts、calculations、judgments、roles 和 limitations；
4. gate 重放计算与局部证据规则；
5. 全部通过后原子进入 `COMPLETE`；
6. 失败则返回结构化诊断，调度器决定修复 prompt、重试或失败收口。

不得再通过自动 append/移动/删除中间目录来“自愈”产物。未知目录只报告，不修改；
Agent 必须按工作包的精确路径重新提交。

## 12. 审计与交付

- Layer 6 由 Orchestrator 调用 gate/report audit API，不让 Agent拼 CLI；
- 每份报告的页眉页脚由确定性报告壳生成，包括截止日期、来源优先级、限制和免责声明；
- finalize 总是写出 `SUMMARY.md` 与机器可读 `status.json`；
- SUMMARY 包含 20 项矩阵、PASS/PWL/N/A/FAIL 计数、限制、数据带宽、角色保障、
  运行耗时、重试次数和全部正式产物链接；
- `status --company/--run-id` 与 `resume` 不依赖原临时 session；
- finalize 失败也写诊断摘要，但必须醒目标记“未准出”。

## 13. 测试策略

### 13.1 单元测试

- Contract v2、section marker、Result Bundle 和状态转换；
- 数据窗口与引用冻结；
- 429 退避、lease 回收和最大重试；
- 角色真实性和降级上限；
- summary 计数与路径。

### 13.2 Hermetic 端到端测试

使用 fake Agent executor 和 fake data CLI 跑完整六层，不联网，至少注入：

- Tushare 未配置、已配置但失败；
- post-finish enrich 时序回归；
- 空 fact、悬空 fact 和错误 `--fact-ids`；
- `assigned_artifacts` 旧/新形态；
- 非法 calculations envelope；
- 缺 section marker；
- 429 后恢复、persistent 429；
- Agent 超时和 stale lease；
- 审计不足和审计 FAIL；
- 多角色缺失、重复上下文和真实独立上下文。

Hermetic E2E 纳入 `scripts/check.sh`。真实联网 canary 不纳入普通 CI，避免上游波动
制造假失败。

### 13.3 Live canary

用三个代表性标的各跑三轮。每轮自动记录：

- 人工干预计数；
- 20 项终态；
- wall clock 与 Agent 调用数；
- 429、重试、超时和降级次数；
- 数据源能力与限制；
- audit/finalize 结果；
- SUMMARY 路径。

只有连续 9 轮全部满足第 3.2 节成功标准，才宣布主链路稳定。

## 14. 迁移策略

1. **Shadow**：先把外置编排器行为固化为测试，再在仓库内实现等价入口；
2. **Dual-run**：同一 fixture 同时跑旧/新控制面，比对计划、work unit 和 gate 结果；
3. **Repository-first**：WorkBuddy/Codex/Claude 入口改为调用仓库内实现，外置脚本降为薄 wrapper；
4. **Canary**：完成 3×3 live canary；
5. **Retire**：另行确认后才删除旧外置实现和过渡 alias。

迁移期间不得重写历史 `local/` 报告，不得删除现有备份或未跟踪文件。

## 15. 风险与缓解

| 风险 | 概率×影响 | 缓解 |
|---|---:|---|
| 迁入仓库后破坏 WorkBuddy 现有入口 | 2×3 | Shadow + dual-run + 薄 wrapper，可回退旧入口 |
| Contract v2 使旧 manifest 不可恢复 | 2×3 | schema version、只读兼容器、fixture 覆盖，不原地改历史运行 |
| 非交互模式误放宽安全边界 | 1×3 | 只覆盖公开只读与本次 run-root；外部写入/交易/敏感信息继续 HARD STOP |
| 固定骨架降低报告表达质量 | 2×2 | section ID 稳定，正文自由；只约束结构不约束观点 |
| Live canary 受外部数据波动影响 | 3×2 | hermetic E2E 与 live canary 分离；限制必须诚实但不得挂死 |
| 多角色成本与 429 冲突 | 3×2 | 有界并发、租约、退避、按 contract 诚实降级 |

所有风险分值达到 6 的项均在迁移、兼容或调度设计中有明确缓解。

## 16. 后续 Darwin 优化门槛

主链路通过 3×3 后，才开始独立 Skill 优化，目标为：

- 带 Skill 相对 baseline 为正或持平的项目达到至少 16/21；
- 不再出现 d8 下降超过 1 分的 Skill；
- runtime 人工风险从当前 9 项降为 0；
- 优先修复 private-company-research、investment-research、earnings-review、
  investment-team、financial-data 的只读 STOP；
- 补 thesis-drift 成功路径评估；
- 压缩 private-company-research、bottleneck-hunter、earnings-team 的重复模板。

该阶段另写实施计划，不与主链路可靠性改造混合提交。
