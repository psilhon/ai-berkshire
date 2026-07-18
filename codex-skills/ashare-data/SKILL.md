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
| `quote <代码>` | 实时行情快照 + 52周高低 | — | 腾讯行情（盘中快照）+ 东财52周 |
| `financials <代码>` | 近5年核心财务（营收/净利/EPS/ROE） | — | 东方财富 |
| `valuation <代码>` | 估值指标（PE/PB/市值）+ 市值自洽验算 | — | 腾讯行情 + 东财52周 |
| `search <关键词>` | 公司名/拼音 → 股票代码（≤10条） | — | 东方财富 suggest |
| `history <代码>` | 年报长序列：ROE/毛利率/净利率/OCF÷净利/利息覆盖 | `--years N`（1-50，默认10） | 东方财富（仅年报） |
| `equity-history <代码>` | 股本变动史（增发/回购/配售及原因） | — | 东方财富 |
| `signals <代码>` | 龙虎榜/资金流/解禁/融资融券四块信号（两融覆盖与时效见陷阱7） | `--date YYYY-MM-DD`（仅龙虎榜，且只在深交所备用源路径真正按日过滤） | 东财 → 新浪（资金流）/深交所（深市龙虎榜） |
| `announcements <代码>` | 公司公告列表（含PDF链接） | `--limit N`（1-100，默认20） | 巨潮 → 深交所（深市）/东财（沪/北） |

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

---

## 关键原则

1. **遵循 `CLAUDE.md` 客观性原则**——只呈现数据与来源，不加买卖倾向
2. **关键数据双源交叉验证**——市场结构化字段的合格 Tushare 冲突按本节优先级选择有效值并保留冲突证据；财务与披露类冲突仍按 `skills/financial-data.md` 核对巨潮原始披露
3. **来源、数据时间、警告三要素随数据一起呈现**——备用源降级必须明示
4. **宁标"数据不足"，不用推测填充**
5. **不替用户做决策**——本 skill 只产数据证据；本项目用于学习研究，不构成投资建议
