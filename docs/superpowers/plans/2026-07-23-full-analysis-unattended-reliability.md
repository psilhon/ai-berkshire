# Full-analysis Unattended Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. For every behavior change, use `superpowers:test-driven-development`; on any unexpected failure, switch to `superpowers:systematic-debugging` before changing implementation.

**Goal:** 把全量公司分析迁成仓库内、可测试、可恢复的单一控制面，并通过恒瑞医药、韦尔股份、格力电器 3 家 × 3 轮无人值守验收。

**Architecture:** `tools/full_analysis_orchestrator.py` 负责生成 work unit、调度与恢复；`tools/full_analysis_contract.json` 是业务契约唯一机器真源；`tools/full_analysis_gate.py` 是业务 manifest 唯一写入者；平台 Agent 只消费 work packet 并提交封闭 Result Bundle。数据命令在显式数据窗口中执行，不再绑定 `ashare-data=RUNNING`；每个 Skill 在进入 `COMPLETE` 前做局部原子验收。

**Tech Stack:** Python 3 标准库、JSON、Markdown、现有 `unittest`、`financial_rigor.py`、`report_audit.py`、`ashare_data.py`；不新增第三方依赖。

**Design reference:** `docs/superpowers/specs/2026-07-23-full-analysis-unattended-reliability-design.md`

---

## Global Constraints

- 保留现有未跟踪文件 `tools/full_analysis_gate.py.bak-20260722-202828`，不得删除、覆盖、暂存或提交。
- 不改写 `local/` 下任何历史报告、manifest 或实盘记录；测试只使用临时目录。
- 不读取或输出 `TUSHARE_TOKEN` 的值，只允许记录 `configured/not_configured`。
- 不修改仓库外 `/Users/psilhon/.workbuddy/berkshire-skill-sync/orchestrate.py`，直到 Task 13 的迁移 checkpoint 获得明确确认。
- 不执行 push、PR、publish、外部消息或交易动作。
- `skills/*.md` 是 canonical；修改后必须运行 `python3 scripts/sync-codex-skills.py`，不得手改生成副本。
- 每个 Task 完成时只暂存该 Task 明确列出的文件，禁止 `git add .`、`git add -A`。
- 每个 milestone 后运行独立回归；最终必须运行 `bash scripts/check.sh`。
- 实现过程中如需改变本计划的持久化 schema、模块边界或新增依赖，属于架构变化，先暂停并回到设计确认。

---

## File Structure and Stable Interfaces

| File | Responsibility |
|---|---|
| `tools/full_analysis_orchestrator.py` | CLI、contract-derived plan/work packet、gate/runtime 协调；不写 manifest |
| `tools/full_analysis_runtime.py` | 持久任务队列、lease、retry、并发窗口；不判断业务 PASS/FAIL |
| `tools/full_analysis_gate.py` | manifest 唯一写入、Result Bundle ingest、局部/最终验收、summary |
| `tools/full_analysis_contract.json` | 20 项业务契约、阶段、section、role、data requirement 唯一真源 |
| `tools/full_analysis_result_schema.json` | Work Packet/Result Bundle 的版本化协议说明 |
| `scripts/orchestrate_full_analysis.py` | canonical orchestrator 的薄命令入口 |
| `scripts/run_full_analysis.py` | 迁移期兼容入口；最终只给弃用提示并转向 canonical CLI |
| `scripts/batch_full_analysis.py` | 迁移期批量入口；最终只调用 canonical runs，不解析财务文本或生成报告 |
| `tests/test_full_analysis_e2e.py` | fake Agent + fake data 的六层 hermetic 验收 |

实现中统一使用以下公开函数和状态名，后续 Task 不得另起同义接口：

- `load_contract(path: Path) -> dict`
- `build_plan(contract: dict) -> list[dict]`
- `build_work_packet(manifest: dict, contract: dict, skill: str, attempt_id: str, execution_profile: str) -> dict`
- `GateClient.run(command: str, *args: str) -> subprocess.CompletedProcess[str]`
- `GateClient.read_manifest(run_root: Path) -> dict`
- `FullAnalysisRuntime.next_work(limit: int, now: datetime) -> list[dict]`
- `FullAnalysisRuntime.mark_running(unit_id: str, context_id: str, now: datetime) -> None`
- `FullAnalysisRuntime.submit_result(unit_id: str, result_path: Path, now: datetime) -> None`
- `FullAnalysisRuntime.record_failure(unit_id: str, kind: str, retry_after_seconds: int | None, now: datetime) -> None`
- `FullAnalysisRuntime.reclaim_expired(now: datetime) -> list[str]`

稳定枚举：

```python
DATA_PHASES = ("BASE_OPEN", "BASE_FROZEN", "INDUSTRY_OPEN", "REFERENCE_FROZEN")
WORK_STATES = ("PENDING", "DISPATCHED", "RUNNING", "RETRY_WAIT",
               "READY_TO_INGEST", "DONE", "FAILED")
EXECUTION_PROFILES = ("standalone", "full_analysis_noninteractive")
```

---

## Milestone 0 — 将真实故障变成可执行回归

### Task 1: 建立三次实跑故障语料与验收词汇

**Files:**
- Create: `tests/fixtures/full_analysis/incidents.json`
- Create: `tests/test_full_analysis_incidents.py`

**Purpose:** 把截图和运行日志中的 53 个症状归并为稳定、脱敏、可被后续 E2E 使用的故障场景，避免计划实施后只剩口头记忆。

- [ ] **Step 1: 定义 fixture schema 的测试**

在 `tests/test_full_analysis_incidents.py` 写测试，要求每个场景至少含：

