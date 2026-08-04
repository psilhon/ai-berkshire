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
     --extra-evidence facts.json     # 真实 fact_updates 数组
     --extra-sources sources.json    # 真实 source_records 数组（与上者必须同时提供）
     [--extra-calculations calcs.json]  [--extra-judgments judg.json]
     [--extra-receipts rcpt.json]       [--extra-capabilities cap.json]
     [--started-at ISO] [--completed-at ISO]

输出：写入 <attempt_dir>/result.json，并打印校验摘要。

它会自动：
- 从 runtime-state 校验 (work_unit_id, attempt_id, nonce, agent_job_id) 与当前租约一致；
- 按 contract 的 evidence_rules 组装证据账本：真实输入优先，缺失部分补**带水印的
  结构地板**（facts/sources/calcs/judgments/receipts/capabilities）；
- 按 report 实际文件重算 bytes/sha256，核对必需章节标题与 min_bytes。

自证红线（v3.4.13）：本工具**不会为未发生的事签发成功证明**。
- 命令回执地板一律 status=UNAVAILABLE + PLACEHOLDER reason，绝不代签 PASS；
- 判断/计算地板带 PLACEHOLDER 水印；capability 地板一律 available=false；
- 真实调研成果必须经 --extra-* 传入，机械字段（sha256/bytes/id 规范）才由工具代劳。

退出码：0 ⟺ bundle 零占位、可提交；2 = 输入非法或单边真实证据；
3 = 全地板 bundle（调试用，Gate 仍会硬拒收）。

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

# 结构地板水印：确定性字符串，Gate `_precheck_placeholder_evidence` 按它硬拒收。
# 生成器与 Gate 必须同口径，任何新增的地板字段都要带上它。
PLACEHOLDER = "PLACEHOLDER"

# 退出码语义（v3.4.13）：0 ⟺ bundle 零占位、可提交；非 0 一律显式暴露问题。
EXIT_OK = 0
EXIT_INVALID = 2          # 输入非法/租约不符/单边真实证据
EXIT_PLACEHOLDER = 3      # 全地板 bundle（仅调试用，须显式 --allow-placeholder-floor 才降级为 0）

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


