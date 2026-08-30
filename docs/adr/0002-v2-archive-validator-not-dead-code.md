# ADR-0002：check-full-analysis-contract.py 的 v2 档案分支是合法遗留，不是死代码

**状态**：Accepted（2026-08-30，/improve-codebase-architecture 评审候选① 调查后的裁决）

## 背景

2026-08-30 架构评审候选①（「实质地板」提为 substance 深模块）附带提出一项清理：
`scripts/check-full-analysis-contract.py` 中

- `SEQUENTIAL_CAPS = {"PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE_PASS"}`（第 43 行）
- `if roles.get("sequential_cap") not in SEQUENTIAL_CAPS`（第 334 行）

评审当时的判断是「幽灵值 + 死校验」：`NOT_APPLICABLE_PASS` 在契约 JSON 与 Gate 的
`RESULT_STATUSES`（用的是 `NOT_APPLICABLE`）里都查不到，且 `tools/full_analysis_contract.json`
中 13 个 skill 的 `roles` 块确实**都没有** `sequential_cap` 键——据此判定为 lean pivot 后的残留。

执行前 trust-but-verify 调查推翻了该判断：

1. 该文件存在**两个互相独立的校验器**：`validate_v2`（第 252 行循环）与 `validate_lean`（第 359 行循环）。
   第 334 行的 `sequential_cap` 校验位于 **`validate_v2`** 分支内。
2. `main()`（第 548-566 行）按契约的 `schema_version` 分派：
   - `full-analysis-contract/v2` → `validate_v2`
   - `full-analysis-contract/lean-v1` → `validate_lean`（当前契约实际走的分支）
3. `validate_v2` 的冻结是**显式决策**，见 commit `fe9001a`
   「docs(contract): ⑥ validate_v2 分支显式冻结为档案校验专用（不删除，trust-but-verify 修正）」。
   它的职责是校验 v2 时代的**历史契约档案**——那些契约里 `roles.sequential_cap` 是真实存在的字段。
4. 因此 `NOT_APPLICABLE_PASS` 是 v2 状态词表的第三个合法取值，与 lean 的 `NOT_APPLICABLE`
   不是同一套词表，二者不构成口径漂移。

**结论：评审候选①的这项清理前提是错的。** 删除它会破坏 v2 历史契约的档案校验能力。
实际执行时未改动该文件，处置正确。

## 决策

- **不删除** `SEQUENTIAL_CAPS`、`NOT_APPLICABLE_PASS` 与 `sequential_cap` 校验。
- **不合并** v2 与 lean 的状态词表——它们服务于不同 schema 代际，不是同一概念的两种拼写。
- 未来评审若再次判定该文件存在「幽灵值 / 死代码」，**先确认其是否位于 `validate_v2` 分支内**；
  位于 v2 分支即属档案校验职责，不适用 lean 时代的清理标准。除非前提事实改变
  （如 v2 档案全部退役、`validate_v2` 被显式移除），不再重复建议。

## 后果

- 正面：保留 v2 历史契约的可校验性；避免一次基于错误前提的破坏性清理。
- 负面：文件内同时存在两套校验逻辑，未来读者需自行分辨某段代码属于哪一代——
  本 ADR 即在偿付这个认知成本。建议后续在 `validate_v2` 函数头部补一行指向本 ADR 的注释。

## 附带教训（评审纪律）

本条记录的真正价值不在结论，而在过程：候选①是基于
「grep 不到 `NOT_APPLICABLE_PASS` ⇒ 它不存在 ⇒ 是残留」这一推理链提出的，
而该推理漏掉了「代码分代」这个维度。**grep 无命中只能证明"当前路径不经过"，
不能证明"无用途"**——下判断前必须确认代码所属的执行分支/代际。
