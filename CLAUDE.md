# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# AI Berkshire — 项目指令

## 项目概述

同时兼容 Claude Code 与 Codex 的价值投资研究 Skill 合集。四大师框架：巴菲特、芒格、段永平、李录。
GitHub: xbtlin/ai-berkshire。本仓库既是个人投研工作区（reports/ 有真实研究产出），也是开源产品——改工具/skill 时不要顺手改动无关报告。

`ai_CLAUDE.md` 是项目级 AI 记忆文件（用户画像、Skill 体系演进、历史误判教训），已入库公开；做投研任务前值得参考，重要修正后可沉淀进去。

## 架构：双系统 Skill 管线

`skills/*.md` 是**唯一权威源**，Codex 侧文件全部由脚本生成：

```
skills/*.md（Claude Code slash command 源文件，权威）
   │ python3 scripts/sync-codex-skills.py
   ├──► codex-skills/*/SKILL.md（生成的 Codex skill 包）
   │ python3 scripts/sync-codex-prompts.py
   └──► codex-prompts/*.md（生成的 Codex slash prompt 兼容层）
```

- **改了 `skills/` 下任何文件后必须跑** `python3 scripts/sync-codex-skills.py`（涉及 slash prompt 时再跑 `sync-codex-prompts.py`）
- 校验生成物是否最新（不重写文件）：`python3 scripts/sync-codex-skills.py --check` / `sync-codex-prompts.py --check`
- **不要手改生成的 `codex-skills/*/SKILL.md`**；仅 Codex-only 手写包例外（需明确标注，且不得存在同名 `skills/*.md`）
- Codex 侧行为规则见 `AGENTS.md`（本文件管 Claude Code，AGENTS.md 管 Codex，改流程时两边同步）

## 常用命令

```bash
# 统一本地检查（单测 + 生成物同步校验 + 报告索引校验）——改 tools/ 或 skills/ 后必跑
# CI（.github/workflows/check.yml）跑的就是同一个脚本，本地过 = CI 过
bash scripts/check.sh

# 单独跑测试 / 单个测试文件 / 单个用例
python3 -m unittest discover -s tests
python3 -m unittest tests.test_financial_rigor
python3 -m unittest tests.test_report_audit.TestVerdictFailClosed -v

# 精确金融计算（Decimal 无浮点漂移；市值/估值/多源交叉/Benford）
python3 tools/financial_rigor.py verify-market-cap --price 510 --shares 9.11e9 --reported 4.65e12 --currency HKD
python3 tools/financial_rigor.py calc --expr '510 * 9.11e9'

# 报告数据抽检（抽15%数据点比对信源，准出/证据不足/打回三态判决）——报告发布前跑
python3 tools/report_audit.py extract --report reports/xxx.md
python3 tools/report_audit.py verdict --results '[...]'

# A股实时行情/财务（腾讯行情+东财，零依赖，curl 直连绕代理）
# CLI 入口是 ashare_data.py，实现拆在 tools/ashare_plugin/ 包（transport/quote/fundamentals/disclosures/market_signals）
# skill 入口 /ashare-data（skills/ashare-data.md：子命令总览/退出码语义/数据陷阱）
python3 tools/ashare_data.py quote 600519
python3 tools/ashare_data.py financials 600519
python3 tools/ashare_data.py search 茅台
```

其它工具：`stock_screener.py`（动量+价值筛选，读 `data/watchlist.json`）、`morningstar_fair_value.py`（晨星公允价值抓取）、`xueqiu_scraper.py`（雪球用户时间线爬虫）、`star_history_chart.py`（README 自托管 star 曲线 SVG）。工具原则上零外部依赖（仅 Python stdlib）；**唯一例外** `xueqiu_scraper.py` 需 playwright（`pip install playwright && playwright install chromium`）。