def build_evidence_ledger(skill: dict, extra_facts: list, extra_sources: list,
                          extra_calcs: list | None = None,
                          extra_judgments: list | None = None,
                          extra_receipts: list | None = None,
                          extra_capabilities: list | None = None):
    """按 contract 组装证据账本：真实输入优先，缺失部分补**带水印的结构地板**。

    命名说明（v3.4.13）：旧名 build_minimum_evidence 具误导性——一旦任一类真实
    输入存在，产出就不再是"minimum"，而是"真实优先 + 地板补齐"。故更名。

    自证红线（v3.4.13 P0）：生成器**不得为未发生的事签发成功证明**。
    地板条目一律带 PLACEHOLDER 水印，且回执状态为 UNAVAILABLE（绝不伪造 PASS），
    capability 一律 available=false（未验证即不可用）。Gate 的占位预检会硬拒收，
    使"未做真实调研的 bundle 能被接受为 DONE"这条路径在机器层面不可达。

    role_runs 例外且无需水印：Gate 不信任 bundle 里的 role_runs，而是从磁盘
    role-<role>.md 备忘录独立校验后派生（verified_by_gate=True，见 gate 1000-1030），
    伪造它不产生信任效果。
    """
    extra_calcs = extra_calcs or []
    extra_judgments = extra_judgments or []
    extra_receipts = extra_receipts or []
    extra_capabilities = extra_capabilities or []
    rules = skill.get("evidence_rules") or []
    sid = skill["skill_id"]

    def n(kind):
        return next((r.get("n", 0) for r in rules if r.get("kind") == kind), 0)

    def vals(kind):
        return next((r.get("values", []) for r in rules if r.get("kind") == kind), [])

    # ---- 来源：提供真实来源时只用真实来源；否则给带水印的结构地板（Gate 会拒收）----
    if extra_sources:
        sources = [dict(s) for s in extra_sources]
    else:
        sources = [{
            "source_id": f"src-{sid}-primary",
            "url": f"https://example.invalid/{sid}/placeholder-primary",
            "retrieved_at": now_iso()[:10],
            "source_type": "other",
            "publisher": f"PLACEHOLDER 占位一手来源（{sid}，未核实）",
            "title": f"{sid} 结构地板占位来源——非真实检索，必须用真实来源替换",
        }]
        min_dual = n("min_dual_source_facts")
        if min_dual > 0:
            sources.append({
                "source_id": f"src-{sid}-secondary",
                "url": f"https://example.invalid/{sid}/placeholder-secondary",
                "retrieved_at": now_iso()[:10],
                "source_type": "other",
                "publisher": f"PLACEHOLDER 占位二次来源（{sid}，未核实）",
                "title": f"{sid} 结构地板占位交叉来源——非真实检索，必须用真实来源替换",
            })

    # ---- 事实：提供真实事实时只用真实事实；否则给带水印的结构地板（Gate 会拒收）----
    # 关键修复（v3.4.12）：真实证据与占位地板不再按 id 合并共存——一旦提供真实证据，
    # 必须零占位残留。此前占位始终生成并与真实证据并列，导致「提供 3 条真实事实仍残留
    # 3 条占位事实 + 1 条占位来源」，Gate 占位预检直接拒收整包。
    if extra_facts:
        facts = [dict(f) for f in extra_facts]
    else:
        min_facts = n("min_facts")
        req_fields = list(dict.fromkeys(vals("required_fact_fields")))
        min_dual = n("min_dual_source_facts")
        fields = list(req_fields)
        while len(fields) < max(min_facts, 1):
            fields.append(f"{sid}_fact_{len(fields) + 1}")
        facts = []
        for i, field in enumerate(fields):
            srcs = [f"src-{sid}-primary"]
            if i < min_dual:
                srcs.append(f"src-{sid}-secondary")
            facts.append({
                "fact_id": f"fact-{sid}-{field}",
                "field": field,
                # v3.4.10：占位值必须自报身份（PLACEHOLDER 前缀），禁止伪装成真实数值——
                # 此前 value={sid}::{field} 配 confidence=high 会被误读为已核实事实。
                "value": f"PLACEHOLDER::{sid}::{field}",
                "source_ids": srcs,
                "confidence": "low",
            })

    # ---- 计算：真实优先；地板 calculation_id 带水印（operation 仍须是 financial_rigor
    # 真子命令 `calc`，否则 audit 重放 returncode=2 → calculation_not_replayed）----
    if extra_calcs:
        calcs = [dict(c) for c in extra_calcs]
    else:
        min_calcs = n("min_calculations")
        calcs = [{
            "calculation_id": f"calculation-{sid}-{PLACEHOLDER}-{j + 1}",
            "operation": "calc",
            "args": {"expr": f"{j + 1}+1"},
        } for j in range(min_calcs)]

    # ---- 判断：真实优先；地板 conclusion 自报占位，禁止伪装成已完成的分析结论 ----
    if extra_judgments:
        judgments = [dict(j) for j in extra_judgments]
    else:
        judgment_rules = list(vals("required_judgment_rule_ids"))
        min_judg = n("min_judgments_with_falsification")
        while len(judgment_rules) < min_judg:
            judgment_rules.append(f"{sid}_falsification_{len(judgment_rules) + 1}")
        base_fact = facts[0]["fact_id"] if facts else f"fact-{sid}-stub"
        judgments = [{
            "judgment_id": f"judgment-{sid}-{PLACEHOLDER}-{i + 1}",
            "rule_id": rid,
            "conclusion": f"{PLACEHOLDER}::未作出真实判断——{sid} / {rid} 的结构地板占位",
            "falsification": [f"{PLACEHOLDER}::未给出真实反证条件，必须由 Agent 替换"],
            "fact_ids": [base_fact],
        } for i, rid in enumerate(judgment_rules)]

    min_roles = n("min_role_runs")
    required_roles = (skill.get("roles") or {}).get("required_roles", [])
    role_ids = [r for r in required_roles if r != "integrator"]
    while len(role_ids) < min_roles:
        role_ids.append(f"role-{len(role_ids) + 1}")
    role_runs = [{"role_id": rid, "status": "PASS"} for rid in role_ids[:max(min_roles, 0)]]

    conditional = next((r for r in rules
                        if r.get("kind") == "conditional_command_operations"), None)

    # ---- 回执：真实优先。地板**绝不伪造 PASS**——status=UNAVAILABLE + 水印 reason。
    # 这是 v3.4.13 P0 的核心：此前地板为 ashare-data 一口气签发 51 条 status=PASS
    # 的"命令已成功执行"回执，而实际一条命令都没跑，Gate 却接受为 DONE。----
    if extra_receipts:
        receipts = [dict(r) for r in extra_receipts]
    else:
        required_ops = list(vals("required_command_operations"))
        operations = list(required_ops)
        if conditional:
            operations.extend(item["op"] for item in conditional.get("values", []))
        min_receipts = n("min_command_receipts")
        while len(operations) < min_receipts:
            operations.append(f"receipt-op-{len(operations) + 1}")
        operations = list(dict.fromkeys(operations))
        receipts = [{
            "receipt_id": f"rcpt-{sid}-{PLACEHOLDER}-{i + 1}",
            "operation": op,
            "status": "UNAVAILABLE",
            "reason": f"{PLACEHOLDER}::命令未实际执行，生成器结构地板不得充当成功回执",
        } for i, op in enumerate(operations)]

    # ---- 能力：真实优先；地板一律 available=false（未验证即不可用，不得默认自称可用）。
    # schema 禁止额外字段，故此处以 false 本身承担"未验证"语义。----
    if extra_capabilities:
        capabilities = [dict(c) for c in extra_capabilities]
    else:
        capabilities = ([{"capability": conditional["capability"], "available": False}]
                        if conditional else [])

    return facts, sources, calcs, judgments, role_runs, receipts, capabilities


