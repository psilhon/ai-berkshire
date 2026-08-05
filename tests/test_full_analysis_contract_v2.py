import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CONTRACT = REPO / "tools" / "full_analysis_contract.json"
VALIDATOR = REPO / "scripts" / "check-full-analysis-contract.py"

# 加载校验器模块（仅读取其常量与 validate()，不执行 main）。
VALIDATOR_SPEC = importlib.util.spec_from_file_location("contract_validator", VALIDATOR)
VALIDATOR_MODULE = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(VALIDATOR_MODULE)

# 当前 contract 为 lean-v1 —— 13 个固定 skill。
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

# lean-v1 每个 skill dict 允许出现的键（不含任何 v2-only 字段）。
ALLOWED_SKILL_KEYS = {
    "skill_id",
    "depends_on",
    "category",
    "stage_dir",
    "spec_source",
    "core",
    "skill_type",
    "applicability",
    "artifact",
    "roles",
    "min_substantive_sections",
    "report_guidance",
    "substance",
}

# v2-only 的字段，lean-v1 中不得出现。
FORBIDDEN_SKILL_KEYS = {
    "sections",
    "evidence_rules",
    "artifact_id",
    "predicates",
    "audit_policy",
    "dual_source",
    "pwl_*",
}

# v2-only 的顶层字段，lean-v1 中不得出现。
FORBIDDEN_TOP_LEVEL_KEYS = {
    "result_schema_version",
    "predicates",
    "pwl_allowlist",
    "pwl_forbidden",
    "generic_required_sections",
}


def load_contract():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


class ContractLeanV1Tests(unittest.TestCase):
    """校验 tools/full_analysis_contract.json 的 CURRENT（lean-v1）契约现实。"""

    # ---- 顶层 schema 头 ----

    def test_schema_version_is_lean_v1(self):
        registry = load_contract()
        self.assertEqual(
            registry.get("schema_version"), "full-analysis-contract/lean-v1"
        )

    def test_manifest_schema_version_is_lean_v1(self):
        registry = load_contract()
        self.assertEqual(
            registry.get("manifest_schema_version"), "full-analysis-manifest/lean-v1"
        )

    def test_no_v2_only_top_level_keys(self):
        registry = load_contract()
        for key in FORBIDDEN_TOP_LEVEL_KEYS:
            self.assertNotIn(key, registry, f"lean-v1 不应含顶层键 {key}")

    # ---- skills 集合 ----

    def test_exactly_13_skills(self):
        registry = load_contract()
        self.assertEqual(len(registry["skills"]), 13)

    def test_skill_ids_match_expected_set(self):
        registry = load_contract()
        self.assertEqual(
            {item["skill_id"] for item in registry["skills"]},
            EXPECTED_SKILLS,
        )

    # ---- 每个 skill 的结构（lean-v1 现实）----

    def test_every_skill_has_only_lean_keys(self):
        registry = load_contract()
        for item in registry["skills"]:
            extra = set(item) - ALLOWED_SKILL_KEYS
            self.assertFalse(
                extra,
                f"skill {item.get('skill_id')} 含有非 lean 键: {sorted(extra)}",
            )

    def test_no_skill_contains_sections_or_evidence_rules(self):
        registry = load_contract()
        for item in registry["skills"]:
            sid = item["skill_id"]
            self.assertNotIn("sections", item, f"{sid} 不应含 sections")
            self.assertNotIn("evidence_rules", item, f"{sid} 不应含 evidence_rules")

    def test_every_skill_artifact_has_formal_path_and_min_bytes(self):
        registry = load_contract()
        for item in registry["skills"]:
            sid = item["skill_id"]
            artifact = item["artifact"]
            self.assertIsInstance(artifact["formal_path"], str)
            self.assertNotEqual(artifact["formal_path"], "")
            self.assertIsInstance(artifact["min_bytes"], int)
            self.assertGreater(artifact["min_bytes"], 0)

    def test_every_skill_artifact_has_no_artifact_id(self):
        registry = load_contract()
        for item in registry["skills"]:
            sid = item["skill_id"]
            self.assertNotIn("artifact_id", item["artifact"], f"{sid} 不应含 artifact_id")

    def test_every_skill_has_substance_flags(self):
        registry = load_contract()
        for item in registry["skills"]:
            sid = item["skill_id"]
            substance = item["substance"]
            self.assertIsInstance(substance["require_as_of"], bool)
            self.assertIsInstance(substance["require_sources"], bool)
            self.assertIsInstance(substance["require_disclaimer"], bool)

    def test_every_skill_has_report_guidance_and_min_substantive_sections(self):
        registry = load_contract()
        for item in registry["skills"]:
            sid = item["skill_id"]
            self.assertIsInstance(item["report_guidance"], str)
            self.assertNotEqual(item["report_guidance"], "")
            self.assertIsInstance(item["min_substantive_sections"], int)
            self.assertGreaterEqual(item["min_substantive_sections"], 1)

    def test_artifact_paths_are_under_known_stage_directories(self):
        registry = load_contract()
        stage_dirs = set(registry["stage_dirs"].values())
        for item in registry["skills"]:
            sid = item["skill_id"]
            path = Path(item["artifact"]["formal_path"])
            self.assertIn(path.parts[0], stage_dirs, sid)

    def test_applicability_and_roles_preserved(self):
        registry = load_contract()
        by_id = {item["skill_id"]: item for item in registry["skills"]}

        self.assertEqual(by_id["industry-funnel"]["applicability"]["predicate"], "always")
        self.assertEqual(
            by_id["bottleneck-hunter"]["applicability"]["predicate"],
            "physical_bottleneck_exists",
        )
        # fanout 类 skill 的 roles
        self.assertEqual(
            by_id["investment-team"]["roles"]["required_roles"],
            ["duan", "buffett", "munger", "li"],
        )
        self.assertEqual(
            by_id["earnings-review"]["roles"]["required_roles"],
            ["duan", "buffett", "munger", "li"],
        )
        self.assertEqual(by_id["earnings-review"]["skill_type"], "fanout")
        self.assertEqual(
            by_id["news-pulse"]["roles"]["required_roles"],
            ["company", "regulatory", "industry", "sentiment", "integrator"],
        )
        # lean-v1 roles.mode 取值
        allowed_modes = {"single_agent", "independent_then_integrator"}
        for item in registry["skills"]:
            self.assertIn(item["roles"]["mode"], allowed_modes, item["skill_id"])

    # ---- 校验器现状（schema 自适应：lean-v1 由原生存量校验器验证）----

    def test_validator_dispatches_on_lean_v1(self):
        # 校验器应自动识别 lean-v1 并使用 lean 校验分支。
        self.assertTrue(hasattr(VALIDATOR_MODULE, "validate_lean"))

    def test_validator_accepts_current_lean_v1_contract(self):
        # lean-v1 契约应由校验器验证通过（check.sh 同样依赖此路径）。
        result = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                "--registry",
                str(CONTRACT),
                "--repo-root",
                str(REPO),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
