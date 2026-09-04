#!/usr/bin/env python3
"""bundle_checks.py — bundle 准入判定的单一真源（占位水印 + NA 证明规则）。

此前同一判定逻辑在 gate（拒收侧）与 mk_result_bundle（生成侧）各写一份：
  - PLACEHOLDER 水印扫描：gate._precheck_placeholder_evidence（五类账本逐类消息）
    vs mk.placeholder_offenders（五类短描述）——字段口径一旦单边增删，
    就回到「生成器放行、Gate 拒收」或反向误杀的旧病；
  - NA 证明规则（判定事实能否证伪谓词 / 来源已登记 / limitations 非空）：
    gate._validate_not_applicable vs mk.build_not_applicable 各自内联，
    `min_independent_contexts_2` 特例写两遍。

本模块只做**纯判定**（无 I/O、无异常、消息中立），两侧各自包装呈现：
  - gate（拒收侧）→ GateError / errors 列表（逐类建议消息）
  - mk（生成侧）→ fail(exit 2) / BLOCK 警告（含期望描述）

与 substance.py / run_layout.py 同模式：小 interface、确定性、无副作用。
"""

from __future__ import annotations

from substance import NA_PREDICATE_FIELDS

# 结构地板水印：确定性字符串，只可能来自生成器地板或 Agent 手写占位。
PLACEHOLDER = "PLACEHOLDER"

# 五类证据账本的扫描口径：(bundle 键, 取值字段拼接, 类别标签)。
# 新增账本类别时在此加一行，gate 与 mk 同时生效——这就是本模块存在的意义。
_PLACEHOLDER_SCANS: list[tuple[str, tuple[str, ...], str]] = [
    ("fact_updates", ("value",), "fact"),
    ("source_records", ("publisher", "title"), "source"),
    ("calculation_requests", ("calculation_id",), "calculation"),
    ("judgments", ("judgment_id", "conclusion"), "judgment"),
    ("command_receipts", ("receipt_id", "reason", "detail"), "receipt"),
]


def placeholder_offenders(bundle: dict) -> list[tuple[str, str]]:
    """扫描 bundle 五类证据账本，返回 (类别标签, 条目 id) 列表（空=零占位）。

    水印是确定性字符串（PLACEHOLDER 前缀），误报为零：见 gate
    `_precheck_placeholder_evidence` 的背景说明（v3.4.10 / v3.4.13）。
    """
    offenders: list[tuple[str, str]] = []
    for key, fields, kind in _PLACEHOLDER_SCANS:
        for entry in bundle.get(key) or []:
            blob = "".join(str(entry.get(f, "") or "") for f in fields)
            if PLACEHOLDER in blob:
                offenders.append((kind, str(entry.get(f"{kind}_id", "") or "")))
    return offenders


def na_predicate_violation(predicate: str, fact: dict) -> str | None:
    """判定事实能否证伪适用性谓词。返回 None=能证明为假；否则返回期望描述。

    规则（与契约 applicability 谓词一一对应）：
    - `min_independent_contexts_2`：field=independent_context_count 且 value 为 <2 的整数；
    - 其余谓词：field=NA_PREDICATE_FIELDS[predicate] 且 value is False。
    """
    if predicate == "min_independent_contexts_2":
        ok = (fact.get("field") == "independent_context_count"
              and isinstance(fact.get("value"), int)
              and not isinstance(fact.get("value"), bool)
              and fact["value"] < 2)
        expectation = "field='independent_context_count' 且 value 为 <2 的整数"
    else:
        expected_field = NA_PREDICATE_FIELDS.get(predicate)
        ok = (expected_field is not None
              and fact.get("field") == expected_field
              and fact.get("value") is False)
        expectation = f"field={expected_field!r} 且 value=false"
    return None if ok else expectation