```python
REQUIRED = {
    "incident_id", "root_cause", "stage", "injected_fault",
    "expected_outcome", "manual_action_forbidden",
}
```

测试还必须断言：

- 三个 canary 公司均出现：`恒瑞医药`、`韦尔股份`、`格力电器`；
- 根因至少覆盖 control-plane、schema、data-window、facts、sections、scheduler、audit、delivery 八类；
- 存在 429、Agent timeout、Tushare unavailable、invalid calculation、empty fact、missing section、artifact type mismatch 场景；
- 所有 `manual_action_forbidden` 均为 `true`。

- [ ] **Step 2: 运行测试确认 RED**

Run: `python3 -m unittest tests.test_full_analysis_incidents -v`

Expected: FAIL，fixture 尚不存在。

- [ ] **Step 3: 写最小 incidents fixture**

每个根因至少一条；将三次实跑中重复症状映射到同一 `root_cause`。不要复制真实报告正文、凭据、完整 URL 查询参数或私人数据。

- [ ] **Step 4: 运行测试确认 GREEN**

Run: `python3 -m unittest tests.test_full_analysis_incidents -v`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/full_analysis/incidents.json tests/test_full_analysis_incidents.py
git commit -m "test(full-analysis): 固化三次实跑故障场景"
```

---

## Milestone 1 — 单一控制面进入仓库

### Task 2: 新建仓库内 canonical orchestrator 与兼容 CLI

**Files:**
- Create: `tools/full_analysis_orchestrator.py`
- Create: `scripts/orchestrate_full_analysis.py`
- Create: `tests/test_full_analysis_orchestrator.py`
- Modify: `scripts/check.sh`

**Interfaces:**

- CLI 首批支持：`plan`、`status`、`init`、`drive-layer`、`collect-layer`；
- `scripts/orchestrate_full_analysis.py` 只是薄入口；
- 计划和角色必须从 contract 读取，不复制外置脚本的 `LAYER_PLAN`/`MULTI_ROLE` 常量。

- [ ] **Step 1: 写失败测试——仓库内入口必须存在并从 contract 生成 plan**

测试使用临时 contract，将 skill 名、stage、role 动态改名，然后运行：

```bash
python3 tools/full_analysis_orchestrator.py plan --registry /tmp/full-analysis-contract-test.json
```

断言输出跟随临时 contract 变化，证明没有硬编码业务副本。

- [ ] **Step 2: 写失败测试——薄脚本与模块输出一致**

分别运行 `tools/full_analysis_orchestrator.py plan` 与
`scripts/orchestrate_full_analysis.py plan`，标准化 JSON 后必须相等。

- [ ] **Step 3: 运行目标测试确认 RED**

Run: `python3 -m unittest tests.test_full_analysis_orchestrator -v`

Expected: FAIL，模块与入口尚不存在。

- [ ] **Step 4: 实现最小 contract-driven orchestrator**

只实现本 Task 测试要求的读取、plan/status/init 包装和 CLI 解析。不要迁入以下旧行为：

- 直接 `save_manifest`；
- 自动移动、append 或删除中间目录；
- 自动构造 role receipt；
- post-finish auto-enrich；
- 编排器内的章节/角色硬编码表。

- [ ] **Step 5: 实现薄入口**

`scripts/orchestrate_full_analysis.py` 只导入并调用模块 `main()`，不维护业务逻辑。

- [ ] **Step 6: 将 orchestrator 测试显式加入本地检查说明**

`unittest discover` 本应自动发现新测试；在 `scripts/check.sh` 注释中把 full-analysis orchestrator/E2E 列入测试职责，不新增第二套命令。

- [ ] **Step 7: 运行测试确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_orchestrator -v
bash scripts/check.sh
```

Expected: 全部 PASS；现有 434 项不得回归。

- [ ] **Step 8: Commit**

```bash
git add tools/full_analysis_orchestrator.py scripts/orchestrate_full_analysis.py tests/test_full_analysis_orchestrator.py scripts/check.sh
git commit -m "feat(full-analysis): 将编排控制面迁入仓库"
```

### Task 3: Gate 独占 manifest 写入与原子 reference freeze

**Files:**
- Modify: `tools/full_analysis_gate.py`
- Modify: `tools/full_analysis_orchestrator.py`
- Modify: `tests/test_full_analysis_gate.py`
- Modify: `tests/test_full_analysis_orchestrator.py`

**Interfaces:**

- 新 gate 命令：`freeze-references --run-root RUN_ROOT --mode self_review|independent_context`；
- 命令内部依次完成 checkpoint、review mode 校验和 `reference_frozen=true` 原子写入；
- Orchestrator 只调用 gate，不直接写 manifest。

- [ ] **Step 1: 写 gate RED 测试**

覆盖：

1. checkpoint 失败时不写 `reference_frozen`；
2. independent context 不足时不写；
3. 成功时一次写入 `review_mode`、`reference_frozen`、`frozen_at`；
4. 重复调用幂等；
5. finalize 后禁止调用。

- [ ] **Step 2: 写 orchestrator RED 测试**

使用 fake gate 记录 argv，断言 `reference-freeze` 只转发为
`freeze-references`，且 orchestrator 在只读 manifest 上仍可运行。

