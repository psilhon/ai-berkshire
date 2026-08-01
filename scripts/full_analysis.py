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


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False))


class PublicParser(argparse.ArgumentParser):
    """隐藏内部桥接命令，避免把 Runtime 协议误当成用户 API。"""

    INTERNAL = ("next-work", "audit", "job-started", "heartbeat", "record-failure", "submit-result", "record-usage", "submit-correction", "rework")

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
    for name in ("status", "resume"):
        cmd = sub.add_parser(name, help=f"{name} 运行"); cmd.add_argument("--run-root", required=True)
    cleanup = sub.add_parser("cleanup", help="只读清理预览")
    cleanup.add_argument("--run-root", required=True); cleanup.add_argument("--dry-run", action="store_true")
    doc = sub.add_parser("doctor", help="执行完整性体检（advisory，非阻断）")
    doc.add_argument("--run-root", required=True); doc.add_argument("--registry", default=gate.DEFAULT_REGISTRY)
    doc.add_argument("--json", action="store_true"); doc.add_argument("--strict", action="store_true")
    doc.add_argument("--write", action="store_true")
    for name in ("next-work",):
        cmd = sub.add_parser(name, help=argparse.SUPPRESS); cmd.add_argument("--run-root", required=True)
        cmd.add_argument("--methodology-mode", default="full", choices=["full", "ref"])
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
        if args.command == "next-work": emit(runtime.next_work(root, methodology_mode=args.methodology_mode)); return 0
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
