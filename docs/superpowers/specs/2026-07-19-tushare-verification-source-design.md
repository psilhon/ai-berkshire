# Tushare 可选交叉验证数据源设计 v1.0

> 日期：2026-07-19  
> 状态：设计已确认，待实施计划  
> 适用范围：`ashare-data`、`financial-data` 及其上层 `full-company-analysis`

## 1. 背景

当前 A 股数据管线以腾讯、东方财富和巨潮为主，并通过统一结果契约暴露来源、时间、降级和错误。该组合可以完成主要研究流程，但部分核心财务、估值和市场数据仍需要一个独立获取链做交叉验证。

本设计新增 Tushare HTTP API 作为**可选、独立的验证源**。Tushare 不替换现有主源和备用源，不改变八个现有 CLI 命令的参数及主要输出，也不因未配置、积分不足或接口暂时不可用而阻断已有主数据流程。

## 2. 目标

1. 在本地配置 `TUSHARE_TOKEN` 后，为现有 A 股数据结果追加结构化的 Tushare 验证信息。
2. 在未配置 token 时不发起任何 Tushare 请求，并明确标记 `NOT_CONFIGURED`。
3. 对可比较字段给出 `MATCH`、`CONFLICT` 或 `INSUFFICIENT`，同时保留两侧原值、来源、期间、单位、数据时间和偏差。
4. 复用同一套工具和规范，使 Claude Code 与 Codex 得到一致的数据行为、错误语义和报告要求。
5. 保证 token 不进入命令行参数、日志、报告、异常、测试快照、代码或版本库。
6. 让 `full-company-analysis` 自动继承验证能力，不增加人工步骤或新的业务 Skill。

## 3. 非目标

- 不把 Tushare 设为实时行情主源；腾讯仍负责实时行情，Tushare 仅提供日终或最近交易日核验。
- 不把 Tushare 设为现有数据源的自动备用源，也不改变现有 fallback 顺序。
- 不要求用户购买积分或开通全部接口；权限不足必须作为可解释状态返回。
- 不引入官方 `tushare` Python SDK、`pandas` 或其他第三方运行时依赖。
- 不把所有 Tushare 接口封装成通用 SDK。
- 不自动读取 `.env`，不打印、持久化或上传 token。
- 不改写既有研究报告，不自动执行 commit、push、PR、发布或外部消息发送。

## 4. 方案选择

### 4.1 采用方案：可选独立验证层

Tushare 位于主数据获取之后，只消费标准化主结果并返回独立的 `verification` 块。优点是对现有行为侵入最小，Tushare 故障不会改变主命令成败，且可以清楚区分“获取失败”和“交叉验证冲突”。代价是同一字段会同时保留主值与验证值，结果体略有增加。

### 4.2 未采用方案

- **作为统一备用源**：可在现有源失败时补数据，但会把权限、积分和日终时效差异引入主流程，容易让同一命令在不同用户环境下产生不同成功语义。
- **作为新独立 Skill**：隔离最强，但会新增人工调用、状态协调和全量分析契约，无法满足“配置后自动核验”的目标。

## 5. 总体架构

```text
现有 CLI / 上层 Skill
        |
        v
现有腾讯 / 东方财富 / 巨潮主数据流程
        |
        +--------------------> DataResult 主结果
        |
        v
TushareVerificationService
        |
        +--> TUSHARE_TOKEN 缺失：NOT_CONFIGURED，不联网
        |
        +--> TushareClient（HTTP JSON，token 仅经 stdin）
                    |
                    v
             期间/单位/修订版归一化
                    |
                    v
        MATCH / CONFLICT / INSUFFICIENT
                    |
                    v
          DataResult.verification
```

### 5.1 组件职责

1. `TransportClient.post_json`
   - 支持通过标准输入向 `curl --data-binary @-` 传入 JSON 请求体。
   - 请求体不出现在进程参数中；错误消息不得回显请求体。
   - 保留当前超时、HTTPS 校验、JSON 解析和错误分类语义。

2. `TushareClient`
   - 位于 `tools/ashare_plugin/tushare.py`。
   - 仅调用 `https://api.tushare.pro`，请求结构为 `api_name`、`token`、`params`、`fields`。
   - 将 Tushare 的 `fields` 与 `items` 组合为字典行，拒绝字段数不一致的数据。
   - 把接口错误区分为未配置、无权限、限流、空数据、上游错误和 schema 错误。

3. `TushareVerificationService`
   - 接收现有标准化主结果，不绕过现有插件直接生成主结果。
   - 选择相应 Tushare API，严格筛选证券、日期、报告期和报告版本。
   - 统一金额、比率、百分比及每股字段的单位，再执行 Decimal 比较。
   - 输出字段级结果和整体状态；不覆盖主值。

