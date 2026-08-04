#!/usr/bin/env python3
"""证据命令执行器（v3.4.15）——PASS 回执的唯一合法签发口。

# 为什么必须用它

在此之前，Agent 想给某个操作记一条 PASS 回执，只需在 result.json 里写下
`{"receipt_id": ..., "operation": "quote", "status": "PASS",
  "argv": [...], "output": "..."}`。argv 与 output 都是自述字符串，Gate 无法分辨
真假——跑一条无关命令、编一段输出，同样能进 DONE。

本执行器把「宣称执行过」变成「确实执行过」：它真实 subprocess 运行命令，捕获
退出码与合并输出，把输出落盘到 evidence/command-output/、计算 sha256、向
evidence/command-log.jsonl 追加留痕，最后用本 run 的密钥对回执做 HMAC 签名。
Agent 只需把打印出的回执**原样**放进 result.json 的 command_receipts 数组。

# 用法

    python3 scripts/run_evidence_command.py \
        --run-root <run_root> --receipt-id rcpt-ashare-data-quote \
        --operation quote -- python3 tools/ashare_data.py quote 000651.SZ

    # 追加到已有回执文件（Agent 通常这样批量攒回执）
    python3 scripts/run_evidence_command.py ... --append-to receipts.json

注意 `--` 之后的部分是**真实要执行的命令**，其中必须出现 operation 这个 token
（Gate 会校验 operation ∈ argv，防止用无关命令为某操作背书）。

# 退出码

    0  命令成功（exit_code==0），已签发 PASS 回执
    1  用法/环境错误（run_root 不存在、缺 --）
    2  命令执行失败（exit_code!=0），已签发 **FAIL** 回执（不签名，附 reason）

退出码 2 不是灾难：如实上报失败是合规行为。把 FAIL 回执照样放进 result.json，
Gate 只对 PASS 回执要求签名。若某操作在当前数据源上根本不可用，用 --unavailable
签发 UNAVAILABLE 回执。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from evidence_receipt import ReceiptError, execute_and_sign, load_journal  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_COMMAND_FAILED = 2


def _append_to(path: Path, receipt: dict) -> None:
    """把回执并入既有 JSON 数组文件（同 receipt_id 覆盖，便于重跑）。"""
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(existing, list):
            existing = []
    except Exception:
        existing = []
    existing = [r for r in existing
                if not (isinstance(r, dict)
                        and r.get("receipt_id") == receipt["receipt_id"])]
    existing.append(receipt)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    command: list[str] = []
    if "--" in raw:
        idx = raw.index("--")
        raw, command = raw[:idx], raw[idx + 1:]

    ap = argparse.ArgumentParser(
        description="真实执行证据命令并签发执行器回执",
        epilog="命令写在 -- 之后，例：... --operation quote -- python3 tools/ashare_data.py quote 000651.SZ")
    ap.add_argument("--run-root", required=True)
    ap.add_argument("--receipt-id", required=True)
    ap.add_argument("--operation", required=True)
    ap.add_argument("--timeout", type=int, default=900, help="秒，默认 900")
    ap.add_argument("--append-to", help="把回执并入该 JSON 数组文件")
    ap.add_argument("--unavailable", action="store_true",
                    help="不执行命令，直接签发 UNAVAILABLE 回执（需配 --reason）")
    ap.add_argument("--reason", help="UNAVAILABLE/FAIL 的原因说明")
    args = ap.parse_args(raw)

    run_root = Path(args.run_root).resolve()
    if not run_root.is_dir():
        print(f"❌ run_root 不存在: {run_root}", file=sys.stderr)
        return EXIT_USAGE

    # UNAVAILABLE：不宣称成功，无需执行、无需签名。
    if args.unavailable:
        if not (args.reason or "").strip():
            print("❌ --unavailable 必须配 --reason 说明为何不可用", file=sys.stderr)
            return EXIT_USAGE
        receipt = {"receipt_id": args.receipt_id, "operation": args.operation,
                   "status": "UNAVAILABLE", "reason": args.reason.strip()}
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        if args.append_to:
            _append_to(Path(args.append_to), receipt)
        return EXIT_OK

    if not command:
        print("❌ 缺少要执行的命令：请在 -- 之后给出完整命令", file=sys.stderr)
        return EXIT_USAGE

    try:
        receipt, exit_code = execute_and_sign(
            run_root, args.receipt_id, args.operation, command,
            timeout=args.timeout, reason=args.reason)
    except ReceiptError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return EXIT_USAGE

    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    if args.append_to:
        _append_to(Path(args.append_to), receipt)

    if exit_code != 0:
        print(f"⚠️  命令失败（exit_code={exit_code}），已签发 FAIL 回执。"
              f"如实提交即可，Gate 不要求 FAIL 回执带签名。", file=sys.stderr)
        return EXIT_COMMAND_FAILED

    # journal 自检：确保刚写的留痕可被 Gate 读回（早发现磁盘/编码异常）
    if (load_journal(run_root).get(args.receipt_id, {}).get("output_digest")
            != receipt["output_digest"]):
        print("⚠️  journal 回读校验失败，Gate 可能拒收该回执", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
