"""rework 命令 —— 报告正文/artifact 类返工的有状态封装（lean 契约现代化版）。

lean 契约（full-analysis-contract/lean-v1）已移除 sections / evidence_rules /
artifact_id：Gate 改为校验「实质地板」（三锚 + 实质章节 + 字节下限）。这带来一点
对 rework 单测的影响，本文件据此现代化：

submit-result 走完整准入 admit_bundle(check_artifacts=True) → _substance_errors，
而后者用 `heading.startswith("##")` 过滤 _section_blocks 的输出，但后者已把 `#`
标记剥掉，导致 min_substantive_sections 永远无法满足、任何 PASS 报告都 ingest 失败
（tools/full_analysis_gate.py:973，属 impl 缺陷，**不在此修改 tools/**）。
rework 命令本身不调 admit_bundle，只读 runtime-state 与 manifest.skills[].attempts，
因此 setUp 沿用 tests/test_full_analysis_correction.py 的既有约定：直接把
「已接受 attempt」播种进 manifest 与 runtime-state，绕开损坏的 submit-result 链路，
仅服务 rework 逻辑本身的单测。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_full_analysis_e2e import build_compliant_report

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "full_analysis.py"
REGISTRY = REPO / "tools" / "full_analysis_contract.json"


class ReworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.run_root = self.root / "local/company/000651.SZ-格力电器/20260723-120000-rw"
        started = self.cli("start", "--registry", REGISTRY, "--repo-root", self.root,
                           "--company", "格力电器", "--code", "000651.SZ",
                           "--as-of", "2026-07-23", "--run-root", self.run_root)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        # 取第一个单元并置为「已有被 Gate 接受产物的 DONE 单元」——rework 的合法前态
        self.lease = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(self.lease["status"], "LEASED")
        self._complete(self.lease)

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)], cwd=self.root,
                              capture_output=True, text=True)

    def state(self):
        return json.loads((self.run_root / "evidence/runtime-state.json").read_text())

    def _complete(self, lease):
        """把单元推进到 rework 要求的前态：DONE + manifest 里有已接受 attempt。

        lean（v3.7+）：无 job-started / 无租约——直接置 DONE 并播种 manifest attempt。
        """
        skill_id = lease["skill_id"]
        attempt_id = lease["attempt_id"]
        attempt_dir = self.run_root / "evidence/attempts" / skill_id / attempt_id
        attempt_dir.mkdir(parents=True, exist_ok=True)
        artifact = attempt_dir / "report.md"
        artifact.write_text(build_compliant_report(REGISTRY, skill_id), encoding="utf-8")

        manifest_path = self.run_root / "evidence/00-analysis-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = next(s for s in manifest["skills"] if s["skill_id"] == skill_id)
        entry["status"] = "PASS"
        entry["attempts"] = [*(entry.get("attempts") or []), attempt_id]
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        state_path = self.run_root / "evidence/runtime-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        unit = next(u for u in state["work_units"]
                    if u["work_unit_id"] == lease["work_unit_id"])
        unit["status"] = "DONE"
        state["concurrency"]["current"] = 0
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def _rework(self, work_unit_id, reason=""):
        return self.cli("rework", "--run-root", self.run_root,
                        "--work-unit-id", work_unit_id,
                        *(["--reason", reason] if reason else []))

    def test_rework_resets_done_unit(self):
        proc = self._rework(self.lease["work_unit_id"], reason="报告缺章节")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        st = self.state()
        unit = next(u for u in st["work_units"] if u["work_unit_id"] == self.lease["work_unit_id"])
        self.assertEqual(unit["status"], "PENDING")
        self.assertIsNone(unit.get("lease"))
        self.assertEqual(unit.get("reuse_attempt"), self.lease["attempt_id"])
        self.assertEqual(st.get("rework_count"), 1)
        events = (self.run_root / "evidence/events.jsonl").read_text().splitlines()
        last = json.loads(events[-1])
        self.assertEqual(last["type"], "rework_initiated")
        self.assertEqual(last["reason"], "报告缺章节")

    def test_rework_rejects_pending_unit(self):
        # 第二个单元从未派发 → 拒绝
        st = self.state()
        pending = next(u for u in st["work_units"] if u["status"] == "PENDING")
        proc = self._rework(pending["work_unit_id"])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("DONE/PARTIAL", proc.stdout + proc.stderr)

    def test_rework_rejects_unknown_unit(self):
        proc = self._rework("wu-no-such-unit")
        self.assertNotEqual(proc.returncode, 0)

    def test_next_work_returns_reuse_base_attempt(self):
        self._rework(self.lease["work_unit_id"])
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(leased["status"], "LEASED")
        self.assertEqual(leased["work_unit_id"], self.lease["work_unit_id"])
        self.assertEqual(leased.get("reuse_base_attempt"), self.lease["attempt_id"])
        self.assertNotEqual(leased["attempt_id"], self.lease["attempt_id"])


if __name__ == "__main__":
    unittest.main()