- [ ] **Step 3: 运行目标测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_gate -v -k freeze_references
python3 -m unittest tests.test_full_analysis_orchestrator -v -k reference
```

- [ ] **Step 4: 在 gate 实现原子命令**

复用现有 checkpoint/review 校验函数；在内存中全部验证成功后只调用一次
`save_manifest`。禁止 orchestrator 复制校验逻辑。

- [ ] **Step 5: 让 orchestrator 只调用 gate**

新增小型 `GateClient`，只暴露读取摘要和执行 gate CLI；模块中不得出现 manifest
写入辅助函数。

- [ ] **Step 6: 删除新控制面中的 destructive self-heal 设计**

若 Task 2 移植时出现任何目录自动归并逻辑，改为只读诊断：返回
`unexpected_paths`，不执行 `shutil.rmtree`、append 或覆盖。

- [ ] **Step 7: 运行回归确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_gate tests.test_full_analysis_orchestrator -v
bash scripts/check.sh
```

- [ ] **Step 8: Commit**

```bash
git add tools/full_analysis_gate.py tools/full_analysis_orchestrator.py tests/test_full_analysis_gate.py tests/test_full_analysis_orchestrator.py
git commit -m "refactor(full-analysis): gate 独占 manifest 状态写入"
```

---

## Milestone 2 — Contract v2 与逐 Skill 原子验收

### Task 4: 将章节、角色和数据依赖统一到 Contract v2

**Files:**
- Modify: `tools/full_analysis_contract.json`
- Modify: `scripts/check-full-analysis-contract.py`
- Modify: `tools/full_analysis_gate.py`
- Modify: `tests/test_full_analysis_contract.py`
- Modify: `tests/test_full_analysis_gate.py`
- Modify: `tests/test_full_analysis_phase2.py`

**Schema changes:**

- registry `schema_version` 升级；
- `required_sections` 从字符串数组迁为对象数组；
- 每项显式包含 `stage_order`、`role_rule`、`data_requirements`；
- 旧 manifest 只读兼容，不原地改写历史运行。

- [ ] **Step 1: 写 Contract v2 RED 测试**

每个 section 必须且只能含：

```json
{
  "section_id": "stable_snake_case",
  "heading": "展示标题",
  "required": true,
  "min_content_chars": 10,
  "aliases": []
}
```

测试 section ID 在单 Skill 内唯一、标题非空、aliases 不与其它 canonical heading 冲突。

- [ ] **Step 2: 写 contract-derived stage/role RED 测试**

测试脚本不得依赖固定 20 项列表；阶段顺序、角色和数据依赖全部从 registry 计算。

- [ ] **Step 3: 写 legacy manifest 只读兼容 RED 测试**

加载 v1 fixture 可以执行 `summary/status`，但任何继续写入命令返回明确迁移提示，不修改历史文件。

- [ ] **Step 4: 运行目标测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_contract -v
python3 -m unittest tests.test_full_analysis_gate -v -k section
```

- [ ] **Step 5: 迁移 20 项 contract**

为每个旧 required heading 分配稳定 `section_id`。过渡 aliases 只收录三次实跑中已经出现的合理同义标题，不接受任意模糊包含匹配。

- [ ] **Step 6: 更新 gate 与独立 contract checker**

使用 Markdown heading/marker 结构校验，不再对全文做裸 substring。错误信息固定输出
`skill + artifact + section_id + expected_heading`。

- [ ] **Step 7: 更新 20 项参数化 fixture**

`tests/test_full_analysis_phase2.py` 的合法产物生成器必须从 contract 生成 section scaffold，禁止在测试里复制另一份标题表。

- [ ] **Step 8: 运行回归确认 GREEN**

```bash
python3 scripts/check-full-analysis-contract.py
python3 -m unittest tests.test_full_analysis_contract tests.test_full_analysis_gate tests.test_full_analysis_phase2 -v
```

- [ ] **Step 9: Commit**

```bash
git add tools/full_analysis_contract.json scripts/check-full-analysis-contract.py tools/full_analysis_gate.py tests/test_full_analysis_contract.py tests/test_full_analysis_gate.py tests/test_full_analysis_phase2.py
git commit -m "feat(full-analysis): Contract v2 统一章节角色与数据依赖"
```

### Task 5: 标准 Work Packet 与 Result Bundle

**Files:**
- Create: `tools/full_analysis_result_schema.json`
- Modify: `tools/full_analysis_orchestrator.py`
- Modify: `tools/full_analysis_gate.py`
- Modify: `tests/test_full_analysis_orchestrator.py`
- Modify: `tests/test_full_analysis_gate.py`
- Modify: `scripts/check-full-analysis-contract.py`

**Interfaces:**

- Orchestrator: `work-packet --run-root RUN_ROOT --skill SKILL --attempt-id ATTEMPT_ID`；
- Gate: `ingest-result --run-root RUN_ROOT --skill SKILL --result-file RESULT_FILE`；
- Result Bundle 顶层键固定为 `schema_version/attempt/artifacts/facts/calculations/judgments/role_runs/limitations/audit`。

- [ ] **Step 1: 写 work packet RED 测试**

断言工作包包含精确 artifact 路径、contract-derived section scaffold、真实输入 fact/artifact ID、角色、profile 和结果 schema 版本。

- [ ] **Step 2: 写 Result Bundle schema RED 测试**

覆盖截图中的错误形态：

- `assigned_artifacts` 为 `list[str]`；
- artifact 缺 `artifact_id`；
- calculations 为 dict 而非 array；
- `expected.result` 字段缺失；
- role 名称与 contract 不一致；
- role path 不存在；
- context ID 重复。

以上必须在 ingest 阶段得到结构化错误，不能抛 `AttributeError`/`TypeError`。

- [ ] **Step 3: 写原子 finish RED 测试**

非法 bundle 被拒绝后，manifest 内容哈希不变、Skill 不进入 `COMPLETE`；合法 bundle
一次登记全部证据并进入 `COMPLETE`。

- [ ] **Step 4: 写 calculations direct-capture RED 测试**

测试 Result Bundle 接受 `financial_rigor.py --json` 原始 envelope；gate 从 envelope
派生 expected 并独立重放，不要求 Agent 手抄两套结构。

- [ ] **Step 5: 运行目标测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_orchestrator tests.test_full_analysis_gate -v -k result
```

