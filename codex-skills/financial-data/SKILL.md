---
name: financial-data
description: 财务数据获取与交叉验证规范：所有涉及企业财务数据研究的通用底层规范，规定美股/港股/A股数据源优先级、双来源交叉验证（误差>1% 须标记）与 A 股数据插件用法，供其他研究 skill 引用。
owner: psilhon
category: 数据与思维工具
maturity: stable
review-cadence: per-release
---

## Codex adapter note

This skill is generated from `skills/financial-data.md` so Claude Code and Codex users share one canonical workflow.

- Treat `$ARGUMENTS` as the user's request in the current Codex thread.
- When the source mentions Claude-only surfaces such as Task, Agent, WebSearch, Bash, Read, or Write, use the closest Codex capability available in this session: subagents when available, web search when needed, shell commands for local tools, and normal file edits for workspace files.
- Use shared project tools from `tools/` in this repository. Prefer running commands from the repository root with paths like `python3 tools/financial_rigor.py ...`; if the current thread starts outside the repo, locate the actual checkout path first instead of assuming a fixed home-directory path.
- Before starting research, run the `date` command to confirm today's date; treat it as the baseline for "latest" data and state the data cutoff date in the report header. Never assume the current date from training data.
- Preserve the research quality rules from `AGENTS.md`: cross-check financial data, use exact arithmetic tools for valuation/math, and clearly label uncertainty and source gaps.

# 财务数据获取与交叉验证规范

本规范适用于所有涉及企业财务数据的研究。**每个关键数据必须来自两个独立来源，误差>1%须标记。**

🔴 **适用范围声明（必读）**：本 skill 对 **A股、港股、美股全部必须执行**。ashare-data 是取数工具、financial-data 是双源交叉验证——两者是不同的工作。严禁写"N/A - A股上市公司数据走ashare-data管线"或类似跳过结论。无论公司是否上市、在哪个市场上市，本规范的双源交叉验证都必须完成。

## Berkshire A 股数据插件

研究工作流可选使用 `python3 tools/ashare_data.py` 获取 A 股证据。插件支持实时行情、财务与股本历史、公告、龙虎榜、资金流、解禁和融资融券，并在结果中标注主源、备用源、数据时间和警告。

```bash
python3 tools/ashare_data.py quote 600519
python3 tools/ashare_data.py financials 600519
python3 tools/ashare_data.py announcements 600519 --limit 20
python3 tools/ashare_data.py signals 600519 --date 2026-07-16
```

插件提供数据证据，不替代本规范的双来源交叉验证，也不直接生成买入或卖出结论。主源或备用源失败时，必须把结果标记为数据不足。

完整子命令清单、退出码语义与数据陷阱见 `skills/ashare-data.md`（slash 入口 `/ashare-data`）。

---

## 数据源优先级

### 美股（PDD、腾讯ADR、网易ADR等）

| 优先级 | 来源 | URL | 获取方式 |
|--------|------|-----|---------|
| 1（主） | **macrotrends** | macrotrends.net/stocks/charts/{ticker} | 直接访问，无需注册 |
| 2（副） | **stockanalysis** | stockanalysis.com/stocks/{ticker}/financials | 直接访问，无需注册 |
| 原始一手 | SEC EDGAR | sec.gov/cgi-bin/browse-edgar | 10-K / 10-Q 原文 |

### 港股（腾讯0700、网易9999、美团3690等）

| 优先级 | 来源 | URL | 获取方式 |
|--------|------|-----|---------|
| 1（主） | **aastocks** | aastocks.com/tc/stocks/analysis/company-fundamental | 直接访问 |
| 2（副） | **macrotrends**（ADR代码） | 腾讯用TCEHY，网易用NTES | 直接访问 |
| 原始一手 | HKEX披露易 | hkexnews.hk | 年报PDF |

### A股（三七互娱、吉比特等）

| 优先级 | 来源 | URL | 获取方式 |
|--------|------|-----|---------|
| 1（主） | **东方财富** | eastmoney.com → 搜股票代码 → 财务报表 | 直接访问 |
| 2（副） | **巨潮资讯** | cninfo.com.cn | 原始年报/季报PDF |
| 可选验证 | **Tushare** | api.tushare.pro | 仅在本地配置 `TUSHARE_TOKEN` 后自动核验；不替代前两者 |

