# Full-analysis Unattended Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Use `superpowers:test-driven-development` for every behavior change, `superpowers:systematic-debugging` for unexpected failures, and `superpowers:verification-before-completion` before claiming a milestone complete.

**Goal:** 建成由 WorkBuddy 原生 Skill 直接编排 Agent、由仓库 Runtime/Gate 确定性收口的单一 A 股公司无人值守全量分析主链路，并通过格力电器一次 live 验收。

**Architecture:** `workbuddy-skills/full-company-analysis/SKILL.md` 是唯一生产 Agent 编排器；`tools/full_analysis_runtime.py` 管理 work unit、attempt、租约、预算和恢复；`tools/full_analysis_gate.py` 管理 v2 manifest、事实/来源/计算/产物注册与准出。Agent 只写 attempt staging 并提交 Result Bundle，Gate 校验后原子晋升；失败只生成未准出的 PARTIAL 与确定性 SUMMARY。

**Tech Stack:** WorkBuddy 原生 Agent 工具、Python 3 标准库、JSON/JSONL、Markdown、现有 `unittest`、`ashare_data.py`、`financial_rigor.py`、`report_audit.py`；不新增第三方依赖。

**Design reference:** `docs/superpowers/specs/2026-07-23-full-analysis-unattended-reliability-design.md`

## Global Constraints

- 仅支持单一 `.SH/.SZ/.BJ` A 股上市公司，RMB 和中国会计口径。
- WorkBuddy 是唯一无人值守生产运行时；Python 代码不得尝试调用或模拟 Agent API。
- 不创建 `tools/full_analysis_orchestrator.py`；`scripts/full_analysis.py` 只是 Runtime/Gate 薄 CLI。
- 20 项业务 Skill 清单、角色、依赖、事实槽、适用性和 PWL 只存在于
  `tools/full_analysis_contract.json`。
- `tools/full_analysis_gate.py` 原位升级到 v2；不保留 v1 写兼容，不迁移旧 manifest。
- 旧 run 只允许读取原文件或得到 `LEGACY_READ_ONLY` 诊断，不能 resume/finalize。
- 保留用户未跟踪文件 `tools/full_analysis_gate.py.bak-20260722-202828`，不得读取、
  删除、覆盖、暂存或提交。
- 不改写 `local/` 中任何历史报告或实盘记录；测试使用临时目录。
- 测试 fixture 只保存脱敏最小事故，不提交三份完整历史报告。
- 不读取或输出任何 token；Tushare 只记录 configured/unavailable 等能力状态。
- 不执行 push、PR、publish、外部消息或交易。
- `skills/*.md` 是 Claude/Codex canonical；修改后运行
  `python3 scripts/sync-codex-skills.py` 并提交对应生成物。
- WorkBuddy canonical 只位于 `workbuddy-skills/full-company-analysis/SKILL.md`；
  `~/.workbuddy/skills/` 只是显式安装副本。
- 每个 Task 只暂存明确列出的文件，禁止 `git add .`、`git add -A` 和
  `git add --all`。
- 每个 Task 遵循 RED → 最小实现 → GREEN → 精确提交；失败修复使用新提交，
  不使用 `--amend` 或 `--no-verify`。
- 每个 milestone 后运行相关测试；最终运行 `bash scripts/check.sh`。
- 本计划不启用 ADR、独立 USAGE、hook、release 或新依赖。
- 任何计划外 schema、依赖、模块边界或外部写入变化都必须停止并重新确认。

---

## Project Profile and File Map

Profile 为 `product`。沿用现有本地检查和安全规则；WorkBuddy Agent 属产品运行能力，
不等同于 Codex 实施阶段的 subagent add-on。

| File | Responsibility |
|---|---|
| `workbuddy-skills/full-company-analysis/SKILL.md` | WorkBuddy 唯一 Agent 编排器 |
| `skills/full-company-analysis.md` | Claude/Codex 兼容说明；明确无人值守不支持 |
| Contract 列出的 20 个 `skills/*.md` | 业务方法 + `UNATTENDED_FULL_ANALYSIS` 最小适配 |
| `tools/full_analysis_contract.json` | Contract v2 唯一机器真源 |
| `tools/full_analysis_result_schema.json` | Result Bundle v1 协议 |
| `tools/full_analysis_runtime.py` | 调度、attempt、lease、budget、event、resume |
| `tools/full_analysis_gate.py` | manifest/registries 单写、ingest、audit、finalize |
| `tools/financial_rigor.py` | 计算执行器 |
| `tools/financial_rigor_result_schema.json` | 计算 envelope schema |
| `tools/report_audit.py` | 稳定函数 API + 诊断 CLI |
| `tools/ashare_data.py` | 数据命令；Runtime 冻结真实命令回执 |
| `scripts/full_analysis.py` | 用户生命周期命令与隐藏内部命令的薄入口 |
| `scripts/install-workbuddy-skills.sh` | WorkBuddy Skill 预览、检查、显式安装 |
| `scripts/check-full-analysis-contract.py` | Contract/Result schema 静态校验 |
| `scripts/check-full-analysis-canary.py` | 单 run live 验收器 |
| `tests/test_full_analysis_*.py` | 单元、集成、hermetic E2E 和 canary |

稳定 schema 版本：

```python
CONTRACT_SCHEMA = "full-analysis-contract/v2"
MANIFEST_SCHEMA = "full-analysis-manifest/v2"
RUNTIME_SCHEMA = "full-analysis-runtime/v1"
RESULT_SCHEMA = "result-schema/v1"
```

稳定状态：

```python
WORK_STATES = (
    "PENDING", "RUNNING", "RETRY_WAIT", "READY_TO_INGEST",
    "DONE", "FAILED", "ABANDONED",
)
EXECUTION_STATES = ("PENDING", "RUNNING", "COMPLETE", "BLOCKED")
CONTRACT_STATUSES = (
    "PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE", "FAIL",
)
RUN_STATUSES = ("RUNNING", "APPROVED", "PARTIAL", "FAILED")
```

稳定内部接口：

```python
# tools/full_analysis_runtime.py
def create_run(company: str, code: str, as_of: str,
               capability_receipt: dict, repo_root: Path) -> Path: ...
def next_work(run_root: Path, now: datetime) -> list[dict]: ...
def mark_job_started(run_root: Path, work_unit_id: str, attempt_id: str,
                     agent_job_id: str, lease_nonce: str,
                     now: datetime) -> None: ...
def heartbeat(run_root: Path, attempt_id: str, now: datetime) -> None: ...
def submit_result(run_root: Path, result_path: Path,
                  now: datetime) -> dict: ...
def record_failure(run_root: Path, attempt_id: str, kind: str,
                   now: datetime) -> dict: ...
def resume_run(run_root: Path, now: datetime) -> dict: ...

# tools/full_analysis_gate.py
def initialize_v2(run_root: Path, contract: dict,
                  identity: dict, snapshots: dict) -> dict: ...
def ingest_result(run_root: Path, result_path: Path,
                  contract: dict) -> dict: ...
def evaluate_contracts(run_root: Path, contract: dict) -> list[dict]: ...
def finalize_run(run_root: Path, contract: dict) -> dict: ...

# tools/report_audit.py
def audit_registered_artifact(report_path: Path, audit_records: list[dict],
                              policy: dict) -> dict: ...
```

Public WorkBuddy lifecycle actions are `start`、`status <run-id>`、
`resume <run-id>`、`cleanup <run-id> --dry-run`。`next-work`、`job-started`、
`heartbeat`、`submit-result`、`record-failure`、`audit` 和 `finalize` 只供
WorkBuddy Skill 内部调用，不允许成为手工修复入口。

---

## Milestone 0 — 固化真实事故与架构护栏

### Task 1: 建立脱敏事故 fixture

**Files:**

- Create: `tests/fixtures/full_analysis/incidents.json`
- Create: `tests/test_full_analysis_incidents.py`

