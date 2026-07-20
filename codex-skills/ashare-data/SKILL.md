---
name: ashare-data
description: A股数据管线统一入口：实时行情/近5年财务/估值/10年年报史/股本变动/公告/龙虎榜资金流解禁等市场信号。零依赖走腾讯行情+东方财富+巨潮（curl 直连），结果标注主源/备用源/数据时间/警告。输入六位代码或公司名即可取数。
---

## Codex adapter note

This skill is generated from `skills/ashare-data.md` so Claude Code and Codex users share one canonical workflow.

- Treat `$ARGUMENTS` as the user's request in the current Codex thread.
- When the source mentions Claude-only surfaces such as Task, Agent, WebSearch, Bash, Read, or Write, use the closest Codex capability available in this session: subagents when available, web search when needed, shell commands for local tools, and normal file edits for workspace files.
- Use shared project tools from `tools/` in this repository. Prefer running commands from the repository root with paths like `python3 tools/financial_rigor.py ...`; if the current thread starts outside the repo, locate the actual checkout path first instead of assuming a fixed home-directory path.
- Before starting research, run the `date` command to confirm today's date; treat it as the baseline for "latest" data and state the data cutoff date in the report header. Never assume the current date from training data.
- Preserve the research quality rules from `AGENTS.md`: cross-check financial data, use exact arithmetic tools for valuation/math, and clearly label uncertainty and source gaps.

# A股数据管线：行情/财务/公告/市场信号统一入口

对 $ARGUMENTS 执行 A 股数据获取，统一走本仓库数据管线 `python3 tools/ashare_data.py`（零外部依赖，腾讯行情 + 东方财富 + 巨潮资讯，curl 直连绕系统代理）。

**支持输入格式**：

| 输入方式 | 示例 | 说明 |
|---------|------|------|
| 六位代码 | `600519` | 默认输出概览：行情 + 估值 + 近5年财务 |
| 代码 + 数据类型 | `600519 公告` | 只取指定类型：行情/财务/估值/年报史/股本/公告/信号 |
| 公司名 | `贵州茅台` | 先 `search` 定位代码；多个匹配时列出候选让用户确认，不默默选 |
| 带后缀代码 | `000651.SZ` | 支持 `.SH`/`.SZ`/`.BJ` 后缀；裸码按号段推断市场（4/8/920 开头判北交所） |

> **工具定位**：管线提供数据证据，不替代 `skills/financial-data.md` 的双来源交叉验证，也不直接生成买入或卖出结论。主源或备用源失败时，必须把结果标记为"数据不足"。

**不适用**：完整投研（用 `/investment-team`）、去劣筛选（用 `/quality-screen`，其内部已接本管线）、财务数据交叉验证规范本身（见 `skills/financial-data.md`）、港股美股数据（见 financial-data 的数据源优先级表）。

---

## 子命令总览

