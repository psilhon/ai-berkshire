# Full Analysis Token Cost Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不降低 Audit、Gate、语义评审和证据链约束的前提下，降低全量分析的重复 Token 输入、整单重试和跨运行重复计算，并建立可审计的真实 Token 成本计量。

**Architecture:** 分四层改造：先记录每个 Agent 阶段的真实 usage；再把确定性证据错误改成 correction bundle；随后将语义评审由“复制全部证据”改为“报告主张 + evidence ID 索引”；最后增加方法论和已通过单元的 digest 缓存。所有减量都通过现有 Gate 校验，缓存命中必须重新验证 schema、digest 和来源绑定。

**2026-08-01 收编（C 层可靠性立项）**：本计划新增 Task 7-9 收编两次全量 run（紫金/雅克）固化的可靠性项——**E9 rework 命令**（封装返工调度，替代手编 runtime-state，Task 7）、**E10 finalize 契约版本钉死**（防过期编排 run 被准出，Task 8）、**E13 评审 finding 源头回写**（fix_source + 季度修复清单，Task 9）。**E11 correction bundle 已由 Task 2 覆盖**（雅克 run 补充 manifest 残留差集清理经验）。C 层与成本优化目标互补：rework/correction 消除整单重跑即 Token 节省；E10 是质量门禁不是减量项，与「不降低约束」约束一致。

**Tech Stack:** Python 3 标准库、JSON/JSONL、现有 Runtime/Gate/Audit、`unittest`、`bash scripts/check.sh`。

## Global Constraints

- 保持现有 Contract v2、Result Bundle v1、Semantic Review v1 的 APPROVED 语义不变；版本升级必须显式记录。
- 不关闭 13 个分析单元、结构化 evidence_rules、financial_rigor 重放或默认 8 个核心语义评审。
- 不新增第三方依赖，不读取或写入凭据，不改变 `local/` 隐私边界。
- 所有新增路径必须可由 Gate 通过 SHA-256、字节数和 schema 重新验证。
- 当前工作树中的 `scripts/full_analysis.py`、`tools/full_analysis_gate.py`、`tools/full_analysis_review.py` 和 `deliverables/` 属于用户改动，执行前必须单独保留并先完成基线测试。

---

### Task 1: 建立真实 Token/字节/重试计量

**Files:**
- Modify: `tools/full_analysis_runtime.py`（usage receipt 生成、CLI 入口、run 汇总）
- Modify: `tools/full_analysis_gate.py`（usage receipt schema 和 manifest 绑定）
- Modify: `scripts/full_analysis.py`（新增 `record-usage` 转发）
- Create: `tests/test_full_analysis_usage.py`
- Modify: `tests/test_full_analysis_runtime.py`
- Modify: `skills/full-company-analysis.md`（Agent/summary/review usage 回传协议）

**Interfaces:**
- 新增 CLI：

```text
python3 scripts/full_analysis.py record-usage \
  --run-root <run_root> \
  --phase work|summary|review \
  --attempt-id <attempt_id> \
  --skill-id <skill_id> \
  --input-tokens <int> --output-tokens <int> \
  --input-bytes <int> --output-bytes <int> \
  --duration-ms <int> [--cache-hit]
```

- 写入 `evidence/usage.jsonl`，每条记录包含：`schema_version`、`run_id`、`phase`、`skill_id`、`attempt_id`、`input_tokens`、`output_tokens`、`input_bytes`、`output_bytes`、`duration_ms`、`cache_hit`。
- `input_tokens`/`output_tokens` 缺失时允许为 `null`，但字节数和 attempt 绑定必须存在；缺失 usage 不得静默伪造为 0。
- manifest 增加只读汇总 `usage_summary`，按 `phase`、`skill_id` 和 `retry_of` 聚合。

- [ ] **Step 1: 写失败测试**：验证 usage receipt 字段完整性、负数拒绝、缺失 token 记录为 `null`、重复 `attempt_id + phase` 被拒绝。
- [ ] **Step 2: 运行测试确认失败**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_usage -v
```

预期：新增 schema/CLI 测试失败。

- [ ] **Step 3: 实现最小闭环**：新增 receipt 校验、JSONL 原子追加、manifest 聚合；不改变现有预算计数逻辑。
- [ ] **Step 4: 运行测试确认通过**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_usage tests.test_full_analysis_runtime -v
```

