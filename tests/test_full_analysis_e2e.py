import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts/full_analysis.py"
GATE = REPO / "tools/full_analysis_gate.py"
REGISTRY = REPO / "tools/full_analysis_contract.json"

ROLE_CN = {
    "duan": "段永平", "buffett": "巴菲特", "munger": "芒格", "li": "李录",
    "editor": "编辑", "reader": "读者", "company": "公司", "regulatory": "监管",
    "industry": "行业", "sentiment": "情绪", "governance": "治理", "business": "业务",
    "technology": "技术", "finance": "财务", "alternative-data": "另类",
}
BOILERPLATE = {"研究免责", "仅供学习研究", "数据截止日", "命令执行记录", "下游证据", "契约计算"}


def build_compliant_report(registry_path, skill_id):
    reg = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    skill = next(s for s in reg["skills"] if s["skill_id"] == skill_id)
    lines = [f"# {skill_id}\n"]
    for sec in skill.get("sections", []):
        if not sec.get("required"):
            continue
        h = sec["heading"]
        needs_depth = sec.get("min_content_chars", 0) > 1 or h not in BOILERPLATE
        fill = (f"{h}的数据详实论证内容充实满足下限要求 " * 30 + "\n") if needs_depth else "占位\n"
        lines.append(f"## {h}\n{fill}")
    need_d = skill.get("min_dissent_points", 0)
    for i in range(need_d):
        lines.append(f"## 分歧点{i + 1}\n与另一视角存在分歧需交锋。数据详实论证内容充实满足下限要求。\n")
    if skill.get("skill_type") == "fanout":
        roles = (skill.get("roles") or {}).get("required_roles", [])
        names = [ROLE_CN.get(r, r) for r in roles if r != "integrator"]
        if len(names) >= 2:
            for k in range(2):
                lines.append(f"## 分歧仲裁{k + 1}\n{names[0]}与{names[1]}在核心判断上分歧明显，需仲裁。"
                             f"数据详实论证内容充实满足下限要求。\n")
    body = "".join(lines)
    min_bytes = skill["artifact"]["min_bytes"]
    while len(body.encode("utf-8")) < min_bytes:
        body += "数据详实论证扩充内容 " * 20 + "\n"
    return body


def build_compliant_evidence(registry_path, skill_id):
    """按全部 contract evidence_rules 生成最小满足证据，供 canary 使用。"""
    reg = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    skill = next(s for s in reg["skills"] if s["skill_id"] == skill_id)
    rules = skill.get("evidence_rules") or []
    sources = [{
        "source_id": f"src.{skill_id}.primary", "url": f"https://example.invalid/{skill_id}",
        "retrieved_at": "2026-07-23", "source_type": "filing",
        "publisher": f"{skill_id} Exchange", "title": f"{skill_id} 一手来源",
    }]
    secondary = {"source_id": f"src.{skill_id}.secondary", "url": f"https://market.invalid/{skill_id}.b",
                 "retrieved_at": "2026-07-23", "source_type": "web",
                 "publisher": f"{skill_id} Market", "title": f"{skill_id} 二次来源"}
    facts = []
    # min_facts / required_fact_fields
    min_facts = next((r.get("n", 0) for r in rules if r.get("kind") == "min_facts"), 0)
    req_fields = next((r.get("values", []) for r in rules if r.get("kind") == "required_fact_fields"), [])
    min_dual = next((r.get("n", 0) for r in rules if r.get("kind") == "min_dual_source_facts"), 0)
    fields = list(dict.fromkeys(req_fields)) or [f"{skill_id}_fact_{i+1}" for i in range(max(min_facts, 1))]
    if len(fields) < max(min_facts, 1):
        fields += [f"{skill_id}_fact_{i+1}" for i in range(len(fields), max(min_facts, 1))]
    for i, field in enumerate(fields):
        srcs = [f"src.{skill_id}.primary"]
        if i < min_dual:  # 前 min_dual 条挂双源
            srcs.append(f"src.{skill_id}.secondary")
        facts.append({"fact_id": f"fact.{skill_id}.{field}", "field": field, "value": f"v{i}",
                      "source_ids": srcs, "confidence": "high"})
    if min_dual > 0:
        sources.append(secondary)
    # min_calculations
    min_calcs = next((r.get("n", 0) for r in rules if r.get("kind") == "min_calculations"), 0)
    calcs = [{"calculation_id": f"calculation.{skill_id}.{j+1}", "operation": "calc",
              "args": {"expr": f"{j + 1} + 1"}}
             for j in range(min_calcs)]
    required_judgments = next(
        (r.get("values", []) for r in rules if r.get("kind") == "required_judgment_rule_ids"), [])
    min_judgments = next(
        (r.get("n", 0) for r in rules if r.get("kind") == "min_judgments_with_falsification"), 0)
    judgment_rules = list(required_judgments)
    while len(judgment_rules) < min_judgments:
        judgment_rules.append(f"{skill_id}_falsification_{len(judgment_rules) + 1}")
    judgments = [{
        "judgment_id": f"judgment.{skill_id}.{i + 1}",
        "rule_id": rule_id,
        "conclusion": f"{skill_id} 结构化判断 {i + 1}",
        "falsification": [f"{skill_id} 反证条件 {i + 1}"],
        "fact_ids": [facts[0]["fact_id"]],
    } for i, rule_id in enumerate(judgment_rules)]
    min_roles = next((r.get("n", 0) for r in rules if r.get("kind") == "min_role_runs"), 0)
    required_roles = (skill.get("roles") or {}).get("required_roles", [])
    role_ids = list(required_roles)
    while len(role_ids) < min_roles:
        role_ids.append(f"role-{len(role_ids) + 1}")
    role_runs = [{"role_id": role_id, "status": "PASS"} for role_id in role_ids[:min_roles]]
    required_ops = next(
        (r.get("values", []) for r in rules if r.get("kind") == "required_command_operations"), [])
    conditional = next(
        (r for r in rules if r.get("kind") == "conditional_command_operations"), None)
    operations = list(required_ops)
    if conditional:
        operations.extend(item["op"] for item in conditional.get("values", []))
    min_receipts = next((r.get("n", 0) for r in rules if r.get("kind") == "min_command_receipts"), 0)
    while len(operations) < min_receipts:
        operations.append(f"receipt-op-{len(operations) + 1}")
    operations = list(dict.fromkeys(operations))
    command_receipts = [{
        "receipt_id": f"receipt.{skill_id}.{i + 1}",
        "operation": operation,
        "status": "PASS",
    } for i, operation in enumerate(operations)]
    capability_records = ([{
        "capability": conditional["capability"], "available": True,
    }] if conditional else [])
    return (
        facts, sources, calcs, judgments, role_runs,
        command_receipts, capability_records,
    )


