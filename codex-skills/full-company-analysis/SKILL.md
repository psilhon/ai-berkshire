---
name: full-company-analysis
description: WorkBuddy 专用单公司全量分析适配器；由 WorkBuddy 原生 Agent 执行真实研究，Runtime 只负责租约、预算与恢复，Gate 负责确定性验收。
platform: workbuddy
registry-schema: full-analysis-contract/v2
result-schema: result-schema/v1
owner: psilhon
category: 编排层
maturity: governed(Phase2-gated)
review-cadence: per-release
---

## Codex adapter note

This skill is generated from `skills/full-company-analysis.md` so Claude Code and Codex users share one canonical workflow.

- Treat `$ARGUMENTS` as the user's request in the current Codex thread.
- When the source mentions Claude-only surfaces such as Task, Agent, WebSearch, Bash, Read, or Write, use the closest Codex capability available in this session: subagents when available, web search when needed, shell commands for local tools, and normal file edits for workspace files.
- Use shared project tools from `tools/` in this repository. Prefer running commands from the repository root with paths like `python3 tools/financial_rigor.py ...`; if the current thread starts outside the repo, locate the actual checkout path first instead of assuming a fixed home-directory path.
- Before starting research, run the `date` command to confirm today's date; treat it as the baseline for "latest" data and state the data cutoff date in the report header. Never assume the current date from training data.
- Preserve the research quality rules from `AGENTS.md`: cross-check financial data, use exact arithmetic tools for valuation/math, and clearly label uncertainty and source gaps.

# WorkBuddy 全量公司分析适配器

这是生产入口，不是第二套业务编排器。它只服务“一家公司、一次完整运行”；`industry-funnel` 仍可在该公司上下文中执行其行业漏斗任务，不改变单公司边界。租约和预算的实现位于 `tools/full_analysis_runtime.py`，正式状态只由 `tools/full_analysis_gate.py` 写入。

## 启动

先执行本地 `date` 与 `uname -m`，把日期作为 `as_of` 基线。随后调用：

```text
python3 scripts/full_analysis.py start --company <公司名> --code <证券代码> --as-of <YYYY-MM-DD>
```

只从返回的 `run_root` 继续。注册表 `tools/full_analysis_contract.json` 是 13 项业务契约、阶段目录、角色、章节和适用性谓词的唯一机器真源；不要在本适配器中复制清单。

## Agent 调度纪律

循环调用 `python3 scripts/full_analysis.py next-work --run-root <run_root>`。每次返回 `LEASED` 后，必须直接使用 **WorkBuddy 原生 Agent** 完成该 work unit；不得由 Python、shell 或旧版 orchestrator 再创建 Agent。

启动原生 Agent 前调用：

```text
python3 scripts/full_analysis.py job-started \
  --run-root <run_root> --work-unit-id <work_unit_id> \
  --attempt-id <attempt_id> --lease-nonce <lease_nonce> \
  --agent-job-id <WorkBuddy 返回的 job id>
```

派发给 Agent 的指令必须包含注册表分配的精确正式产物路径，并要求其把中间文件放在：

```text
<run_root>/evidence/attempts/<skill_id>/<attempt_id>/
```

Agent 必须返回 Result Bundle v1（`schema_version=result-schema/v1`）和短收据。除 facts/sources/calculation requests 外，必须按当前 skill 的 `evidence_rules` 填写结构化 `judgments`、`command_receipts` 与 `capability_records`；缺少任何已注册规则所需账本时 Audit 必须失败。多角色单元的 `role_runs` 不接受 Agent 自证，由 Gate 根据实际 `role-<role>.md` 备忘录生成并绑定路径、字节数与 SHA-256。计算请求只能提交 operation/args，重放结果由共享 Audit Job 调用 `financial_rigor.py` 生成，Agent 不得自证。`NOT_APPLICABLE` 不是自报状态：必须提交 `not_applicable` 结构、带来源的判定事实和负向验收报告；`always`/`always_applicable` 单元不得 N/A。主上下文只接收 `attempt_id`、`result_path`、`status`、`bytes`、`sha256`；**不读取报告正文、不复制隐藏推理、不把长文本带回主上下文**。

**派发前必读 `next-work` 注入的规范**：每次 `next-work` 返回的 payload 内含 `methodology_text`（即 `skills/<skill_id>.md` 完整方法论）、`sections`（章节与最低字数要求）、`min_bytes`（本报告字节下限）与 `roles`/`fanout_required`。执行 Agent 必须以 `methodology_text` 为强制规范完整落地，**不得仅凭 skill 名称凭记忆发挥**；报告字节数必须 ≥ `min_bytes`，否则 Gate 拒收。

**多角色 skill 必须真扇出**：当 `fanout_required: true` 时，必须为 `roles.required_roles` 中每个角色（除 `integrator` 外）启动一个**独立原生 Agent**（用 Task 工具 fan-out），各自在 `evidence/attempts/<skill_id>/<attempt_id>/role-<role>.md` 产出独立分析备忘录（每个 ≥300 字节，且不得相互引用以保证独立性）；最后由整合 Agent 读取全部角色备忘录产出正式整合报告。缺少任一 `role-<role>.md` 时 Gate 会拒收。单 Agent skill 则由一个原生 Agent 按 methodology 完整执行。

