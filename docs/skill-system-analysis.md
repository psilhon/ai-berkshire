# AI Berkshire Skill 体系分析

> 数据截止：2026-07-21（基于 `skills/`、`codex-skills/`、`tools/`、`scripts/`、`SKILLS-GUIDE.md` 当前版本整理）
> 分析目标：理解本仓库的 Skill 体系架构、资产规模、同步健康度，并给出 Skill 开发的规范与改进建议。

---

## 1. 体系架构：双目标 + 生成管线

本仓库的 Skill 体系为 **Claude Code（canonical 源）↔ Codex（生成产物）** 双目标设计，核心原则是一份规范源、自动生成多端产物。

```
skills/*.md              ← [规范源] Claude Code slash command，人工维护
      │  scripts/sync-codex-skills.py
      ▼
codex-skills/*/SKILL.md  ← [生成] Codex skill，追加 "Codex adapter note"，含 1 个 Codex-only 手工包

tools/*.py + *.json      ← [共享] 精确计算/校验/数据管线工具，被各 skill 调用
scripts/*.sh + *.bat     ← [安装] install-claude-commands / install-codex-skills
docs/SKILLS-GUIDE.md     ← [文档] 21 个 canonical Skill（20 业务 + 1 编排层）使用说明
```

**关键设计点**
- `skills/*.md` 是唯一真源；`codex-skills/` 为生成物，**严禁手工编辑生成物**（AGENTS.md 已明令）。
- `investment-memo-craft` 是唯一的 **Codex-only 手工包**，已在文件头显式标注 “Do not add a same-named `skills/*.md`”，符合 AGENTS.md 约定。

---

## 2. 资产清单

| 资产 | 数量 | 说明 |
|------|------|------|
| `skills/*.md` | **21** | canonical 规范源（含 1 个 orchestration 编排层） |
| `codex-skills/*/SKILL.md` | **22** | 21 个由 `skills/` 生成 + 1 个 Codex-only（`investment-memo-craft`） |
| `tools/*.py` | 12 | 共享校验/数据工具 |
| `tools/*.json` | 2 | `full_analysis_contract.json`（20 项业务注册表）、`financial_rigor_result_schema.json` |
| 文档 | 1 | `SKILLS-GUIDE.md` |

**规模分布（skills/ 行数）**：最小 `financial-data`(145) → 最大 `private-company-research`(1071)，总量 ~5982 行。多数 skill 在 150–480 行区间，`private-company-research` 为超长单文件。

---

## 3. 功能分类（21 个 canonical skill）

| 类别 | Skill | 量级* |
|------|-------|------|
| 深度公司研究 | `investment-research`、`investment-team`、`management-deep-dive`、`private-company-research`、`deep-company-series` | 重/极重 |
| 财报分析 | `earnings-review`、`earnings-team` | 重/极重 |
| 行业与筛选 | `industry-research`、`industry-funnel`、`quality-screen`、`bottleneck-hunter`、`investment-checklist` | 中/重 |
| 持仓与论文管理 | `portfolio-review`、`thesis-tracker`、`thesis-drift`、`news-pulse` | 中/重 |
| 数据与思维工具 | `financial-data`、`ashare-data`、`dyp-ask`、`wechat-article` | 轻/中 |
| 编排层（orchestration） | `full-company-analysis`（Phase2-gated，不计入 20 项业务集合） | 极重 |

\* 量级取自 `SKILLS-GUIDE.md` 成本梯度表。

---

## 4. 同步与一致性健康度（实测）

| 检查项 | 结果 |
|--------|------|
| `python3 scripts/sync-codex-skills.py --check` | ✅ 通过，21 个 Codex skill 全部为最新，无 stale |
| `tools/` 引用解析 | ✅ 7 个被引用文件（`ashare_data.py`、`financial_rigor.py`、`full_analysis_contract.json`、`full_analysis_gate.py`、`report_audit.py`、`xueqiu_scraper.py`、`financial_rigor_result_schema.json`）**全部存在，无悬空引用** |
| Codex-only 标注 | ✅ `investment-memo-craft` 已正确标注，未污染 `skills/` |
| 生成物可重生成 | ✅ 脚本为确定性生成（frontmatter 保留 + adapter note 追加），可一键重建 |

**结论**：生成管线本身健康、可重放，`codex-skills` 与 `skills/` 完全一致。问题集中在 **规范源自身的元信息一致性**，而非同步。

---

## 5. 质量与一致性问题（按优先级）

### ✅ P1 — frontmatter 严重不一致（已修复 · 2026-07-21）
> **修复记录**：已为缺失的 **18** 个 skill 补齐 `name` + `description` frontmatter，描述统一写清“触发场景 + 输入 + 产出”，风格对齐 `ashare-data`/`news-pulse`。现 **21 / 21** 全部合规。生成物已重跑 `sync-codex-skills.py`，`--check` 通过；Codex 端 description 现继承源自定义描述（替代原 auto 占位）。

原问题（存档）：仅 3 / 21 个 canonical skill 含 frontmatter（`ashare-data`、`full-company-analysis`、`news-pulse`），其余 18 个缺失，导致 Claude Code slash command 只能靠文件名触发、命令列表无描述、自动匹配率低。虽然 `sync-codex-skills.py` 会为 Codex 端自动补 description，但 Claude Code 端长期缺失属系统性短板。

