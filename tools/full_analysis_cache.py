#!/usr/bin/env python3
"""跨运行 APPROVED 产物缓存（Task 5）。

缓存键 = sha256(company_id + as_of + skill_id + methodology_sha256
                + input_evidence_digest + capability_profile)：
- methodology_sha256：该 skill 的 spec 文件内容 hash（方法论变化 → 失效）
- input_evidence_digest：当前 run manifest 中非该 skill 的 facts 规范化 digest
  （上游证据变化 → 失效；correction 改 fact 同样反映到 digest）
- capability_profile：capabilities 排序 JSON（能力变化 → 失效）

缓存目录：<company_root>/.full-analysis-cache/<cache_key>/，
只保存受 Gate 接受的 bundle 副本与 artifact 副本（带 sha256，篡改即 MISS）。
不保存 prompt、凭据或原始外部响应。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

CACHE_DIR_NAME = ".full-analysis-cache"
CACHE_SCHEMA = "approved-cache/v1"


def cache_root(run_root: Path) -> Path:
    """缓存根 = 公司目录下的 .full-analysis-cache（run_root 的父目录）。"""
    return Path(run_root).parent / CACHE_DIR_NAME


def _methodology_sha256(registry: dict, skill_id: str) -> str | None:
    skill = next((s for s in registry.get("skills", []) if s.get("skill_id") == skill_id), None)
    if not skill:
        return None
    spec = skill.get("spec_source")
    if not spec:
        return None
    spec_path = Path(__file__).resolve().parents[1] / spec
    if not spec_path.is_file():
        return None
    return hashlib.sha256(spec_path.read_bytes()).hexdigest()


def _input_evidence_digest(manifest: dict, skill_id: str) -> str:
    """非本 skill 的 facts 规范化 digest（上游证据；跨 skill fact 引用算入）。"""
    other = [
        {k: f.get(k) for k in ("fact_id", "field", "value", "source_ids", "skill_id")}
        for f in manifest.get("facts", [])
        if f.get("skill_id") != skill_id
    ]
    other.sort(key=lambda f: f.get("fact_id") or "")
    return hashlib.sha256(
        json.dumps(other, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _capability_profile(manifest: dict) -> str:
    caps = manifest.get("capabilities") or {}
    return json.dumps(caps, ensure_ascii=False, sort_keys=True)


def cache_key_for_skill(run_root: Path, manifest: dict, registry: dict, skill_id: str) -> str:
    company_code = (manifest.get("company") or {}).get("code", "?")
    as_of = (manifest.get("run") or {}).get("as_of", "?")
    method = _methodology_sha256(registry, skill_id) or "no-spec"
    input_digest = _input_evidence_digest(manifest, skill_id)
    cap = _capability_profile(manifest)
    raw = f"{company_code}|{as_of}|{skill_id}|{method}|{input_digest}|{cap}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def store_approved(run_root: Path, manifest: dict, registry: dict) -> dict:
    """finalize APPROVED 后调用：把每个 PASS 单元的正式产物写入缓存。"""
    stored = 0
    root = Path(run_root)
    for item in manifest.get("skills", []):
        if item.get("status") not in ("PASS", "PASS_WITH_LIMITATIONS"):
            continue
        records = item.get("artifact_records") or []
        if not records:
            continue
        record = records[0]
        formal = root / record.get("path", "")
        if not formal.is_file():
            continue
        key = cache_key_for_skill(root, manifest, registry, item["skill_id"])
        target = cache_root(root) / key
        target.mkdir(parents=True, exist_ok=True)
        artifact_name = Path(record.get("path", "artifact.md")).name
        artifact_target = target / artifact_name
        shutil.copy2(formal, artifact_target)
        bundle = {
            "schema_version": CACHE_SCHEMA,
            "skill_id": item["skill_id"],
            "status": item["status"],
            "artifact": {
                "name": artifact_name,
                "path": artifact_target.as_posix(),
                "bytes": formal.stat().st_size,
                "sha256": hashlib.sha256(formal.read_bytes()).hexdigest(),
            },
            "facts": [f for f in manifest.get("facts", []) if f.get("skill_id") == item["skill_id"]],
            "calculations": [c for c in manifest.get("calculations", [])
                             if c.get("skill_id") == item["skill_id"]],
            "judgments": [j for j in manifest.get("judgments", [])
                          if j.get("skill_id") == item["skill_id"]],
            "command_receipts": [r for r in manifest.get("command_receipts", [])
                                 if r.get("skill_id") == item["skill_id"]],
            "capabilities": manifest.get("capabilities") or {},
            "stored_at": _now_iso(),
        }
        (target / "bundle.json").write_text(
            json.dumps(bundle, ensure_ascii=False, indent=1), encoding="utf-8")
        stored += 1
    return {"stored": stored, "cache_root": str(cache_root(root))}


def lookup(run_root: Path, manifest: dict, registry: dict, skill_id: str) -> dict:
    """只读查询：命中且 digest 完整 → HIT，否则 MISS。"""
    root = Path(run_root)
    key = cache_key_for_skill(root, manifest, registry, skill_id)
    target = cache_root(root) / key
    bundle_path = target / "bundle.json"
    if not bundle_path.is_file():
        return {"status": "MISS", "skill_id": skill_id, "key": key}
    try:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "MISS", "skill_id": skill_id, "key": key}
    artifact = target / bundle.get("artifact", {}).get("name", "")
    if not artifact.is_file():
        return {"status": "MISS", "skill_id": skill_id, "key": key, "reason": "artifact 缺失"}
    expected_sha = bundle.get("artifact", {}).get("sha256")
    actual_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if expected_sha != actual_sha:
        return {"status": "MISS", "skill_id": skill_id, "key": key,
                "reason": "artifact digest 不匹配（缓存被篡改）"}
    return {
        "status": "HIT", "skill_id": skill_id, "key": key,
        "artifact_path": artifact.as_posix(),
        "artifact_sha256": actual_sha,
        "bundle_path": bundle_path.as_posix(),
        "stored_at": bundle.get("stored_at"),
    }


def _now_iso() -> str:
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0).isoformat()
