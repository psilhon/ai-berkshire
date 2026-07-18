# Full Company Analysis Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 v1.4 实现评审确认的 7 个准出漏洞，使伪造 N/A/审计、失败计算、既有脏文件变更、缺口径双源、非 JSON 参数错误、占位领域证据和缺失运行上下文均被机器检查捕获。

**Architecture:** 保留现有注册表 + gate + financial-rigor 三组件边界。调用者只提交原始证据；gate 读取冻结 schema、重放计算、调用现有报告审计算法、计算谓词和内容摘要，并将最终状态写回 manifest/result。Phase 2 继续由注册表驱动，但每个 IMPLEMENTED 契约必须具有至少一条可执行 evidence rule。

**Tech Stack:** Python 3 标准库、`unittest`、JSON、Git porcelain v2；不新增依赖，不安装全局 Skill，不执行外部写入。

## Global Constraints

- 保持 Apple Silicon 原生兼容与 Python 零第三方依赖。
- 不修改现有研究报告和用户未跟踪文件。
- `skills/*.md` 修改后必须同步 Codex/Prompt 生成物。
- 采用 RED → GREEN；最终运行目标测试、全量测试和 `bash scripts/check.sh`。
- 不 commit、push、PR、publish 或安装全局 Skill。

---

### Task 1: 锁死金融 JSON 与计算重放协议

**Files:**
- Modify: `tests/test_financial_rigor.py`
- Modify: `tests/test_full_analysis_gate.py`
- Modify: `tools/financial_rigor.py`
- Modify: `tools/full_analysis_gate.py`

**Interfaces:**
- Consumes: `tools/financial_rigor_result_schema.json`
- Produces: `validate_financial_envelope(envelope, process_exit_code, schema)` 与严格的 `replay_calculation(calc, schema)`

- [ ] **Step 1: Write failing tests** — 增加非法 Decimal/缺必需参数在 `--json` 下仍只输出一个 ERROR envelope 的测试；增加 expected 缺字段、result 缺字段、工具非 PASS 和进程/JSON exit 不一致的重放测试。
- [ ] **Step 2: Verify RED** — 运行新增测试，确认失败原因分别是 argparse 在 stderr 输出 usage、空 expected 被接受。
- [ ] **Step 3: Implement minimal fix** — 使用 JSON-aware ArgumentParser；gate 按冻结 schema 校验 envelope/result/Decimal/枚举/退出码，并要求 expected 完整匹配且计算结果为 PASS。
- [ ] **Step 4: Verify GREEN** — 运行 `tests/test_financial_rigor.py` 与计算重放测试。

### Task 2: 锁死事实、N/A 与报告审计证据

**Files:**
- Modify: `tests/test_full_analysis_gate.py`
- Modify: `tests/test_full_analysis_phase2.py`
- Modify: `tools/full_analysis_gate.py`

**Interfaces:**
- Consumes: evidence payload、manifest/company、`tools/report_audit.py`
- Produces: 封闭 evidence record schema、`evaluate_applicability(...)`、由 gate 重算的审计 verdict

- [ ] **Step 1: Write failing tests** — 复现缺 subject/period/unit 仍判双源、`always_applicable` 伪 N/A、悬空 input fact、调用者自报审计 PASS。
- [ ] **Step 2: Verify RED** — 确认旧实现仍接受上述四类伪证据。
- [ ] **Step 3: Implement minimal fix** — 严格校验事实/来源/计算/判断/角色/限制/审计记录；N/A 只接受当前契约谓词、可解析 fact ID、谓词实际为 false、负向文件包含谓词/fact/替代路径；审计记录只接收 seed/ratio/原始核验值，由 gate 从 artifact 重新抽样并运行三态判决。
- [ ] **Step 4: Verify GREEN** — 运行 gate 审计、事实、N/A 目标测试。

### Task 3: 为 Git 基线增加内容摘要

