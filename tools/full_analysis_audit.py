#!/usr/bin/env python3
"""共享 Audit Job：验证事实可追溯性、计算重放，并按 contract 强制执行 per-skill 证据规则。

两层校验：
1. 可追溯性（traceability）：事实必须挂来源、来源不可缺失、计算必须重放、id 不可重复。
2. 证据充分性（evidence sufficiency，P1）：对每个产出报告的适用 skill，
   - 基线：至少 1 条事实（零证据的 required 单元必须 FAIL）；
   - 穷尽执行 contract 接受的全部 evidence_rules；任何新增规则若无 evaluator
     会导致 Audit 失败，而不是被静默跳过。

证据归因：facts/calculations 携带 skill_id（由 Gate 合并时按 bundle.skill_id 打标）；
无 skill_id 的管线事实归属数据源 ashare-data。

另输出统一质量记分卡 evidence/quality-scorecard.json（不只单一 PASS/WARN）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from urllib.parse import urlparse
from pathlib import Path

from full_analysis_snapshot import analysis_snapshot


PWL_CODES = {"tushare_unavailable", "web_bandwidth_degraded", "ephemeral_source"}
DEFAULT_REGISTRY = str(Path(__file__).resolve().parent / "full_analysis_contract.json")
REPORTABLE_STATUSES = {"PASS", "PASS_WITH_LIMITATIONS"}
PIPELINE_SKILL = "ashare-data"  # 无 skill_id 归因的管线事实默认归属
FINANCIAL_RIGOR = Path(__file__).resolve().parent / "financial_rigor.py"
SUPPORTED_RULE_KINDS = {
    "min_facts", "min_dual_source_facts", "min_calculations",
    "min_judgments_with_falsification", "min_role_runs", "min_command_receipts",
    "required_fact_fields", "required_judgment_rule_ids",
    "required_command_operations", "conditional_command_operations",
}


def load_manifest(run_root: Path) -> dict:
    return json.loads((Path(run_root) / "evidence/00-analysis-manifest.json").read_text(encoding="utf-8"))


def load_registry(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, data) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _evidence_by_skill(manifest: dict) -> dict:
    """按 skill_id 归因全部契约证据账本。"""
    acc: dict = {}

    def bucket(skill_id):
        return acc.setdefault(skill_id or PIPELINE_SKILL, {
            "facts": [], "calculations": [], "judgments": [],
            "role_runs": [], "command_receipts": [],
        })

    for ledger, key in (
        ("facts", "facts"),
        ("calculations", "calculations"),
        ("judgments", "judgments"),
        ("role_runs", "role_runs"),
        ("command_receipts", "command_receipts"),
    ):
        for record in manifest.get(ledger, []):
            bucket(record.get("skill_id"))[key].append(record)
    return acc


def _violation(code: str, skill_id: str, detail: str) -> list[dict]:
    return [{"code": code, "skill_id": skill_id, "detail": detail}]


def _source_identity(source: dict) -> str | None:
    publisher = source.get("publisher")
    if isinstance(publisher, str) and publisher.strip():
        return publisher.strip().casefold()
    url = source.get("url")
    if isinstance(url, str):
        return urlparse(url).netloc.casefold() or None
    return None


def _eval_min_facts(skill_id, rule, ev, _context):
    actual = len(ev["facts"])
    return [] if actual >= rule["n"] else _violation(
        "insufficient_facts", skill_id, f"事实 {actual} < 要求 {rule['n']}")


def _eval_required_fact_fields(skill_id, rule, ev, _context):
    fields = {f.get("field") for f in ev["facts"]}
    missing = [field for field in rule["values"] if field not in fields]
    return [] if not missing else _violation(
        "missing_required_fact_fields", skill_id, f"缺必需事实字段 {missing}")


def _eval_min_dual_source_facts(skill_id, rule, ev, context):
    sources = context["sources"]
    independent = 0
    for fact in ev["facts"]:
        publishers = {
            _source_identity(sources[sid])
            for sid in set(fact.get("source_ids") or [])
            if sid in sources and _source_identity(sources[sid])
        }
        if len(publishers) >= 2:
            independent += 1
    return [] if independent >= rule["n"] else _violation(
        "insufficient_dual_source_facts", skill_id,
        f"独立双源事实 {independent} < 要求 {rule['n']}")


def _replayed_calculations(ev: dict) -> list[dict]:
    return [
        calc for calc in ev["calculations"]
        if isinstance(calc.get("expected"), dict)
        and calc["expected"].get("replayed") is True
        and calc["expected"].get("outcome") == "PASS"
    ]


def _replay_calculation_requests(calculations: list[dict]) -> bool:
    """由共享 Audit Job 调用确定性工具重放，Agent 不得自证 expected。"""
    changed = False
    for calc in calculations:
        if isinstance(calc.get("expected"), dict):
            continue
        operation = calc.get("operation")
        args = calc.get("args")
        if not isinstance(operation, str) or not isinstance(args, dict):
            continue
        command = [sys.executable, str(FINANCIAL_RIGOR), operation]
        for key, value in args.items():
            flag = "--" + key.replace("_", "-")
            if isinstance(value, bool):
                if value:
                    command.append(flag)
            else:
                encoded = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
                command.extend([flag, encoded])
        completed = subprocess.run(command, capture_output=True, text=True)
        calc["expected"] = {
            "replayed": completed.returncode == 0,
            "outcome": "PASS" if completed.returncode == 0 else "FAIL",
            "tool": "financial_rigor.py",
            "returncode": completed.returncode,
            "output_sha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
        }
        changed = True
    return changed


def _eval_min_calculations(skill_id, rule, ev, _context):
    actual = len(_replayed_calculations(ev))
    return [] if actual >= rule["n"] else _violation(
        "insufficient_calculations", skill_id,
        f"已重放计算 {actual} < 要求 {rule['n']}")


def _eval_required_judgment_rule_ids(skill_id, rule, ev, _context):
    present = {j.get("rule_id") for j in ev["judgments"]}
    missing = [rule_id for rule_id in rule["values"] if rule_id not in present]
    return [] if not missing else _violation(
        "missing_required_judgment_rules", skill_id, f"缺必需判断规则 {missing}")


def _eval_min_judgments_with_falsification(skill_id, rule, ev, _context):
    actual = sum(
        1 for judgment in ev["judgments"]
        if isinstance(judgment.get("falsification"), list)
        and any(str(item).strip() for item in judgment["falsification"])
    )
    return [] if actual >= rule["n"] else _violation(
        "insufficient_judgments_with_falsification", skill_id,
        f"含证伪条件判断 {actual} < 要求 {rule['n']}")


def _eval_min_role_runs(skill_id, rule, ev, context):
    run_root = context["run_root"]
    roles = {
        record.get("role_id") for record in ev["role_runs"]
        if (
            record.get("status") == "PASS"
            and record.get("role_id")
            and record.get("verified_by_gate") is True
            and isinstance(record.get("artifact_path"), str)
            and (run_root / record["artifact_path"]).is_file()
            and not (run_root / record["artifact_path"]).is_symlink()
            and (run_root / record["artifact_path"]).stat().st_size == record.get("bytes")
            and hashlib.sha256(
                (run_root / record["artifact_path"]).read_bytes(),
            ).hexdigest() == record.get("sha256")
        )
    }
    return [] if len(roles) >= rule["n"] else _violation(
        "insufficient_role_runs", skill_id, f"独立角色运行 {len(roles)} < 要求 {rule['n']}")


def _passed_operations(ev: dict) -> set[str]:
    return {
        receipt.get("operation") for receipt in ev["command_receipts"]
        if receipt.get("status") == "PASS" and receipt.get("operation")
    }


def _eval_min_command_receipts(skill_id, rule, ev, _context):
    actual = sum(1 for receipt in ev["command_receipts"] if receipt.get("status") == "PASS")
    return [] if actual >= rule["n"] else _violation(
        "insufficient_command_receipts", skill_id,
        f"成功命令回执 {actual} < 要求 {rule['n']}")


def _eval_required_command_operations(skill_id, rule, ev, _context):
    passed = _passed_operations(ev)
    missing = [operation for operation in rule["values"] if operation not in passed]
    return [] if not missing else _violation(
        "missing_required_command_operations", skill_id, f"缺成功命令操作 {missing}")


def _eval_conditional_command_operations(skill_id, rule, ev, context):
    capability = rule.get("capability")
    available = context["capabilities"].get(capability)
    limitations = {item.get("code") for item in context["limitations"] if isinstance(item, dict)}
    if available is False and "tushare_unavailable" in limitations:
        return []
    if available is not True:
        return _violation(
            "missing_capability_attestation", skill_id,
            f"缺 capability {capability!r} 可用性声明")
    values = rule.get("values", [])
    passed = _passed_operations(ev)
    required = rule.get("min_satisfied_ratio", 1.0)

    # 兑结合约声明的容忍标志：对已登记回执并附 limitation 说明的命令，
    # 在计算满足率分母时予以豁免（结构性不适用 / 真实空数据，均不构成证据缺陷）。
    exempt_ops: set[str] = set()
    for receipt in ev["command_receipts"]:
        op = receipt.get("operation")
        reason = str(receipt.get("reason") or "")
        if receipt.get("status") == "FAIL" and reason:
            if ("market_level_cmd_na" in reason
                    and rule.get("tolerate_missing_with_limitation")):
                exempt_ops.add(op)
            elif ("empty_data" in reason
                    and rule.get("tolerate_failed_with_limitation")):
                exempt_ops.add(op)

    eligible = [item["op"] for item in values if item["op"] not in exempt_ops]
    satisfied = sum(1 for operation in eligible if operation in passed)
    ratio = satisfied / len(eligible) if eligible else 1.0
    return [] if ratio >= required else _violation(
        "insufficient_conditional_command_operations", skill_id,
        f"条件命令满足率 {ratio:.3f} < 要求 {required:.3f}"
        f"（合格命令 {len(eligible)}/{len(values)}，豁免 {len(exempt_ops)} 个带说明的失败/不适用命令）")


RULE_EVALUATORS = {
    "min_facts": _eval_min_facts,
    "required_fact_fields": _eval_required_fact_fields,
    "min_dual_source_facts": _eval_min_dual_source_facts,
    "min_calculations": _eval_min_calculations,
    "required_judgment_rule_ids": _eval_required_judgment_rule_ids,
    "min_judgments_with_falsification": _eval_min_judgments_with_falsification,
    "min_role_runs": _eval_min_role_runs,
    "min_command_receipts": _eval_min_command_receipts,
    "required_command_operations": _eval_required_command_operations,
    "conditional_command_operations": _eval_conditional_command_operations,
}


def _check_skill_evidence(
    skill_id: str,
    rules: list,
    ev: dict,
    *,
    sources: dict[str, dict],
    capabilities: dict,
    limitations: list,
    run_root: Path,
) -> list[dict]:
    """对单个 skill 穷尽执行 evidence_rules，返回违规列表。"""
    violations = []
    if not ev["facts"]:
        violations.extend(_violation("no_skill_evidence", skill_id, "产出报告但零事实"))
    context = {
        "sources": sources,
        "capabilities": capabilities,
        "limitations": limitations,
        "run_root": run_root,
    }
    for rule in rules:
        kind = rule.get("kind")
        evaluator = RULE_EVALUATORS.get(kind)
        if evaluator is None:
            violations.extend(_violation(
                "unsupported_evidence_rule", skill_id, f"Audit 未实现规则 {kind!r}"))
            continue
        violations.extend(evaluator(skill_id, rule, ev, context))
    return violations


def audit(run_root: Path, registry_path: Path = Path(DEFAULT_REGISTRY)) -> tuple[dict, int]:
    root = Path(run_root)
    manifest = load_manifest(root)
    facts = manifest.get("facts", [])
    sources = manifest.get("sources", [])
    calculations = manifest.get("calculations", [])
    if _replay_calculation_requests(calculations):
        _atomic_write_json(root / "evidence/00-analysis-manifest.json", manifest)
    source_ids = [source.get("source_id") for source in sources]
    fact_ids = [fact.get("fact_id") for fact in facts]
    errors: list[dict] = []
    warnings: list[dict] = []

    # ---- 层 1：可追溯性 ----
    for value, code in ((source_ids, "duplicate_source_id"), (fact_ids, "duplicate_fact_id")):
        seen = set()
        for identifier in value:
            if identifier in seen:
                errors.append({"code": code, "detail": identifier})
            seen.add(identifier)
    known_sources = set(source_ids)
    facts_with_source = 0
    for fact in facts:
        refs = fact.get("source_ids")
        if not isinstance(refs, list) or not refs:
            errors.append({"code": "fact_without_source", "detail": fact.get("fact_id")})
            continue
        facts_with_source += 1
        for source_id in refs:
            if source_id not in known_sources:
                errors.append({"code": "missing_source", "detail": f"{fact.get('fact_id')} -> {source_id}"})
    replayed = 0
    for calc in calculations:
        expected = calc.get("expected")
        if (isinstance(expected, dict) and expected.get("replayed") is True
                and expected.get("outcome") == "PASS"):
            replayed += 1
        else:
            errors.append({"code": "calculation_not_replayed", "detail": calc.get("calculation_id")})
    for item in manifest.get("limitations", []):
        if isinstance(item, dict) and item.get("code") not in PWL_CODES:
            warnings.append({"code": "unclassified_limitation", "detail": item.get("code")})

    # ---- 层 2：per-skill 证据充分性 ----
    registry = load_registry(registry_path)
    unsupported = sorted({
        rule.get("kind")
        for skill in registry.get("skills", [])
        for rule in skill.get("evidence_rules", [])
        if rule.get("kind") not in RULE_EVALUATORS
    })
    if unsupported or set(RULE_EVALUATORS) != SUPPORTED_RULE_KINDS:
        errors.append({
            "code": "evidence_evaluator_coverage",
            "detail": {"unsupported": unsupported,
                       "missing": sorted(SUPPORTED_RULE_KINDS - set(RULE_EVALUATORS))},
        })
    rules_by_skill = {s["skill_id"]: (s.get("evidence_rules") or []) for s in registry.get("skills", [])}
    ev_by_skill = _evidence_by_skill(manifest)
    source_index = {source.get("source_id"): source for source in sources if source.get("source_id")}
    evidence_checks: list[dict] = []
    evidence_violations: list[dict] = []
    for item in manifest.get("skills", []):
        sid = item["skill_id"]
        status = item.get("status")
        if status not in REPORTABLE_STATUSES:
            continue  # N/A / 未完成单元不要求证据
        rules = rules_by_skill.get(sid, [])
        ev = ev_by_skill.get(sid, {
            "facts": [], "calculations": [], "judgments": [],
            "role_runs": [], "command_receipts": [],
        })
        viols = _check_skill_evidence(
            sid, rules, ev,
            sources=source_index,
            capabilities=manifest.get("capabilities") or {},
            limitations=item.get("limitations") or [],
            run_root=root,
        )
        evidence_checks.append({
            "skill_id": sid, "status": status,
            "fact_count": len(ev["facts"]),
            "calculation_count": len(_replayed_calculations(ev)),
            "judgment_count": len(ev["judgments"]),
            "role_run_count": len(ev["role_runs"]),
            "command_receipt_count": len(ev["command_receipts"]),
            "required_rules": len(rules),
            "violations": [v["code"] for v in viols],
        })
        evidence_violations.extend(viols)
    errors.extend(evidence_violations)

    total_facts = len(facts)
    sample_size = max(5, int(total_facts * 0.1)) if total_facts else 0
    checked = min(total_facts, sample_size) if total_facts else 0
    claim_source_coverage = (facts_with_source / total_facts) if total_facts else 0.0

    report = {
        "audit_schema_version": "full-analysis-audit/v2",
        "run_id": manifest.get("run", {}).get("run_id"),
        **analysis_snapshot(manifest, registry_path),
        "status": "PASS" if not errors else "FAIL",
        "facts": {"total": total_facts, "checked": checked, "sample_rule": "max(5, 10%)"},
        "sources": {"total": len(sources), "unique": len(set(source_ids))},
        "calculations": {"total": len(calculations), "replayed": replayed},
        "evidence": {
            "skills_checked": len(evidence_checks),
            "violation_count": len(evidence_violations),
            "per_skill": evidence_checks,
        },
        "errors": errors,
        "warnings": warnings,
    }

    # ---- 质量记分卡（不只单一 PASS/WARN）----
    scorecard = {
        "scorecard_schema_version": "quality-scorecard/v1",
        "run_id": manifest.get("run", {}).get("run_id"),
        "audit_status": report["status"],
        "claim_source_coverage": round(claim_source_coverage, 3),
        "facts_total": total_facts,
        "sources_total": len(sources),
        "calculations_replayed": replayed,
        "calculations_total": len(calculations),
        "evidence_sufficiency": {
            "skills_with_violations": sorted({v["skill_id"] for v in evidence_violations}),
            "violation_count": len(evidence_violations),
            "per_skill": evidence_checks,
        },
    }

    audit_dir = root / "evidence/audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(audit_dir / "audit-result.json", report)
    _atomic_write_json(root / "evidence/quality-scorecard.json", scorecard)
    return report, 0 if report["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="全量分析共享 Audit")
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY, type=Path)
    args = parser.parse_args()
    try:
        report, code = audit(args.run_root, args.registry)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"❌ Audit 无法执行: {exc}")
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