- [ ] **Step 6: 实现结果 schema 与手写标准库校验器**

不引入 `jsonschema` 依赖。JSON 文件是协议文档真源；checker 校验 gate 支持的版本与文件一致。

- [ ] **Step 7: 实现 work packet 生成**

Markdown scaffold 使用稳定 section marker，例如：

```markdown
<!-- section:data_cutoff -->
## 数据截止日
```

- [ ] **Step 8: 实现 `ingest-result` 原子路径**

先在内存副本完成 schema、artifact、section、facts、calculations、roles 和局部 evidence
规则检查；全部通过后一次写 manifest。保留旧 `finish-skill --evidence-file` 作为迁移期
兼容入口，但新 orchestrator 不再调用它。

- [ ] **Step 9: 运行回归确认 GREEN**

```bash
python3 scripts/check-full-analysis-contract.py
python3 -m unittest tests.test_full_analysis_gate tests.test_full_analysis_orchestrator tests.test_financial_rigor -v
```

- [ ] **Step 10: Commit**

```bash
git add tools/full_analysis_result_schema.json tools/full_analysis_orchestrator.py tools/full_analysis_gate.py tests/test_full_analysis_orchestrator.py tests/test_full_analysis_gate.py scripts/check-full-analysis-contract.py
git commit -m "feat(full-analysis): 引入标准工作包与原子结果收口"
```

---

## Milestone 3 — 修复数据生命周期与 fact 闭环

### Task 6: 数据窗口替代 post-finish auto-enrich

**Files:**
- Modify: `tools/full_analysis_gate.py`
- Modify: `tools/full_analysis_contract.json`
- Modify: `tools/full_analysis_orchestrator.py`
- Modify: `tests/test_full_analysis_gate.py`
- Modify: `tests/test_full_analysis_orchestrator.py`
- Modify: `tests/test_full_analysis_phase2.py`

**Manifest addition:**

```json
"data_state": {
  "phase": "BASE_OPEN|BASE_FROZEN|INDUSTRY_OPEN|REFERENCE_FROZEN",
  "command_receipts": [],
  "facts": []
}
```

- [ ] **Step 1: 写数据窗口状态机 RED 测试**

覆盖合法/非法转换、引用冻结后不可再运行数据命令、finalize 后不可改变数据。

- [ ] **Step 2: 写 auto-enrich 死锁回归测试**

构造 `ashare-data=COMPLETE` 且 data window 仍开放的运行，执行 contract 声明的下游数据命令应成功；同一命令在 `REFERENCE_FROZEN` 后应拒绝。

- [ ] **Step 3: 写能力检查 RED 测试**

使用 fake `ashare_data.py`：

- 未配置 token；
- 已配置但命令失败；
- 主源失败、备用源成功；
- 全部源失败。

测试只传布尔能力，不读取 token 内容。

- [ ] **Step 4: 运行目标测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_gate -v -k data_window
python3 -m unittest tests.test_full_analysis_orchestrator -v -k enrich
```

- [ ] **Step 5: 实现 gate 数据窗口命令**

新增 `open-data-window` / `freeze-data-window`，并让 `run-ashare-command` 按
`data_state.phase` 授权。command receipt 放在 run-level `data_state`，不依赖
ashare-data 的 execution state。

- [ ] **Step 6: 让 orchestrator 按 contract 预计算命令并分批执行**

- Layer 1 前执行基础需求并冻结；
- Layer 2 fact 收口和 `set-industry` 后执行行业增强；
- 删除新控制面中的 finish 后 enrich 路径。

- [ ] **Step 7: 更新 gate 证据规则读取共享 receipt/fact**

现有 ashare-data/consumer 的命令和 lineage 规则必须能引用 run-level data IDs；悬空、
后向引用和未成功命令继续 FAIL。

- [ ] **Step 8: 运行回归确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_gate tests.test_full_analysis_orchestrator tests.test_full_analysis_phase2 -v
bash scripts/check.sh
```

- [ ] **Step 9: Commit**

```bash
git add tools/full_analysis_gate.py tools/full_analysis_contract.json tools/full_analysis_orchestrator.py tests/test_full_analysis_gate.py tests/test_full_analysis_orchestrator.py tests/test_full_analysis_phase2.py
git commit -m "fix(full-analysis): 以数据窗口消除 enrich 时序死锁"
```

### Task 7: 自动登记结构化 facts 并闭合 set-industry

**Files:**
- Modify: `tools/full_analysis_gate.py`
- Modify: `tools/full_analysis_orchestrator.py`
- Modify: `tests/test_full_analysis_gate.py`
- Modify: `tests/test_full_analysis_orchestrator.py`

- [ ] **Step 1: 写 fact ingest RED 测试**

合法 Result Bundle 的 facts 在 ingest 时进入 manifest；重复 fact ID、悬空 source、
非法数值或口径缺失必须在当前 Skill 被拒绝。

- [ ] **Step 2: 写 `set-industry` work packet RED 测试**

Layer 2 完成后，orchestrator 必须从已登记 facts 选择分部收入/营业收入候选并输出
独立 fact ID 数组，不能把多个 ID 拼成一个字符串。

- [ ] **Step 3: 写缺事实失败信息 RED 测试**