完成后调用：

```text
python3 scripts/full_analysis.py submit-result \
  --run-root <run_root> --result <attempt_dir>/result.json
```

租约期间按需调用 `heartbeat`。Agent 失败调用 `record-failure`；429 只走 Runtime 的全局冷却与降并发，禁止手工绕过预算。达到 `stop_dispatch_at`（30 次）后停止非核心派生重试；达到 `hard_max`（33 次）立即停止新派发，生成 PARTIAL/SUMMARY，验收失败。预算参数以 Gate `budget_params` 为准，本处数字仅作人读说明。

## 执行一致性纪律（防质量坍塌，强制）

**每个 work unit 必须由独立原生 Agent 完成，主上下文只做调度，不得直接撰写分析正文。** 这是质量稳定的物理前提——独立 Agent 拥有新鲜上下文窗口、完整 `methodology_text`、专属外部调研任务；主上下文一旦亲自续写，深度必然坍塌（历史事故根因，勿回退）。

- **会话摘要压缩后严禁主上下文直写**：若本次会话经历过上下文摘要/压缩，后续所有未完成的分析单元**必须继续派发真子 Agent + 重新外部取数**（WebSearch、数据管线、`financial_rigor` 验算），严禁凭压缩摘要 + 参数记忆在主上下文里"顺手写完"。压缩只会丢失上下文，不能改变执行架构。
- **heartbeat 是真研究的指纹**：长任务 Agent 在租约期间必须周期性调用 `heartbeat`。一个分析 run 若全程零心跳却产出了全部单元，几乎等价于"主上下文直写"——doctor 会就此告警，必须人工复核 10 号后单元是否退化。
- **扇出单元不得在主上下文模拟**：`fanout_required` 单元的各角色必须用 Task 工具派独立 Agent，禁止单 Agent 串行"扮演"多角色后自称已扇出。

## 恢复与收口

WorkBuddy 重启后调用 `resume`。Runtime 会先检查活动租约目录中的孤儿 Result Bundle：Gate 验证通过则直接接管为 DONE；无结果或验证失败才标为 abandoned 并重新排队。已经被 Gate 接受的正式产物可复用。

所有 work unit 收口后执行：

```text
# 主上下文只读汇总正式产物，先写入：
# <run_root>/evidence/attempts/summary/summary.md
python3 scripts/full_analysis.py register-summary \
  --run-root <run_root> --summary <run_root>/evidence/attempts/summary/summary.md
python3 scripts/full_analysis.py audit --run-root <run_root>
python3 scripts/full_analysis.py review prepare --run-root <run_root>
# 为每个核心单元、全部 N/A 与 delivery-summary 的简报派独立评审 Agent，逐份 review ingest
python3 scripts/full_analysis.py review summarize --run-root <run_root>
python3 tools/full_analysis_gate.py finalize --run-root <run_root>
```

总结报告是正式交付，不是 Gate 外附件。它必须包含核心结论、投资/财报/行业三条主干、补充参考、产物索引、数据截止日和免责声明，只能综合已登记正式产物，不得引入新取数或新推理。Gate 会冻结其路径、字节数和 SHA-256；Review 会使用全部归因证据检查总结，修改总结或任一底层证据都会使旧 Audit/Review 过期。

Audit 不通过、总结缺失、Audit 快照过期、语义评审范围/五维度不完整、评审与当前报告或证据摘要不一致，或仍有 `PENDING/RUNNING/FAILED` 时均不得准出。任何 ingest、总结重登记或返工都会使旧 Audit 与 Review 失效，必须重新执行。最终报告只能引用 Audit-PASS 的事实、来源和已重放计算；任何降级必须使用注册表允许的 PWL 原因并显式写入限制。

finalize 之后（或随时）执行质量体检：

```text
python3 scripts/full_analysis.py doctor --run-root <run_root>
```

doctor 是**advisory 非阻断**诊断（不影响 APPROVE/FAIL），专门捕捉"过了 Gate 下限但仍可能坍塌"的执行退化指纹：①全部/大量分析单元贴线（字节仅略超 `min_bytes`，深度存疑）②零 heartbeat（疑似主上下文直写）③深度分化不足。**过下限 ≠ 同等深度**，下限只是地板。若 doctor 返回 WARN：必须人工复核被点名的贴线单元与 10 号后单元，确认是真深度不足还是合法的快 run；确属坍塌的，按"返工路径"（重置目标单元为 PENDING + manifest 置 PARTIAL + 记录 `rework_initiated` 事件）重新派真子 Agent 返工，再重跑 audit+finalize。

重复运行 Benchmark 只比较同一公司、同一 `as_of`、同一 Contract digest 且全部 `APPROVED` 的 run；任一事实、计算或判断在某个 run 缺失都算不稳定，不得以空集合冒充 100% 一致。

所有产出仅供学习研究，不构成投资建议。
