# Changelog

本仓库遵循 [语义化版本](https://semver.org/lang/zh-CN/)（SemVer）。
所有发版记录以 git tag 为准，本文件为人工维护的变更摘要。

---

## [v3.3.0] — 2026-07-26

> 全量分析质量闭环成型 + 契约精简至 13 项 + 防凑数校准
> 累计 7 个提交（v3.2.0..v3.3.0）：把全量分析从"硬字节闸门"升级为
> 「硬地板 + 实质校验 + 软诊断」三层质量防护，契约由 20 项精简至 13 项，
> 收口序列重构为编排器统一编排的 A→B→C→D 四步，并对 Agent 隐藏字节目标以根治凑数。

### ✨ 新增 (Added)
- **doctor 执行完整性体检**（`tools/full_analysis_doctor.py`）：advisory 软诊断层，三类坍塌指纹——大量分析单元贴线（字节 <1.15× 下限）、零 heartbeat（疑似主上下文直写）、深度分化不足（标准化 margin 变异系数 <0.25）。接入 finalize 末尾，写 `evidence/doctor-report.json`，异常静默吞掉绝不影响 APPROVE/FAIL。
- **review 评审管线**（`tools/full_analysis_review.py`）：`prepare`（为核心 skill 生成评审简报）→ `ingest`（接收评审结果）→ `summarize`（聚合）。
- **snapshot 确定性快照**（`tools/full_analysis_snapshot.py`）：Audit 与 Gate 共享的输入快照，投影含 facts/sources/calculations/judgments/receipts/role_runs/capabilities 与 `manifest.delivery`，供 finalize 快照一致性复核。
- **benchmark 基准工具**（`tools/full_analysis_benchmark.py`）。
- **Agent 协作文档体系**：`docs/agents/` 新增 issue-tracker（GitHub Issues + gh CLI）、triage-labels（五标签分诊）、domain 三篇；CLAUDE.md 增「Agent skills」章节。

### 🔁 变更 (Changed)
- **契约精简 20 → 13 项**：去掉内容/组合类 skill（deep-company-series / earnings-team / portfolio-review / private-company-research / thesis-drift / wechat-article / dyp-ask），合并财报精读为单一四大师扇出 `earnings-review`。现 13 业务 skill：ashare-data / financial-data / quality-screen / investment-checklist / investment-research / investment-team / management-deep-dive / earnings-review / industry-research / industry-funnel / bottleneck-hunter / news-pulse / thesis-tracker。
- **收口序列重构为 A→B→C→D**：13 units DONE 后编排器统一编排——A) 派 deep-summary Agent 熔炼 13 份正式产物写 summary；B) register-summary 登记冻结（须在 audit 前：snapshot 投影含 delivery）；C) audit → review prepare/summarize → finalize；D) APPROVED 后派 html-express Agent 生成总结 HTML 派生展示件。
- **防凑数校准**：`min_bytes` 明确定位为防坍塌地板（挡 403 字节式空报告）而非写作目标；`next_work` payload 不再向执行 Agent 暴露 `min_bytes` 具体数字（改 None + `length_policy` 语义字段），`ANTI_PADDING_DIRECTIVE` 增「关于长度·必读」；实质小节判定门槛由魔法数 80 提为命名常量 `SUBSTANTIVE_MIN_CHARS=150`（低于此视为一句话占位不计实质）。

### 🔒 可靠性 (Reliability, P0)
- **ingest 事务性**：重构为「只读校验阶段 → 事务化晋级阶段」，被拒 attempt 绝不触碰正式文件（旧版在校验前复制，被拒 attempt 会留旧哈希覆盖正式文件）。
- **finalize 哈希复核**：正式产物实际 bytes/sha256 与 manifest 不一致即 PARTIAL 拒出，捕获「manifest 合格但正式文件被污染」。
- **doctor 心跳按 accepted attempt 关联**：旧失败 attempt 心跳不再替零心跳 accepted attempt 背书；accepted attempt 无记录时回退 skill 级。
- **partial 日志告警**：`events_status="partial"` 产生 WARN，不再静默 PASS。

