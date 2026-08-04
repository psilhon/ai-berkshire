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

# 历史档案豁免：**file-level allowlist，不是目录豁免**（v3.4.13）。
# 目录级豁免（此前的 `docs/` 整体排除）会让未来新增的 docs 文件自动逃逸扫描——
# 那不是"扫描有边界"，那是"扫描有黑洞"。改为逐文件登记：默认全仓纳入扫描，
# 想豁免必须在此显式列出并写明理由，新增文件一律先红再决定。
LEGACY_ALLOWLIST = {
    "CHANGELOG.md": "发版记录：历史版本条目必须保留当时的真实标识",
    "skills/.darwin-results.tsv": "Darwin 评分档案：历史行按当时的 skill_id 记录",
    "docs/ashare-data-tiered-upgrade-plan.md": "历史规划档案（改名前）",
    "docs/skill-system-analysis.md": "历史分析档案（改名前）",
    "docs/superpowers/plans/2026-07-18-full-company-analysis-review-fixes.md": "dated plan 史实",
    "docs/superpowers/plans/2026-07-19-tushare-market-precedence.md": "dated plan 史实",
    "docs/superpowers/plans/2026-07-19-tushare-verification-source.md": "dated plan 史实",
    "docs/superpowers/plans/2026-07-20-ashare-full-analysis-integrity-fixes.md": "dated plan 史实",
    "docs/superpowers/plans/2026-07-20-full-analysis-true-multiagent-orchestration.md": "dated plan 史实",
    "docs/superpowers/plans/2026-07-23-full-analysis-unattended-reliability.md": "dated plan 史实",
    "docs/superpowers/plans/2026-07-25-full-analysis-quality-closure.md": "dated plan 史实",
    "docs/superpowers/plans/2026-07-30-full-analysis-token-cost.md": "dated plan 史实",
    "docs/superpowers/plans/2026-08-01-full-analysis-wave-scheduling-and-preflight.md": "dated plan 史实",
    "docs/superpowers/specs/2026-07-17-full-company-analysis-skill-design.md": "dated spec 史实",
    "docs/superpowers/specs/2026-07-19-tushare-verification-source-design.md": "dated spec 史实",
    "docs/superpowers/specs/2026-07-23-full-analysis-unattended-reliability-design.md": "dated spec 史实",
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


def _is_binary(raw: bytes) -> bool:
    """git 同款启发式：前 8KB 出现 NUL 即判为二进制（图片/字体等，天然不可文本扫描）。"""
    return b"\x00" in raw[:8192]


def scan_bare_legacy_ids(files: list, root: Path = ROOT) -> tuple:
    """扫描给定文件列表，返回 (含裸旧标识的文件, 无法解码的文本文件)。

    解码策略（v3.4.13）：**strict 解码，绝不 errors="replace"**。
    replace 会把坏字节静默换成 U+FFFD——若坏字节正好落在标识串中间，匹配失效而扫描
    仍报"通过"，等于给自己发假的合规证明。现在：二进制文件（含 NUL）明确跳过，
    其余文件一旦解码失败就作为**独立故障**上报，让坏字节变响而不是变哑。
    """
    offenders, undecodable = [], []
    for rel in files:
        path = root / rel
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if _is_binary(raw):
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            undecodable.append(f"{rel}: {exc}")
            continue
        if has_bare_legacy_id(text):
            offenders.append(rel)
    return offenders, undecodable


class Invariant1RenameConsistency(unittest.TestCase):
    """全仓 tracked 文件中不得存在裸旧编排标识（必须带 -workbuddy 后缀）。

    v3.4.13：扫描面由「白名单目录」改为「全仓 tracked 减去 file-level 豁免」——
    此前 docs/ 被整目录排除，新增的 docs 文件会自动逃逸；现在默认全扫，豁免须登记。
    """

    def _scan_targets(self) -> list:
        tracked = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files"],
            capture_output=True, text=True, check=True).stdout.splitlines()
        return [f for f in tracked
                if f not in LEGACY_ALLOWLIST and f not in GUARD_SELF
                and not f.endswith(".pyc")]

    def test_no_bare_legacy_id_in_tracked_files(self):
        offenders, undecodable = scan_bare_legacy_ids(self._scan_targets())
        self.assertEqual(
            offenders, [],
            f"以下文件仍含裸旧编排标识（改名不变量被破坏）: {offenders}")
        self.assertEqual(
            undecodable, [],
            f"以下文本文件无法按 UTF-8 解码，扫描结果不可信（坏字节可能吞掉标识串）: "
            f"{undecodable}")

    def test_allowlist_entries_all_exist_and_are_still_needed(self):
        """豁免清单必须保持"活的"：条目既不能指向已删文件（腐化），
        也不能豁免一个其实已经合规的文件（借豁免掩盖扫描面收缩）。"""
        stale, needless = [], []
        for rel, reason in LEGACY_ALLOWLIST.items():
            self.assertTrue(reason, f"{rel} 豁免缺理由说明")
            path = ROOT / rel
            if not path.is_file():
                stale.append(rel)
                continue
            if not has_bare_legacy_id(path.read_text(encoding="utf-8")):
                needless.append(rel)
        self.assertEqual(stale, [], f"豁免清单指向不存在的文件（应删除条目）: {stale}")
        self.assertEqual(
            needless, [],
            f"以下文件已无裸旧标识，豁免属多余（应从清单移除，纳入扫描）: {needless}")

    def test_docs_are_actually_scanned(self):
        """回归守护：docs/ 必须真的在扫描面内（此前整目录被排除）。
        非豁免的 docs 文件若混入旧标识，必须被抓。"""
        targets = self._scan_targets()
        scanned_docs = [f for f in targets if f.startswith("docs/")]
        self.assertTrue(scanned_docs, "docs/ 未进入扫描面——目录级黑洞回归")

    # —— 故障注入：判定谓词 + 扫描器（含解码失败上报）都必须真能变红 ——
    # 注：本文件已被 GUARD_SELF 排除出自身扫描，故此处可安全写入裸标识字面量做注入。
    def test_predicate_detects_bare_legacy_id(self):
        self.assertTrue(has_bare_legacy_id("README 仍引用 full-company-analysis 旧名"))
        self.assertFalse(has_bare_legacy_id("已改名 full-company-analysis-workbuddy 合规"))

    def test_scanner_reports_offender_and_undecodable_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bad.md").write_text("引用 full-company-analysis 旧名", encoding="utf-8")
            (root / "ok.md").write_text("full-company-analysis-workbuddy", encoding="utf-8")
            # 无 NUL 但非 UTF-8（GBK 中文）：必须被显式上报，而不是被 replace 静默吞掉
            (root / "gbk.md").write_bytes("旧名说明".encode("gbk"))
            # 含 NUL 的二进制：跳过，不得污染结果
            (root / "font.woff").write_bytes(b"wOFF\x00\x00\x00\x00binary")
            offenders, undecodable = scan_bare_legacy_ids(
                ["bad.md", "ok.md", "gbk.md", "font.woff"], root=root)
            self.assertEqual(offenders, ["bad.md"])
            self.assertEqual(len(undecodable), 1)
            self.assertTrue(undecodable[0].startswith("gbk.md:"), undecodable)


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