def placeholder_offenders(bundle: dict) -> list:
    """返回 bundle 中所有带 PLACEHOLDER 水印的证据条目描述（空=零占位）。

    与 Gate 的 `_precheck_placeholder_evidence` 同口径，用于在**提交前**就把
    "含占位的 bundle"暴露为非零退出，而不是等到 Gate 才拒收。
    """
    hits = []
    for fact in bundle.get("fact_updates") or []:
        if PLACEHOLDER in str(fact.get("value", "")):
            hits.append(f"fact {fact.get('fact_id')}")
    for src in bundle.get("source_records") or []:
        if PLACEHOLDER in f"{src.get('publisher', '')}{src.get('title', '')}":
            hits.append(f"source {src.get('source_id')}")
    for calc in bundle.get("calculation_requests") or []:
        if PLACEHOLDER in str(calc.get("calculation_id", "")):
            hits.append(f"calculation {calc.get('calculation_id')}")
    for judgment in bundle.get("judgments") or []:
        if PLACEHOLDER in f"{judgment.get('judgment_id', '')}{judgment.get('conclusion', '')}":
            hits.append(f"judgment {judgment.get('judgment_id')}")
    for rcpt in bundle.get("command_receipts") or []:
        blob = f"{rcpt.get('receipt_id', '')}{rcpt.get('reason', '')}{rcpt.get('detail', '')}"
        if PLACEHOLDER in blob:
            hits.append(f"receipt {rcpt.get('receipt_id')}")
    return hits


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
    ap.add_argument("--extra-calculations",
                    help="JSON 文件，内容为 calculation_requests 数组（真实验算参数）")
    ap.add_argument("--extra-judgments",
                    help="JSON 文件，内容为 judgments 数组（真实判断+反证条件）")
    ap.add_argument("--extra-receipts",
                    help="JSON 文件，内容为 command_receipts 数组（真实命令回执；"
                         "未提供时地板只发 UNAVAILABLE，绝不代签 PASS）")
    ap.add_argument("--extra-capabilities",
                    help="JSON 文件，内容为 capability_records 数组（真实能力探测结果；"
                         "未提供时地板一律 available=false）")
    ap.add_argument("--allow-placeholder-floor", action="store_true",
                    help="仅调试：允许全 PLACEHOLDER 地板 bundle 以退出码 0 返回。"
                         "生产禁用——Gate 仍会硬拒收。")
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

    # run_root 已 .resolve()（macOS 上 /var → /private/var）。report 必须做同样的
    # 符号链接解析，否则 /var/... 形式的合法路径会在 relative_to 处被误判为
    # "不在 run_root 内"（v3.4.13：macOS 临时目录下必现）。
    report = Path(args.report)
    if not report.is_absolute():
        report = run_root / report
    report = report.resolve()
    if not report.is_file():
        fail(f"report 文件不存在: {report}")
    try:
        rel = report.relative_to(run_root).as_posix()
    except ValueError:
        fail(f"report 必须位于 run_root 内: {report}（run_root={run_root}）")
    if not rel.startswith("evidence/attempts/"):
        fail(f"report 必须位于 evidence/attempts/ 下: {rel}")

    def load_extra(flag_value: str | None, wrapper_key: str) -> list:
        if not flag_value:
            return []
        data = json.loads(Path(flag_value).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get(wrapper_key, [])
        return data

    extra_facts = load_extra(args.extra_evidence, "fact_updates")
    extra_sources = load_extra(args.extra_sources, "source_records")
    extra_calcs = load_extra(args.extra_calculations, "calculation_requests")
    extra_judgments = load_extra(args.extra_judgments, "judgments")
    extra_receipts = load_extra(args.extra_receipts, "command_receipts")
    extra_caps = load_extra(args.extra_capabilities, "capability_records")

    # 单边真实证据必须显式失败（v3.4.13 P1）：只传 facts 或只传 sources 时，
    # 另一类会退化成 PLACEHOLDER 地板并与真实证据混排，Gate 必然拒收整包，
    # 而生成器此前仍返回 0 —— 静默产出不可提交的 bundle。事实与来源互为引用
    # （fact.source_ids 指向 source_records），二者必须同真同假。
    if bool(extra_facts) != bool(extra_sources):
        got, lack = (("--extra-evidence", "--extra-sources") if extra_facts
                     else ("--extra-sources", "--extra-evidence"))
        fail(f"单边真实证据不被接受：已提供 {got} 但缺 {lack}。"
             f"fact.source_ids 必须指向真实 source_records，只补一边会让另一边退化为 "
             f"{PLACEHOLDER} 地板并被 Gate 拒收整包。请同时提供两者。")

    facts, sources, calcs, judgments, role_runs, receipts, capabilities = \
        build_evidence_ledger(skill, extra_facts, extra_sources,
                              extra_calcs, extra_judgments, extra_receipts, extra_caps)

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
    offenders = placeholder_offenders(bundle)
    summary = {
        "result_path": str(out),
        "skill_id": args.skill_id,
        "status": args.status,
        "report_bytes": report.stat().st_size,
        "min_bytes": skill["artifact"].get("min_bytes"),
        "facts": len(facts),
        "sources": len(sources),
        "calculations": len(calcs),
        "judgments": len(judgments),
        "role_runs": len(role_runs),
        "receipts": len(receipts),
        "placeholder_entries": len(offenders),
        "submittable": not offenders,
        "precheck_warnings": warnings,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if warnings:
        print("⚠️ 预检有警告（不阻断，但 Gate 实质校验可能拒收，请补齐后再 submit）", file=sys.stderr)

    # 不变量（v3.4.13）：退出码 0 ⟺ bundle 零占位、可提交。
    # 此前"全地板 bundle"也返回 0，使"未做调研仍拿到成功信号"成为可能。
    if offenders:
        print(
            f"❌ 本 bundle 含 {len(offenders)} 条 {PLACEHOLDER} 结构地板证据"
            f"（非真实调研）：{offenders[:8]}{' …' if len(offenders) > 8 else ''}\n"
            f"   地板只用于本地调 bundle 结构；Gate 预提交门禁会硬拒收，禁止 submit。\n"
            f"   请补齐真实证据后重跑："
            f"--extra-evidence/--extra-sources/--extra-calculations/"
            f"--extra-judgments/--extra-receipts。",
            file=sys.stderr,
        )
        if not args.allow_placeholder_floor:
            return EXIT_PLACEHOLDER
        print("（--allow-placeholder-floor 已启用：仅降级退出码，Gate 仍会拒收）",
              file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