预期：全部 PASS，并可从 fixture 运行根目录读取 `usage_summary`。

- [ ] **Step 5: 更新协议文档**：明确 WorkBuddy、summary Agent、review Agent 在完成后提交 usage；若提供商不返回 token，只提交字符数并标记 `tokens_unavailable`。
- [ ] **Step 6: 提交**：

```bash
git add tools/full_analysis_runtime.py tools/full_analysis_gate.py scripts/full_analysis.py tests/test_full_analysis_usage.py tests/test_full_analysis_runtime.py skills/full-company-analysis.md
git commit -m "feat(full-analysis): add token usage accounting"
```

---

### Task 2: 将确定性审计失败改为 correction bundle

**Files:**
- Modify: `tools/full_analysis_gate.py:296-330, 610-690`（bundle 校验和 correction 合并）
- Modify: `tools/full_analysis_runtime.py:398-470, 581-650`（correction 状态与重试调度）
- Modify: `scripts/full_analysis.py`（新增 `submit-correction`）
- Create: `tests/fixtures/full_analysis/correction-bundle.json`
- Modify: `tests/test_full_analysis_gate_v2.py`
- Modify: `tests/test_full_analysis_runtime.py`
- Modify: `skills/full-company-analysis.md`（返工协议）

**Interfaces:**
- correction bundle 固定结构：

```json
{
  "schema_version": "correction-bundle/v1",
  "run_id": "<run_id>",
  "skill_id": "<skill_id>",
  "base_attempt_id": "<accepted-or-rejected-attempt>",
  "corrections": {
    "calculation_requests": [],
    "command_receipts": [],
    "fact_updates": [],
    "judgments": []
  }
}
```

- 新增 `submit-correction`：只允许修改已有 `calculation_id`、`receipt_id`、`fact_id`、`judgment_id`；禁止携带新的正式报告路径。
- Gate 在合并前调用现有重放/条件命令校验；通过后使用 last-write-wins 写入 manifest，并保留 `base_attempt_id` 和 correction digest。
- 仅当 correction 通过后仍存在报告正文或 artifact 问题，才将 work unit 置为 `PENDING` 并重新派 Agent。

**本 run 验证补充（2026-08-01 雅克 run rework，C 层 E11 已立项于此）**：correction 合并必须**以 bundle 请求集合为准做差集**，同时清理 manifest 中已删除请求的残留记录——雅克 run 中删除一条冲突的 cross-validate 请求后，`manifest.calculations` 仍残留旧记录，audit 的 `calculation_not_replayed` 继续按旧记录判定。只做增量覆盖会在 audit 阶段二次暴露。同类清理适用于被 correction 修改归因的 fact（last-write-wins 换 skill 后，旧 skill 的 `required_fact_fields` 判缺）。

- [ ] **Step 1: 写失败测试**：提交只修正 calculation/receipt 的 bundle，验证不新增 attempt、不重写报告，Audit 可读取新值。
- [ ] **Step 2: 运行测试确认失败**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_gate_v2 -v
```

预期：`submit-correction` 尚不存在或仍触发整单重试。

- [ ] **Step 3: 实现 correction schema 和 Gate 合并**：复用现有 `validate_result_bundle` 的字段校验，不复制一套宽松逻辑。
- [ ] **Step 4: 实现 Runtime 调度**：将 `audit violation` 分为 `CORRECTABLE_EVIDENCE` 与 `REPORT_REQUIRED_RETRY`，前者不消耗新的 work-unit attempt。
- [ ] **Step 5: 运行测试确认通过**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_gate_v2 tests.test_full_analysis_runtime -v
```

预期：条件回执和 calculation replay 修复只消耗 correction，不新增完整报告 attempt。

- [ ] **Step 6: 更新返工文档并提交**：