4. CLI 与 Skill 适配层
   - 八个现有命令保持命令名、参数和主退出码不变。
   - 配置 token 后自动追加验证块；未配置时只追加或记录 `NOT_CONFIGURED`，不发请求。
   - `full-company-analysis` 通过 `ashare-data` 与 `financial-data` 继承该能力，无需新增 Skill 或人工确认。

## 6. 数据源与 API 映射

| 现有命令/能力 | 现有主流程 | Tushare 验证接口 | 验证范围 | 权限处理 |
|---|---|---|---|---|
| `quote` | 腾讯实时行情 | `daily_basic` | 最近交易日收盘价、总/流通市值；不得与盘中价直接判冲突 | 可用则核验，否则 `INSUFFICIENT` |
| `valuation` | 腾讯/东方财富现有口径 | `daily_basic` | PE、PB、换手率、总/流通市值 | 可用则核验，否则 `INSUFFICIENT` |
| `financials` | 东方财富 | `income`、`balancesheet`、`cashflow`、`fina_indicator` | 收入、利润、资产负债、现金流、ROE 等可映射字段 | 分接口记录权限；允许部分核验 |
| `history` | 东方财富年度财务历史 | `income`、`balancesheet`、`cashflow`、`fina_indicator` | 相同报告期的收入、利润、现金流、ROE、毛利率和净利率 | 分接口记录权限；不匹配报告期不得比较 |
| `equity-history` | 东方财富股本历史 | `daily_basic`、`share_float` | 同日股本/流通市值及解禁事件辅助核验 | 高权限接口缺失不影响基础核验 |
| `search` | 现有代码/名称搜索 | `stock_basic` | 代码、名称、市场、上市状态 | 无权限或空数据时不改变主搜索结果 |
| `signals` | 东方财富等市场信号 | `moneyflow`、`top_list`、融资融券、`share_float` | 资金流、龙虎榜、融资融券、解禁的同日同口径核验 | 每类独立状态，禁止以一类失败代表全部失败 |
| `announcements` | 巨潮及现有备用源 | `anns_d` | 公告标题、日期、证券与链接元数据 | 仅在用户权限允许时启用；失败显式记录 |

映射表是能力边界，不代表所有账户都具备相应权限。实现不得为了凑齐验证字段而调用未列入本设计的接口。

## 7. 统一验证契约

Tushare 信息作为现有 `DataResult` 的可选扩展，不改变已有顶层字段：

```json
{
  "ok": true,
  "data": {},
  "source": "eastmoney",
  "fallback_used": false,
  "as_of": "2026-07-18",
  "warnings": [],
  "verification": {
    "provider": "tushare",
    "status": "MATCH",
    "configured": true,
    "as_of": "2026-07-18",
    "warnings": [],
    "endpoints": [
      {
        "api_name": "daily_basic",
        "ok": true,
        "source": "tushare.daily_basic",
        "error_type": null,
        "row_count": 1
      }
    ],
    "fields": [
      {
        "field": "pb",
        "status": "MATCH",
        "primary_value": "1.23",
        "verification_value": "1.24",
        "primary_source": "eastmoney",
        "verification_source": "tushare.daily_basic",
        "period": "2026-07-18",
        "unit": "multiple",
        "deviation_pct": "0.81300813"
      }
    ]
  }
}
```

### 7.1 验证状态

- `MATCH`：证券、期间、单位和报告版本可比，且偏差在允许范围内。
- `CONFLICT`：字段可比但偏差超出允许范围，或两个来源对同一离散事实给出不同值。
- `INSUFFICIENT`：缺值、期间不一致、单位无法确定、接口权限不足、空数据或不存在可信映射，不能作正反判断。
- `NOT_CONFIGURED`：本地不存在 `TUSHARE_TOKEN`，且已确认没有发起 Tushare 请求。

整体状态由字段状态确定：存在 `CONFLICT` 时整体为 `CONFLICT`；否则存在至少一个 `MATCH` 时为 `MATCH`；只有不足项时为 `INSUFFICIENT`；未配置时为 `NOT_CONFIGURED`。部分接口失败必须保留各自状态和警告，不得把成功字段一起降为失败。

### 7.2 错误类型

Tushare 验证层至少区分：

- `not_configured`
- `permission_denied`
- `rate_limited`
- `empty_data`
- `upstream_error`
- `schema_error`
- `period_mismatch`
- `unit_mismatch`

这些错误只影响验证块。现有主流程成功时，Tushare 错误不得改变主 `ok` 或 CLI 主退出码；现有主流程失败时，也不得用 Tushare 验证结果把主流程伪装成成功。

## 8. 比较与归一化规则

### 8.1 标的与期间

