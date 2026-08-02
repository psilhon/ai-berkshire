"""mk_result_bundle.py 转正回归测试：验证其生成的 Result Bundle 对全部 13 个
契约 skill 都满足 result-schema/v1 结构约束（E16：bundle 生成必须走确定性工具，
禁止子 Agent 手写易错 JSON——五粮液 run 的 schema 返工根因）。"""
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / "tools" / "full_analysis_contract.json"
SCHEMA = REPO / "tools" / "full_analysis_result_schema.json"

sys.path.insert(0, str(REPO / "scripts"))
import mk_result_bundle as mkb  # noqa: E402


def _validate_schema_value(value, schema, path):
    """复用 Gate 的 schema 校验语义做结构断言（额外字段/缺失必填/枚举越界）。"""
    if schema.get("type") == "array":
        assert isinstance(value, list), f"{path} 应为数组"
        for i, item in enumerate(value):
            _validate_schema_value(item, schema.get("items", {}), f"{path}[{i}]")
    elif schema.get("type") == "object":
        assert isinstance(value, dict), f"{path} 应为对象"
        required = set(schema.get("required", []))
        missing = sorted(required - set(value))
        assert not missing, f"{path} 缺字段 {missing}"
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(props))
            assert not extra, f"{path} 含未知字段 {extra}"
        for key, item in value.items():
            if key in props:
                _validate_schema_value(item, props[key], f"{path}.{key}")
    elif "enum" in schema:
        assert value in schema["enum"], f"{path} 值 {value!r} 不在枚举 {schema['enum']}"
    elif schema.get("type") in ("string",):
        assert isinstance(value, str), f"{path} 应为字符串"
    elif schema.get("type") == "boolean":
        assert isinstance(value, bool), f"{path} 应为布尔"


