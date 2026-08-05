#!/usr/bin/env python3
"""全量分析公共 CLI：薄适配层，实际调度由 WorkBuddy Runtime 完成。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import full_analysis_gate as gate  # noqa: E402
import full_analysis_runtime as runtime  # noqa: E402
import full_analysis_audit as audit_tool  # noqa: E402
import full_analysis_doctor as doctor  # noqa: E402
import full_analysis_review as review  # noqa: E402
import full_analysis_benchmark as benchmark  # noqa: E402
import full_analysis_cache as cache  # noqa: E402


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False))


class PublicParser(argparse.ArgumentParser):
    """隐藏内部桥接命令，避免把 Runtime 协议误当成用户 API。"""

    INTERNAL = ("next-work", "audit", "job-started", "heartbeat", "record-failure", "submit-result", "record-usage", "submit-correction", "rework", "mark-failed")

    def format_help(self):
        text = super().format_help()
        # 从 positional choices 行中移除内部命令（不依赖注册顺序）
        def _strip_internal(m: re.Match) -> str:
            names = [n.strip() for n in m.group(1).split(",") if n.strip() not in self.INTERNAL]
            return "{" + ",".join(names) + "}"
        text = re.sub(r"\{([^}]+)\}", _strip_internal, text)
        # 移除内部命令的帮助行
        text = "\n".join(line for line in text.splitlines()
                         if not any(f"    {name} " in line for name in self.INTERNAL)) + "\n"
        return text


def parser() -> argparse.ArgumentParser:
    p = PublicParser(description="WorkBuddy 全量公司分析 Runtime")
    sub = p.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="启动单公司运行")
    start.add_argument("--registry", default=gate.DEFAULT_REGISTRY)
    start.add_argument("--repo-root", default=Path.cwd())
    start.add_argument("--company", required=True)
    start.add_argument("--code", required=True)
    start.add_argument("--as-of", required=True)
    start.add_argument("--run-root")
    # v3.4.10：E1 机器门禁三态（fail-close）——stale=True（HEAD 落后最新 tag）与
    # stale=None（无 git 环境 / 无任何 tag / git 命令异常，不可判定）均拒绝启动；
    # 仅 stale=False（HEAD 为最新 tag 或领先）放行。显式 --allow-stale 是唯一覆盖路径。
    start.add_argument("--allow-stale", action="store_true",
                       help="跳过 E1 版本门禁（HEAD 落后最新 tag，或无 git/tag/命令异常导致不可判定时仍启动；"
                            "仅在人工确认目标版本无误后使用）")
    for name in ("status", "resume"):
        cmd = sub.add_parser(name, help=f"{name} 运行"); cmd.add_argument("--run-root", required=True)
    cleanup = sub.add_parser("cleanup", help="只读清理预览")
    cleanup.add_argument("--run-root", required=True); cleanup.add_argument("--dry-run", action="store_true")
    doc = sub.add_parser("doctor", help="执行完整性体检（advisory，非阻断）")
    doc.add_argument("--run-root", required=True); doc.add_argument("--registry", default=gate.DEFAULT_REGISTRY)
    doc.add_argument("--json", action="store_true"); doc.add_argument("--strict", action="store_true")
    doc.add_argument("--write", action="store_true")
    # v3.4.2 fix（MEDIUM）：CHECKPOINT 可执行闭环
    badj = sub.add_parser("budget-adjust", help="调高派发预算（budget 触顶 CHECKPOINT 用，只允许上调）")
    badj.add_argument("--run-root", required=True)
    badj.add_argument("--stop-dispatch-at", type=int, default=None, help="新的 stop_dispatch_at（仅上调）")
    badj.add_argument("--hard-max", type=int, default=None, help="新的 hard_max（仅上调）")
    badj.add_argument("--reason", default="", help="调整原因（写入 events.jsonl 可追溯）")
    elog = sub.add_parser("event-log", help="人工事件写入（doctor CHECKPOINT 复核结论用）")
    elog.add_argument("--run-root", required=True)
    elog.add_argument("--kind", required=True, choices=["human_review", "manual_rework", "doctor_checkpoint"])
    elog.add_argument("--note", default="", help="复核结论/说明文本")
    for name in ("next-work",):
        cmd = sub.add_parser(name, help=argparse.SUPPRESS); cmd.add_argument("--run-root", required=True)
        cmd.add_argument("--methodology-mode", default="full", choices=["full", "ref"])
        # v3.4.2 fix：W3 错峰支持——只从白名单 skill 中派发（逗号分隔 skill_id 列表）
        cmd.add_argument("--allowlist", default=None,
                         help="逗号分隔的 skill_id 白名单；非空时只派发白名单内的就绪单元（W3 错峰用）")
    # lean 模式（v3.7）：移除租约 watchdog（sweep）——失败由编排器显式 mark-failed 判定并声明，
    # 不再依赖常驻进程自动回收。保留 mark-failed 作为失败声明的唯一入口。
    mf = sub.add_parser("mark-failed", help=argparse.SUPPRESS)
    mf.add_argument("--run-root", required=True)
    mf.add_argument("--skill-id", required=True)
    mf.add_argument("--reason", required=True)
    mf.add_argument("--retry", action="store_true", help="不置 FAILED，改为重新置 PENDING 供重派")
    aud = sub.add_parser("audit", help=argparse.SUPPRESS)
    aud.add_argument("--run-root", required=True); aud.add_argument("--registry", default=gate.DEFAULT_REGISTRY)
    fin = sub.add_parser("finalize", help=argparse.SUPPRESS)
    fin.add_argument("--run-root", required=True); fin.add_argument("--registry", default=gate.DEFAULT_REGISTRY)
    started = sub.add_parser("job-started", help=argparse.SUPPRESS)
    started.add_argument("--run-root", required=True); started.add_argument("--work-unit-id", required=True)
    started.add_argument("--attempt-id", required=True); started.add_argument("--lease-nonce", required=True)
    started.add_argument("--agent-job-id", required=True)
    beat = sub.add_parser("heartbeat", help=argparse.SUPPRESS)
    beat.add_argument("--run-root", required=True); beat.add_argument("--work-unit-id", required=True)
    beat.add_argument("--attempt-id", required=True); beat.add_argument("--lease-nonce", required=True)
    fail = sub.add_parser("record-failure", help=argparse.SUPPRESS)
    fail.add_argument("--run-root", required=True); fail.add_argument("--work-unit-id", required=True)
    fail.add_argument("--attempt-id", required=True); fail.add_argument("--reason", required=True)
    usage = sub.add_parser("record-usage", help=argparse.SUPPRESS)
    usage.add_argument("--run-root", required=True)
    usage.add_argument("--phase", required=True, choices=["work", "summary", "review"])
    usage.add_argument("--attempt-id", required=True)
    usage.add_argument("--skill-id", required=True)
    usage.add_argument("--input-tokens", type=int, default=None)
    usage.add_argument("--output-tokens", type=int, default=None)
    usage.add_argument("--input-bytes", type=int, default=None)
    usage.add_argument("--output-bytes", type=int, default=None)
    usage.add_argument("--duration-ms", type=int, default=None)
    usage.add_argument("--cache-hit", action="store_true")
    corr = sub.add_parser("submit-correction", help=argparse.SUPPRESS)
    corr.add_argument("--run-root", required=True); corr.add_argument("--registry", default=gate.DEFAULT_REGISTRY)
    corr.add_argument("--correction", required=True)
    rw = sub.add_parser("rework", help=argparse.SUPPRESS)
    rw.add_argument("--run-root", required=True); rw.add_argument("--work-unit-id", required=True)
    rw.add_argument("--reason", default="")
    submit = sub.add_parser("submit-result", help=argparse.SUPPRESS)
    submit.add_argument("--run-root", required=True); submit.add_argument("--registry", default=gate.DEFAULT_REGISTRY)
    submit.add_argument("--result", required=True)
    summary = sub.add_parser("register-summary", help="登记并冻结最终总结报告")
    summary.add_argument("--run-root", required=True)
    summary.add_argument("--registry", default=gate.DEFAULT_REGISTRY)
    summary.add_argument("--summary", required=True)
    rh = sub.add_parser("render-html", help="确定性渲染 HTML 展示件（register-summary 后立即执行，非阻断）")
    rh.add_argument("--run-root", required=True)
    rh.add_argument("--registry", default=gate.DEFAULT_REGISTRY)
    # P2 语义评审层
    rev = sub.add_parser("review", help="语义评审（prepare/ingest/summarize）")
    rev_sub = rev.add_subparsers(dest="review_command", required=True)
    rev_prep = rev_sub.add_parser("prepare", help=argparse.SUPPRESS)
    rev_prep.add_argument("--run-root", required=True); rev_prep.add_argument("--registry", default=gate.DEFAULT_REGISTRY)
    rev_prep.add_argument("--scope", default=None)
    rev_prep.add_argument("--payload-mode", default="compact", choices=["compact", "full"])
    rev_ing = rev_sub.add_parser("ingest", help=argparse.SUPPRESS)
    rev_ing.add_argument("--run-root", required=True); rev_ing.add_argument("--review", required=True)
    rev_sum = rev_sub.add_parser("summarize", help=argparse.SUPPRESS)
    rev_sum.add_argument("--run-root", required=True)
    rev_fix = rev_sub.add_parser("fix-list", help="导出评审 finding 源头修复清单（E13）")
    rev_fix.add_argument("--run-root", required=True)
    rev_fix.add_argument("--severity", default=None, choices=["high", "medium", "low"])
    rev_fix.add_argument("--out", default=None)
    # P3 重复运行稳定性基准
    bench = sub.add_parser("benchmark", help="重复运行稳定性基准（对比 2+ 个 run）")
    bench.add_argument("--run-roots", required=True, nargs="+")
    bench.add_argument("--output-dir", default=None)
    cl = sub.add_parser("cache-lookup", help=argparse.SUPPRESS)
    cl.add_argument("--run-root", required=True); cl.add_argument("--skill-id", required=True)
    cl.add_argument("--registry", default=gate.DEFAULT_REGISTRY)
    cs = sub.add_parser("cache-store", help=argparse.SUPPRESS)
    cs.add_argument("--run-root", required=True); cs.add_argument("--registry", default=gate.DEFAULT_REGISTRY)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "start":
            args.platform = "workbuddy"
            root = Path(args.run_root) if args.run_root else gate.build_run_root(Path(args.repo_root), args.code, args.company)
            args.run_root = str(root)
            gate.cmd_init(args)
            state = runtime.initialize(root)
            emit({"status": "STARTED", "run_root": str(root), "budget": state["budget"]})
            return 0
        if args.command == "benchmark":
            run_roots = [Path(r) for r in args.run_roots]
            output_dir = Path(args.output_dir) if args.output_dir else run_roots[0] / "evidence/benchmark"
            report, code = benchmark.compare(run_roots, output_dir)
            emit(report)
            return code
        root = Path(args.run_root)
        if args.command == "status": emit(runtime.load_state(root)); return 0
        if args.command == "resume": emit(runtime.resume(root)); return 0
        if args.command == "cleanup":
            if not args.dry_run:
                raise runtime.RuntimeErrorState("cleanup 仅支持 --dry-run；删除须由用户逐项授权")
            state = runtime.load_state(root)
            emit({"status": "DRY_RUN", "run_root": str(root), "removable_attempts": [
                str(root / "evidence/attempts" / u["skill_id"] / (u.get("lease") or {}).get("attempt_id", ""))
                for u in state["work_units"] if u.get("status") in {"DONE", "FAILED"}
            ]})
            return 0
        if args.command == "doctor":
            return doctor.run_and_render(root, Path(args.registry),
                                         as_json=args.json, write=args.write, strict=args.strict)
        if args.command == "budget-adjust":
            emit(runtime.budget_adjust(root, stop_dispatch_at=args.stop_dispatch_at,
                                       hard_max=args.hard_max, reason=args.reason)); return 0
        if args.command == "event-log":
            emit(runtime.log_event(root, kind=args.kind, note=args.note)); return 0
        if args.command == "next-work":
            allowlist = tuple(s.strip() for s in args.allowlist.split(",")) if args.allowlist else None
            emit(runtime.next_work(root, methodology_mode=args.methodology_mode, allowlist=allowlist)); return 0
        if args.command == "mark-failed":
            emit(runtime.mark_failed(root, args.skill_id, args.reason, retry=args.retry)); return 0
        if args.command == "audit":
            report, code = audit_tool.audit(root, Path(args.registry)); emit(report); return code
        if args.command == "job-started": emit(runtime.job_started(root, args.work_unit_id, args.attempt_id, args.lease_nonce, args.agent_job_id)); return 0
        if args.command == "heartbeat": emit(runtime.heartbeat(root, args.work_unit_id, args.attempt_id, args.lease_nonce)); return 0
        if args.command == "record-failure": emit(runtime.record_failure(root, args.work_unit_id, args.attempt_id, args.reason)); return 0
        if args.command == "record-usage":
            emit(runtime.record_usage(
                root, phase=args.phase, attempt_id=args.attempt_id, skill_id=args.skill_id,
                input_tokens=args.input_tokens, output_tokens=args.output_tokens,
                input_bytes=args.input_bytes, output_bytes=args.output_bytes,
                duration_ms=args.duration_ms, cache_hit=args.cache_hit))
            return 0
        if args.command == "submit-result": emit(runtime.submit_result(root, Path(args.registry), Path(args.result))); return 0
        if args.command == "submit-correction": return gate.cmd_submit_correction(args)
        if args.command == "rework": emit(runtime.rework(root, args.work_unit_id, args.reason)); return 0
        if args.command == "register-summary": return gate.cmd_register_summary(args)
        if args.command == "render-html": return gate.cmd_render_html(args)
        if args.command == "finalize": return gate.cmd_finalize(args)
        if args.command == "cache-lookup":
            manifest = gate.load_manifest(root)
            emit(cache.lookup(root, manifest, gate.load_registry(Path(args.registry)), args.skill_id))
            return 0
        if args.command == "cache-store":
            manifest = gate.load_manifest(root)
            emit(cache.store_approved(root, manifest, gate.load_registry(Path(args.registry))))
            return 0
        if args.command == "review":
            if args.review_command == "prepare":
                return review.cmd_prepare(args)
            if args.review_command == "ingest":
                return review.cmd_ingest(args)
            if args.review_command == "summarize":
                return review.cmd_summarize(args)
            if args.review_command == "fix-list":
                return review.cmd_fix_list(args)
            return 2
        return 2
    except (gate.GateError, runtime.RuntimeErrorState) as exc:
        print(f"❌ {exc}")
        return getattr(exc, "code", 1)


if __name__ == "__main__":
    raise SystemExit(main())
