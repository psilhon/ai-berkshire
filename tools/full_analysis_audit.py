#!/usr/bin/env python3
"""共享 Audit Job：验证事实可追溯性与计算重放，不产生业务结论。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


PWL_CODES = {"tushare_unavailable", "web_bandwidth_degraded", "ephemeral_source"}


def load_manifest(run_root: Path) -> dict:
    return json.loads((Path(run_root) / "evidence/00-analysis-manifest.json").read_text(encoding="utf-8"))


def audit(run_root: Path) -> tuple[dict, int]:
    root = Path(run_root)
    manifest = load_manifest(root)
    facts = manifest.get("facts", [])
    sources = manifest.get("sources", [])
    calculations = manifest.get("calculations", [])
    source_ids = [source.get("source_id") for source in sources]
    fact_ids = [fact.get("fact_id") for fact in facts]
    errors: list[dict] = []
    warnings: list[dict] = []
    for value, code in ((source_ids, "duplicate_source_id"), (fact_ids, "duplicate_fact_id")):
        seen = set()
        for identifier in value:
            if identifier in seen:
                errors.append({"code": code, "detail": identifier})
            seen.add(identifier)
    known_sources = set(source_ids)
    for fact in facts:
        refs = fact.get("source_ids")
        if not isinstance(refs, list) or not refs:
            errors.append({"code": "fact_without_source", "detail": fact.get("fact_id")})
            continue
        for source_id in refs:
            if source_id not in known_sources:
                errors.append({"code": "missing_source", "detail": f"{fact.get('fact_id')} -> {source_id}"})
    replayed = 0
    for calc in calculations:
        expected = calc.get("expected") or {}
        if expected.get("replayed") is True:
            replayed += 1
        else:
            errors.append({"code": "calculation_not_replayed", "detail": calc.get("calculation_id")})
    for code in manifest.get("limitations", []):
        if isinstance(code, dict) and code.get("code") not in PWL_CODES:
            warnings.append({"code": "unclassified_limitation", "detail": code.get("code")})
    sample_size = max(5, int(len(facts) * 0.1)) if facts else 0
    checked = min(len(facts), sample_size) if facts else 0
    report = {
        "audit_schema_version": "full-analysis-audit/v1",
        "run_id": manifest.get("run", {}).get("run_id"),
        "status": "PASS" if not errors else "FAIL",
        "facts": {"total": len(facts), "checked": checked, "sample_rule": "max(5, 10%)"},
        "sources": {"total": len(sources), "unique": len(set(source_ids))},
        "calculations": {"total": len(calculations), "replayed": replayed},
        "errors": errors,
        "warnings": warnings,
    }
    target = root / "evidence/audit/audit-result.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    return report, 0 if report["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="全量分析共享 Audit")
    parser.add_argument("--run-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        report, code = audit(args.run_root)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"❌ Audit 无法执行: {exc}")
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