```bash
git add tools/full_analysis_gate.py tools/full_analysis_runtime.py scripts/full_analysis.py tests/fixtures/full_analysis/correction-bundle.json tests/test_full_analysis_gate_v2.py tests/test_full_analysis_runtime.py skills/full-company-analysis.md
git commit -m "feat(full-analysis): add targeted correction bundles"
```

---

### Task 3: 将语义评审简报改为引用级 compact payload

**Files:**
- Modify: `tools/full_analysis_review.py:126-171, 196-310`
- Modify: `tests/test_full_analysis_review.py`
- Create: `tests/fixtures/full_analysis/compact-review-manifest.json`
- Modify: `skills/full-company-analysis.md:139-206`

**Interfaces:**
- 将 `review-brief/v1` 升级为 `review-brief/v2`，保留 `--payload-mode full` 作为诊断兼容模式；默认使用 `compact`。
- compact brief 只保留：

```json
{
  "report": {"path": "...", "sha256": "...", "claim_sections": []},
  "evidence_index": {
    "facts": [{"fact_id": "...", "skill_id": "...", "source_ids": []}],
    "sources": [{"source_id": "...", "uri": "...", "published_at": "..."}],
    "calculations": [{"calculation_id": "...", "operation": "..."}],
    "judgments": [{"judgment_id": "...", "fact_ids": [], "calculation_ids": []}]
  },
  "evidence_path": "evidence/00-analysis-manifest.json",
  "evidence_sha256": "..."
}
```

- `claim_sections` 从正式报告按 `##` 标题提取有限长度的结论段落；不把完整报告正文复制到 JSON。
- delivery-summary 使用 summary 报告、产物索引和全局 evidence ID 索引，不再嵌入全部 receipts/role_runs。
- review Agent 读取 `evidence_path` 只读核对 ID；Gate 仍校验 `report_sha256` 和 `evidence_sha256`，防止 brief 与 manifest 脱节。

- [ ] **Step 1: 写失败测试**：使用当前 fixture 生成 v2 brief，验证关键 fact 引用保留、无关 receipt 不出现、单份 brief 字节数低于 v1。
- [ ] **Step 2: 运行测试确认失败**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_review -v
```

预期：当前实现仍输出完整 `report.content` 和全量 evidence。

- [ ] **Step 3: 实现 claim/evidence index 构建**：保留当前跨 skill fact 修复，但只加入判断或报告明确引用的 fact/source/calculation。
- [ ] **Step 4: 实现 v2 digest 绑定和 v1 full fallback**：默认 compact，发现旧 Agent 只支持 v1 时显式记录 `payload_mode=full`，不得静默降级。
- [ ] **Step 5: 运行测试确认通过并测量降幅**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_review -v
python3 tools/full_analysis_review.py prepare --run-root <fixture-run-root>
du -h <fixture-run-root>/evidence/review/review-brief-*.json
```

预期：评审结果 schema、digest、引用完整性全部 PASS；记录 v1/v2 总字节数。

- [ ] **Step 6: 更新文档并提交**：

```bash
git add tools/full_analysis_review.py tests/test_full_analysis_review.py tests/fixtures/full_analysis/compact-review-manifest.json skills/full-company-analysis.md
git commit -m "perf(full-analysis): compact semantic review payloads"
```

---

### Task 4: 缓存方法论与稳定指令前缀

**Files:**
- Modify: `tools/full_analysis_runtime.py:345-395`
- Modify: `scripts/full_analysis.py`
- Modify: `tests/test_full_analysis_runtime.py`
- Modify: `skills/full-company-analysis.md:48-54`

**Interfaces:**
- `next-work` 增加：`methodology_ref`、`methodology_sha256`、`methodology_mode`。
- 支持两种显式模式：

```text
methodology_mode=full  # 旧 Agent 兼容，发送全文
methodology_mode=ref   # 发送 spec 路径、hash、稳定指令和本次任务增量
```

- `ref` 模式仍保留完整规范的本地可读路径，但不把全文放入返回 payload；执行适配器必须在 Agent 侧按 hash 加载或使用提供商缓存。
- 同一 `methodology_sha256` 在同一 run 内只允许一次完整加载；重试只发送 correction/task delta。