FLAG_RX = re.compile(r"--[a-z][a-z0-9-]*")
INLINE_CODE_RX = re.compile(r"`+([^`\n]+)`+")
FENCED_RX = re.compile(r"```.*?```", re.S)
# 裸文散命令的作用域终止符：命令后接中文标点/管道/连接符即视为命令结束，
# 防止把同一行里 git/gh 的 flag 误挂到自有 CLI 上。
PROSE_CUT_RX = re.compile(r"[。；;，,、）)]|\|\||&&|\s\|\s")


def _attribute(cmd: str, claims: dict) -> None:
    """若 cmd 是对某自有 CLI 的真实调用，则把其中所有 --flag 归属该 CLI。"""
    for name in OWN_CLIS:
        if INVOKE_RX[name].search(cmd):
            claims[name].update(FLAG_RX.findall(cmd))


def _doc_flag_claims(doc_text: str) -> dict:
    """从文档提取 {cli文件名: 该 CLI 被文档要求支持的 flag 集合}。

    提取四类上下文（v3.4.13 新增第 3、4 类：**行内完整命令**）：
    1) 围栏代码块内、以 `python3 (scripts|tools)/<cli>.py` 真正调用的命令行（含 \\ 续行）；
    2) 段落级点名某自有 CLI 后、单独用反引号包裹的散列 flag（如 `` `--allow-stale` ``）；
    3) 行内代码 span 里的**完整命令**（如 `` `python3 scripts/full_analysis.py x --y` ``）
       ——此前只抓「单独包裹的 flag」，整条命令写在一对反引号里时其 flag 全部漏检；
    4) 未加反引号、直接写在正文里的完整命令（截断到句读/管道，避免吞掉 git 的 flag）。
    不在任何自有 CLI 上下文中的散列 flag（如 git/gh 的 flag）一律忽略，避免误报。
    """
    claims = {name: set() for name in OWN_CLIS}
    # 1) 围栏代码块：按命令行（含续行）提取，只认 python3 调用
    for block in re.findall(r"```[a-zA-Z]*\n(.*?)```", doc_text, re.S):
        lines = block.split("\n")
        i = 0
        while i < len(lines):
            cmd = lines[i]
            while cmd.rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                cmd = cmd.rstrip()[:-1] + " " + lines[i]
            _attribute(cmd, claims)
            i += 1

    prose = FENCED_RX.sub("", doc_text)

    # 2) 段落级点名 CLI + 单独反引号包裹的 flag
    for para in re.split(r"\n\s*\n", prose):
        for name in OWN_CLIS:
            if name in para:
                claims[name].update(re.findall(r"`(--[a-z][a-z0-9-]*)`", para))

    # 3) 行内代码 span 内的完整命令
    for span in INLINE_CODE_RX.findall(prose):
        _attribute(span, claims)

    # 4) 裸文中的完整命令：从调用点截到句读/管道处，避免误吞同行的他方 flag
    prose_bare = INLINE_CODE_RX.sub(" ", prose)
    for line in prose_bare.split("\n"):
        for name in OWN_CLIS:
            m = INVOKE_RX[name].search(line)
            if not m:
                continue
            tail = line[m.start():]
            cut = PROSE_CUT_RX.search(tail)
            claims[name].update(FLAG_RX.findall(tail[:cut.start()] if cut else tail))
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

    # —— 故障注入 A：完全不存在的 flag（任何 CLI 都没注册）必须被抓 ——
    def test_unregistered_flag_in_doc_is_detected(self):
        doc = "```text\npython3 scripts/full_analysis.py start --company X --ghost-flag\n```"
        self.assertIn(
            ("full_analysis.py", "--ghost-flag"),
            check_documented_flags(doc),
            "文档塞入未注册 flag 必须被守卫捕获（否则守卫空转）")

    # —— 故障注入 B：flag **真实存在但注册在别的 CLI** 时，仍须判缺失 ——
    # v3.4.13 修正：此前本测试也用 --ghost-flag（哪个 CLI 都没有），与 A 完全同义，
    # 根本没验证「归属」这件事（Duplicated Code + 空转）。现在用真实他属 flag。
    def test_flag_registered_in_another_cli_is_still_missing(self):
        borrowed = "--extra-evidence"
        self.assertIn(borrowed, _registered_flags(OWN_CLIS["mk_result_bundle.py"]),
                      "前提失效：该 flag 应真实注册于 mk_result_bundle.py")
        self.assertNotIn(borrowed, _registered_flags(OWN_CLIS["full_analysis.py"]),
                         "前提失效：该 flag 不应注册于 full_analysis.py")
        doc = f"```text\npython3 scripts/full_analysis.py start {borrowed} x.json\n```"
        self.assertIn(("full_analysis.py", borrowed), check_documented_flags(doc),
                      "flag 注册在别的 CLI 不等于本 CLI 支持——归属校验必须判缺失")
        # 同一个 flag 归属正确的 CLI 时必须放行（证明上面的红不是"一律判红"）
        ok_doc = (f"```text\npython3 scripts/mk_result_bundle.py --run-root r "
                  f"{borrowed} x.json\n```")
        self.assertEqual(check_documented_flags(ok_doc), [])

    # —— 故障注入 C：行内完整命令（非围栏代码块）也必须被解析 ——
    def test_inline_full_command_flags_are_extracted(self):
        inline_span = "运行 `python3 scripts/full_analysis.py doctor --ghost-inline` 即可。"
        self.assertIn(("full_analysis.py", "--ghost-inline"),
                      check_documented_flags(inline_span),
                      "行内代码里的完整命令其 flag 必须被抓（此前整条命令包在反引号里会漏检）")
        bare_prose = "直接执行 python3 scripts/full_analysis.py doctor --ghost-bare 观察输出"
        self.assertIn(("full_analysis.py", "--ghost-bare"),
                      check_documented_flags(bare_prose),
                      "裸文里的完整命令其 flag 必须被抓")

    def test_other_tools_flags_on_same_line_are_not_misattributed(self):
        """截断规则防误报：同一行里 git 的 flag 不得被挂到自有 CLI 上。"""
        doc = ("先 `python3 scripts/full_analysis.py doctor`，再 `git tag --list`；"
               "最后 gh release create --notes-file x")
        self.assertEqual(check_documented_flags(doc), [])

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
