---
name: full-company-analysis
description: WorkBuddy 专用单公司全量分析适配器。对一家公司执行 13 业务 skill 端到端全量研究（波次调度→审计→语义评审→finalize）。触发词：全量分析 <公司名>、/full-company-analysis <公司名>、全量跑 <公司名>。由 WorkBuddy 原生 Agent 执行真实研究，Runtime 只负责租约、预算与恢复，Gate 负责确定性验收。
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

**输入**：公司名 + 证券代码 + 日期。**输出**：`run_root`（后续所有命令的 `--run-root` 参数）。

1. **定基线**：执行本地 `date` 与 `uname -m`，把日期作为 `as_of` 基线（不得用训练记忆假设日期）。
2. **版本校验（E1，强制）**：执行仓库状态核验，防止「基于过期编排启动的 run」被继续推进（历史事故根因）。**机器门禁（v3.4.4 起）**：`start` 命令内置 `_git_stale_check()`——HEAD 落后于最新发版 tag 时**拒绝启动**（`E1 版本门禁`），须先 `git checkout <最新tag>` 或显式 `--allow-stale` 覆盖。文档自查命令（与门禁互补，查未提交改动）：

```bash
cd <仓库根> && git status --short tools/full_analysis_contract.json scripts/full_analysis.py tools/full_analysis_gate.py tools/full_analysis_runtime.py skills/full-company-analysis.md && git log -1 --oneline && git tag --list "v*" | sort -V | tail -1
```

   - **预期 tag 来源（动态，v3.4.4 修正）**：预期版本 = `git tag --list "v*" | sort -V | tail -1`（最新发版 tag），**不硬编码具体版本号**——文档出现具体 tag 示例即视为过期。对比 `git log -1` 的 HEAD 与最新 tag：HEAD 落后（非最新 tag 祖先）即 checkout 过期，机器门禁拒绝；`--allow-stale` 仅用于人工确认目标版本无误的场景。
   - 若上述编排相关文件（**含 skill 文档本身**）存在**未提交改动**：🔴 **CHECKPOINT（人工确认，不阻塞自动继续）**——先与用户确认「工作区契约/脚本是最新意图版本」再继续，确认后全自动推进；不得基于「会话早期印象」假设版本，必须实时读取。
3. **启动 run**：

```text
python3 scripts/full_analysis.py start --company <公司名> --code <证券代码> --as-of <YYYY-MM-DD>
```

   - 启动被 `E1 版本门禁` 拒绝时：先 `git checkout v3.4.4`（当前最新 tag）再重试；确认目标版本无误且必须基于当前 HEAD 启动时，追加 `--allow-stale`。

   - 只从返回的 `run_root` 继续。注册表 `tools/full_analysis_contract.json` 是 13 项业务契约、阶段目录、角色、章节和适用性谓词的唯一机器真源；不要在本适配器中复制清单。
4. **核对落盘**：`start` 会把当前契约文件的 SHA-256（`contract.registry_sha256`）与 HEAD commit（`run.contract_commit`）记录到 `evidence/00-analysis-manifest.json`；启动后核对两条已落盘（E10 机器强制兜底见下）。
5. **核对预算**：`start` 返回 `budget` 时，核对 `normal_target` 与当前注册表 skill 数是否匹配（13 项业务契约 + preflight）；数量异常视为版本错配，停止并核对。
6. **E10 机器强制（与 E1 互补）**：E1 是编排器启动前自查（文档纪律），E10 是 `finalize` 硬校验——finalize 重算当前契约 digest，与 run 记录不一致则拒绝准出（`CONTRACT_VERSION_MISMATCH`，无 `--force` 绕过），防「过期编排 run 被 APPROVED」。run 启动后更新过契约的旧 run 只能迁移产物重跑。

## Agent 调度纪律

**领单元命令（v3.4.4 起必须带 --allowlist 实现错峰，禁止裸调用）**：

```text
# 普通波次（W1/W2/W5 或无需错峰的单元）：允许裸 next-work
python3 scripts/full_analysis.py next-work --run-root <run_root>
# 错峰波次（W3/W4）：必须带 --allowlist 指定本轮可领的 skill 集合
python3 scripts/full_analysis.py next-work --run-root <run_root> --allowlist investment-team,earnings-review
```

自 v3.3.10 起 runtime 按 **depends_on 依赖波次**调度：只有上游全 DONE 的单元才会返回 `LEASED`，跨波单元被挡（返回 `NO_WORK/DEPENDENCIES_PENDING`）直到上游完成。每次返回 `LEASED` 后，必须直接使用 **WorkBuddy 原生 Agent** 完成该 work unit；不得由 Python、shell 或旧版 orchestrator 再创建 Agent。

**波次并行派发（v3.3.10，强制）**——依赖波次的墙钟收益来自"一波内多单元并行"，而非逐个串行：

