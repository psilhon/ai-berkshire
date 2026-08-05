"""Task 2：correction bundle —— 确定性证据错误的定向修正（lean 契约现代化版）。

lean 契约（full-analysis-contract/lean-v1）已移除 sections / evidence_rules /
artifact_id：Gate 改为校验「实质地板」（三锚 + 实质章节 + 字节下限）。这带来两点
对 correction 单测的影响，本文件据此现代化：

1. submit-result 走完整准入 admit_bundle(check_artifacts=True) → _substance_errors，
   而后者仍按 contract `sections` 计数（lean 已移除），导致 min_substantive_sections
   永远无法满足、任何 PASS 报告都 ingest 失败（见 tests/test_full_analysis_e2e.py
   的 expectedFailure 注释，属 impl 缺陷，**不在此修改 tools/**）。correction 命令
   本身不调 admit_bundle，因此本测试直接把「已接受 attempt」写进 manifest，绕开
   损坏的 submit-result 链路，仅服务 correction 逻辑本身的单测。

2. lean 下所有技能 evidence_rules 为空 → 没有回执白名单 → Gate 不再对 correction
   做执行器签名校验（_precheck_command_receipts 白名单为空直接放行）。原
   test_correction_rejects_forged_pass_receipt（验证「伪造签名回执被拒」）在 lean 下
   不再成立，改为验证 correction 对回执的**唯一剩余硬约束**：只能引用 manifest 中
   已存在的 receipt_id，禁止凭空新增未注册回执。
"""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "full_analysis.py"
REGISTRY = REPO / "tools" / "full_analysis_contract.json"

sys.path.insert(0, str(REPO / "tools"))
import evidence_receipt as er  # 真 oracle：让夹具用执行器真实签发回执  # noqa: E402


