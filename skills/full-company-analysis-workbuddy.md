---
name: full-company-analysis-workbuddy
description: WorkBuddy 单公司全量分析编排器（lean 模式）。对一家公司执行 13 业务 skill 端到端研究，产出 13 份真实研究报告 + HTML 交付件。触发词：全量分析 <公司名>、/full-company-analysis-workbuddy <公司名>、全量跑 <公司名>。两条底线：内容质量 + 失败必须显式声明。报告是唯一交付物，不追求速度、允许失败。
platform: workbuddy
registry-schema: full-analysis-contract/lean-v1
result-schema: result-schema/v1
owner: psilhon
category: 编排层
maturity: beta
review-cadence: per-release
---

# WorkBuddy 全量公司分析编排器（lean 模式）

## 核心哲学（两条底线）

本编排器只保证两件事：

1. **内容质量**：每份研究报告必须基于真实数据、遵循方法论、声明数据截止日 / 来源 / 免责。
2. **失败显式声明**：任何单元做不出来、数据缺失、推理不成立，必须显式 `mark-failed` 声明，**不静默跳过、不自动重试、不伪造占位**。

其余一切（速度、租约看门狗与租约身份机、波次错峰白名单、证据账本 PLACEHOLDER、双源强制、版本钉死、自动恢复、job-started/heartbeat/record-failure 命令）已从流水线移除或降级为可选。报告是**唯一交付物**；证据账本（result.json）只是可选辅助，空账本合法，绝不合成 PLACEHOLDER 占位。

> **为什么去掉冗余检查**：历史版本逐代叠加租约看门狗、波次错峰、证据账本 PLACEHOLDER 自动填充、双源强制、版本钉死等防御机制，目标是「让 run 不卡住、过 Gate」。但这些机制优化的是「流程不报错」而非「研究更深」，且 PLACEHOLDER 合成会伪造证据。lean 模式回退到本质：真实研究 + 显式失败声明。

## 启动

1. **定基线**：执行本地 `date`，把日期作为 `as_of` 基线（不得用训练记忆假设日期）。记录数据截止日，报告头必须声明。
2. **启动 run**：

```text
python3 scripts/full_analysis.py start --company <公司名> --code <证券代码> --as-of <YYYY-MM-DD>
```

   只从返回的 `run_root` 继续。契约 `tools/full_analysis_contract.json`（schema `full-analysis-contract/lean-v1`）是 13 项业务 skill、依赖关系、正式产物路径、报告指引的唯一机器真源；不要在本适配器中复制清单。

## 派发循环（核心执行）

按依赖顺序逐个单元派发原生 Agent。**依赖由 `next-work` 自动按其 `depends_on` 拓扑返回就绪单元**，无需手动错峰或白名单。

```text
python3 scripts/full_analysis.py next-work --run-root <run_root>
# 返回 LEASED 单元 → 派发原生 Agent 完成 → submit-result
# 返回 NO_WORK  → 全部就绪单元已派发完毕
```

**派发纪律（最小集）**：
1. 每次 `next-work` 返回 `LEASED` 后，用 **WorkBuddy 原生 Agent** 完成该 work unit。禁止用 Python/shell 再创建 Agent，禁止由主上下文直接撰写分析正文（主上下文只做调度——独立 Agent 有新鲜上下文与外部调研能力，是质量稳定的物理前提）。
2. 把 `next-work` payload 的 `methodology_text`（`skills/<skill_id>.md` 完整方法论）作为 Agent 的**强制规范**完整落地，不得仅凭 skill 名称凭记忆发挥。
3. Agent 把报告写入 attempt 目录与契约指定的正式产物路径（`artifact.formal_path`）。
4. 🔴 **CHECKPOINT · 移交前质量门（优化点 1）**：Agent 在调 `mk_result_bundle` / `submit-result` **之前**，必须先对产出报告跑自我校验（未过 self-check 不得移交，质量由 skill 自身在移交前确保）：
   ```text
   python3 scripts/full_analysis.py self-check \
     --run-root <run_root> --skill-id <skill_id> --report <attempt_dir>/report.md
   ```
   退出码 0 = 通过；非 0 = 发现实质错误（实质章节数 / 分歧交锋 / 标题占比 / 数据截止日·来源·免责三锚 / 字节下限），Agent 必须**修复报告后重跑 self-check**，或显式 `mark-failed` 声明放弃。**质量由 skill 自身在移交前确保**，Gate 仅在 submit-result 再做一次边界兜底（同一套 `_substance_errors`），双重保险但不重复劳动。
5. Agent 自我校验通过后调用 `submit-result`（如用生成器，运行 `mk_result_bundle` 生成 result.json；空账本合法，无 PLACEHOLDER）。

> 编排已极简（优化点 2）：lean 模式**完全没有租约 / job-started / heartbeat / record-failure / 波次白名单**。next-work 直接返回就绪单元，Agent 完成后尽快 submit-result 即可；不存在租约过期被拒的情形，也无需轮询或等待。

**多角色 skill 真扇出**：当 `roles.mode == "fanout"` 时，为 `required_roles` 中每个角色启动独立原生 Agent，各自产出角色备忘录，最后由整合 Agent 读取全部备忘录产出正式报告（多角色备忘录缺失也会被 self-check / Gate 拦截）。

## 报告质量要求（第一条底线）