1. **收齐一波**：连续调用 `next-work` 直到返回 `NO_WORK`（`CONCURRENCY_LIMIT` 或 `DEPENDENCIES_PENDING`），把这批 `LEASED` 单元收为一个波次批次（并发上限 `concurrency.max`，默认 4）。**错峰波次用带 `--allowlist` 的调用收齐（见第 5 条具体命令），白名单外的就绪单元不会在本轮被领。**
2. **并行派发**：把该批次的每个单元作为**独立前台 Agent**，在**同一条消息里一次性全部派发**（多个 Agent 工具调用并发运行）。禁止逐个前台串行（那会把波次退化为全串行，浪费依赖图的 ~90 分收益）。
3. **并行 job-started**：所有 Agent 返回 `agent_job_id` 后，为每个单元调用 `job-started`。
4. **收尾与下一波**：各 Agent 完成即 `submit-result`；全部提交后回到第 1 步领取下一波。
5. **错峰（W3 拆两部分 + W4 industry-funnel 单独，v3.4.4 起带 allowlist 执行）**——波次内若混编「扇出重单元（TTL 80min）」与「轻单元（TTL 40min）」，轻单元在重单元研究（取数+WebSearch+写作常 >40min）完成前租约即过期被 sweep 误回收，导致重跑（宏景/沪电 run 实证）。**默认分派纪律（具体命令，逐字执行）**：
   - **W3a**：循环 `next-work --allowlist investment-team,earnings-review` 收齐两单元 → 并行派发 → 全部 DONE 前**不得**进入 W3b。
   - **W3b（W3a 全 DONE 后）**：循环 `next-work --allowlist management-deep-dive,industry-research` 收齐两单元 → 并行派发。
   - **W4a**：`next-work --allowlist industry-funnel` 单独派发（funnel 需全市场候选池取数+多轮 WebSearch，单独跑可避免与扇出/数据重单元争并发与 429）。
   - **W4b（funnel DONE 后）**：循环 `next-work --allowlist bottleneck-hunter,news-pulse` 收齐两单元 → 并行派发。
   - **屏障**：W3b 只能在 W3a 两单元全部 DONE 后领取（`--allowlist` 只限制本轮可领集合，**不能**凭白名单越过依赖——W3b 单元依赖已满足时会就绪，故编排器必须以「W3a 全 DONE」为 W3b 领取的前置条件，不得提前调用 W3b 的 allowlist）；若误在 W3a 未完成时领 W3b，轻单元会重蹈租约过期覆辙。
   - 若上游连续 429，配合下方 E12 lite 降级继续。
6. **波次墙钟（错峰后实测）**：W1→W5 关键路径 ≈ 42-50 分（并行波次）；W3 拆分后墙钟略增（约 +8-10 分）但轻单元零重跑，净收益为正（待 v3.4.4 后 run 复验）。

当前 13 单元的波次（契约 depends_on 的拓扑分层，**编排默认分派**）：W1 `ashare-data` → W2 `financial-data`/`quality-screen`/`investment-checklist`/`investment-research`（×4 并行）→ **W3 拆两部分**：W3a `investment-team`+`earnings-review`（扇出重单元并行）→ W3b `management-deep-dive`+`industry-research`（轻单元并行，W3a 全部 DONE 后领）→ **W4 先单独跑 `industry-funnel`**，完成后再并行 `bottleneck-hunter`/`news-pulse` → W5 `thesis-tracker`。关键路径墙钟 ≈ 42-50 分（vs 串行 ~142 分）。

启动原生 Agent 前调用：

```text
python3 scripts/full_analysis.py job-started \
  --run-root <run_root> --work-unit-id <work_unit_id> \
  --attempt-id <attempt_id> --lease-nonce <lease_nonce> \
  --agent-job-id <WorkBuddy 返回的 job id>
```

**调度时序纪律（E2，强制）**——非扇出单元租约默认 40 分钟过期（宏景 run 实证：mgmt ~35min、ind-research ~25min，旧 20 分钟频繁被 sweep 误回收）；扇出单元自动倍增（= 20 × 独立角色数，如 team/earnings 4 角色 = 80 分钟）；heartbeat 按派发时的租约 TTL 续期。租约过期后 submit 被拒、只能走 resume 孤儿恢复（requeue 会丢失已完工作）。以下顺序不可颠倒：