没有满足条件的分部事实时，在生成 Layer 3 前返回：缺哪个字段、由哪个 Skill 生产、
可重试哪个 work unit；不得只报“fact_id empty”。

- [ ] **Step 4: 运行目标测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_gate tests.test_full_analysis_orchestrator -v -k industry
```

- [ ] **Step 5: 实现结构化 fact 注册与行业 scope builder**

不从报告全文启发式抽数字。行业 scope builder 只消费 Result Bundle 已登记的
`segment_revenue`/`segment_operating_income` 等明确字段。

- [ ] **Step 6: 让 Layer 3 barrier 自动调用 gate `set-industry`**

满足事实条件时自动执行；不满足时只重派生产这些事实的 Layer 2 unit，不重跑整层。

- [ ] **Step 7: 运行回归确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_gate tests.test_full_analysis_orchestrator tests.test_full_analysis_phase2 -v
```

- [ ] **Step 8: Commit**

```bash
git add tools/full_analysis_gate.py tools/full_analysis_orchestrator.py tests/test_full_analysis_gate.py tests/test_full_analysis_orchestrator.py
git commit -m "feat(full-analysis): 结构化登记事实并自动生成行业范围"
```

---

## Milestone 4 — 有界调度、重试与诚实降级

### Task 8: 持久调度队列、lease 与 resume

**Files:**
- Create: `tools/full_analysis_runtime.py`
- Modify: `tools/full_analysis_orchestrator.py`
- Create: `tests/test_full_analysis_runtime.py`
- Modify: `tests/test_full_analysis_orchestrator.py`

**Scheduler state:** `PENDING/DISPATCHED/RUNNING/RETRY_WAIT/READY_TO_INGEST/DONE/FAILED`。

- [ ] **Step 1: 写队列状态机 RED 测试**

非法跃迁必须失败；状态文件使用临时文件 + 原子 replace；重复提交幂等。

- [ ] **Step 2: 写 lease 回收 RED 测试**

冻结时钟，构造过期 `RUNNING` unit；`resume` 后应回到 `PENDING` 或 `RETRY_WAIT`，
attempt 递增且保留诊断，不修改业务 manifest。

- [ ] **Step 3: 写 run-root 恢复 RED 测试**

新进程只凭 `run_id/run_root` 恢复，不依赖原终端 session 或内存对象。

- [ ] **Step 4: 运行测试确认 RED**

Run: `python3 -m unittest tests.test_full_analysis_runtime tests.test_full_analysis_orchestrator -v`

- [ ] **Step 5: 实现最小 runtime 模块**

runtime 只管理调度状态；业务完成与否继续读取 gate manifest。状态文件固定放在
`RUN_ROOT/dispatch/runtime-state.json`。

- [ ] **Step 6: 增加 orchestrator 命令**

- `next-work --limit N`
- `mark-running --unit-id --context-id`
- `submit-result --unit-id --result-file`
- `record-failure --unit-id --kind --retry-after`
- `resume`
- `status`

- [ ] **Step 7: 运行回归确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_runtime tests.test_full_analysis_orchestrator -v
bash scripts/check.sh
```

- [ ] **Step 8: Commit**

```bash
git add tools/full_analysis_runtime.py tools/full_analysis_orchestrator.py tests/test_full_analysis_runtime.py tests/test_full_analysis_orchestrator.py
git commit -m "feat(full-analysis): 增加持久任务队列与自动恢复"
```

### Task 9: 429 退避、并发控制与多角色真实性

**Files:**
- Modify: `tools/full_analysis_runtime.py`
- Modify: `tools/full_analysis_orchestrator.py`
- Modify: `tools/full_analysis_gate.py`
- Modify: `tests/test_full_analysis_runtime.py`
- Modify: `tests/test_full_analysis_gate.py`

- [ ] **Step 1: 写 429 RED 测试**

冻结时钟并注入前两次 429、第三次成功；断言尊重 `retry_after`、attempt=3、最终 DONE。

- [ ] **Step 2: 写 persistent 429 RED 测试**

第三次仍失败时停止重试，按 contract 进入 PWL 或 FAIL；不得保留 RUNNING。

- [ ] **Step 3: 写自适应并发 RED 测试**

Web-heavy 默认同时 lease 不超过 2；出现 429 后降到 1；连续成功后只在下一波恢复，
不瞬间放大。

- [ ] **Step 4: 写真实 role receipt RED 测试**

缺角色产物、重复 context ID、同一 context 冒充多个角色、路径不存在均不得获得
independent assurance。顺序 fallback 必须产生标准 limitation。

- [ ] **Step 5: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_runtime -v -k rate
python3 -m unittest tests.test_full_analysis_gate -v -k role
```

- [ ] **Step 6: 实现退避与并发策略**

仅使用注入时钟和标准库随机源，测试固定 seed；不得在单元测试真实 sleep。

- [ ] **Step 7: 实现 contract-driven fallback**

多角色降级上限继续由 `role_rule.sequential_cap` 决定；Orchestrator 只记录真实失败和
执行模式，不自行给出 PASS/PWL。

