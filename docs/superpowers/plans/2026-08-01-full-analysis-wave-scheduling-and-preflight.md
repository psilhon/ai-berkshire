# 全量分析管线提速方案：波次调度 + 派发前门禁

- 日期：2026-08-01
- 版本目标：v3.3.9（波次调度）+ v3.3.9 同批（预提交门禁）
- 实证基准：沪电股份 run-de464d091c75c839（`local/Company/002463.SZ-沪电股份/20260801-123708-78bd79`），13/13 DONE，audit 52/52 PASS，review 8/8（3 轮返工），墙钟 192.4 分。
- 上游：`docs/superpowers/plans/2026-07-30-full-analysis-token-cost.md`（v3.3.5/6/7 可靠性，已交付）。本方案只动调度与门禁，不动可靠性契约（correction/rework/缓存/finalize 复核保持原样）。

---

## 一、诊断：192 分花在哪，以及代码事实锚点

沪电 run 的墙钟结构（研究阶段串行 + 三轮返工是主矛盾）：

| 阶段 | 墙钟 | 性质 | 代码锚点 |
|---|---|---|---|
| ashare-data | ~1.5 分 | 数据底座，所有下游依赖 | `gate.cmd_init` 按 registry 顺序生成 work_units |
| 下游 12 单元 | ~142 分 | **被 registry 顺序串成一条线** | `runtime._next_work_locked` L305-414，`unit = candidates[0]` |
| review 返工 | ~36 分 | 口径/数字不可复算 → 3 轮 | `audit._replay_calculation_requests` L145-181 |
| 派发间隙/租约回收 | ~13 分 | sweep + 无批量派发 | `runtime._sweep_expired_leases` L237-295 |

### 关键代码事实（勘察确认）

1. **调度是纯线性**：`_next_work_locked` 从 `state["work_units"]` 取第一个 PENDING/RETRY_WAIT（L337），`concurrency.max=4`（gate L525）只是并发上限，但 next-work **单次只发一个租约**，靠编排器多次调用驱动。无预取、无批量派发。

2. **contract 无任何依赖字段**：`tools/full_analysis_contract.json` 每 skill 只有 skill_id/category/stage_dir/spec_source/sections/evidence_rules/roles…，**不存在 depends_on/inputs/upstream**（已全量扫描）。ashare 的 `conditional_command_operations.feeds` 隐含下游但不被调度读取。→ 波次调度必须**新增依赖声明**。

3. **E4 预校验存在但覆盖不足**：`gate._precheck_evidence_rules` L363-414（submit 时、ingest 前）校验必需 field/rule_id/capability/falsification。但**拼错成"另一个合法格式名"会漏过**，最终到 audit 以 `missing_required_*` 拒（correctable=True，走 submit-correction）。→ 这是返工源头之一，可前移。

4. **audit 是事后参数校验**：`audit._replay_calculation_requests` 拼 argv 调 `financial_rigor.py`（parser L832-879，three-scenario 用 `nargs=3`，参数错 rc=2）。cross-validate rc=1 记 CONFLICT。**笔误要到 audit 才暴露**，此时产物已写盘、attempt 已消耗。→ 可在 submit 前加一道 parser dry-run。

5. **ashare 回执无硬门禁**：result.json 有 `command_receipts[]`（receipt_id/operation/status），但 gate 仅 last-write-wins 合并（L735-750），**不校验"每个已执行命令都有回执 + 禁用白名单外操作"**。白名单实为契约 op 清单，由 audit `_eval_required_command_operations` L244-248 事后核满足率（0.7）。→ 漏报要等到 audit。

6. **fanout 租约 TTL=20 分 vs 实际时长**：`LEASE_MINUTES=20`（L28），fanout（team/earnings=[duan,buffett,munger,li]，news-pulse=4 角色+integrator）要在一个租约内串行跑完多 role，易超时。心跳 `_heartbeat_locked` L483 可续期，但**子 Agent 派发层未周期性回调** → `_sweep_expired_leases` 判 heartbeat_lost 回收。本次 3 个 fanout 单元都因此过期重租。

