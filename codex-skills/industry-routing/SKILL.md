---
name: industry-routing
description: 行业附录路由（按需加载）：20 个行业的主附录选择、必备 KPI、主估值方法与 berkshire A股数据命令映射。消费方 skill（investment-research / earnings-review / industry-research / industry-funnel）在选定行业附录前读取本文件，避免机械按 GICS 标签选行业。
owner: psilhon
category: 数据与思维工具
maturity: stable
review-cadence: per-release
---

## Codex adapter note

This skill is generated from `skills/industry-routing.md` so Claude Code and Codex users share one canonical workflow.

- Treat `$ARGUMENTS` as the user's request in the current Codex thread.
- When the source mentions Claude-only surfaces such as Task, Agent, WebSearch, Bash, Read, or Write, use the closest Codex capability available in this session: subagents when available, web search when needed, shell commands for local tools, and normal file edits for workspace files.
- Use shared project tools from `tools/` in this repository. Prefer running commands from the repository root with paths like `python3 tools/financial_rigor.py ...`; if the current thread starts outside the repo, locate the actual checkout path first instead of assuming a fixed home-directory path.
- Before starting research, run the `date` command to confirm today's date; treat it as the baseline for "latest" data and state the data cutoff date in the report header. Never assume the current date from training data.
- Preserve the research quality rules from `AGENTS.md`: cross-check financial data, use exact arithmetic tools for valuation/math, and clearly label uncertainty and source gaps.

# 行业附录路由（Industry Routing，按需加载）

> 来源移植：本文件路由矩阵与选择协议移植自外部项目 `rollingSirius/equity-research-skill` 的 `references/industry-routing.md`（MIT），经 berkshire 纪律改写——补充 **A股数据命令映射列**（对应 `ashare_data.py` / Tushare），并保留预测登记与复盘字段。它是行业分类的**唯一人工可读入口**，不替代 `industry-research` / `industry-funnel` 的行业级扫描，只在单公司研究时决定「读哪个附录、抓哪些 KPI」。

## 1. 选择协议

1. 按**未来 3–5 年价值贡献**选一个主附录：以收入占比、正常化利润、投入资本、估值权重共同判断，不单看收入标签。
2. **只有当次业务会改变 KPI、现金流模型或估值方法时**，才加载一个次附录。
3. 控股公司或三个以上重要分部 → 按 SOTP 拆分；每个分部各用适用附录，集团层单列净债务、总部费用、交叉持股。
4. 无完全匹配 → 选经济模型最接近的附录，并在报告中写明适配与未覆盖项。
5. 附录中的表名/字段名/必写结论是语义要求；按报告语言完整翻译，不得中英模板混排。

## 2. 路由矩阵（20 行业 + berkshire A股数据映射）