**Interfaces:**

- Consumes: 三次运行问题汇总中的根因，不复制完整报告。
- Produces: 后续 E2E 使用的 `incident_id/root_cause/injected_fault/expected_outcome`。

- [ ] **Step 1: 写 fixture schema 的失败测试**

```python
REQUIRED = {
    "incident_id", "root_cause", "stage",
    "injected_fault", "expected_outcome",
    "manual_action_forbidden",
}

def test_incident_fixture_covers_observed_root_causes(self):
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    self.assertTrue(rows)
    self.assertTrue(all(REQUIRED <= row.keys() for row in rows))
    causes = {row["root_cause"] for row in rows}
    self.assertEqual(
        causes,
        {
            "control_plane", "schema", "data_lifecycle", "facts",
            "sections", "scheduler", "audit", "delivery",
        },
    )
    self.assertTrue(all(row["manual_action_forbidden"] is True for row in rows))
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
python3 -m unittest tests.test_full_analysis_incidents -v
```

Expected: FAIL，fixture 尚不存在。

- [ ] **Step 3: 写最小事故数据**

每类根因至少一条；明确覆盖 Tushare unavailable、post-finish enrich、empty fact、
artifact type mismatch、calculation schema、missing section、429、timeout、
late result、audit invocation 和 scattered delivery。公司只保留证券代码和公开简称，
不复制报告正文或 URL 敏感查询参数。

- [ ] **Step 4: 运行测试确认 GREEN**

Run:

```bash
python3 -m unittest tests.test_full_analysis_incidents -v
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/full_analysis/incidents.json tests/test_full_analysis_incidents.py
git commit -m "test(full-analysis): 固化脱敏实跑事故"
```

---

## Milestone 1 — Contract v2 与封闭协议

### Task 2: 原位升级 20 项 Contract v2

**Files:**

- Modify: `tools/full_analysis_contract.json`
- Modify: `scripts/check-full-analysis-contract.py`
- Modify: `tests/test_full_analysis_contract.py`
- Modify: `tests/test_full_analysis_phase2.py`

**Interfaces:**

- Consumes: 现有 20 项 registry 和已确认的适用性/角色/目录决策。
- Produces: `full-analysis-contract/v2`，供 Runtime、Gate、Skill 和测试读取。

- [ ] **Step 1: 写 Contract v2 失败测试**

测试精确断言：

```python
EXPECTED_SKILLS = {
    "ashare-data", "financial-data", "quality-screen",
    "investment-checklist", "investment-research", "investment-team",
    "management-deep-dive", "earnings-review", "earnings-team",
    "industry-research", "industry-funnel", "bottleneck-hunter",
    "news-pulse", "thesis-tracker", "thesis-drift",
    "portfolio-review", "private-company-research",
    "deep-company-series", "dyp-ask", "wechat-article",
}

def test_v2_has_one_exact_twenty_skill_registry(self):
    self.assertEqual(REGISTRY["schema_version"], "full-analysis-contract/v2")
    self.assertEqual(
        {row["skill_id"] for row in REGISTRY["skills"]},
        EXPECTED_SKILLS,
    )
```

同时断言：

- `industry-funnel` 为正常适用；
- `bottleneck-hunter` 使用明确瓶颈谓词；
- portfolio/private/thesis-drift 使用已确认 N/A 谓词；
- investment-team 为 4+1、earnings-team 为 4+1+1、news-pulse 为 4+1；
- section 只包含稳定 ID、展示标题、适用谓词和最低证据；
- PWL 白名单只有三项；
- 禁止 PWL 列表完整；
- stage directory 映射与设计文档一致。

- [ ] **Step 2: 写“无精确标题滥用”失败测试**

```python
def test_only_machine_critical_sections_are_required(self):
    for skill in REGISTRY["skills"]:
        for section in skill["sections"]:
            self.assertIn(
                section["section_id"],
                {
                    "data_cutoff", "sources_scope", "limitations",
                    "research_disclaimer", "core_conclusion",
                    "downstream_evidence", "contract_calculations",
                } | set(skill.get("domain_section_ids", [])),
            )
            self.assertNotIn("aliases", section)
```

- [ ] **Step 3: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_contract -v
python3 scripts/check-full-analysis-contract.py
```

Expected: FAIL，当前 registry 仍是 v1。

- [ ] **Step 4: 重写 registry 和 checker**

使用对象字段 `skill_id/category/dependencies/applicability/fact_slots/
source_requirements/artifacts/sections/roles/calculation_requests/audit_policy`。
删除旧 `generic_required_sections`、裸标题数组、layer 数字和可由 Agent 自填的
保障字段。Checker 必须从 registry 自身读取清单，不在 Python 中复制 20 项常量。

- [ ] **Step 5: 更新参数化测试 fixture**

测试产物 scaffold 从 v2 contract 生成；不再用固定标题子串构造“合法报告”。
`NOT_APPLICABLE` 由谓词 fixture 生成，不要求 Agent 写假 N/A 报告。

- [ ] **Step 6: 运行测试确认 GREEN**

```bash
python3 scripts/check-full-analysis-contract.py
python3 -m unittest tests.test_full_analysis_contract tests.test_full_analysis_phase2 -v
```

Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add tools/full_analysis_contract.json scripts/check-full-analysis-contract.py tests/test_full_analysis_contract.py tests/test_full_analysis_phase2.py
git commit -m "feat(full-analysis): 升级二十项业务契约到 v2"
```

### Task 3: 定义 Result Bundle v1

**Files:**

- Create: `tools/full_analysis_result_schema.json`
- Modify: `scripts/check-full-analysis-contract.py`
- Create: `tests/test_full_analysis_result_schema.py`

**Interfaces:**

- Consumes: Contract v2 的 skill/role/fact/artifact ID。
- Produces: `result-schema/v1` 的封闭协议文档和标准库校验规则。

- [ ] **Step 1: 写 Result Bundle 失败测试**

```python
REQUIRED = {
    "schema_version", "run_id", "work_unit_id", "attempt_id",
    "agent_job_id", "lease_nonce", "skill_id", "role_id", "status",
    "artifact_records", "fact_updates", "source_records",
    "calculation_requests", "judgments", "limitations",
    "pwl_candidates", "started_at", "completed_at", "error",
}

def test_schema_declares_all_required_fields(self):
    self.assertEqual(SCHEMA["schema_version"], "result-schema/v1")
    self.assertEqual(set(SCHEMA["required"]), REQUIRED)
```

增加非法 fixture：`assigned_artifacts` 为 `list[str]`、calculations 为 dict、
缺 job ID、错误 role、artifact 无 SHA-256、Agent 提交 `expected.result`。

- [ ] **Step 2: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_result_schema -v
```

Expected: FAIL。

- [ ] **Step 3: 写 schema 与 stdlib checker**

Schema 明确数组元素字段、允许未知顶层字段但不授予验收 credit、失败时 error 必填、
calculation 只允许 operation+args。`scripts/check-full-analysis-contract.py` 校验
Contract 引用的 result schema 版本存在且一致。

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_result_schema -v
python3 scripts/check-full-analysis-contract.py
```

- [ ] **Step 5: Commit**

```bash
git add tools/full_analysis_result_schema.json scripts/check-full-analysis-contract.py tests/test_full_analysis_result_schema.py
git commit -m "feat(full-analysis): 定义封闭结果包协议"
```

---

## Milestone 2 — Gate v2：单写者、注册表与原子晋升

### Task 4: 建立 v2 run layout 和正式注册表

**Files:**

- Modify: `tools/full_analysis_gate.py`
- Modify: `tests/test_full_analysis_gate.py`
- Create: `tests/fixtures/full_analysis/legacy_manifest_v1.json`

**Interfaces:**

- Consumes: Contract v2、公司身份、关键文件快照。
- Produces: v2 manifest、facts/sources/calculations/artifacts 注册表和标准目录。