1. **前台并行派发 Agent**（Agent 工具默认模式），从每个返回结果取真实 `agent_job_id`；一波内的多个单元在**同一条消息里并行派发**（见上「波次并行派发」）。**禁止后台派发**（`run_in_background` 不返回 job id，Agent 完成后无法及时 job-started，租约过期即触发 requeue 灾难）——并行用"一条消息多个前台 Agent"实现，不用后台。
2. 所有并行 Agent 返回后**立即**为每个单元调用 `job-started`（60 秒内），各自完成后 `submit-result`；不要把提交拖到下一个波次之后。
3. 若提交被拒（身份不匹配/实质校验/预提交门禁），当场修复 result.json 或报告后重提，**不要留到收口阶段批量处理**——批量返工会使整条返工链（audit→prepare→评审→ingest→summarize）连锁重跑。
4. **长任务强制 heartbeat**：预计执行超过 10 分钟的 Agent（扇出单元、多模块研究单元），派发指令必须要求其每完成一个主要阶段调用一次 `heartbeat`（命令见 BUNDLE-SPEC，心跳按租约 TTL 续期）；全程零心跳的 run 会被 doctor 判「疑似主上下文直写」，需人工复核。
5. **429 降级派发（E12）**：派发 Agent 遇模型 429 限流时，改派 `model:"lite"` 继续（不中止 run、不消耗 runtime 预算）；runtime 的 429 冷却仅约束 Agent job 预算侧，派发层的模型降级是编排器正常容错，两者不混淆。若 lite 亦连续失败，才走 `record-failure` 重试退避。
6. **并行限流权衡（v3.3.10，W3 拆分后更新）**：峰值并行度受 `concurrency.max`（默认 4）约束，已从 1 起步调优。三个扇出单元（team/earnings/news）内部已是多角色独立上下文，叠加单元级并行峰值可达 8-10 Agent，易触发上游 429。纪律：数据类轻单元（ashare/financial/quality/checklist/research）大胆并行；**W3 按第 5 条拆两部分错峰**（重扇出与轻单元不混编），若仍连续 429 则把重单元内部两扇出也拆开错峰，配合本条第 5 项 lite 降级。
7. **Agent 返回空/未 job-started 的兜底（E15，强制）**：若并行 Agent 返回**空结果**（拿不到真实 `agent_job_id`，五粮液 run W4 三单元卡死根因），**不要等待租约自然过期**——立即对对应单元执行 `resume`（`python3 scripts/full_analysis.py resume --run-root <run_root>`）。v3.3.12 起 runtime 对「从未 job-started 的 LEASED 租约 + 磁盘存在 result.json」支持孤儿恢复：只要 bundle 的 `attempt_id` + `lease_nonce` 与租约一致即接管为 DONE（agent_job_id 不作强校验，因为从未登记），产物合格则直接晋级、不合格才标 abandoned 重新排队。**注意**：Agent 自提交路径（Agent 内部完成 mk_result_bundle 后自行 submit-result）同样受此兜底——即使 Agent 忘了 job-started，submit 不再因 agent_job_id 缺失被拒。

派发给 Agent 的指令必须包含注册表分配的精确正式产物路径，并要求其把中间文件放在：

```text
<run_root>/evidence/attempts/<skill_id>/<attempt_id>/
```

**usage 回传协议（E14，Task 1 后强制）**——每个 work/summary/review Agent 完成后必须回传真实用量，供成本基准与预算告警（Task 6）使用：

```text
python3 scripts/full_analysis.py record-usage \
  --run-root <run_root> --phase work|summary|review \
  --attempt-id <attempt_id> --skill-id <skill_id> \
  --input-tokens <int> --output-tokens <int> \
  --input-bytes <int> --output-bytes <int> \
  --duration-ms <int> [--cache-hit]
```

三条纪律：①**token 缺失允许**——提供商不返回 token 时只提交字节数（`input_tokens`/`output_tokens` 记 `null`），禁止伪造为 0；②**字节数必填**——`input_bytes`/`output_bytes` 缺失整条被拒；③**一次一条**——同一 `attempt_id + phase` 只允许一条记录，重复提交被拒。汇总写入 `evidence/usage.jsonl` 并在 manifest `usage_summary` 按 phase/skill 聚合，作为 benchmark 与 `COST_BUDGET_EXCEEDED` 告警的唯一真源。
Agent 必须返回 Result Bundle v1（`schema_version=result-schema/v1`）和短收据。除 facts/sources/calculation requests 外，必须按当前 skill 的 `evidence_rules` 填写结构化 `judgments`、`command_receipts` 与 `capability_records`；缺少任何已注册规则所需账本时 Audit 必须失败。多角色单元的 `role_runs` 不接受 Agent 自证，由 Gate 根据实际 `role-<role>.md` 备忘录生成并绑定路径、字节数与 SHA-256。计算请求只能提交 operation/args，重放结果由共享 Audit Job 调用 `financial_rigor.py` 生成，Agent 不得自证。`NOT_APPLICABLE` 不是自报状态：必须提交 `not_applicable` 结构、带来源的判定事实和负向验收报告；`always`/`always_applicable` 单元不得 N/A。主上下文只接收 `attempt_id`、`result_path`、`status`、`bytes`、`sha256`；**不读取报告正文、不复制隐藏推理、不把长文本带回主上下文**。

**派发前必读 `next-work` 注入的规范**：每次 `next-work` 返回的 payload 内含 `methodology_text`（即 `skills/<skill_id>.md` 完整方法论 + 结构指令 + 证据指令 + Result Bundle 模板）、`sections`（章节与最低字数要求）、`evidence_rules`（本 skill 的结构化证据最低要求）与 `roles`/`fanout_required`。执行 Agent 必须以 `methodology_text` 为强制规范完整落地，**不得仅凭 skill 名称凭记忆发挥**。三条刚性纪律：

1. **heading 逐字使用**：报告 `##` 标题必须逐字使用 `sections` 数组中每个条目的 `heading` 字段值（如「数据截止日」「核心结论」），**严禁使用 `section_id`**（如 `data_cutoff`）。Gate 按 heading 精确匹配，用 section_id 整份报告被拒收。
2. **## 后必须有正文**：每个 `##` 章节下必须先有 ≥150 字正文段落，再展开 `###` 子节。`##` 后紧跟 `###` 会导致该章节被判为「无正文」，不计入实质章节数。
3. **结构化证据必填**：Result Bundle 必须包含 `fact_updates` / `source_records` / `calculation_requests` / `judgments` / `command_receipts`，按 `evidence_rules` 满足最低条数。只写报告正文不写证据 → Audit 产生 violation → 阻断准出。

