#!/usr/bin/env python3
"""Unit tests for scripts/check-full-analysis-contract.py — 独立注册表校验器.

校验器与 gate 是两条独立实现路径 (v1.4 §6.1.4), 防注册表与 gate 同错。
本文件先于校验器实现写成 (TDD RED), 每个 RED 场景断言 exit 1 且错误信息含关键词。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VALIDATOR = REPO / "scripts" / "check-full-analysis-contract.py"
ORCHESTRATOR_SKILL = REPO / "skills" / "full-company-analysis.md"

STAGE_DIRS = {
    "01-data-screen": "01-数据与快筛",
    "02-company-earnings": "02-公司与财报",
    "03-industry-opportunity": "03-行业与机会",
    "04-thesis-boundary": "04-论文与组合",
    "05-content": "05-内容生产",
}
STAGES = list(STAGE_DIRS)


class TestOrchestratorUsageContract(unittest.TestCase):
    def test_defaults_to_private_local_output_without_requiring_git(self):
        text = ORCHESTRATOR_SKILL.read_text(encoding="utf-8")
        self.assertIn("`visibility` | 否", text)
        self.assertIn("默认 `private`", text)
        self.assertIn("Git 不是运行前提", text)
        self.assertNotIn("不得静默默认", text)
        self.assertNotIn("无非 Git fallback", text)
        self.assertNotIn("private 根目录必须被 Git 忽略", text)


def make_skill_item(i, name=None, **overrides):
    """构造一条合法注册项 (index i, 1-based)。"""
    name = name or f"skill-{i:02d}"
    stage = STAGES[(i - 1) % len(STAGES)]
    item = {
        "index": i,
        "name": name,
        "stage": stage,
        "spec_source": f"skills/{name}.md",
        "artifact_rules": [{
            "path": f"{STAGE_DIRS[stage]}/{i:02d}-{name}.md",
            "min_bytes": 800,
            "required_sections": ["数据截止日", "直接来源", "限制", "仅供学习研究"],
            "audit_policy": "required",
        }],
        "evidence_rules": [],
        "domain_evidence_status": "PHASE2_PENDING",
        "applicability_rule": {"predicate_id": "always_applicable", "alternative": None},
        "role_rule": {"required_roles": [], "min_independent_contexts": 0,
                      "sequential_cap": "PASS"},
        "legacy_output_patterns": [],
    }
    item.update(overrides)
    return item


def make_registry(n=20, mutate=None):
    """构造合成注册表 dict; mutate(registry) 可注入缺陷。"""
    reg = {
        "registry_schema_version": 1,
        "manifest_schema_version": 1,
        "annotations_schema_version": 1,
        "stage_dirs": STAGE_DIRS,
        "negative_acceptance_dir": "06-负向验收",
        "generic_required_sections": ["数据截止日", "直接来源", "限制", "仅供学习研究"],
        "predicates": ["always_applicable", "is_a_share"],
        "skills": [make_skill_item(i) for i in range(1, n + 1)],
    }
    if mutate:
        mutate(reg)
    return reg


class _ValidatorCase(unittest.TestCase):
    """在临时目录落合成仓库 (注册表 + skills/*.md), subprocess 跑校验器。"""

    def run_validator(self, registry, skill_texts=None, omit_specs=()):
        """写入合成注册表与 skill 文件, 返回 CompletedProcess。

        skill_texts: {spec_source 相对路径: 文件内容}; 缺省为每个注册项生成一个
        无保存章节的最小 skill 文件。omit_specs 中的 spec_source 不自动生成
        (用于测"spec_source 不存在"场景)。
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "skills").mkdir()
        texts = dict(skill_texts or {})
        for item in registry.get("skills", []):
            src = item.get("spec_source")
            if src and src not in texts and src not in omit_specs:
                texts[src] = f"# {item.get('name')}\n\n占位规范, 无保存章节。\n"
        for rel, text in texts.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        reg_path = root / "contract.json"
        reg_path.write_text(json.dumps(registry, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(VALIDATOR), "--registry", str(reg_path),
             "--repo-root", str(root)],
            capture_output=True, text=True)

    def assert_fails_with(self, proc, keyword):
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn(keyword, proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


class TestRegistryStructure(_ValidatorCase):
    def test_valid_synthetic_registry_passes(self):
        proc = self.run_validator(make_registry())
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)

    def test_19_items_fails(self):
        self.assert_fails_with(self.run_validator(make_registry(19)), "20")

    def test_21_items_fails(self):
        self.assert_fails_with(self.run_validator(make_registry(21)), "20")

    def test_duplicate_name_fails(self):
        def mutate(reg):
            reg["skills"][1]["name"] = reg["skills"][0]["name"]
        self.assert_fails_with(self.run_validator(make_registry(mutate=mutate)),
                               "name")

    def test_bad_audit_policy_fails(self):
        def mutate(reg):
            reg["skills"][0]["artifact_rules"][0]["audit_policy"] = "optional"
        self.assert_fails_with(self.run_validator(make_registry(mutate=mutate)),
                               "audit_policy")

    def test_missing_spec_source_fails(self):
        reg = make_registry()
        reg["skills"][0]["spec_source"] = "skills/ghost.md"
        proc = self.run_validator(reg, omit_specs={"skills/ghost.md"})
        self.assert_fails_with(proc, "ghost")

    def test_artifact_path_traversal_fails(self):
        def mutate(reg):
            reg["skills"][0]["artifact_rules"][0]["path"] = "../escape.md"
        self.assert_fails_with(self.run_validator(make_registry(mutate=mutate)),
                               "..")

    def test_artifact_path_conflict_fails(self):
        def mutate(reg):
            reg["skills"][1]["artifact_rules"][0]["path"] = \
                reg["skills"][0]["artifact_rules"][0]["path"]
        self.assert_fails_with(self.run_validator(make_registry(mutate=mutate)),
                               "冲突")

    def test_glob_in_legacy_pattern_fails(self):
        # §8.3: reports/** 这类过宽模式不能算覆盖证明 → 直接非法
        def mutate(reg):
            reg["skills"][0]["legacy_output_patterns"] = ["reports/**"]
        self.assert_fails_with(self.run_validator(make_registry(mutate=mutate)),
                               "*")

    def test_unknown_predicate_fails(self):
        def mutate(reg):
            reg["skills"][0]["applicability_rule"]["predicate_id"] = "made_up"
        self.assert_fails_with(self.run_validator(make_registry(mutate=mutate)),
                               "made_up")

    def test_multiple_errors_all_listed(self):
        # 失败要大声: 两处缺陷必须一次全部列出, 不只报首个
        def mutate(reg):
            reg["skills"][0]["artifact_rules"][0]["audit_policy"] = "optional"
            reg["skills"][1]["applicability_rule"]["predicate_id"] = "made_up"
        proc = self.run_validator(make_registry(mutate=mutate))
        self.assertEqual(proc.returncode, 1)
        out = proc.stdout + proc.stderr
        self.assertIn("audit_policy", out)
        self.assertIn("made_up", out)


class TestSavePathCoverage(_ValidatorCase):
    """§15.2#12: 从 skill 保存语义章节提取硬编码路径, 每条必须被具体模板覆盖。"""

    SKILL_WITH_SAVE = (
        "# skill-01\n\n## 分析流程\n\n参考样例 `reports/别家公司.md` 不算写入目标。\n\n"
        "## 保存报告\n\n将最终报告写入 `reports/{公司名}/{公司名}-research-{YYYYMMDD}.md`。\n"
    )

    def test_uncovered_extracted_path_fails(self):
        reg = make_registry()
        proc = self.run_validator(reg,
                                  skill_texts={"skills/skill-01.md": self.SKILL_WITH_SAVE})
        self.assert_fails_with(proc, "research")

    def test_covered_extracted_path_passes(self):
        def mutate(reg):
            reg["skills"][0]["legacy_output_patterns"] = [
                "reports/{company}/{company}-research-{date}.md"]
        proc = self.run_validator(make_registry(mutate=mutate),
                                  skill_texts={"skills/skill-01.md": self.SKILL_WITH_SAVE})
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)

    def test_path_outside_save_section_not_extracted(self):
        # 非保存章节里的路径 (参考样本) 不得被提取 → 无需覆盖也通过
        text = "# skill-01\n\n## 分析流程\n\n对比 `reports/参考样本.md` 与年报。\n"
        proc = self.run_validator(make_registry(),
                                  skill_texts={"skills/skill-01.md": text})
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)

    def test_home_path_extracted_and_covered(self):
        text = ("# skill-01\n\n## 输出文件\n\n"
                "将完整最终报告写入 `~/{公司名}投资研究报告_{日期}.md`。\n")
        def mutate(reg):
            reg["skills"][0]["legacy_output_patterns"] = [
                "~/{company}投资研究报告_{date}.md"]
        proc = self.run_validator(make_registry(mutate=mutate),
                                  skill_texts={"skills/skill-01.md": text})
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        # 同一文件不覆盖时必须失败
        proc2 = self.run_validator(make_registry(),
                                   skill_texts={"skills/skill-01.md": text})
        self.assert_fails_with(proc2, "投资研究报告")


class TestPhase2EvidenceRules(_ValidatorCase):
    """Phase 2 §15.3: IMPLEMENTED 契约必须有领域专属断言; evidence_rules 词表校验。"""

    def test_implemented_generic_only_fails(self):
        def mutate(reg):
            reg["skills"][0]["domain_evidence_status"] = "IMPLEMENTED"
            # 只有通用 required_sections, 无 evidence_rules → §15.3 失败
        self.assert_fails_with(self.run_validator(make_registry(mutate=mutate)),
                               "evidence_rules")

    def test_implemented_with_domain_section_but_no_evidence_rule_fails(self):
        def mutate(reg):
            reg["skills"][0]["domain_evidence_status"] = "IMPLEMENTED"
            reg["skills"][0]["artifact_rules"][0]["required_sections"] = \
                ["数据截止日", "直接来源", "限制", "仅供学习研究", "命令执行记录"]
        proc = self.run_validator(make_registry(mutate=mutate))
        self.assert_fails_with(proc, "evidence_rules")

    def test_implemented_with_evidence_rules_passes(self):
        def mutate(reg):
            reg["skills"][0]["domain_evidence_status"] = "IMPLEMENTED"
            reg["skills"][0]["evidence_rules"] = [
                {"kind": "required_fact_fields", "values": ["revenue"]}]
        proc = self.run_validator(make_registry(mutate=mutate))
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)

    def test_implemented_with_count_only_rule_still_fails(self):
        def mutate(reg):
            reg["skills"][0]["domain_evidence_status"] = "IMPLEMENTED"
            reg["skills"][0]["evidence_rules"] = [
                {"kind": "min_facts", "n": 7}]
        self.assert_fails_with(self.run_validator(make_registry(mutate=mutate)),
                               "领域标识")

    def test_invalid_evidence_rule_kind_fails(self):
        def mutate(reg):
            reg["skills"][0]["evidence_rules"] = [{"kind": "rm_rf", "n": 1}]
        self.assert_fails_with(self.run_validator(make_registry(mutate=mutate)),
                               "evidence_rule kind")

    def test_evidence_rule_bad_n_fails(self):
        def mutate(reg):
            reg["skills"][0]["evidence_rules"] = [{"kind": "min_facts", "n": 0}]
        self.assert_fails_with(self.run_validator(make_registry(mutate=mutate)),
                               "evidence_rule n")


class TestRealRegistry(unittest.TestCase):
    """真实仓库注册表必须通过 (默认参数直跑)。"""

    def test_real_registry_passes(self):
        proc = subprocess.run([sys.executable, str(VALIDATOR)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_phase2_all_contracts_implemented(self):
        """Phase 2 完成门: 真实注册表 20 项均 domain_evidence_status=IMPLEMENTED。"""
        reg = json.loads((REPO / "tools" / "full_analysis_contract.json")
                         .read_text(encoding="utf-8"))
        pending = [s["name"] for s in reg["skills"]
                   if s.get("domain_evidence_status") != "IMPLEMENTED"]
        self.assertEqual(pending, [], f"仍为 PHASE2_PENDING 的契约: {pending}")

    def test_phase2_all_implemented_contracts_have_machine_evidence_rules(self):
        reg = json.loads((REPO / "tools" / "full_analysis_contract.json")
                         .read_text(encoding="utf-8"))
        missing = [s["name"] for s in reg["skills"]
                   if s.get("domain_evidence_status") == "IMPLEMENTED"
                   and not s.get("evidence_rules")]
        self.assertEqual(missing, [],
                         f"IMPLEMENTED 但没有机器 evidence_rules: {missing}")

    def test_phase2_all_contracts_have_domain_identifying_rule(self):
        reg = json.loads((REPO / "tools" / "full_analysis_contract.json")
                         .read_text(encoding="utf-8"))
        domain_kinds = {"required_fact_fields", "required_judgment_rule_ids",
                        "required_command_operations"}
        missing = [s["name"] for s in reg["skills"]
                   if not any(r.get("kind") in domain_kinds
                              for r in s.get("evidence_rules", []))]
        self.assertEqual(missing, [],
                         f"缺领域标识 evidence rule: {missing}")

    def test_investment_team_requires_four_named_views_and_arbitration(self):
        """最终综合报告必须保留四位投资人的独立观点与仲裁。"""
        reg = json.loads((REPO / "tools" / "full_analysis_contract.json")
                         .read_text(encoding="utf-8"))
        item = next(skill for skill in reg["skills"]
                    if skill["name"] == "investment-team")
        sections = set(item["artifact_rules"][0]["required_sections"])

        required_sections = {
            "段永平视角",
            "巴菲特视角",
            "芒格视角",
            "李录视角",
            "四视角对照表",
            "分歧仲裁",
            "综合结论",
        }
        self.assertTrue(required_sections.issubset(sections),
                        f"investment-team 缺少强制章节: "
                        f"{sorted(required_sections - sections)}")
        self.assertEqual(
            set(item["artifact_rules"][0]["required_heading_sections"]),
            required_sections,
            "七项命名输出必须按 Markdown 标题验收，不能只做正文子串匹配",
        )
        self.assertEqual(
            item["role_rule"]["required_roles"],
            [
                "interpreter-duan",
                "interpreter-buffett",
                "interpreter-munger",
                "interpreter-li",
            ],
        )
        legacy_sections = {
            "business视角", "financial视角", "industry视角", "risk视角",
        }
        self.assertTrue(legacy_sections.isdisjoint(sections))

        skill_names = {skill["name"] for skill in reg["skills"]}
        self.assertTrue(
            {"buffett-ask", "munger-ask", "li-lu-ask"}.isdisjoint(skill_names),
            "四视角应由 investment-team 统一编排，不应新增三个独立 Skill",
        )


if __name__ == "__main__":
    unittest.main()
