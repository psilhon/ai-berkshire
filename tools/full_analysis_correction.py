#!/usr/bin/env python3
"""correction bundle 校验与落账（自 full_analysis_gate.py 外移，2026-08-30 候选⑩分区）。

correction 是 submit-result 之外的第二条账本修正通道：只允许修正**已存在**的
四类账本条目（禁新增 ID / 禁携带正式报告路径），并对非 removed 回执重跑与
submit-result 完全相同的预检——防止借 correction 绕过 Gate 注入伪造 PASS 回执。

对外接口：cmd_submit_correction（scripts/full_analysis.py submit-correction 路由）。
依赖 full_analysis_gate 的判定真源与 manifest I/O（单向依赖，零循环）。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

# gate 内部缝的跨模块复用（同包 internal seam，ADR-0001 同精神）：correction 与
# submit-result 必须共用同一回执预检与 provenance 合并逻辑，否则口径分叉。
from full_analysis_gate import (
    GateError,
    _merge_provenance,
    _precheck_command_receipts,
    append_event,
    find_skill,
    load_json,
    load_manifest,
    load_registry,
    now_iso,
    save_manifest,
)

CORRECTION_SCHEMA = "correction-bundle/v1"
CORRECTION_KINDS = ("calculation_requests", "command_receipts", "fact_updates", "judgments")
_CORRECTION_ID_KEYS = {
    "calculation_requests": "calculation_id",
    "command_receipts": "receipt_id",
    "fact_updates": "fact_id",
    "judgments": "judgment_id",
}
_CORRECTION_TARGETS = {
    "calculation_requests": "calculations",
    "command_receipts": "command_receipts",
    "fact_updates": "facts",
    "judgments": "judgments",
}
CORRECTION_FORBIDDEN = {
    "artifact_records", "source_records", "role_runs", "capability_records",
    "limitations", "pwl_candidates", "report", "summary",
}


def _validate_correction(correction: dict, manifest: dict, registry: dict) -> None:
    if correction.get("schema_version") != CORRECTION_SCHEMA:
        raise GateError(f"correction schema_version 必须是 {CORRECTION_SCHEMA}")
    run_id = (manifest.get("run") or {}).get("run_id")
    if correction.get("run_id") != run_id:
        raise GateError(f"correction run_id 与 run 不匹配: {correction.get('run_id')!r}")
    skill_id = correction.get("skill_id")
    find_skill(registry, skill_id)  # 不存在即抛
    forbidden = sorted(k for k in CORRECTION_FORBIDDEN if k in correction)
    if forbidden:
        raise GateError(f"correction 禁止携带 {forbidden}（只允许 corrections 内四类账本修正，不得带正式报告路径）")
    corrections = correction.get("corrections")
    if not isinstance(corrections, dict):
        raise GateError("corrections 必须为对象")
    non_empty = [k for k in CORRECTION_KINDS if corrections.get(k)]
    if not non_empty:
        raise GateError("corrections 至少一类非空")
    extra = sorted(set(corrections) - set(CORRECTION_KINDS))
    if extra:
        raise GateError(f"corrections 含未知类别 {extra}（允许 {list(CORRECTION_KINDS)}）")
    entry = next((item for item in manifest["skills"] if item["skill_id"] == skill_id), None)
    known_attempts = set(entry.get("attempts") or []) if entry else set()
    base = correction.get("base_attempt_id")
    if not base or base not in known_attempts:
        raise GateError(f"base_attempt_id {base!r} 不在 {skill_id} 已接受 attempts {sorted(known_attempts)} 中")
    id_sets = {
        "calculation_requests": {c.get("calculation_id") for c in manifest["calculations"] if c.get("calculation_id")},
        "command_receipts": {r.get("receipt_id") for r in manifest["command_receipts"] if r.get("receipt_id")},
        "fact_updates": {f.get("fact_id") for f in manifest["facts"] if f.get("fact_id")},
        "judgments": {j.get("judgment_id") for j in manifest["judgments"] if j.get("judgment_id")},
    }
    for kind in CORRECTION_KINDS:
        id_key = _CORRECTION_ID_KEYS[kind]
        for item in corrections.get(kind) or []:
            if not isinstance(item, dict):
                raise GateError(f"{kind} 条目必须为对象")
            rid = item.get(id_key)
            if not rid:
                raise GateError(f"{kind} 条目缺 {id_key}")
            if rid not in id_sets[kind]:
                raise GateError(
                    f"{kind} 引用不存在的 {id_key}={rid!r}（correction 只允许修改已有 ID，禁止新增）")


def _validate_correction_receipts(correction: dict, registry: dict, run_root: Path) -> None:
    """Task #45：correction 同样受回执绑定约束，禁止借 correction 绕过 Gate 注入
    未经执行器签发的 PASS 回执。

    correction 直接改写 manifest 的账本、不走 admit_bundle，若不重跑回执预检，伪造的
    PASS 回执可借此绕过签名校验进入生产账本。这里对 correction 提交的非 removed 回执
    复用与 submit-result 完全相同的 `_precheck_command_receipts`，保证两条路径口径一致。
    """
    skill = find_skill(registry, correction["skill_id"])
    recs = [r for r in correction["corrections"].get("command_receipts") or []
            if not r.get("removed")]
    if not recs:
        return
    errs = _precheck_command_receipts({"command_receipts": recs}, skill, run_root)
    if errs:
        raise GateError(
            "correction 回执预检未通过（禁止借 correction 注入未经验证签发的 PASS 回执）：\n"
            + "\n".join(errs))


def _apply_correction(manifest: dict, correction: dict, run_root: Path) -> None:
    corrections = correction["corrections"]
    # 1. removed 差集清理（雅克 run 经验：已删除请求的残留会让 audit 二次暴露）
    for kind, target in _CORRECTION_TARGETS.items():
        id_key = _CORRECTION_ID_KEYS[kind]
        removed = {
            item.get(id_key)
            for item in corrections.get(kind) or []
            if item.get("removed") is True
        }
        if removed:
            manifest[target] = [
                record for record in manifest[target]
                if record.get(id_key) not in removed
            ]
    # 2. 非 removed → last-write-wins 覆盖（复用 _merge_provenance 同一套合并逻辑）
    pseudo = {
        "skill_id": correction["skill_id"],
        "fact_updates": [f for f in corrections.get("fact_updates") or [] if not f.get("removed")],
        "calculation_requests": [c for c in corrections.get("calculation_requests") or [] if not c.get("removed")],
        "judgments": [j for j in corrections.get("judgments") or [] if not j.get("removed")],
        "command_receipts": [r for r in corrections.get("command_receipts") or [] if not r.get("removed")],
    }
    _merge_provenance(manifest, pseudo, run_root=run_root)
    # 3. 保留 correction 记录（base_attempt_id + digest，供审计/复核追溯）
    digest = hashlib.sha256(
        json.dumps(correction, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    manifest.setdefault("corrections", []).append({
        "schema_version": CORRECTION_SCHEMA,
        "skill_id": correction["skill_id"],
        "base_attempt_id": correction["base_attempt_id"],
        "digest": digest,
        "applied_at": now_iso(),
    })


def cmd_submit_correction(args: argparse.Namespace) -> int:
    root, registry = Path(args.run_root), load_registry(Path(args.registry))
    manifest = load_manifest(root)
    correction = load_json(Path(args.correction), "Correction Bundle")
    _validate_correction(correction, manifest, registry)
    _validate_correction_receipts(correction, registry, root)
    next_manifest = copy.deepcopy(manifest)
    _apply_correction(next_manifest, correction, root)
    save_manifest(root, next_manifest)
    append_event(root, {
        "type": "correction_applied",
        "skill_id": correction["skill_id"],
        "base_attempt_id": correction["base_attempt_id"],
    })
    print(json.dumps({
        "status": "CORRECTED",
        "skill_id": correction["skill_id"],
        "base_attempt_id": correction["base_attempt_id"],
    }, ensure_ascii=False))
    return 0