- [ ] **Step 1: 写失败测试**：验证 `ref` 模式返回稳定 hash/path、payload 不含完整 skill 文本，`full` 模式保持兼容。
- [ ] **Step 2: 运行测试确认失败**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_runtime -v
```

- [ ] **Step 3: 实现 mode 选择和 hash 校验**：默认先在测试/诊断环境启用 `ref`，生产切换前要求适配器报告 `methodology_loaded=true`。
- [ ] **Step 4: 运行测试确认通过**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_runtime tests.test_full_analysis_cli -v
```

- [ ] **Step 5: 更新派发协议并提交**：

```bash
git add tools/full_analysis_runtime.py scripts/full_analysis.py tests/test_full_analysis_runtime.py skills/full-company-analysis.md
git commit -m "perf(full-analysis): cache methodology payloads"
```

---

### Task 5: 增加跨运行的 APPROVED 产物缓存

**Files:**
- Modify: `tools/full_analysis_runtime.py`
- Modify: `tools/full_analysis_gate.py`
- Modify: `scripts/full_analysis.py`
- Create: `tools/full_analysis_cache.py`
- Create: `tests/test_full_analysis_cache.py`
- Modify: `tests/test_full_analysis_e2e.py`
- Modify: `skills/full-company-analysis.md`

**Interfaces:**
- 缓存键：

```text
sha256(company_id + as_of + skill_id + methodology_sha256
       + input_evidence_digest + capability_profile)
```

- 新增只读查询：`cache-lookup --run-root <run_root> --skill-id <skill_id>`。
- `next-work` 先查询已 APPROVED 缓存；命中后由 Gate 重新校验 bundle、artifact hash、evidence 绑定，再登记为 `CACHE_REUSED`，不派 Agent。
- 缓存目录只保存受 Gate 接受的 bundle 和 artifact 副本；不保存 prompt、凭据或原始外部响应中的敏感字段。
- 以下情况强制失效：方法论 hash 变化、输入 evidence digest 变化、capability 变化、底层事实/计算被 correction 修改。

- [ ] **Step 1: 写失败测试**：第一次运行产生缓存；第二次相同输入命中；修改一个 fact 后只使相关 skill 失效。
- [ ] **Step 2: 运行测试确认失败**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_cache tests.test_full_analysis_e2e -v
```

- [ ] **Step 3: 实现 content-addressed cache**：只接受已 APPROVED 且 digest 完整的产物，复制后由当前 run 重新登记路径和 hash。
- [ ] **Step 4: 实现下游失效传播**：skill 变更使 summary 和相关 review 失效；无关 skill 保持 `CACHE_REUSED`。
- [ ] **Step 5: 运行测试确认通过并检查隐私边界**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_cache tests.test_full_analysis_e2e -v
python3 tools/report_audit.py --help
git status --short
```

- [ ] **Step 6: 提交**：

```bash
git add tools/full_analysis_cache.py tools/full_analysis_runtime.py tools/full_analysis_gate.py scripts/full_analysis.py tests/test_full_analysis_cache.py tests/test_full_analysis_e2e.py skills/full-company-analysis.md
git commit -m "perf(full-analysis): reuse approved skill artifacts"
```

---

### Task 6: 建立成本基准、预算告警和发布门槛

**Files:**
- Modify: `tools/full_analysis_benchmark.py`
- Modify: `tests/test_full_analysis_benchmark.py`
- Modify: `tools/full_analysis_gate.py`
- Modify: `skills/full-company-analysis.md`
- Create: `docs/full-analysis-cost-budget.md`

**Interfaces:**
- benchmark 输出固定指标：`input_tokens`、`output_tokens`、`input_bytes`、`output_bytes`、`attempt_count`、`correction_count`、`review_bytes`、`cache_hit_rate`、`wall_time_ms`，按 `work/summary/review/total` 汇总。
- Gate 只在超预算时给出明确 `COST_BUDGET_EXCEEDED`，不允许自动静默关闭质量校验。
- 初始建议阈值：
  - 同一 skill 完整 attempt 不超过 1 次；确定性证据错误优先走 correction。
  - compact review brief 必须小于 full 模式对应 payload。
  - 每次运行必须有 usage summary；只有 provider token 不可用时才允许 `tokens_unavailable`。

