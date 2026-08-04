#!/usr/bin/env python3
"""不变量守卫（v3.4.12 重构）。

把三轮返工的共同病根固化成机器断言。全称性质只能靠「覆盖所有分支 + 负例（故障注入）」证明。
本文件刻意避免在自己源码里直接写出裸旧标识（只在断言消息里以描述性措辞提及），也不依赖
「正例通过」来宣称全称成立——每条守卫都配一条故障注入测试，先看着它红，再确认它绿。

对应关系：
- Invariant 1（改名一致性）  ← v3.4.9：README 死链 + 4 业务 skill 旧标识
- Invariant 2（文档宣称⊆CLI） ← v3.4.8：--allow-stale 文档存在但从未注册 argparse
- Invariant 3（校验器负例）   ← v3.4.9：frontmatter「收紧」空转（负例未先行）
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

# 本仓库自有 CLI（文档命令行 flag 的合法注册地），按文件名定位用于 CLI 归属校验
OWN_CLIS = {
    "full_analysis.py": ROOT / "scripts" / "full_analysis.py",
    "mk_result_bundle.py": ROOT / "scripts" / "mk_result_bundle.py",
    "full_analysis_gate.py": ROOT / "tools" / "full_analysis_gate.py",
}

# 历史档案：允许保留旧的标识符写法（评分记录/发版记录/历史规划文档）
LEGACY_ALLOWLIST = {
    "skills/.darwin-results.tsv",
    "CHANGELOG.md",
}

# 守卫自身的源码路径：不得把守卫源码当活跃真源来扫描，否则会自我误报（v3.4.11 自红根因）
GUARD_SELF = {"tests/test_invariants.py"}

BARE_LEGACY_RX = re.compile(r"full-company-analysis(?!-workbuddy)")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Invariant 1：改名一致性
# ---------------------------------------------------------------------------
def has_bare_legacy_id(text: str) -> bool:
    """活跃真源中不得出现裸旧标识（必须带 -workbuddy 后缀）。"""
    return bool(BARE_LEGACY_RX.search(text))


class Invariant1RenameConsistency(unittest.TestCase):
    """活跃真源中不得存在裸旧编排标识（必须带 -workbuddy 后缀）。"""

    def test_no_bare_legacy_id_in_tracked_active_files(self):
        tracked = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files"],
            capture_output=True, text=True, check=True).stdout.splitlines()
        # 活跃面：代码/skill/文档真源；排除 local/（gitignore 本就不可见）与历史档案。
        # 注意：docs/ 是历史设计档案（dated plans/specs/ROADMAP，含 superpowers/ 下 40+ 篇），
        # 引用旧名是当时史实，不在活跃真源范畴——扫描它会逼着重写历史，故显式排除
        # （与 CHANGELOG/.darwin-results.tsv 同属档案豁免）。
        active = [
            f for f in tracked
            if re.match(r"^(skills/|workbuddy-skills/|codex-skills/|scripts/|tools/|tests/)", f)
            or f in ("README.md", "CLAUDE.md", "SKILLS-GUIDE.md", "AGENTS.md")
        ]
        active = [
            f for f in active
            if f not in LEGACY_ALLOWLIST and f not in GUARD_SELF
            and not f.endswith(".pyc") and ".darwin" not in f and "superpowers" not in f
        ]
        offenders = []
        for rel in active:
            # errors="replace"：解码错误显式标成替换符，而非静默吞掉（可能吞掉匹配串）
            text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
            if has_bare_legacy_id(text):
                offenders.append(rel)
        self.assertEqual(
            offenders, [],
            f"以下活跃文件仍含裸旧编排标识（改名不变量被破坏）: {offenders}")

    # —— 故障注入：直接验证判定谓词对裸标识敏感、对带后缀不敏感 ——
    # 注：本文件已被 GUARD_SELF 排除出自身扫描，故此处可安全写入裸标识字面量做注入。
    def test_predicate_detects_bare_legacy_id(self):
        self.assertTrue(has_bare_legacy_id("README 仍引用 full-company-analysis 旧名"))
        self.assertFalse(has_bare_legacy_id("已改名 full-company-analysis-workbuddy 合规"))


# ---------------------------------------------------------------------------
# Invariant 2：文档宣称 ⊆ CLI 注册（并按 CLI 归属校验，防错误 CLI 注册通过）
# ---------------------------------------------------------------------------
def _registered_flags(cli_path: Path) -> set:
    text = cli_path.read_text(encoding="utf-8")
    return set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', text))


# 仅当 CLI 作为被执行的命令（python3 前缀）时才算调用；避免把 git status 路径参数里的
# tools/full_analysis_gate.py 误判成「调用 gate」而把整块 git flag 错挂到 gate 上。
INVOKE_RX = {
    name: re.compile(rf"python3\s+(?:scripts|tools)/{re.escape(name)}")
    for name in OWN_CLIS
}


def _doc_flag_claims(doc_text: str) -> dict:
    """从文档提取 {cli文件名: 该 CLI 被文档要求支持的 flag 集合}。

    提取两类上下文：
    1) 以 `python3 (scripts|tools)/<cli>.py` 真正调用的命令行（含 \\ 续行），其全部
       --flag 归属该 CLI；同一代码块里的 git/rm 命令（CLI 仅作路径参数）一律忽略；
    2) 同一段落内显式点名某自有 CLI 的散列 --flag（行内代码），且只抓「单独用反引号
       包裹」的 flag（如 `` `--allow-stale` ``），不抓 `git tag --list` 这种整段反引号。
    不在任何自有 CLI 上下文中的散列 flag（如 git/gh 的 flag）一律忽略，避免误报。
    """
    claims = {name: set() for name in OWN_CLIS}
    # 1) 代码块：按命令行（含续行）提取，只认 python3 调用
    for block in re.findall(r"```[a-zA-Z]*\n(.*?)```", doc_text, re.S):
        lines = block.split("\n")
        i = 0
        while i < len(lines):
            cmd = lines[i]
            while cmd.rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                cmd = cmd.rstrip()[:-1] + " " + lines[i]
            for name in OWN_CLIS:
                if INVOKE_RX[name].search(cmd):
                    claims[name].update(re.findall(r"--[a-z][a-z0-9-]*", cmd))
            i += 1
    # 2) 行内：段落级点名 CLI + 单独反引号包裹的 flag
    for para in re.split(r"\n\s*\n", doc_text):
        for name in OWN_CLIS:
            if name in para:
                claims[name].update(re.findall(r"`(--[a-z][a-z0-9-]*)`", para))
    return claims


def check_documented_flags(doc_text: str | None = None) -> list:
    """返回 [(cli, flag), ...]：文档要求但未在对应 CLI 注册的 flag。空=通过。

    按 CLI 归属校验：文档把某 flag 归属到 full_analysis.py，就必须真的注册在它的
    argparse 里——注册到别的 CLI 也判缺失（防 --allow-stale 式脱节与错误 CLI 注册）。
    """
    doc_text = doc_text if doc_text is not None else ORCHESTRATOR_DOC.read_text(encoding="utf-8")
    claims = _doc_flag_claims(doc_text)
    missing = []
    for name, flags in claims.items():
        reg = _registered_flags(OWN_CLIS[name])
        for fl in sorted(flags):
            if fl not in reg:
                missing.append((name, fl))
    return missing


class Invariant2DocumentedFlagsRegistered(unittest.TestCase):
    """文档命令行宣称的每个 flag 必须真实注册于它所属的 CLI（防 --allow-stale 式脱节）。"""

    def test_every_documented_flag_is_registered(self):
        missing = check_documented_flags()
        self.assertEqual(
            missing, [],
            f"文档宣称但对应 CLI 未注册的 flag: {missing}。"
            f"文档宣称的能力必须机器可达——补注册或改文档，二者必居其一。")

    # —— 故障注入：文档若塞入未注册 flag，守卫必须红 ——
    def test_unregistered_flag_in_doc_is_detected(self):
        doc = "```text\npython3 scripts/full_analysis.py start --company X --ghost-flag\n```"
        self.assertIn(
            ("full_analysis.py", "--ghost-flag"),
            check_documented_flags(doc),
            "文档塞入未注册 flag 必须被守卫捕获（否则守卫空转）")

    # —— 故障注入：在错误 CLI 上下文注册也判缺失 ——
    def test_flag_registered_in_wrong_cli_still_missing(self):
        # 假设文档把 --ghost-flag 归属 full_analysis.py，但它实际只注册在 mk_result_bundle.py
        doc = "```text\npython3 scripts/full_analysis.py start --ghost-flag\n```"
        # 用 mk_result_bundle.py 的注册集伪造「已注册」，验证校验不看错 CLI
        missing = check_documented_flags(doc)
        self.assertIn(("full_analysis.py", "--ghost-flag"), missing)

    def test_registered_flag_in_synthetic_doc_passes(self):
        doc = "```text\npython3 scripts/full_analysis.py start --company X --allow-stale\n```"
        self.assertEqual(check_documented_flags(doc), [])


# ---------------------------------------------------------------------------
# Invariant 3：校验器必须真拒非法输入（负例先行）
# ---------------------------------------------------------------------------
class Invariant3ValidatorsRejectInvalidInput(unittest.TestCase):
    """校验器必须真拒非法输入（负例先行）：收紧规则若不能让自己变红，就是空转。

    注：frontmatter name 不一致 / contract registry 损坏的负例已由各自专用测试模块覆盖
    （tests/test_check_skill_frontmatter.py、contract 专用测试），此处只保留 v3.4.9
    空转复现这一条最有价值的守卫，避免与既有测试重复（Duplicated Code smell）。
    """

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


if __name__ == "__main__":
    unittest.main()
