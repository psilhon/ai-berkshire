"""WorkBuddy 生产适配器（lean 模式）与 canonical workflow 的同步守卫。

lean 重写后，编排器文档不再提及 tools/full_analysis_runtime.py / 短回执字段
（attempt_id / result_path）等旧机制；断言必须跟随文档的**当前真实内容**，
否则守卫测试守的是已经不存在的口径。
"""
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ADAPTER = REPO / "workbuddy-skills/full-company-analysis-workbuddy/SKILL.md"


class WorkBuddyAdapterTests(unittest.TestCase):
    def test_adapter_is_workbuddy_native_and_does_not_spawn_python_agents(self):
        text = ADAPTER.read_text(encoding="utf-8")
        self.assertIn("platform: workbuddy", text)
        self.assertIn("registry-schema: full-analysis-contract/lean-v1", text)
        self.assertIn("tools/full_analysis_contract.json", text)
        self.assertIn("WorkBuddy 原生 Agent", text)
        self.assertIn("next-work", text)
        self.assertIn("submit-result", text)
        self.assertNotIn("full_analysis_orchestrator.py", text)
        self.assertNotIn("subprocess.Popen", text)

    def test_adapter_states_lean_two_bottom_lines(self):
        """lean 模式的两条底线必须写在适配器里：报告是唯一交付物 + 失败显式声明。"""
        text = ADAPTER.read_text(encoding="utf-8")
        self.assertIn("两条底线", text)
        self.assertIn("唯一交付物", text)
        self.assertIn("mark-failed", text)
        self.assertIn("不静默跳过", text)
        # 空账本合法、绝不合成占位——与 mk_result_bundle 的 lean 行为同口径
        self.assertIn("空账本合法", text)

    def test_adapter_frontmatter_keeps_governance_metadata_in_sync(self):
        canonical = (REPO / "skills/full-company-analysis-workbuddy.md").read_text(encoding="utf-8")
        adapter = ADAPTER.read_text(encoding="utf-8")
        for field in ("owner", "category", "maturity", "review-cadence"):
            canonical_line = next((line for line in canonical.splitlines() if line.startswith(field + ":")), None)
            adapter_line = next((line for line in adapter.splitlines() if line.startswith(field + ":")), None)
            self.assertEqual(adapter_line, canonical_line, field)

    def test_adapter_is_exact_copy_of_canonical_workflow(self):
        canonical = (
            REPO / "skills/full-company-analysis-workbuddy.md"
        ).read_text(encoding="utf-8")
        adapter = ADAPTER.read_text(encoding="utf-8")
        self.assertEqual(
            adapter,
            canonical,
            "WorkBuddy 生产适配器必须由 canonical workflow 原样同步",
        )


if __name__ == "__main__":
    unittest.main()