- [ ] **Step 1: 写 v2 初始化失败测试**

```python
def test_initialize_v2_creates_only_canonical_run_files(self):
    initialize_v2(run_root, contract, identity, snapshots)
    expected = {
        "evidence/00-analysis-manifest.json",
        "evidence/facts.json",
        "evidence/sources.json",
        "evidence/calculations.json",
        "evidence/artifacts.json",
    }
    self.assertTrue(expected <= relative_files(run_root))
    self.assertFalse((run_root / "manifest.json").exists())
```

增加测试：

- run-root 名称以代码为主键；
- 公司/代码不匹配拒绝；
- 只接受 `.SH/.SZ/.BJ`；
- manifest 初始只保存身份、版本、适用性和执行状态；
- 旧 v1 fixture 的写命令返回 `LEGACY_READ_ONLY` 且哈希不变；
- unrelated git/local 变化不影响关键快照。

- [ ] **Step 2: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_gate -v
```

Expected: FAIL，当前 Gate 写根目录 `manifest.json`。

- [ ] **Step 3: 原位改造 Gate 存储层**

保留可复用的路径校验、原子写和精确计算工具；删除 v1 command flow 对
`begin-skill/finish-skill/set-industry/reference-freeze` 的业务依赖。实现：

```python
MANIFEST_REL = Path("evidence/00-analysis-manifest.json")
REGISTRY_RELS = {
    "facts": Path("evidence/facts.json"),
    "sources": Path("evidence/sources.json"),
    "calculations": Path("evidence/calculations.json"),
    "artifacts": Path("evidence/artifacts.json"),
}
```

所有写入使用同目录临时文件、flush、fsync 和 `os.replace`。正式目录只由 Gate 写。

- [ ] **Step 4: 实现确定性 N/A**

Gate 根据 Contract predicate 生成固定回执并写入对应正式目录。N/A 不创建 Agent
work unit，不允许 Agent 自报 N/A。

- [ ] **Step 5: 运行测试确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_gate -v
python3 scripts/check-full-analysis-contract.py
```

- [ ] **Step 6: Commit**

```bash
git add tools/full_analysis_gate.py tests/test_full_analysis_gate.py tests/fixtures/full_analysis/legacy_manifest_v1.json
git commit -m "refactor(full-analysis): 建立 Gate v2 单写存储"
```

### Task 5: Result ingest、稳定 ID 与原子晋升

**Files:**

- Modify: `tools/full_analysis_gate.py`
- Modify: `tools/financial_rigor_result_schema.json`
- Modify: `tests/test_full_analysis_gate.py`
- Modify: `tests/test_financial_rigor.py`

**Interfaces:**

- Consumes: 当前 attempt 的 Result Bundle。
- Produces: 接受/拒收诊断、正式注册表和原子晋升产物。

- [ ] **Step 1: 写 attempt 绑定失败测试**

覆盖错误 run/work unit/attempt/job/lease nonce、迟到 attempt、重复提交、错误 schema
版本。每个拒收都必须：

```python
before = hash_tree(run_root / "evidence")
result = ingest_result(run_root, bad_result, contract)
self.assertEqual(result["accepted"], False)
self.assertEqual(hash_tree(run_root / "evidence"), before)
```

Gate 拒收不得修改任何业务文件；Runtime 在收到拒收结果后另行追加事件。

- [ ] **Step 2: 写注册表失败测试**

覆盖：

- Agent 改名或合并预分配 fact ID；
- `skill.extra.*` 之外的可选 ID；
- source 悬空或同源镜像冒充独立来源；
- artifact 路径越界、字节数或哈希不符；
- 机器关键 section ID 缺失；
- unknown role 和缺强制角色。

- [ ] **Step 3: 写 calculation request 失败测试**

```python
request = {
    "calculation_id": "valuation.base",
    "operation": "three-scenario",
    "args": {"bear": "10", "base": "15", "bull": "20"},
}
```

断言 Gate 调用 `financial_rigor.py`、保存唯一 envelope、报告只能引用 Gate 格式化结果；
Agent 带 `expected.result` 或报告数字不一致时拒收。每项 calculation 包含币种、单位、
期间、输入精度、展示舍入和 contract tolerance；缺 tolerance 时严格一致。

- [ ] **Step 4: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_gate tests.test_financial_rigor -v
```

- [ ] **Step 5: 实现内存校验事务**

顺序固定为 schema → lease → path/hash → sources → facts → calculations →
judgments → sections → roles → contract evidence。全部成功后：

1. 写新注册表临时文件；
2. 将 attempt artifact 原子复制/替换到 contract formal path；
3. 更新 manifest execution state 为 COMPLETE；

Gate 返回接受结果后，Runtime 再追加接受事件并把 work unit 标记为 DONE。

任一步失败不晋升，不把 Skill 标为 COMPLETE。

- [ ] **Step 6: 实现 PWL 白名单**

只接受 `tushare_unavailable/web_bandwidth_degraded/ephemeral_source`，且检查设计文档
定义的证据前提。所有禁止项直接 FAIL，不允许自由文本 limitation 改变状态。

- [ ] **Step 7: 运行测试确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_gate tests.test_financial_rigor -v
bash scripts/check.sh
```

- [ ] **Step 8: Commit**

```bash
git add tools/full_analysis_gate.py tools/financial_rigor_result_schema.json tests/test_full_analysis_gate.py tests/test_financial_rigor.py
git commit -m "feat(full-analysis): 原子收取事实计算与产物"
```

---

## Milestone 3 — Runtime：状态、恢复、预算与调度

### Task 6: 建立 Runtime 单写队列与事件账本

**Files:**

- Create: `tools/full_analysis_runtime.py`
- Create: `scripts/full_analysis.py`
- Create: `tests/test_full_analysis_runtime.py`
- Create: `tests/test_full_analysis_cli.py`
- Modify: `scripts/check.sh`

**Interfaces:**

- Consumes: Contract v2、capability receipt、Gate v2。
- Produces: runtime-state、events、attempt/work packet、公开 lifecycle CLI。

- [ ] **Step 1: 写状态机失败测试**

```python
LEGAL = {
    "PENDING": {"RUNNING", "FAILED"},
    "RUNNING": {"READY_TO_INGEST", "RETRY_WAIT", "ABANDONED", "FAILED"},
    "RETRY_WAIT": {"PENDING", "FAILED"},
    "READY_TO_INGEST": {"DONE", "RETRY_WAIT", "FAILED"},
    "DONE": set(),
    "FAILED": set(),
    "ABANDONED": set(),
}
```

测试非法跃迁、原子 snapshot、单调 event sequence、重复提交幂等、attempt 目录精确
结构和 Agent 只写自己目录。默认记录不包含完整 transcript、隐藏推理或完整工具日志；
`debug_transcript=true` 只影响一个 local-only run，不改变 Gate 结果。

- [ ] **Step 2: 写单驱动锁失败测试**

同一 run 第二个 session 获取写锁必须失败且仍能执行 `status`。锁包含 session ID、
heartbeat 和创建时间；失效锁只有 `resume` 能接管。

- [ ] **Step 3: 写 CLI 失败测试**

公开 help 只展示：

```text
start
status
resume
cleanup
```

内部命令仍可由 WorkBuddy Skill 调用，但 `argparse` help 使用
`argparse.SUPPRESS`。`status/resume` 必须使用明确 run ID；公司名不能自动选择最新
目录。`cleanup` 无 `--execute` 时只读。