---

## 执行规范

🔴 STOP / 检查点：在从来源1/来源2（macrotrends/stockanalysis/aastocks/东财/巨潮等）抓取外部财务数据前，必须先向用户确认（给出明确选项：如"确认按规范取数""仅先列数据清单不抓取"），获得明确同意后再继续；未经确认不得自主发起外部取数。

### 第一步：获取数据

对每个财务指标（收入、净利润、毛利率、经营现金流、资产负债率等），分别从**来源1**和**来源2**取数。

🔴 STOP / 检查点：在把两源数据标记为"一致/双源核验通过"并据此下结论前，必须先向用户确认（给出明确选项：如"按误差表判定并标注""仅呈现原始两源数字待核"），获得明确同意后再继续；误差>1% 不得静默当一致，>5% 不得直接使用。

### 第二步：误差计算与标记

```
误差率 = |来源1数值 - 来源2数值| / 来源1数值 × 100%
```

| 误差 | 处理方式 |
|------|---------|
| ≤ 1% | ✅ 一致，取来源1数值，标注两个来源 |
| 1% ~ 5% | ⚠️ 标记"数据存在差异"，注明两个数值，说明可能原因（汇率/会计口径） |
| > 5% | ❌ 标记"数据存在重大差异"，必须查原始财报核实，不得直接使用 |

### 第三步：数据呈现格式

每个关键数据必须按以下格式标注：

```
收入：1,239亿元 ✅
  - macrotrends: 1,241亿元
  - stockanalysis: 1,237亿元
  - 误差: 0.3%
```

差异示例：
```
净利润：245亿元 ⚠️ 数据存在差异
  - macrotrends: 245亿元（GAAP）
  - stockanalysis: 278亿元（Non-GAAP）
  - 误差: 13.5% — 原因：会计口径不同（GAAP vs Non-GAAP）
```

### A 股 Tushare 验证边界

Tushare 可作为独立获取链验证结构化字段，但不能替代巨潮原始财报。对财务、公告和重大事项，只有 `MATCH` 字段可以计入双源事实；`CONFLICT` 必须保留两值并查第三源或原始披露；`INSUFFICIENT` 和 `NOT_CONFIGURED` 均不得计作第二来源。财务比较必须匹配主体、报告期、单位、合并口径与修订版。

行情、估值、市值、换手率和可比较股本等市场结构化字段例外：若同主体、同交易日、同单位的 Tushare 字段为 `CONFLICT`，以 Tushare 作为有效值，并保留原值、偏差和覆盖原因；Tushare 不可用、期间/单位不一致时不得覆盖。

---

## 常见差异原因（不一定是数据错误）

| 原因 | 说明 |
|------|------|
| GAAP vs Non-GAAP | 最常见，尤其是利润类数据 |
| 汇率换算 | 港币/人民币/美元换算时间点不同 |
| 财年定义 | 自然年 vs 财年（如苹果财年10月结束） |
| 合并口径 | 是否含少数股东权益 |
| 数据更新滞后 | 某平台尚未更新最新一期财报 |

---

🔴 STOP / 检查点：在只有单一来源（未上市公司、数据滞后、Tushare 未配置等）而需把数据标 `[估计]` 或当作可用事实呈现前，必须先向用户确认（给出明确选项：如"确认标[估计]并注明缺第二源""暂不纳入结论"），获得明确同意后再继续；未经确认不得把单源伪装成已核验双源。

## 特别规则

1. **未上市公司**（米哈游、莉莉丝等）：只有一手数据来源时，数据前标记 `[估计]`，不执行交叉验证
2. **季度数据 vs 年度数据**：优先使用年度数据做交叉验证，季度数据部分来源可能有滞后
3. **原始财报优先**：若两个来源均与原始财报（10-K/年报PDF）不符，以原始财报为准，标记来源错误

---

## 股价与复权（历史序列必读）

价格有三种口径，混用会让历史股价位置、长期涨幅、历史估值分位全部失真：

| 口径 | 含义 | 用途 |
|------|------|------|
| 不复权 | 实际成交价，除权除息日跳空 | 仅用于"当前时点"快照 |
| 前复权 | 以最新价为基准回调历史价 | 历史股价对比、N年涨幅、历史PE band 一律用它 |
| 后复权 | 以上市首日为基准前推 | 计算历史总回报/年化收益 |