7. **限流退避已有雏形**：`runtime._record_failure_locked` L501-522，`reason=="rate_limit"` 时 `concurrency.max=1` + 全局冷却 600s。ashare transport L66-71 把 429/403 归 `rate_limited`（仅分类，不重试）。`model:"lite"` 绕行**不在代码**，写在 `skills/full-company-analysis.md` L57 由编排提示层决定。→ 并行化必须复用并强化这套退避。

---

## 二、综合优化方案（按性价比 + 风险排序）

### 设计原则

- **先门禁，后并行**：门禁改动小、收益确定、零并行风险，且能根治"一个笔误毁全量"；并行收益最大但需要改编排 + 扛限流，作为第二步。
- **并行度从 4 起步**，配合既有限流退避；数据类轻单元大胆并行，重单元（earnings/team）错峰。
- **不动可靠性契约**：correction/rework/缓存/finalize 复核全部保持 v3.3.7 行为，本方案是纯加法。

### P0-A：派发前参数预校验（submit 前 parser dry-run）—— 最先做

**目标**：把 audit 才暴露的 calc/cross-validate/three-scenario 参数笔误，前移到 submit 阶段当场拒回 Agent，不进 audit、不耗 attempt。

**落点**：`gate.validate_result_bundle`（L359 附近，E4 之后）新增 `_precheck_calculation_params`：
- 遍历 result 的 `calculations[]`，对每条 request 复用 `financial_rigor.py` 的 argparse（import 其 build_parser，`parse_known_args` 不执行计算，仅验参数合法性），参数错（rc=2 等价）→ 抛 `GateError`，把 argv + 期望格式回传 Agent。
- 复用 audit 已有的 `_replay_calculation_requests` 参数拼装逻辑，抽成共享 helper（`tools/financial_rigor.py` 或 gate 内），避免两处漂移。

**收益**：~30 分（本次 3 轮返工中约一半源于参数/字段笔误）。**难度：低**（一道门禁 + 一个 dry-run）。

**风险**：dry-run 必须与 audit 真执行**逐字节同参数**，否则"预校验过、audit 挂"更糟。→ TDD 红线：同一 request，preflight 判定必须与 audit replay 的 rc 一致（用沪电 52 条 calc 做黄金回归）。

### P0-B：依赖波次并行调度 —— 收益最大

**目标**：把 registry 线性顺序升级为依赖图波次，每波同时派 3-4 个 Agent。

**波次设计**（依据真实数据依赖）：

```
W1  ashare-data                         （数据底座，~1.5 分）
W2  financial-data, quality-screen,
    investment-checklist,
    investment-research                 （轻数据单元，~6.8 分关键路径）
W3  investment-team, management-deep-dive,
    earnings-review, industry-research  （重/扇出单元，~14.3 分）
W4  industry-funnel, bottleneck-hunter,
    news-pulse                          （汇总/扇出，~13.3 分）
W5  thesis-tracker                      （终局综合，~6.4 分）
```

关键路径 = W1 1.5 + W2 6.8 + W3 14.3 + W4 13.3 + W5 6.4 ≈ **42 分**（研究阶段从 142 分降至此）。

**落点**：
1. `contract.json` 每 skill 增 `depends_on: [skill_id…]`（向后兼容：缺省视为仅依赖 ashare）。
2. `runtime._next_work_locked`：候选从"第一个 PENDING"改为"所有 PENDING 且 depends_on 全 DONE 的单元"，在 `concurrency.max` 上限内**一次返回多个租约**（新增批量派发返回结构，向后兼容单租约）。
3. `scripts/full_analysis.py next-work`：新增 `--suggest-batch` / 默认批量返回；编排器按批派发。
4. 波次内顺序按 `roles` 扇出开销错峰：重单元（earnings/team）与轻单元混编，避免峰值全扇出。

**收益**：~90 分。**难度：中**（依赖声明 + 批量租约 + 编排器适配）。

**风险与对策**：
- **峰值 8-10 Agent → 429**：复用 `_record_failure_locked` 的 rate_limit 退避，并把 `concurrency.max` 起步设 4；数据类轻单元可并行，earnings/team 在波次内错峰（W3 内部再分两小批）。
- **依赖声明错误 → 死锁/越级**：TDD 必须覆盖（a）环检测拒绝 init，（b）depends_on 未完成绝不放行，（c）缺省回退 ashare-only。
- **向后兼容**：旧 run（无 depends_on）必须仍能线性跑完。