**派发模板必含账本清单（E3，强制）**——Audit 按「契约精确值」核对账本，Agent 按模板印象填中文名/自定义 rule_id 必被拒（两次 run 均已踩坑）。编排器派发 prompt 必须**把本 skill 的契约要求逐字内嵌**，不得让 Agent 自行推断：

- **sections 清单**：把 `next-work` payload 的 `sections[]` 的 `heading` 逐字列出（一个都不能少），并写明「## 下先写 ≥150 字正文再展开 ###」。
- **evidence_rules 账本清单**：把 `evidence_rules[]` 转成可执行清单——
  - `required_fact_fields`：逐字列出 field 值（如 `quality_metric_1`…`quality_metric_7`、`income_statement`/`balance_sheet`/`cash_flow`、`funnel_universe`/`funnel_top10`/`funnel_final3`），要求 fact_updates 的 `field` 与之完全一致（**禁止用中文名**）。
  - `required_judgment_rule_ids`：逐字列出 rule_id（如 `checklist_final_decision`/`investment_thesis`/`contrarian_synthesis`/`management_integrity`/`industry_scope`/`physical_bottleneck`/`price_move_attribution`/`core_thesis`），要求 judgments 至少一条 `rule_id` 精确匹配。
  - `conditional_command_operations.capability`：逐字给出 capability 名（如 `tushare_configured`），要求 capability_records 含该名且 `available: true`。
  - `min_judgments_with_falsification`：要求每条第 `falsification` 非空数组，条数 ≥ n。
- **result.json 结构红线**：command_receipts 每条只允许 `receipt_id/operation/status/detail/reason` 五键；fact_updates/source_records/judgments 等列表对象 `additionalProperties=false`，不得携带扩展字段（如 `detail`/`skill_id`）；评审维度 `dimensions` 必须是数组 `[{dimension, verdict}]` 而非 dict。
- **calc 表达式红线**：`financial_rigor.py calc` 只支持纯四则运算（白名单 `0123456789.+-*/() eE`），**禁止 `round(...)` 与 `^` 幂运算**（会被判「不安全的表达式」导致 Audit 重放失败）；需要取整/幂时提交不含 round/^ 的表达式（如 `(1-59.46/86.11)*100`）。
- **fact_id/receipt_id 命名纪律（防跨单元覆盖，强制）**：所有 `fact_updates[].fact_id` 和 `command_receipts[].receipt_id` **必须以本 skill 的 `skill_id` 作为前缀**，格式为 `fact-<skill_id>-<descriptor>` 和 `rcpt-<skill_id>-<descriptor>`。**禁止通用编号**（如 `fact-001`、`fact-price-301396`、`rcpt-quote-301396`）。**根因**：gate ingest 按 `fact_id`/`receipt_id` 做 last-write-wins 合并，不同 skill 使用相同 ID 会覆盖 `skill_id` 归属，导致 audit 缺字段（宏景 run 因 ID 冲突触发 3 轮 correction 修复）。示例：ashare-data 用 `fact-ashare-data-price`、`rcpt-ashare-data-quote`；thesis-tracker 用 `fact-thesis-tracker-price`、`rcpt-thesis-tracker-quote`；financial-data 用 `fact-financial-data-revenue`、`rcpt-financial-data-income-stmt`。

**多角色 skill 必须真扇出**：当 `fanout_required: true` 时，必须为 `roles.required_roles` 中每个角色（除 `integrator` 外）启动一个**独立原生 Agent**（用 Task 工具 fan-out），各自在 `evidence/attempts/<skill_id>/<attempt_id>/role-<role>.md` 产出独立分析备忘录（每个 ≥300 字节，且不得相互引用以保证独立性）；最后由整合 Agent 读取全部角色备忘录产出正式整合报告。缺少任一 `role-<role>.md` 时 Gate 会拒收。单 Agent skill 则由一个原生 Agent 按 methodology 完整执行。

**result.json 必须用确定性生成器构造（E16，强制，防手写 JSON 返工）**——**禁止子 Agent 手写 result.json**。Agent 完成报告后，**必须**运行：

```text
python3 scripts/mk_result_bundle.py \
  --run-root <run_root> \
  --skill-id <skill_id> --work-unit-id <wu-xxx> \
  --attempt-id <attempt-xxx> --lease-nonce <nonce> \
  --agent-job-id <真实 agent_job_id> \
  --report <attempt_dir>/report.md \
  --status PASS \
  [--extra-evidence <facts.json>] [--extra-sources <sources.json>] \
  [--limitation "code|detail"] [--pwl <pwl>] [--role-id <role>]
```

