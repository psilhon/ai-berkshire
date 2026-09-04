"""sync-codex-skills.py 目标侧孤儿检测回归测试（v3.10.14）。

守护的病根：sync 脚本此前只遍历源侧（skills/*.md → 生成目标），
删源后 codex-skills/<name>/ 整目录永久残留（--check 永远报绿）。
本测试确保：
1. 孤儿判定以「生成标记」为准 —— Codex-only 手写包（如 investment-memo-craft）
   无标记，永远不被误删；
2. --check 遇孤儿必须变红（exit 1）；
3. 生成模式必须清理孤儿整目录。
"""
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "sync-codex-skills.py"


def _load():
    spec = importlib.util.spec_from_file_location("sync_codex_skills", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _skill_with_marker(name: str) -> str:
    return (
        "---\n"
        f'name: {name}\n'
        'description: "generated"\n'
        "---\n\n"
        f"## Codex adapter note\n\n"
        f"This skill is generated from `skills/{name}` so Claude Code "
        "and Codex users share one canonical workflow.\n"
    )


class FindGeneratedOrphansTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_marker_keyed_detection(self):
        """带标记且源已删 → 孤儿；带标记但源仍在 / 无标记手写包 / 杂项 → 非孤儿。"""
        with tempfile.TemporaryDirectory() as td:
            codex = Path(td) / "codex-skills"
            # ① 生成过但源已删：孤儿
            orphan = codex / "ghost"
            orphan.mkdir(parents=True)
            (orphan / "SKILL.md").write_text(_skill_with_marker("ghost"), encoding="utf-8")
            # ② 源仍存在（由调用方 source_names 表达）：非孤儿
            live = codex / "alpha"
            live.mkdir()
            (live / "SKILL.md").write_text(_skill_with_marker("alpha"), encoding="utf-8")
            # ③ Codex-only 手写包（无生成标记）：非孤儿 —— investment-memo-craft 场景
            handmade = codex / "investment-memo-craft"
            handmade.mkdir()
            (handmade / "SKILL.md").write_text("# Investment Memo Craft\nCodex-only.\n",
                                               encoding="utf-8")
            (handmade / "agents").mkdir()
            (handmade / "agents" / "openai.yaml").write_text("model: gpt\n", encoding="utf-8")
            # ④ 无 SKILL.md 的杂项目录 / 散文件：跳过
            (codex / "no-skill-md").mkdir()
            (codex / "loose.txt").write_text("x", encoding="utf-8")

            orphans = self.mod.find_generated_orphans(codex, {"alpha"})
            self.assertEqual([p.name for p in orphans], ["ghost"])

    def test_missing_codex_dir_returns_empty(self):
        self.assertEqual(
            self.mod.find_generated_orphans(Path("/tmp/never-exists-sync-orphans"), set()),
            [],
        )


class SyncScriptEndToEndTests(unittest.TestCase):
    """在假仓库（tmp/scripts + tmp/skills + tmp/codex-skills）里实跑脚本 CLI。"""

    def _fake_repo(self, root: Path) -> Path:
        (root / "skills").mkdir(parents=True)
        (root / "skills" / "alpha.md").write_text("# Alpha\n正文 $ARGUMENTS\n", encoding="utf-8")
        (root / "skills" / "full-company-analysis-workbuddy.md").write_text(
            "# Orchestrator\n", encoding="utf-8")
        (root / "scripts").mkdir(parents=True)
        shutil.copy2(SCRIPT, root / "scripts" / "sync-codex-skills.py")
        return root / "scripts" / "sync-codex-skills.py"

    def _seed_orphan(self, root: Path, name: str = "ghost"):
        codex = root / "codex-skills"
        orphan = codex / name
        orphan.mkdir(parents=True)
        (orphan / "SKILL.md").write_text(_skill_with_marker(name), encoding="utf-8")
        (orphan / "agents").mkdir()
        (orphan / "agents" / "openai.yaml").write_text("model: gpt\n", encoding="utf-8")
        return orphan

    def _run(self, script: Path, *args: str):
        return subprocess.run([sys.executable, str(script), *args],
                              capture_output=True, text=True)

    def test_check_mode_red_on_orphan_then_green_after_generate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = self._fake_repo(root)
            orphan = self._seed_orphan(root)

            red = self._run(script, "--check")
            self.assertEqual(red.returncode, 1, red.stdout + red.stderr)
            self.assertIn("ghost", red.stdout, "孤儿必须被点名")

            done = self._run(script)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertFalse(orphan.exists(), "生成模式必须清理孤儿整目录（含附属资产）")
            self.assertIn("Removed 1", done.stdout)

            green = self._run(script, "--check")
            self.assertEqual(green.returncode, 0, green.stdout + green.stderr)

    def test_handwritten_codex_only_package_survives_generate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = self._fake_repo(root)
            handmade = root / "codex-skills" / "investment-memo-craft"
            handmade.mkdir(parents=True)
            (handmade / "SKILL.md").write_text("# Investment Memo Craft\nCodex-only.\n",
                                               encoding="utf-8")

            done = self._run(script)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertTrue((handmade / "SKILL.md").is_file(),
                            "手写 Codex-only 包被误删")

    def test_real_repo_has_no_generated_orphans(self):
        """仓库现状不变式：codex-skills 里不允许存在「带生成标记但源已删」的目录。"""
        mod = _load()
        source_names = {p.stem for p in (REPO / "skills").glob("*.md")}
        self.assertEqual(
            mod.find_generated_orphans(REPO / "codex-skills", source_names),
            [],
            "真实仓库存在生成孤儿，先重跑 sync-codex-skills.py 清理",
        )


if __name__ == "__main__":
    unittest.main()