新增/改名/移动报告后跑 `python3 scripts/build_report_index.py` 重建 `reports/INDEX.md`（check.sh 的 `--check` 会拦住索引不同步）。

## 目录结构

```
skills/          — Skill 权威源（.md）
codex-skills/    — 生成物，勿手改
codex-prompts/   — 生成物，勿手改
scripts/         — sync-* 生成脚本 + install-* 安装脚本
tools/           — 金融计算/数据/审计工具（python3）；ashare_plugin/ 是 A股数据实现包
tests/           — unittest 单测（financial_rigor / report_audit / ashare_plugin 等）
data/            — watchlist.json、基本面缓存、回测 CSV
reports/         — 研究报告产出（按公司名建文件夹）
筛选公司/         — 全市场筛选结果与召回池
实盘记录/         — 实盘操作与镜子测试
research/        — 研究素材底稿
docs/            — ROADMAP 与专题文档
```

## 报告目录与命名规范

公司相关报告放 `reports/{公司名}/` 文件夹内；行业/漏斗/主题/组合/多公司报告放 `reports/` 根目录。

| Skill | 文件命名格式 | 位置 |
|------|---------|------|
| /investment-team | 目录含 README + 01-04 四视角（段永平商业模式/巴菲特财务估值/芒格行业竞争/李录风险管理层）+ `最终报告.md` | `reports/{公司名}/` |
| /investment-research | `{公司名}-research-{YYYYMMDD}.md` | 公司文件夹 |
| /investment-checklist | `{公司名}-checklist-{YYYYMMDD}.md` | 公司文件夹 |
| /earnings-review, /earnings-team | `{公司名}-earnings-{期间}.md`（如 `腾讯-earnings-2025Q4.md`） | 公司文件夹 |
| /management-deep-dive | `{公司名}-management-{YYYYMMDD}.md` | 公司文件夹 |
| /thesis-tracker | `{公司名}-thesis.md`（长期维护） | 公司文件夹 |
| /thesis-drift | 对比 `{公司名}-thesis-*.md` 新旧版本 | 公司文件夹 |
| /news-pulse | `{公司名}-news-{YYYYMMDD}.md` | 公司文件夹 |
| /deep-company-series | `《看懂{公司名}》-{YYYYMMDD}/` 目录（3-8篇长文）；旧版目录已存在时不覆盖不混放 | 公司文件夹 |
| /private-company-research | `{公司名}-private-{YYYYMMDD}.md` | 公司文件夹 |
| /industry-research | `{行业名}-industry-{YYYYMMDD}.md` | 根目录 |
| /industry-funnel | `{行业名}-funnel-{YYYYMMDD}.md` | 根目录 |
| /bottleneck-hunter | master-map / watchlist / daily / `{趋势名}-bottleneck-{YYYYMMDD}.md` | `reports/bottleneck-map/` |
| /portfolio-review | `portfolio-latest.md`（持续更新） | 根目录 |

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

## 公开仓库与隐私边界

本仓库公开。以下内容只存本地、永不入库（.gitignore 已排除）：

- `local/` — 所有不打算公开的文件放这里
- `reports/portfolio-latest.md` — 组合报告只存本地
- `实盘记录/` 内是公开的**权重口径**版；原始账本（含股数/金额）在 `local/实盘记录-原始/`

写报告/整理文件时不要把上述私密内容挪进会被 track 的路径。音视频大文件（.m4a/.mp3/.mp4 等）默认不入库，确需提交用 `git add -f` 显式加入。

## Git 操作

- 远程仓库：`https://github.com/xbtlin/ai-berkshire.git`（README 面向外部用户写的安装路径是 `~/ai-berkshire/`，本机工作区就是本仓库所在目录）
- 推送前先 `git pull --rebase origin main`（远程经常有新提交）
- commit message 用中文，描述清楚改了什么
- 不要推送中间过程文件（如 data_collection.md），只推最终报告
- 报告写完后主动询问是否推送到 GitHub