- [ ] **Step 4: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_runtime tests.test_full_analysis_cli -v
```

- [ ] **Step 5: 实现最小 Runtime**

使用标准库 `dataclasses/pathlib/json/fcntl`（macOS/Linux）或现有可移植锁逻辑，
不引入数据库。全局和 attempt 事件使用一行一个 JSON 的 `events.jsonl`。

Work Packet 只包含 contract-declared input slice、稳定输出路径、角色、section ID、
result schema、execution mode、attempt/job binding 和禁止行为。

- [ ] **Step 6: 实现 public CLI 和内部桥接**

`scripts/full_analysis.py` 只导入 Runtime/Gate 的 `main()` 或 dispatcher，不保存业务
清单。`start` 要求 WorkBuddy capability receipt；Python CLI 自身不尝试生成 Agent。

- [ ] **Step 7: 运行测试确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_runtime tests.test_full_analysis_cli -v
bash scripts/check.sh
```

- [ ] **Step 8: Commit**

```bash
git add tools/full_analysis_runtime.py scripts/full_analysis.py tests/test_full_analysis_runtime.py tests/test_full_analysis_cli.py scripts/check.sh
git commit -m "feat(full-analysis): 建立持久 Runtime 与生命周期入口"
```

### Task 7: Lease、resume 和关键版本冻结

**Files:**

- Modify: `tools/full_analysis_runtime.py`
- Modify: `tests/test_full_analysis_runtime.py`

**Interfaces:**

- Consumes: Runtime state 和关键文件哈希。
- Produces: 精确恢复、ABANDONED、版本漂移阻断。

- [ ] **Step 1: 写 lease/restart 失败测试**

冻结时钟，覆盖：

- 20 分钟 lease；
- heartbeat 延长；
- app restart 后旧 RUNNING → ABANDONED；
- 完整且已 Gate 接受的 DONE 结果复用；
- 未完成 unit 使用新 attempt；
- 迟到旧结果拒收。

- [ ] **Step 2: 写 resume 边界失败测试**

```python
def test_resume_rejects_after_twenty_four_hours(self):
    result = resume_run(run_root, now=started_at + timedelta(hours=24, seconds=1))
    self.assertEqual(result["status"], "NEW_RUN_REQUIRED")

def test_resume_rejects_critical_hash_drift(self):
    mutate_critical_file(snapshot_fixture)
    self.assertEqual(resume_run(run_root, now)["status"], "VERSION_MISMATCH")
```

只冻结 WorkBuddy Skill、Runtime、Gate、Contract、Result schema、financial_rigor、
report_audit 和 data CLI。无关工作树变化不失败。

增加测试：新 run 不读取其他 run 的 facts、sources、calculations、Agent artifacts 或
audit；正式目录出现非 Gate 晋升写入时设置 `manual_intervention=true`。Registered
Runtime/Gate/当前租约 Agent 的合法写入不得误报。

- [ ] **Step 3: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_runtime -v
```

- [ ] **Step 4: 实现恢复**

`resume` 属运行控制，不设置 manual_intervention。任何用户 fact/artifact/state 修补仍由
Gate watch 标记人工干预。跨版本和 >24h 只允许 status，不能迁移。

- [ ] **Step 5: 运行测试确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_runtime -v
```

- [ ] **Step 6: Commit**

```bash
git add tools/full_analysis_runtime.py tests/test_full_analysis_runtime.py
git commit -m "feat(full-analysis): 支持租约与同版本恢复"
```

### Task 8: 并发、429、job 预算和 4 小时收口

**Files:**

- Modify: `tools/full_analysis_runtime.py`
- Modify: `tests/test_full_analysis_runtime.py`

**Interfaces:**

- Consumes: work DAG、Agent receipt、时钟。
- Produces: 有界调度决策和 `PARTIAL` 终止原因。

- [ ] **Step 1: 写固定并发和退避失败测试**

断言：

- 同时 RUNNING ≤ 2；
- 首个 429 后并发降为 1；
- 冷却 600 秒；
- 普通失败按 60/180 秒进入下一 attempt；
- 每个 unit 总 attempt ≤ 3；
- Agent 启动即计 job，失败不退款。

- [ ] **Step 2: 写预算预留失败测试**

```python
RESERVED_FINAL_JOBS = 3  # audit + synthesis + synthesis repair

def test_noncore_never_consumes_core_reserve(self):
    state["jobs_used"] = 41
    state["minimum_core_jobs_remaining"] = 6
    self.assertEqual(next_work(run_root, now), core_only_units)
```

达到 45 后停止所有新的非核心派发和重试；达到 50 后 `stop_reason=JOB_LIMIT`，不得
继续 dispatch。预检 Agent probe 在成功 run 中计 1 job。

- [ ] **Step 3: 写 wall-clock 失败测试**

3 小时只记录目标超时提示，不停止；4 小时立即停止新派发、终态 PARTIAL，并调用
确定性 partial/summary renderer。预算或时间耗尽不得 PWL。

- [ ] **Step 4: 写依赖图失败测试**

测试只有三个硬屏障：preflight、fact/source/calculation freeze、audit/finalization。
同一展示分组内的非关键 unit 不得阻塞依赖已满足的 unit。

- [ ] **Step 5: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_runtime -v
```

- [ ] **Step 6: 实现调度策略**

使用注入时钟，单元测试不真实 sleep。多角色每个角色、integrator/editor/reader、
Audit、Synthesis、repair 都按真实 WorkBuddy Agent 调用计 job。

- [ ] **Step 7: 运行测试确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_runtime -v
bash scripts/check.sh
```

- [ ] **Step 8: Commit**

```bash
git add tools/full_analysis_runtime.py tests/test_full_analysis_runtime.py
git commit -m "feat(full-analysis): 实施限流预算与时限收口"
```

---

## Milestone 4 — 数据、来源、计算与审计

### Task 9: 数据能力预检与冻结命令回执

**Files:**

- Modify: `tools/full_analysis_runtime.py`
- Modify: `tools/full_analysis_gate.py`
- Modify: `tests/test_full_analysis_runtime.py`
- Modify: `tests/test_full_analysis_gate.py`
- Modify only if required by failing contract tests: `tools/ashare_data.py`
- Modify only if required: `tests/test_ashare_data.py`

**Interfaces:**

- Consumes: WorkBuddy capability receipt 和现有 data CLI。
- Produces: preflight、command receipt、source record、固定 `as_of`。

- [ ] **Step 1: 写 preflight 失败测试**

Local preflight 检查可执行文件、CLI help/smoke、路径权限和关键哈希。WorkBuddy 侧
capability receipt 必须证明 Agent 工具、公开只读 Web 和 probe attempt 写入；缺一项
不创建 manifest。

- [ ] **Step 2: 写数据生命周期失败测试**

Runtime 在消费者 Agent 启动前运行 Contract 声明的命令并冻结：

```json
{
  "command_id": "cmd.quote.primary",
  "operation": "quote",
  "argv_redacted": ["python3", "tools/ashare_data.py", "quote", "000651"],
  "exit_code": 0,
  "stdout_path": "evidence/commands/cmd.quote.primary.stdout",
  "stderr_path": "evidence/commands/cmd.quote.primary.stderr",
  "sha256": "64-hex",
  "completed_at": "ISO-8601"
}
```

命令回执归 Gate 所有，不与 `ashare-data=RUNNING` 绑定；引用冻结后禁止新增或修改已被
消费的命令。

- [ ] **Step 3: 写 Tushare 与 freshness 失败测试**

覆盖 configured+available、configured+unavailable、not configured、替代来源充分和
全部来源失败。测试不读取 token 内容。行情使用最近收盘交易日；财务使用截至 as_of
最新正式披露；无法证明最新时产生禁止 PWL 的 `stale_data`。冻结时钟跨过午夜后，
同一 run 的 as_of 和数据快照不得自动刷新。

- [ ] **Step 4: 写来源独立性失败测试**

同一公告的公司站与交易所镜像只算一源；primary+独立数据服务算两源；ephemeral 不得
成为核心事实唯一来源。PDF 保存原文件和 SHA-256；Web/news 只保存短摘录元数据。

