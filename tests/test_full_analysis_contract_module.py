"""Contract 深模块（tools/full_analysis_contract.py）的接口测试。

测试全部穿过公开接口（load_contract / find_skill / get_skill_or_none），
不触碰内部实现。这是契约解析的唯一缝：gate/runtime/mk_result_bundle 的
契约读取行为都由这组函数承载。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import full_analysis_contract as contract  # noqa: E402


class ContractModuleTests(unittest.TestCase):
    def test_default_load_returns_lean_contract_with_13_skills(self):
        registry = contract.load_contract()
        self.assertEqual(registry["schema_version"], "full-analysis-contract/lean-v1")
        self.assertEqual(len(registry["skills"]), 13)

    def test_contract_path_points_to_repo_contract_json(self):
        self.assertEqual(
            contract.CONTRACT_PATH,
            REPO / "tools" / "full_analysis_contract.json")

    def test_find_skill_returns_exact_match(self):
        registry = contract.load_contract()
        skill = contract.find_skill(registry, "ashare-data")
        self.assertEqual(skill["skill_id"], "ashare-data")

    def test_find_skill_unknown_raises_contract_error(self):
        registry = contract.load_contract()
        with self.assertRaises(contract.ContractError) as ctx:
            contract.find_skill(registry, "no-such-skill")
        self.assertIn("未知 skill_id", str(ctx.exception))

    def test_get_skill_or_none_returns_none_for_unknown(self):
        registry = contract.load_contract()
        self.assertIsNone(contract.get_skill_or_none(registry, "no-such-skill"))
        self.assertEqual(
            contract.get_skill_or_none(registry, "ashare-data")["skill_id"],
            "ashare-data")

    def test_strict_rejects_bad_schema_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "registry.json"
            bad.write_text(json.dumps(
                {"schema_version": "full-analysis-contract/v99", "skills": []}),
                encoding="utf-8")
            with self.assertRaises(contract.ContractError) as ctx:
                contract.load_contract(bad)
            self.assertIn("不支持的注册表 schema_version", str(ctx.exception))

    def test_strict_rejects_wrong_skill_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "registry.json"
            bad.write_text(json.dumps(
                {"schema_version": "full-analysis-contract/lean-v1",
                 "skills": [{"skill_id": "only-one"}]}), encoding="utf-8")
            with self.assertRaises(contract.ContractError) as ctx:
                contract.load_contract(bad)
            self.assertIn("13 个 skill", str(ctx.exception))

    def test_strict_rejects_unreadable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.json"
            with self.assertRaises(contract.ContractError):
                contract.load_contract(missing)

    def test_strict_rejects_non_object_top_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "registry.json"
            bad.write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaises(contract.ContractError) as ctx:
                contract.load_contract(bad)
            self.assertIn("顶层必须为对象", str(ctx.exception))

    def test_lenient_degrades_to_empty_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.json"
            self.assertEqual(contract.load_contract(missing, strict=False),
                             {"skills": []})
            broken = Path(tmp) / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            self.assertEqual(contract.load_contract(broken, strict=False),
                             {"skills": []})


if __name__ == "__main__":
    unittest.main()
