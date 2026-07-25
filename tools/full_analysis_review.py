#!/usr/bin/env python3
"""P2 语义评审层：对核心分析单元做独立语义评审。

定位：确定性 Gate（min_bytes/证据规则/哈希）之后、doctor 体检之前的中间层。
- Gate 回答"产物是否存在、是否达标、是否可追溯"（结构）；
- 语义评审回答"结论是否真的被证据支持、是否有未解决冲突、反面证据是否被处理"（语义）；
- Doctor 回答"执行过程是否退化"（过程指纹）。

语义评审不替代确定性 Gate；它产生 REVIEW_REQUIRED + 定向返工建议，
且完整的 REVIEW_PASSED 是 finalize 准出的必要条件。

工作流：
1. prepare：读取核心 skill 的正式报告 + 归因证据，生成评审简报（review-brief-<skill>.json）；
2. ingest：接收评审子 Agent 产出的结构化评审结果，校验 schema 后落盘；
3. summarize：聚合所有评审结果 → 总体裁决（REVIEW_PASSED / REVIEW_REQUIRED）+ 定向返工清单。

评审五维度：
- evidence_support：核心结论是否由归因证据支持
- unresolved_conflicts：是否存在未解决的事实冲突
- counter_evidence：反面证据/分歧点是否被充分处理
- valuation_consistency：估值区间与事实/计算是否内部一致
- limitations_completeness：限制项是否完整、是否存在未披露的重大不确定性
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


REVIEW_SCHEMA_VERSION = "semantic-review/v1"
BRIEF_SCHEMA_VERSION = "review-brief/v1"
SUMMARY_SCHEMA_VERSION = "review-summary/v1"

# 语义评审范围：高判断密度的核心单元（可由 --scope 覆盖）
DEFAULT_REVIEW_SCOPE = [
    "investment-research",
    "investment-team",
    "investment-checklist",
    "management-deep-dive",
    "earnings-review",
    "industry-research",
    "thesis-tracker",
    "delivery-summary",
]

# 五维度评审协议：每个维度的检查要点，嵌入简报供评审 Agent 使用
REVIEW_PROTOCOL = {
    "evidence_support": {
        "label": "证据支持",
        "checks": [
            "核心结论中的每个定量/定性断言，是否能在归因事实或来源中找到对应支撑",
            "是否存在结论强度超过证据强度的过度推断（如证据为单源低置信，结论却用确定性措辞）",
            "关键数字（营收/利润/增速/市占率）是否与归因事实一致",
        ],
    },
    "unresolved_conflicts": {
        "label": "事实冲突",
        "checks": [
            "不同来源对同一事实的数值是否存在未解释的分歧（如两源营收差异 >5%）",
            "报告是否承认并解释了数据矛盾，还是选择性忽略",
            "双源事实是否真正交叉验证，还是仅形式挂双源",
        ],
    },
    "counter_evidence": {
        "label": "反面证据",
        "checks": [
            "反面检验/分歧点章节是否引用了真实的对立证据，而非稻草人",
            "看空/看多逻辑是否被同等力度对待，还是明显偏向一方",
            "是否存在已知的重大风险因素（监管/竞争/技术替代）被完全回避",
        ],
    },
    "valuation_consistency": {
        "label": "估值一致性",
        "checks": [
            "估值区间的输入假设（增速/利润率/折现率）是否与报告其他部分的事实一致",
            "乐观/中性/悲观情景是否覆盖合理范围，而非全部偏向乐观",
            "估值结论与核心结论的买入/持有/回避建议是否逻辑自洽",
        ],
    },
    "limitations_completeness": {
        "label": "限制完整性",
        "checks": [
            "数据截止日、来源范围、方法论限制是否如实披露",
            "是否存在影响结论的重大不确定性未被列入限制项",
            "PWL（PASS_WITH_LIMITATIONS）的限制码是否准确反映了实际降级原因",
        ],
    },
}

REPORTABLE_STATUSES = {"PASS", "PASS_WITH_LIMITATIONS"}
REVIEWABLE_STATUSES = REPORTABLE_STATUSES | {"NOT_APPLICABLE"}


def load_manifest(run_root: Path) -> dict:
    return json.loads((Path(run_root) / "evidence/00-analysis-manifest.json").read_text(encoding="utf-8"))


def load_registry(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _digest(value) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evidence_for_skill(manifest: dict, skill_id: str) -> dict:
    """提取归因到指定 skill 的完整结构化证据。"""
    if skill_id == "delivery-summary":
        return {
            "facts": manifest.get("facts") or [],
            "sources": manifest.get("sources") or [],
            "calculations": manifest.get("calculations") or [],
            "judgments": manifest.get("judgments") or [],
            "command_receipts": manifest.get("command_receipts") or [],
            "role_runs": manifest.get("role_runs") or [],
        }
    facts = [f for f in manifest.get("facts", []) if f.get("skill_id") == skill_id]
    calcs = [c for c in manifest.get("calculations", []) if c.get("skill_id") == skill_id]
    judgments = [j for j in manifest.get("judgments", []) if j.get("skill_id") == skill_id]
    receipts = [r for r in manifest.get("command_receipts", []) if r.get("skill_id") == skill_id]
    role_runs = [r for r in manifest.get("role_runs", []) if r.get("skill_id") == skill_id]
    # 来源：归因到该 skill 的，或被该 skill 事实引用的
    fact_source_ids = set()
    for f in facts:
        fact_source_ids.update(f.get("source_ids") or [])
    sources = [s for s in manifest.get("sources", [])
               if s.get("skill_id") == skill_id or s.get("source_id") in fact_source_ids]
    return {
        "facts": facts,
        "sources": sources,
        "calculations": calcs,
        "judgments": judgments,
        "command_receipts": receipts,
        "role_runs": role_runs,
    }


def _read_report(run_root: Path, skill_item: dict) -> str | None:
    """读取 skill 的正式报告内容。"""
    records = skill_item.get("artifact_records") or []
    if not records:
        return None
    rel = records[0].get("path", "")
    fp = Path(run_root) / rel
    if not fp.is_file():
        return None
    return fp.read_text(encoding="utf-8")


def required_review_scope(manifest: dict) -> list[str]:
    """Return the minimum production review scope for this manifest."""
    scope = list(DEFAULT_REVIEW_SCOPE)
    for item in manifest.get("skills", []):
        if (item.get("status") == "NOT_APPLICABLE"
                and item.get("skill_id") not in scope):
            scope.append(item["skill_id"])
    return scope


def cmd_prepare(args: argparse.Namespace) -> int:
    """为每个评审范围内的核心 skill 生成评审简报。"""
    root = Path(args.run_root)
    registry = load_registry(Path(args.registry))
    manifest = load_manifest(root)
    scope = (
        args.scope.split(",")
        if args.scope
        else required_review_scope(manifest)
    )
    # 构建 contract 索引
    contract_skills = {s["skill_id"]: s for s in registry.get("skills", [])}
    manifest_skills = {s["skill_id"]: s for s in manifest.get("skills", [])}

    review_dir = root / "evidence/review"
    review_dir.mkdir(parents=True, exist_ok=True)
    prepared = []
    brief_index = {}

    for skill_id in scope:
        if skill_id == "delivery-summary":
            summary = (manifest.get("delivery") or {}).get("summary")
            if not summary:
                continue
            m_item = {
                "status": "PASS",
                "artifact_records": [summary],
            }
            c_item = {
                "sections": [{"heading": heading} for heading in (
                    "核心结论速览", "主干①·投资分析", "主干②·财报研读",
                    "主干③·行业分析", "补充与参考", "产物索引",
                )],
                "core": True,
                "roles": {"mode": "single_agent"},
            }
        else:
            m_item = manifest_skills.get(skill_id)
            if not m_item:
                continue
            status = m_item.get("status")
            if status not in REVIEWABLE_STATUSES:
                continue
            c_item = contract_skills.get(skill_id, {})
        evidence = _evidence_for_skill(manifest, skill_id)
        report_text = _read_report(root, m_item)
        if report_text is None:
            continue  # 无正式产物则跳过

        report_digest = hashlib.sha256(report_text.encode("utf-8")).hexdigest()
        evidence_digest = _digest(evidence)
        digest_payload = {
            "run_id": manifest.get("run", {}).get("run_id"),
            "skill_id": skill_id,
            "report_digest": report_digest,
            "evidence_digest": evidence_digest,
            "contract": {
                "sections": [s["heading"] for s in c_item.get("sections", [])],
                "core": c_item.get("core", False),
                "roles_mode": (c_item.get("roles") or {}).get("mode", "single_agent"),
            },
            "review_protocol": REVIEW_PROTOCOL,
        }
        brief_digest = _digest(digest_payload)
        brief = {
            "brief_schema_version": BRIEF_SCHEMA_VERSION,
            "skill_id": skill_id,
            "run_id": manifest.get("run", {}).get("run_id"),
            "brief_digest": brief_digest,
            "prepared_at": _now_iso(),
            "report": {
                "path": (m_item.get("artifact_records") or [{}])[0].get("path", ""),
                "content": report_text,
                "bytes": len(report_text.encode("utf-8")),
                "sha256": report_digest,
            },
            "evidence": {
                "fact_count": len(evidence["facts"]),
                "source_count": len(evidence["sources"]),
                "calculation_count": len(evidence["calculations"]),
                "facts": evidence["facts"],
                "sources": evidence["sources"],
                "calculations": evidence["calculations"],
                "judgments": evidence["judgments"],
                "command_receipts": evidence["command_receipts"],
                "role_runs": evidence["role_runs"],
                "sha256": evidence_digest,
            },
            "contract": digest_payload["contract"],
            "review_protocol": REVIEW_PROTOCOL,
            "instructions": (
                "你是独立语义评审 Agent。请逐一检查五个维度，对每个维度给出 PASS 或 FINDING。"
                "FINDING 必须包含 severity（high/medium/low）、具体描述、证据引用、返工建议。"
                "若任一维度存在 high severity finding，verdict 必须为 REVIEW_REQUIRED。"
                "输出严格遵循 semantic-review/v1 schema。不要编造证据引用。"
            ),
        }
        brief_path = review_dir / f"review-brief-{skill_id}.json"
        _atomic_write_json(brief_path, brief)
        brief_index[skill_id] = {
            "brief_digest": brief_digest,
            "report_digest": report_digest,
            "evidence_digest": evidence_digest,
        }
        prepared.append({"skill_id": skill_id, "brief_path": str(brief_path),
                         "fact_count": len(evidence["facts"]),
                         "report_bytes": len(report_text.encode("utf-8"))})

    _atomic_write_json(review_dir / "review-index.json", {
        "run_id": manifest.get("run", {}).get("run_id"),
        "scope": [item["skill_id"] for item in prepared],
        "briefs": brief_index,
        "prepared_at": _now_iso(),
    })
    print(json.dumps({"prepared": prepared, "count": len(prepared)}, ensure_ascii=False))
    return 0


def _validate_review_result(result: dict) -> list[str]:
    """校验评审结果的 schema 合规性。"""
    errors = []
    if result.get("review_schema_version") != REVIEW_SCHEMA_VERSION:
        errors.append(f"review_schema_version 必须为 {REVIEW_SCHEMA_VERSION!r}")
    if not isinstance(result.get("skill_id"), str) or not result["skill_id"]:
        errors.append("skill_id 缺失")
    for field in ("run_id", "brief_digest", "report_digest", "evidence_digest"):
        if not isinstance(result.get(field), str) or not result[field]:
            errors.append(f"{field} 缺失")
    if result.get("verdict") not in ("PASS", "REVIEW_REQUIRED"):
        errors.append(f"verdict 必须为 PASS 或 REVIEW_REQUIRED，实际 {result.get('verdict')!r}")
    findings = result.get("findings")
    dimensions = result.get("dimensions")
    valid_dims = set(REVIEW_PROTOCOL.keys())
    finding_dims = set()
    if not isinstance(dimensions, list):
        errors.append("dimensions 必须为数组")
    else:
        seen_dims = []
        for i, dimension in enumerate(dimensions):
            if not isinstance(dimension, dict):
                errors.append(f"dimensions[{i}] 必须为对象")
                continue
            name = dimension.get("dimension")
            seen_dims.append(name)
            if name not in valid_dims:
                errors.append(f"dimensions[{i}].dimension 非法: {name!r}")
            if dimension.get("verdict") not in ("PASS", "FINDING"):
                errors.append(f"dimensions[{i}].verdict 非法")
            if dimension.get("verdict") == "FINDING":
                finding_dims.add(name)
        if len(seen_dims) != len(set(seen_dims)) or set(seen_dims) != valid_dims:
            errors.append("dimensions 必须恰好覆盖五个评审维度且不得重复")
    if not isinstance(findings, list):
        errors.append("findings 必须为数组")
    else:
        described_dims = set()
        for i, f in enumerate(findings):
            if not isinstance(f, dict):
                errors.append(f"findings[{i}] 必须为对象")
                continue
            if f.get("dimension") not in valid_dims:
                errors.append(f"findings[{i}].dimension 非法: {f.get('dimension')!r}")
            if f.get("severity") not in ("high", "medium", "low"):
                errors.append(f"findings[{i}].severity 非法: {f.get('severity')!r}")
            if not isinstance(f.get("description"), str) or not f["description"]:
                errors.append(f"findings[{i}].description 缺失")
            if not isinstance(f.get("evidence_refs"), list) or not f["evidence_refs"]:
                errors.append(f"findings[{i}].evidence_refs 缺失")
            if not isinstance(f.get("remediation"), str) or not f["remediation"]:
                errors.append(f"findings[{i}].remediation 缺失")
            described_dims.add(f.get("dimension"))
        if finding_dims != described_dims:
            errors.append("dimensions 的 FINDING 与 findings 覆盖不一致")
        if result.get("verdict") == "PASS" and any(
                finding.get("severity") == "high" for finding in findings if isinstance(finding, dict)):
            errors.append("存在 high finding 时 verdict 不得为 PASS")
    return errors


def cmd_ingest(args: argparse.Namespace) -> int:
    """接收并校验一份评审结果，落盘到 evidence/review/。"""
    review_path = Path(args.review)
    try:
        result = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"❌ 评审结果不可读: {exc}", file=sys.stderr)
        return 2
    errors = _validate_review_result(result)
    if errors:
        print(f"❌ 评审结果 schema 不合规: {errors}", file=sys.stderr)
        return 1
    skill_id = result["skill_id"]
    root = Path(args.run_root)
    brief_path = root / "evidence/review" / f"review-brief-{skill_id}.json"
    try:
        brief = json.loads(brief_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"❌ 缺少有效评审简报: {exc}", file=sys.stderr)
        return 1
    expected = {
        "run_id": brief.get("run_id"),
        "brief_digest": brief.get("brief_digest"),
        "report_digest": (brief.get("report") or {}).get("sha256"),
        "evidence_digest": (brief.get("evidence") or {}).get("sha256"),
    }
    mismatched = [field for field, value in expected.items() if result.get(field) != value]
    if mismatched:
        print(f"❌ 评审结果与当前简报不匹配: {mismatched}", file=sys.stderr)
        return 1
    out_path = root / "evidence/review" / f"review-result-{skill_id}.json"
    _atomic_write_json(out_path, result)
    print(json.dumps({"skill_id": skill_id, "verdict": result["verdict"],
                      "findings_count": len(result.get("findings", [])),
                      "path": str(out_path)}, ensure_ascii=False))
    return 0


def aggregate(run_root: Path) -> tuple[dict, int]:
    """聚合所有评审结果 → 总体裁决 + 定向返工清单。返回 (summary_dict, exit_code)。

    供 cmd_summarize（CLI）和 Gate finalize（强制准出门）复用。
    """
    root = Path(run_root)
    review_dir = root / "evidence/review"
    if not review_dir.is_dir():
        return {}, 2

    index_path = review_dir / "review-index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, 1
    expected_scope = set(index.get("scope") or [])
    expected_briefs = index.get("briefs") or {}
    results = []
    invalid_results = []
    stale_skills = set()
    try:
        current_manifest = load_manifest(root)
    except (OSError, json.JSONDecodeError, KeyError):
        current_manifest = None
    for sid in sorted(expected_scope):
        brief_path = review_dir / f"review-brief-{sid}.json"
        try:
            brief = json.loads(brief_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            invalid_results.append({"skill_id": sid, "reason": f"评审简报不可读: {exc}"})
            stale_skills.add(sid)
            continue
        expected = expected_briefs.get(sid) or {}
        digest_payload = {
            "run_id": brief.get("run_id"),
            "skill_id": brief.get("skill_id"),
            "report_digest": (brief.get("report") or {}).get("sha256"),
            "evidence_digest": (brief.get("evidence") or {}).get("sha256"),
            "contract": brief.get("contract"),
            "review_protocol": brief.get("review_protocol"),
        }
        brief_binding = {
            "brief_digest": _digest(digest_payload),
            "report_digest": digest_payload["report_digest"],
            "evidence_digest": digest_payload["evidence_digest"],
        }
        if any(brief_binding[key] != expected.get(key) for key in brief_binding):
            invalid_results.append({"skill_id": sid, "reason": "评审简报与 review-index 不一致"})
            stale_skills.add(sid)
            continue
        if current_manifest is not None:
            report_path = root / (brief.get("report") or {}).get("path", "")
            current_report_digest = (
                hashlib.sha256(report_path.read_bytes()).hexdigest()
                if report_path.is_file() else None
            )
            current_evidence_digest = _digest(_evidence_for_skill(current_manifest, sid))
            if (current_report_digest != expected.get("report_digest")
                    or current_evidence_digest != expected.get("evidence_digest")):
                invalid_results.append({"skill_id": sid, "reason": "报告或证据已在评审后变化"})
                stale_skills.add(sid)
    for fp in sorted(review_dir.glob("review-result-*.json")):
        try:
            result = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            invalid_results.append({"file": fp.name, "reason": f"不可读: {exc}"})
            continue
        errors = _validate_review_result(result)
        sid = result.get("skill_id")
        if sid in stale_skills:
            errors.append("对应评审简报已过期")
        expected = expected_briefs.get(sid) or {}
        if sid not in expected_scope:
            errors.append("skill_id 不在 prepared scope")
        for field, expected_key in (
            ("brief_digest", "brief_digest"),
            ("report_digest", "report_digest"),
            ("evidence_digest", "evidence_digest"),
        ):
            if result.get(field) != expected.get(expected_key):
                errors.append(f"{field} 与 review-index 不一致")
        if result.get("run_id") != index.get("run_id"):
            errors.append("run_id 与 review-index 不一致")
        if errors:
            invalid_results.append({"file": fp.name, "skill_id": sid, "reason": "; ".join(errors)})
            continue
        results.append(result)

    result_skills = {result["skill_id"] for result in results}
    missing_skills = sorted(expected_scope - result_skills)

    # 聚合
    skills_review_required = []
    all_findings = []
    rework_targets = []
    for r in results:
        sid = r["skill_id"]
        findings = r.get("findings", [])
        high_findings = [f for f in findings if f.get("severity") == "high"]
        if r.get("verdict") == "REVIEW_REQUIRED" or high_findings:
            skills_review_required.append(sid)
            rework_targets.append({
                "skill_id": sid,
                "reason": "; ".join(f["description"] for f in high_findings) or "评审标记 REVIEW_REQUIRED",
                "high_severity_count": len(high_findings),
            })
        for f in findings:
            all_findings.append({"skill_id": sid, **f})

    overall_verdict = (
        "REVIEW_REQUIRED"
        if skills_review_required or missing_skills or invalid_results
        else "REVIEW_PASSED"
    )
    severity_counts = {"high": 0, "medium": 0, "low": 0}
    for f in all_findings:
        sev = f.get("severity", "low")
        if sev in severity_counts:
            severity_counts[sev] += 1

    summary = {
        "summary_schema_version": SUMMARY_SCHEMA_VERSION,
        "run_id": index.get("run_id"),
        "summarized_at": _now_iso(),
        "overall_verdict": overall_verdict,
        "skills_reviewed": len(results),
        "skills_review_required": sorted(skills_review_required),
        "missing_skills": missing_skills,
        "invalid_results": invalid_results,
        "severity_counts": severity_counts,
        "total_findings": len(all_findings),
        "rework_targets": rework_targets,
        "findings": all_findings,
        "per_skill": [
            {"skill_id": r["skill_id"], "verdict": r.get("verdict"),
             "findings_count": len(r.get("findings", []))}
            for r in results
        ],
    }
    return summary, (0 if overall_verdict == "REVIEW_PASSED" else 1)


def cmd_summarize(args: argparse.Namespace) -> int:
    """聚合所有评审结果 → 总体裁决 + 定向返工清单。"""
    root = Path(args.run_root)
    review_dir = root / "evidence/review"
    if not review_dir.is_dir():
        print("❌ evidence/review/ 不存在，请先运行 prepare + ingest", file=sys.stderr)
        return 2

    summary, code = aggregate(root)
    if not summary:
        print("❌ 无有效评审结果", file=sys.stderr)
        return code if code != 0 else 1

    summary_path = root / "evidence/review/semantic-review-summary.json"
    _atomic_write_json(summary_path, summary)
    print(json.dumps({"overall_verdict": summary["overall_verdict"],
                      "skills_review_required": summary["skills_review_required"],
                      "total_findings": summary["total_findings"],
                      "severity_counts": summary["severity_counts"],
                      "path": str(summary_path)}, ensure_ascii=False))
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="全量分析语义评审层（P2）")
    sub = parser.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prepare", help="为核心 skill 生成评审简报")
    prep.add_argument("--run-root", required=True)
    prep.add_argument("--registry", default=str(Path(__file__).resolve().parent / "full_analysis_contract.json"))
    prep.add_argument("--scope", default=None,
                      help="逗号分隔的 skill_id 列表，覆盖默认评审范围")

    ing = sub.add_parser("ingest", help="接收一份评审结果")
    ing.add_argument("--run-root", required=True)
    ing.add_argument("--review", required=True, help="评审结果 JSON 文件路径")

    sub.add_parser("summarize", help="聚合评审结果").add_argument("--run-root", required=True)

    args = parser.parse_args(argv)
    handlers = {"prepare": cmd_prepare, "ingest": cmd_ingest, "summarize": cmd_summarize}
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