- 六位 A 股代码必须转换为 Tushare 的交易所后缀代码，并核对市场。
- 日频字段只比较同一交易日。实时行情只能与 Tushare 同日已收盘数据比较；盘中或 Tushare 尚未更新时，只提供“最近收盘参考”，状态为 `INSUFFICIENT`，不得判定价格冲突。
- 财务字段必须匹配相同报告期、合并/母公司口径以及报告类型。
- 同一报告期存在多个公告版本时，优先使用最新可识别修订版；无法证明两侧修订版一致时为 `INSUFFICIENT`。
- 禁止从结果集中任取第一行作为目标记录。

### 8.2 单位

- 金额统一到明确的人民币单位后再比较，保留原单位和标准化单位。
- 比率统一使用小数或倍数语义；百分比字段明确区分“百分数”和“百分点”。
- 股本、市值、每股指标及价格不得仅凭字段名猜测换算比例。
- 单位缺失或映射不唯一时返回 `unit_mismatch`，不得计算偏差。

### 8.3 数值与偏差

- 使用 `Decimal`，禁止用二进制浮点执行验收比较。
- 默认相对偏差阈值为 `1%`；分母为零时使用字段专用绝对偏差规则，若该字段没有已定义规则则为 `INSUFFICIENT`。
- 偏差计算及舍入规则必须集中定义并由测试固定；报告展示可以舍入，判定必须使用未展示的标准化 Decimal 值。
- 离散字段使用精确枚举映射，不套用数值阈值。

### 8.4 冲突处置

出现 `CONFLICT` 时同时保留主值和 Tushare 值，并要求上层报告：

1. 不使用冲突字段形成高置信度结论；
2. 引入第三独立来源或人工核对原始披露；
3. 在未仲裁前标记数据限制及其对估值/判断的影响。

## 9. 凭据与安全设计

1. token 只从运行进程的 `TUSHARE_TOKEN` 环境变量取得；工具不读取 `.env`。
2. 用户设置 token 时应在自己的本地终端完成，不在聊天中粘贴。
3. Python 将含 token 的 JSON 写入 `curl` 标准输入，`curl` 使用 `--data-binary @-`；token 不进入 argv。
4. 不打印请求体，不把请求体拼入异常，不记录 token 长度、前后缀或哈希。
5. 测试使用固定哨兵字符串，并断言该字符串不存在于 argv、stdout、stderr、异常、日志和快照。
6. 报告只记录 `configured: true/false`、接口名、状态与非敏感错误分类。
7. 任何调试开关都不得放宽上述边界。

## 10. CLI 与上层工作流行为

### 10.1 八个 CLI 命令

- 命令名、位置参数、可选参数和主退出码保持兼容。
- 未配置 token：不联网，主结果照常输出，验证状态为 `NOT_CONFIGURED`。
- 已配置且验证成功：在主结果之后追加结构化验证摘要。
- 部分接口无权限、限流或空数据：主结果照常输出，验证块逐项标出失败；不得用空列表表示成功。
- `signals` 和 `announcements` 允许分项成功，必须明确成功数、失败数与各自原因。
- 本次不新增 `--json` 参数；CLI 的人类可读摘要与插件内部结构化结果必须来自同一判定对象，不维护第二套状态逻辑。

### 10.2 `full-company-analysis`

- 不新增 Tushare 专用 Skill 或注册表业务项。
- 数据阶段调用现有 `ashare-data` / `financial-data` 时自动得到验证块。
- gate 仅在验证块实际存在时审计其结构；未配置 token 是合法且可见的限制，不得伪称双源验证完成。
- 出现 `CONFLICT` 时，相关关键事实不得进入“已验证”状态，直到第三源或人工原始披露完成仲裁。

## 11. Claude Code 与 Codex 一致性

- 规范源仍为 `skills/ashare-data.md` 和 `skills/financial-data.md`。
- 实施后通过 `scripts/sync-codex-skills.py` 生成对应 Codex Skill，并通过 `scripts/sync-codex-prompts.py` 同步对应兼容提示词。
- 两个平台调用同一 `tools/ashare_data.py` 与 `tools/ashare_plugin/`，不得复制平台专用验证逻辑。
- 同步检查必须证明生成物无漂移；本地安装只更新本次涉及的 Skill/命令。
- 默认产物继续本地化保存，不要求 Git，也不自动改写无关 `local/reports/` 内容。

## 12. 预计实施边界

实施阶段预期仅涉及以下范围，最终以实施计划中的测试驱动步骤为准：

- `tools/ashare_plugin/transport.py`
- `tools/ashare_plugin/tushare.py`（新增）
- `tools/ashare_plugin/tushare_verification.py`（新增）
- `tools/ashare_data.py`
- `tests/` 中对应 transport、Tushare、CLI 与全量 gate 测试
- `skills/ashare-data.md`
- `skills/financial-data.md`
- 必要的 `full-company-analysis` 契约说明或 gate 结构校验
- 同步生成的 `codex-skills/` 与必要的 `codex-prompts/`