每份报告必须满足契约 `substance` 三锚 + `artifact.min_bytes` + `min_substantive_sections`，否则 Gate 判为不达标：

- **数据截止日**：报告须含 `YYYY-MM-DD` 形式日期（来自 `as_of`），声明数据新鲜度。
- **来源声明**：须标注数据直接来源（Tushare / 东方财富 / 腾讯行情 / 巨潮 / WebSearch 等），不得无源断言。
- **仅供学习研究声明**：报告须含「仅供学习研究 / 免责 / 非投资建议」类声明。
- **实质深度**：遵循契约 `report_guidance` 的自然章节结构撰写（不强制固定标题），章节须有真实正文（`min_substantive_sections` 个以上实质章节，每章 ≥150 字），不得凑数、不得 PLACEHOLDER 占位。
- **真实数据**：所有数字须来自真实取数（数据管线 / WebSearch / 一手财报），禁止编造、禁止用占位符填充。

`next-work` payload 的 `report_guidance` 给出该 skill 的自然结构提示（如「数据截止日、直接来源、核心结论、关键数据表、限制与缺口、仅供学习研究声明」），Agent 据此组织内容，**不强制固定标题**。

## 失败处理（第二条底线，核心）

🛑 **STOP · 失败显式声明点（第二条底线）**：**允许任务失败。失败必须显式声明，绝不静默。**

当 Agent 无法完成某单元（数据缺失、推理不成立、取数失败、超时中断）时：

```text
python3 scripts/full_analysis.py mark-failed \
  --run-root <run_root> --skill-id <skill_id> \
  --reason "<具体失败原因：缺什么数据 / 哪步推理不成立 / 取数失败>"
# 可选重试（重新排队一次）：追加 --retry
```

- `mark-failed` 在 run 状态中记录该单元 `FAILED` + 失败原因 + 声明时间，**向用户显式报告**哪些单元失败、为何失败。
- **不自动重试、不启动看门狗、不做孤儿恢复**。如需重试，显式加 `--retry` 重新排队一次。
- 失败的单元不计入交付；最终交付物（HTML/报告集）明确标注哪些单元缺失及原因。

> 已移除的机制（勿再用）：sweep 看门狗、resume 孤儿恢复、heartbeat 续租、**租约身份机（lease nonce / agent_job_id 校验）及其 job-started / heartbeat / record-failure 命令**、波次错峰白名单、E1/E10 版本钉死、evidence_rules 账本核对、PLACEHOLDER 自动填充。这些曾为「不让 run 卡住」而存在，但牺牲了质量与诚实。质量现已由每个 skill 的 `self-check` 在移交前确保，Gate 仅做边界兜底。

## 收口与交付（L1 主线）

所有可完成的单元 DONE 后，编排器执行：

```text
# 步骤 A：派 deep-summary Agent（独立原生 Agent），读取已完成的正式报告，
# 忠实熔炼为总结报告 <run_root>/evidence/attempts/summary/summary.md
# （只读只提炼，不取新数、不新推理；缺失单元在总结中显式说明）

# 步骤 B：登记并冻结总结
python3 scripts/full_analysis.py register-summary \
  --run-root <run_root> --summary <run_root>/evidence/attempts/summary/summary.md

# 步骤 B2：确定性渲染 HTML 展示件（非阻断，不依赖 audit）
python3 scripts/full_analysis.py render-html --run-root <run_root>
```

- **HTML 是确定性展示件**：同一 markdown 逐字节一致渲染，零 LLM、零方差，品质不随 run 漂移。
- 总结报告须如实标注缺失单元（来自 mark-failed 记录），不得隐瞒失败。

## 可选评估层（需要验证时执行）

L1 交付不强制评估层。需要质量验证 / 对外正式交付 / benchmark 时，按序执行（各自独立可跑）：

- **L2 结构验证**：`audit`（报告 substance 底线 + 字节/章节核对）→ 不达标则补写报告后重跑。
- **L3 语义评估**：`review prepare` → 派评审 Agent（五维）→ `review ingest` → `review summarize`。
- **L4 准出与体检**：`finalize`（契约 digest 校验）+ `doctor`（退化指纹，advisory 非阻断）。

评估层只做校验与准出，不改变 L1 交付内容。失败单元在评估前已显式声明，评估层不重复掩盖。

## 精简禁止事项

| # | 禁令 | 后果 |
|---|------|------|
| 禁-A | 禁止主上下文直接撰写分析正文（须派独立原生 Agent） | 上下文坍塌，质量退化 |
| 禁-B | 禁止编造数据 / 用 PLACEHOLDER 占位填充报告 | 虚假研究，误导决策 |
| 禁-C | 禁止无源断言（数字须标注真实来源） | 不可追溯 |
| 禁-D | 禁止用训练记忆假设日期（须执行 `date`） | 数据基线错位 |
| 禁-E | 禁止静默跳过失败单元（必须 `mark-failed` 显式声明） | 隐瞒缺陷 |
| 禁-F | 禁止仅凭 skill 名称凭记忆发挥（须完整落地 methodology_text） | 章节/证据缺失 |
| 禁-G | 禁止启动 sweep 看门狗 / resume 孤儿恢复（已移除机制） | 静默卡死或伪造恢复 |

所有产出仅供学习研究，不构成投资建议。