规则：

1. 涉及历史价格的分析统一用**前复权**，且同一分析内**不得混用**复权与不复权来源。
2. 当前市值/当前PE 用**当前实际股价 × 当前总股本**即可，与复权无关——复权只影响历史序列。
3. 跨越拆股/大比例送转的每股指标（历史EPS、历史股价），必须复权还原后再同比。
4. 总回报/年化收益需计入分红（后复权已含），只看价格涨幅会低估。
5. 增发/回购后市值验算以最新总股本为准（`financial_rigor.py verify-market-cap` 偏差>5% 会提示核对）。

---

## 快速索引

| 场景 | 主要来源 | 备用来源 |
|------|---------|---------|
| PDD / 拼多多 | macrotrends.net/stocks/charts/PDD | stockanalysis.com/stocks/pdd |
| 腾讯 | macrotrends.net/stocks/charts/TCEHY | aastocks（0700.HK） |
| 网易 | macrotrends.net/stocks/charts/NTES | aastocks（9999.HK） |
| 三七互娱 | eastmoney.com（002555） | cninfo.com.cn |
| 吉比特 | eastmoney.com（603444） | cninfo.com.cn |
| Nintendo | macrotrends.net/stocks/charts/NTDOY | stockanalysis.com/stocks/ntdoy |
| Capcom | macrotrends（CCOEY） | stockanalysis（CCOEY） |

---

## 反例与红线（不要做）

- 不要单源当双源：每个关键数据必须两个独立来源，同一发行人多份报告/同一接口镜像/自洽重算都不算第二源。
- 不要忽略误差>1%：两源误差>1%必须标记，>5%须查原始财报核实，不得直接使用。
- 不要混用复权口径：历史价格分析统一前复权，同一分析内不得混用不复权与复权来源。
- 不要把 Tushare `CONFLICT`/`INSUFFICIENT`/`NOT_CONFIGURED` 当第二来源或已验证：只 `MATCH` 计双源，其余记限制。
- 不要在用户未授权时把据此整理的数据落盘为报告或对外发布：本规范只定取数标准，落盘/发布须经 D4 检查点。
- 🔴 **严禁跳过**：不要写"N/A - A股上市公司数据走ashare-data管线"之类——本 skill 对 A股/港股/美股一律必执行，ashare-data 是取数工具，不是交叉验证的替代。

---

## 失败处理（如果 X 失败 → Y）

- 如果 来源1/来源2 取数失败或限流 → 执行：换备用源或原始财报（10-K/年报PDF），仍取不到标 `[估计]`/数据不足，不编造。
- 如果 两源误差 >5% → 执行：查原始财报核实，不得直接使用，标"数据存在重大差异"。
- 如果 Tushare 返回 `CONFLICT`/`INSUFFICIENT`/`NOT_CONFIGURED` → 执行：只记限制，不计入双源事实，财务类须查第三源或原始披露仲裁。
- 如果 用户输入含糊（如未指明市场/期间/币种）→ 执行：按快速索引默认源处理并明确告知口径，或请用户确认。

---

## Tushare 数据引用

| 命令 | 数据内容 | 用途 | 层级 |
|------|---------|------|:--:|
| `mainbz` | 主营业务构成（分产品/分地区，fina_mainbz） | 分部营收独立第二源——与东财 F10 主营构成交叉核对 | 推荐 |

> **数据源优先级**：Tushare fina_mainbz（分部独立源）与东财 F10 双向交叉验证。误差 >1% 须标记。本 skill 的核心机制就是双源交叉验证，Tushare 提供了其中的独立第二源。

---

## 依赖与资源清单

本 Skill 依赖以下外部工具与资源（根路径 `$BERKSHIRE_ROOT=/Users/psilhon/WorkSpace/stock/berkshire`）：

| 依赖项 | 路径 | 用途 | 可达性 |
|--------|------|------|--------|
| ashare_data.py | `tools/ashare_data.py` | A股数据管线（行情/财务/公告/市场信号） | ✅ |

> **自检**：所有路径均为 `$BERKSHIRE_ROOT` 仓库内文件，已确认存在。新增依赖需同步更新本清单。
