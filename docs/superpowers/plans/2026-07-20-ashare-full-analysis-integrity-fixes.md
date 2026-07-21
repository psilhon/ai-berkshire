# A-share Full-analysis Integrity Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依次修复 ashare-data 在 full-company-analysis 中的命令映射、条件命令、skill 同步、执行收据、事实血缘与 920 市场识别问题。

**Architecture:** 注册表继续作为命令与消费关系的单一机器真源；gate 负责能力快照、命令实际执行、证据冻结与血缘校验。调用者不再提交可伪造的 ashare 命令收据，只提交由命令输出提炼的事实和产物引用。

**Tech Stack:** Python 3 标准库、JSON、`unittest`、现有 skill 生成/安装脚本；不新增依赖。

## Global Constraints

- 保留当前工作树中用户已有的 private 路径、runner 和报告改动。
- 不读取或输出 `TUSHARE_TOKEN`；只记录 configured/not configured 布尔能力。
- 每项先写失败测试并确认 RED，再做最小实现和目标回归。
- 不 commit、push、PR、publish 或执行外部写入。

---

### Task 1: 注册表与 CLI 命令一致性

**Files:**
- Modify: `tests/test_full_analysis_contract.py`
- Modify: `scripts/check-full-analysis-contract.py`
- Modify: `tools/full_analysis_contract.json`

**Interfaces:**
- Consumes: `tools/ashare_data.py` 中 `add_parser(...)` 命令集合
- Produces: 注册表 required/conditional operation 必须存在于 CLI 的独立校验

- [ ] 写测试证明真实注册表的 `overview` 不存在于 CLI，确认 RED。
- [ ] 增加独立命令集合校验并将 `overview` 改为 `quote`。
- [ ] 运行契约测试与独立注册表检查，确认 GREEN。

### Task 2: 条件 Tushare 命令强制语义

**Files:**
- Modify: `tests/test_full_analysis_gate.py`
- Modify: `tests/test_full_analysis_contract.py`
- Modify: `scripts/check-full-analysis-contract.py`
- Modify: `tools/full_analysis_contract.json`
- Modify: `tools/full_analysis_gate.py`

**Interfaces:**
- Produces: init 时冻结 `tushare_configured`；已配置时命令必须成功，未配置时必须记录 `tushare_not_configured`

- [ ] 写已配置缺命令、失败命令、未配置缺限制的 RED 测试。
- [ ] 将 advisory 规则改成 conditional 规则并由 gate 执法。
- [ ] 运行 gate/contract 目标测试确认 GREEN。

### Task 3: Skill 生成物与安装副本同步

**Files:**
- Modify: `skills/ashare-data.md`
- Modify: `skills/full-company-analysis.md`
- Regenerate: `codex-skills/ashare-data/SKILL.md`
- Regenerate: `codex-skills/full-company-analysis/SKILL.md`
- Regenerate: related `codex-prompts/*.md`

**Interfaces:**
- Produces: canonical、仓库生成物与本机已安装副本内容一致

- [ ] 先运行同步检查记录旧 installed 副本不一致。
- [ ] 更新 canonical 指令并生成仓库适配物。
- [ ] 使用现有安装脚本同步本机副本，只验证内容哈希，不输出凭据。

### Task 4: Gate-owned 命令执行收据

**Files:**
- Modify: `tests/test_full_analysis_gate.py`
- Modify: `tools/full_analysis_gate.py`
- Modify: `skills/full-company-analysis.md`

**Interfaces:**
- Produces: `run-command` 以 argv 数组执行 allowlisted ashare 命令，保存 stdout/stderr 与 SHA-256，并写入 manifest；调用者提交同类 receipt 被拒绝

- [ ] 写伪 argv、非零退出、空来源和调用者自报 receipt 的 RED 测试。
- [ ] 实现无 shell 的受限执行与冻结收据。
- [ ] 运行命令收据目标测试确认 GREEN。

### Task 5: 共享事实与 artifact/command 血缘

**Files:**
- Modify: `tests/test_full_analysis_gate.py`
- Modify: `tests/test_full_analysis_phase2.py`
- Modify: `tools/full_analysis_gate.py`
- Modify: `tools/full_analysis_contract.json`
- Modify: `skills/full-company-analysis.md`

**Interfaces:**
- Produces: 每个声明产物的 `artifact_record`，包含稳定 artifact ID、输入 artifact ID、fact ID 与 command ID；advisory feeds 目标必须引用 ashare artifact/command

- [ ] 写悬空 ID、后向引用、缺 feeds 血缘和 ashare 无共享事实的 RED 测试。
- [ ] 增加封闭 schema、全局唯一性和拓扑/消费映射校验。
- [ ] 运行 gate 与 20 项参数化测试确认 GREEN。

### Task 6: 920 北交所识别

**Files:**
- Modify: `tests/test_ashare_plugin_core.py`
- Modify: `tools/ashare_plugin/identifiers.py`

**Interfaces:**
- Produces: 裸 `920xxx` 与显式 `.BJ` 统一得到 BJ、`0.<code>`、`bj<code>`

- [ ] 写裸 920 的 RED 测试。
- [ ] 将 920 判断置于沪市 9 开头规则之前。
- [ ] 运行 plugin/legacy 路由测试确认 GREEN。

### Task 7: 收口验证

**Files:**
- Verify only: all changed files

- [ ] 运行目标测试、生成物 `--check`、契约检查。
- [ ] 运行 `bash scripts/check.sh`。
- [ ] 审计最终 diff，确认用户已有改动未被覆盖且无外部写入。
