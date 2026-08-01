# Full Analysis 成本预算与可观测性（Task 6）

> 目标：在不降低 Audit、Gate、语义评审和证据链约束的前提下，让全量分析的真实成本
> **可计量、可对比、可告警**。成本是优化项，不是质量项——质量门槛由 audit/review 独立把关，
> 成本门槛**只告警、不静默关闭任何质量校验**。

## 计量来源（Task 1）

- `evidence/usage.jsonl`：每条记录 = 一个 work/summary/review Agent 阶段的真实用量
  （`input_tokens`/`output_tokens`/`input_bytes`/`output_bytes`/`duration_ms`/`cache_hit`）。
- `manifest.usage_summary`：由 usage.jsonl 全量重算的只读汇总（`total_tokens`/`total_records`/
  `by_phase`/`by_skill`），是 benchmark 与告警的唯一真源。
- 协议：Agent 完成后必须 `record-usage`；provider 不返回 token 时只提交字节数
  （token 记 `null`），禁止伪造 0；字节数缺失整条被拒；`attempt_id + phase` 唯一。

## 阈值（初始建议，可调）

| 指标 | 阈值 | 告警码 | 说明 |
|------|------|--------|------|
| usage_summary | run 必须有 | `missing_usage_summary` | 旧流程 run 或 usage 未回传 |
| 同一 skill 完整 attempt | ≤ 1 次 | `excessive_attempts` | 确定性证据错误应走 `submit-correction`（不耗 attempt）；报告问题走 `rework` 后重复完整提交视为超限 |
| 单份 review brief | < 500 KB | `oversized_review_brief` | compact 模式（v2）异常信号，检查 `--payload-mode` |
| compact vs full | compact < full | （Task 3 测试守护） | `tests/test_full_analysis_review.py` 断言字节对比 |

## 告警位置

- `finalize` APPROVED 后输出 `cost_budget` 字段：
  - `verdict: COST_BUDGET_OK` / `COST_BUDGET_EXCEEDED`
  - `exceeded: [{code, detail}]` 逐项列出超限原因
  - 同时写 `cost_budget_exceeded` 事件到 `evidence/events.jsonl`
- **不阻断 APPROVED**：成本告警不改变 exit code 与准出语义；质量仍由 audit/语义评审把关。

## 运行基准（benchmark）

```bash
python3 scripts/full_analysis.py benchmark --run-roots <run1> <run2> [--output-dir <dir>]
```

新增 `metrics.usage` 输出：`per_run`（total_tokens/total_records/by_phase/by_skill）+
`cache_hit_rate`（缓存命中率，Task 5 生效后随 `cache_hit` 回传累计）。

## 缓存复用（Task 5）

- finalize APPROVED 后自动把每个 PASS 单元写入 `<公司目录>/.full-analysis-cache/<key>/`
  （bundle.json + artifact 副本，带 sha256）。
- 缓存键 = `sha256(company_id + as_of + skill_id + methodology_sha256
  + input_evidence_digest + capability_profile)`——方法论、上游事实、能力变化都会使 key 变化而失效。
- 查询：`python3 scripts/full_analysis.py cache-lookup --run-root <run> --skill-id <skill>`
  （只读；篡改缓存 artifact 会使 digest 校验失败返回 MISS）。
- 缓存只保存受 Gate 接受的 bundle/artifact，不保存 prompt、凭据与外部响应。

## 隐私边界

缓存目录位于 `local/` 之下（`local/Company/<公司>/.full-analysis-cache/`），
随公司目录受 `.gitignore` 保护，不会进入公开仓库。
