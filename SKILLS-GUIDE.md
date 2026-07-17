# Skills 使用指南（Claude Code）

本文档是 20 个投研 Skill 在 Claude Code 环境下的完整使用说明：调用格式、参数、执行流程、产出位置与注意事项。Codex 用户请参考 [AGENTS.md](AGENTS.md) 与 `codex-skills/`（由 `skills/*.md` 自动生成，工作流一致）。

> 数据截止：2026-07-17，基于 `skills/*.md` 当前版本整理。Skill 源文件更新后，以源文件为准。

## 目录

- [通用前提](#通用前提)
- [🔬 深度研究类](#-深度研究类)：investment-research / investment-team / management-deep-dive / private-company-research / deep-company-series
- [📊 财报分析类](#-财报分析类)：earnings-review / earnings-team
- [🏭 行业筛选类](#-行业筛选类)：industry-research / industry-funnel / quality-screen / bottleneck-hunter / investment-checklist
- [📈 持仓管理类](#-持仓管理类)：portfolio-review / thesis-tracker / thesis-drift / news-pulse
- [🧠 思维工具类](#-思维工具类)：dyp-ask / financial-data / ashare-data / wechat-article
- [典型工作流串联](#典型工作流串联)

---

## 通用前提

### 安装与调用

```bash
# 安装：把 skills/*.md 复制到 ~/.claude/commands/
bash scripts/install-claude-commands.sh
```

之后在 Claude Code 里用 `/skill名 参数` 调用，skill 文件中的 `$ARGUMENTS` 即斜杠命令后跟的文字。在本仓库工作区内可直接调用，无需安装。

### 权限准备

多 Agent 类 skill（investment-team / news-pulse / earnings-team 等）依赖 WebSearch 已在 `settings.local.json` 的 `permissions.allow` 放行。`/investment-team` 启动前会强制预检——未放行会停下不启动，避免后台 Agent 被静默拦截后用训练知识伪装联网结果（issue #58 教训）。

如果信任当前环境，也可用 `claude --dangerously-skip-permissions` 启动以减少逐次授权确认（注意该模式关闭工具审批保护）。

### 所有 Skill 共享的红线

- 研究开始前先跑 `date` 确认今天日期，以此为"最新数据"基线并在报告头标注数据截止日
- 关键财务数据至少 2 个独立来源交叉验证，误差 >1% 须标记（规范见 `/financial-data`）
- 涉及计算一律走 `python3 tools/financial_rigor.py`（Decimal 精确），禁止 LLM 心算
- 发布级报告须过 `python3 tools/report_audit.py` 抽检，拿到【准出】判决才能发布
- 数据不足诚实标注"数据不足"，不用推测填充；事实与观点严格区分

### 成本梯度

| 量级 | Skill | 说明 |
|------|-------|------|
| 轻量 | dyp-ask、ashare-data | 纯对话 / 单线取数，分钟级 |
| 中量 | investment-checklist、quality-screen、news-pulse、financial-data | 少量并行 Agent 或批量取数 |
| 重量 | investment-research、earnings-review、industry-research、industry-funnel、bottleneck-hunter、portfolio-review、thesis-tracker、thesis-drift、wechat-article | 大量联网搜索 + 工具验算 + 抽检 |
| 极重 | investment-team、earnings-team、private-company-research、deep-company-series | 4-8 个 Agent 并行，token 量级大 |

控制成本的正确方式是调整 workflow 而非期待深度研究变便宜：先用 `/quality-screen` 或 `/news-pulse` 初筛，值得深入再上重量级。

### 报告落盘口径

个别 skill 文件正文写的输出路径是 `~/xxx.md` 或 `reports/` 根目录，与项目 CLAUDE.md 的报告命名表不完全一致。**在本仓库内执行时以 CLAUDE.md 命名表为准**：公司相关报告进 `reports/{公司名}/` 文件夹，行业/主题/组合报告放 `reports/` 根目录。新增报告后跑 `python3 scripts/build_report_index.py` 重建索引。

---

## 🔬 深度研究类

### /investment-research — 四大师框架单公司系统研究

```
/investment-research 腾讯控股
/investment-research 苹果 AAPL
/investment-research 600519 贵州茅台
```

- **参数**：公司名或股票代码，任意可识别公司的文本
- **流程**：单主线八步——AI 研究偏见自觉（信息丰富度 A/B/C 评级）→ 数据收集 + financial_rigor 交叉验证 → 段永平生意本质 → 巴菲特护城河（五类逐一验证）→ 芒格逆向思考风险清单 → 管理层评估 → 反向 DCF + 三情景估值 → 综合决策备忘录（含四大师模拟点评）
- **产出**：`reports/{公司名}/{公司名}-research-{YYYYMMDD}.md`，结论须给明确买入/观望/回避与具体价格区间
- **注意**：C 级（信息稀缺）公司必须附"需一手验证的问题清单"；报告结尾区分"AI 分析置信度"与"投资确定性"

### /investment-team — 4 Agent 并行投研团队（最快最全）

```
/investment-team 美团
```

- **参数**：公司名
- **流程**：主代理任 team-lead，同一条消息并行启动 4 个后台 Agent——商业模式（段永平视角）/ 财务估值（巴菲特视角）/ 行业竞争（芒格视角）/ 风险与管理层（李录视角），各自联网研究后 SendMessage 回传，team-lead 汇总输出含四维评分、Bull/Bear、巴菲特 Checklist、分层操作建议的最终报告
- **产出**：`reports/{公司名}/` 目录——README + 01-04 四视角分报告 + `最终报告.md`
- **注意**：启动前强制 WebSearch 权限预检；Agent 联网失败禁止伪装，须在报告顶部醒目标注降级声明；4 Agent 并行需几分钟，期间会向用户更新进度

### /management-deep-dive — 管理层纵深研究

```
/management-deep-dive 美团
/management-deep-dive 王兴 美团
/management-deep-dive 黄仁勋 英伟达
```

- **参数**：公司名，或「人名 公司名」
- **流程**：先 WebSearch 识别关键决策人物，再并行派 4 个 Agent 收集——CEO 公开发言与预测记录 / 资本配置决策 / 治理结构与薪酬 / 侧面口碑（员工/客户/行业）；按诚信 35% + 战略执行 25% + 资本配置 25% + 治理 15% 加权评分，套段永平"买人"三问定星级
- **产出**：`reports/{公司名}/{公司名}-management-{YYYYMMDD}.md`
- **注意**：**诚信一票否决**（第一问不是"是"直接 ★1 不投）；看行为不看言辞、在困难中看真相、不要爱上管理层
- **适用场景**：常规研究中管理层评分 ≤★3 拿不准，或管理层本身是核心投资逻辑时

### /private-company-research — 未上市公司深度研究

```
/private-company-research 蚂蚁集团
/private-company-research SpaceX
```

- **参数**：未上市公司名称，中英文均可
- **流程**：展示 7 角色团队框架确认后启动，6 个侦探式 Agent 并行——商业解码 / 财务拼凑与估值 / 竞争地图 / 风险治理 / 技术 IP / 替代数据信号挖掘；team-lead 做数据冲突仲裁、信号一致性检验、反偏见检查后汇总
- **产出**：`reports/{公司名}/{公司名}-private-{YYYYMMDD}.md`，含六维评分、估值区间（保守/合理/乐观 + 安全边际）、一页纸投资决策表、信息盲区地图
- **注意**：每个关键数据标来源、时间与置信度（🟢🟡🔴），推算过程完全透明；严防虚假保守/虚假精确/对标陷阱/幸存者偏差；信息极度稀缺时切"第一性原理模式"，诚实留白不硬凑估值

### /deep-company-series — 《看懂XX》3-8 篇长文系列（量级最大）

```
/deep-company-series 腾讯
```

- **参数**：公司名
- **流程**：四阶段——①调研（近 5 年年报 + 最新季报 + ≥3 份卖方研报，先用 /investment-team 或 /investment-research 打内部底稿，与用户确认各篇论点）→ ②按公司复杂度定篇数（复杂 7-8 篇约 12 万字 / 简单 3 篇约 3 万字），按 01→末篇顺序写作 → ③Explore agent 并行做跨篇一致性检查 → ④发布前 grep 隐私扫描，确认后才推送
- **产出**：`reports/{公司名}/《看懂{公司名}》-{YYYYMMDD}/` 目录，每篇 `0X-标题.md`，另有 `00-系列说明.md` 目录索引；旧版目录已存在时不覆盖不混放
- **注意**：严禁"伪精确"——不做概率加权期望值、不线性外推增速、未公开持股只给区间标"不可知"；禁用"显然/必然/严重低估"等词；写完**不立即 push**，等用户审阅修订后才推送

---

## 📊 财报分析类

### /earnings-review — 财报精读（单人视角，一手资料）

```
/earnings-review 腾讯 2025Q4
/earnings-review PDD 2025年报
/earnings-review 美团 最新
```

- **参数**：「公司名 期间」；"最新"或省略期间时默认读最近一期财报
- **流程**：先做资料可得性 A/B/C 评级；并行 Agent 抓一手资料（财报原文/电话会纪要/致股东信/投资者日材料）→ 核心数据提取 + financial_rigor 验证 → MD&A 管理层语气与承诺追踪 → 附注与异常信号挖掘 → ≥4 季度趋势与指引对比 → 七部分精读报告 → report_audit 抽检
- **产出**：`reports/{公司名}/{公司名}-earnings-{期间}.md`
- **注意**：读原文不读摘要，拿不到完整原文必须标注"非原始财报，来自第三方汇总"；结论必须明确判超/符合/低于预期，禁止"基本符合"式两面话；核心原则——看变化不看绝对值、听语气不只听内容、查附注不只看正文

### /earnings-team — 6 Agent 财报团队 + 公众号成稿

```
/earnings-team 腾讯 2025Q4
```

- **参数**：同 /earnings-review
- **流程**：三阶段——①并行抓一手资料并评级，随后 4 个大师视角 Agent 并行解读：段永平（生意本质）/ 巴菲特（财务质量审计）/ 芒格（竞争格局）/ 李录（风险信号与承诺追踪）→ ②team-lead 合成研究底稿，重点找四视角的共识、矛盾和被忽略的角落 → ③编辑 Agent（改写 1000-3000 字公众号文章）+ 读者 Agent（四维度评审）并行，处理"必须修改"项后定稿并抽检
- **产出**：全在 `reports/{公司名}/`——`{公司名}-earnings-{期间}.md`（定稿）+ `-研究底稿.md` + 四视角分报告（`-段永平/-巴菲特/-芒格/-李录.md`）+ `-读者评审.md`
- **注意**：四视角必须相互印证挑战而非各说各话；每个积极发现附反面论据；定位为"重要公司的关键财报"专用，日常财报用 /earnings-review 即可

---

## 🏭 行业筛选类

### /industry-research — 产业链全景研究

```
/industry-research 核电
/industry-research AI算力液冷
/industry-research 人形机器人产业链
```

- **参数**：行业名或投资主题/逻辑链描述
- **流程**：八步——投资逻辑链构建与逐环节验证（找已落地的验证事件）→ 产业链全景图并标记卡脖子环节 → 全球上市公司扫描分 Tier 1-4（后台 Agent 全市场搜索）→ 各环节 Tier 1/2 公司四大师分析 → 行业级风险清单 + 历史类比 → 李录文明趋势判断 → 核心/卫星/期权/ETF 四层组合配置 + 买卖信号 → 综合决策备忘录
- **产出**：`reports/{行业名}-industry-{YYYYMMDD}.md`（reports/ 根目录）
- **注意**：要求中英文双语信源、主动搜冷门标的（对抗成熟行业/龙头/上市/英文四类偏好）；每家公司标注信息充分度 A/B/C 级

### /industry-funnel — 全市场漏斗筛到 3 家

```
/industry-funnel AI 算力
/industry-funnel 创新药
```

- **参数**：行业名或投资方向
- **流程**：五步漏斗——①全市场扫描（活跃度∪涨幅∪市值并集得 30-60 家，覆盖 A股/港股/美股/国际/未上市）→ ②5 条硬指标粗筛到 ≤10 家 → ③逐家 300-500 字精析，按组合互补性（高确定性/成长型/期权型）终选 3 家 → ④对 3 家做四大师深度分析（每家 800-1200 字）→ ⑤组合表 + ETF 替代 + 信息充分度自评
- **产出**：`reports/{行业名}-funnel-{YYYYMMDD}.md`（reports/ 根目录）
- **注意**：每层淘汰的公司必须留名字 + 淘汰理由，不能黑箱；行业占比 <30% 的"沾边股"标注"非纯正标的"；凑不齐 3 家宁写"2 家 + 1 家观察"不凑数
- **与 /industry-research 的分工**：research 偏产业链结构全景，funnel 偏个股筛选；可先 research 看格局，再 funnel 精选标的

### /quality-screen — 7 条硬指标去劣快筛

```
/quality-screen 腾讯, 美团, 英伟达
/quality-screen 中国啤酒行业
/quality-screen 恒生指数成分股
```

- **参数**：个股（可多家）/ 行业名 / 市场指数 / 主题，后三种走批量模式（先搜 10-30 家清单再逐家筛）
- **流程**：7 条排除指标（10 年均 ROE<8%、5 年累计 FCF 为负、利息覆盖 <2、毛利率 <15%、OCF/NI<0.7、净利率 <5%、5 年股本膨胀 >20%）+ 3 条豁免规则（战略投入期/主动低利润率/高周转薄利）；A股优先走 `tools/ashare_data.py` 管线，批量模式每家一个并行 Agent
- **产出**：汇总表（✅❌⚠️）+ 通过/排除/豁免/边界争议分节；批量模式附通过率与行业选股结论
- **注意**：宁可漏网不可误杀，**通过 ≠ 一流**（去劣只是第一步）；金融股按专用口径（③④不适用，判决主要依托①⑥⑦）；历史股本禁用 TOTAL_SHARE 静态值，须走 equity-history 或净利润÷EPS 反推

### /bottleneck-hunter — 供应链瓶颈猎手

```
/bottleneck-hunter AI基础设施
/bottleneck-hunter          # 无参数则扫内置 5 大超级趋势
```

- **参数**：超级趋势名（可选）
- **流程**：七步——超级趋势确认 → 供应链物理拆解到 Layer 0-4（重点扫 2-3 层）→ 按 6 条标准给瓶颈定 S/A/B 级 → 对 S/A 级找上市公司 + 估值红黄绿灯检查 → 正向验证 + 芒格式反向验证 → 瓶颈机会排名表 + 行动建议 → 增量更新总地图与观察名单
- **产出**：全在 `reports/bottleneck-map/`——完整扫描 `{趋势名}-bottleneck-{YYYYMMDD}.md`、每日扫描 `daily/`、持续维护 `master-map.md` 与 `watchlist.md`
- **注意**：**估值是硬门槛不可跳过**——红灯（市值>TAM 20% / PS>30x 且增速<100% 等）则信号强度封顶★★，瓶颈纯正度和叙事都不能覆盖；需刻意搜日韩台小市值供应商对抗偏见
- **衔接**：发现 S 级瓶颈 + 多重验证的标的后，转 /investment-team 深入研究

### /investment-checklist — 巴菲特买入前六关（10 分钟级）

```
/investment-checklist 腾讯
/investment-checklist 腾讯, 茅台, 英伟达
/investment-checklist NVDA AAPL MSFT
```

- **参数**：单个或多个公司名/代码，逗号/顿号/空格分隔，中英文均可
- **流程**：每家一个并行数据收集 Agent → 逐家过六关（能力圈/好生意/护城河/管理层/安全边际/仓位纪律，前五关 ★1-5 评分）→ 镜子测试（5 句话说清买入理由）→ 8 条快速否决清单 → 多公司附总览对比表
- **产出**：`reports/{公司名}/{公司名}-checklist-{YYYYMMDD}.md`；结论四态——通过/未通过/灰色地带/N-A
- **注意**：说不清赚钱方式直接标"不在能力圈"；管理层严重诚信问题（★1）直接否决；镜子测试不可跳过，5 句话说不完整 = 不买；C 级公司不勉强填满表格，"数据不足"标灰色地带
- **定位**：买入前排除坏选择的快速把关，通过后才进 /investment-research 或 /investment-team

---

## 📈 持仓管理类

### /portfolio-review — 组合审视与优化

```
/portfolio-review 腾讯30%, 美团20%, 茅台20%, 英伟达15%, 现金15%
/portfolio-review 腾讯 500股 @480港元, 美团 1000股 @130港元
/portfolio-review 我的持仓          # 需已有 reports/portfolio-latest.md
```

- **参数**：持仓清单，支持比例式 / 股数+成本价式 / "我的持仓"三种
- **流程**：七步——解析持仓标准化 → 按持仓数并行取数（股价/估值/财务变化/重大事件/一致预期）→ 单仓位体检（三问：现价还买吗 / 持有 5 年舒服吗 / 论文完整吗）→ 组合层分析（集中度/相关性/机会成本排序/四情景压力测试）→ 调仓建议 → 输出报告 → 更新组合文件
- **产出**：持续更新单一文件 `reports/portfolio-latest.md`（非按日期建新文件）；结论必须明确回答组合健康度、最应该做的一件事、当前最大风险
- **注意**：**该文件只存本地永不入库**（.gitignore 已排除）；机会成本判据是"排名最后的持仓预期回报是否高于现金（无风险利率约 4%）"；每季度审视一次足够，不过度交易；不在本 skill 内直接推荐替换个股

### /thesis-tracker — 投资论文追踪（买入后纪律系统）

```
/thesis-tracker 腾讯              # 自动判断：无论文则建立，有则追踪检查
/thesis-tracker 茅台 建立论文      # 强制重建
/thesis-tracker 苹果 季度检查      # 基于最新财报检查
```

- **参数**：公司名，可选后缀"建立论文"/"季度检查"
- **流程**：建立模式——200 字以内 5 句话核心论文 + 3-7 条可验证核心假设 + 红线清单 + 估值锚点；追踪模式——收集最新财报/事件/股价/内部人交易 → 逐条验证假设（🟢成立/🟡弱化/🔴受损/⚫破裂）→ 红线检查 → 健康度评分（10 − 破裂×3 − 受损×2 − 弱化×1 − 红线触发×5，1-10 分封顶保底）→ 明确加仓/持有/减仓/清仓建议，检查记录追加回论文文件
- **产出**：长期维护 `reports/{公司名}/{公司名}-thesis.md` 单文件
- **注意**：5 句话写不完整 = 买入决策不清晰；红线触发必须醒目标注并给明确行动建议；已有 /investment-research 或 /investment-team 报告时优先从中读取数据

### /thesis-drift — 论文漂移检测

```
/thesis-drift 腾讯                # 自动查找论文与历史快照
/thesis-drift 腾讯 reports/腾讯-thesis-20250101.md reports/腾讯-thesis-20260701.md
```

- **参数**：公司名，或「公司名 旧报告路径 新报告路径」
- **流程**：证据归一化 → financial_rigor 数值校验 → 五维度（估值锚点/核心假设/红线/管理层/护城河）逐一判 Improved/Unchanged/Weakened，每个非 Unchanged 结论必须引用具体新证据 → 八段式漂移报告
- **产出**：漂移检测报告（不产出固定命名新文件）；缺基线时建议将当前报告存为基线
- **注意**：**只比证据不比文风**——同义改写/排序/语气变化判 Unchanged，找不到证据不得推断漂移；股价涨跌只影响估值锚点，不自动改变生意质量判断；红线触发优先级高于估值便宜；缺基线不得凭记忆补造旧论文
- **依赖**：需要 /thesis-tracker 建立的结构化基线，无基线时会引导先建

### /news-pulse — 股价异动快速归因（10-15 分钟级）

```
/news-pulse 拼多多 最近14天 股价3天跌12%
/news-pulse 腾讯 财报后异动 回溯7天
/news-pulse 600519
```

- **参数**：公司标识（中/英文名/代码），可附时间窗口（默认回溯 14 天）、异动描述、关注侧重；缺参数会先反问澄清
- **流程**：信息可得性 A/B/C 分级后，4 个并行侦察 Agent——公司事件 / 监管政策 / 行业与竞争对手 / 市场情绪与卖方大V，合成异动归因报告（一句话归因 + 合并时间线 + 归因表 + 性质判断四选一 + 行动建议 + 7-30 天跟踪清单）
- **产出**：`reports/{公司名}/{公司名}-news-{YYYYMMDD}.md`
- **注意**：定位是快速情报响应，不要陷入深度分析；找不到主因明确写"真因不明"——这是最危险的结论，可能市场在抢跑；区分催化剂与巧合（影响量级要匹配）；不因持仓预设"情绪波动没事"
- **适用场景**：单日 ±5%、一周 ±10% 的股价异动，或判断新闻是噪音还是信号

---

## 🧠 思维工具类

### /dyp-ask — 段永平第一人称问答（最轻量）

```
/dyp-ask 腾讯现在算好生意吗？
/dyp-ask 该不该借钱加仓？
/dyp-ask 怎么看多元化经营
```

- **参数**：任意问题——投资、商业、人生、具体公司、宏观均可
- **流程**：纯角色扮演问答，不联网不派 Agent；内化段永平九大思想体系（投资信仰/生意模式/Stop doing list/能力圈/估值买卖/企业文化/管理层/宏观/平常心），按问题类型路由框架回答
- **产出**：对话中直接回答，不产出文件
- **注意**：不给精确股价目标、不预测市场走势、不推荐具体买卖；能力圈外问题坦诚"我不懂"

### /financial-data — 财务数据获取与双源验证规范

```
/financial-data 腾讯 0700 双源验证近三年收入与净利润
/financial-data 600519
```

- **参数**：公司名/代码（美股 ticker、港股代码、A股六位码）；也可无参数作为规范加载供其它 skill 遵守
- **流程**：对每个财务指标从两个独立来源取数（美股 macrotrends+stockanalysis+SEC EDGAR、港股 aastocks+macrotrends ADR+HKEX、A股 东方财富+巨潮），计算误差率按三档处理：≤1% ✅ / 1-5% ⚠️ 标记 / >5% ❌ 必须查原始财报核实
- **产出**：规范格式数据块（主数值 + 两来源数值 + 误差率 + 标记），供研究报告引用
- **注意**：ashare_data 只算一个证据来源，不豁免双源验证；历史价格统一前复权且同一分析内不混用口径；总回报需计入分红；两来源均与原始财报不符时以原始财报为准
- **定位**：数据质量基础规范，被 investment-research / earnings-review 等所有取数环节引用

### /ashare-data — A股数据统一入口

```
/ashare-data 600519           # 概览三件套：行情+估值+近5年财务
/ashare-data 600519 公告      # 只取指定类型：行情/财务/估值/年报史/股本/公告/信号
/ashare-data 贵州茅台          # 公司名先 search 定码
```

- **参数**：六位代码 / 代码+数据类型 / 公司名 / 带后缀代码（000651.SZ，支持 .SH/.SZ/.BJ）
- **流程**：先跑 `date` 定基线，按需调用 `python3 tools/ashare_data.py` 八个子命令（quote/financials/valuation/search/history/equity-history/signals/announcements）；零外部依赖，腾讯行情+东方财富+巨潮 curl 直连；结果保留数据源/数据时间/警告三要素，备用源降级必须明示
- **产出**：对话中直接呈现中文数据，不产出文件
- **注意**：skill 内置 10 条数据陷阱必读——TOTAL_SHARE 是静态当前值禁当历史股本用、无复权价格序列、financials 年报缺失会自动混入季报（同比前核对报告期）、52 周高低限流显示 "-" 不要转述成 0、北交所老号段已迁 920 需先 search 等；任何失败宁标"数据不足"不用推测填充

### /wechat-article — 公众号文章三 Agent 协作

```
/wechat-article Qwen3技术报告解读
/wechat-article 为什么巴菲特不买科技股
```

- **参数**：主题描述（技术论文解读 / 技术主题 / 商业投资主题）
- **流程**：四阶段——①确认文章定位（目标读者/深度/长度/风格，未指定则询问），2-3 个研究 Agent 并行收集素材 → ②作者 Agent 写 3000-4000 字初稿 → ③编辑 Agent（标题/结构/节奏）+ 读者 Agent（目标读者视角）并行审阅 → ④综合修改定稿（两者矛盾时偏向读者），论文类完成 PDF 配图提取
- **产出**：技术主题 `reports/AI产业研究/公众号-{主题}-{YYYYMMDD}.md`；投资主题 `reports/{公司名}/{公司名}-公众号-{YYYYMMDD}.md`；配图存 `assets/{主题简称}/`
- **注意**：不虚构数据（无来源标"估计"）；禁 AI 腔套话；公式 LaTeX 格式且每个配大白话翻译；配图必须实际插入（论文类从 PDF 提取 ≥500KB 高清图，禁止 [图X] 占位符）；段落不超 4 行、不用 emoji
- **依赖**：论文解读类需 `pdftoppm` 与 PIL 做配图提取

---

## 典型工作流串联

```
发现候选                          买入决策                      买入后纪律
─────────                        ─────────                    ─────────
/industry-research（看格局）      /investment-checklist        /thesis-tracker（建论文）
/industry-funnel（漏斗选股）  →   （六关把门）              →       ↓
/bottleneck-hunter（找瓶颈）      /investment-research         每季 /earnings-review
/quality-screen（去劣快筛）       或 /investment-team           异动 /news-pulse
                                 （深度研究）                  定期 /thesis-drift
                                                              组合 /portfolio-review
```

- **快速判断一家公司值不值得研究**：/quality-screen → /investment-checklist（两步都是排除逻辑，通过 ≠ 买入）
- **深研一家公司**：/investment-research（单线细致）或 /investment-team（并行快速）；管理层拿不准加 /management-deep-dive；未上市公司改用 /private-company-research
- **财报季**：日常公司 /earnings-review，重仓关键财报 /earnings-team
- **对外发布**：公司系列长文 /deep-company-series，单篇公众号 /wechat-article
- **随时**：/dyp-ask 当思维陪练；/ashare-data、/financial-data 是所有环节的取数底座

---

*本项目用于学习研究，不构成投资建议。*
