#!/usr/bin/env python3
"""check-skill-frontmatter.py 回归测试（v3.4.10）。

防漏检：v3.4.9 的"收紧"实测仍可绕过——删除 platform: workbuddy 后
name==stem 不触发 name 分支，校验照样通过。本版改为独立的双向
platform 规则并固化负例测试。
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-skill-frontmatter.py"

VALID_BASE = """---
name: {name}
description: 测试 skill
owner: tester
category: 编排层
maturity: stable
review-cadence: per-release
{extra}---

# 测试
"""


def load_checker():
    spec = importlib.util.spec_from_file_location("check_skill_frontmatter", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_skill(directory: Path, filename: str, name: str, platform: str | None):
    extra = f"platform: {platform}\n" if platform else ""
    (directory / filename).write_text(
        VALID_BASE.format(name=name, extra=extra), encoding="utf-8")


class FrontmatterPlatformBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.skills = Path(self.temp.name)
        self.mod = load_checker()
        self.mod.CLAUDE_SKILLS = self.skills

    def tearDown(self):
        self.temp.cleanup()

    def run_main(self) -> int:
        return self.mod.main()

    def test_workbuddy_file_with_platform_passes(self):
        write_skill(self.skills, "foo-workbuddy.md", "foo-workbuddy", "workbuddy")
        self.assertEqual(self.run_main(), 0)

    def test_workbuddy_file_missing_platform_fails(self):
        # v3.4.9 漏检复现用例：name==stem 时旧逻辑直接放行
        write_skill(self.skills, "foo-workbuddy.md", "foo-workbuddy", None)
        self.assertNotEqual(self.run_main(), 0)

    def test_workbuddy_file_wrong_platform_fails(self):
        write_skill(self.skills, "foo-workbuddy.md", "foo-workbuddy", "claude")
        self.assertNotEqual(self.run_main(), 0)

    def test_platform_declared_without_suffix_fails(self):
        # 反向约束：声明 platform: workbuddy 的文件名必须带 -workbuddy 后缀
        write_skill(self.skills, "bar.md", "bar", "workbuddy")
        self.assertNotEqual(self.run_main(), 0)

    def test_plain_skill_without_platform_passes(self):
        write_skill(self.skills, "baz.md", "baz", None)
        self.assertEqual(self.run_main(), 0)

    def test_name_mismatch_no_longer_exempted(self):
        # v3.4.10：name 严格等于文件名，无 -workbuddy 豁免后门
        write_skill(self.skills, "qux.md", "qux-other", None)
        self.assertNotEqual(self.run_main(), 0)

    def test_repo_skills_pass(self):
        # 仓库真实 skills/ 全量通过（含编排 skill 的平台绑定）
        self.mod.CLAUDE_SKILLS = ROOT / "skills"
        self.assertEqual(self.mod.main(), 0)


if __name__ == "__main__":
    unittest.main()
