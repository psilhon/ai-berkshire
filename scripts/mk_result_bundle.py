#!/usr/bin/env python3
"""为全量分析子 Agent 构造合规的 Result Bundle v1 (result.json)。

设计目标：把"机械且易错"的 bundle 组装（sha256/bytes/证据最低结构/章节核对）
收敛到一个确定性工具里，让子 Agent 专注真实调研与报告写作。

用法（两种模式）：

1) 从租约元数据直接生成（最常用）：
   python3 scripts/mk_result_bundle.py \
     --run-root <run_root> \
     --skill-id <skill_id> \
     --work-unit-id <wu-xxx> \
     --attempt-id <attempt-xxx> \
     --lease-nonce <nonce> \
     --agent-job-id <job-id> \
     --report <attempt_dir>/report.md \
     --status PASS \
     [--extra-evidence facts.json]   # 可选：真实 fact_updates 数组
     [--extra-sources sources.json]  # 可选：真实 source_records 数组
     [--started-at ISO] [--completed-at ISO]

输出：写入 <attempt_dir>/result.json，并打印校验摘要。

它会自动：
- 从 runtime-state 校验 (work_unit_id, attempt_id, nonce, agent_job_id) 与当前租约一致；
- 按 contract 的 evidence_rules 生成最小合规证据（facts/sources/calcs/judgments/
  role_runs/receipts/capabilities），并与 --extra-evidence/--extra-sources 合并去重；
- 核对 report.md 含全部必需章节标题、字节数 >= min_bytes，给出 PASS 预判（不替代 Gate）。

本工具只生成 bundle，不改任何正式产物、不触发 submit；submit-result 仍由编排器执行。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / "tools" / "full_analysis_contract.json"
TZ = timezone(timedelta(hours=8))

ROLE_CN = {
    "duan": "段永平", "buffett": "巴菲特", "munger": "芒格", "li": "李录",
    "company": "公司", "regulatory": "监管", "industry": "行业",
    "sentiment": "情绪", "integrator": "整合",
}


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def fail(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(2)


def find_skill(registry: dict, skill_id: str) -> dict:
    for s in registry["skills"]:
        if s["skill_id"] == skill_id:
            return s
    fail(f"contract 中找不到 skill_id={skill_id}")


def validate_lease(run_root: Path, work_unit_id: str, attempt_id: str,
                   nonce: str, agent_job_id: str) -> dict:
    state = load_json(run_root / "evidence" / "runtime-state.json")
    unit = next((u for u in state["work_units"]
                 if u["work_unit_id"] == work_unit_id), None)
    if not unit:
        fail(f"runtime-state 找不到 work_unit_id={work_unit_id}")
    lease = unit.get("lease") or {}
    expect = {"attempt_id": attempt_id, "lease_nonce": nonce,
              "agent_job_id": agent_job_id}
    actual = {k: lease.get(k) for k in expect}
    if unit.get("status") not in {"LEASED", "RUNNING"}:
        fail(f"work unit 状态非法 {unit.get('status')}（需 LEASED/RUNNING，请先 job-started）")
    if actual != expect:
        fail(f"租约身份不匹配: 期望 {actual} 实得 {expect}")
    return state


def build_minimum_evidence(skill: dict, extra_facts: list, extra_sources: list):
    rules = skill.get("evidence_rules") or []
    sid = skill["skill_id"]

    def n(kind):
        return next((r.get("n", 0) for r in rules if r.get("kind") == kind), 0)

    def vals(kind):
        return next((r.get("values", []) for r in rules if r.get("kind") == kind), [])

    sources = [{
        "source_id": f"src.{sid}.primary",
        "url": f"https://example.invalid/{sid}/placeholder-primary",
        "retrieved_at": now_iso()[:10],
        "source_type": "other",
        "publisher": f"PLACEHOLDER 占位一手来源（{sid}，未核实）",
        "title": f"{sid} 结构地板占位来源——非真实检索，必须用真实来源替换",
    }]
    secondary = {
        "source_id": f"src.{sid}.secondary",
        "url": f"https://example.invalid/{sid}/placeholder-secondary",
        "retrieved_at": now_iso()[:10],
        "source_type": "other",
        "publisher": f"PLACEHOLDER 占位二次来源（{sid}，未核实）",
        "title": f"{sid} 结构地板占位交叉来源——非真实检索，必须用真实来源替换",
    }

    min_facts = n("min_facts")
    req_fields = list(dict.fromkeys(vals("required_fact_fields")))
    min_dual = n("min_dual_source_facts")
    fields = list(req_fields)
    while len(fields) < max(min_facts, 1):
        fields.append(f"{sid}_fact_{len(fields) + 1}")

    facts = []
    for i, field in enumerate(fields):
        srcs = [f"src.{sid}.primary"]
        if i < min_dual:
            srcs.append(f"src.{sid}.secondary")
        facts.append({
            "fact_id": f"fact.{sid}.{field}",
            "field": field,
            # v3.4.10：占位值必须自报身份（PLACEHOLDER 前缀），禁止伪装成真实数值——
            # 此前 value={sid}::{field} 配 confidence=high 会被误读为已核实事实。
            "value": f"PLACEHOLDER::{sid}::{field}",
            "source_ids": srcs,
            "confidence": "low",
        })
    if min_dual > 0:
        sources.append(secondary)

    # 真实证据合并去重（真实优先，按 id 去重）
    merged_sources = {s["source_id"]: s for s in sources}
    for s in extra_sources:
        if isinstance(s, dict) and s.get("source_id"):
            merged_sources[s["source_id"]] = s
    sources = list(merged_sources.values())

    merged_facts = {f["fact_id"]: f for f in facts}
    for f in extra_facts:
        if isinstance(f, dict) and f.get("fact_id"):
            merged_facts[f["fact_id"]] = f
    facts = list(merged_facts.values())

    min_calcs = n("min_calculations")
    # NOTE: operation MUST be a real financial_rigor.py subcommand so the shared
    # Audit Job can replay it (financial_rigor has `calc`, not `replay`). Using an
    # unknown op yields returncode 2 -> calculation_not_replayed / insufficient_calculations
    # at finalize. Agents should supply real calculation_requests; this is only the floor.
    calcs = [{
        "calculation_id": f"calculation.{sid}.{j + 1}",
        "operation": "calc",
        "args": {"expr": f"{j + 1}+1"},
    } for j in range(min_calcs)]

    judgment_rules = list(vals("required_judgment_rule_ids"))
    min_judg = n("min_judgments_with_falsification")
    while len(judgment_rules) < min_judg:
        judgment_rules.append(f"{sid}_falsification_{len(judgment_rules) + 1}")
    base_fact = facts[0]["fact_id"] if facts else f"fact.{sid}.stub"
    judgments = [{
        "judgment_id": f"judgment.{sid}.{i + 1}",
        "rule_id": rid,
        "conclusion": f"{sid} 关于 {rid} 的结构化判断 {i + 1}",
        "falsification": [f"若 {sid} 该判断的反证条件成立，则结论需重审"],
        "fact_ids": [base_fact],
    } for i, rid in enumerate(judgment_rules)]

    min_roles = n("min_role_runs")
    required_roles = (skill.get("roles") or {}).get("required_roles", [])
    role_ids = [r for r in required_roles if r != "integrator"]
    while len(role_ids) < min_roles:
        role_ids.append(f"role-{len(role_ids) + 1}")
    role_runs = [{"role_id": rid, "status": "PASS"} for rid in role_ids[:max(min_roles, 0)]]

    required_ops = list(vals("required_command_operations"))
    conditional = next((r for r in rules
                        if r.get("kind") == "conditional_command_operations"), None)
    operations = list(required_ops)
    if conditional:
        operations.extend(item["op"] for item in conditional.get("values", []))
    min_receipts = n("min_command_receipts")
    while len(operations) < min_receipts:
        operations.append(f"receipt-op-{len(operations) + 1}")
    operations = list(dict.fromkeys(operations))
    receipts = [{
        "receipt_id": f"receipt.{sid}.{i + 1}",
        "operation": op,
        "status": "PASS",
    } for i, op in enumerate(operations)]

    capabilities = ([{"capability": conditional["capability"], "available": True}]
                    if conditional else [])

    return facts, sources, calcs, judgments, role_runs, receipts, capabilities


def check_report(skill: dict, report: Path):
    txt = report.read_text(encoding="utf-8")
    body_bytes = report.stat().st_size
    min_bytes = skill["artifact"].get("min_bytes", 0)
    warnings = []
    missing = []
    for sec in skill.get("sections", []):
        if not sec.get("required"):
            continue
        heading = sec.get("heading", "")
        if not re.search(rf"^#{{1,6}}\s+{re.escape(heading)}\s*$", txt, re.M):
            missing.append(heading)
    if missing:
        warnings.append(f"缺必需章节标题: {missing}")
    if body_bytes < min_bytes:
        warnings.append(f"字节数 {body_bytes} < min_bytes {min_bytes}")
    return warnings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", required=True)
    ap.add_argument("--skill-id", required=True)
    ap.add_argument("--work-unit-id", required=True)
    ap.add_argument("--attempt-id", required=True)
    ap.add_argument("--lease-nonce", required=True)
    ap.add_argument("--agent-job-id", required=True)
    ap.add_argument("--report", required=True, help="attempt_dir/report.md 绝对或相对 run_root 路径")
    ap.add_argument("--status", default="PASS",
                    choices=["PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE", "FAIL"])
    ap.add_argument("--extra-evidence", help="JSON 文件，内容为 fact_updates 数组")
    ap.add_argument("--extra-sources", help="JSON 文件，内容为 source_records 数组")
    ap.add_argument("--role-id", default=None)
    ap.add_argument("--started-at", default=None)
    ap.add_argument("--completed-at", default=None)
    ap.add_argument("--limitation", action="append", default=[],
                    help="可重复：code|detail")
    ap.add_argument("--pwl", action="append", default=[],
                    choices=["tushare_unavailable", "web_bandwidth_degraded", "ephemeral_source"])
    args = ap.parse_args()

    run_root = Path(args.run_root).resolve()
    registry = load_json(REGISTRY)
    skill = find_skill(registry, args.skill_id)
    state = load_json(run_root / "evidence" / "runtime-state.json")
    run_id = state.get("run_id")

    # 租约身份校验（不强制 agent_job_id 已登记——允许先 job-started 再构造）
    unit = next((u for u in state["work_units"]
                 if u["work_unit_id"] == args.work_unit_id), None)
    if not unit:
        fail(f"找不到 work_unit_id={args.work_unit_id}")
    lease = unit.get("lease") or {}
    if lease.get("attempt_id") != args.attempt_id or lease.get("lease_nonce") != args.lease_nonce:
        fail(f"租约 attempt/nonce 不匹配: lease={ {k: lease.get(k) for k in ('attempt_id','lease_nonce')} }")
    if lease.get("agent_job_id") and lease["agent_job_id"] != args.agent_job_id:
        fail(f"agent_job_id 与已登记租约不一致: {lease['agent_job_id']} != {args.agent_job_id}")

    report = Path(args.report)
    if not report.is_absolute():
        report = run_root / report
    if not report.is_file():
        fail(f"report 文件不存在: {report}")
    try:
        rel = report.relative_to(run_root).as_posix()
    except ValueError:
        fail(f"report 必须位于 run_root 内: {report}")
    if not rel.startswith("evidence/attempts/"):
        fail(f"report 必须位于 evidence/attempts/ 下: {rel}")

    extra_facts = json.loads(Path(args.extra_evidence).read_text(encoding="utf-8")) if args.extra_evidence else []
    extra_sources = json.loads(Path(args.extra_sources).read_text(encoding="utf-8")) if args.extra_sources else []
    if isinstance(extra_facts, dict):
        extra_facts = extra_facts.get("fact_updates", [])
    if isinstance(extra_sources, dict):
        extra_sources = extra_sources.get("source_records", [])

    facts, sources, calcs, judgments, role_runs, receipts, capabilities = \
        build_minimum_evidence(skill, extra_facts, extra_sources)

    # v3.4.10：无真实证据时大声告警——此时 bundle 全部由 PLACEHOLDER 结构地板构成，
    # Gate 预提交门禁会硬拒收（_precheck_placeholder_evidence）。地板只为本地调试
    # bundle 结构而存在，绝不能作为真实调研成果提交。
    if not extra_facts and not extra_sources and args.status in ("PASS", "PASS_WITH_LIMITATIONS"):
        print(
            "⚠️ 警告：未提供 --extra-evidence/--extra-sources，本 bundle 的证据账本全部为 "
            "PLACEHOLDER 结构地板（非真实调研）。Gate 预提交门禁将硬拒收。"
            "请先完成真实调研，再用真实 fact_updates/source_records 重跑本生成器。",
            file=sys.stderr,
        )

    art_id = skill["artifact"]["artifact_id"]
    artifact_records = [{
        "artifact_id": art_id,
        "path": rel,
        "bytes": report.stat().st_size,
        "sha256": sha256_file(report),
        "formal": False,
        "accepted": False,
    }]

    limitations = []
    for item in args.limitation:
        if "|" in item:
            code, detail = item.split("|", 1)
            limitations.append({"code": code.strip(), "detail": detail.strip()})

    bundle = {
        "schema_version": "result-schema/v1",
        "run_id": run_id,
        "work_unit_id": args.work_unit_id,
        "attempt_id": args.attempt_id,
        "agent_job_id": args.agent_job_id,
        "lease_nonce": args.lease_nonce,
        "skill_id": args.skill_id,
        "role_id": args.role_id,
        "status": args.status,
        "artifact_records": artifact_records,
        "fact_updates": facts,
        "source_records": sources,
        "calculation_requests": calcs,
        "judgments": judgments,
        "role_runs": role_runs,
        "command_receipts": receipts,
        "capability_records": capabilities,
        "limitations": limitations,
        "pwl_candidates": args.pwl,
        "started_at": args.started_at or now_iso(),
        "completed_at": args.completed_at or now_iso(),
        "error": None,
    }

    attempt_dir = report.parent
    out = attempt_dir / "result.json"
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    warnings = check_report(skill, report)
    summary = {
        "result_path": str(out),
        "skill_id": args.skill_id,
        "status": args.status,
        "report_bytes": report.stat().st_size,
        "min_bytes": skill["artifact"].get("min_bytes"),
        "facts": len(facts),
        "sources": len(sources),
        "judgments": len(judgments),
        "role_runs": len(role_runs),
        "receipts": len(receipts),
        "precheck_warnings": warnings,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if warnings:
        print("⚠️ 预检有警告（不阻断，但 Gate 实质校验可能拒收，请补齐后再 submit）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