- [ ] **Step 5: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_runtime tests.test_full_analysis_gate tests.test_ashare_data -v
```

- [ ] **Step 6: 实现最小数据桥**

Runtime 只运行真实 CLI 并冻结回执，不用正则从 stdout 生成事实。`ashare-data`
Agent 使用回执和独立来源填充 Runtime 预分配 fact slot，Gate 再验证。

只有现有 CLI 无法给出稳定退出码或必要机器输出时才改 `ashare_data.py`，且只为该
真实边界增加最小 `--json`/exit-code 支持。

- [ ] **Step 7: 运行测试确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_runtime tests.test_full_analysis_gate tests.test_ashare_data -v
bash scripts/check.sh
```

- [ ] **Step 8: Commit**

```bash
git add tools/full_analysis_runtime.py tools/full_analysis_gate.py tests/test_full_analysis_runtime.py tests/test_full_analysis_gate.py
# 仅实际修改时追加 tools/ashare_data.py tests/test_ashare_data.py
git commit -m "feat(full-analysis): 冻结数据能力与来源回执"
```

### Task 10: 稳定 report_audit API 与 Shared Evidence Audit

**Files:**

- Modify: `tools/report_audit.py`
- Modify: `tools/full_analysis_gate.py`
- Modify: `tools/full_analysis_runtime.py`
- Modify: `tests/test_report_audit.py`
- Modify: `tests/test_full_analysis_gate.py`
- Modify: `tests/test_full_analysis_runtime.py`

**Interfaces:**

- Consumes: 已登记 artifact、facts、sources、calculations。
- Produces: `evidence/audit/` 中的确定性审计记录和综合准入信号。

- [ ] **Step 1: 写函数 API 失败测试**

```python
result = audit_registered_artifact(
    report_path,
    audit_records=[verified_fact],
    policy={"minimum_samples": 1, "require_independent_sources": True},
)
self.assertIn(result["status"], {"PASS", "INSUFFICIENT", "FAIL"})
```

Gate 传 artifact ID 和已登记路径；未知路径、0 样本和缺独立来源 fail closed。
继续保留明确 CLI 子命令用于诊断，但禁止“传文件路径猜命令”。

- [ ] **Step 2: 写 Audit Agent 抽样失败测试**

Runtime 生成一个独立 system work unit，抽样必须：

- 100% 核心事实、估值输入、重大风险和所有 `eligible_for_final=true` 的事实；
- 100% calculation；
- 普通事实确定性抽样 10%，最少 5 条；
- 高严重度来源冲突全检；
- 普通样本失败后扩展同类全检。

Audit Work Packet 不包含作者结论，只包含 fact/source/calculation slice。

- [ ] **Step 3: 写 audit failure 阻断测试**

核心 fact FAIL 或 INSUFFICIENT 时不得生成 Final Synthesis work unit；不能 PWL。

- [ ] **Step 4: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_report_audit tests.test_full_analysis_gate tests.test_full_analysis_runtime -v
```

- [ ] **Step 5: 实现函数接口和审计调度**

复用现有提取/判决函数，移除 Gate 对 shell 命令拼接的依赖。抽样 seed 写入审计记录，
结果路径由 Gate 分配。

- [ ] **Step 6: 运行测试确认 GREEN**

```bash
python3 -m unittest tests.test_report_audit tests.test_full_analysis_gate tests.test_full_analysis_runtime -v
bash scripts/check.sh
```

- [ ] **Step 7: Commit**

```bash
git add tools/report_audit.py tools/full_analysis_gate.py tools/full_analysis_runtime.py tests/test_report_audit.py tests/test_full_analysis_gate.py tests/test_full_analysis_runtime.py
git commit -m "feat(full-analysis): 建立独立证据审计接口"
```

### Task 11: 最终综合、FINAL/PARTIAL 和确定性 SUMMARY

**Files:**

- Modify: `tools/full_analysis_runtime.py`
- Modify: `tools/full_analysis_gate.py`
- Modify: `tests/test_full_analysis_runtime.py`
- Modify: `tests/test_full_analysis_gate.py`

**Interfaces:**

- Consumes: 审计通过的冻结核心材料和 Final Synthesis Result Bundle。
- Produces: `FINAL_REPORT.md` 或 `PARTIAL_REPORT.md`、`SUMMARY.md`、`status.json`。

- [ ] **Step 1: 写综合输入隔离失败测试**

Final Synthesis Work Packet 只能读取已接受核心报告、研究增强报告、facts、sources、
calculations 和 audit verdict；其中可引用 fact 的审计状态必须为 PASS。它不能读取
失败 attempt、角色错误日志或派生内容新增事实。

- [ ] **Step 2: 写 FINAL 失败测试**

FINAL 必须包含 12 个设计章节、三情景估值和稳定 `[F:]/[S:]/[C:]` 引用。每个核心
数字和判断可追溯；不检查字数。格式/引用错误最多创建一次 synthesis repair，不能改
冻结事实或替代失败 Skill。Gate 必须逐个断言实际引用的 fact 都属于审计 PASS 集合。

- [ ] **Step 3: 写 PARTIAL 失败测试**

任一核心 FAIL、适用契约未完成、job=50、wall=4h、manual intervention、version drift
或 synthesis repair 失败时：

```python
self.assertFalse((run_root / "FINAL_REPORT.md").exists())
self.assertTrue((run_root / "PARTIAL_REPORT.md").is_file())
self.assertIn("未准出", partial_text)
self.assertNotIn("建议买入", partial_text)
```

PARTIAL 由 Gate 确定性生成，不消耗 Agent。

- [ ] **Step 4: 写 SUMMARY 幂等失败测试**

相同 registry/runtime/audit 输入生成字节一致的正文（受控生成时间字段除外）。包含
20 项矩阵、N/A、job、重试、429、时长、PWL、人工干预、版本哈希、中间体积和链接。

- [ ] **Step 5: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_gate tests.test_full_analysis_runtime -v
```

- [ ] **Step 6: 实现单向准出**

冻结研究 → Audit → Synthesis staging → deterministic final audit → atomic FINAL。
顶层状态严格使用 RUNNING/APPROVED/PARTIAL/FAILED；`human_review` 初始为 PENDING，
不改变机器状态。

