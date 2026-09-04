# ADR-0001：ashare_data 私有函数测试是合法内部缝，akshare_data pe-band 不构成重复

**状态**：Accepted（2026-08-10，/improve-codebase-architecture 评审 ④ 的裁决）

## 背景

2026-08-10 架构评审提出候选④：「ashare_data 是深模块但缝被测试捅穿（~30 处直捅私有函数）；akshare_data 的 pe-band 与 ashare_data.cmd_pe_band 同源重复，应归并」。执行前 trust-but-verify 调查推翻了两个前提：

1. **9 个被测试直引用的私有函数全部是 codebase-design 承认的合法 internal seams**：
   - 纯函数类（`_em_secu_code`/`_qq_code`/`_em_secid`/`_positive_years`/`_fmt_zt_time`/`_anomaly_market`/`_cls_sign`）：确定性输入输出，直测是唯一精确断言方式；
   - 网络原语类（`_fetch_datacenter_rows`/`_em_hot_rank`）：测试在 transport 层 mock（`_curl_json`/`_curl_json_post`），不碰真网络——这正是测 I/O 函数的正统做法。若改走 90 个公开 cmd_* 命令，要么重复 mock 传输层、要么真发网络请求，两者都更差。
2. **两个 pe-band 不是重复**：
   - `akshare_data.cmd_pe_band` = 腾讯源**价格分布**分位（零 token，自述 PE 需外部 EPS）；
   - `ashare_data.cmd_pe_band` = Tushare daily_basic **真 PE/PB** 历史分位（需 token，Tier 0）。
   - 数据源不同、指标口径不同。且 akshare_data 提供 ashare_data 零依赖层缺失的**零 token 前复权 OHLC**（cmd_kline 需 TUSHARE_TOKEN），是陷阱 #3 的零 token 补充路径。

## 决策

- **不迁移** test_ashare_data.py 的私有函数引用，**不删除** akshare_data.py，**不合并**两个 pe-band。
- 未来评审如再次提出上述重构，先读本 ADR；除非前提事实改变（如 ashare_data 提供了零 token OHLC 命令、或测试开始真发网络请求），不再重复建议。
- 同步修正过时文档：skills/ashare-data.md 陷阱 #3 由「无复权价格序列」更新为现状（cmd_kline 已补缺口但需 token）。

## 后果

- 正面：避免一轮无收益的大迁移（1584 行测试），保留 transport 层 mock 的测试精度；akshare_data 作为零 token 备用路径继续可用。
- 负面：私有函数的重构仍受 30 处测试约束——这是 internal seam 的固有代价（测试穿过实现内部），接受它。

---

## 2026-09-04 修订：akshare_data 前提失效，已移除

**触发**：ponytail-audit 全仓过度设计审计（2026-09-04）。

**新证据**（原裁决时未掌握）：

1. **依赖不可用**：`akshare_data.py` 依赖 `akshare` 与 `requests`，二者在本机**均未安装**（实测 `ModuleNotFoundError`）；它是全仓 `tools/` + `scripts/` 唯一的第三方 import，与仓库实际「零依赖」状态矛盾。
2. **从未被使用**：全仓检索 `akshare_data`（含 `local/` 全部历史运行记录）零命中；零测试。
3. **后果**：本 ADR「后果·正面」断言的「akshare_data 作为零 token 备用路径**继续可用**」为假。更糟的是 `skills/ashare-data.md` 仍推荐 agent 走这条路径——照文档执行必然失败。

**修订决策**：

- **撤销**本 ADR 第 19 行的「不删除 akshare_data.py」，**移除** `tools/akshare_data.py`（−243L，v3.10.13）。
- 同步修正 `skills/ashare-data.md` 陷阱 #3：不再推荐仓库内脚本，无 token 场景改为「自行取数（如 pip 装 akshare）」。
- 本 ADR 第 1 条（私有函数是合法 internal seam）**继续有效**；第 2 条（两个 pe-band 不构成重复）随脚本一并移除，无遗留分歧。
- 仓库恢复为 **stdlib 零依赖**。

**恢复条件**：若未来确需零 token 前复权 OHLC 且已验证依赖可用，可从 git 历史（`v3.10.12` 及更早）取回该文件，并按本 ADR 原前提重新评估。

---

## 2026-09-04 修订②：私有函数去重边界（候选⑧ source-level adapter 收敛）

**触发**：2026-08-30 架构评审候选⑧——ashare_data 的 `_em_secu_code` / `_qq_code` / `_em_secid` / `_fetch_datacenter_rows` 与 `ashare_plugin` 的 `CodeIdentity` / `fundamentals._fetch_rows` 逐字同构，构成第二实现。

**与本文第 1 条的关系**：第 1 条裁决的是「测试直捅私有函数是合法 internal seam」——**继续有效**。候选⑧消除的不是测试缝，而是**生产代码里的第二实现**（同一映射逻辑写两遍，单边改动即分叉）。两者正交：去重后私有函数名字、签名、行为、测试全部保留，只是函数体改为委托单一真源。

**修订决策**：

- `_em_secu_code` / `_em_secid` → 委托 `CodeIdentity.secu_code` / `.secid`（`InvalidCodeError` 继承 `ValueError`，异常语义不变）。
- `_qq_code` → 有效代码委托 `CodeIdentity.quote_code`；**保留无效代码的旧前缀映射垫片**——`cmd_quote("INVALID") → False` 的宽限降级路径被测试锁定，严格化属单独立项。
- `_fetch_datacenter_rows` → 委托 `ashare_plugin.fundamentals.fetch_datacenter_rows`（原 `_fetch_rows` 转公开别名）；`_curl_json` 经 TransportClient 适配注入，`TransportError` 转回 `ConnectionError` 保持调用方语义。
- **分页方言合并与 cmd_quote 改用 fetch_quote 单独立项**，本修订不含。

**后果**：测试零改动（142 个 ashare 用例全绿）；代码→插件单一真源；遗留一个显式声明的 `_qq_code` 兼容垫片（无效代码路径），其移除条件 = cmd_quote 对无效代码改抛 ValueError 并更新对应测试。

**2026-09-04 v3.10.15 追记**：垫片移除条件已满足——`_qq_code` 改为纯委托 `CodeIdentity.quote_code`，无效代码抛 ValueError（main 转 exit 2 参数错误），对应测试更新（`test_invalid_code_raises_value_error` / `test_main_exits_two_on_invalid_code`）。本修订无遗留垫片。