| 主附录 | 适用边界 | 主估值方法 | 必备 KPI | 常用次附录 | berkshire A股数据命令 |
|---|---|---|---|---|---|
| SaaS | 订阅/用量制软件 | 反向 DCF、DCF、EV/ARR | NRR/RPO、获客效率、Rule of 40 | 互联网/平台、硬件 | `peers`、`mainbz`、`ratios`、`report-list`（行业研报）、`ths-hot`（热度旁证） |
| 半导体 | 芯片/代工/设备/材料/EDA/IP/封测 | 跨周期 DCF、EV/EBITDA、P/E | 库存周期、ASP/units、良率/利用率 | 硬件、工业 | `peers`、`mainbz`、`ratios`、`kline`（周期） |
| 银行 | 吸收存款承担信用风险的持牌银行 | 剩余收益、P/TBV-ROTCE | NIM、deposit beta、信用成本、CET1 | 支付/金融科技、资本市场 | `financials`、`ratios`、`balance-sheet`、`audit` |
| 保险 | 财险/寿险/健康险/再保险 | P/B-ROE、P/EV、剩余收益 | combined ratio/VNB、准备金、偿付能力 | 资本市场 | `financials`、`ratios`、`balance-sheet` |
| 医药 | 药品研发与商业化 | 逐资产 rNPV、SOTP | PoS、催化剂、LOE、cash runway | 医疗服务 | `announcements`、`report-list`、`express`（业绩快报） |
| 医疗服务/器械/CRO-CDMO | 医院/诊所/器械/诊断/研发生产外包 | DCF、EV/EBITDA、SOTP | 量/利用率、报销、订单/单位经济 | 医药、工业 | `mainbz`、`ratios`、`announcements` |
| 消费 | 品牌/零售/餐饮/消费品 | DCF、P/E、EV/EBITDA | 量价 mix、同店、sell-through、库存 | 互联网/平台、硬件 | `mainbz`、`ratios`、`financials`、`history` |
| 能源 | 上游油气/中游/炼化/综合能源 | 资产 NAV、周期 DCF | 储量/递减、完全成本、套保 | 公用事业、化工工业 | `financials`、`ratios`、`history`（10年） |
| 公用事业 | 受监管水电气/竞争发电/长期合同基建 | rate-base、DDM、DCF | allowed/earned ROE、融资、账单 | 能源、REIT | `financials`、`ratios`、`divident`（分红） |
| 互联网/平台 | 广告/电商/本地生活/平台型 | 分部 SOTP、DCF | 用户参与、变现率、GMV/take rate | 支付、SaaS、媒体游戏 | `peers`、`mainbz`、`report-list`、`ths-hot` |
| 支付/金融科技 | 卡组织/收单/钱包/BNPL/交易所 | DCF、EV/收入、P/E | TPV、净 take rate、损失 vintage | 银行、资本市场 | `financials`、`ratios`、`pledge`（治理） |
| 资本市场基础设施 | 资管/交易所/券商/评级 | DCF、P/E、AUM/交易量驱动 | AUM/净流入或交易量、费率、资本 | 银行、支付 | `financials`、`ratios`、`repurchase` |
| 地产/REIT | 权益 REIT/mREIT/开发商 | NAV、P/AFFO、DDM | FFO/AFFO、同店 NOI、cap rate、债务墙 | 公用事业、银行 | `financials`、`equity-history`、`ratios` |
| 工业/机械 | 设备/自动化/航空航天/工程制造 | 中周期 DCF、EV/EBITDA | 订单、book-to-bill、backlog、后市场 | 半导体、硬件 | `mainbz`、`ratios`、`announcements` |
| 电信 | 移动/固网/铁塔/卫星 | DCF、SOTP、股息率 | ARPU/churn、capex、频谱、股息覆盖 | REIT、媒体 | `financials`、`ratios`、`divident` |
| 汽车/EV | 整车及整车经济为核心的公司 | 周期 DCF、SOTP | 单车经济、盈亏平衡销量、库存/runway | 硬件、消费 | `mainbz`、`ratios`、`financials`、`kline` |
| 金属/矿业 | 矿商/勘探开发 | 分矿山 NAV、期权法 | AISC/成本曲线、储量、矿山寿命 | 工业、能源 | `financials`、`history`、`ratios` |
| 航空/运输 | 航空/机场/铁路/航运/快递物流 | 周期 DCF、NAV、EV/EBITDA | 单位收益/成本、载运率、运力订单簿 | 公用事业、REIT | `financials`、`ratios`、`announcements` |
| 游戏/媒体/内容 IP | 游戏/影视/音乐/出版/流媒体/IP授权 | IP SOTP、DCF、EV/订户 | 用户/受众、付费、内容 ROI、摊销 | 互联网/平台、消费 | `mainbz`、`report-list`、`ths-hot` |
| 硬件/消费电子/AI 服务器 | 设备/终端/服务器及硬件系统 | 周期 DCF、EV/EBITDA、SOTP | units/ASP/BOM、库存、客户供应商集中 | 半导体、SaaS、工业 | `mainbz`、`ratios`、`peers`、`kline` |