- [ ] **Step 1: 写失败测试**：构造超出 review bytes、重复 attempt、缺 usage summary 的 run，验证告警内容和 exit code 稳定。
- [ ] **Step 2: 运行测试确认失败**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_benchmark -v
```

- [ ] **Step 3: 实现 benchmark/预算汇总**：不改变 APPROVED 判定，只增加可观测性和显式告警。
- [ ] **Step 4: 更新运维文档**：记录 v3.3.4 基线和优化后的对照表，区分“本地 CPU 时间”和“LLM Token”。
- [ ] **Step 5: 完整验证**：

```bash
git diff --check
PYTHONPATH=tests python3 -m unittest discover -s tests -p 'test_*.py'
bash scripts/check.sh
```

预期：全量测试通过，生成物同步，Contract 校验通过，成本指标可从 run 目录复核。
- [ ] **Step 6: 提交**：

```bash
git add tools/full_analysis_benchmark.py tests/test_full_analysis_benchmark.py tools/full_analysis_gate.py skills/full-company-analysis.md docs/full-analysis-cost-budget.md
git commit -m "chore(full-analysis): enforce measurable cost budgets"
```

---

### Task 7: 封装 rework 命令（C 层 E9，替代手编 runtime-state）

> **立项背景（2026-08-01）**：雅克 run 的返工全部通过手工改写 `evidence/runtime-state.json`（DONE→PENDING + 清 lease）完成，手编状态文件是事故源（一次 wu 与 attempt 错配污染了两个单元的租约）。E9 把该操作封装为受校验的命令，与 Task 2 的 correction 分工：**确定性证据错误走 correction（不耗 attempt），报告正文/artifact 问题才走 rework（耗一次 attempt）**。

**Files:**
- Modify: `tools/full_analysis_runtime.py`（rework 状态机、租约清理、事件）
- Modify: `scripts/full_analysis.py`（新增 `rework` 子命令）
- Modify: `tools/full_analysis_gate.py`（run.rework_count 只读汇总，不参与 APPROVED 判定）
- Create: `tests/test_full_analysis_rework.py`
- Modify: `skills/full-company-analysis.md`（返工协议与 Task 2 共用章节，合并编辑避免冲突）

**Interfaces:**
- 新增 CLI：

```text
python3 scripts/full_analysis.py rework \
  --run-root <run_root> --work-unit-id <work_unit_id> [--reason <string>]
```

- 语义：`DONE`/`PARTIAL` → `PENDING` + 清 lease + 追加 `rework_initiated` 事件（含 reason）+ 旧 attempt 记入 `abandoned_attempts`（供编排器复用产物）+ `run.rework_count += 1`。
- 防呆：目标单元必须已存在被 Gate 接受的 attempt（有 base_attempt）；`PENDING`/`LEASED`/从未派发的单元 `rework` 直接拒绝（rc=1 + 明确错误）。
- 联动：rework 后 `next-work` 返回的新 attempt 携带 `reuse_base_attempt`（旧 attempt id），编排器可像雅克 run 那样复用 report/role-memo/raw，只更新身份字段与 artifact 哈希——把「手编+复制」降级为「一条命令 + 一条复用规则」。

- [ ] **Step 1: 写失败测试**：①DONE 单元 rework → PENDING + 清 lease + `rework_initiated` 事件 + abandoned 记录；②PENDING 单元 rework 被拒 rc=1；③rework 后 next-work 返回 `reuse_base_attempt`。
- [ ] **Step 2: 运行测试确认失败**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_rework -v
```