| 命令 | 用途 | 关键参数 | 数据源（主 → 备） |
|------|------|---------|-----------------|
| `quote <代码>` | 实时行情快照 + 52周高低 + 价格双源核对 | — | 腾讯行情（盘中快照）+ 东财52周 + 新浪（独立价格第二源） |
| `financials <代码>` | 近5年核心财务（营收/净利/EPS/ROE） | — | 东方财富 |
| `valuation <代码>` | 估值指标（PE/PB/市值）+ 市值自洽验算 + 价格双源 + Tushare 股息率 | — | 腾讯行情 + 东财52周 + 新浪价格双源 |
| `search <关键词>` | 公司名/拼音 → 股票代码（≤10条） | — | 东方财富 suggest |
| `history <代码>` | 年报长序列：ROE/毛利率/净利率/OCF÷净利/利息覆盖 | `--years N`（1-50，默认10） | 东方财富（仅年报） |
| `equity-history <代码>` | 股本变动史（增发/回购/配售及原因） | — | 东方财富 |
| `signals <代码>` | 龙虎榜/资金流/解禁/融资融券四块信号（两融覆盖与时效见陷阱7） | `--date YYYY-MM-DD`（仅龙虎榜，且只在深交所备用源路径真正按日过滤） | 东财 → 新浪（资金流）/深交所（深市龙虎榜） |
| `announcements <代码>` | 公司公告列表（含PDF链接） | `--limit N`（1-100，默认20） | 巨潮 → 深交所（深市）/东财（沪/北） |
| `mainbz <代码>` | 主营业务构成 分产品 + 分地区（收入/占比/利润） | — | Tushare `fina_mainbz`（**分部独立第二源**，需 TUSHARE_TOKEN） |
| `managers <代码>` | 管理层履历：姓名/职位/性别/出生年/学历/任职起止/简历 | — | Tushare `stk_managers`（**补履历缺口**，需 TUSHARE_TOKEN） |
| `repurchase <代码>` | 股票回购：进度/数量/金额/价格上限 | — | Tushare `repurchase`（**回购结构化**，需 TUSHARE_TOKEN） |
| `pledge <代码>` | 股权质押：比例趋势/笔数（高质押=治理红线） | — | Tushare `pledge_stat`（**治理风险信号**，需 TUSHARE_TOKEN） |
| `express <代码>` | 业绩快报：财报前早期业绩（营收/净利/EPS/ROE/同比） | — | Tushare `express`（**提前量**，需 TUSHARE_TOKEN） |
| `kline <代码>` | 前复权日线序列 + 区间高低/收益 + 腾讯交叉 | `--days N`（默认120） | Tushare `daily`+`adj_factor`（**独立历史价源**，需 TUSHARE_TOKEN） |
| `audit <代码>` | 财务审计意见：是否标准无保留 + 事务所 + 费用 | — | Tushare `fina_audit`（**治理信号**，需 TUSHARE_TOKEN） |
| `holder-num <代码>` | 股东户数趋势（筹码集中度） | — | Tushare `stk_holdernumber`（需 TUSHARE_TOKEN） |
| `ratios <代码>` | 财务比率全景 ROE/扣非ROE/ROA/ROIC/毛利/净利/流动比/速动比/OCF·营收 | — | Tushare `fina_indicator`（**quality-screen 独立比率集**，需 TUSHARE_TOKEN） |
| `peers <代码>` | 行业可比公司池：申万一/二/三级成员股（industry-funnel 候选池） | `--level l1/l2/l3`（默认l3） | Tushare `index_member_all`（**候选池自动化**，需 TUSHARE_TOKEN） |
| `north-hold <代码>` | 北向持股趋势（占比/外资情绪） | — | Tushare `hk_hold`（沪深股通，需 TUSHARE_TOKEN） |
| `index-val [指数]` | 大盘估值分位：PE/PB 历史分位（市场择时锚） | 指数别名 hs300/zz500/sse/cyb…（默认hs300） | Tushare `index_dailybasic`（需 TUSHARE_TOKEN） |

> 上表为常用命令；CLI 另有一批 Tushare 增强命令（`pe-band`/`dividend`/`shareholders`/`management`/`consensus`/`research-visits`/`insider-trades`/`industry-pe`/`hk-quote`/`ah-cross-check`/`disclosure-calendar`/`news`），均需 `TUSHARE_TOKEN`，`python3 tools/ashare_data.py --help` 可列全。

---

## 执行流程

### 第一步：确认标的与需求

1. 跑 `date` 确认今天日期（CLAUDE.md 投研核心原则），作为"最新数据"基线。
2. 输入是公司名 → 先 `python3 tools/ashare_data.py search {关键词}` 定码；多个匹配列出候选让用户确认。
3. 用户没指定数据类型 → 默认取概览三件套（quote + valuation + financials），**不要默默把八个命令全跑一遍**。

### 第二步：按需取数

```bash
# 概览三件套：行情/估值/近5年财务
python3 tools/ashare_data.py quote <代码>
python3 tools/ashare_data.py valuation <代码>
python3 tools/ashare_data.py financials <代码>

# 长周期基本面：10年年报指标 + 股本变动史（历史稀释唯一正确口径）
python3 tools/ashare_data.py history <代码> --years 10
python3 tools/ashare_data.py equity-history <代码>

# 事件与市场信号证据
python3 tools/ashare_data.py announcements <代码> --limit 20
python3 tools/ashare_data.py signals <代码>
```