- [ ] **Step 8: 运行回归确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_runtime tests.test_full_analysis_gate tests.test_full_analysis_orchestrator -v
```

- [ ] **Step 9: Commit**

```bash
git add tools/full_analysis_runtime.py tools/full_analysis_orchestrator.py tools/full_analysis_gate.py tests/test_full_analysis_runtime.py tests/test_full_analysis_gate.py
git commit -m "feat(full-analysis): 有界处理限流超时与多角色降级"
```

---

## Milestone 5 — 非交互执行、审计与最终交付

### Task 10: 增加 `full_analysis_noninteractive` 执行档

**Files:**
- Modify: `skills/full-company-analysis.md`
- Modify: `tools/full_analysis_orchestrator.py`
- Modify: `tests/test_full_analysis_orchestrator.py`
- Regenerate: `codex-skills/full-company-analysis/SKILL.md`

- [ ] **Step 1: 写执行档 RED 测试**

Work packet 必须明确列出：已授权的公开只读检索、确定性计算、Agent 启动和本次
run-root 已分配路径写入；同时列出身份歧义、敏感信息、外部写入、交易和覆盖删除
仍须 STOP。

- [ ] **Step 2: 写禁止重复确认 RED 测试**

生成的 Agent prompt 不得要求对 WebSearch、公开数据或已分配产物路径再次询问用户；
也不得包含 `NON_INTERACTIVE` 即“绕过所有权限”的宽泛措辞。

- [ ] **Step 3: 更新 canonical Skill**

把仓库内 orchestrator 写为唯一执行入口；外置路径只放迁移说明，不再承载业务步骤。

- [ ] **Step 4: 重新生成 Codex Skill**

```bash
python3 scripts/sync-codex-skills.py
python3 scripts/sync-codex-skills.py --check
```

- [ ] **Step 5: 运行回归确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_orchestrator -v -k profile
bash scripts/check.sh
```

- [ ] **Step 6: Commit**

```bash
git add skills/full-company-analysis.md codex-skills/full-company-analysis/SKILL.md tools/full_analysis_orchestrator.py tests/test_full_analysis_orchestrator.py
git commit -m "feat(full-analysis): 增加安全的无人值守执行档"
```

### Task 11: 自动审计、逐项诊断与顶层 SUMMARY

**Files:**
- Modify: `tools/full_analysis_gate.py`
- Modify: `tools/full_analysis_orchestrator.py`
- Modify: `tools/report_audit.py`（仅在需要稳定程序接口时）
- Modify: `tests/test_full_analysis_gate.py`
- Modify: `tests/test_full_analysis_orchestrator.py`
- Modify: `tests/test_report_audit.py`

**Outputs:**

- `RUN_ROOT/SUMMARY.md`
- `RUN_ROOT/status.json`

- [ ] **Step 1: 写审计 API RED 测试**

Orchestrator/gate 必须传 report 内容或标准 audit record，不得把文件路径误当
`report_audit.py` 子命令。0 样本只能 INSUFFICIENT/FAIL。

- [ ] **Step 2: 写统一报告壳 RED 测试**

所有 canonical artifact scaffold 必须含数据截止日、来源优先级、限制、免责声明的
稳定 section ID；缺少时 ingest 当前 Skill 即失败。

- [ ] **Step 3: 写 SUMMARY RED 测试**

即使 finalize FAIL，也必须写 SUMMARY/status，包含：

- 20 项矩阵与计数；
- 限制、数据带宽、assurance；
- 每项耗时、attempt、429/timeout 次数；
- 正式 artifact 链接；
- “已准出/未准出”醒目标记。

- [ ] **Step 4: 写幂等 RED 测试**

重复 `finalize`/`summary` 不得重复追加内容；相同 manifest 产生相同状态正文（时间字段除外并受控）。

- [ ] **Step 5: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_gate tests.test_full_analysis_orchestrator tests.test_report_audit -v -k summary
```

- [ ] **Step 6: 实现程序化审计与 summary renderer**

复用 `report_audit` 现有函数；不要通过 shell 拼接 `extract --report`。英文段落检测保持
advisory，并用金融缩写 allowlist 降低误报。

- [ ] **Step 7: 输出恢复入口**

`status --company/--run-id` 必须打印 SUMMARY 的绝对路径；`resume` 从 runtime-state
继续，不依赖临时 session。

- [ ] **Step 8: 运行回归确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_gate tests.test_full_analysis_orchestrator tests.test_report_audit -v
bash scripts/check.sh
```

- [ ] **Step 9: Commit**

```bash
git add tools/full_analysis_gate.py tools/full_analysis_orchestrator.py tools/report_audit.py tests/test_full_analysis_gate.py tests/test_full_analysis_orchestrator.py tests/test_report_audit.py
git commit -m "feat(full-analysis): 自动审计并生成统一运行摘要"
```

---

## Milestone 6 — Hermetic E2E、迁移与文档

### Task 12: 跑完整六层的 hermetic E2E 与故障注入

**Files:**
- Create: `tests/fakes/fake_full_analysis_agent.py`
- Create: `tests/fakes/fake_ashare_data.py`
- Create: `tests/test_full_analysis_e2e.py`
- Modify: `scripts/check.sh`
- Modify: `.github/workflows/check.yml`（仅当当前 workflow 未调用 `scripts/check.sh`）

- [ ] **Step 1: 写 happy-path E2E RED 测试**

在临时仓库运行 init → 基础数据窗口 → Layer 1-4 → industry → reference freeze →
Layer 5 → audit/finalize。fake Agent 根据 work packet 生成合法 Result Bundle。

断言 20/20 非 FAIL、无 PENDING/RUNNING、SUMMARY/status 存在、manual intervention=0。

- [ ] **Step 2: 参数化 incidents fixture**

逐条读取 Task 1 fixture 注入 fault，断言 `expected_outcome`，并验证禁止的人工动作从未发生。

- [ ] **Step 3: 加 destructive-path 保护测试**

创建未知目录和用户文件；运行 collect/finalize 后内容哈希不变，证明系统不再自动移动、
append 或删除未知产物。

- [ ] **Step 4: 运行测试确认 RED**

