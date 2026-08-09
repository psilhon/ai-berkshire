# AI Berkshire Skill 体系分析

> 数据截止：2026-08-09（基于 `skills/`、`codex-skills/`、`workbuddy-skills/`、`tools/`、`scripts/`、`SKILLS-GUIDE.md` 当前版本整理）
> 分析目标：理解本仓库的 Skill 体系架构、资产规模、同步健康度，并给出 Skill 开发的规范与改进建议。

---

## 1. 体系架构：双目标 + 生成管线

本仓库的 Skill 体系为 **Claude Code（canonical 源）↔ Codex / WorkBuddy（生成产物）** 多目标设计，核心原则是一份规范源、自动生成多端产物。

```
skills/*.md              ← [规范源] 17 个 canonical（16 业务 + 1 编排层），人工维护
      │  scripts/sync-codex-skills.py（确定性生成，frontmatter 保留 + adapter note 追加）
      ▼
codex-skills/*/SKILL.md   ← [生成] Codex skill（17 个生成包 + 1 个 Codex-only 手工包 investment-memo-craft）
workbuddy-skills/full-company-analysis-workbuddy/SKILL.md ← [生成] WorkBuddy 生产适配器（源文件原文）

tools/*.py + *.json      ← [共享] 精确计算/校验/数据管线工具，被各 skill 调用
scripts/*.sh + *.bat     ← [安装] install-claude-commands / install-codex-skills
docs/SKILLS-GUIDE.md     ← [文档] 17 个 canonical Skill（16 业务 + 1 编排层）使用说明
```

**关键设计点**
- `skills/*.md` 是唯一真源；`codex-skills/` 为生成物，**严禁手工编辑生成物**（AGENTS.md 已明令）。
- `investment-memo-craft` 是唯一的 **Codex-only 手工包**，已在文件头显式标注 “Do not add a same-named `skills/*.md`”，符合 AGENTS.md 约定。
- `full-company-analysis-workbuddy` 为编排层：源在 `skills/`，Codex 端生成 adapter，WorkBuddy 端生成生产适配器 `workbuddy-skills/full-company-analysis-workbuddy/SKILL.md`。

---

## 2. 资产清单

| 资产 | 数量 | 说明 |
|------|------|------|
| `skills/*.md` | **17** | canonical 规范源：16 业务 + 1 编排层 |
| `codex-skills/*/SKILL.md` | **18** | 17 个由 `skills/` 生成（含编排 adapter）+ 1 个 Codex-only（`investment-memo-craft`） |
| `workbuddy-skills/*/SKILL.md` | 1 | WorkBuddy 生产适配器（`full-company-analysis-workbuddy`，源文件原文） |
| `tools/*.json` | 2 | `full_analysis_contract.json`（13 项业务契约注册表，schema `full-analysis-contract/lean-v1`）、`financial_rigor_result_schema.json` |
| 文档 | 1 | `SKILLS-GUIDE.md` |

**契约边界**：16 个业务 Skill 中 **13 个**组成单公司全量分析契约（`tools/full_analysis_contract.json` 唯一机器真源）；**3 个契约外独立**（`a-share-market-sentiment`、`macro-liquidity`、`a-share-prospectus-analysis`），市场级/IPO 场景独立运行，不参与 13 单元调度。

---

## 3. 功能分类（17 个 canonical skill）

| 类别 | Skill | 契约 |
|------|-------|------|
| 深度公司研究 | `investment-research`、`investment-team`、`management-deep-dive`、`a-share-prospectus-analysis` | 前 3 入契约，`a-share-prospectus-analysis` 独立 |
| 财报分析 | `earnings-review` | 入契约 |
| 行业与筛选 | `industry-research`、`industry-funnel`、`quality-screen`、`bottleneck-hunter`、`investment-checklist` | 入契约 |
| 持仓与论文管理 | `thesis-tracker`、`news-pulse` | 入契约 |
| 数据与思维工具 | `financial-data`、`ashare-data`、`a-share-market-sentiment`、`macro-liquidity` | 前 2 入契约，后 2 独立（市场级） |
| 编排层（orchestration） | `full-company-analysis-workbuddy`（lean-v1，调度 13 项业务单元） | 编排入口 |

---

## 4. 同步与一致性健康度（实测 · 2026-08-09）

| 检查项 | 结果 |
|--------|------|
| `python3 scripts/sync-codex-skills.py --check` | ✅ 通过，17 个 Codex skill + WorkBuddy adapter 全部为最新，无 stale |
| `scripts/check-skill-frontmatter.py`（CI 卡点） | ✅ 17 / 17 全部合规（6 必填字段 + 取值合法性） |
| `bash scripts/check.sh`（单测 + frontmatter + 同步 + 索引 + 契约） | ✅ REAL_EXIT=0 全绿（734 单测；lean-v1 契约结构合法） |
| Codex-only 标注 | ✅ `investment-memo-craft` 已正确标注，未污染 `skills/` |
| 生成物可重生成 | ✅ 脚本为确定性生成，可一键重建 |

**结论**：生成管线健康、可重放，`codex-skills` / `workbuddy-skills` 与 `skills/` 完全一致；frontmatter 治理纪律由 CI 卡点强制执行。

---

## 5. 质量与一致性问题（按优先级，历史修复存档）