预期：`rework` 子命令不存在（rc=2）或直接改状态文件。
- [ ] **Step 3: 实现状态机与事件**：复用现有 lease/event 写入路径，不得绕过锁；`rework_count` 仅作可观测性。
- [ ] **Step 4: 运行测试确认通过**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_rework tests.test_full_analysis_runtime -v
```

- [ ] **Step 5: 更新 skill 返工协议并提交**：

```bash
git add tools/full_analysis_runtime.py scripts/full_analysis.py tools/full_analysis_gate.py tests/test_full_analysis_rework.py skills/full-company-analysis.md
git commit -m "feat(full-analysis): add rework command for report-level retries"
```

---

### Task 8: finalize 契约版本钉死（C 层 E10，防过期 run 准出）

> **立项背景（2026-08-01）**：雅克首轮 run 基于旧 HEAD（c7e00c3）启动，用户 06:55 提交最新版编排（ashare-data L0-L3 分级 + finalize 修复）后，旧 run 若无人发现就会被 APPROVED。E1（A 层）是编排器启动前自查（文档纪律），E10 是机器强制：**finalize 时校验 run 契约 digest == 当前契约 digest，不一致拒绝准出**。

**Files:**
- Modify: `tools/full_analysis_runtime.py`（start 时记录 contract digest 与 HEAD commit）
- Modify: `tools/full_analysis_gate.py` 或 `tools/full_analysis_runtime.py` 的 finalize 入口（准出前校验）
- Modify: `scripts/full_analysis.py`（finalize 校验错误码）
- Create: `tests/test_full_analysis_runtime.py` 追加用例 或 `tests/test_full_analysis_e2e.py`
- Modify: `skills/full-company-analysis.md`（启动校验节，与 E1 互补说明）

**Interfaces:**
- `start` 写入 `manifest.run.contract_digest`（`sha256(tools/full_analysis_contract.json)`）与 `manifest.run.contract_commit`（`git rev-parse HEAD`，无 git 时为 null）。
- `finalize` 重算当前契约 digest；与 run 记录不一致 → rc=1 + 明确错误码 `CONTRACT_VERSION_MISMATCH` + 提示「用最新版契约重新 start，产物仍在旧 run 目录可复用（按 E9 复用规则）」。
- 兼容：契约文件缺失/损坏时按不一致处理；`--force` 不提供（过期 run 不应有绕过路径）。

- [ ] **Step 1: 写失败测试**：①start 后 manifest 含 contract_digest/contract_commit；②改动契约副本后 finalize 拒绝 rc=1 且错误码为 `CONTRACT_VERSION_MISMATCH`；③契约一致时 finalize 正常。
- [ ] **Step 2: 运行测试确认失败**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_e2e -v
```

- [ ] **Step 3: 实现 start 记录与 finalize 校验**：digest 计算用现有 `hashlib`，不新增依赖；校验点放在 audit 快照之后、准出写盘之前。
- [ ] **Step 4: 运行测试确认通过**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_e2e tests.test_full_analysis_runtime -v
```

- [ ] **Step 5: 更新 skill 并提交**：

```bash
git add tools/full_analysis_runtime.py tools/full_analysis_gate.py scripts/full_analysis.py tests/test_full_analysis_e2e.py skills/full-company-analysis.md
git commit -m "feat(full-analysis): pin contract digest at finalize"
```

---

### Task 9: 评审 finding 源头回写（C 层 E13，季度修复清单）

> **立项背景（2026-08-01）**：紫金 run 的 8 条评审 findings（1 medium + 7 low）与雅克 run 的 13 medium/16 low 大部分是**传播型笔误**（FCF 口径、A 股腿市值 10 倍笔误、股东户数基数错误等），源头在子 Agent 备忘录或管线输出，只落盘 `evidence/review/` 无法回流到源头。E13 让 finding 携带 `fix_source`，并支持导出季度修复清单，下次 run 前修源头。

**Files:**
- Modify: `tools/full_analysis_review.py`（ingest 时收集 fix_source、新增 fix-list 导出）
- Modify: `scripts/full_analysis.py`（新增 `review fix-list` 转发）
- Create: `docs/full-analysis-review-fix-list.md`（清单格式模板）
- Modify: `tests/test_full_analysis_review.py`
- Modify: `skills/full-company-analysis.md`（评审协议：finding 必须填 fix_source）

**Interfaces:**
- `review-result/v1` 增加可选字段 `fix_source`：`{"file": "<相对 run_root 路径>", "line_approx": <int|null>, "kind": "report"|"role_memo"|"pipeline_raw"|"methodology", "note": "<一句话>"}`——指向问题**源头**（子 Agent 备忘录/管线 raw/正式报告），而非评审结果本身。
- `review ingest` 时聚合 `fix_sources` 写入 `review-index.json`。
- 新增 CLI：

```text
python3 scripts/full_analysis.py review fix-list --run-root <run_root> [--severity high|medium|low] [--out <path>]
```

- 输出 Markdown 清单：按 `kind` 分组（pipeline_raw → 修数据源/脚本；role_memo → 下次 run 修派发提示；report → 走 rework）；每条含 skill、severity、fix_source、原始 finding 描述。缺 `fix_source` 的 finding 单独归入 `UNFIXED` 组并在 summarize 提示。
- 低严重度笔误不进返工链（沿用「low 不阻断 REVIEW_PASSED」规则），由清单在季度复核或下次 run 前批量修复。

- [ ] **Step 1: 写失败测试**：①带 fix_source 的 review-result ingest 后 index 聚合正确；②fix-list 按 severity/kind 分组输出；③缺 fix_source 的 finding 归入 UNFIXED 且 summarize 提示。
- [ ] **Step 2: 运行测试确认失败**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_review -v
```

