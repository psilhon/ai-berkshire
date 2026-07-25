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
    REPO / "skills/full-company-analysis.md",
    REPO / "workbuddy-skills/full-company-analysis/SKILL.md",
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
        text = (REPO / "README.md").read_text(encoding="utf-8")
        for rel in re.findall(r"\]\((skills/[^)#]+\.md)\)", text):
            self.assertTrue((REPO / rel).is_file(), rel)

    def test_full_analysis_workflow_registers_summary_before_audit(self):
        text = (
            REPO / "skills/full-company-analysis.md"
        ).read_text(encoding="utf-8")
        register_at = text.index("register-summary")
        audit_at = text.index(" audit --run-root", register_at)
        finalize_at = text.index("finalize", audit_at)
        self.assertLess(register_at, audit_at)
        self.assertLess(audit_at, finalize_at)
        self.assertNotIn("html-express", text)
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


if __name__ == "__main__":
    unittest.main()
