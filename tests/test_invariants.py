#!/usr/bin/env python3
"""不变量守卫（v3.4.11）：把三轮返工的共同病根固化成机器断言。

病根：修复只改"被点名的那一行"，然后用正例通过宣称"全称性质成立"。
全称性质只能靠覆盖所有分支 + 负例来证明。本文件把三个已发生的返工
各钉成一条机器断言，同类错误在提交时被 check.sh 拦截，而非等 review。

对应关系：
- Invariant 1（改名一致性）  ← v3.4.9：README 死链 + 4 业务 skill 旧标识
- Invariant 2（文档宣称⊆CLI） ← v3.4.8：--allow-stale 文档存在但从未注册 argparse
- Invariant 3（校验器负例）   ← v3.4.9：frontmatter"收紧"空转（负例未先行）
"""

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DOC = ROOT / "skills" / "full-company-analysis-workbuddy.md"

# 本仓库自有 CLI（文档命令行 flag 的合法注册地）
OWN_CLIS = [
    ROOT / "scripts" / "full_analysis.py",
    ROOT / "scripts" / "mk_result_bundle.py",
    ROOT / "tools" / "full_analysis_gate.py",
]

# 历史档案：允许保留旧标识（评分记录/发版记录/历史规划文档）
LEGACY_ALLOWLIST = {
    "skills/.darwin-results.tsv",   # Darwin 评分历史档案
    "CHANGELOG.md",                 # 发版记录按历史原样保留
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Invariant1RenameConsistency(unittest.TestCase):
    """活跃真源中不得存在裸旧标识 full-company-analysis（必须带 -workbuddy）。"""

    def test_no_bare_legacy_id_in_tracked_active_files(self):
        tracked = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files"],
            capture_output=True, text=True, check=True).stdout.splitlines()
        # 活跃面：代码/skill/文档真源；排除 local/（gitignore 本就不可见）与历史档案
        active = [
            f for f in tracked
            if re.match(r"^(skills/|workbuddy-skills/|codex-skills/|scripts/|tools/|tests/)", f)
            or f in ("README.md", "CLAUDE.md", "SKILLS-GUIDE.md", "AGENTS.md")
        ]
        active = [f for f in active if f not in LEGACY_ALLOWLIST
                  and not f.endswith(".pyc")
                  and ".darwin" not in f
                  and "superpowers" not in f]
        offenders = []
        for rel in active:
            text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
            # 裸旧标识 = full-company-analysis 后面不是 -workbuddy
            if re.search(r"full-company-analysis(?!-workbuddy)", text):
                offenders.append(rel)
        self.assertEqual(
            offenders, [],
            f"以下活跃文件仍含裸旧标识 full-company-analysis（改名不变量被破坏）: {offenders}")


class Invariant2DocumentedFlagsRegistered(unittest.TestCase):
    """文档命令行宣称的每个 flag 必须真实注册于某个自有 CLI（防 --allow-stale 式脱节）。"""

    @staticmethod
    def _doc_command_flags() -> set:
        text = ORCHESTRATOR_DOC.read_text(encoding="utf-8")
        flags = set()
        # 匹配自有 CLI 命令行（含 `python3 scripts/full_analysis.py ...` 与 \ 续行）
        pattern = re.compile(r"python3\s+(?:scripts|tools)/[\w./-]+\.py")
        for match in pattern.finditer(text):
            # 取该行起到下一个代码块围栏/空行为止的命令文本（处理续行）
            start = match.start()
            seg = text[start:start + 800]
            seg = seg.split("\n\n")[0]
            seg = re.sub(r"\\\n", " ", seg)          # 合并续行
            seg = seg.split("\n```")[0]               # 代码块结束即止
            seg = seg.split("\n>")[0]
            flags.update(re.findall(r"--[a-z][a-z0-9-]*", seg))
        return flags

    @staticmethod
    def _registered_flags() -> set:
        flags = set()
        for cli in OWN_CLIS:
            text = cli.read_text(encoding="utf-8")
            flags.update(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', text))
        return flags

    def test_every_documented_flag_is_registered(self):
        doc_flags = self._doc_command_flags()
        self.assertTrue(doc_flags, "文档应至少包含一条自有 CLI 命令行（提取规则失效？）")
        registered = self._registered_flags()
        missing = sorted(doc_flags - registered)
        self.assertEqual(
            missing, [],
            f"文档宣称但任何自有 CLI 都未注册的 flag: {missing}。"
            f"文档宣称的能力必须机器可达——补注册或改文档，二者必居其一。")


class Invariant3ValidatorsRejectInvalidInput(unittest.TestCase):
    """校验器必须真拒非法输入（负例先行）：收紧规则若不能让自己变红，就是空转。"""

    def test_frontmatter_validator_rejects_missing_platform(self):
        # v3.4.9 空转复现用例：-workbuddy 文件删 platform 必须被拒
        checker = load_module("cfm", ROOT / "scripts" / "check-skill-frontmatter.py")
        with tempfile.TemporaryDirectory() as tmp:
            skills = Path(tmp)
            (skills / "foo-workbuddy.md").write_text(
                "---\nname: foo-workbuddy\ndescription: x\nowner: t\n"
                "category: 编排层\nmaturity: stable\nreview-cadence: per-release\n"
                "---\n\n# x\n", encoding="utf-8")
            checker.CLAUDE_SKILLS = skills
            self.assertNotEqual(checker.main(), 0, "缺 platform 的 -workbuddy skill 必须被拒")

    def test_frontmatter_validator_rejects_name_mismatch(self):
        checker = load_module("cfm2", ROOT / "scripts" / "check-skill-frontmatter.py")
        with tempfile.TemporaryDirectory() as tmp:
            skills = Path(tmp)
            (skills / "bar.md").write_text(
                "---\nname: not-bar\ndescription: x\nowner: t\n"
                "category: 编排层\nmaturity: stable\nreview-cadence: per-release\n"
                "---\n\n# x\n", encoding="utf-8")
            checker.CLAUDE_SKILLS = skills
            self.assertNotEqual(checker.main(), 0, "name 与文件名不一致必须被拒")

    def test_contract_validator_rejects_broken_registry(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            checker = load_module(
                "cfc", ROOT / "scripts" / "check-full-analysis-contract.py")
            with tempfile.TemporaryDirectory() as tmp:
                broken = Path(tmp) / "contract.json"
                broken.write_text(json.dumps({"schema_version": "wrong"}),
                                  encoding="utf-8")
                errors = checker.validate(broken, ROOT)
                self.assertTrue(errors, "非法 registry 必须产出校验错误")
        finally:
            sys.path.pop(0)


if __name__ == "__main__":
    unittest.main()
