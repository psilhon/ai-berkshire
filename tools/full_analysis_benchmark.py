#!/usr/bin/env python3
"""P3 重复运行稳定性基准：对比同一公司的多次 run，量化输出一致性。

定位：确定性 Gate/Audit/Doctor/Review 之后的元质量层。
单次 run 的确定性检查回答"这一次是否合格"；
稳定性基准回答"多次运行是否给出一致的答案"。

核心指标（目标值）：
- fact_consistency_rate：关键事实跨 run 一致率（目标 100%）
- calculation_consistency_rate：计算结果跨 run 一致率（目标 100%）
- claim_source_coverage：claim→source 覆盖率（目标 100%）
- conclusion_drift：未解释核心结论漂移数（目标 0）
- evidence_count_variance：各 skill 证据数量方差（目标趋近 0）
- doctor_agreement：doctor 裁决跨 run 一致率

工作流：
1. compare：接收 2+ 个 run-root，对比 manifest 中的 facts/calculations/sources/judgments；
2. 产出 evidence/benchmark/stability-report.json。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path


STABILITY_SCHEMA_VERSION = "stability-report/v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_manifest(run_root: Path) -> dict:
    return json.loads((Path(run_root) / "evidence/00-analysis-manifest.json").read_text(encoding="utf-8"))


def _load_json_safe(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _index_facts(manifest: dict) -> dict[str, dict]:
    """按 fact_id 索引事实。"""
    return {f["fact_id"]: f for f in manifest.get("facts", []) if "fact_id" in f}


def _index_calculations(manifest: dict) -> dict[str, dict]:
    """按 calculation_id 索引计算。"""
    return {c["calculation_id"]: c for c in manifest.get("calculations", []) if "calculation_id" in c}


def _fact_consistency(runs: list[dict]) -> dict:
    """关键事实跨 run 一致率。

    对比维度：同一 fact_id 的 value 是否一致。
    """
    if len(runs) < 2:
        return {"rate": None, "total_fields": 0, "consistent_fields": 0, "divergent": []}

    fact_indices = [_index_facts(m) for m in runs]
    all_fact_ids = set()
    for idx in fact_indices:
        all_fact_ids.update(idx.keys())

    consistent = 0
    missing_count = 0
    divergent = []
    for fid in sorted(all_fact_ids):
        values = [
            str(idx[fid].get("value", "")) if fid in idx else None
            for idx in fact_indices
        ]
        missing = sum(value is None for value in values)
        if missing:
            missing_count += 1
            divergent.append({"fact_id": fid, "values": values, "missing_runs": missing})
        elif len(set(values)) == 1:
            consistent += 1
        else:
            divergent.append({"fact_id": fid, "values": values})

    total = consistent + len(divergent)
    rate = (consistent / total) if total else 1.0
    return {
        "rate": round(rate, 4),
        "total_fields": total,
        "consistent_fields": consistent,
        "divergent_count": len(divergent),
        "missing_count": missing_count,
        "divergent": divergent[:20],  # 截断避免报告过大
    }


def _calculation_consistency(runs: list[dict]) -> dict:
    """计算结果跨 run 一致率。"""
    if len(runs) < 2:
        return {"rate": None, "total": 0, "consistent": 0, "divergent": []}

    calc_indices = [_index_calculations(m) for m in runs]
    all_calc_ids = set()
    for idx in calc_indices:
        all_calc_ids.update(idx.keys())

    consistent = 0
    missing_count = 0
    divergent = []
    for cid in sorted(all_calc_ids):
        outcomes = []
        for idx in calc_indices:
            if cid not in idx:
                outcomes.append(None)
                continue
            exp = idx[cid].get("expected") or {}
            outcomes.append(str(
                exp.get("output_sha256", exp.get("outcome", exp.get("replayed", "")))))
        missing = sum(outcome is None for outcome in outcomes)
        if missing:
            missing_count += 1
            divergent.append({"calculation_id": cid, "outcomes": outcomes,
                              "missing_runs": missing})
        elif len(set(outcomes)) == 1:
            consistent += 1
        else:
            divergent.append({"calculation_id": cid, "outcomes": outcomes})

    total = consistent + len(divergent)
    rate = (consistent / total) if total else 1.0
    return {
        "rate": round(rate, 4),
        "total": total,
        "consistent": consistent,
        "divergent_count": len(divergent),
        "missing_count": missing_count,
        "divergent": divergent[:20],
    }


def _claim_source_coverage(runs: list[dict]) -> dict:
    """claim→source 覆盖率：每个 run 中事实挂来源的比例。"""
    coverages = []
    for m in runs:
        facts = m.get("facts", [])
        if not facts:
            coverages.append(0.0)
            continue
        with_source = sum(1 for f in facts if f.get("source_ids"))
        coverages.append(with_source / len(facts))
    return {
        "per_run": [round(c, 4) for c in coverages],
        "min": round(min(coverages), 4) if coverages else None,
        "mean": round(statistics.mean(coverages), 4) if coverages else None,
    }


def _conclusion_drift(runs: list[dict]) -> dict:
    """核心结论漂移：对比各 run 的 judgments 集合。

    以 judgment_id 为键，对比 verdict 是否一致。
    """
    if len(runs) < 2:
        return {"drift_count": 0, "total_judgments": 0, "drifted": []}

    judgment_indices = []
    for m in runs:
        idx = {}
        for j in m.get("judgments", []):
            jid = j.get("judgment_id")
            if jid:
                idx[jid] = j.get("conclusion", j.get("verdict", ""))
        judgment_indices.append(idx)

    all_jids = set()
    for idx in judgment_indices:
        all_jids.update(idx.keys())

    drifted = []
    for jid in sorted(all_jids):
        conclusions = [idx.get(jid) for idx in judgment_indices]
        if any(value is None for value in conclusions) or len(set(conclusions)) > 1:
            drifted.append({"judgment_id": jid, "conclusions": conclusions,
                            "missing_runs": sum(value is None for value in conclusions)})

    return {
        "drift_count": len(drifted),
        "total_judgments": len(all_jids),
        "drifted": drifted[:20],
    }


def _evidence_count_variance(run_roots: list[Path]) -> dict:
    """各 skill 事实数量方差；这是覆盖规模指标，不冒充语义质量评分。"""
    scorecards = []
    for root in run_roots:
        sc = _load_json_safe(root / "evidence/quality-scorecard.json")
        if sc:
            scorecards.append(sc)
    if len(scorecards) < 2:
        return {"available": False, "reason": "不足 2 个 run 有 quality-scorecard"}

    # 对比 per_skill 的 fact_count
    skill_fact_counts: dict[str, list[int]] = {}
    for sc in scorecards:
        for ps in sc.get("evidence_sufficiency", {}).get("per_skill", []):
            sid = ps.get("skill_id", "?")
            skill_fact_counts.setdefault(sid, []).append(ps.get("fact_count", 0))

    variances = {}
    for sid, counts in skill_fact_counts.items():
        if len(counts) >= 2:
            variances[sid] = round(statistics.variance(counts), 4)

    mean_var = round(statistics.mean(variances.values()), 4) if variances else 0.0
    return {
        "available": True,
        "runs_with_scorecard": len(scorecards),
        "mean_variance": mean_var,
        "per_skill_variance": variances,
    }


def _cohort_issues(manifests: list[dict]) -> list[str]:
    issues = []
    identities = {
        "company.code": [m.get("company", {}).get("code") for m in manifests],
        "company.name": [m.get("company", {}).get("name") for m in manifests],
        "run.as_of": [m.get("run", {}).get("as_of") for m in manifests],
        "contract.registry_sha256": [
            m.get("contract", {}).get("registry_sha256") for m in manifests
        ],
    }
    for label, values in identities.items():
        if any(value in (None, "") for value in values):
            issues.append(f"{label} 缺失，无法确认可比性")
        elif len(set(values)) != 1:
            issues.append(f"{label} 不一致: {values}")
    statuses = [m.get("run", {}).get("status") for m in manifests]
    if any(status != "APPROVED" for status in statuses):
        issues.append(f"run.status 必须全部为 APPROVED: {statuses}")
    return issues


def _doctor_agreement(run_roots: list[Path]) -> dict:
    """doctor 裁决跨 run 一致率。"""
    verdicts = []
    for root in run_roots:
        dr = _load_json_safe(root / "evidence/doctor-report.json")
        if dr:
            verdicts.append(dr.get("verdict", "UNKNOWN"))
    if len(verdicts) < 2:
        return {"available": False, "reason": "不足 2 个 run 有 doctor-report"}
    unique = set(verdicts)
    return {
        "available": True,
        "verdicts": verdicts,
        "agreement": len(unique) == 1,
        "unique_verdicts": sorted(unique),
    }


def compare(run_roots: list[Path], output_dir: Path | None = None) -> tuple[dict, int]:
    """对比多个 run，产出稳定性报告。"""
    if len(run_roots) < 2:
        return {"error": "至少需要 2 个 run-root"}, 2

    manifests = []
    valid_roots = []
    for root in run_roots:
        try:
            m = load_manifest(root)
            manifests.append(m)
            valid_roots.append(root)
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            print(f"⚠️  跳过不可读 run {root}: {exc}", file=sys.stderr)

    if len(manifests) < 2:
        return {"error": "有效 run 不足 2 个"}, 2

    cohort_issues = _cohort_issues(manifests)
    if cohort_issues:
        report = {
            "stability_schema_version": STABILITY_SCHEMA_VERSION,
            "generated_at": _now_iso(),
            "runs_compared": len(manifests),
            "run_ids": [m.get("run", {}).get("run_id", "?") for m in manifests],
            "run_roots": [str(r) for r in valid_roots],
            "overall_verdict": "INCOMPARABLE",
            "issues": cohort_issues,
            "metrics": {},
        }
        if output_dir:
            _atomic_write_json(output_dir / "stability-report.json", report)
        return report, 2

    report = {
        "stability_schema_version": STABILITY_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "runs_compared": len(manifests),
        "run_ids": [m.get("run", {}).get("run_id", "?") for m in manifests],
        "run_roots": [str(r) for r in valid_roots],
        "metrics": {
            "fact_consistency": _fact_consistency(manifests),
            "calculation_consistency": _calculation_consistency(manifests),
            "claim_source_coverage": _claim_source_coverage(manifests),
            "conclusion_drift": _conclusion_drift(manifests),
            "evidence_count_variance": _evidence_count_variance(valid_roots),
            "doctor_agreement": _doctor_agreement(valid_roots),
        },
    }

    # 总体裁决
    fc = report["metrics"]["fact_consistency"]["rate"]
    cc = report["metrics"]["calculation_consistency"]["rate"]
    drift = report["metrics"]["conclusion_drift"]["drift_count"]
    coverage_min = report["metrics"]["claim_source_coverage"]["min"]

    issues = []
    if fc is not None and fc < 1.0:
        issues.append(f"事实一致率 {fc} < 100%")
    if cc is not None and cc < 1.0:
        issues.append(f"计算一致率 {cc} < 100%")
    if drift > 0:
        issues.append(f"结论漂移 {drift} 项")
    if coverage_min is not None and coverage_min < 1.0:
        issues.append(f"最低 claim→source 覆盖率 {coverage_min} < 100%")

    report["overall_verdict"] = "STABLE" if not issues else "UNSTABLE"
    report["issues"] = issues

    if output_dir:
        _atomic_write_json(output_dir / "stability-report.json", report)

    return report, (0 if report["overall_verdict"] == "STABLE" else 1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="全量分析重复运行稳定性基准（P3）")
    parser.add_argument("--run-roots", required=True, nargs="+",
                        help="2+ 个 run-root 路径（同一公司的多次运行）")
    parser.add_argument("--output-dir", default=None,
                        help="输出目录（默认写入第一个 run-root 的 evidence/benchmark/）")
    args = parser.parse_args(argv)

    run_roots = [Path(r) for r in args.run_roots]
    output_dir = Path(args.output_dir) if args.output_dir else run_roots[0] / "evidence/benchmark"

    report, code = compare(run_roots, output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