该工具自动：从 runtime-state 校验租约身份（attempt_id + lease_nonce 强校验；agent_job_id 仅在已登记时比对）、按 contract 的 `evidence_rules` 生成**最小合规证据**（facts/sources/calcs/judgments/role_runs/receipts/capabilities 缺一即自动补足且满足最低条数）、按 report 实际文件重算 bytes/sha256、核对全部必需章节标题与 min_bytes 并给出预检警告。Agent 只需把**真实调研产物**（fact 数值/来源/计算参数/判断）通过 `--extra-evidence`/`--extra-sources` 传入合并，机械性字段全部交给工具。**历史根因**：五粮液 run 中 Agent 手写 JSON 导致 4 类 schema 返工（fact 用 `sources` 字段而非 `source_ids`、source_type 枚举越界、calculation 缺 `calculation_id`、limitations 写成字符串数组），audit 前反复修补——E16 从源头消除。

**result.json 优先写入**：Agent 完成分析后，第一步运行 mk_result_bundle 生成 result.json（写入 attempt_dir），第二步再调用 submit-result。即使 submit-result 因会话中断失败，磁盘上的 result.json 可被 `resume` 的孤儿恢复机制接管（Runtime 会检查 `evidence/attempts/<skill_id>/<attempt_id>/result.json` 是否存在且 Gate 可接受）。

完成后调用：

```text
python3 scripts/full_analysis.py submit-result \
  --run-root <run_root> --result <attempt_dir>/result.json
```

租约期间按需调用 `heartbeat`（长任务按上文「调度时序纪律」第 4 条**强制**）。Agent 失败调用 `record-failure`；429 在派发层按「调度时序纪律」第 5 条降级 lite 继续，runtime 侧的 429 冷却只约束 Agent job 预算，禁止手工绕过预算。达到 `stop_dispatch_at`（30 次）后停止非核心派生重试；达到 `hard_max`（33 次）立即停止新派发，生成 PARTIAL/SUMMARY，验收失败。🔴 **CHECKPOINT（budget 触顶，必报用户）**：触顶即向用户报告「剩余工作单元 + 已 APPROVED 产物 + 触顶原因（429 持续 / 单元反复返工）」，由用户决定继续还是收口为 PARTIAL。**继续分支（v3.4.2 闭环）**：用户同意调高预算后执行 `python3 scripts/full_analysis.py budget-adjust --run-root <run_root> --stop-dispatch-at <新值> --hard-max <新值> --reason "<原因>"`（只允许上调，防静默降标，调整记入 events.jsonl）；否则收口 PARTIAL。预算参数以 Gate `budget_params` 为准，本处数字仅作人读说明。

## 执行一致性纪律（防质量坍塌，强制）

**每个 work unit 必须由独立原生 Agent 完成，主上下文只做调度，不得直接撰写分析正文。** 这是质量稳定的物理前提——独立 Agent 拥有新鲜上下文窗口、完整 `methodology_text`、专属外部调研任务；主上下文一旦亲自续写，深度必然坍塌（历史事故根因，勿回退）。

- **会话摘要压缩后严禁主上下文直写**：若本次会话经历过上下文摘要/压缩，后续所有未完成的分析单元**必须继续派发真子 Agent + 重新外部取数**（WebSearch、数据管线、`financial_rigor` 验算），严禁凭压缩摘要 + 参数记忆在主上下文里"顺手写完"。压缩只会丢失上下文，不能改变执行架构。
- **heartbeat 是真研究的指纹**：长任务 Agent 在租约期间必须周期性调用 `heartbeat`。一个分析 run 若全程零心跳却产出了全部单元，几乎等价于"主上下文直写"——doctor 会就此告警，必须人工复核 10 号后单元是否退化。
- **扇出单元不得在主上下文模拟**：`fanout_required` 单元的各角色必须用 Task 工具派独立 Agent，禁止单 Agent 串行"扮演"多角色后自称已扇出。

---

## 🚫 禁止事项清单（红灯规则全集）

> **用途**：快速扫描「绝对不能做的事」。每条禁令在原文中有完整上下文和根因解释，编号对应下文 `[禁-N]` 标注。

### A. 派发与并行纪律

| # | 禁令 | 后果 | 原文位置 |
|---|------|------|---------|
| 禁-1 | **禁止后台派发** Agent（`run_in_background`） | 不返回 job_id → 无法 job-started → 租约过期 requeue 灾难 | Agent 调度纪律 §1 |
| 禁-2 | **禁止逐个前台串行**派发（同波单元必须在一条消息里并行） | 波次退化为全串行，浪费依赖图 ~90 分收益 | Agent 调度纪律 §2 |
| 禁-3 | **禁止裸调用 next-work**（W3/W4 必须带 `--allowlist`） | 重扇出与轻单元混编 → 轻单元租约过期被 sweep 误回收重跑 | Agent 调度纪律 §5 |
| 禁-4 | **禁止**用 Python/shell/旧版 orchestrator 再创建 Agent | 绕过 Runtime 租约/预算管控，导致状态不一致 | Agent 调度纪律 |
| 禁-5 | **不得**提前调用 W3b 的 allowlist（必须在 W3a 全 DONE 后） | 轻单元会重蹈租约过期覆辙 | Agent 调度纪律 §5 屏障 |
| 禁-6 | **不得**由主上下文直接撰写分析正文 | 主上下文无新鲜窗口/外部调研 → 深度坍塌 | 执行一致性纪律 |
| 禁-7 | **严禁**会话摘要压缩后主上下文直写（必须真子 Agent + 重新取数） | 压缩丢失上下文，凭记忆写 → 质量坍塌 | 执行一致性纪律 |
| 禁-8 | **扇出单元不得在主上下文模拟**（禁止单 Agent "扮演"多角色后自称已扇出） | role-*.md 缺失 → Gate 拒收 | 执行一致性纪律 |

