#!/usr/bin/env python3
"""run_layout 深模块的接口测试。

背景（2026-08-30 架构评审候选②）：run 目录的布局知识此前散布三个模块五处字面量
（gate 375/1058/1182 · mk_result_bundle 358 · scripts/full_analysis 175），
且 gate 与 runtime **各自又定义了一遍**四个状态文件路径。改一次目录结构要追多处。
收敛到 tools/run_layout.py 后，本测试机器证明「单一真源」：
gate 的 re-export 与 runtime 的本地名必须是同一对象，谓词是唯一的判定入口。

边界：本模块只收拢"东西放在哪"，不收拢"谁来写"——状态所有权仍分属
gate（manifest/events）与 runtime（state/usage），此处不加断言。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import run_layout  # noqa: E402


class TestSingleSource(unittest.TestCase):
    def test_gate_reexports_are_same_object(self):
        import full_analysis_gate as gate
        for name in ("MANIFEST_REL", "RUNTIME_STATE_REL", "EVENTS_REL",
                     "ATTEMPTS_REL", "SUMMARY_ATTEMPTS_REL"):
            self.assertIs(getattr(gate, name), getattr(run_layout, name), name)

    def test_runtime_state_rel_is_same_object(self):
        """runtime 的 STATE_REL 必须就是布局里的 RUNTIME_STATE_REL（不是副本）。"""
        import full_analysis_runtime as runtime
        self.assertIs(runtime.STATE_REL, run_layout.RUNTIME_STATE_REL)
        self.assertIs(runtime.EVENTS_REL, run_layout.EVENTS_REL)
        self.assertIs(runtime.MANIFEST_REL, run_layout.MANIFEST_REL)
        self.assertIs(runtime.USAGE_REL, run_layout.USAGE_REL)


class TestPredicates(unittest.TestCase):
    def test_in_attempts(self):
        self.assertTrue(run_layout.in_attempts("evidence/attempts/x/report.md"))
        self.assertTrue(run_layout.in_attempts(Path("evidence/attempts/x/report.md")))
        # 同级但不同名的目录不得误判（前缀匹配必须有尾部斜杠）
        self.assertFalse(run_layout.in_attempts("evidence/attempts_other/x.md"))
        self.assertFalse(run_layout.in_attempts("evidence/other/x.md"))

    def test_in_summary_attempts(self):
        self.assertTrue(run_layout.in_summary_attempts("evidence/attempts/summary/s.md"))
        self.assertFalse(run_layout.in_summary_attempts("evidence/attempts/x/report.md"))

    def test_summary_is_subset_of_attempts(self):
        """总结目录必须仍在 attempts 之下——否则 Gate 的 artifact 准入会拒收总结。"""
        self.assertTrue(run_layout.in_attempts("evidence/attempts/summary/s.md"))


if __name__ == "__main__":
    unittest.main()