### 第三步：来源与数据时间标注

- `signals` / `announcements` 自带元信息块（数据源 / 是否备用源 / 数据时间 / ⚠️警告），呈现结果时**原样保留**，不得裁掉。
- 其余六个命令不打印来源元信息，转述数据时人工补记来源与取数时间：行情/估值 = 腾讯行情盘中快照 + 东财52周；财务/年报史/股本 = 东方财富。
- 结果标明"备用源"时（如公告降级到深交所/东财），必须明示"数据来自备用源"，不得当主源数据静默使用。

### 第四步：失败与数据不足处理

| 退出码 | 含义 | 处理 |
|--------|------|------|
| 0 | 成功且有数据 | 正常呈现 |
| 1 | 接口失败或无数据 | 标记"数据不足"，可用 WebSearch 补充（补充数据仍按 financial-data 双源规范验证） |
| 2 | 参数错误（如 `--years` 超出 1-50） | 修正参数重试 |

注意不对称：`history`/`equity-history`/`financials` 对**格式无效**代码（非6位数字/非法后缀）预校验退 2，格式合法但不存在的代码走接口查询、以空数据退 1；`quote`/`valuation` 不预校验代码，无效代码一律以"未找到"退 1；`announcements --limit` 超范围（非 1-100）由库层拦截退 1 而非 2。

- 任何失败**宁标"数据不足"，也不用推测填充**；不得用空结果伪装成功。
- `signals` 是部分成功语义：四块信号任一可用即成功，不可用的块逐条列在警告里——呈现时把不可用块明确说出来，不要只报可用的。

### full-company-analysis 集成

在 `full-company-analysis` 中不得只粘贴命令文本：基础命令由 gate 的 `run-ashare-command` 实际执行并冻结收据；随后至少登记三个可复用核心事实 `price`、`market_cap`、`revenue`。每条事实使用 gate 的 `fact_id/field/subject/period/unit/value/tolerance_pct/sources` 封闭结构，来源保留 publisher、document/URL、acquisition chain 与访问时间；取不到就记录限制，禁止默认值或估算填充。

ashare 报告的 `artifact_records` 必须把上述 fact IDs 与全部成功 command IDs 连接到 `assigned_artifacts` 中的 artifact ID。注册表 `conditional_command_operations.values` 指定的下游 `feeds` 必须引用同一 artifact ID 和对应 command ID，不得复制文本后丢失血缘。

---

## 数据陷阱（转述数据前必读）

1. **TOTAL_SHARE 是静态值**：东财财务主表的总股本字段是"当前股本"覆盖所有历史行，**禁止当历史股本用**；历史稀释/回购一律走 `equity-history`。
2. **valuation 的市值验算是自洽检查**（市值÷价格反推股本，偏差恒近0），不是独立核验；真核验走 `python3 tools/financial_rigor.py verify-market-cap` + `equity-history` 的最新总股本。
3. **无复权价格序列**：管线不提供日K/OHLC 历史价格；历史价格分析的复权口径规则见 `skills/financial-data.md`「股价与复权」。
4. **history 不输出自由现金流**：东财主表无可靠资本开支字段，FCF 须走现金流量表原文或第二来源。
5. **financials 年报缺失自动降级**：查不到年报时会去掉年报过滤重查，结果可能混入季报——同比前先核对报告期。
6. **52周高低来自东财**，限流取不到时显示 `-`（不算失败），不要把 `-` 转述成 0 或"无波动"。
7. **融资融券块只覆盖两融标的，且为 T+1 披露**：查无记录返回"数据不足"（empty_data）时应表述为"非两融标的或无两融记录"，不是工具缺陷；最新记录是上一交易日的余额，不要当盘中实时数据转述。
8. **沪市/北交所龙虎榜备用源均未实现**（仅深市有深交所备用），解禁无备用源：这些块主源失败即"数据不足"，无降级。
9. **北交所存量代码已迁 920 号段**：老号段代码（43/83/87 开头，如诺思兰德 430047→920047）在东财已查不到，`financials`/`history`/`equity-history` 用老码恒"数据不足"退 1（腾讯行情对老码仍留别名）——先 `search` 公司名拿现行 920 代码再取数。
10. **输出是中文文本，无 `--json`**：需要程序化处理时 import `tools.ashare_plugin` 库层（DataResult 契约含 ok/source/fallback_used/as_of/warnings）。

