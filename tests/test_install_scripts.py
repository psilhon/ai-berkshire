#!/usr/bin/env python3
"""Targeted-install tests for the dual-platform install scripts (design v1.4 §6.1.7/§17).

All installs go to tempfile destinations via env overrides (CODEX_HOME /
CLAUDE_COMMANDS_DIR); the real ~/.codex and ~/.claude are never touched.
"""

import filecmp
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODEX_INSTALL = ROOT / "scripts" / "install-codex-skills.sh"
CLAUDE_INSTALL = ROOT / "scripts" / "install-claude-commands.sh"
SKILL_NAME = "full-company-analysis"


def run_script(script, *args, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
    )


def assert_dirs_identical(testcase, left, right):
    cmp = filecmp.dircmp(left, right)
    testcase.assertEqual(cmp.left_only, [], msg=f"{left} 多出: {cmp.left_only}")
    testcase.assertEqual(cmp.right_only, [], msg=f"{right} 多出: {cmp.right_only}")
    testcase.assertEqual(cmp.diff_files, [], msg=f"内容不一致: {cmp.diff_files}")
    testcase.assertEqual(cmp.funny_files, [], msg=f"无法比较: {cmp.funny_files}")
    for sub in cmp.common_dirs:
        assert_dirs_identical(testcase, Path(left) / sub, Path(right) / sub)


class TestCodexOnlyInstall(unittest.TestCase):
    def test_only_installs_single_skill_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_script(
                CODEX_INSTALL,
                "--only",
                SKILL_NAME,
                env_extra={"CODEX_HOME": tmp},
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            dest = Path(tmp) / "skills"
            installed = sorted(p.name for p in dest.iterdir())
            self.assertEqual(installed, [SKILL_NAME])
            source = ROOT / "codex-skills" / SKILL_NAME
            self.assertTrue(source.is_dir(), msg="codex-skills 源目录不存在")
            assert_dirs_identical(self, source, dest / SKILL_NAME)

    def test_only_unknown_skill_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_script(
                CODEX_INSTALL,
                "--only",
                "no-such-skill-xyz",
                env_extra={"CODEX_HOME": tmp},
            )
            self.assertNotEqual(proc.returncode, 0)


class TestClaudeOnlyInstall(unittest.TestCase):
    def test_only_installs_single_command_identical_to_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "commands"
            proc = run_script(
                CLAUDE_INSTALL,
                "--only",
                SKILL_NAME,
                env_extra={"CLAUDE_COMMANDS_DIR": str(dest)},
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            installed = sorted(p.name for p in dest.iterdir())
            self.assertEqual(installed, [f"{SKILL_NAME}.md"])
            source = ROOT / "skills" / f"{SKILL_NAME}.md"
            self.assertTrue(source.is_file(), msg="skills 源文件不存在")
            self.assertTrue(
                filecmp.cmp(source, dest / f"{SKILL_NAME}.md", shallow=False),
                msg="安装副本与源不逐字节一致",
            )

    def test_only_unknown_skill_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "commands"
            proc = run_script(
                CLAUDE_INSTALL,
                "--only",
                "no-such-skill-xyz",
                env_extra={"CLAUDE_COMMANDS_DIR": str(dest)},
            )
            self.assertNotEqual(proc.returncode, 0)


class TestFullInstallUnchanged(unittest.TestCase):
    """无 --only 时保持全量安装行为。"""

    def test_codex_full_install_count_matches_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_script(CODEX_INSTALL, env_extra={"CODEX_HOME": tmp})
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            dest = Path(tmp) / "skills"
            installed = sorted(p.name for p in dest.iterdir() if p.is_dir())
            sources = sorted(
                p.name for p in (ROOT / "codex-skills").iterdir() if p.is_dir()
            )
            self.assertEqual(installed, sources)

    def test_claude_full_install_count_matches_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "commands"
            proc = run_script(
                CLAUDE_INSTALL, env_extra={"CLAUDE_COMMANDS_DIR": str(dest)}
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            installed = sorted(p.name for p in dest.glob("*.md"))
            sources = sorted(p.name for p in (ROOT / "skills").glob("*.md"))
            self.assertEqual(installed, sources)


if __name__ == "__main__":
    unittest.main()
