# Tushare 数据源缺口评估与集成路线图

> **实施状态（2026-07-19 更新）：Tier-1（6）+ Tier-2（6）已全部实现并实盘验证。**
> 新增 CLI 命令：`mainbz`（分部）、`managers`（履历）、`repurchase`（回购）、`pledge`（质押）、`express`（快报）、`kline`（前复权日线）、`audit`（审计意见）、`holder-num`（股东户数）、`ratios`（比率全景）、`peers`（申万候选池）、`north-hold`（北向持股）、`index-val`（大盘估值分位）。另加：`quote`/`valuation` 新浪独立价源 + `daily_basic` 扩字段（pe_ttm/dv_ratio/dv_ttm/ps_ttm）。均需 `TUSHARE_TOKEN`。Tier-3 为按需项，未实施。
>
> 评估日：2026-07-19（Asia/Shanghai）
> 前提：账号为**付费 Tushare Pro（10,000 积分）**；下列接口除 Level-2 逐笔/部分另类需单独开通外，基本全覆盖。
> ⚠️ **积分门槛为估计值**，评估时 tushare.pro 实时文档抓取不可用；接入前请在个人中心最终核对每个接口的积分与权限。
> 定位：数据集成路线图，供 `tools/ashare_plugin/` 演进参考；不构成投资建议。
>
> **单真源提示**：条件命令的编排路由（命令 → 消费 skill → 采集层）以 `tools/full_analysis_contract.json` 的 `conditional_command_operations.values`（`capability`/`feeds`/`layer` 字段）为唯一真源；下表『受益 skill』列是**集成动机**参考，可能比契约 `feeds` 更宽或不同，**不作编排路由依据**。

## 一、现状定性（关键判断）

当前已接入约 23 个 Tushare 接口（见 `tools/ashare_plugin/tushare_verification.py` 的 `COMMAND_APIS` / `API_FIELDS`）：

`daily_basic`、`income`、`balancesheet`、`cashflow`、`fina_indicator`、`share_float`、`stock_basic`、`moneyflow`、`top_list`、`margin_detail`、`anns_d`、`stk_surv`、`stk_holdertrade`、`forecast`、`top10_holders`、`dividend`、`stk_rewards`、`sw_daily`、`index_classify`、`major_news`、`disclosure_date`、`hk_daily`、`hk_basic`。

**核心问题：几乎全部只用作"交叉验证"（`MATCH/CONFLICT/INSUFFICIENT`），而非独立主数据源。** 项目大量数据当前靠东方财富 F10 单源、或靠巨潮公告手工提取（回购/分红/管理层）——而这些 Tushare 都有结构化接口。对付费用户，额度回报最高的用法是把 Tushare 从"纯裁判"升级为"**分部 / 回购 / 质押 / 管理层履历 / 复权历史 / 估值分位的独立主数据源**"。

另外：`fina_indicator` 虽已接入，但只取了 8 个字段（`roe / grossprofit_margin / netprofit_margin / debt_to_assets` + 元字段），而该接口有 100+ 字段可用。

## 二、高价值缺口（按价值投资场景 + 本项目实际空缺排序）

### 🔴 Tier 1 — 直接补当前硬空缺（建议先做）

| Tushare 接口 | 补的空缺 | 受益 skill（集成动机，非路由） | ~积分 |
|---|---|---|---|
| **`fina_mainbz`** 主营业务构成 | 分部收入（集运/码头、分产品/分地区）当前**只靠东财 F10 单源** → Tushare 给独立第二源，分部真双源 | investment-research / quality-screen | ~2000 |
| **`stk_managers`** 上市公司管理层 | **直接补 management-deep-dive 里"管理层履历（出生年/首次任职）未取"的硬缺口** | management-deep-dive | ~2000 |
| **`repurchase`** 股票回购 | 回购进度/金额/数量（当前**靠巨潮公告手工提取**）→ 结构化独立源 | management-deep-dive / news-pulse | ~2000 |
| **`express`** 业绩快报 | 正式财报前的早期业绩信号（营收/净利预览）→ 提前量 | earnings-review | ~120 |
| **`daily` / `pro_bar`** 复权日线 | 补"**管线不提供复权 OHLC 序列**"（news-pulse 异动分析现只能借腾讯 kline）→ 独立历史价格源 | news-pulse / thesis-drift | ~120 |
| **`pledge_stat` / `pledge_detail`** 股权质押 | 控股股东质押率 = 治理/风险红线信号（当前完全没有） | thesis-tracker 红线 / 风险 | ~2000 |

