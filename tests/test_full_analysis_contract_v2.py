import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CONTRACT = REPO / "tools" / "full_analysis_contract.json"
VALIDATOR = REPO / "scripts" / "check-full-analysis-contract.py"

EXPECTED_SKILLS = {
    "ashare-data",
    "financial-data",
    "quality-screen",
    "investment-checklist",
    "investment-research",
    "investment-team",
    "management-deep-dive",
    "earnings-review",
    "earnings-team",
    "industry-research",
    "industry-funnel",
    "bottleneck-hunter",
    "news-pulse",
    "thesis-tracker",
    "thesis-drift",
    "portfolio-review",
    "private-company-research",
    "deep-company-series",
    "dyp-ask",
    "wechat-article",
}

MACHINE_SECTIONS = {
    "data_cutoff",
    "sources_scope",
    "limitations",
    "research_disclaimer",
    "core_conclusion",
    "downstream_evidence",
    "contract_calculations",
}


def load_contract():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


class ContractV2Tests(unittest.TestCase):
    def run_validator(self, registry):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "contract.json"
            path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    str(VALIDATOR),
                    "--registry",
                    str(path),
                    "--repo-root",
                    str(REPO),
                ],
                capture_output=True,
                text=True,
            )

    def assert_v2_header(self, registry):
        self.assertEqual(registry.get("schema_version"), "full-analysis-contract/v2")
        self.assertEqual(registry.get("manifest_schema_version"), "full-analysis-manifest/v2")
        self.assertEqual(registry.get("result_schema_version"), "result-schema/v1")

    def test_real_registry_declares_exact_v2_contract_and_twenty_skills(self):
        registry = load_contract()

        self.assert_v2_header(registry)
        self.assertEqual(
            {item["skill_id"] for item in registry["skills"]},
            EXPECTED_SKILLS,
        )
        self.assertEqual(len(registry["skills"]), 20)

    def test_real_registry_uses_structured_sections_not_generic_title_arrays(self):
        registry = load_contract()
        self.assert_v2_header(registry)

        self.assertNotIn("generic_required_sections", registry)
        for item in registry["skills"]:
            self.assertNotIn("required_sections", item)
            sections = item["sections"]
            ids = [section["section_id"] for section in sections]
            self.assertEqual(len(ids), len(set(ids)), item["skill_id"])
            self.assertTrue(MACHINE_SECTIONS <= set(ids), item["skill_id"])
            for section in sections:
                self.assertRegex(section["section_id"], r"^[a-z][a-z0-9_]+$")
                self.assertTrue(section["heading"])
                self.assertIsInstance(section["required"], bool)
                self.assertGreaterEqual(section["min_content_chars"], 0)

    def test_applicability_and_role_rules_match_confirmed_policy(self):
        registry = load_contract()
        self.assert_v2_header(registry)
        by_id = {item["skill_id"]: item for item in registry["skills"]}

        self.assertEqual(by_id["industry-funnel"]["applicability"]["predicate"], "always")
        self.assertEqual(
            by_id["bottleneck-hunter"]["applicability"]["predicate"],
            "physical_bottleneck_exists",
        )
        self.assertEqual(
            by_id["portfolio-review"]["applicability"]["predicate"],
            "private_portfolio_input",
        )
        self.assertEqual(
            by_id["private-company-research"]["applicability"]["predicate"],
            "is_unlisted",
        )
        self.assertEqual(
            by_id["thesis-drift"]["applicability"]["predicate"],
            "paired_thesis_snapshots",
        )
        self.assertEqual(
            by_id["investment-team"]["roles"]["required_roles"],
            ["duan", "buffett", "munger", "li"],
        )
        self.assertEqual(
            by_id["earnings-team"]["roles"]["required_roles"],
            ["duan", "buffett", "munger", "li", "editor", "reader"],
        )
        self.assertEqual(
            by_id["news-pulse"]["roles"]["required_roles"],
            ["company", "regulatory", "industry", "sentiment", "integrator"],
        )

    def test_pwl_policy_is_closed_and_single_context_is_forbidden(self):
        registry = load_contract()
        self.assert_v2_header(registry)

        self.assertEqual(
            set(registry["pwl_allowlist"]),
            {"tushare_unavailable", "web_bandwidth_degraded", "ephemeral_source"},
        )
        self.assertIn("single_context_fallback", registry["pwl_forbidden"])
        self.assertIn("manual_intervention", registry["pwl_forbidden"])
        self.assertIn("budget_exhausted", registry["pwl_forbidden"])

    def test_validator_rejects_v1_schema(self):
        registry = load_contract()
        registry["schema_version"] = "full-analysis-contract/v1"

        result = self.run_validator(registry)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("schema", result.stdout + result.stderr)

    def test_validator_rejects_duplicate_section_id(self):
        registry = load_contract()
        self.assert_v2_header(registry)
        registry["skills"][0]["sections"].append(
            copy.deepcopy(registry["skills"][0]["sections"][0])
        )

        result = self.run_validator(registry)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("section_id", result.stdout + result.stderr)

    def test_validator_rejects_unknown_skill_count(self):
        registry = load_contract()
        registry["skills"] = registry["skills"][:-1]

        result = self.run_validator(registry)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("20", result.stdout + result.stderr)

    def test_every_artifact_path_is_under_confirmed_stage_directory(self):
        registry = load_contract()
        self.assert_v2_header(registry)
        stage_dirs = set(registry["stage_dirs"].values())

        for item in registry["skills"]:
            path = Path(item["artifact"]["formal_path"])
            self.assertIn(path.parts[0], stage_dirs, item["skill_id"])
            self.assertTrue(item["artifact"]["artifact_id"].startswith("artifact."))


if __name__ == "__main__":
    unittest.main()
