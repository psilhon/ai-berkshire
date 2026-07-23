import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ADAPTER = REPO / "workbuddy-skills/full-company-analysis/SKILL.md"


class WorkBuddyAdapterTests(unittest.TestCase):
    def test_adapter_is_workbuddy_native_and_does_not_spawn_python_agents(self):
        text = ADAPTER.read_text(encoding="utf-8")
        self.assertIn("platform: workbuddy", text)
        self.assertIn("tools/full_analysis_runtime.py", text)
        self.assertIn("tools/full_analysis_gate.py", text)
        self.assertIn("WorkBuddy 原生 Agent", text)
        self.assertIn("job-started", text)
        self.assertIn("submit-result", text)
        self.assertNotIn("full_analysis_orchestrator.py", text)
        self.assertNotIn("subprocess.Popen", text)

    def test_adapter_requires_short_receipt_only_for_parent_context(self):
        text = ADAPTER.read_text(encoding="utf-8")
        self.assertIn("attempt_id", text)
        self.assertIn("result_path", text)
        self.assertIn("不读取报告正文", text)


if __name__ == "__main__":
    unittest.main()