- [ ] **Step 3: 实现 fix_source schema 校验与聚合**：复用现有 review-result schema 校验路径；缺省时允许为 null（不强制已有工具链重写评审结果）。
- [ ] **Step 4: 实现 fix-list 导出**：Markdown 输出到 `docs/full-analysis-review-fix-list.md` 或 `--out` 指定路径；不修改任何证据文件。
- [ ] **Step 5: 运行测试确认通过并提交**：

```bash
PYTHONPATH=tests python3 -m unittest tests.test_full_analysis_review -v
git add tools/full_analysis_review.py scripts/full_analysis.py docs/full-analysis-review-fix-list.md tests/test_full_analysis_review.py skills/full-company-analysis.md
git commit -m "feat(full-analysis): export review findings fix-source list"
```

---

## 实施顺序与发布策略

建议按以下顺序分三个可回滚版本发布（C 层可靠性项并入节奏，不单独开版）：

1. **v3.3.5：只加计量和 correction bundle + 可靠性前置（Task 1/2/7/8）**。不改变默认 payload，不改变评审范围；目标是消除确定性错误导致的整单重跑，并把「返工命令封装（E9）」与「契约版本钉死（E10）」作为可靠性底线先行落地——E10 直接防住「过期编排 run 被 APPROVED」类事故，宜早不宜迟。
2. **v3.3.6：启用 compact review payload 和 methodology ref（Task 3/4）**。保留 `full` 回退模式；先在一家公司样本上对比 v1/v2 字节数、评审 verdict 和 evidence 引用完整性。E13 评审源头回写（Task 9）随本版本评审链路改造一起收口（review-result schema 同步升级）。
3. **v3.3.7：启用 APPROVED cache 和增量失效（Task 5）+ 成本基准门槛（Task 6）**。先只读命中、人工确认无误后再默认复用；budget 告警只在超限时显式提示，不静默降级质量。

暂不建议直接减少 skill 数量、关闭语义评审或降低 evidence_rules 下限；这会把 Token 节省建立在不可量化的质量损失上。

## 完成判定

- 真实 usage 可按 run、phase、skill、attempt 查询。
- 条件回执/计算重放类错误不再触发完整报告重写（correction，Task 2）；报告正文类返工走 `rework` 命令且不再手编 runtime-state（Task 7）。
- compact review payload 的字节数和 Token 数可与 full 模式直接对比。
- 相同输入的第二次运行能够命中 approved cache，输入变化能够精确失效。
- **finalize 拒绝契约 digest 不一致的过期 run（Task 8），无 `--force` 绕过路径。**
- **评审 findings 可导出按 kind/severity 分组的源头修复清单（Task 9），缺 fix_source 的 finding 被显式标记。**
- `bash scripts/check.sh` 和全量单元测试通过，且 APPROVED/REVIEW_REQUIRED 结果与优化前保持一致。
