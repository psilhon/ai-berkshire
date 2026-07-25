#!/usr/bin/env python3
"""Deterministic input snapshot shared by Full Analysis Audit and Gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


SNAPSHOT_SCHEMA_VERSION = "full-analysis-snapshot/v1"
LEDGERS = (
    "facts", "sources", "calculations", "judgments",
    "command_receipts", "role_runs", "capabilities",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_projection(manifest: dict) -> dict:
    run = manifest.get("run") or {}
    projection = {
        "manifest_schema_version": manifest.get("manifest_schema_version"),
        "contract": manifest.get("contract"),
        "run": {
            "run_id": run.get("run_id"),
            "platform": run.get("platform"),
            "as_of": run.get("as_of"),
        },
        "company": manifest.get("company"),
        "skills": [{
            "skill_id": item.get("skill_id"),
            "status": item.get("status"),
            "attempts": item.get("attempts") or [],
            "artifact_records": item.get("artifact_records") or [],
            "limitations": item.get("limitations") or [],
            "not_applicable": item.get("not_applicable"),
        } for item in manifest.get("skills", [])],
        "artifacts": manifest.get("artifacts") or [],
        "delivery": manifest.get("delivery") or {},
    }
    for ledger in LEDGERS:
        projection[ledger] = manifest.get(ledger) or ([] if ledger != "capabilities" else {})
    return projection


def analysis_snapshot(manifest: dict, registry_path: Path) -> dict:
    registry_sha256 = sha256_file(registry_path)
    payload = {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "registry_sha256": registry_sha256,
        "manifest": _manifest_projection(manifest),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "registry_sha256": registry_sha256,
        "snapshot_digest": hashlib.sha256(encoded).hexdigest(),
    }