不在本次范围内的现有脏工作树改动必须保留，不得回滚或顺手重构。

## 13. 测试策略

### 13.1 单元测试

- transport 确认敏感 JSON 经 stdin 传输。
- token 哨兵字符串在 argv、输出、错误和日志中均不可观察。
- `fields` + `items` 正确映射，列数不一致返回 `schema_error`。
- Tushare 响应区分权限、限流、空数据、上游错误和 schema 错误。
- 代码、日期、报告期、修订版和单位归一化覆盖正反用例。
- `MATCH`、`CONFLICT`、`INSUFFICIENT`、`NOT_CONFIGURED` 聚合规则均有测试。
- Decimal 阈值边界、零分母及百分比/百分点用例得到固定结果。

### 13.2 CLI 与契约回归

- 八个现有命令的参数和主成功/失败退出语义不回归。
- 无 token 时使用请求探针证明 Tushare 调用次数为零。
- 验证层各类失败不改变成功主流程的退出码。
- `signals`、`announcements` 的部分失败计数和警告准确。
- CLI 摘要与插件内部结构化结果对同一输入得出相同状态。
- `full-company-analysis` 对未配置、匹配、冲突和不足状态分别执行正确 gate 行为。

### 13.3 仓库级验证

- `python3 scripts/sync-codex-skills.py --check`
- `python3 scripts/sync-codex-prompts.py --check`
- `bash scripts/check.sh`
- 测试默认不访问真实 Tushare 网络。

## 14. 二元验收标准

1. 未配置 `TUSHARE_TOKEN` 时，Tushare 网络调用严格为零。
2. token 在 argv、stdout、stderr、异常、日志、报告和测试快照中均不存在。
3. 权限不足、限流、空数据和 schema 错误具有互不混淆的机器状态。
4. `MATCH`、`CONFLICT` 和 `INSUFFICIENT` 均有字段级与整体级测试。
5. 期间、单位、报告版本不一致时绝不产生伪 `MATCH`。
6. 八个 CLI 命令的参数与主退出码语义不回归。
7. Tushare 失败不会把成功主结果变为失败，也不会把失败主结果变为成功。
8. Claude Code 与 Codex 的规范源、生成物和底层工具保持一致。
9. 统一本地检查通过，且不修改无关报告或用户现有改动。
10. 用户在本地配置 token 后，完成一次真实 A 股标的验证；只报告接口可用性、权限和字段覆盖，不暴露凭据。

## 15. 真实验证流程

实现及离线测试全部通过后，由用户在本地终端设置 `TUSHARE_TOKEN`。验证过程：

1. 只确认 `TUSHARE_TOKEN: present`，不读取或展示值。
2. 选择一个已明确代码的 A 股标的，依次运行八个命令的有限真实调用。
3. 记录每个接口的可用、无权限、限流、空数据或 schema 状态，以及可比较字段数量。
4. 检查实时与日终边界、财报期间、单位和修订版是否正确处理。
5. 将验证记录写入本地公司目录；不覆盖既有研究报告，不提交 Git。
6. 若权限不足，只说明受影响接口和验证缺口，不要求用户在聊天中提供 token，也不把缺权限误报为代码故障。

## 16. 风险与约束

- Tushare 的积分及接口权限会因账户而异，因此“代码可用”和“当前账户可调用”必须分别报告。
- Tushare 是日终/结构化验证源，不能证明盘中腾讯价格错误；时间不可比时必须保持 `INSUFFICIENT`。
- 上游字段或响应 schema 可能变化；解析器必须 fail-closed 为 `schema_error`，不得猜测错位字段。
- 财务来源可能使用不同修订版或主体口径；仅同名字段不足以构成可比性。
- Tushare 与现有来源并非原始监管披露的替代品；关键冲突仍需第三源或原始公告仲裁。

## 17. 实施门槛

本文件经用户审阅确认后，下一步只编制逐步实施计划。实施必须采用测试先行：先用失败测试冻结凭据不可观察、验证 schema、期间/单位规则和 CLI 不回归，再修改生产代码。未完成上述计划与测试设计前，不开始实现。

## 18. 官方接口依据

- Tushare HTTP API 请求与响应契约：<https://tushare.pro/document/1?doc_id=130>
- Tushare 接口权限说明：<https://tushare.pro/document/1?doc_id=108>
- `stock_basic`：<https://tushare.pro/document/2?doc_id=25>
- `daily_basic`：<https://tushare.pro/document/2?doc_id=32>
- `income`：<https://tushare.pro/document/2?doc_id=33>
- `balancesheet`：<https://tushare.pro/document/2?doc_id=36>
- `cashflow`：<https://tushare.pro/document/2?doc_id=44>
- `fina_indicator`：<https://tushare.pro/document/2?doc_id=79>
- `anns_d`：<https://tushare.pro/document/2?doc_id=176>