> `peers` 用 Tushare `index_member_all` 取申万一/二/三级成员股作候选池；`mainbz` 用 Tushare `fina_mainbz` 作分部独立第二源；行业研报用东财 `reportapi`（零鉴权）。全部命令见 `skills/ashare-data.md`。

## 3. A股一手数据入口

优先当地监管申报与公司 IR，定义明确的附注为先：

| 行业 | A股一手/官方入口 |
|---|---|
| 通用公司申报 | 巨潮 `cninfo.com.cn` · 东方财富 F10 · 公司 IR |
| SaaS/互联网/消费/硬件 | 公司 IR + 监管申报；用户/渠道数据仅作辅助证据 |
| 半导体 | 公司公告（产能/良率）+ 行业研报（`report-list` 行业码）+ WSTS/SIA/SEMI（全球） |
| 银行/支付 | 人民银行 `pbc.gov.cn` · 国家金融监督管理总局 `nfra.gov.cn` · 年报附注（NIM/信用成本） |
| 保险 | 国家金融监督管理总局 · 年报（VNB/准备金/偿付能力） |
| 医药/医疗 | NMPA `nmpa.gov.cn` · 临床登记 + 公司公告（临床/获批） |
| 能源/公用事业 | 公司 supplemental + 监管电价/气量文件 |
| 资本市场 | 交易所披露 + 公司公告（AUM/费率） |
| 地产/REIT | 公司 supplemental（FFO/AFFO/NOI）+ 土地登记 |
| 电信 | 工信部统计 `miit.gov.cn` + 公司年报（ARPU/churn） |
| 汽车 | 中国汽车工业协会 `caam.org.cn` + 公司产销快报（`announcements`） |
| 金属/矿业 | 公司公告（储量/产量）+ LME/CME（全球价） |
| 航空/运输 | 国家邮政局 `spb.gov.cn` + 公司运营数据公告 |
| 游戏/媒体 | 版署/广电监管 + 公司公告（流水/付费） |

付费库/行业媒体可发现线索，但不得替代可获得的原始来源。每次引用记录 URL、发布日期、数据期间、定义、抓取日期。

## 4. 预测登记与复盘

行业结论必须写成可追踪预测，而非宽泛观点。每条核心预测至少记录：

| 字段 | 要求 |
|---|---|
| 预测对象 | 明确 KPI/价格/利润率/事件/估值变量 |
| 基准值与截止日 | 当前值、期间、来源 |
| 预测区间与期限 | 区间、方向、明确验证日期 |
| 驱动与先行指标 | 2–4 个可观测变量及阈值 |
| 失效条件 | 上行/下行证伪分别定义 |
| 复盘结果 | 命中/部分命中/未命中/无法验证 |
| 误差归因 | 数据/时间/模型/外生冲击/论点错误 |
| 模型回写 | 调整假设/情景概率/数据源；不得事后改写原预测 |

更新报告时保留旧预测及时间戳，新增复盘行。股价变化只能作为市场结果之一，不得单独证明经营预测对错；须分别复盘经营 KPI、催化剂路径、估值倍数、总回报。

## 5. 消费方 skill 调用约定

- `investment-research`：第一步行业分类、第七步估值方法选择前，先读本文件选定主/次附录与必备 KPI。
- `earnings-review`：阶段一四大师分析、阶段二行业对比前，按本文件锁定行业 KPI 与对比口径。
- `industry-research` / `industry-funnel`：行业级扫描后落地到单公司时，用本文件决定单公司估值方法与 KPI。
- 只读按需加载，不强制全文载入；报告头部声明 `行业附录: <slug>[, <slug>]` 供检查器复核。

---

*仅供学习研究，不构成投资建议。行业路由矩阵移植自 rollingSirius/equity-research-skill（MIT）。*