class FullAnalysisE2ETests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.run_root = self.root / "local/company/000651.SZ-格力电器/20260723-120000-e2e"

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)], cwd=self.root, capture_output=True, text=True)

    def test_single_company_canary_closes_all_twenty_units(self):
        started = self.cli("start", "--registry", REGISTRY, "--repo-root", self.root,
                           "--company", "格力电器", "--code", "000651.SZ", "--as-of", "2026-07-23",
                           "--run-root", self.run_root)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        run_id = manifest["run"]["run_id"]
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        by_id = {item["skill_id"]: item for item in registry["skills"]}
        for _ in range(13):
            leased = self.cli("next-work", "--run-root", self.run_root)
            self.assertEqual(leased.returncode, 0, leased.stdout + leased.stderr)
            lease = json.loads(leased.stdout)
            self.assertEqual(lease["status"], "LEASED")
            started_job = self.cli("job-started", "--run-root", self.run_root,
                                   "--work-unit-id", lease["work_unit_id"], "--attempt-id", lease["attempt_id"],
                                   "--lease-nonce", lease["lease_nonce"], "--agent-job-id", f"job-{lease['attempt_id']}")
            self.assertEqual(started_job.returncode, 0, started_job.stdout + started_job.stderr)
            skill_id = lease["skill_id"]
            attempt_dir = self.run_root / "evidence/attempts" / skill_id / lease["attempt_id"]
            attempt_dir.mkdir(parents=True, exist_ok=True)
            artifact = attempt_dir / "report.md"
            body = build_compliant_report(REGISTRY, skill_id)
            artifact.write_text(body, encoding="utf-8")
            roles = (by_id[skill_id].get("roles") or {})
            if roles.get("mode") == "independent_then_integrator":
                for role in roles.get("required_roles", []):
                    if role == "integrator":
                        continue
                    (attempt_dir / f"role-{role}.md").write_text(
                        f"角色 {role} 独立分析：" + "数据详实论证 " * 80 + "\n", encoding="utf-8")
            (ev_facts, ev_sources, ev_calcs, ev_judgments, ev_roles,
             ev_receipts, ev_capabilities) = build_compliant_evidence(REGISTRY, skill_id)
            bundle = {
                "schema_version": "result-schema/v1", "run_id": run_id,
                "work_unit_id": lease["work_unit_id"], "attempt_id": lease["attempt_id"],
                "agent_job_id": f"job-{lease['attempt_id']}", "lease_nonce": lease["lease_nonce"],
                "skill_id": skill_id, "role_id": None, "status": "PASS",
                "artifact_records": [{"artifact_id": by_id[skill_id]["artifact"]["artifact_id"],
                                      "path": str(artifact.relative_to(self.run_root)), "bytes": artifact.stat().st_size,
                                      "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(), "formal": False, "accepted": False}],
                "fact_updates": ev_facts, "source_records": ev_sources,
                "calculation_requests": ev_calcs, "judgments": ev_judgments,
                "role_runs": ev_roles, "command_receipts": ev_receipts,
                "capability_records": ev_capabilities,
                "limitations": [], "pwl_candidates": [], "started_at": "2026-07-23T12:00:00+08:00",
                "completed_at": "2026-07-23T12:01:00+08:00", "error": None,
            }
            result_path = attempt_dir / "result.json"
            result_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
            submitted = self.cli("submit-result", "--run-root", self.run_root, "--registry", REGISTRY, "--result", result_path)
            self.assertEqual(submitted.returncode, 0, submitted.stdout + submitted.stderr)
        summary_dir = self.run_root / "evidence/attempts/summary"
        summary_dir.mkdir(parents=True)
        summary_path = summary_dir / "summary.md"
        current_manifest = json.loads(
            (self.run_root / "evidence/00-analysis-manifest.json").read_text())
        artifact_index = "\n".join(
            record["path"]
            for item in current_manifest["skills"]
            for record in item["artifact_records"]
        )
        summary_path.write_text(
            "# 核心结论速览\n" + "\n".join(
                f"## {heading}\n{heading}结论均来自已登记正式产物。" * 100
                for heading in (
                    "主干①·投资分析", "主干②·财报研读", "主干③·行业分析",
                    "补充与参考", "产物索引", "数据截止日", "仅供学习研究",
                )
            ) + "\n" + artifact_index,
            encoding="utf-8",
        )
        registered_summary = self.cli(
            "register-summary", "--run-root", self.run_root,
            "--registry", REGISTRY, "--summary", summary_path,
        )
        self.assertEqual(
            registered_summary.returncode, 0,
            registered_summary.stdout + registered_summary.stderr)
        audit = self.cli("audit", "--run-root", self.run_root, "--registry", REGISTRY)
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        before_review = subprocess.run(
            [sys.executable, str(GATE), "finalize", "--run-root", str(self.run_root),
             "--registry", str(REGISTRY)],
            cwd=self.root, capture_output=True, text=True,
        )
        self.assertNotEqual(before_review.returncode, 0)
        self.assertIn("语义评审", before_review.stdout + before_review.stderr)

        prepared = self.cli(
            "review", "prepare", "--run-root", self.run_root, "--registry", REGISTRY)
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        review_dir = self.run_root / "evidence/review"
        for brief_path in sorted(review_dir.glob("review-brief-*.json")):
            brief = json.loads(brief_path.read_text(encoding="utf-8"))
            review_result = {
                "review_schema_version": "semantic-review/v1",
                "skill_id": brief["skill_id"],
                "run_id": brief["run_id"],
                "brief_digest": brief["brief_digest"],
                "report_digest": brief["report"]["sha256"],
                "evidence_digest": brief["evidence"]["sha256"],
                "verdict": "PASS",
                "dimensions": [
                    {"dimension": dimension, "verdict": "PASS"}
                    for dimension in brief["review_protocol"]
                ],
                "findings": [],
            }
            submitted_review = review_dir / f"submitted-{brief['skill_id']}.json"
            submitted_review.write_text(
                json.dumps(review_result, ensure_ascii=False), encoding="utf-8")
            ingested_review = self.cli(
                "review", "ingest", "--run-root", self.run_root,
                "--review", submitted_review,
            )
            self.assertEqual(
                ingested_review.returncode, 0,
                ingested_review.stdout + ingested_review.stderr)
        summarized = self.cli("review", "summarize", "--run-root", self.run_root)
        self.assertEqual(summarized.returncode, 0, summarized.stdout + summarized.stderr)
        finalized = subprocess.run([sys.executable, str(GATE), "finalize", "--run-root", str(self.run_root), "--registry", str(REGISTRY)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(finalized.returncode, 0, finalized.stdout + finalized.stderr)
        final_manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        self.assertEqual(final_manifest["run"]["status"], "APPROVED")


if __name__ == "__main__":
    unittest.main()