- [ ] **Step 7: 运行测试确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_gate tests.test_full_analysis_runtime -v
bash scripts/check.sh
```

- [ ] **Step 8: Commit**

```bash
git add tools/full_analysis_runtime.py tools/full_analysis_gate.py tests/test_full_analysis_runtime.py tests/test_full_analysis_gate.py
git commit -m "feat(full-analysis): 确定性生成最终交付物"
```

---

## Milestone 5 — 业务 Skill 适配与 WorkBuddy 总控

### Task 12: 为 20 个业务 Skill 增加最小无人值守适配

**Files:**

- Modify:
  - `skills/ashare-data.md`
  - `skills/financial-data.md`
  - `skills/quality-screen.md`
  - `skills/investment-checklist.md`
  - `skills/investment-research.md`
  - `skills/investment-team.md`
  - `skills/management-deep-dive.md`
  - `skills/earnings-review.md`
  - `skills/earnings-team.md`
  - `skills/industry-research.md`
  - `skills/industry-funnel.md`
  - `skills/bottleneck-hunter.md`
  - `skills/news-pulse.md`
  - `skills/thesis-tracker.md`
  - `skills/thesis-drift.md`
  - `skills/portfolio-review.md`
  - `skills/private-company-research.md`
  - `skills/deep-company-series.md`
  - `skills/dyp-ask.md`
  - `skills/wechat-article.md`
- Regenerate: 上述 20 个同名 `codex-skills/*/SKILL.md`
- Create: `tests/test_full_analysis_skill_adapters.py`

**Interfaces:**

- Consumes: `execution_mode=UNATTENDED_FULL_ANALYSIS` Work Packet。
- Produces: 无重复确认、仍保留 HARD STOP 的业务 Skill。

- [ ] **Step 1: 写适配一致性失败测试**

测试从 Contract 读取 20 项 `spec_source`，逐文件断言存在唯一 marker：

```markdown
<!-- unattended-full-analysis-adapter:v1 -->
```

并断言适配段包含 `Work Packet`、`Result Bundle`、`不得重复确认`、`公开只读`、
`已分配 attempt 目录`、`不得发布/交易/读取敏感信息`。

- [ ] **Step 2: 写冲突 STOP 扫描失败测试**

仅在 `UNATTENDED_FULL_ANALYSIS` 语义下，公开 WebSearch、公开数据、Agent 启动和
assigned attempt write 不得要求用户确认。身份歧义、敏感信息、外部写入、交易和
未分配路径必须继续 STOP。

- [ ] **Step 3: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_skill_adapters -v
```

- [ ] **Step 4: 向每个 canonical Skill 加入同一最小规则块**

规则块明确只覆盖全量模式；它优先于本 Skill 中针对公开只读研究的旧确认点，但不覆盖
HARD STOP。研究方法正文保持不变，Result Bundle 细节由 Work Packet 提供，不在 20
份文件复制 schema。

- [ ] **Step 5: 重新生成并验证 Codex Skill**

```bash
python3 scripts/sync-codex-skills.py
python3 scripts/sync-codex-skills.py --check
python3 -m unittest tests.test_full_analysis_skill_adapters -v
```

- [ ] **Step 6: Commit**

```bash
git add skills/ashare-data.md skills/financial-data.md skills/quality-screen.md skills/investment-checklist.md skills/investment-research.md skills/investment-team.md skills/management-deep-dive.md skills/earnings-review.md skills/earnings-team.md skills/industry-research.md skills/industry-funnel.md skills/bottleneck-hunter.md skills/news-pulse.md skills/thesis-tracker.md skills/thesis-drift.md skills/portfolio-review.md skills/private-company-research.md skills/deep-company-series.md skills/dyp-ask.md skills/wechat-article.md
git add codex-skills/ashare-data/SKILL.md codex-skills/financial-data/SKILL.md codex-skills/quality-screen/SKILL.md codex-skills/investment-checklist/SKILL.md codex-skills/investment-research/SKILL.md codex-skills/investment-team/SKILL.md codex-skills/management-deep-dive/SKILL.md codex-skills/earnings-review/SKILL.md codex-skills/earnings-team/SKILL.md codex-skills/industry-research/SKILL.md codex-skills/industry-funnel/SKILL.md codex-skills/bottleneck-hunter/SKILL.md codex-skills/news-pulse/SKILL.md codex-skills/thesis-tracker/SKILL.md codex-skills/thesis-drift/SKILL.md codex-skills/portfolio-review/SKILL.md codex-skills/private-company-research/SKILL.md codex-skills/deep-company-series/SKILL.md codex-skills/dyp-ask/SKILL.md codex-skills/wechat-article/SKILL.md tests/test_full_analysis_skill_adapters.py
git commit -m "feat(skills): 适配无人值守全量工作包"
```

### Task 13: 建立仓库 canonical WorkBuddy 总控 Skill

**Files:**

- Create: `workbuddy-skills/full-company-analysis/SKILL.md`
- Modify: `skills/full-company-analysis.md`
- Regenerate: `codex-skills/full-company-analysis/SKILL.md`
- Create: `tests/test_workbuddy_full_analysis_skill.py`

**Interfaces:**

- Consumes: `scripts/full_analysis.py` 内部命令和 WorkBuddy Agent 工具。
- Produces: 唯一生产 Agent orchestration loop。

- [ ] **Step 1: 写 WorkBuddy Skill 静态失败测试**

断言总控 Skill：

- 直接使用 WorkBuddy Agent；
- 不引用 `tools/full_analysis_orchestrator.py` 或外置 `orchestrate.py`；
- 子 Agent 只返回 `attempt_id/result_path/status/bytes`；
- main 不读取报告全文；
- 不按 Skill 硬编码模型，使用 WorkBuddy 当前默认并仅记录实际返回的 model ID；
- 默认不保存完整 transcript、隐藏推理或完整工具日志；
- 每次派发前领取 Work Packet 和租约；
- 429/timeout/heartbeat/result submission 交给 Runtime；
- 不提供单上下文 fallback；
- 最终只链接 FINAL/PARTIAL 和 SUMMARY。

- [ ] **Step 2: 写多角色与最小上下文失败测试**

Contract 角色顺序必须映射为独立 Agent jobs；角色互不可见；integrator 只能读取本组
已接受角色结果；Audit 看 facts/sources 而非作者结论；Synthesis 不读失败 attempts。

- [ ] **Step 3: 写 Claude/Codex 非支持失败测试**

`skills/full-company-analysis.md` 必须明确非 WorkBuddy 无人值守编排不受支持，禁止
静默单上下文 COMPLETE；仍可指导用户使用单个业务 Skill 和确定性工具。

- [ ] **Step 4: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_workbuddy_full_analysis_skill -v
```

- [ ] **Step 5: 写 WorkBuddy orchestration loop**

Skill 顺序固定：

1. 解析唯一公司身份；
2. 运行 local preflight；
3. 调用一个 capability probe Agent；
4. `start` 创建 run；
5. 循环 `next-work → Agent → short receipt → submit/failure`；
6. 维持 heartbeat；
7. Runtime 返回终态后输出链接。

总控不允许手工补 fact、section、artifact 或 report。

- [ ] **Step 6: 更新兼容 Skill 并生成 Codex**

```bash
python3 scripts/sync-codex-skills.py
python3 scripts/sync-codex-skills.py --check
python3 -m unittest tests.test_workbuddy_full_analysis_skill -v
```

- [ ] **Step 7: Commit**

```bash
git add workbuddy-skills/full-company-analysis/SKILL.md skills/full-company-analysis.md codex-skills/full-company-analysis/SKILL.md tests/test_workbuddy_full_analysis_skill.py
git commit -m "feat(workbuddy): 增加原生全量分析总控 Skill"
```

### Task 14: 增加显式 WorkBuddy Skill 安装器

**Files:**

- Create: `scripts/install-workbuddy-skills.sh`
- Create: `tests/test_install_workbuddy_skills.py`

**Interfaces:**

- Consumes: 仓库 `workbuddy-skills/`。
- Produces: 默认预览、`--check`、显式 `--install`。

- [ ] **Step 1: 写安装器失败测试**

使用临时 `WORKBUDDY_HOME`，断言：

- 无参数不写入，只打印 planned diff；
- `--check` 在缺失/漂移时返回非零；
- `--install` 只复制 canonical Skill；
- 安装后 `--check` 返回 0；
- 未知参数返回 2；
- 不修改权限配置、不启动 WorkBuddy、不自动监听。

- [ ] **Step 2: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_install_workbuddy_skills -v
```

- [ ] **Step 3: 实现安装器**

目标固定为 `${WORKBUDDY_HOME:-$HOME/.workbuddy}/skills/full-company-analysis/SKILL.md`。
仅创建精确父目录和复制单文件；不使用宽泛 glob 或递归删除。

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
python3 -m unittest tests.test_install_workbuddy_skills -v
bash scripts/install-workbuddy-skills.sh
```

第二条只预览，不写真实 WorkBuddy 目录。

- [ ] **Step 5: Commit**

```bash
git add scripts/install-workbuddy-skills.sh tests/test_install_workbuddy_skills.py
git commit -m "feat(workbuddy): 增加显式 Skill 安装检查"
```

---

## Milestone 6 — Hermetic E2E 与故障注入

### Task 15: 跑完整单公司 fake WorkBuddy 流程

**Files:**

- Create: `tests/fakes/fake_workbuddy_agent.py`
- Create: `tests/fakes/fake_ashare_data.py`
- Create: `tests/test_full_analysis_e2e.py`
- Modify: `scripts/check.sh`
- Modify only if current workflow does not call check.sh: `.github/workflows/check.yml`

**Interfaces:**

- Consumes: Contract、Runtime、Gate、incidents fixture。
- Produces: 不联网的完整生命周期证明。

- [ ] **Step 1: 写 happy-path E2E 失败测试**

在临时 run-root 模拟 WorkBuddy 的短回执循环。断言：

- 20 项均进入 PASS/PWL/N/A/FAIL 终态；
- 合法 fixture 为 APPROVED；
- 多角色 job ID 全部独立；
- 正式目录没有 attempt 文件；
- FINAL、SUMMARY、status 存在；
- manual intervention=0；
- jobs_used≤50。

- [ ] **Step 2: 参数化全部故障**

逐条读取 `incidents.json`，并增加：

- 429 降并发和冷却；
- timeout/lease/ABANDONED/resume；
- late result/lease mismatch；
- result schema/hash/fact ID/section；
- calculation mismatch + 一次 repair；
- unregistered write；
- version drift；
- 45/50 job；
- 4 小时；
- Tushare fallback；
- audit insufficient；
- core failure no FINAL。

- [ ] **Step 3: 写“主 Agent 不代写”保护测试**

失败 Agent 三次后 Skill 必须 BLOCKED；fake main 不得创建该 Skill 正式报告。任何
single-context role receipt 都 FAIL。

- [ ] **Step 4: 写重放一致性测试**

同一 fixture、固定时钟和 seed 两次运行应生成相同 contract matrix、registry 和
SUMMARY 正文；run ID 和时间字段单独归一。

- [ ] **Step 5: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_e2e -v
```

- [ ] **Step 6: 只连接真实四个边界**

1. Runtime 生成 Work Packet；
2. fake Agent 写 attempt artifacts/Result Bundle；
3. Runtime 提交给 Gate；
4. Gate 接受后 Runtime 标记 DONE。

Fake 不得直接写 manifest、注册表或正式报告。

- [ ] **Step 7: 纳入统一检查**

`scripts/check.sh` 继续使用 unittest discovery；只更新职责说明或缺失调用。
若 `.github/workflows/check.yml` 已调用 `bash scripts/check.sh`，不修改 workflow。

- [ ] **Step 8: 运行完整验证**

```bash
python3 -m unittest tests.test_full_analysis_e2e -v
bash scripts/check.sh
```

Expected: 全部 PASS；E2E 无网络。

- [ ] **Step 9: Commit**

```bash
git add tests/fakes/fake_workbuddy_agent.py tests/fakes/fake_ashare_data.py tests/test_full_analysis_e2e.py scripts/check.sh
# 仅实际修改时追加 .github/workflows/check.yml
git commit -m "test(full-analysis): 覆盖无人值守故障闭环"
```

---

## Milestone 7 — 直接迁移与旧控制面清理

### Task 16: 删除仓库旧入口并更新现有文档

**Files:**

- Delete: `scripts/run_full_analysis.py`
- Delete: `scripts/batch_full_analysis.py`
- Create: `tests/test_full_analysis_entrypoints.py`
- Modify: `README.md`
- Modify: `docs/ROADMAP.md`

**Interfaces:**

- Consumes: 新 `scripts/full_analysis.py` 和 WorkBuddy Skill。
- Produces: 仓库只剩一个全量分析控制路径。

- [ ] **Step 1: 写旧入口退役失败测试**

断言仓库不存在两个旧脚本；README 只指向 WorkBuddy Skill 和
`scripts/full_analysis.py`；仓库中没有 `full_analysis_orchestrator.py`、
旧 batch 公司清单或硬编码 as-of。

- [ ] **Step 2: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_entrypoints -v
```

- [ ] **Step 3: 用 apply_patch 删除两个旧脚本**

不修改任何 `local/` 报告。确认 `rg` 无生产调用方后删除：

- `scripts/run_full_analysis.py`
- `scripts/batch_full_analysis.py`

- [ ] **Step 4: 更新 README 和 ROADMAP**

README 只说明 start/status/resume/cleanup、WorkBuddy-only 边界、不会发布/交易；
ROADMAP 记录 hermetic E2E 完成、格力 live canary 待验收。不开新 ADR/USAGE。

- [ ] **Step 5: 运行测试确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_entrypoints -v
bash scripts/check.sh
git diff --check
```

- [ ] **Step 6: Commit**

```bash
git add README.md docs/ROADMAP.md tests/test_full_analysis_entrypoints.py
git add scripts/run_full_analysis.py scripts/batch_full_analysis.py
git commit -m "refactor(full-analysis): 移除旧批量与模板入口"
```

### Task 17: 清理仓库外旧编排器并安装新 Skill

**Files outside repository, explicitly confirmed for direct cleanup:**

- Delete:
  - `/Users/psilhon/.workbuddy/berkshire-skill-sync/orchestrate.py`
  - `/Users/psilhon/.workbuddy/berkshire-skill-sync/_run_l1.py`
  - `/Users/psilhon/.workbuddy/berkshire-skill-sync/_verify.py`
  - `/Users/psilhon/.workbuddy/berkshire-skill-sync/cleanup_intermediate_dirs.py`
  - `/Users/psilhon/.workbuddy/berkshire-skill-sync/skills/full-company-analysis.md`
  - `/Users/psilhon/.workbuddy/berkshire-skill-sync/MIGRATE-full-company-analysis.md`
  - `/Users/psilhon/.workbuddy/berkshire-skill-sync/FULL-ANALYSIS-REPORT.md`
  - `/Users/psilhon/.workbuddy/skills/full-company-analysis/`
- Modify:
  - `/Users/psilhon/.workbuddy/berkshire-skill-sync/sync.py`
  - `/Users/psilhon/.workbuddy/berkshire-skill-sync/README.md`
  - `/Users/psilhon/.workbuddy/berkshire-skill-sync/CHANGELOG.md`

**Interfaces:**

- Consumes: 用户自有备份、新 canonical WorkBuddy Skill、全部本地 GREEN 证据。
- Produces: 无旧 wrapper/回退的单一安装副本；保留其余 20 个业务 Skill 同步。

- [ ] **Step 1: 迁移前验证**

```bash
bash scripts/check.sh
python3 -m unittest tests.test_full_analysis_e2e tests.test_install_workbuddy_skills -v
bash scripts/install-workbuddy-skills.sh --check
```

前两条必须 PASS；第三条在旧副本存在时应报告 drift/nonzero，不写文件。

- [ ] **Step 2: 精确解析目标**

逐个使用 `test -e` 和绝对路径确认目标；不得展开 glob，不得触碰
`.learnings/`、其余业务 Skill 或用户未点名文件。用户已声明自行备份，本 Task 不再
复制备份。

- [ ] **Step 3: 修改外部同步器**

从 `sync.py` 删除旧 `ORCH_SRC`/`_render_orch` 和 full-company 特例；README 与
CHANGELOG 删除 active usage。运行：

```bash
python3 /Users/psilhon/.workbuddy/berkshire-skill-sync/sync.py --check
```

Expected: 其余 20 个业务 Skill 检查通过，不再生成 full-company-analysis。

- [ ] **Step 4: 删除精确旧文件和安装目录**

仅删除本 Task `Delete` 列表；不使用宽泛目录目标或未解析变量。

- [ ] **Step 5: 安装新 canonical Skill**

```bash
bash scripts/install-workbuddy-skills.sh --install
bash scripts/install-workbuddy-skills.sh --check
```

Expected: 安装成功且哈希一致。

- [ ] **Step 6: 迁移后验证**

```bash
test ! -e /Users/psilhon/.workbuddy/berkshire-skill-sync/orchestrate.py
rg -n "orchestrate\\.py|ORCH_SRC|_render_orch" /Users/psilhon/.workbuddy/berkshire-skill-sync/sync.py /Users/psilhon/.workbuddy/berkshire-skill-sync/README.md /Users/psilhon/.workbuddy/berkshire-skill-sync/CHANGELOG.md
bash scripts/install-workbuddy-skills.sh --check
bash scripts/check.sh
```

第二条 Expected: 无匹配并以 1 退出；其余 Expected: PASS。

> 外部修改不进入本仓库 commit。记录执行时间、目标和验证结果，但不复制旧代码内容。

---

## Milestone 8 — 格力电器单次 Live Canary

### Task 18: 建立单 run canary checker

**Files:**

- Create: `scripts/check-full-analysis-canary.py`
- Create: `tests/test_full_analysis_canary_check.py`
- Local-only review record: `local/full-analysis-canary/{run_id}/human-review.json`

**Interfaces:**

- Consumes: 一个 run-root/status 和一个人工接受/拒绝记录。
- Produces: 二元上线验收结果。

- [ ] **Step 1: 写 checker 失败测试**

合法输入必须同时满足：

```python
{
    "company_code": "000651.SZ",
    "run_status": "APPROVED",
    "human_review": "ACCEPTED",
    "manual_intervention": False,
    "jobs_used_max": 50,
    "wall_clock_seconds_max": 14400
}
```

并检查 20 项矩阵、N/A 谓词、PWL 白名单、Audit、FINAL/SUMMARY/status、关键哈希、
无未完成状态和正式目录无半成品。任何一项失败时 checker 返回 1 并列出精确原因。

- [ ] **Step 2: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_full_analysis_canary_check -v
```

- [ ] **Step 3: 实现只读 checker**

Checker 不修改 run。人工评价保存在 ignored canary 目录，与机器 run_status 分离；
原 run 即使人工 REJECTED 也不篡改。

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
python3 -m unittest tests.test_full_analysis_canary_check -v
bash scripts/check.sh
```

- [ ] **Step 5: Commit**

```bash
git add scripts/check-full-analysis-canary.py tests/test_full_analysis_canary_check.py
git commit -m "test(full-analysis): 增加单公司实跑验收器"
```

### Task 19: 执行格力电器单次无人值守验收

**Files:**

- Local-only run: WorkBuddy 启动回执给出的格力电器新 run-root
- Local-only review: 与该 run ID 对应的
  `local/full-analysis-canary/{run_id}/human-review.json`
- Modify after acceptance: `docs/ROADMAP.md`

**Interfaces:**

- Consumes: 已安装的新 WorkBuddy Skill 和实时公开数据。
- Produces: 一次机器 APPROVED + 人工 ACCEPTED 的上线证据。

- [ ] **Step 1: 启动前验证日期、安装和本地检查**

```bash
date
bash scripts/install-workbuddy-skills.sh --check
bash scripts/check.sh
```

记录实际日期为 `as_of` 基线，不从训练数据猜日期。

- [ ] **Step 2: 在 WorkBuddy 启动唯一标的**

请求：

```text
全量分析 格力电器 000651.SZ
```

启动回执必须包含唯一 run ID、3 小时目标、4 小时硬上限、约 40 job 目标和 50 job
硬上限；不得要求第二次确认。

- [ ] **Step 3: 运行中只读观察**

允许对启动回执中的 run ID 执行 `status`；禁止编辑
manifest/fact/section/artifact、重置失败 attempt、替写报告或跳过 Gate。
WorkBuddy 重启时只允许对同一 run ID 执行符合设计的 `resume`。

- [ ] **Step 4: 机器终态检查**

```bash
python3 scripts/check-full-analysis-canary.py \
  --run-root "$CANARY_RUN_ROOT" \
  --review-file "$CANARY_REVIEW_FILE"
```

其中 `CANARY_RUN_ROOT` 和 `CANARY_REVIEW_FILE` 分别设为 WorkBuddy 启动回执返回的
绝对 run-root，以及该 run ID 对应的 local-only 人工评价文件。

在人工评价前，Expected: 因 `human_review=PENDING` 返回 1，但机器检查部分必须显示
APPROVED；若机器不是 APPROVED，不进入人工接受步骤。

- [ ] **Step 5: 人工质量评价**

用户阅读 `FINAL_REPORT.md`，只给 `ACCEPTED` 或 `REJECTED`。评价维度为可用性、深度、
可读性和证据支撑，不按字数。不得编辑原报告后改判。

- [ ] **Step 6: 最终 checker**

写入 local-only `human-review.json` 后重跑 Step 4。

Expected: `run_status=APPROVED` 且 `human_review=ACCEPTED` 时 PASS。

若失败：

1. 原 run 保持不变；
2. 将新问题增加到 `incidents.json`；
3. 写 RED 测试；
4. 修复并跑 `bash scripts/check.sh`；
5. 创建全新格力电器 run。

- [ ] **Step 7: 验收通过后更新 Roadmap**

```bash
git add docs/ROADMAP.md
git commit -m "docs(full-analysis): 记录格力无人值守验收通过"
```

不提交 ignored run、review 或完整实时报告。

---

## Final Verification Checklist

- [ ] `python3 -m unittest tests.test_full_analysis_incidents -v`
- [ ] `python3 -m unittest tests.test_full_analysis_contract -v`
- [ ] `python3 -m unittest tests.test_full_analysis_result_schema -v`
- [ ] `python3 -m unittest tests.test_full_analysis_gate -v`
- [ ] `python3 -m unittest tests.test_full_analysis_runtime -v`
- [ ] `python3 -m unittest tests.test_full_analysis_cli -v`
- [ ] `python3 -m unittest tests.test_report_audit -v`
- [ ] `python3 -m unittest tests.test_full_analysis_skill_adapters -v`
- [ ] `python3 -m unittest tests.test_workbuddy_full_analysis_skill -v`
- [ ] `python3 -m unittest tests.test_install_workbuddy_skills -v`
- [ ] `python3 -m unittest tests.test_full_analysis_e2e -v`
- [ ] `python3 -m unittest tests.test_full_analysis_entrypoints -v`
- [ ] `python3 -m unittest tests.test_full_analysis_canary_check -v`
- [ ] `python3 scripts/check-full-analysis-contract.py`
- [ ] `python3 scripts/sync-codex-skills.py --check`
- [ ] `bash scripts/install-workbuddy-skills.sh --check`
- [ ] `bash scripts/check.sh`
- [ ] `git diff --check`
- [ ] `git status --short` 只包含当前 Task 文件和保留的用户 backup
- [ ] 仓库不存在 `tools/full_analysis_orchestrator.py`
- [ ] 仓库内旧 run/batch 脚本不存在
- [ ] 仓库外旧 `orchestrate.py` 和专用 full-company source 不存在
- [ ] 外部 sync 仍能检查其余 20 个业务 Skill
- [ ] 未读取 secret，未修改历史 `local/` 报告，未执行外部系统写入
- [ ] 格力 canary：机器 APPROVED、人工 ACCEPTED、≤50 jobs、≤4h

## Milestone Commit Boundaries

建议保持以下独立提交：

1. 事故 fixture；
2. Contract v2；
3. Result Bundle schema；
4. Gate v2 storage；
5. Gate ingest/calculations；
6. Runtime/CLI；
7. lease/resume；
8. scheduling/budget；
9. data/source receipts；
10. evidence audit；
11. final delivery；
12. 20 Skill adapters；
13. WorkBuddy canonical Skill；
14. WorkBuddy installer；
15. hermetic E2E；
16. legacy repo entry removal/docs；
17. canary checker；
18. canary completion record。

仓库外 WorkBuddy 清理和安装不进入 Git commit；它必须发生在 hermetic E2E 全绿之后、
live canary 之前。

## Explicitly Deferred

以下内容不进入本计划：

- 21 个独立 Skill 的第二轮 Darwin 优化；
- 跨 run cache；
- 自适应并发高于 2；
- HK/US/多公司/私人组合无人值守；
- HTML/图表/发布；
- 密码学 Agent 作者证明；
- 自动清理或后台过期；
- Codex/Claude 无人值守总控。

格力 canary 通过后，另写 Darwin positive-transfer 实施计划。