### ✅ P2 — 治理元数据未标准化（已修复 · 2026-07-21）
> **修复记录**：治理字段已统一迁入 frontmatter 并标准化——全部 21 个 skill 现带 `owner` / `category` / `maturity` / `review-cadence` 四字段（`full-company-analysis` 额外 `registry-schema: v1`），原 HTML 注释治理行已移除。`SKILLS-GUIDE.md` 同步刷新：计数改为「21 个（20 业务 + 1 编排层）」、数据截止 07-21、新增「🎛 编排层」章节与 frontmatter 规范小节。新增 `scripts/check-skill-frontmatter.py` 并接入 `check.sh`，作为 CI 卡点强制 6 必填字段 + 取值合法性。

原问题（存档）：`full-company-analysis` 使用 HTML 注释式治理字段，仅此一处、难以被脚本/CI 解析；`SKILLS-GUIDE.md` 自述「20 个」且未覆盖编排层、日期停在 07-17。

### ✅ P3 — Codex prompt 兼容层已彻底收敛（已删除 · 2026-07-21）
> **收敛记录（破坏性，已确认）**：已将三端维护三角彻底收敛为两端（skills / codex-skills）。删除 `codex-prompts/` 目录、`scripts/sync-codex-prompts.py`、`scripts/install-codex-prompts.sh/.bat`，并从 `scripts/check.sh` 移除其校验步骤；`install-codex-skills.sh/.bat` 末尾的 prompts 提示语已删；`scripts/check-full-analysis-contract.py` 的 `_INFRA_PREFIXES` 已移除 `codex-prompts/`；`AGENTS.md` / `CLAUDE.md` / 三语 `README` / `SKILLS-GUIDE.md` 同步改为「双入口」描述。所有引用已清除，删除均通过 `git rm` 完成、可 `git restore` 恢复。

原问题（存档）：`codex-prompts/` 已被标注 deprecated，但仍随 `skills/` 同步维护，形成三端维护三角，新人易困惑。

### ✅ 功能重叠（Skill 开发路由需明确 → 已用路由表解决 · 2026-07-21）
> **解决记录**：`SKILLS-GUIDE.md` 新增「🧭 选型决策路由表」章节，按用户意图列出首选 Skill + 不要选 + 原因，覆盖全部重叠组：
- `investment-research` vs `investment-team` vs `full-company-analysis`：单师细致 / 多 Agent 并行最快 / 端到端编排（Phase2-gated），路由表已区分。
- `industry-research` vs `industry-funnel`：全景产业链扫描 vs 漏斗精选 3 家。
- `earnings-review` vs `earnings-team`：单人一手精读 vs 四大师 + 编辑 + 读者成稿。
- `full-company-analysis` 调度 20 项业务由 `tools/full_analysis_contract.json` 机器真源约束，SKILLS-GUIDE 不维护第二份清单（已在编排层小节声明），漂移风险已通过「单源」原则消解。

---

## 6. Skill 开发规范建议（落地清单）

**新增 / 修改一个 skill 的标准流程**
1. 只在 `skills/<name>.md` 编写（kebab-case 命名，`<name>` 即调用入口）。
2. **必须带 frontmatter**（补齐 P1 短板）：
   ```yaml
   ---
   name: <name>                       # 必填，kebab-case，须与文件名一致
   description: <触发场景 + 输入 + 输出>  # 必填，越具体越利于自动匹配
   owner: psilhon                      # 必填
   category: <深度公司研究|财报分析|行业与筛选|持仓与论文管理|数据与思维工具|编排层>  # 必填
   maturity: <stable|beta|governed(Phase2-gated)>  # 必填
   review-cadence: <per-release|on-change|quarterly|annual>  # 必填
   # registry-schema: v1              # 仅 full-company-analysis 需要
   ---
   ```
   > 上述 6 个字段由 `scripts/check-skill-frontmatter.py` 在 `check.sh` 中强制校验，缺失或取值非法会令检查失败。
3. 若涉及多 Agent / 团队，在正文写明权限预检（参考 `investment-team` 的 WebSearch 放行检查，issue #58 教训）。
4. 调用 `python3 tools/financial_rigor.py` 做精确计算、`tools/report_audit.py` 做准出抽检（共享红线）。
5. 跑生成与校验：
   ```bash
   python3 scripts/sync-codex-skills.py
   bash scripts/check.sh
   ```
6. 若新增业务 skill 纳入编排，同步更新 `tools/full_analysis_contract.json`，并在 `SKILLS-GUIDE.md` 登记。

**Codex-only vs canonical 决策树**
- 通用工作流 → 写 `skills/`，自动生成两端。
- 仅 Codex 需要的“写作/判断覆盖层”（如 `investment-memo-craft`）→ 直接写 `codex-skills/<name>/SKILL.md`，文件头标注 Codex-only，**不要**反向建同名 `skills/` 源。

**描述（description）写法**
- 写清“何时触发 + 输入形态 + 产出”，例如 ashare-data 的“零依赖走腾讯行情+东方财富+巨潮，输入六位代码或公司名即可取数”。这同时提升 Claude Code 与 Codex 的自动匹配率。

---

## 7. 结论

体系架构设计成熟：**单源、自动生成、可重放校验**，同步健康度实测通过、无悬空引用。原主要短板（**规范源自身的元信息纪律**——frontmatter 覆盖仅 3/21、治理字段未标准化、文档与 contract 存在人工漂移风险）已通过 P1 + P2 + P3 三轮修复闭环：现 21/21 均带完整 frontmatter（含治理字段），`SKILLS-GUIDE.md` 已覆盖编排层并刷新口径，`scripts/check-skill-frontmatter.py` 已作为 CI 卡点接入 `check.sh`，且三端维护三角已收敛为两端（`skills/` + `codex-skills/`，`codex-prompts/` 弃用层已删除）——Skill 开发从“靠自觉”变为“靠 CI 卡点 + 单生成管线”。功能重叠的触发路由已用「🧭 选型决策路由表」固化。