### B. 报告与证据格式

| # | 禁令 | 后果 | 原文位置 |
|---|------|------|---------|
| 禁-9 | **严禁**使用 `section_id`（如 `data_cutoff`），必须用 heading 原文（如「数据截止日」） | Gate 按 heading 精确匹配，用 section_id 整份报告被拒收 | 派发前必读规范 |
| 禁-10 | **禁止**用中文名填 fact_updates 的 `field`（如「毛利率」→ 必须用 `gross_margin`） | Audit 按契约 field 精确匹配失败 → violation | 派发模板 E3 |
| 禁-11 | **禁止**通用编号 fact_id/receipt_id（如 `fact-001`、`rcpt-quote-301396`），必须带 skill_id 前缀 | 跨单元 last-write-wins 覆盖 → audit 缺字段（宏景 run 3 轮返工） | 派发模板 E3 |
| 禁-12 | **禁止** `round()` 与 `^` 幂运算在 calc 表达式中 | `financial_rigor.py` 判「不安全表达式」→ Audit 重放失败 | 派发模板 E3 |
| 禁-13 | **禁止子 Agent 手写 result.json**（必须用 `mk_result_bundle.py` 确定性生成器） | Schema 返工 4 类（sources/source_type/calculation_id/limitations） | E16 纪律 |
| 禁-14 | **不得**携带扩展字段（fact_updates/source_records/judgments 等 `additionalProperties=false`） | Schema 校验失败 → Audit 拒收 | 派发模板 E3 |
| 禁-15 | **不得**仅凭 skill 名称凭记忆发挥（必须完整落地 `methodology_text`） | 章节/证据缺失 → Audit violation | 派发前必读规范 |
| 禁-16 | **不得**让 Agent 自行推断契约要求（编排器派发 prompt 必须逐字内嵌 sections/evidence_rules） | 中文名/自定义 rule_id → Audit 拒收（两次 run 踩坑） | 派发模板 E3 |

### C. 审计与返工

| # | 禁令 | 后果 | 原文位置 |
|---|------|------|---------|
| 禁-17 | **禁止** audit 失败后一律整单重跑（必须按 correctable/report 分流） | 轻量修正→完整重跑浪费资源 + 丢失已完工作 | 返工协议 E9/E11 |
| 禁-18 | **禁止** correction-bundle 携带报告路径 | correction 只修账本，带报告会被拒 | 返工协议 §1 |
| 禁-19 | **不得**以 override 评审代替修正（凡 high/medium finding 必须先修源文件再重走全链路） | 评审价值被架空，语义缺陷残留 | 语义评审纪律 |
| 禁-20 | **不得**编造 evidence_refs（必须真实存在于归因账本或报告正文） | 虚假引用 → 评审误判 PASS | 语义评审纪律 |
| 禁-21 | **禁止**单 Agent "扮演"多角色后自称已扇出（`fanout_required` 必须真扇出独立 Agent） | role-*.md 缺失 → Gate 拒收 | 多角色 skill 纪律 |

### D. 数据与基线

| # | 禁令 | 后果 | 原文位置 |
|---|------|------|---------|
| 禁-22 | **不得**用训练记忆假设日期（必须执行本地 `date`） | 数据截止日错位 → 全 run 信息基线错误 | 启动 §1 |
| 禁-23 | **不得**基于「会话早期印象」假设版本（必须实时读契约文件） | 过期编排启动 → E10 finalize 拒出 | 启动 §2 |
| 禁-24 | **禁止**伪造 token 为 0（提供商不返回时记 `null`） | 成本基准失真 → benchmark 不可比 | usage 回传协议 |
| 禁-25 | **禁止**手工绕过 429 预算冷却（runtime 429 冷却约束 Agent job 预算） | 预算形同虚设 → hard_max 被破 → 无法收口 | Agent 调度纪律 §5 |
| 禁-26 | **不得**引入新数据/新推理/新结论（deep-summary Agent 只读只提炼） | 总结报告不再是忠实综合 → 与底层产物矛盾 | 总结产出纪律 |

### E. 路径与格式细节

