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
