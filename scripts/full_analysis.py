#!/usr/bin/env python3
"""全量分析公共 CLI：薄适配层，实际调度由 WorkBuddy Runtime 完成。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import full_analysis_gate as gate  # noqa: E402
import full_analysis_runtime as runtime  # noqa: E402
import full_analysis_audit as audit_tool  # noqa: E402


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False))


class PublicParser(argparse.ArgumentParser):
    """隐藏内部桥接命令，避免把 Runtime 协议误当成用户 API。"""

    def format_help(self):
        text = super().format_help()
        internal = ("next-work", "audit", "job-started", "heartbeat", "record-failure", "submit-result")
        text = text.replace("{start,status,resume,cleanup,next-work,audit,job-started,heartbeat,record-failure,submit-result}",
                            "{start,status,resume,cleanup}")
        text = "\n".join(line for line in text.splitlines() if not any(f"    {name} " in line for name in internal)) + "\n"
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
    for name in ("next-work", "audit"):
        cmd = sub.add_parser(name, help=argparse.SUPPRESS); cmd.add_argument("--run-root", required=True)
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
    submit = sub.add_parser("submit-result", help=argparse.SUPPRESS)
    submit.add_argument("--run-root", required=True); submit.add_argument("--registry", default=gate.DEFAULT_REGISTRY)
    submit.add_argument("--result", required=True)
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
        if args.command == "next-work": emit(runtime.next_work(root)); return 0
        if args.command == "audit":
            report, code = audit_tool.audit(root); emit(report); return code
        if args.command == "job-started": emit(runtime.job_started(root, args.work_unit_id, args.attempt_id, args.lease_nonce, args.agent_job_id)); return 0
        if args.command == "heartbeat": emit(runtime.heartbeat(root, args.work_unit_id, args.attempt_id, args.lease_nonce)); return 0
        if args.command == "record-failure": emit(runtime.record_failure(root, args.work_unit_id, args.attempt_id, args.reason)); return 0
        if args.command == "submit-result": emit(runtime.submit_result(root, Path(args.registry), Path(args.result))); return 0
        return 2
    except (gate.GateError, runtime.RuntimeErrorState) as exc:
        print(f"❌ {exc}")
        return getattr(exc, "code", 1)


if __name__ == "__main__":
    raise SystemExit(main())