---

## 环境与时效

- Python ≥ 3.8，零外部依赖；网络走 `/usr/bin/curl` 直连（`--noproxy '*'`，15秒超时）；仅东财 JSON GET 接口对瞬时错误重试 1 次，腾讯行情与公告 POST 请求不重试。
- 行情为盘中快照，管线不缓存；财务数据随东财披露更新。
- `search` 用东财公开 token，可用环境变量 `EASTMONEY_SEARCH_TOKEN` 覆盖。

## Tushare 可选交叉验证

- 工具只检查 `TUSHARE_TOKEN` 是否存在；不要在对话、命令参数、报告或仓库中粘贴 token。
- 未配置时输出 `NOT_CONFIGURED` 且不发起 Tushare 请求；现有主数据流程和退出码不受影响。
- 已配置时，八个命令自动附加 Tushare 验证摘要：`MATCH`、`CONFLICT` 或 `INSUFFICIENT`。
- Tushare 不是传输 fallback；但在 `quote`、`valuation` 等同主体、同交易日、同单位的市场结构化字段发生 `CONFLICT` 时，输出以 Tushare 值为有效值，并逐项保留“主数据原值 → Tushare 值”的覆盖记录。
- 财务、公告、重大事项、文本名称、市场信号及任何期间/单位不一致字段不适用上述覆盖，仍保留原始披露或主数据值；接口无权限、限流或空数据时也不得覆盖。
- 实时腾讯行情只与同一交易日已更新的 Tushare 日终数据比较；日期不同必须为 `INSUFFICIENT`。
- `MATCH` 只代表对应字段在相同标的、期间、单位和报告版本下偏差不超过 1%，不代表整份报告自动完成双源核验。
- **扩展字段（`quote`/`valuation`）**：Tushare `daily_basic` 额外取 `pe_ttm`（对齐腾讯"动态PE"口径，可定位 `pe` 冲突是否只是 TTM vs 静态差）、`dv_ratio`（股息率）、`dv_ttm`、`ps_ttm`。`valuation` 会显示 Tushare `dv_ratio` 股息率，但**其口径可能含上一周期高分红、非前瞻收益率**，须按分红期间自行核对，不能直接当作研究报告的前瞻股息率第二源。

## 独立价格第二源（新浪）

`quote`/`valuation` 在腾讯行情之外，另取**新浪独立行情**（`hq.sinajs.cn`，不同发布主体、不同传播链）做价格双源核对：

- 输出 `价格双源` 行：`MATCH`（偏差≤1%，价格达真独立双源）/ `CONFLICT`（两源冲突，两值并列）/ 新浪不可用（价格暂为单源）。
- 这是真正的第二**行情链**（区别于 Tushare 交叉，也区别于东财 52 周），失败时静默降级为单源、从不打断主行情。
- 价格双源只覆盖现价；PE/PB/市值仍以腾讯为主、Tushare 交叉，不因价格双源自动升级为逐字段双源。

---

## 关键原则

1. **遵循 `CLAUDE.md` 客观性原则**——只呈现数据与来源，不加买卖倾向
2. **关键数据双源交叉验证**——市场结构化字段的合格 Tushare 冲突按本节优先级选择有效值并保留冲突证据；财务与披露类冲突仍按 `skills/financial-data.md` 核对巨潮原始披露
3. **来源、数据时间、警告三要素随数据一起呈现**——备用源降级必须明示
4. **宁标"数据不足"，不用推测填充**
5. **不替用户做决策**——本 skill 只产数据证据；本项目用于学习研究，不构成投资建议
