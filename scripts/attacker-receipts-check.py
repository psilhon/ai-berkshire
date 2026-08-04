#!/usr/bin/env python3
"""v3.4.15 回执防线 · 攻击者视角验证（已挂 check.sh，自维护防线）。

背景：实现者自写的测试（含故障注入）只能证明"已知攻击面"，不能证明"收敛"。
本脚本以攻击者视角，枚举 v3.4.10→v3.4.15 已知的全部绕过手法，逐一打 Gate 的
**单一准入谓词 admit_bundle**，观察每条是变红（防御有效）还是变绿（可绕过）。

定位与边界（必须如实理解，勿拔高）：
- 本脚本由实现者编写，已挂进 scripts/check.sh 成为持续防线；挂入后它退化为
  **自维护测试**（与 tests/ 同性质），不再有"第二方"独立性。收敛与否仍是经验
  命题，须由用户 review + 真实 run 裁决，本脚本不宣称收敛。
- 枚举只能覆盖已知攻击面，不能证明没有第 N+1 种绕过。
- **假红教训（重要）**：脚本第一版因前置字段非法（calculation_id 格式 /
  lease_nonce 写死）导致所有攻击被**无关校验短路**成假红——若当时停手就会把
  "calc 格式拦的"当成"回执防线拦的"报出。因此本脚本的每条判定都打印拒收理由，
  且依赖人工/审查确认理由归属正确防线；任何人改动本脚本后必须重跑并核对理由。

结果语义：
    ❌ 意外可绕过   = 威胁模型未声明、理论应拒却放行 → 真缺陷（exit 1）
    ℹ️ 威胁模型边界 = v3.4.15 文档明确声明的可绕过点（删密钥降级 legacy、
                     读密钥伪造全套签名+journal+落盘）→ 如实暴露，不算新缺陷
判定标准：预期拒收→实际拒收 = 防御有效；预期拒收→实际放行 = 意外可绕过（exit 1）。

用法：python3 scripts/attacker-receipts-check.py
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "full_analysis.py"
GATE = REPO / "tools" / "full_analysis_gate.py"
REGISTRY = REPO / "tools" / "full_analysis_contract.json"

sys.path.insert(0, str(REPO / "tools"))
import full_analysis_gate as gate  # noqa: E402
import evidence_receipt as er  # noqa: E402

BOILERPLATE = {"研究免责", "仅供学习研究", "数据截止日", "命令执行记录", "下游证据", "契约计算"}
SKILL_ID = "ashare-data"


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------

def build_report(registry_path, skill_id):
    reg = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    skill = next(s for s in reg["skills"] if s["skill_id"] == skill_id)
    lines = [f"# {skill_id}\n"]
    for sec in skill.get("sections", []):
        if not sec.get("required"):
            continue
        h = sec["heading"]
        needs_depth = sec.get("min_content_chars", 0) > 1 or h not in BOILERPLATE
        fill = (f"{h}的数据详实论证内容充实满足下限要求 " * 30 + "\n") if needs_depth else "占位\n"
        lines.append(f"## {h}\n{fill}")
    for i in range(skill.get("min_dissent_points", 0)):
        lines.append(f"## 分歧点{i + 1}\n与另一视角存在分歧需交锋。数据详实论证内容充实满足下限要求。\n")
    body = "".join(lines)
    while len(body.encode("utf-8")) < skill["artifact"]["min_bytes"]:
        body += "数据详实论证扩充内容 " * 20 + "\n"
    return body


FACTS = [
    {"fact_id": "fact-ashare-data-price", "field": "price", "value": 1.0,
     "source_ids": ["src-ashare-data-primary"], "confidence": "high"},
    {"fact_id": "fact-ashare-data-market-cap", "field": "market_cap", "value": 2.0,
     "source_ids": ["src-ashare-data-primary"], "confidence": "high"},
    {"fact_id": "fact-ashare-data-revenue", "field": "revenue", "value": 3.0,
     "source_ids": ["src-ashare-data-primary"], "confidence": "high"},
]
SOURCES = [
    {"source_id": "src-ashare-data-primary", "url": "https://example.invalid/1",
     "retrieved_at": "2026-07-23", "source_type": "filing",
     "publisher": "Exchange", "title": "一手来源"},
    {"source_id": "src-ashare-data-secondary", "url": "https://example.invalid/2",
     "retrieved_at": "2026-07-23", "source_type": "web",
     "publisher": "Market", "title": "二次来源"},
]
CALCS = [{"calculation_id": "calculation.ashare-data.attack", "operation": "calc",
          "args": {"expr": "1 + 1"}}]
CAPS = [{"capability": "tushare_configured", "available": True}]


def new_run(root: Path, tag: str) -> Path:
    run_root = root / f"local/company/000651.SZ-格力电器/20260723-120000-{tag}"
    r = subprocess.run(
        [sys.executable, str(GATE), "init", "--registry", str(REGISTRY),
         "--repo-root", str(root), "--company", "格力电器", "--code", "000651.SZ",
         "--as-of", "2026-07-23", "--platform", "workbuddy",
         "--run-root", str(run_root), "--allow-stale"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return run_root


def make_bundle(run_root: Path, attempt: str, *, receipts=None, facts=None,
                status="PASS", error=None, lease_nonce="lease-x",
                agent_job_id=None) -> dict:
    attempt_dir = run_root / f"evidence/attempts/{SKILL_ID}/{attempt}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    source = attempt_dir / "report.md"
    source.write_text(build_report(REGISTRY, SKILL_ID), encoding="utf-8")
    run_id = gate.load_manifest(run_root)["run"]["run_id"]
    return {
        "schema_version": "result-schema/v1", "run_id": run_id,
        "work_unit_id": f"wu-{SKILL_ID}", "attempt_id": attempt,
        "agent_job_id": agent_job_id or f"job-{attempt}", "lease_nonce": lease_nonce,
        "skill_id": SKILL_ID, "role_id": None, "status": status,
        "artifact_records": [{
            "artifact_id": f"artifact.{SKILL_ID}",
            "path": str(source.relative_to(run_root)),
            "bytes": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "formal": False, "accepted": False,
        }],
        "fact_updates": facts if facts is not None else FACTS,
        "source_records": SOURCES, "calculation_requests": CALCS,
        "judgments": [], "role_runs": [],
        "command_receipts": receipts if receipts is not None else [],
        "capability_records": CAPS, "limitations": [], "pwl_candidates": [],
        "started_at": "2026-07-23T12:00:00+08:00",
        "completed_at": "2026-07-23T12:01:00+08:00", "error": error,
    }


def admit(run_root: Path, bundle: dict) -> list:
    return gate.admit_bundle(bundle, run_root, gate.load_registry(REGISTRY))


# --------------------------------------------------------------------------
# 攻击清单
# --------------------------------------------------------------------------

def main() -> int:
    results = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "repo"
        root.mkdir()
        run1 = new_run(root, "attack1")
        run1_id = gate.load_manifest(run1)["run"]["run_id"]

        # 合法执行器回执（后续做篡改/伪造的基底）
        legit = er.execute_and_sign(
            run1, "rcpt-ashare-data-legit", "quote",
            [sys.executable, "-c", "import sys; sys.stdout.write('ok')", "quote"])[0]

        # C1：跨 run 搬回执
        run2 = new_run(root, "attack2")
        foreign = er.execute_and_sign(
            run2, "rcpt-ashare-data-foreign", "quote",
            [sys.executable, "-c", "import sys; sys.stdout.write('ok')", "quote"])[0]

        cases = [
            # (id, 说明, bundle, 预期: reject=必须拒 / boundary=威胁模型声明可过)
            ("A1", "v3.4.13 最原始自证：PASS 回执无任何执行痕迹",
             make_bundle(run1, "atk-a1", receipts=[
                 {"receipt_id": "rcpt-ashare-data-a1", "operation": "quote", "status": "PASS"}]),
             "reject"),
            ("A2", "占位水印：fact value 含 PLACEHOLDER",
             make_bundle(run1, "atk-a2", facts=[
                 {**FACTS[0], "value": "PLACEHOLDER::未核实"}, FACTS[1], FACTS[2]]),
             "reject"),
            ("B1", "v3.4.14 核心盲区：手写 argv+output（自述字符串、无签名）",
             make_bundle(run1, "atk-b1", receipts=[
                 {"receipt_id": "rcpt-ashare-data-b1", "operation": "quote", "status": "PASS",
                  "argv": ["tushare", "quote", "--ts_code", "000651.SZ"],
                  "output": "quote 实际执行输出已落盘"}]),
             "reject"),
            ("B2", "伪造标记：PASS 回执 detail 写 TEST_FIXTURE::未连接真实命令日志",
             make_bundle(run1, "atk-b2", receipts=[
                 {"receipt_id": "rcpt-ashare-data-b2", "operation": "quote", "status": "PASS",
                  "argv": ["tushare", "quote", "--ts_code", "000651.SZ"],
                  "output": "quote 输出", "detail": "TEST_FIXTURE::未连接真实命令日志"}]),
             "reject"),
            ("B3", "白名单外操作：custom-download 声称 PASS",
             make_bundle(run1, "atk-b3", receipts=[
                 {"receipt_id": "rcpt-ashare-data-b3", "operation": "custom-download",
                  "status": "PASS"}]),
             "reject"),
            ("C1", "跨 run 搬回执：把另一 run 执行器签发的回执原样搬入",
             make_bundle(run1, "atk-c1", receipts=[foreign]),
             "reject"),
            ("C2", "篡改执行器回执：output_digest 改 0×64",
             make_bundle(run1, "atk-c2", receipts=[{**legit, "output_digest": "0" * 64}]),
             "reject"),
        ]

        for cid, desc, bundle, expect in cases:
            errs = admit(run1, bundle)
            rejected = bool(errs)
            if expect == "reject":
                verdict = "✅ 防御有效（红）" if rejected else "❌ 意外可绕过（绿）"
            else:  # boundary
                verdict = "ℹ️ 威胁模型边界（绿）" if not rejected else "✅ 比威胁模型更严（红）"
            results.append((cid, desc, verdict, "; ".join(errs[:2]) if errs else "放行"))
            print(f"[{cid}] {desc}")
            print(f"      → {verdict}")
            if errs:
                print(f"        拒收理由：{errs[0].strip()}")
            print()

        # C3：correction 注入手写 PASS 回执（走真实 submit-result + submit-correction 链路）
        print("[C3] correction 注入手写 PASS 回执（先提交合法单元，再经 correction 抹签名重写）")
        leased = json.loads(subprocess.run(
            [sys.executable, str(CLI), "next-work", "--run-root", str(run1)],
            capture_output=True, text=True, cwd=str(root)).stdout)
        subprocess.run([sys.executable, str(CLI), "job-started", "--run-root", str(run1),
                        "--work-unit-id", leased["work_unit_id"],
                        "--attempt-id", leased["attempt_id"],
                        "--lease-nonce", leased["lease_nonce"],
                        "--agent-job-id", f"job-{leased['attempt_id']}"],
                       capture_output=True, text=True, cwd=str(root))
        bundle_ok = make_bundle(run1, leased["attempt_id"], receipts=[legit],
                                lease_nonce=leased["lease_nonce"],
                                agent_job_id=f"job-{leased['attempt_id']}")
        rp = run1 / f"evidence/attempts/{SKILL_ID}/{leased['attempt_id']}/result.json"
        rp.write_text(json.dumps(bundle_ok, ensure_ascii=False), encoding="utf-8")
        sub = subprocess.run([sys.executable, str(CLI), "submit-result", "--run-root", str(run1),
                              "--registry", str(REGISTRY), "--result", str(rp)],
                             capture_output=True, text=True, cwd=str(root))
        assert sub.returncode == 0, sub.stdout + sub.stderr
        forged = dict(legit)
        forged.pop("signature", None)
        correction = {
            "schema_version": "correction-bundle/v1", "run_id": run1_id,
            "skill_id": SKILL_ID, "base_attempt_id": leased["attempt_id"],
            "corrections": {"calculation_requests": [], "fact_updates": [],
                            "judgments": [], "command_receipts": [forged]},
        }
        cp = run1 / "evidence/attempts/correction-atk.json"
        cp.write_text(json.dumps(correction, ensure_ascii=False), encoding="utf-8")
        cc = subprocess.run([sys.executable, str(CLI), "submit-correction", "--run-root", str(run1),
                             "--registry", str(REGISTRY), "--correction", str(cp)],
                            capture_output=True, text=True, cwd=str(root))
        rejected = cc.returncode != 0
        verdict = "✅ 防御有效（红）" if rejected else "❌ 意外可绕过（绿）"
        results.append(("C3", "correction 注入手写 PASS 回执（抹签名重写既有回执）", verdict,
                        (cc.stdout + cc.stderr).strip()[:200] if rejected else "放行"))
        print(f"      → {verdict}")
        if rejected:
            print(f"        拒收理由：{(cc.stdout + cc.stderr).strip()[:200]}")
        print()

        # C4：删密钥降级 legacy → 手写 argv+output（威胁模型声明边界）
        print("[C4] 删密钥降级 legacy：恶意进程删除 receipt-signing-key 后手写 argv+output")
        key_path = run1 / er.SECRET_REL
        key_path.unlink()
        errs = admit(run1, make_bundle(run1, "atk-c4", receipts=[
            {"receipt_id": "rcpt-ashare-data-c4", "operation": "quote", "status": "PASS",
             "argv": ["tushare", "quote", "--ts_code", "000651.SZ"],
             "output": "quote 输出"}]))
        rejected = bool(errs)
        verdict = "ℹ️ 威胁模型边界（绿，已声明）" if not rejected else "✅ 比威胁模型更严（红）"
        results.append(("C4", "删密钥降级 legacy 后手写 argv+output", verdict,
                        "; ".join(errs[:2]) if errs else "放行"))
        print(f"      → {verdict}")
        if errs:
            print(f"        拒收理由：{errs[0].strip()}")
        print()

        # C5：读密钥伪造全套（签名+journal+落盘文件）——恶意进程视角（威胁模型声明边界）
        print("[C5] 读密钥伪造全套：签名 + journal 留痕 + 落盘文件全部伪造")
        # C4 删掉了 run1 的密钥；执行器与恶意进程均可 ensure 自愈生成新密钥。
        # 恶意进程读到密钥后伪造全套，验证六项校验是否如威胁模型所述全部放行。
        secret = er.ensure_signing_secret(run1)
        rid = "rcpt-ashare-data-forged"
        out_rel = f"{er.OUTPUT_DIR_REL}/{rid}.txt"
        (run1 / out_rel).write_text("forged-but-consistent output", encoding="utf-8")
        digest = hashlib.sha256((run1 / out_rel).read_bytes()).hexdigest()
        receipt = {
            "receipt_id": rid, "operation": "quote", "status": "PASS",
            "argv": ["tushare", "quote", "--ts_code", "000651.SZ"],
            "output": out_rel, "executed_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": 0, "output_digest": digest,
            "executor_version": er.EXECUTOR_VERSION,
        }
        receipt["signature"] = er.sign_payload(secret, receipt, run1_id)
        er.append_journal(run1, {
            "receipt_id": rid, "operation": "quote", "argv": receipt["argv"],
            "exit_code": 0, "executed_at": receipt["executed_at"], "output": out_rel,
            "output_digest": digest, "executor_version": er.EXECUTOR_VERSION,
            "run_id": run1_id,
        })
        errs = admit(run1, make_bundle(run1, "atk-c5", receipts=[receipt]))
        rejected = bool(errs)
        verdict = "ℹ️ 威胁模型边界（绿，已声明）" if not rejected else "✅ 比威胁模型更严（红）"
        results.append(("C5", "读密钥伪造签名+journal+落盘（恶意进程）", verdict,
                        "; ".join(errs[:2]) if errs else "放行"))
        print(f"      → {verdict}")
        if errs:
            print(f"        拒收理由：{errs[0].strip()}")
        print()

    # 汇总
    print("=" * 64)
    print("汇总（第二方验证，非最终裁决）")
    print("=" * 64)
    for cid, desc, verdict, detail in results:
        print(f"  [{cid}] {verdict}")
    bypass = [cid for cid, _, v, _ in results if v.startswith("❌")]
    print("=" * 64)
    if bypass:
        print(f"❌ 发现意外可绕过：{bypass} —— 威胁模型未声明的缺口，需立即处理")
        return 1
    print("✅ 已知攻击面全部被拒；两个绿点为威胁模型已声明的边界（非新缺陷）。")
    print("   注：本脚本已挂 check.sh，退化为自维护防线；收敛与否仍须用户 review 裁决。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
