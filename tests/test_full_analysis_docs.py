import re
import subprocess
import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ACTIVE_DOCS = (
    REPO / "README.md",
    REPO / "CLAUDE.md",
    REPO / "SKILLS-GUIDE.md",
    REPO / "skills/full-company-analysis-workbuddy.md",
    REPO / "workbuddy-skills/full-company-analysis-workbuddy/SKILL.md",
)
REMOVED_SKILLS = (
    "deep-company-series",
    "dyp-ask",
    "earnings-team",
    "portfolio-review",
    "private-company-research",
    "thesis-drift",
    "wechat-article",
)


class FullAnalysisDocumentationTests(unittest.TestCase):
    def test_active_docs_have_current_skill_count_and_no_removed_entries(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in ACTIVE_DOCS
        )
        self.assertNotRegex(combined, r"20\s*项|20\s*个|21\s*个")
        for skill_id in REMOVED_SKILLS:
            self.assertNotIn(skill_id, combined)

    def test_readme_skill_links_resolve(self):
        # v3.4.10：覆盖 README 全部仓库相对 .md 链接（不只 skills/ 前缀）——
        # v3.4.9 改名后 workbuddy-skills/ 旧路径死链正是只匹配 skills/ 前缀漏掉的。
        text = (REPO / "README.md").read_text(encoding="utf-8")
        links = re.findall(r"\]\(([^)#\s]+\.md)\)", text)
        self.assertTrue(links, "README 应至少包含一个相对 .md 链接")
        for rel in links:
            if rel.startswith(("http://", "https://")):
                continue
            # local/ 整体不入库（.gitignore），CI 环境无法解析其链接；
            # 入库目录（skills/ workbuddy-skills/ docs/ 等）的死链必须报错。
            if rel.startswith("local/"):
                continue
            self.assertTrue((REPO / rel).is_file(), f"README 死链: {rel}")

    def test_active_docs_use_canonical_company_run_path(self):
        for path in (REPO / "README.md", REPO / "CLAUDE.md"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("local/company/", text, path.name)
            self.assertIn("local/Company/", text, path.name)

    def test_full_analysis_workflow_registers_summary_before_audit(self):
        text = (
            REPO / "skills/full-company-analysis-workbuddy.md"
        ).read_text(encoding="utf-8")
        register_at = text.index("register-summary")
        audit_at = text.index(" audit --run-root", register_at)
        finalize_at = text.index("finalize", audit_at)
        self.assertLess(register_at, audit_at)
        self.assertLess(audit_at, finalize_at)
        # HTML 总结报告是 Gate 之外的派生展示件（2026-07-26 升级：固化为确定性渲染）。
        # 不再派 html-express Agent，改由 Gate 内置确定性渲染器（render-html 命令 +
        # tools/full_analysis_html.py）生成，保证品质零方差。必须明确围栏：它是 markdown
        # 的派生件、不参与 Gate 管线，且不得声称"不进入 Gate"（该措辞会把 Gate 产物与
        # 展示件混为一谈）。
        self.assertIn("HTML 版总结报告", text)
        self.assertIn("render-html", text)
        self.assertIn("确定性渲染", text)
        self.assertIn("不参与 audit/review/finalize", text)
        self.assertIn("派生展示件", text)
        self.assertNotIn("不进入 Gate", text)

    def test_legacy_batch_generator_fails_loudly_without_claiming_completion(self):
        completed = subprocess.run(
            [sys.executable, str(REPO / "scripts/gen_batch_reports.py")],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        output = completed.stdout + completed.stderr
        self.assertIn("已停用", output)
        self.assertNotIn("COMPLETE", output)
        self.assertNotIn("PASS_WITH_LIMITATIONS", output)

    def test_investment_checklist_does_not_duplicate_contract_byte_floor(self):
        text = (REPO / "skills/investment-checklist.md").read_text(
            encoding="utf-8")
        self.assertNotIn("不少于 3000 字节", text)
        self.assertIn("Contract", text)


if __name__ == "__main__":
    unittest.main()
