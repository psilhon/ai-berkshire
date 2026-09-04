"""check-stop-gates.py 的故障注入测试（2026-08-30 候选④守卫，此前零测试覆盖）。

先红后绿：用合成 skill 文件注入「缺要素的确认门」必须变红，补齐后必须变绿；
真门 / 硬停止规则 / 散文提及三种语义分别落在 强校验 / INFO 两桶。
真实仓库不变式：skills/ 下所有确认门骨架完整（与 check.sh 同断言）。
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check-stop-gates.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_stop_gates", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 三要素齐全的真门（单行含句号，_gates_in 按行拼接到句号）
GREEN_GATE = (
    "🔴 STOP / 检查点：在对外发布报告前，必须先向用户确认"
    "（给出明确选项：如\"确认发布\"\"仅生成草稿待审阅\"），"
    "获得明确同意后再继续；未经确认不得自主发布。"
)
# 缺「明确选项」：用户无从选择
RED_NO_OPTION = (
    "🔴 STOP / 检查点：在对外发布报告前，必须先向用户确认，"
    "获得明确同意后再继续；未经确认不得自主发布。"
)
# 缺「停止效力」：确认了但没有禁止式/许可式收尾
RED_NO_EFFICACY = (
    "🔴 STOP / 检查点：在对外发布报告前，必须先向用户确认"
    "（给出明确选项：如\"确认发布\"\"仅生成草稿待审阅\"）。"
)
# 非确认类标记（硬停止规则语义）→ INFO 桶，不判红
INFO_GATE = "🔴 STOP：可信度不足 40% 时，否决本报告，不进入后续分析。"


class CheckStopGatesTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def _skills_dir(self, *bodies: str) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="stop-gates-test-"))
        for i, body in enumerate(bodies):
            (tmp / f"skill-{i}.md").write_text(body, encoding="utf-8")
        return tmp

    def test_green_gate_passes(self):
        gates, others, violations = self.mod.check(self._skills_dir(GREEN_GATE))
        self.assertEqual((gates, others, violations), (1, 0, []))

    def test_missing_option_is_red(self):
        _, _, violations = self.mod.check(self._skills_dir(RED_NO_OPTION))
        self.assertEqual(len(violations), 1)
        self.assertIn("明确选项", violations[0])

    def test_missing_efficacy_is_red(self):
        _, _, violations = self.mod.check(self._skills_dir(RED_NO_EFFICACY))
        self.assertEqual(len(violations), 1)
        self.assertIn("停止效力", violations[0])

    def test_non_confirm_gate_lands_in_info(self):
        gates, others, violations = self.mod.check(self._skills_dir(INFO_GATE))
        self.assertEqual((gates, others, violations), (0, 1, []))

    def test_fault_injection_red_then_green(self):
        """先红：注入缺要素门必红；后绿：补齐同文件后必绿。"""
        skills = self._skills_dir(RED_NO_OPTION)
        _, _, red = self.mod.check(skills)
        self.assertEqual(len(red), 1, "注入的残缺门必须变红")
        (next(skills.glob("skill-*.md"))).write_text(GREEN_GATE, encoding="utf-8")
        gates, _, green = self.mod.check(skills)
        self.assertEqual((gates, green), (1, []), "补齐后必须变绿")

    def test_real_repo_all_gates_complete(self):
        """真仓不变式：skills/ 全部确认门三要素齐全（check.sh 同断言）。"""
        gates, others, violations = self.mod.check(REPO / "skills")
        self.assertGreater(gates, 0, "真仓应存在用户确认门（守卫失效信号）")
        self.assertEqual(violations, [], f"真仓存在残缺门: {violations}")

    def test_cli_exit_zero_on_real_repo(self):
        proc = __import__("subprocess").run(
            [sys.executable, str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("✅", proc.stdout)


if __name__ == "__main__":
    unittest.main()