Run: `python3 -m unittest tests.test_full_analysis_e2e -v`

- [ ] **Step 5: 接通四个固定边界并确认 GREEN**

仅允许实现以下连接：

1. `build_work_packet()` 生成 fake Agent 输入；
2. fake Agent 写 `full_analysis_result_schema.json` 合法 Result Bundle；
3. `FullAnalysisRuntime.submit_result()` 调用 `GateClient.run("ingest-result", "--run-root", str(run_root), "--skill", skill, "--result-file", str(result_path))`；
4. gate 成功后 runtime 将 unit 从 `READY_TO_INGEST` 改为 `DONE`。

不得在 fake 中写 manifest、跳过 gate 或直接修改业务终态。

- [ ] **Step 6: 确认 check.sh/CI 同源**

若 `.github/workflows/check.yml` 已运行 `bash scripts/check.sh`，不修改 workflow；否则只改为调用同一命令。

- [ ] **Step 7: 运行完整验证**

```bash
python3 -m unittest tests.test_full_analysis_e2e -v
bash scripts/check.sh
```

Expected: 全部 PASS，E2E 不联网。

- [ ] **Step 8: Commit**

```bash
git add tests/fakes/fake_full_analysis_agent.py tests/fakes/fake_ashare_data.py tests/test_full_analysis_e2e.py scripts/check.sh
# 仅实际修改时才追加 .github/workflows/check.yml
git commit -m "test(full-analysis): 六层端到端覆盖真实故障注入"
```

### Task 13: ADR、使用文档与外置入口迁移 checkpoint

**Files:**
- Create: `docs/adr/0001-full-analysis-single-control-plane.md`
- Create or Modify: `docs/USAGE.md`
- Modify: `README.md`
- Modify: `docs/ROADMAP.md`
- Modify: `scripts/run_full_analysis.py`
- Modify: `scripts/batch_full_analysis.py`
- Create: `tests/test_full_analysis_entrypoints.py`
- External after explicit confirmation only: `~/.workbuddy/berkshire-skill-sync/orchestrate.py`

- [ ] **Step 1: 写 ADR**

必须包含 `Status/Context/Decision/Consequences`，记录：仓库内 canonical 控制面、gate
单写者、外置脚本降为 wrapper、为何不继续维护两套实现。

- [ ] **Step 2: 写使用文档**

覆盖 init、status、resume、SUMMARY 路径、失败语义、数据限制、非交互边界和
“不会自动 push/publish/交易”。

- [ ] **Step 3: 更新 Roadmap**

把“Skill 输出回归测试”从长期项调整为已落地的 hermetic E2E；3×3 live canary 在完成前保持未勾选。

- [ ] **Step 4: 写旧入口退役 RED 测试**

`tests/test_full_analysis_entrypoints.py` 必须断言：

- `scripts/run_full_analysis.py --help` 指向 `orchestrate_full_analysis.py`，不含硬编码 `AS_OF` 或报告模板；
- `scripts/batch_full_analysis.py --help` 只描述批量创建 canonical run，不解析 `ashare_data.py` 文本；
- 两个脚本均不 import `report_audit`、不直接调用 `full_analysis_gate.py`、不写 manifest；
- 旧数字参数调用返回 exit 2 和迁移说明，不生成任何 run root。

Run: `python3 -m unittest tests.test_full_analysis_entrypoints -v`

Expected: FAIL，两个旧脚本仍包含独立控制逻辑。

- [ ] **Step 5: 将两个旧脚本改为兼容入口**

`run_full_analysis.py` 对新参数透传 canonical CLI；旧的公司加数值参数调用明确拒绝并输出迁移命令。
`batch_full_analysis.py` 只读取显式公司清单并逐项调用 canonical `init`，不再抓取、正则解析数据或生成报告。

- [ ] **Step 6: 运行入口测试确认 GREEN**

Run: `python3 -m unittest tests.test_full_analysis_entrypoints -v`

Expected: PASS，仓库内只剩一个业务控制实现。

- [ ] **Step 7: 运行文档/同步验证**

```bash
git diff --check
bash scripts/check.sh
```

- [ ] **Step 8: Commit 仓库文档与兼容入口**

```bash
git add docs/adr/0001-full-analysis-single-control-plane.md docs/USAGE.md README.md docs/ROADMAP.md scripts/run_full_analysis.py scripts/batch_full_analysis.py tests/test_full_analysis_entrypoints.py
git commit -m "docs(full-analysis): 记录单一控制面迁移与恢复用法"
```

- [ ] **Step 9: 外置入口迁移前阻塞确认**

向用户展示：

- 将修改的绝对路径；
- 新 wrapper 内容摘要；
- 旧文件备份/回退路径；
- 仓库内 E2E 与 `check.sh` 结果。

未获得明确确认，不执行下一步。

- [ ] **Step 10: 获确认后把外置脚本降为薄 wrapper**

Wrapper 只定位仓库并调用 `scripts/orchestrate_full_analysis.py`；不保留业务常量、
manifest 修改或自动 enrich。保留可验证回退副本，具体备份目标先解析并展示，禁止覆盖。

- [ ] **Step 11: 验证 wrapper 等价**

对 `plan/status` 比较仓库入口和外置 wrapper 的标准化 JSON；不得触发真实研究或外部写入。

> 外置 wrapper 不进入本仓库 commit；在 milestone 报告中记录修改与回退路径。

---

## Milestone 7 — 3×3 Live Canary 与主链路准出

### Task 14: Canary 验收器与九轮实跑