def _seed_accepted_attempt(run_root, skill_id, attempt_id, with_receipt=False):
    """把「已接受 attempt」（含 1 calc / 1 fact / 1 source / 1 artifact，可选 1 回执）
    直接写进 manifest。

    不走 submit-result（lean 下被 _substance_errors 的 sections 计数缺陷拦截），
    因为 correction 命令本身不调 admit_bundle，无需完整 ingest。返回
    (calc, fact, source, receipt_or_None) 供构造 correction 载荷与断言复用。
    """
    manifest = json.loads(
        (run_root / "evidence/00-analysis-manifest.json").read_text(encoding="utf-8"))
    entry = next(s for s in manifest["skills"] if s["skill_id"] == skill_id)
    entry["attempts"] = list(entry.get("attempts") or []) + [attempt_id]
    entry["status"] = "DONE"
    calc = {
        "calculation_id": f"calculation.{skill_id}.corr1",
        "operation": "calc",
        "args": {"expr": "1 + 1"},
    }
    fact = {
        "fact_id": f"fact.{skill_id}.corr1",
        "field": "price",
        "value": 12.34,
        "source_ids": [f"src.{skill_id}.primary"],
        "confidence": "high",
    }
    source = {
        "source_id": f"src.{skill_id}.primary",
        "url": f"https://example.invalid/{skill_id}",
        "retrieved_at": "2026-07-23",
        "source_type": "filing",
        "publisher": f"{skill_id} Exchange",
        "title": f"{skill_id} 一手来源",
    }
    artifact = {
        "artifact_id": f"artifact.{skill_id}",  # lean 无 artifact_id → Gate 回退到此
        "path": f"evidence/attempts/{skill_id}/{attempt_id}/report.md",
        "bytes": 10,
        "sha256": "seed-artifact-sha",
        "formal": False,
        "accepted": True,
    }
    manifest["calculations"].append(calc)
    manifest["facts"].append(fact)
    manifest["sources"].append(source)
    manifest["artifacts"].append(artifact)
    receipt = None
    if with_receipt:
        receipt_id = f"receipt.{skill_id}.1"
        receipt, _exit = er.execute_and_sign(
            Path(run_root), receipt_id, "corr-op",
            [sys.executable, "-c", "import sys; sys.stdout.write('evidence-ok')",
             "corr-op"])
        manifest["command_receipts"].append(receipt)
    (run_root / "evidence/00-analysis-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return calc, fact, source, receipt


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.run_root = self.root / "local/company/000651.SZ-格力电器/20260723-120000-corr"
        started = self.cli("start", "--registry", REGISTRY, "--repo-root", self.root,
                           "--company", "格力电器", "--code", "000651.SZ",
                           "--as-of", "2026-07-23", "--run-root", self.run_root)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        manifest = json.loads(
            (self.run_root / "evidence/00-analysis-manifest.json").read_text())
        self.run_id = manifest["run"]["run_id"]
        self.attempt_id = "attempt.financial-data.corr1"
        self.calc, self.fact, self.source, _ = _seed_accepted_attempt(
            self.run_root, "financial-data", self.attempt_id, with_receipt=False)

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)], cwd=self.root,
                              capture_output=True, text=True)

    def manifest(self):
        return json.loads(
            (self.run_root / "evidence/00-analysis-manifest.json").read_text())

    def _correction(self, **over):
        calc = self.calc
        fact = self.fact
        base = {
            "schema_version": "correction-bundle/v1",
            "run_id": self.run_id,
            "skill_id": "financial-data",
            "base_attempt_id": self.attempt_id,
            "corrections": {
                "calculation_requests": [{**calc, "args": {"expr": "2 + 3"}}],
                "fact_updates": [{**fact, "removed": True}],
                "judgments": [],
                "command_receipts": [],
            },
        }
        return json.dumps({**base, **over}, ensure_ascii=False)

    def _apply(self, payload):
        p = self.run_root / "evidence/attempts/correction-test.json"
        p.write_text(payload, encoding="utf-8")
        return self.cli("submit-correction", "--run-root", self.run_root,
                        "--registry", REGISTRY, "--correction", p)

    def test_correction_updates_calc_and_removes_fact_without_new_attempt(self):
        before = self.manifest()
        attempts_before = len(before["skills"][next(i for i, s in enumerate(before["skills"])
                                                  if s["skill_id"] == "financial-data")]["attempts"])
        artifact_before = before["artifacts"][0]["sha256"]
        proc = self._apply(self._correction())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        after = self.manifest()
        calcs = {c["calculation_id"]: c for c in after["calculations"]}
        self.assertEqual(
            calcs[self.calc["calculation_id"]]["args"]["expr"], "2 + 3")
        fact_ids = {f["fact_id"] for f in after["facts"]}
        self.assertNotIn(self.fact["fact_id"], fact_ids)
        attempts_after = len(next(s for s in after["skills"]
                                  if s["skill_id"] == "financial-data")["attempts"])
        self.assertEqual(attempts_after, attempts_before, "correction 不得新增 attempt")
        self.assertEqual(after["artifacts"][0]["sha256"], artifact_before,
                         "correction 不得重写报告")
        # correction 记录保留
        corrs = after.get("corrections", [])
        self.assertTrue(corrs, "manifest 应记录 correction")
        self.assertEqual(corrs[-1]["skill_id"], "financial-data")
        self.assertEqual(corrs[-1]["base_attempt_id"], self.attempt_id)
        self.assertIn("digest", corrs[-1])

    def test_correction_replayed_by_audit(self):
        # 只改 calc（不删 fact，避免审计 evidence 不足），验证 audit 重放新值
        calc = self.calc
        payload = json.dumps({
            "schema_version": "correction-bundle/v1",
            "run_id": self.run_id,
            "skill_id": "financial-data",
            "base_attempt_id": self.attempt_id,
            "corrections": {
                "calculation_requests": [{**calc, "args": {"expr": "2 + 3"}}],
                "fact_updates": [], "judgments": [], "command_receipts": [],
            },
        }, ensure_ascii=False)
        self.assertEqual(self._apply(payload).returncode, 0)
        audit = self.cli("audit", "--run-root", self.run_root, "--registry", REGISTRY)
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        report = json.loads(audit.stdout)
        self.assertEqual(report["calculations"]["replayed"], 1)
        self.assertEqual(report.get("evidence", {}).get("violation_count"), 0)

    def test_correction_rejects_unknown_id(self):
        calc = self.calc
        payload = self._correction().replace(
            calc["calculation_id"], "calculation.brand-new.999")
        proc = self._apply(payload)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("不存在", proc.stdout + proc.stderr)

    def test_correction_rejects_unregistered_receipt_id(self):
        # lean 契约已移除 evidence_rules → 没有回执白名单 → Gate 不再对 correction
        # 做执行器签名校验（_precheck_command_receipts 白名单为空直接放行）。
        # 因此「伪造签名回执」这类攻击在 lean 下不再被签名校验挡住；correction 对
        # 回执的唯一剩余硬约束是：只能引用 manifest 中【已存在】的 receipt_id，
        # 禁止凭空新增未注册回执。此测试验证该底线。
        rr2 = self.root / "local/company/000651.SZ-格力电器/20260723-120000-corr2"
        started = self.cli("start", "--registry", REGISTRY, "--repo-root", self.root,
                           "--company", "格力电器", "--code", "000651.SZ",
                           "--as-of", "2026-07-23", "--run-root", rr2)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        manifest = json.loads(
            (rr2 / "evidence/00-analysis-manifest.json").read_text())
        run_id2 = manifest["run"]["run_id"]
        attempt2 = "attempt.financial-data.corr2"
        _seed_accepted_attempt(rr2, "financial-data", attempt2, with_receipt=True)
        payload = json.dumps({
            "schema_version": "correction-bundle/v1",
            "run_id": run_id2,
            "skill_id": "financial-data",
            "base_attempt_id": attempt2,
            "corrections": {
                "calculation_requests": [], "fact_updates": [], "judgments": [],
                # receipt id 从未在 manifest 注册过 → correction 不得凭空新增
                "command_receipts": [{
                    "receipt_id": "receipt.forged.999",
                    "operation": "corr-op", "status": "PASS",
                    "argv": ["python", "-c", "x"], "output": "fake",
                }],
            },
        }, ensure_ascii=False)
        p = rr2 / "evidence/attempts/correction-forge.json"
        p.write_text(payload, encoding="utf-8")
        proc = self.cli("submit-correction", "--run-root", rr2,
                        "--registry", REGISTRY, "--correction", p)
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("不存在", proc.stdout + proc.stderr)

    def test_correction_rejects_missing_schema_version(self):
        proc = self._apply(
            self._correction().replace('"schema_version": "correction-bundle/v1",', ""))
        self.assertNotEqual(proc.returncode, 0)

    def test_correction_rejects_artifact_records(self):
        payload = self._correction().replace(
            '"corrections"', '"artifact_records": [{"path": "x"}], "corrections"')
        proc = self._apply(payload)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("禁止", proc.stdout + proc.stderr)

    def test_correction_rejects_wrong_run_id(self):
        proc = self._apply(self._correction().replace(self.run_id, "run-wrong"))
        self.assertNotEqual(proc.returncode, 0)

    def test_correction_rejects_wrong_skill(self):
        proc = self._apply(
            self._correction().replace('"skill_id": "financial-data"', '"skill_id": "ashare-data"'))
        self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