| # | 禁令 | 后果 | 原文位置 |
|---|------|------|---------|
| 禁-27 | **不得**重新计算/改写 digest（必须从简报逐字复制） | ingest 摘要不匹配拒收 | 语义评审纪律 |
| 禁-28 | **不得**写成 `name`/`result` 之类别名（维度项必须用 `dimension`/`verdict` 键名） | JSON schema 校验失败 | 语义评审纪律 |
| 禁-29 | **禁止**用相对路径传 `register-summary` / `review ingest` | 双重拼接报错 | 语义评审纪律 |
| 禁-30 | **不要**把提交拖到下一个波次之后（Agent 返回后 60 秒内 job-started） | 租约过期 → submit 被拒 | 调度时序纪律 §2 |
| 禁-31 | **不要**留到收口阶段批量处理 submit 被拒（当场修复或报告后重提） | 批量返工使 audit→prepare→评审→ingest→summarize 连锁重跑 | 调度时序纪律 §3 |
| 禁-32 | **不要**等待租约自然过期（Agent 返回空 → 立即 resume） | 五粮液 run W4 三单元卡死根因 | E15 兜底 |
| 禁-33 | **不要**在本适配器中复制契约清单（注册表是唯一真源） | 副本过时 → 派发信息错位 | 启动 §3 |

---

## 恢复与收口

**返工协议（E9/E11 分工，强制）**——audit 失败的返工按错误类型分流，禁止一律整单重跑：

1. **证据账本类（CORRECTABLE_EVIDENCE）→ `submit-correction`**：audit 错误的 `correctable: true`（缺 fact 字段/rule_id/capability、receipt 不足、计算参数需修正、已删除请求的残留清理等）只修账本、不重写报告、不新增 attempt。用 `correction-bundle/v1` 提交修正（只允许修改已有 ID；删除用 `"removed": true`；禁止携带报告路径）：

```text
python3 scripts/full_analysis.py submit-correction \
  --run-root <run_root> --correction <correction.json>
```

correction 合并**以修正集合为准做差集**：被 removed 的记录从 manifest 移除（防止残留让 audit 二次暴露），其余 last-write-wins 覆盖；`base_attempt_id` 与 digest 记入 manifest.corrections 可追溯。
2. **报告正文/artifact 类（REPORT_REQUIRED_RETRY）→ `rework`**：缺章节/缺正文/实质校验不通过才重置单元重新派 Agent（见下）。

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

# 步骤 B2：生成 HTML 展示件（register-summary 后立即执行，不等 audit/finalize）
# 用 Gate 内置确定性渲染器，读取已登记的 summary.md 生成自包含 HTML（不派 Agent）
python3 scripts/full_analysis.py render-html --run-root <run_root>
# HTML 只是 markdown 的展示件，不参与 Gate 校验，不依赖 audit 结果
# 渲染是纯函数：同一 markdown → 同一 HTML；若 markdown 因评审返工被编辑，重跑本命令即可

# 步骤 C：审计 + 语义评审 + 准出
python3 scripts/full_analysis.py audit --run-root <run_root>
python3 scripts/full_analysis.py review prepare --run-root <run_root>
# 为 scope 内每个 skill 派独立评审 Agent（见语义评审纪律）
python3 scripts/full_analysis.py review summarize --run-root <run_root>
python3 tools/full_analysis_gate.py finalize --run-root <run_root>