class MkResultBundleTests(unittest.TestCase):
    def setUp(self):
        self.registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    def test_minimum_evidence_covers_every_contract_skill(self):
        """对全部 13 个 skill，build_minimum_evidence 都必须产出满足
        evidence_rules 最低条数的证据（无任何 skill 抛错或产出不足）。"""
        for skill in self.registry["skills"]:
            with self.subTest(skill=skill["skill_id"]):
                rules = {r.get("kind"): r for r in skill.get("evidence_rules", [])}
                facts, sources, calcs, judgments, role_runs, receipts, caps = (
                    mkb.build_minimum_evidence(skill, [], []))

                req_fields = rules.get("required_fact_fields", {}).get("values", [])
                fact_fields = {f["field"] for f in facts}
                for field in req_fields:
                    self.assertIn(field, fact_fields,
                                  f"{skill['skill_id']} 缺必需 fact field {field}")

                min_facts = rules.get("min_facts", {}).get("n", 0)
                self.assertGreaterEqual(len(facts), min_facts,
                                        f"{skill['skill_id']} facts < {min_facts}")

                min_dual = rules.get("min_dual_source_facts", {}).get("n", 0)
                dual = sum(1 for f in facts if len(f.get("source_ids", [])) >= 2)
                self.assertGreaterEqual(dual, min_dual,
                                        f"{skill['skill_id']} 双源 facts < {min_dual}")

                min_calcs = rules.get("min_calculations", {}).get("n", 0)
                self.assertGreaterEqual(len(calcs), min_calcs,
                                        f"{skill['skill_id']} calcs < {min_calcs}")
                for c in calcs:
                    # E16: operation 必须是 financial_rigor 真实子命令，否则 audit 无法重放
                    self.assertIn(c["operation"], ("calc",), f"{skill['skill_id']} 非法 op")

                min_judg = rules.get("min_judgments_with_falsification", {}).get("n", 0)
                self.assertGreaterEqual(len(judgments), min_judg,
                                        f"{skill['skill_id']} judgments < {min_judg}")
                for j in judgments:
                    self.assertTrue(j.get("falsification"),
                                    f"{skill['skill_id']} judgment 缺 falsification")

                req_judge = rules.get("required_judgment_rule_ids", {}).get("values", [])
                judge_ids = {j["rule_id"] for j in judgments}
                for rid in req_judge:
                    self.assertIn(rid, judge_ids,
                                  f"{skill['skill_id']} 缺必需 judgment rule_id {rid}")

                min_roles = rules.get("min_role_runs", {}).get("n", 0)
                self.assertGreaterEqual(len(role_runs), min_roles,
                                        f"{skill['skill_id']} role_runs < {min_roles}")

                min_receipts = rules.get("min_command_receipts", {}).get("n", 0)
                self.assertGreaterEqual(len(receipts), min_receipts,
                                        f"{skill['skill_id']} receipts < {min_receipts}")

                cond = rules.get("conditional_command_operations")
                if cond:
                    cap_names = {c["capability"] for c in caps}
                    self.assertIn(cond["capability"], cap_names,
                                  f"{skill['skill_id']} 缺 capability 声明")

    def test_generated_bundle_satisfies_result_schema(self):
        """模拟完整 bundle 组装路径：构造最小 report + 租约身份后，验证
        mk_result_bundle 输出的 result.json 通过 result-schema/v1 结构校验。"""
        skill = next(s for s in self.registry["skills"] if s["skill_id"] == "ashare-data")
        # 直接构造与 main() 相同的 bundle 结构（不依赖真实 run_root）
        facts, sources, calcs, judgments, role_runs, receipts, caps = (
            mkb.build_minimum_evidence(skill, [], []))
        bundle = {
            "schema_version": "result-schema/v1",
            "run_id": "run-test",
            "work_unit_id": "wu-ashare-data",
            "attempt_id": "attempt-test",
            "agent_job_id": "agent-test",
            "lease_nonce": "nonce-test",
            "skill_id": "ashare-data",
            "role_id": None,
            "status": "PASS",
            "artifact_records": [{
                "artifact_id": skill["artifact"]["artifact_id"],
                "path": "evidence/attempts/ashare-data/attempt-test/report.md",
                "bytes": 4096,
                "sha256": "a" * 64,
                "formal": False,
                "accepted": False,
            }],
            "fact_updates": facts,
            "source_records": sources,
            "calculation_requests": calcs,
            "judgments": judgments,
            "role_runs": role_runs,
            "command_receipts": receipts,
            "capability_records": caps,
            "limitations": [],
            "pwl_candidates": [],
            "started_at": "2026-08-02T12:00:00+08:00",
            "completed_at": "2026-08-02T12:01:00+08:00",
            "error": None,
        }
        # 逐项按 schema 校验（顶层 + 各账本数组）
        _validate_schema_value(bundle, self.schema, "$")
        for key in ("fact_updates", "source_records", "calculation_requests",
                    "judgments", "role_runs", "command_receipts",
                    "capability_records", "limitations"):
            _validate_schema_value(
                bundle[key],
                self.schema["properties"][key],
                f"$.{key}")

    def test_check_report_detects_missing_headings(self):
        """check_report 必须能抓出缺必需章节标题的报告（防「标题带编号」与
        「缺章节」两类历史事故回归）。"""
        skill = next(s for s in self.registry["skills"] if s["skill_id"] == "ashare-data")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "report.md"
            p.write_text("# 标题\n\n正文内容\n", encoding="utf-8")
            warnings = mkb.check_report(skill, p)
            self.assertTrue(any("缺必需章节标题" in w for w in warnings))
            # 满足全部章节时无章节警告（字节不足单独由 min_bytes 断言）
            headings = [s["heading"] for s in skill["sections"] if s.get("required")]
            body = "\n\n".join(f"## {h}\n\n正文 {h} 的内容不少于一行。" for h in headings)
            p.write_text(body, encoding="utf-8")
            warnings = mkb.check_report(skill, p)
            self.assertFalse(any("缺必需章节标题" in w for w in warnings))
            self.assertTrue(any("字节数" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