**Files:**
- Create: `scripts/check-full-analysis-canary.py`
- Create: `tests/test_full_analysis_canary_check.py`
- Local-only outputs: `local/full-analysis-canary/2026-07-23/`
- Modify after success: `docs/ROADMAP.md`

**Canary set:**

1. 恒瑞医药 `600276.SH`；
2. 韦尔股份 `603501.SH`；
3. 格力电器 `000651.SZ`。

- [ ] **Step 1: 写 canary checker RED 测试**

输入九份 fake `status.json`，检查：

- 恰好 3 公司 × 3 次；
- manual intervention=0；
- 20/20 非 FAIL；
- 无 PENDING/RUNNING；
- schema/type/runtime errors=0；
- 每份报告元数据完整；
- 每轮 ≤4 小时；
- 9 轮中位数 ≤3 小时；
- SUMMARY 路径均存在。

- [ ] **Step 2: 实现只读 checker**

脚本只读取用户显式提供的 run root/status 文件并输出汇总，不联网、不修改运行结果。

- [ ] **Step 3: 运行 checker 测试确认 GREEN**

Run: `python3 -m unittest tests.test_full_analysis_canary_check -v`

- [ ] **Step 4: Commit checker**

```bash
git add scripts/check-full-analysis-canary.py tests/test_full_analysis_canary_check.py
git commit -m "test(full-analysis): 增加三乘三实跑准出检查"
```

- [ ] **Step 5: 执行九轮 live canary**

每次运行前执行 `date`；使用 `full_analysis_noninteractive`；只写对应新 run root。每轮结束立即运行 checker 的单轮检查。不得手工：

- 编辑 manifest；
- 补 section/fact/artifact；
- 重置 RUNNING；
- 替代失败 Agent 写报告；
- 跳过 audit/finalize。

任何一轮失败都作为新 incident 加入 hermetic fixture，先写 RED 测试并修复，再从该公司的连续计数重新开始。

- [ ] **Step 6: 汇总九轮并验证**

```bash
python3 scripts/check-full-analysis-canary.py \
  --root local/full-analysis-canary/2026-07-23
bash scripts/check.sh
```

Expected: canary checker PASS；完整本地检查 PASS。

- [ ] **Step 7: 更新 Roadmap 并本地提交**

只提交 Roadmap 状态，不提交 ignored canary 报告：

```bash
git add docs/ROADMAP.md
git commit -m "docs(full-analysis): 记录无人值守三乘三验收通过"
```

---

## Milestone 8 — 主链路稳定后启动 Darwin 第二阶段

### Task 15: 重跑 21 Skill 基线并生成独立优化计划

**Files:**
- Local-only evaluation outputs: `local/darwin-evaluation/post-canary/`
- Create after evaluation: 先运行本地 `date`，再把实际 `YYYY-MM-DD` 写入 `docs/superpowers/plans/YYYY-MM-DD-skill-positive-transfer.md`

- [ ] **Step 1: 重跑确认过的 42 条 prompt**

保持同一 rubric、两名盲评 judge 和 baseline 方式；补两条 thesis-drift 成功路径，但单独报告，不混入旧批次可比均值。

- [ ] **Step 2: 验证阶段目标**

- positive/tie 至少 16/21；
- 任一 Skill d8 相对 baseline 降幅不超过 1；
- runtime 人工风险为 0；
- standalone 只读动作不再重复 STOP；
- 外部写入和交易 STOP 仍完整。

- [ ] **Step 3: 若未达标，单独写优化计划**

优先顺序：

1. private-company-research；
2. investment-research；
3. earnings-review；
4. investment-team；
5. financial-data；
6. runtime-neutral 风险；
7. 冗长模板压缩。

该计划不得反向扩大本次主链路可靠性 commit 的范围。

- [ ] **Step 4: 不自动 commit 评估输出**

Darwin 结果位于 ignored `local/`；只在用户确认后提交新的优化计划文档。

---

## Final Verification Checklist

- [ ] `python3 -m unittest tests.test_full_analysis_incidents -v`
- [ ] `python3 -m unittest tests.test_full_analysis_contract -v`
- [ ] `python3 -m unittest tests.test_full_analysis_gate -v`
- [ ] `python3 -m unittest tests.test_full_analysis_phase2 -v`
- [ ] `python3 -m unittest tests.test_full_analysis_orchestrator -v`
- [ ] `python3 -m unittest tests.test_full_analysis_runtime -v`
- [ ] `python3 -m unittest tests.test_full_analysis_e2e -v`
- [ ] `python3 -m unittest tests.test_full_analysis_canary_check -v`
- [ ] `python3 scripts/check-full-analysis-contract.py`
- [ ] `python3 scripts/sync-codex-skills.py --check`
- [ ] `bash scripts/check.sh`
- [ ] `git diff --check`
- [ ] `git status --short` 只含计划内文件与明确保留的用户文件
- [ ] Canary checker 对九轮结果 PASS
- [ ] README/USAGE/ADR/Roadmap 与最终行为一致
- [ ] 未读取或输出 secret；未修改历史 `local/` 报告；未执行外部写入

## Milestone Commit Boundaries

建议保持以下独立、可回退提交边界：

1. incident fixtures；
2. tracked orchestrator；
3. manifest single-writer；
4. Contract v2；
5. Work Packet/Result Bundle；
6. data windows；
7. fact/industry closure；
8. runtime queue；
9. retry/role truthfulness；
10. noninteractive profile；
11. audit/SUMMARY；
12. hermetic E2E；
13. docs/ADR；
14. canary checker；
15. canary completion record。

任何提交失败或测试未通过时，新建修复提交；不使用 `--amend`、`--no-verify` 或强制推送。
