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
# 步骤 A：编排器派 deep-summary Agent（独立原生 Agent），
# 读取 13 份正式产物，按总结报告章节结构忠实熔炼，写入：
# <run_root>/evidence/attempts/summary/summary.md
# （主上下文不得代写；Agent 须遵循下方「总结产出纪律」）

# 步骤 B：登记并冻结总结（Gate 校验章节/字节/产物索引覆盖）
python3 scripts/full_analysis.py register-summary \
  --run-root <run_root> --summary <run_root>/evidence/attempts/summary/summary.md

# 步骤 C：审计 + 语义评审 + 准出
python3 scripts/full_analysis.py audit --run-root <run_root>
python3 scripts/full_analysis.py review prepare --run-root <run_root>
# 为 scope 内每个 skill 派独立评审 Agent（见语义评审纪律）
python3 scripts/full_analysis.py review summarize --run-root <run_root>
python3 tools/full_analysis_gate.py finalize --run-root <run_root>

# 步骤 D：finalize APPROVED 后，编排器派 html-express Agent（独立原生 Agent），
# 将已冻结的 <公司名>-全量分析-总结报告.md 渲染为 HTML 展示件
```

> **为什么 register-summary 必须在 audit 之前**：`analysis_snapshot` 的投影包含 `manifest.delivery`，register-summary 写入 `delivery.summary` 会改变快照摘要。若 audit 先于 register-summary 执行，finalize 的快照一致性校验将因摘要不匹配而拒绝准出。

### 总结产出纪律（步骤 A，deep-summary Agent 强制）

编排器派发 deep-summary Agent 时，指令必须包含：

- **输入**：13 份正式产物的绝对路径（从 manifest 的 `skills[].artifact_records[].path` 取得）+ `as_of` 日期 + 公司名/代码。
- **方法论**：遵循 deep-summary skill 的忠实熔炼纪律（`references/distillation-guide.md`）——只读、只提炼，不 WebSearch、不取新数、不做新推理。
- **输出格式**：必须包含 `register-summary` 要求的 8 个必需章节（核心结论速览 / 主干①·投资分析 / 主干②·财报研读 / 主干③·行业分析 / 补充与参考 / 产物索引 / 数据截止日 / 仅供学习研究），字节 ≥ 2500。
- **产物索引**：必须逐条列出 13 份正式产物的完整相对路径，缺一即被 `register-summary` 拒收。
- **写入路径**：`<run_root>/evidence/attempts/summary/summary.md`。
- **禁止**：不得引入新数据/新推理/新结论；不得调用 `register-summary`/`audit`/`finalize`（这些由编排器执行）；不得触碰 `evidence/` 下其他文件。

总结报告是正式交付，不是 Gate 外附件。只能综合已登记正式产物，不得引入新取数或新推理。Gate 会冻结其路径、字节数和 SHA-256；Review 会使用全部归因证据检查总结，修改总结或任一底层证据都会使旧 Audit/Review 过期。

### HTML 版总结报告（步骤 D，html-express Agent 派生展示件）

finalize **APPROVED 之后**，编排器派独立原生 Agent，用 `/html-express` 把已冻结的 markdown 总结渲染为自包含 HTML（`<公司名>-全量分析-总结报告.html`，放在 run root），作为可读性更强的展示件。纪律：

- **它是 markdown 的派生展示件，不是 Gate 产物**：不参与 audit/review/finalize，不进入 manifest，不改变任何 digest。Gate 的准出真源永远是 markdown。
- **只在 APPROVED 后生成**：markdown 已被 Gate 冻结，HTML 才有稳定真源；finalize 前生成会因后续评审返工而失效。
- **100% 忠实于 APPROVED markdown**：不得引入新数据、新推理或新结论；每个数字必须能在 markdown 中找到对应。
- **遵循 html-express 设计系统**：自包含单文件、内联 tokens.css（cream paper / terracotta / trust 墨蓝 / serif）、组件化（metric-card / data-table / comparison-table / timeline / badge / quote-card / details / columns）；可加 masthead、节导航、滚动显现等微交互。
- **生成后必查**：① 无占位符残留（`填这里/Lorem/TBD/TODO/placeholder/待填`）② 关键数字全部在位（营收/归母/ROE/PE/估值区间等逐一 grep）③ 标签全闭合（`<section>/<table>/<div>` 开闭计数相等）④ 标题与锚点完整。
- **markdown 一旦被编辑并重新 finalize，HTML 必须重新生成**：旧 HTML 立即作废，不得与新 APPROVED 版本并存误导。

## 语义评审纪律（P2，强制）

确定性 Gate 回答“产物是否存在、是否达标、是否可追溯”（结构层）；**语义评审回答“结论是否真的被证据支持、冲突是否解决、反面证据是否被处理”（语义层）**，是 Gate 与 doctor 之间的中间层。它能抓到结构层抓不到的跨产物语义吞漏（如总结只呈现主基准估值、吞掉保守基准悲观情景；跨单元数字误植）。**完整 REVIEW_PASSED 是 finalize 准出的必要条件**——8 个 scope skill 全部有有效评审结果、无 REVIEW_REQUIRED、无 high finding、无 stale/invalid。

默认评审范围（`required_review_scope`）：8 个高判断密度核心单元 `investment-research / investment-team / investment-checklist / management-deep-dive / earnings-review / industry-research / thesis-tracker / delivery-summary`，外加全部 `NOT_APPLICABLE` 单元。

**review-result schema（`semantic-review/v1`）**——评审子 Agent 每个 skill 产出一份 JSON，ingest 时按此严格校验，任一字段不合规即拒收：

- `review_schema_version` 必须恰为 `"semantic-review/v1"`；
- `skill_id` / `run_id` / `brief_digest` / `report_digest` / `evidence_digest` 均为非空字符串，且必须与对应 `review-brief-<skill>.json` 中的值**逐字一致**（digest 绑定，见下）；
- `verdict` ∈ {`PASS`, `REVIEW_REQUIRED`}；存在任一 high finding 时 verdict 不得为 PASS；
- `dimensions` 必须**恰好覆盖五个维度且不得重复**，每维 `verdict` ∈ {`PASS`, `FINDING`}：
  1. `evidence_support`（核心结论是否由归因证据支持，有无过度推断）
  2. `unresolved_conflicts`（双源事实分歧是否解释，是否形式挂双源）
  3. `counter_evidence`（看空/分歧证据是否被同等力度处理，有无稻草人）
  4. `valuation_consistency`（估值假设与事实是否内部自洽，情景是否全偏乐观）
  5. `limitations_completeness`（限制项是否完整，有无未披露重大不确定性）
- `findings` 每条含 `dimension` / `severity`∈{high,medium,low} / `description` / `evidence_refs`（非空数组）/ `remediation`；dimensions 中标 FINDING 的维度集合必须与 findings 覆盖的维度集合**完全一致**。

最小合规模板：

```json
{
  "review_schema_version": "semantic-review/v1",
  "skill_id": "investment-research",
  "run_id": "<逐字复制简报>",
  "brief_digest": "<逐字复制简报>",
  "report_digest": "<逐字复制简报 report.sha256>",
  "evidence_digest": "<逐字复制简报 evidence.sha256>",
  "verdict": "PASS",
  "dimensions": [
    {"dimension": "evidence_support", "verdict": "PASS"},
    {"dimension": "unresolved_conflicts", "verdict": "PASS"},
    {"dimension": "counter_evidence", "verdict": "PASS"},
    {"dimension": "valuation_consistency", "verdict": "PASS"},
    {"dimension": "limitations_completeness", "verdict": "PASS"}
  ],
  "findings": []
}
```

**评审子 Agent 派发纪律**：`review prepare` 后，为 scope 内每个 skill 派一个**独立原生 Agent**（只读各自的 `evidence/review/review-brief-<skill>.json`），要求其逐维检查并产出 review-result。已知机械性 bug（务必规避）：

- **禁止编造 evidence_refs**：finding 的证据引用必须真实存在于该 skill 的归因 facts/sources/calculations 或报告正文，不得凭印象虚构；
- **digest 逐字复制**：`run_id`/`brief_digest`/`report_digest`/`evidence_digest` 从简报原样复制，不得重新计算或改写，否则 ingest 因摘要不匹配拒收；
- **JSON 内引号转义**：description/remediation 字符串内避免未转义 ASCII 双引号，改用中文引号「」或转义，否则 JSON 解析失败；
- **键名严格对齐 schema**：维度项用 `dimension`/`verdict`，不得写成 `name`/`result` 之类别名；
- 每份评审结果写入 `evidence/review/review-result-<skill>.json`（ingest 落盘路径），随后逐个执行 `review ingest --run-root <run_root> --review <该路径>`。

**digest 绑定与过期机制**：ingest 与 aggregate 都精确比对三个 digest。`report_digest`=报告正文 SHA-256，`evidence_digest`=归因证据的规范化 SHA-256。因此：**编辑总结报告会改变 `report_digest`，使 delivery-summary 的评审结果过期**（但 manifest.facts 未变，故其余 7 个 skill 的 evidence_digest 与评审结果仍有效）；**编辑任一底层证据会改变相关 skill 的 evidence_digest**。`register-summary` 改 manifest 又会使 audit 快照过期。任何 ingest、总结重登记或返工都使旧 Audit 与 Review 失效，必须按链重跑。

**编辑总结后的返工链（强制顺序，缺一即被 finalize 拒出）**：

```text
# 1) 派 deep-summary Agent 重写 <run_root>/evidence/attempts/summary/summary.md（修 finding 或扩写深度）
# 2) 重登记总结（改 manifest，使 audit 快照过期）
python3 scripts/full_analysis.py register-summary --run-root <run_root> --summary <绝对路径>/summary.md
# 3) 重跑 audit 刷新快照（finalize 校验 audit 快照与当前 manifest 一致，manifest 一变必须重跑）
python3 scripts/full_analysis.py audit --run-root <run_root>
# 4) 删除过期的 delivery-summary 评审结果（report_digest 已变）
rm <run_root>/evidence/review/review-result-delivery-summary.json
# 5) 重生成简报（刷新 delivery-summary 的 digest 绑定）
python3 scripts/full_analysis.py review prepare --run-root <run_root>
# 6) 派独立评审 Agent 复审 delivery-summary，写 review-result 后 ingest
python3 scripts/full_analysis.py review ingest --run-root <run_root> --review <run_root>/evidence/review/review-result-delivery-summary.json
# 7) 重新聚合至 REVIEW_PASSED
python3 scripts/full_analysis.py review summarize --run-root <run_root>
# 8) 准出
python3 tools/full_analysis_gate.py finalize --run-root <run_root>
```

返工时**不得以 override 评审代替修正**：语义评审的价值就在于抓真实缺陷，凡 high/medium finding 必须先修正总结源文件或底层证据，再重走全链路、由独立评审确认修复。`register-summary` 与 `review ingest` 均须传**绝对路径**，相对路径会双重拼接报错。返工链跑完、重新 finalize APPROVED 后，若 run root 已存在 HTML 展示件，必须按「HTML 版总结报告」纪律重新生成，使 HTML 与最新 APPROVED markdown 一致。

Audit 不通过、总结缺失、Audit 快照过期、语义评审范围/五维度不完整、评审与当前报告或证据摘要不一致，或仍有 `PENDING/RUNNING/FAILED` 时均不得准出。最终报告只能引用 Audit-PASS 的事实、来源和已重放计算；任何降级必须使用注册表允许的 PWL 原因并显式写入限制。

finalize 之后（或随时）执行质量体检：

```text
python3 scripts/full_analysis.py doctor --run-root <run_root>
```

doctor 是**advisory 非阻断**诊断（不影响 APPROVE/FAIL），专门捕捉"过了 Gate 下限但仍可能坍塌"的执行退化指纹：①全部/大量分析单元贴线（字节仅略超 `min_bytes`，深度存疑）②零 heartbeat（疑似主上下文直写）③深度分化不足。**过下限 ≠ 同等深度**，下限只是地板。若 doctor 返回 WARN：必须人工复核被点名的贴线单元与 10 号后单元，确认是真深度不足还是合法的快 run；确属坍塌的，按"返工路径"（重置目标单元为 PENDING + manifest 置 PARTIAL + 记录 `rework_initiated` 事件）重新派真子 Agent 返工，再重跑 audit+finalize。

重复运行 Benchmark 只比较同一公司、同一 `as_of`、同一 Contract digest 且全部 `APPROVED` 的 run；任一事实、计算或判断在某个 run 缺失都算不稳定，不得以空集合冒充 100% 一致。

所有产出仅供学习研究，不构成投资建议。