# 步骤 D：finalize APPROVED 后跑 doctor 体检；HTML 已在步骤 B2 生成
# 收口后处理（Task 5/6）：finalize 自动写跨运行产物缓存（cache-store）与
# 成本告警（cost_budget 字段，非阻断）。编排器可查询缓存复用上游产物：
#   python3 scripts/full_analysis.py cache-lookup --run-root <run> --skill-id <skill>
#   （命中 HIT 时，同一公司同 as_of 的后续 run 对应单元可直接复用 artifact，
#     不派 Agent；方法论/上游事实/能力变化自动使缓存失效）
#   python3 scripts/full_analysis.py benchmark --run-roots <run1> <run2>
#   （stability 报告含 metrics.usage：total_tokens / cache_hit_rate）
```

> **为什么 register-summary 必须在 audit 之前**：`analysis_snapshot` 的投影包含 `manifest.delivery`，register-summary 写入 `delivery.summary` 会改变快照摘要。若 audit 先于 register-summary 执行，finalize 的快照一致性校验将因摘要不匹配而拒绝准出。

### 总结产出纪律（步骤 A，deep-summary Agent 强制）

编排器派发 deep-summary Agent 时，指令必须包含：

- **输入**：13 份正式产物的绝对路径（从 manifest 的 `skills[].artifact_records[].path` 取得）+ `as_of` 日期 + 公司名/代码。
- **方法论**：遵循 deep-summary skill 的忠实熔炼纪律——引用其 `references/distillation-guide.md`（本仓库场景下位于 `~/.workbuddy/skills/deep-summary/references/`，即用户级 skill 安装目录；若该文件在所用 Runtime 的 skill 安装目录中不可达，按「只读、只提炼、不 WebSearch、不取新数、不做新推理」原则执行）——只读、只提炼，不 WebSearch、不取新数、不做新推理。
- **输出格式**：必须包含 `register-summary` 要求的 8 个必需章节（核心结论速览 / 主干①·投资分析 / 主干②·财报研读 / 主干③·行业分析 / 补充与参考 / 产物索引 / 数据截止日 / 仅供学习研究），字节 ≥ 2500。
- **产物索引**：必须逐条列出 13 份正式产物的完整相对路径，缺一即被 `register-summary` 拒收。
- **写入路径**：`<run_root>/evidence/attempts/summary/summary.md`。
- **禁止**：不得引入新数据/新推理/新结论；不得调用 `register-summary`/`audit`/`finalize`（这些由编排器执行）；不得触碰 `evidence/` 下其他文件。

总结报告是正式交付，不是 Gate 外附件。只能综合已登记正式产物，不得引入新取数或新推理。Gate 会冻结其路径、字节数和 SHA-256；Review 会使用全部归因证据检查总结，修改总结或任一底层证据都会使旧 Audit/Review 过期。

### HTML 版总结报告（步骤 B2，确定性渲染）

register-summary 完成后，编排器**立即**执行 `python3 scripts/full_analysis.py render-html --run-root <run_root>`，由 Gate 内置**确定性渲染器**（`tools/full_analysis_html.py`）把已登记的 summary.md 渲染为自包含 HTML，**不派 Agent、不等待 audit/review/finalize**。HTML 只是 markdown 的派生展示件，不参与 Gate 校验。纪律：

- **确定性渲染 = 固化品质**：设计系统（cream paper / terracotta / trust 墨蓝 / serif + masthead 报头 + sticky 导航 + 编号章节 + 样式化表格 + 滚动显现微交互）已逐字固化为代码。同一份 markdown 永远渲染出逐字节一致的 HTML，**零 LLM 参与、零 token、零方差、零失败模式**——展示件品质不随 run 漂移，这是把"用户认可的输出质量"钉死在流程里的机制。
- **它是 markdown 的派生展示件，不是 Gate 产物**：不参与 audit/review/finalize，不进入 manifest，不改变任何 digest。Gate 的准出真源永远是 markdown。
- **它不依赖 finalize**：register-summary 后 summary.md 已被 Gate 校验（章节/字节/产物索引），HTML 即可生成——这是解耦关键。过去绑在 finalize 上导致 audit 阻塞→HTML 永久不可得。（2026-07-26 落地）
- **双入口、幂等一致**：步骤 B2 的 `render-html` 命令负责即时生成；finalize APPROVED 之后 Gate 再跑一次 `_generate_summary_html()` 作兜底。二者共用同一渲染器，因渲染确定性，两次产出逐字节一致，不存在"谁覆盖谁"。
- **100% 忠实于 markdown**：渲染器（`build_summary_page`）只转换 markdown 原文（标题/表格/列表/代码/引用/加粗/斜体/安全链接），不引入新数据、新推理或新结论；javascript: 与属性逃逸类链接被剔除，元数据一律转义。
- **非阻断**：HTML 生成失败只打印警告到 stderr，不影响后续 audit/review/finalize 流程。任何异常都被捕获，绝不静默。
- **品质由回归测试守护**：`tests/test_full_analysis_html.py` 守住确定性 / 安全性 / 结构完整性（8 章节锚点、报头、导航、印章、免责声明）/ 忠实性（无 stash 占位符泄漏）四条底线，逐 run 无需人工逐项核查。
- **markdown 一旦被编辑并重新 register-summary，重跑 render-html 即可**：确定性渲染保证重跑即得与新版本一致的 HTML，旧 HTML 被原地覆盖，不会并存误导。

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
- **`fix_source`（E13，推荐填写）**：每条 finding 可带 `fix_source` 指向问题**源头**而非评审结果本身——`{"file": "<相对 run_root 路径>", "line_approx": <int|null>, "kind": "pipeline_raw"|"role_memo"|"report"|"methodology", "note": "<一句话>"}`。传播型笔误（口径错、倍数笔误、基数错误）应填到源头（子 Agent 备忘录/管线 raw/正式报告），ingest 后聚入 `review-index.json`，可用 `review fix-list` 导出季度源头修复清单；缺 fix_source 的 finding 归 UNFIXED 组。low 笔误不进返工链，由清单在下次 run 前批量修源头。

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

doctor 是**advisory 非阻断**诊断（不影响 APPROVE/FAIL），专门捕捉"过了 Gate 下限但仍可能坍塌"的执行退化指纹：①全部/大量分析单元贴线（字节仅略超 `min_bytes`，深度存疑）②零 heartbeat（疑似主上下文直写）③深度分化不足。**过下限 ≠ 同等深度**，下限只是地板。若 doctor 返回 WARN：🔴 **CHECKPOINT（人工复核，不阻塞收口）**——必须人工复核被点名的贴线单元与 10 号后单元，确认是真深度不足还是合法的快 run；确属坍塌的，按"返工路径"（重置目标单元为 PENDING + manifest 置 PARTIAL + 记录 `rework_initiated` 事件）重新派真子 Agent 返工，再重跑 audit+finalize。**复核结论落盘（v3.4.2 闭环）**：执行 `python3 scripts/full_analysis.py event-log --run-root <run_root> --kind doctor_checkpoint --note "复核结论：确属坍塌/合法快 run + 依据"`（kind 白名单：human_review/manual_rework/doctor_checkpoint），结论写入 `evidence/events.jsonl` 备查。

重复运行 Benchmark 只比较同一公司、同一 `as_of`、同一 Contract digest 且全部 `APPROVED` 的 run；任一事实、计算或判断在某个 run 缺失都算不稳定，不得以空集合冒充 100% 一致。

所有产出仅供学习研究，不构成投资建议。
