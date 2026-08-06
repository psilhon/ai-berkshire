# CONTEXT.md — AI Berkshire 领域文档

> 本文件是 berkshire 仓库的**领域词汇表（ubiquitous language）与约定**入口，供 Agent skills（见 `docs/agents/`）与日常投研任务引用。
> 由 `/domain-modeling` 按需扩展；当前为骨架版，术语随项目演进补充。

## 领域概览

本仓库是**同时兼容 Claude Code 与 Codex 的价值投资研究 Skill 合集**（AI Berkshire Codex）。
核心是把巴菲特 / 芒格 / 段永平 / 李录的投研方法论，沉淀为可复用的研究 Skill 与金融严谨校验工具。

## 词汇表（glossary）

| 术语 | 含义 | 出处 / 约束 |
|---|---|---|
| 价值投资研究 | 以企业内在价值为核心的投研工作流 | 项目定位 |
| 四大师框架 | 巴菲特 / 芒格 / 段永平 / 李录 的分析视角合集 | `CLAUDE.md` 项目概述 |
| 全量公司分析（full-company-analysis-workbuddy） | 单一公司端到端投研编排 + 验收体系（WorkBuddy 编排器） | `tools/full_analysis_contract.json` |
| 双系统 Skill 管线 | `skills/*.md` 为权威源，Codex 侧由脚本生成 | `AGENTS.md` / `CLAUDE.md` |
| 金融严谨（financial rigor） | Decimal 精确计算、多源交叉验证、报告审计 | `tools/financial_rigor.py` / `tools/report_audit.py` |
| ADR | 架构决策记录，存于 `docs/adr/` | `docs/agents/domain.md` |

## 约定

- **数据截止日**：每次研究前先 `date` 确认今日，作为"最新"数据基线，并在报告头标注数据截止日。
- **多源验证**：要求核验的财务数据须来自至少两个独立信源。
- **精确算术**：市值 / 估值 / 交叉校验 / 情景分析用 `python3 tools/financial_rigor.py`（Decimal，无浮点漂移）。
- **报告审计**：发布前 `python3 tools/report_audit.py` 抽检（准出 / 证据不足 / 打回三态）。
- **低置信标注**：低置信结论、不完整数据、信源缺口须明确标注。
- **隐私边界**：`local/` 仅本地、不入库（公开仓）。

## 文件结构

```
/
├── CONTEXT.md            ← 本文件（领域词汇表）
├── docs/adr/             ← 架构决策记录（见 README）
├── skills/*.md           ← Claude Code slash command 权威源
├── codex-skills/*/       ← 生成的 Codex skill 包
├── tools/*.py            ← 金融校验 / 数据工具
└── local/reports/        ← 研究产出（本地、不公开）
```

## 使用词汇表

当你的输出命名一个领域概念（issue 标题、重构提案、假设、测试名）时，使用本词汇表定义的术语，不要漂移到词汇表明确回避的同义词。
