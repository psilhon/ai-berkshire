# Skills 使用指南

本仓库当前包含 13 个投研业务 Skill 和 1 个编排 Skill。`skills/*.md` 是 workflow 权威源；`codex-skills/*/SKILL.md` 与 WorkBuddy 全量分析适配器由 `python3 scripts/sync-codex-skills.py` 生成并通过 `--check` 校验。

> 数据截止：2026-07-25。具体章节、证据和适用性要求以各 Skill 源文件及 `tools/full_analysis_contract.json` 为准。

## 通用前提

- 研究开始前运行 `date`，并在报告头标注数据截止日。
- 关键财务数据至少使用两个独立来源；差异必须解释。
- 估值和精确计算使用 `python3 tools/financial_rigor.py`。
- 报告交付前运行 `python3 tools/report_audit.py`；全量分析还必须通过 Gate、共享 Audit 和独立语义 Review。
- 数据不足时明确记录限制，不用猜测补齐。
- 修改 Skill 后运行 `python3 scripts/sync-codex-skills.py`，完成前运行 `bash scripts/check.sh`。

## 业务 Skill

| 类别 | Skill | 适用场景 | 主要特点 |
|---|---|---|---|
| 数据 | `ashare-data` | A 股行情、财务、公告和市场信号取数 | 统一数据入口，记录命令收据、来源和时间 |
| 数据 | `financial-data` | 财务数据获取与交叉验证 | 双源核对、口径差异和冲突处理 |
| 快筛 | `quality-screen` | 个股、行业或指数成分去劣 | 七条硬指标，先排除明显不合格标的 |
| 快筛 | `investment-checklist` | 买入前快速检查 | 能力圈、生意、护城河、管理层、安全边际和风险 |
| 公司 | `investment-research` | 单公司系统研究 | 四大师综合框架、反证与情景估值 |
| 公司 | `investment-team` | 高重要性公司的多视角研究 | 四个独立角色先研究，再由整合角色仲裁分歧 |
| 公司 | `management-deep-dive` | 管理层是核心变量时 | 诚信、执行、资本配置和治理纵深分析 |
| 财报 | `earnings-review` | 最新财报或指定期间精读 | 四大师独立解读后整合，优先使用一手披露 |
| 行业 | `industry-research` | 产业链全景和竞争格局 | 按环节扫描驱动力、风险、估值与机会 |
| 行业 | `industry-funnel` | 从全市场筛到少量候选 | 分层筛选并保留淘汰理由 |
| 行业 | `bottleneck-hunter` | 寻找产业链物理瓶颈 | 判断瓶颈是否真实、可持续和可投资 |
| 风险 | `news-pulse` | 股价异动快速归因 | 公司、监管、行业和情绪四路侦察 |
| 论文 | `thesis-tracker` | 买入后持续跟踪 | 记录证伪条件、触发器和论文健康度 |

## 选择建议

- 不确定公司是否值得深挖：先用 `quality-screen`，再用 `investment-checklist`。
- 单公司常规深研：用 `investment-research`；关键决策需要独立视角交锋时用 `investment-team`。
- 财报发布后：用 `earnings-review`；管理层判断仍是主要分歧时补 `management-deep-dive`。
- 从行业找公司：先 `industry-research` 看结构，再用 `industry-funnel` 收敛候选；物理供给约束明显时补 `bottleneck-hunter`。
- 股价突然异动：先 `news-pulse` 判断事件性质，再决定是否重跑公司研究或更新 `thesis-tracker`。
- 需要完整单公司闭环：使用 `full-company-analysis`，不要手工串联后自行宣称“全量完成”。

## 编排 Skill：full-company-analysis

`full-company-analysis` 是 WorkBuddy 生产入口，不是额外的业务分析方法。它按 Contract 调度 13 个业务单元，并强制执行以下闭环：

1. `start` 创建运行目录、预算、授权信封和 13 个 work unit。
2. `next-work` 租约注入完整方法论；独立 Agent 写入 attempt 目录。
3. `submit-result` 绑定租约并由 Gate 晋级正式产物。N/A 必须提交可验证谓词事实、来源和负向验收报告；始终适用的单元不得 N/A。
4. 全部业务单元终态后，主上下文只读综合正式产物，写入 `evidence/attempts/summary/summary.md` 并执行 `register-summary`。
5. 运行共享 Audit；随后 `review prepare` 为核心单元和 `delivery-summary` 生成独立评审简报，逐份 `review ingest` 后 `review summarize`。
6. `finalize` 只在正式产物、总结报告、Audit 快照和语义 Review 全部有效时写入 `APPROVED`。
7. `doctor` 提供非阻断退化诊断；重复运行用 `benchmark` 比较稳定性。

```bash
python3 scripts/full_analysis.py start \
  --company 格力电器 --code 000651.SZ --as-of 2026-07-25

python3 scripts/full_analysis.py register-summary \
  --run-root <run_root> \
  --summary <run_root>/evidence/attempts/summary/summary.md

python3 scripts/full_analysis.py audit --run-root <run_root>
python3 scripts/full_analysis.py review prepare --run-root <run_root>
python3 scripts/full_analysis.py review summarize --run-root <run_root>
python3 tools/full_analysis_gate.py finalize --run-root <run_root>
```

Runtime 的硬预算是 33 个 Agent job，30 次后停止非核心派生重试；并发上限为 4。共享状态使用跨进程锁，`resume` 会先验证孤儿结果再决定接管或重排。

全量运行的启动请求只授权只读外部研究、`run_root` 内写入和研究结论。它不授权 push、PR、发布、发送、外部系统写入、越界写入或敏感数据访问。

所有产出仅供学习研究，不构成投资建议。