**Files:**
- Modify: `tests/test_full_analysis_gate.py`
- Modify: `tools/full_analysis_gate.py`

**Interfaces:**
- Consumes: `git status --porcelain=v2 -z --untracked-files=all`
- Produces: JSON baseline（status record + path fingerprint）与 finalize 双向比较

- [ ] **Step 1: Write failing test** — init 前创建未跟踪文件，init 后只改其内容，finalize 必须 FAIL。
- [ ] **Step 2: Verify RED** — 确认旧 status-record 集合比较错误放行。
- [ ] **Step 3: Implement minimal fix** — 对基线已有 Git 可见路径记录 regular/symlink/type/size/SHA-256；finalize 比较新增、消失、status 变化和内容变化，运行根仍在 allowlist。
- [ ] **Step 4: Verify GREEN** — 运行边界检测测试，确认未改变的既有脏文件仍不误报。

### Task 4: 让 20 项 Phase 2 契约具有可执行专项证据

**Files:**
- Modify: `tests/test_full_analysis_contract.py`
- Modify: `tests/test_full_analysis_phase2.py`
- Modify: `scripts/check-full-analysis-contract.py`
- Modify: `tools/full_analysis_contract.json`
- Modify: `tools/full_analysis_gate.py`

**Interfaces:**
- Consumes: registry `evidence_rules`
- Produces: `min_command_receipts` 规则和 20 项非空专项规则

- [ ] **Step 1: Write failing tests** — IMPLEMENTED + 领域标题但无 evidence rule 必须失败；每个真实契约 evidence_rules 非空；占位报告不带专项 evidence 必须 FAIL。
- [ ] **Step 2: Verify RED** — 确认独立校验器仍把领域标题当实现完成。
- [ ] **Step 3: Implement minimal fix** — 校验器要求 IMPLEMENTED 的 evidence_rules 非空；新增命令收据规则；按 v1.4 §6.4 为 20 项配置 facts/calculations/judgments/roles/commands 的机器计数门。
- [ ] **Step 4: Verify GREEN** — 运行契约校验和 20×GREEN/RED 参数化测试。

### Task 5: 补齐 review mode 与 industry 的可信写入路径

**Files:**
- Modify: `tests/test_full_analysis_gate.py`
- Modify: `tools/full_analysis_gate.py`
- Modify: `skills/full-company-analysis.md`
- Regenerate: `codex-skills/full-company-analysis/SKILL.md`
- Regenerate: `codex-prompts/full-company-analysis.md`

**Interfaces:**
- Produces: `set-review-mode --mode self_review|user|independent_context`；`set-industry --industry-file`，由 gate 计算 primary/multi_segment；result/summary 输出两个字段

- [ ] **Step 1: Write failing tests** — init 默认如实记录 self_review；非法 independent_context 被拒；industry 按 50%/累计 80% 规则计算且 source fact 必须存在；result/summary 包含两字段。
- [ ] **Step 2: Verify RED** — 确认旧 CLI 无这些命令且 result 丢字段。
- [ ] **Step 3: Implement minimal fix** — 增加两条受 schema 约束的上下文命令、finalize 一致性检查及 Skill 调用说明。
- [ ] **Step 4: Verify GREEN** — 运行上下文测试并同步生成物。

### Task 6: 全量回归与交付证据

**Files:**
- Verify only: all changed files

**Interfaces:**
- Produces: 新鲜测试证据与未修复/限制清单

- [ ] **Step 1: Targeted verification** — 运行新增复现测试、金融协议、gate、契约与 Phase 2 测试。
- [ ] **Step 2: Static verification** — 运行生成物 `--check`、Python compile、注册表校验和 diff 检查。
- [ ] **Step 3: Full verification** — 运行 `bash scripts/check.sh`；若项目全量测试不包含所有新增测试，再运行 `python3 -m unittest discover -s tests`。
- [ ] **Step 4: Scope audit** — 检查 git status/diff，确认未修改用户未跟踪内容、无外部写入、无全局安装。

