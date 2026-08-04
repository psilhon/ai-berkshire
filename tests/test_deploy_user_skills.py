"""用户级 Codex 副本部署器回归测试（v3.4.13）。

守护的病根：仓库三副本有 check.sh 守着，**用户级部署副本此前零机制**，
发版后靠手工拷贝 → 每轮 review 都能翻出"用户级副本文案落后"。本测试确保
部署器本身是确定性的、--check 能真的把漂移变红（而不是永远打印"一致"）。
"""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "deploy-user-skills.py"


def _load():
    spec = importlib.util.spec_from_file_location("deploy_user_skills", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DeployUserSkillsTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def _fake_src(self, root: Path, names=("alpha", "beta")) -> Path:
        src = root / "codex-skills"
        for n in names:
            (src / n).mkdir(parents=True)
            (src / n / "SKILL.md").write_text(f"# {n}\n内容 {n}\n", encoding="utf-8")
        return src

    def test_plan_is_deterministic_and_name_sorted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = self._fake_src(root, ("gamma", "alpha", "beta"))
            dest = root / "dest"
            items = self.mod.plan(src, dest)
            self.assertEqual([n for n, _, _ in items], ["alpha", "beta", "gamma"])
            self.assertEqual(items, self.mod.plan(src, dest), "同输入必须同输出")
            self.assertEqual(items[0][1], dest / "alpha" / "SKILL.md")

    def test_drifted_detects_missing_and_modified(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = self._fake_src(root)
            dest = root / "dest"
            items = self.mod.plan(src, dest)
            self.assertEqual(self.mod.drifted(items), ["alpha", "beta"], "全缺失应全报")
            # 只落一个：另一个仍应报漂移
            (dest / "alpha").mkdir(parents=True)
            (dest / "alpha" / "SKILL.md").write_text("# alpha\n内容 alpha\n", encoding="utf-8")
            self.assertEqual(self.mod.drifted(items), ["beta"])
            # 内容被改：必须重新变红（此前用户级副本正是这种"存在但落后"）
            (dest / "alpha" / "SKILL.md").write_text("# alpha\n被手改的旧文案\n",
                                                     encoding="utf-8")
            self.assertEqual(self.mod.drifted(items), ["alpha", "beta"])

    def _run(self, dest: Path, check=False):
        argv = [sys.executable, str(SCRIPT), "--dest", str(dest)]
        if check:
            argv.append("--check")
        return subprocess.run(argv, capture_output=True, text=True)

    def test_cli_check_red_then_deploy_then_green(self):
        """端到端：空目标 --check 必须 exit 1；部署后 --check 必须 exit 0。
        （若 --check 永远返回 0，这个守卫就是摆设——先看它红再看它绿。）"""
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "codex-skills"
            red = self._run(dest, check=True)
            self.assertEqual(red.returncode, 1, red.stderr)
            self.assertIn("漂移", red.stderr)

            done = self._run(dest)
            self.assertEqual(done.returncode, 0, done.stderr)

            green = self._run(dest, check=True)
            self.assertEqual(green.returncode, 0, green.stderr)
            self.assertIn("一致", green.stdout)

            # 手改一个副本后必须重新变红
            victim = next(dest.glob("*/SKILL.md"))
            victim.write_text("手改的旧文案", encoding="utf-8")
            self.assertEqual(self._run(dest, check=True).returncode, 1)

    def test_deploy_does_not_touch_foreign_skills(self):
        """只覆盖仓库拥有的 skill 名，用户自建的其它 skill 不得被删改。"""
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "codex-skills"
            (dest / "my-own-skill").mkdir(parents=True)
            keep = dest / "my-own-skill" / "SKILL.md"
            keep.write_text("用户自建，勿动", encoding="utf-8")
            self.assertEqual(self._run(dest).returncode, 0)
            self.assertTrue(keep.is_file(), "用户自建 skill 被删除")
            self.assertEqual(keep.read_text(encoding="utf-8"), "用户自建，勿动")

    def test_plan_and_drift_include_skill_assets(self):
        """v3.4.14 修复：plan 必须覆盖 skill 目录下的附属资产（如 agents/openai.yaml），
        而非仅 SKILL.md；否则资产静默缺失却 --check 仍报绿。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "codex-skills"
            (src / "alpha").mkdir(parents=True)
            (src / "alpha" / "SKILL.md").write_text("# alpha\n", encoding="utf-8")
            (src / "alpha" / "agents").mkdir(parents=True)
            (src / "alpha" / "agents" / "openai.yaml").write_text("model: gpt\n", encoding="utf-8")
            dest = root / "dest"
            items = self.mod.plan(src, dest)
            paths = [str(p) for _, p, _ in items]
            self.assertIn(str(dest / "alpha" / "SKILL.md"), paths)
            self.assertIn(str(dest / "alpha" / "agents" / "openai.yaml"), paths)
            # 缺失时全部报漂移
            self.assertEqual(set(self.mod.drifted(items)), {"alpha"})
            # 落盘（含资产）后应无漂移
            for _, p, c in items:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(c)
            self.assertEqual(self.mod.drifted(items), [])
            # 资产被改 → 重新变红（此前这类"存在但落后"正是盲区）
            (dest / "alpha" / "agents" / "openai.yaml").write_text("model: hacked\n")
            self.assertEqual(set(self.mod.drifted(items)), {"alpha"})

    def test_repo_source_is_populated(self):
        """源目录必须真有内容——否则部署器会"成功部署 0 个"并报绿。
        且必须包含 skill 的附属资产（如 investment-memo-craft/agents/openai.yaml）。"""
        items = self.mod.plan(REPO / "codex-skills", Path("/tmp/never-written"))
        self.assertGreaterEqual(len(items), 15, "codex-skills 源副本数量异常（含附属资产应 >14）")
        asset_paths = [str(p) for _, p, _ in items]
        self.assertTrue(
            any(p.endswith("investment-memo-craft/agents/openai.yaml") for p in asset_paths),
            "investment-memo-craft 的附属资产 agents/openai.yaml 未被纳入部署")


if __name__ == "__main__":
    unittest.main()
