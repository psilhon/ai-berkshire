"""tests/conftest.py（run 目录工厂）的接口测试。

机器证明工厂产物与 gate init 同构：状态文件路径取 run_layout 常量、
读写走 run_store、attempt 播种登记进 manifest。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conftest  # noqa: E402

import run_layout  # noqa: E402
import run_store  # noqa: E402


class RunFactoryTests(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="run-factory-test-"))

    def test_canonical_layout_via_run_layout_constants(self):
        run_root = conftest.make_run_root(self.td, run_id="r1", skills=("ashare-data",))
        self.assertEqual(run_store.load_manifest(run_root)["run"]["run_id"], "r1")
        self.assertEqual(
            run_store.load_manifest(run_root)["manifest_schema_version"],
            "full-analysis-manifest/v2")
        state = run_store.load_runtime_state(run_root)
        self.assertEqual(state["state_version"], run_store.STATE_VERSION)
        self.assertEqual(state["budget"]["normal_target"], 3)  # 2N+1, N=1
        # 事件与 evidence 种子文件落在 run_layout 常量声明的位置
        self.assertTrue((run_root / run_layout.EVENTS_REL).exists())
        self.assertTrue((run_root / "evidence" / "facts.json").exists())

    def test_seed_attempt_registers_into_manifest(self):
        run_root = conftest.make_run_root(self.td, skills=("ashare-data",))
        attempt_dir = conftest.seed_attempt(run_root, "ashare-data", "a1", "# 报告\n")
        self.assertEqual(
            attempt_dir.relative_to(run_root).as_posix(),
            (run_layout.ATTEMPTS_REL / "ashare-data" / "a1").as_posix())
        self.assertTrue((attempt_dir / "report.md").read_text(encoding="utf-8").startswith("#"))
        manifest = run_store.load_manifest(run_root)
        entry = next(s for s in manifest["skills"] if s["skill_id"] == "ashare-data")
        self.assertEqual(entry["attempts"], ["a1"])

    def test_work_units_override_is_honored(self):
        leased = [{"work_unit_id": "wu-x", "skill_id": "x", "status": "LEASED",
                   "lease": {"attempt_id": "a1", "lease_nonce": "n", "agent_job_id": "j"}}]
        run_root = conftest.make_run_root(self.td, skills=("x",), work_units=leased)
        self.assertEqual(run_store.load_runtime_state(run_root)["work_units"], leased)

    def test_run_started_at_override(self):
        past = "2026-01-01T00:00:00+00:00"
        run_root = conftest.make_run_root(self.td, run_started_at=past)
        self.assertEqual(run_store.load_runtime_state(run_root)["run_started_at"], past)


if __name__ == "__main__":
    unittest.main()
