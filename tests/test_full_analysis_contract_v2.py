import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CONTRACT = REPO / "tools" / "full_analysis_contract.json"
VALIDATOR = REPO / "scripts" / "check-full-analysis-contract.py"
VALIDATOR_SPEC = importlib.util.spec_from_file_location("contract_validator", VALIDATOR)
VALIDATOR_MODULE = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(VALIDATOR_MODULE)

EXPECTED_SKILLS = {
    "ashare-data",
    "financial-data",
    "quality-screen",
    "investment-checklist",
    "investment-research",
    "investment-team",
    "management-deep-dive",
    "earnings-review",
    "industry-research",
    "industry-funnel",
    "bottleneck-hunter",
    "news-pulse",
    "thesis-tracker",
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
        self.assertEqual(len(registry["skills"]), 13)

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
            by_id["investment-team"]["roles"]["required_roles"],
            ["duan", "buffett", "munger", "li"],
        )
        self.assertEqual(
            by_id["earnings-review"]["roles"]["required_roles"],
            ["duan", "buffett", "munger", "li"],
        )
        self.assertEqual(
            by_id["earnings-review"]["skill_type"], "fanout",
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

    def test_validator_rejects_authorization_scope_expansion(self):
        registry = load_contract()
        registry["authorization_profile"]["granted"].append(
            "external_publish")

        result = self.run_validator(registry)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("authorization_profile", result.stdout + result.stderr)

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
        self.assertIn("13", result.stdout + result.stderr)

    def test_every_artifact_path_is_under_confirmed_stage_directory(self):
        registry = load_contract()
        self.assert_v2_header(registry)
        stage_dirs = set(registry["stage_dirs"].values())

        for item in registry["skills"]:
            path = Path(item["artifact"]["formal_path"])
            self.assertIn(path.parts[0], stage_dirs, item["skill_id"])
            self.assertTrue(item["artifact"]["artifact_id"].startswith("artifact."))

    def test_each_skill_exposes_synced_core_and_predicate_projection(self):
        registry = load_contract()
        self.assert_v2_header(registry)
        for item in registry["skills"]:
            self.assertIsInstance(item.get("core"), bool, item["skill_id"])
            self.assertEqual(item.get("predicates"), [item["applicability"]["predicate"]], item["skill_id"])
            self.assertIn(item["applicability"]["predicate"], registry["predicates"])

    def test_validator_rejects_stale_predicate_projection(self):
        registry = load_contract()
        self.assert_v2_header(registry)
        registry["skills"][0]["predicates"] = ["stale_predicate"]
        result = self.run_validator(registry)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("predicates", result.stdout + result.stderr)

    def _skill_with(self, registry, skill_id, evidence_rules):
        for item in registry["skills"]:
            if item["skill_id"] == skill_id:
                item["evidence_rules"] = evidence_rules
                return
        raise AssertionError(f"skill 不存在: {skill_id}")

    def test_validator_rejects_duplicate_evidence_kind(self):
        registry = load_contract()
        self.assert_v2_header(registry)
        # financial-data 原本无 min_facts，注入两条重复 min_facts
        self._skill_with(registry, "financial-data", [
            {"kind": "required_fact_fields", "values": ["revenue"]},
            {"kind": "min_facts", "n": 2},
            {"kind": "min_facts", "n": 3},
        ])
        result = self.run_validator(registry)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kind 重复", result.stdout + result.stderr)

    def test_validator_rejects_role_run_rule_for_single_agent(self):
        registry = load_contract()
        self.assert_v2_header(registry)
        skill = next(
            item for item in registry["skills"]
            if item["skill_id"] == "management-deep-dive"
        )
        skill["evidence_rules"].append({"kind": "min_role_runs", "n": 1})

        result = self.run_validator(registry)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("single_agent", result.stdout + result.stderr)

    def test_validator_rejects_min_facts_below_required_fields(self):
        registry = load_contract()
        self.assert_v2_header(registry)
        self._skill_with(registry, "financial-data", [
            {"kind": "required_fact_fields", "values": ["revenue", "margin", "eps"]},
            {"kind": "min_facts", "n": 1},  # 1 < 3，逻辑不可达
        ])
        result = self.run_validator(registry)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("逻辑不可达", result.stdout + result.stderr)

    def test_validator_rejects_duplicate_required_fact_fields(self):
        registry = load_contract()
        self.assert_v2_header(registry)
        self._skill_with(registry, "financial-data", [
            {"kind": "required_fact_fields", "values": ["revenue", "revenue"]},
        ])
        result = self.run_validator(registry)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required_fact_fields 值重复", result.stdout + result.stderr)

    def test_validator_rejects_dual_source_exceeding_min_facts(self):
        registry = load_contract()
        self.assert_v2_header(registry)
        self._skill_with(registry, "financial-data", [
            {"kind": "required_fact_fields", "values": ["revenue"]},
            {"kind": "min_facts", "n": 1},
            {"kind": "min_dual_source_facts", "n": 2},  # 2 > 1，逻辑不可达
        ])
        result = self.run_validator(registry)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("逻辑不可达", result.stdout + result.stderr)

    def test_validator_rejects_duplicate_section_heading(self):
        registry = load_contract()
        self.assert_v2_header(registry)
        skill = registry["skills"][0]
        dup = copy.deepcopy(skill["sections"][0])
        dup["section_id"] = "dup_heading_section"  # 改 id 规避 section_id 唯一性
        skill["sections"].append(dup)  # heading 与 sections[0] 相同
        result = self.run_validator(registry)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("heading 必须唯一", result.stdout + result.stderr)

    def test_validator_rejects_zero_min_content_chars_on_required_section(self):
        registry = load_contract()
        self.assert_v2_header(registry)
        skill = registry["skills"][0]
        required_sec = next(s for s in skill["sections"] if s.get("required"))
        required_sec["min_content_chars"] = 0
        result = self.run_validator(registry)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("min_content_chars 必须 > 0", result.stdout + result.stderr)

    def test_audit_evaluator_registry_covers_every_allowed_evidence_kind(self):
        kinds, error = VALIDATOR_MODULE._audit_evaluator_kinds(REPO)

        self.assertIsNone(error)
        self.assertEqual(kinds, VALIDATOR_MODULE.EVIDENCE_KINDS)


if __name__ == "__main__":
    unittest.main()
