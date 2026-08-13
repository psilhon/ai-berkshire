# Skills 使用指南

本仓库当前包含 **16 个投研业务 Skill、1 个编排 Skill 和 1 个行业路由参考**（共 18 个 canonical 源文件）。`industry-routing` 是被 12 个消费方 skill 按需加载的路由矩阵（选行业附录 / 必备 KPI / berkshire A股数据命令），不是独立业务入口。`skills/*.md` 是 workflow 权威源；`codex-skills/*/SKILL.md` 与 WorkBuddy 全量分析适配器由 `python3 scripts/sync-codex-skills.py` 生成并通过 `--check` 校验。

其中 **13 个业务 Skill 组成单公司全量分析契约**（`tools/full_analysis_contract.json`，schema `full-analysis-contract/lean-v1`），另有 **3 个市场级 / IPO 独立 Skill**（`a-share-market-sentiment`、`macro-liquidity`、`a-share-prospectus-analysis`）不参与契约、独立运行。

> 数据截止：2026-08-13。具体章节、证据和适用性要求以各 Skill 源文件及 `tools/full_analysis_contract.json` 为准。

## 通用前提

- 研究开始前运行 `date`，并在报告头标注数据截止日。
- 关键财务数据至少使用两个独立来源；差异必须解释。
- 估值和精确计算使用 `python3 tools/financial_rigor.py`。
- 报告交付前运行 `python3 tools/report_audit.py`；全量分析还必须通过 self-check 与 Gate 的 substance 兜底，需要质量验证时再跑 Audit 与语义 Review。
- 数据不足时明确记录限制，不用猜测补齐。
- 修改 Skill 后运行 `python3 scripts/sync-codex-skills.py`，完成前运行 `bash scripts/check.sh`。

## 投研分析核心原则（最高优先级）

- **客观、客观、客观**——所有投研分析必须基于事实和数据，严禁主观臆断
- 严格区分"事实"与"观点"：事实用数据支撑，观点必须明确标注为"观点"或"推测"
- **不预设立场**：先摆数据、再推逻辑、最后得结论，结论必须从数据中自然推出
- 禁止"我认为/我觉得/显然"等主观表述，改用"数据显示/证据表明/根据XX来源"
- **呈现正反两面**：每个核心判断都必须附带反面论据（"但另一方面..."），让读者自己权衡
- 对不确定的事情诚实说"不确定"或"数据不足"，不用推测填充确定性
- **研究开始前先跑 `date` 确认今天日期**，以此为"最新数据"基线并在报告头标注数据截止日，绝不用训练数据里的日期
- 所有 skill 执行时都必须遵守以上原则

## 报告语言与风格

- 所有报告使用**中文**；风格直接、犀利、不说废话
- 数据必须标注来源，关键数据至少 2 个来源交叉验证；估计值必须注明"估计"
- 评分使用★符号（★1-5），不含半星
- 穿插巴菲特/芒格/段永平/李录的语录点评
- 本项目用于学习研究，不构成投资建议

## 数据校验（报告发布前）

- 市值必须手算校验：股价 × 总股本，与报告市值对比
- 货币单位明确标注（港币/人民币/美元/韩元），防止混淆
- PE/ROE 等指标用 `tools/financial_rigor.py` 精确计算
- 发布级报告先过 `tools/report_audit.py` 抽检

## 报告目录与命名规范

公司相关报告放 `local/reports/{公司名}/` 文件夹内；行业/漏斗/主题/组合/多公司报告放 `local/reports/` 根目录（下表"根目录"均指此处；旧 `reports/` 根目录已在 v1.0 全量迁移至 `local/reports/`）。