### 🧪 测试 (Tests)
- 新增 `test_full_analysis_doctor.py` / `test_full_analysis_review.py` / `test_full_analysis_benchmark.py` / `test_full_analysis_docs.py`；gate_v2 增 P0 事务性与哈希复核用例（被拒 attempt 不覆盖正式文件、finalize 拒篡改产物）。
- `bash scripts/check.sh` 全绿（437 单元测试 + frontmatter 治理 + Codex 同步 + 13 项契约校验 + 报告索引）。

### ⚠️ 升级注意
- 契约为破坏性精简：旧 20 项契约 run 的 manifest 与本版不兼容，请以 13 项契约重新 init。
- `next_work` payload 的 `min_bytes` 现为 None，依赖该字段读具体数值的下游脚本需改读 `length_policy` 或 contract 本体。

---

## [v3.2.0] — 2026-07-24

> 质量闸门实质化 + 并发上限 2→4
> 累计 15 个提交（v3.1.0..v3.2.0）：完整落地全量分析 Runtime / Gate v2 / Audit 管线，
> 并将质量闸门从"纯字节门槛"升级为"实质校验"，并发上限由 2 提升至 4。

### ✨ 新增 (Added)
- **全量分析运行时（Runtime）**：租约（20 分钟 + 心跳续租）、预算闸门（正常 40 / 停派 45 / 硬上限 50）、429 冷却与降并发、迟到结果拒收。
- **Gate v2 运行根与产物晋级管线**：artifact 实际字节数 / hash / 路径约束确定性校验。
- **事实来源与计算 Audit**（`tools/report_audit.py`）：`duplicate_source_id` / `fact_without_source` / `calculation_not_replayed` 检测。
- **Result Bundle v1 schema 冻结** + **Contract v2 注册表**（20 项契约，含分级 `min_bytes` 与 `evidence_rules`）。
- **WorkBuddy 原生 Agent 适配器**（薄层 CLI → runtime → gate → audit），切换自旧编排。
- **编排 skill 治理元数据**（owner / category / maturity）。

### 🔁 变更 (Changed)
- **质量闸门从"纯字节门槛"升级为"实质校验"**：分歧 / 反面检验标记数、扇出类具名分歧、标题占比上限（防骨架注水）、防坍塌字节软下限。
- **runtime.next_work 派发 payload 注入 `methodology_text` 与扇出要求**，杜绝执行 Agent 退化为单遍写大纲。
- **并发上限由 2 提升至 4**（`tools/full_analysis_gate.py` 初始状态 `concurrency.max`）。
- 契约 `min_bytes` 由"深度目标"降级为"防坍塌软下限"，新增 `skill_type` / `min_dissent_points` / `min_substantive_sections`。
- `result_schema` 新增结构化实质字段 `key_claims` / `calculations` / `dissent_points` / `scenarios` / `reverse_tests`。
- 21 个 skill 质量强化与 Codex 包同步。

### 🐛 修复 (Fixed)
- **`_merge_provenance` 来源去重硬化**：sources 按 `source_id` 去重并丢弃 null 占位；facts 按 `fact_id` 去重、无来源的管线事实自动挂接规范源 `src.ashare_pipeline`；calculations 去重丢弃 null 占位（根治 audit FAIL，持久修复未来所有 run）。
- 适配器元数据同步与契约映射收紧。

### 📚 文档 (Docs)
- 无人值守可靠性设计 / 实施计划 / 方案收敛文档。

### 🧪 测试 (Tests)
- v2 测试入口更新 + 单公司 canary；脱敏实跑事故固化。
- `bash scripts/check.sh` 全绿（321 单元测试 + frontmatter + Codex 同步 + 契约校验 + 报告索引）。

---

## [v3.1.0] — 2026-07-22

> 层驱动编排重构 + 封闭证据 schema + 21 skill 质量强化

（详见 git history：`git log v3.0.0..v3.1.0`）

---

## [v3.0.0] — 2026-07-21

> 全量分析编排基座与批量脚本补齐

---

## [v2.0.0] / [v2.0.1] / [v1.0.x]

早期版本，历史提交见 `git tag --list`。

[v3.2.0]: https://github.com/psilhon/ai-berkshire/releases/tag/v3.2.0
[v3.1.0]: https://github.com/psilhon/ai-berkshire/releases/tag/v3.1.0
[v3.0.0]: https://github.com/psilhon/ai-berkshire/releases/tag/v3.0.0
