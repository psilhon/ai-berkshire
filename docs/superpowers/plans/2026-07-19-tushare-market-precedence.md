# Tushare 市场数据冲突优先级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对可比市场结构化字段的 Tushare 冲突值建立可审计的有效值选择机制。

**Architecture:** 验证层计算字段级有效值元数据；CLI 仅消费行情和估值字段的有效值并输出覆盖说明。财务、公告和信号路径继续只报告冲突，不修改主数据。

**Tech Stack:** Python 3 标准库、unittest、既有 skills 生成脚本。

## Global Constraints

- 不读取、输出或持久化 `TUSHARE_TOKEN`。
- 仅 `CONFLICT` 且验证来源为 `tushare.*` 的市场字段可覆盖。
- 期间/单位不一致与非市场字段必须保持主数据有效值。
- 本次不执行 Git 提交、推送或发布。

---

### Task 1: 字段级有效值契约

**Files:**
- Modify: `tests/test_ashare_plugin_tushare_verification.py`
- Modify: `tools/ashare_plugin/tushare_verification.py`

- [ ] 写入市场冲突字段采用 Tushare、财务冲突字段保持主来源、期间不一致不覆盖的失败测试。
- [ ] 运行 `python3 -m unittest tests.test_ashare_plugin_tushare_verification -q`，确认新测试在缺少有效值元数据时失败。
- [ ] 最小实现字段分类与 `effective_value`、`effective_source`、`precedence_applied` 元数据。
- [ ] 重跑同一测试，确认通过。

### Task 2: CLI 可见覆盖

**Files:**
- Modify: `tests/test_ashare_data.py`
- Modify: `tools/ashare_data.py`

- [ ] 写入 `valuation` 冲突时输出采用 Tushare 值和原值的失败测试。
- [ ] 运行 `python3 -m unittest tests.test_ashare_data.TestTushareCliVerification -q`，确认失败。
- [ ] 在行情/估值打印前应用有效市场字段，并逐项打印覆盖说明。
- [ ] 重跑同一测试，确认通过。

### Task 3: Gate 与双端规范

**Files:**
- Modify: `tests/test_full_analysis_gate.py`
- Modify: `tools/full_analysis_gate.py`（仅在现有事实分类不能识别有效来源时）
- Modify: `skills/ashare-data.md`
- Modify: `skills/financial-data.md`
- Modify: `skills/full-company-analysis.md`
- Regenerate: `codex-skills/{ashare-data,financial-data,full-company-analysis}/SKILL.md`
- Regenerate: `codex-prompts/{ashare-data,financial-data,full-company-analysis}.md`

- [ ] 写入 gate 对 Tushare 市场有效来源的分类测试。
- [ ] 运行对应测试，确认失败或证明既有分类已满足契约。
- [ ] 更新最小 gate 逻辑（如需要）与三份 canonical skill 规则；同步 Codex 生成物。
- [ ] 运行定向测试、`python3 scripts/sync-codex-skills.py --check`、`python3 scripts/sync-codex-prompts.py --check` 和 `python3 scripts/check-full-analysis-contract.py`。