| Skill | 文件命名格式 | 位置 |
|------|---------|------|
| /investment-team | 目录含 README + 01-04 四视角（段永平商业模式/巴菲特财务估值/芒格行业竞争/李录风险管理层）+ `最终报告.md` | `local/reports/{公司名}/` |
| /investment-research | `{公司名}-research-{YYYYMMDD}.md` | 公司文件夹 |
| /investment-checklist | `{公司名}-checklist-{YYYYMMDD}.md` | 公司文件夹 |
| /earnings-review | `{公司名}-earnings-{期间}.md`（如 `腾讯-earnings-2025Q4.md`） | 公司文件夹 |
| /management-deep-dive | `{公司名}-management-{YYYYMMDD}.md` | 公司文件夹 |
| /thesis-tracker | `{公司名}-thesis.md`（长期维护） | 公司文件夹 |
| /news-pulse | `{公司名}-news-{YYYYMMDD}.md` | 公司文件夹 |
| /industry-research | `{行业名}-industry-{YYYYMMDD}.md` | 根目录 |
| /industry-funnel | `{行业名}-funnel-{YYYYMMDD}.md` | 根目录 |
| /bottleneck-hunter | master-map / watchlist / daily / `{趋势名}-bottleneck-{YYYYMMDD}.md` | `local/reports/bottleneck-map/` |
| /a-share-market-sentiment | `A股市场情绪-{YYYYMMDD}.md`（情绪评级 + 仓位分档） | 根目录 |
| /macro-liquidity | `宏观流动性-{YYYYMMDD}.md`（美元层 + A股层双水位） | 根目录 |
| /a-share-prospectus-analysis | `{公司名}-招股书-{YYYYMMDD}.md`（独立归档于 `local/IPO/{公司名}-{代码}/`） | `local/IPO/{公司名}-{代码}/` |
|  /full-company-analysis-workbuddy | `<run_id>/` 运行目录（路径由 Gate 生成） | `local/Company/<code>-<公司>/` |

## 公开仓库与隐私边界

本仓库公开。以下内容只存本地、永不入库（.gitignore 已排除）：

- `local/` — 所有不打算公开的文件放这里（含 local/reports/ local/筛选公司/ local/实盘记录/ local/research/）

写报告/整理文件时不要把上述私密内容挪进会被 track 的路径。音视频大文件（.m4a/.mp3/.mp4 等）默认不入库，确需提交用 `git add -f` 显式加入。

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
| 市场级 | `a-share-market-sentiment` | A股市场情绪水位（独立于契约） | 5 指标（两融/北向/估值分位/换手/开户发基）→ 预警计数 → 评级与仓位分档 |
| 市场级 | `macro-liquidity` | 宏观流动性监测（独立于契约） | 美元层（Fed 净流动性/SOFR/MOVE/日元套息）+ A股层（两融/北向/中债/Shibor）双水位 |
| IPO | `a-share-prospectus-analysis` | 招股书深度分析（独立于契约） | 概念层六步 + 操作层九步精读（含**步骤9 需求真实性检验/买单主体分解**）+ 七缺口补强（G1–G7）+ **同业横向对比 H1–H4**（cohort 选择/多维矩阵/个性 vs 行业共性判定/同业数据源），内置宇树科技示例（含同业 cohort 对照） |

> 标注「独立于契约」的 3 个 Skill 不参与 `full-company-analysis-workbuddy` 的 13 单元调度，各自按需独立运行。

## 选择建议

- 不确定公司是否值得深挖：先用 `quality-screen`，再用 `investment-checklist`。
- 单公司常规深研：用 `investment-research`；关键决策需要独立视角交锋时用 `investment-team`。
- 财报发布后：用 `earnings-review`；管理层判断仍是主要分歧时补 `management-deep-dive`。
- 从行业找公司：先 `industry-research` 看结构，再用 `industry-funnel` 收敛候选；物理供给约束明显时补 `bottleneck-hunter`。
- 股价突然异动：先 `news-pulse` 判断事件性质，再决定是否重跑公司研究或更新 `thesis-tracker`。
- 判断"市场现在贪婪还是恐慌 / 是否过热"：用 `a-share-market-sentiment`；判断"全球或本土流动性水位"：用 `macro-liquidity`（两者互补：流动性回答"钱够不够"，情绪回答"人有多疯/多怕"）。
- 打新 / IPO 研究：用 `a-share-prospectus-analysis`，基于一手招股书做独立判断。
- 需要完整单公司闭环：使用 `full-company-analysis-workbuddy`，不要手工串联后自行宣称"全量完成"。

## 编排 Skill：full-company-analysis-workbuddy（lean-v1）

`full-company-analysis-workbuddy` 是 WorkBuddy 生产入口，不是额外的业务分析方法。它按契约 `tools/full_analysis_contract.json`（schema `full-analysis-contract/lean-v1`）调度 13 个业务单元，只保证两件事（两条底线）：