### P1：ashare 回执完整性门禁

**目标**：result.json 提交时校验"每个已执行命令都有回执 + 无白名单外操作"，把漏报挡在 audit 前。

**落点**：`gate.validate_result_bundle` 增 `_precheck_command_receipts`：
- 对照契约 ashare 的 `required_command_operations` + `conditional_command_operations` op 清单（白名单），校验 receipts 覆盖率与 op 合法性；缺回执/出现白名单外 op → 抛 `GateError`。
- 复用 audit `_eval_required_command_operations` L244-248 的 op 清单解析，抽共享 helper。

**收益**：~20 分。**难度：低**。

### P2：收紧派发间隙

**目标**：批量登记 job-started、心跳合并，减少 sweep 空转。

**落点**：`runtime` 增 `job-started --batch`（一次登记多个 attempt）；心跳随批量 next-work 顺带续期。**收益：~5 分。难度：低。** 可与 P0-B 批量派发同批落地。

### 附：fanout 租约续期（随 P0-B 一起修，非独立项）

本次 3 个 fanout 单元全部因 TTL=20 分超时被回收。对策：fanout 单元（`roles.mode=="independent_then_integrator"`）派发时 TTL 按 `len(required_roles)+1` 倍增，并在 SKILL.md 编排层要求每完成一个 role 回调一次 `heartbeat`。此项不单列 release，随波次调度同批。

---

## 三、限流权衡（必须写进编排层）

并行化不是免费的。三个 fanout 单元内部已是"多角色独立上下文"，叠加单元级并行峰值 8-10 Agent，必触发上游 429（此前靠 `model:"lite"` 绕行）。落地纪律：

1. `concurrency.max` 起步 4，**不一次拉满**；
2. 数据类轻单元（ashare/financial/quality/checklist）大胆并行；重单元（earnings/team）在 W3 内错峰分两小批；
3. 429 触发时沿用 rate_limit 全局冷却 600s + 降并发至 1（已有逻辑），编排层叠加 `model:"lite"` 降级；
4. 每个 fanout 单元派发后周期性 heartbeat，防 sweep 回收。

---

## 四、分阶段路线图与 TDD 任务分解

全部遵循仓库六步 TDD + `bash scripts/check.sh` 全绿门禁。改动 gate/runtime/contract 后必跑 check.sh。

### Release v3.3.9（门禁批，先做，零并行风险）

| Task | 内容 | 验证红线 |
|---|---|---|
| T1 | P0-A 参数预校验：抽 `financial_rigor` parser 为共享 helper，gate `_precheck_calculation_params` dry-run | 沪电 52 条 calc 黄金回归：preflight 判定 == audit replay rc；笔误 request submit 即拒、不进 audit |
| T2 | P1 ashare 回执门禁：`_precheck_command_receipts` + 共享 op 清单 helper | 缺回执/白名单外 op 在 submit 拒；合法回执放行；与 audit op 清单逐字节一致 |
| T3 | 门禁回传格式：GateError 携带 argv/期望格式/缺失 op，编排层可原地让 Agent 改 | 回传信息足够 Agent 一次改对（用沪电真实返工样例回放） |

> **v3.3.9 已交付（2026-08-01）**：T1/T2/T3 全部落地，check.sh 全绿（632 测试，较基线 +14）。
> 落地形态：`financial_rigor.build_rigor_argv` + `preflight_diagnose_params` 为 gate 与 audit
> 共用的 argv 拼装单一真源（audit 手拼逻辑已删除，`test_build_argv_matches_legacy_audit_handassembly`
> 黄金测试防漂移）；gate `validate_result_bundle` 在 E4 后聚合跑 `_precheck_calculation_params`
> （rc=2 参数错）+ `_precheck_command_receipts`（白名单外 PASS 操作），单次抛错、Agent 一轮修完。
> 门禁只拦确定性错误（参数笔误/虚构成功），豁免与满足率仍由 audit 权威判定，不判重。
> 未提交，待用户决定；打 tag 前可先跑沪电重放验证端到端收益。

### Release v3.3.10（波次调度批，第二步）