### 🟡 Tier 2 — 深化现有、补估值分位

| 接口 | 价值 | ~积分 |
|---|---|---|
| **`fina_indicator` 扩字段** | 已接但只取 8 字段；加 `current_ratio / quick_ratio / debt_to_eqt / roa / roic / ocf_to_or / cfps / bps / …` → quality-screen 7 指标更全、多点独立比对 | ~2000 |
| **`index_dailybasic`** 大盘/指数每日 PE/PB | **估值历史分位参照**（审计反复标"历史估值分位证据不足"）→ 破净是否真便宜有了行业锚 | ~400 |
| **`index_member_all`** 申万指数成员股 | industry-funnel **候选池自动生成**（现在手工列候选）→ 去劣更系统 | ~2000 |
| **`moneyflow_hsgt`** 沪深港通资金流 | 外资/机构情绪（个股北向净流入）→ news-pulse 资金面 | ~2000 |
| **`fina_audit`** 财务审计意见 | 是否"标准无保留意见" = 治理硬信号 | ~2000 |
| **`stk_holdernumber`** 股东人数 | 筹码集中度趋势 | ~2000 |

### 🟢 Tier 3 — 情境/中价值

`top10_floatholders`（前十大流通股东）、`block_trade`（大宗交易）、`bak_basic`（更多基本面字段）、`cb_basic` / `cb_daily`（若标的有可转债）、宏观 `cn_ppi` / `cn_gdp` / `cn_pmi`（**周期股尤其相关**，如中远海控看贸易/工业景气）、`hsgt_top10`（沪深港通十大成交股）。

### ⚪ 不建议接入（低价值 / 不匹配价值投资）

技术因子 `stk_factor` / `stk_factor_pro`、Level-2 逐笔/分钟线、`cctv_news`、基金/期货全套接口、大盘泛资金流——偏交易、非股票或与现有数据冗余。

## 三、Tushare 覆盖不到的（必须保留外部一手源）

| 数据 | 来源 |
|---|---|
| 集运运价 **SCFI / CCFI** | 上海航运交易所 SSE |
| 全球船队/运力/订单 | Alphaliner / Clarksons |
| **港股完整财务**（Tushare 港股仅行情 + 基础） | 港交所披露易 HKEX |
| **官方年报 PDF 原文** | 巨潮 cninfo |

这也回答了上一轮数据源评估的 #3（年报原文做第二源）与 #4（Clarksons/CTS）：这类 Tushare 补不了，仍靠一手源。

## 四、集成方式（低风险增量）

现有机制已通用，加新接口是"加映射"而非"改核心"：

1. `API_FIELDS[<api_name>]` 增加接口字段元组。
2. `COMMAND_APIS[<command>]` 增加新命令 → 接口映射。
3. 若为"Tushare 作主数据、无腾讯/东财对照物"的接口（如 `stk_managers` / `repurchase` / `pledge_stat`），复用现有 `_tushare_primary_fields()` 模式（只报 endpoint 健康度，不做逐字段冲突比较）。
4. 若为"独立第二源比对"（如 `fina_mainbz` 对分部、`daily` 对历史价），走 `compare_decimal` / `compare_text`。
5. 在 `tools/ashare_data.py` 加对应 `cmd_*` 展示；改 `skills/ashare-data.md` 后跑 `python3 scripts/sync-codex-skills.py`（+ prompts）与 `bash scripts/check.sh`。
6. 全程 TDD：`tests/test_ashare_plugin_tushare_verification.py` 加接口用例。

## 五、结论

- **有大量可用数据没用起来**：至少 6 个 Tier-1 接口直接补本项目当前的手工/单源/缺失空缺，全部在 10,000 积分覆盖内。
- **战略建议**：Tushare 从"纯裁判"升级为若干"独立主数据源"。
- **推荐起步**：`fina_mainbz`（分部双源）+ `stk_managers`（补管理层履历）——直接补审计与 management-deep-dive 的实锤空缺。

仅供学习研究，不构成投资建议。