1. **内容质量**：每份研究报告基于真实数据、遵循方法论、声明数据截止日 / 来源 / 免责。
2. **失败显式声明**：任何单元做不出来、数据缺失、推理不成立，必须显式 `mark-failed`，**不静默跳过、不自动重试、不伪造占位**。

lean 模式已移除租约看门狗 / 租约身份机 / 波次错峰白名单 / 证据账本 PLACEHOLDER / 双源强制 / 版本钉死 / 自动恢复等冗余机制。报告是**唯一交付物**；证据账本（result.json）只是可选辅助，空账本合法。

闭环流程（含两个用户确认门）：

1. `start` 创建运行目录与 13 个 work unit（依赖由 `next-work` 按其 `depends_on` 拓扑自动返回就绪单元）。
   🔴 **启动确认门（用户）**：`start` 后、**派发第一个业务 Agent 前**，必须先向用户确认公司/代码/as_of（选项：确认启动 / 修改参数后重启 / 取消本 run），防跑错标的白跑 13 单元；未经确认不得自主启动流水线。
2. `next-work` 返回 `LEASED` 后派 **WorkBuddy 原生 Agent**（禁止主上下文直写正文），Agent 完整落地 `methodology_text` 并写入 attempt 目录 + `artifact.formal_path`。
3. 移交前必须跑 `self-check`（实质章节数 / 三锚 / 字节下限）；通过后 `submit-result`（或 `mk_result_bundle` 生成 result.json；空账本合法）。Gate 在 `submit-result` 再做一次 substance 边界兜底（双层互不信任）。
4. 失败单元：`mark-failed --reason ...`（可选 `--retry` 重排一次），不自动重试、不启动看门狗、不做孤儿恢复。
5. 全部业务单元终态后，先经 🔴 **收口确认门（用户）**（deep-summary 熔炼 / `register-summary` 冻结 / `render-html` 渲染前须用户确认，选项：确认收口 / 先人工审阅已完成报告再收口 / 补做失败单元再收口），再派 deep-summary Agent 熔炼总结 → `register-summary` 冻结 → `render-html` 确定性渲染（零 LLM 零方差）。总结须如实标注缺失单元，且遵循 `docs/delivery-summary-quality-standard.md`（25-40KB、≥6 表格、≥5 内联 SVG、三大章节深化、数字溯源抽查）。
6. **L2-L4 可选评估层**（不强制）：`audit`（结构验证）→ `review`（五维语义）→ `finalize`+`doctor`（digest 校验 + 退化指纹，advisory 非阻断）。L1 交付不依赖评估层。

> **单元级无用户确认门（有意设计）**：每个 work unit 的派发不设用户门——13 单元自动流水线若每单元打扰会拖垮"全量研究"的连贯性；各业务 skill 内部本有各自的 🔴 STOP 确认门（启动后台 Agent / 输出强建议 / 落盘发布）。编排器仅在**启动**与**收口**两个全局节点设用户门，是 conscious trade-off，非遗漏。

```bash
python3 scripts/full_analysis.py start \
  --company 格力电器 --code 000651.SZ --as-of 2026-07-25

python3 scripts/full_analysis.py next-work --run-root <run_root>
python3 scripts/full_analysis.py self-check \
  --run-root <run_root> --skill-id <skill_id> --report <attempt_dir>/report.md
python3 scripts/full_analysis.py submit-result --run-root <run_root> ...
python3 scripts/full_analysis.py register-summary \
  --run-root <run_root> --summary <run_root>/evidence/attempts/summary/summary.md
python3 scripts/full_analysis.py render-html --run-root <run_root>

# 可选评估层（需要质量验证 / 正式交付 / benchmark 时）
python3 scripts/full_analysis.py audit --run-root <run_root>
python3 scripts/full_analysis.py review prepare --run-root <run_root>
python3 scripts/full_analysis.py review summarize --run-root <run_root>
python3 tools/full_analysis_gate.py finalize --run-root <run_root>
```

全量运行的启动请求只授权只读外部研究、`run_root` 内写入和研究结论。它不授权 push、PR、发布、发送、外部系统写入、越界写入或敏感数据访问。

所有产出仅供学习研究，不构成投资建议。
