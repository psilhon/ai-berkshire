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

只从返回的 `run_root` 继续。注册表 `tools/full_analysis_contract.json` 是 20 项业务契约、阶段目录、角色、章节和适用性谓词的唯一机器真源；不要在本适配器中复制清单。

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

Agent 必须返回 Result Bundle v1（`schema_version=result-schema/v1`）和短收据。主上下文只接收 `attempt_id`、`result_path`、`status`、`bytes`、`sha256`；**不读取报告正文、不复制隐藏推理、不把长文本带回主上下文**。完成后调用：

```text
python3 scripts/full_analysis.py submit-result \
  --run-root <run_root> --result <attempt_dir>/result.json
```

租约期间按需调用 `heartbeat`。Agent 失败调用 `record-failure`；429 只走 Runtime 的全局冷却与降并发，禁止手工绕过预算。达到 45 次后停止非核心派生重试；达到 50 次立即停止新派发，生成 PARTIAL/SUMMARY，验收失败。

## 恢复与收口

WorkBuddy 重启后调用 `resume`。Runtime 会把旧的 `LEASED/RUNNING` 尝试标为 abandoned 并为未完成 work unit 重新排队；已经被 Gate 接受的正式产物可复用。

所有 work unit 收口后执行：

```text
python3 scripts/full_analysis.py audit --run-root <run_root>
python3 tools/full_analysis_gate.py finalize --run-root <run_root>
```

Audit 不通过或仍有 `PENDING/RUNNING/FAILED` 时不得生成准出报告。最终报告只能引用 Audit-PASS 的事实、来源和计算；任何降级必须使用注册表允许的 PWL 原因并显式写入限制。

所有产出仅供学习研究，不构成投资建议。