| Task | 内容 | 验证红线 |
|---|---|---|
| T4 | contract `depends_on` 声明 + init 环检测 | 13 skill 依赖图无环；缺省回退 ashare-only；旧 run 兼容 |
| T5 | `_next_work_locked` 依赖图 + 批量租约（concurrency.max 内多租约） | depends_on 未完成绝不放行；批量返回 ≤ max；单租约向后兼容 |
| T6 | `next-work --suggest-batch` CLI + 编排器适配 | 波次划分 == W1-W5 设计；每波 3-4 个 |
| T7 | fanout TTL 倍增 + heartbeat 编排纪律 | fanout 单元不再被 sweep 误回收（沪电 3 单元回放） |
| T8 | 限流退避强化：concurrency 起步 4 + W3 错峰 + lite 降级写进 SKILL.md | 429 注入测试：降并发 + 冷却生效，不死锁 |

> **v3.3.10 已交付（2026-08-01）**：T4/T5/T7/T8 落地，check.sh 全绿（649 测试，较 v3.3.9 +17）。
> - **T4**：contract 13 skill 增 `depends_on`（ashare 为根，缺省回退 ashare-only）；runtime 纯函数
>   `build_dependency_graph`/`detect_dependency_cycle`/`compute_dependency_waves` 为依赖图单一所有者；
>   `gate.cmd_init` 环检测 + 持久化 `dependency_graph`/`dependency_waves` 进 state、每 work_unit 带 depends_on；
>   契约校验器自包含校验 depends_on 引用合法 + 无环（刻意不 import runtime）。
> - **T5**：`_next_work_locked` 依赖门禁——候选过滤为「depends_on 全 DONE」的就绪单元，NO_WORK 细分
>   `DEPENDENCIES_PENDING`/`QUEUE_EMPTY`。**未做多租约批量**：编排器循环填充 + 单租约返回即可在并发上限内
>   实现波次并行，且保持单租约形状最大向后兼容。W3 错峰经依赖设计精确涌现 W1-W5 五波（测试锁定）。
> - **T7**：`_lease_ttl_for_skill` fanout 单元租约 TTL = 20 × 独立角色数（team/earnings 4 角色 = 80 分），
>   lease 存 `lease_ttl_minutes`，heartbeat 按存储 TTL 续期——根治沪电 run 三个扇出单元被 sweep 误回收。
> - **T8**：方法论四份副本（skills/workbuddy-skills/codex-skills/~/.workbuddy）同步「波次并行派发」纪律。
>   **关键修正**：并行不用后台（后台不返回 job_id → 租约过期 requeue 灾难），而是「一条消息里多个前台
>   Agent 并行派发」；W3 重单元错峰 + 429 lite 降级写进 E2 纪律。
> - **T6（--suggest-batch CLI）取消**：编排器 `next-work` 循环填充已隐式提供波次批次，无需独立 CLI。
> 未提交，待用户决定；端到端墙钟收益须靠沪电重放验证（见验证总门禁）。

### 验证总门禁

- 每个 Task 后 `bash scripts/check.sh` 全绿（当前基线 615 测试）。
- v3.3.10 完成后，用**沪电同一 as_of 重跑**做端到端回归：墙钟应从 192.4 分降至 **~70 分（±15 分）**，且 audit/review/finalize 结论与本次 APPROVED 一致（产物可缓存命中）。
- 三份 skill 文档（`skills/full-company-analysis.md` = workbuddy-skills = ~/.workbuddy）同步，改源后跑 `scripts/sync-codex-skills.py`。

---

## 五、预期收益汇总

| 措施 | 节省 | 难度 | release |
|---|---|---|---|
| P0-A 参数预校验 | ~30 分 | 低 | v3.3.9 |
| P1 回执门禁 | ~20 分 | 低 | v3.3.9 |
| P2 派发间隙 | ~5 分 | 低 | v3.3.10 |
| P0-B 波次并行 | ~90 分 | 中 | v3.3.10 |
| **合计** | **~145 分** | | 墙钟 192 → ~70 分（≈2.7×） |

理论墙钟 ~70 分；研究阶段关键路径从 142 分降到 ~42 分。保守口径（含限流退避损耗）落地 ~70-90 分。

---

## 六、开工建议

**先做 v3.3.9（T1-T3）**：改动小、收益确定（~50 分）、零并行风险，且根治"一个笔误毁全量"这类事故。**并行化（v3.3.10）作为第二步**，收益最大但需扛限流与依赖正确性。
