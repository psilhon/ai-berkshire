#!/usr/bin/env python3
"""run_layout.py — run 目录布局的唯一真源（2026-08-30 架构评审候选②）。

此前 run 目录的知识散布三个模块、五处字面量：
  - gate.py:375 / 1058  `str(rel).startswith("evidence/attempts/")`（artifact 准入）
  - gate.py:1182        `rel.as_posix().startswith("evidence/attempts/summary/")`（总结路径）
  - mk_result_bundle.py:358  `rel.startswith("evidence/attempts/")`（生成器侧）
  - scripts/full_analysis.py:175  手工拼 `evidence/attempts/<skill_id>/...`（cleanup）
加上"三个状态文件各归谁写"：
  - gate 写 MANIFEST / EVENTS，runtime 写 RUNTIME_STATE（各自定义一次）
改一次目录结构要追 3 个模块 5 处调用点，locality 极差——典型的知识散布。

本模块把**布局**（路径常量 + 判定谓词）收成一条缝：调用方只学常量与两个谓词。
注意边界：本模块只管"东西放在哪"，**不管"怎么读写"**——状态文件 I/O 归
run_store.py，写入时机与校验策略归 gate（manifest / events）与
runtime（runtime-state / usage）。

三层分工：run_layout（放在哪）→ run_store（怎么读写）→ gate/runtime（何时写）。

与 substance.py / full_analysis_contract.py 同模式：小 interface、确定性、无副作用。
"""

from __future__ import annotations

from pathlib import Path

# ---- 产出物目录 ----
# ---- 产出物目录 ----
# evidence 根目录：账本种子文件（facts/sources/calculations/artifacts.json）等
# 散文件的父目录；此前 gate:788 直拼 "evidence"/name，是全仓最后一处字面量。
EVIDENCE_REL = Path("evidence")
# attempt 目录：每个业务单元一次的尝试产物（report.md / result.json …）
ATTEMPTS_REL = EVIDENCE_REL / "attempts"
# 总结专属子目录：deep-summary 的熔炼产物必须先落在这里才被 Gate 接受
SUMMARY_ATTEMPTS_REL = ATTEMPTS_REL / "summary"

# ---- 运行时状态四件套（路径在此单点定义，I/O 归 run_store，时机归 gate / runtime）----
MANIFEST_REL = Path("evidence/00-analysis-manifest.json")   # gate 写（经 ingest-result 等）
RUNTIME_STATE_REL = Path("evidence/runtime-state.json")     # runtime 写（work unit 状态机）
EVENTS_REL = Path("evidence/events.jsonl")                  # 双方追加
USAGE_REL = Path("evidence/usage.jsonl")                    # runtime 写（token/预算台账）
# 运行时状态跨进程互斥锁（runtime_lock 持有；路径归属布局层，I/O 归 run_store）
LOCK_REL = Path("evidence/locks/runtime-state.lock")


def _as_posix(rel: str | Path) -> str:
    return rel.as_posix() if isinstance(rel, Path) else str(rel)


def in_attempts(rel: str | Path) -> bool:
    """rel 是否位于 attempt 目录下（artifact 准入的硬约束）。"""
    return _as_posix(rel).startswith(ATTEMPTS_REL.as_posix() + "/")


def in_summary_attempts(rel: str | Path) -> bool:
    """rel 是否位于总结专属子目录下。"""
    return _as_posix(rel).startswith(SUMMARY_ATTEMPTS_REL.as_posix() + "/")
