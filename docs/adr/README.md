# Architecture Decision Records (ADRs)

本目录存放 berkshire 仓库的**架构决策记录（ADR）**。每条决策一个文件，编号从 `0001` 起。

## 格式约定

- 文件名：`NNNN-<slug>.md`（如 `0001-xxx.md`），`NNNN` 四位零填充、从 `0001` 起。
- 每条 ADR 建议包含：
  - **状态**（Proposed / Accepted / Deprecated / Superseded）
  - **背景**（为什么会做这个决策）
  - **决策**（我们决定做什么）
  - **后果**（带来的正面 / 负面后果）

## 何时新增 ADR

- 引入新的研究管线 / Skill 架构
- 改变双系统 Skill 管线的同步策略（`skills/*.md` → `codex-skills/`）
- 调整金融严谨 / 报告审计的校验规则
- 任何"以后会有人问为什么"的架构选择

> 由 `/domain-modeling` 按需懒创建；缺失时相关 skill 静默跳过。