### ✅ P1 — frontmatter 严重不一致（已修复 · 2026-07-21）
> **修复记录**：曾仅 3 / 21 个 canonical skill 含 frontmatter，已为缺失的 18 个补齐 `name` + `description`。此后新增 skill 一律带完整 frontmatter（2026-08-09 快照：17 / 17 合规）。

### ✅ P2 — 治理元数据未标准化（已修复 · 2026-07-21）
> **修复记录**：治理字段统一迁入 frontmatter（`owner` / `category` / `maturity` / `review-cadence`），新增 `scripts/check-skill-frontmatter.py` 接入 `check.sh` 作为 CI 卡点强制 6 必填字段 + 取值合法性。

### ✅ P3 — Codex prompt 兼容层已彻底收敛（已删除 · 2026-07-21）
> **收敛记录（破坏性，已确认）**：`codex-prompts/` 三端维护三角已收敛为两端（`skills/` + `codex-skills/`），相关脚本与文档引用全部清除。

### ✅ 功能重叠（已用路由表解决 · 2026-07-21 → 2026-08-09 刷新）
> `SKILLS-GUIDE.md`「选择建议」章节覆盖全部重叠组路由：`investment-research` vs `investment-team` vs 全量编排、`industry-research` vs `industry-funnel`、`earnings-review` 单师 vs 团队、以及新增的市场级双件（`a-share-market-sentiment` 回答"人有多疯" vs `macro-liquidity` 回答"钱够不够"）。

### ✅ P4 — 2026-08-09 新增 3 skill 的 frontmatter 合规回归（已修复）
> **修复记录**：8/9 新增 `a-share-prospectus-analysis.md` 时 `category: 公司` 不在合法枚举（合法值：深度公司研究/财报分析/行业与筛选/持仓与论文管理/数据与思维工具/编排层），导致 `check-skill-frontmatter.py` 返回 1、`test_repo_skills_pass` 变红。已改为 `深度公司研究`，重跑 `sync-codex-skills.py` 并重建报告索引后 `check.sh` REAL_EXIT=0 全绿。教训：新增 skill 提交前必须跑 `bash scripts/check.sh`，不能只依赖 `--check`。

---

## 6. Skill 开发规范建议（落地清单）

**新增 / 修改一个 skill 的标准流程**
1. 只在 `skills/<name>.md` 编写（kebab-case 命名，`<name>` 即调用入口）。
2. **必须带 frontmatter**：
   ```yaml
   ---
   name: <name>                       # 必填，kebab-case，须与文件名一致
   description: <触发场景 + 输入 + 输出>  # 必填，越具体越利于自动匹配
   triggers: ...                      # 可选，触发词列表
   owner: psilhon                      # 必填
   category: <深度公司研究|财报分析|行业与筛选|持仓与论文管理|数据与思维工具|编排层>  # 必填，取值受 check.sh 卡点约束
   maturity: <stable|beta|governed(Phase2-gated)>  # 必填
   review-cadence: <per-release|on-change|quarterly|annual>  # 必填
   # platform: workbuddy              # 仅编排层 full-company-analysis-workbuddy 需要（-workbuddy 后缀绑定）
   # registry-schema: full-analysis-contract/lean-v1   # 仅编排层需要
   ---
   ```
   > 上述 6 个字段由 `scripts/check-skill-frontmatter.py` 在 `check.sh` 中强制校验，缺失或取值非法会令检查失败。
3. 若涉及多 Agent / 团队，在正文写明权限预检（参考 `investment-team` 的 WebSearch 放行检查）。
4. 调用 `python3 tools/financial_rigor.py` 做精确计算、`tools/report_audit.py` 做准出抽检（共享红线）。
5. 跑生成与校验（**两条都必跑**，新增 skill 尤其不能只跑 `--check`）：
   ```bash
   python3 scripts/sync-codex-skills.py
   bash scripts/check.sh
   ```
6. 若新增业务 skill 纳入编排，同步更新 `tools/full_analysis_contract.json`；若为契约外独立 skill，在 `SKILLS-GUIDE.md` 登记并标注「独立于契约」。

**Codex-only vs canonical 决策树**
- 通用工作流 → 写 `skills/`，自动生成两端。
- 仅 Codex 需要的“写作/判断覆盖层”（如 `investment-memo-craft`）→ 直接写 `codex-skills/<name>/SKILL.md`，文件头标注 Codex-only，**不要**反向建同名 `skills/` 源。

**描述（description）写法**
- 写清“何时触发 + 输入形态 + 产出”，例如 ashare-data 的“零依赖走腾讯行情+东方财富+巨潮，输入六位代码或公司名即可取数”。这同时提升 Claude Code 与 Codex 的自动匹配率。

---

## 7. 结论

体系架构设计成熟：**单源、自动生成、可重放校验**，同步健康度实测通过、无悬空引用。资产规模经历了两轮收敛：07-21 的 21 个 → 07-25 契约精简 20→13 业务单元 → 08-09 稳定为 **17 个 canonical（16 业务 + 1 编排层），其中 13 个入契约、3 个契约外独立**。frontmatter 治理由 CI 卡点（`check-skill-frontmatter.py`）强制，任何取值非法都会令 `check.sh` 失败；08-09 新增 skill 的 category 合规回归已闭环。Skill 开发从“靠自觉”变为“靠 CI 卡点 + 单生成管线”。
