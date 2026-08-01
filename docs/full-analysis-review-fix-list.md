# 评审 Finding 源头修复清单（模板）

> 本文件是 `review fix-list` 命令的输出模板/示例（E13，Task 9）。
> 每次 APPROVED run 后执行：
>
> ```bash
> python3 scripts/full_analysis.py review fix-list \
>   --run-root <run_root> [--severity high|medium|low] [--out <path>]
> ```
>
> 输出按 kind 分组：`pipeline_raw`（数据管线/脚本源头）→ `role_memo`（子 Agent 备忘录源头）
> → `report`（正式报告正文）→ `methodology`（方法论/契约源头）→ `UNFIXED`（未定位源头）。
>
> 使用纪律：
> - **low/medium 传播型笔误不进返工链**（不阻断 REVIEW_PASSED），由本清单在季度复核或下次 run 前批量修源头。
> - **high finding 必须当场走返工链**（修源文件 → rework/correction → audit → prepare → 重审 → ingest），不等待清单。
> - 修源头 = 修子 Agent 备忘录/管线脚本/报告正文中**首次产生错误**的位置，而非评审结果本身。

---

# 评审 Finding 源头修复清单

生成时间: <YYYY-MM-DD> ｜ 共 N 条 finding（high/medium/low = h/m/l）

## 数据管线/脚本源头（N 条）

- [severity] (skill_id/dimension) 描述
  - 源头: `evidence/attempts/<skill>/<attempt>/raw/<file>` ｜ note

## 子 Agent 备忘录源头（N 条）

- [severity] (skill_id/dimension) 描述
  - 源头: `evidence/attempts/<skill>/<attempt>/role-<role>.md` ｜ note

## 正式报告正文（N 条）

- [severity] (skill_id/dimension) 描述
  - 源头: `<stage>/<skill>.md` ｜ note

## 方法论/契约源头（N 条）

- [severity] (skill_id/dimension) 描述
  - 源头: `skills/<skill>.md` ｜ note

## 未定位源头（季度复核补定位）（N 条）

- [severity] (skill_id/dimension) 描述
  - 源头: `-` ｜ （评审时未填 fix_source，复核时补定位）
