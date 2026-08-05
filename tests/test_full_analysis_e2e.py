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

# 真 oracle：让夹具直接调用执行器签发回执，而非手写 argv/output。
# v3.4.14 的 false oracle（夹具自己拼回执）正是绑定失效没被发现的根因。
sys.path.insert(0, str(REPO / "tools"))
import evidence_receipt as er  # noqa: E402

ROLE_CN = {
    "duan": "段永平", "buffett": "巴菲特", "munger": "芒格", "li": "李录",
    "editor": "编辑", "reader": "读者", "company": "公司", "regulatory": "监管",
    "industry": "行业", "sentiment": "情绪", "governance": "治理", "business": "业务",
    "technology": "技术", "finance": "财务", "alternative-data": "另类",
}
AS_OF = "2026-07-23"
_BODY_UNIT = (
    "该判断由公开披露数据逐项交叉核对得出，覆盖营业收入、毛利率与经营性现金流三条线索，"
    "并与同业可比公司做横向对照；同时列出反面证据与主要风险点，避免单向叙事。"
)


def build_compliant_report(registry_path, skill_id):
    """lean 契约下生成一份尽量贴近「实质地板」的报告骨架。

    lean 已移除 sections/evidence_rules/artifact_id：Gate 不再校验固定标题，
    改校验「实质地板」（数据截止日 + 数据来源 + 免责 + 足量实质章节 + 字节下限）。
    注意：当前 impl 的 _substance_errors 仍按 contract sections 计数
    （tools/full_analysis_gate.py:971），导致 min_substantive_sections 永远无法满足、
    任何 PASS 报告都无法 ingest——这是已知 impl 缺陷，见 canary 的 expectedFailure 注释。
    此处报告尽量贴近 lean 要求（三锚 + 多 ## 实质章节 + 字节下限），以便 impl 修复后
    canary 直接转绿。
    """
    reg = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    skill = next(s for s in reg["skills"] if s["skill_id"] == skill_id)
    lines = [f"# {skill_id} 分析报告\n"]
    anchors = (
        f"数据截止日 {AS_OF}。"
        "数据来源：Tushare 行情接口与巨潮资讯网公开披露文件。"
        "本报告仅供学习研究，不构成投资建议。\n"
    )
    lines.append(anchors + "\n")
    sections = max(1, int(skill.get("min_substantive_sections", 1) or 1))
    for i in range(sections):
        lines.append(f"## 主题{i + 1}\n{_BODY_UNIT * 6}（主题{i + 1}的独立论证与数据支撑）\n")
    if skill.get("skill_type") == "fanout":
        roles = (skill.get("roles") or {}).get("required_roles", [])
        names = [ROLE_CN.get(r, r) for r in roles if r != "integrator"]
        if len(names) >= 2:
            for k in range(2):
                lines.append(
                    f"## 分歧仲裁{k + 1}\n{names[0]}与{names[1]}在核心判断上分歧明显，需要仲裁。"
                    f"{_BODY_UNIT * 3}（第 {k + 1} 处交锋）\n")
    body = "".join(lines)
    min_bytes = int(skill["artifact"].get("min_bytes", 0) or 0)
    filler = "补充论证：把上述数据与推演进一步展开，逐条落到可核验的口径上。"
    while len(body.encode("utf-8")) < min_bytes:
        body += filler * 8 + "\n"
    return body


def build_compliant_evidence(registry_path, skill_id, run_root=None):
    """按全部 contract evidence_rules 生成最小满足证据，供 canary 使用。

    run_root 给定时，每条 PASS 回执**真实调用执行器签发**（execute_and_sign），
    让 canary 在真实编排链路上验证「回执绑定」名副其实；run_root 为 None 时退化
    为 v3.4.14 风格的手写 argv/output（仅供不依赖执行器的纯单元场景）。
    """
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
    command_receipts = []
    for i, operation in enumerate(operations):
        receipt_id = f"receipt.{skill_id}.{i + 1}"
        if run_root is not None:
            # 真 oracle：用执行器真实 subprocess 跑一条命令并签名签发。
            # 命令必须含 operation token（Gate 校验 operation ∈ argv），且退出码 0。
            command = [sys.executable, "-c",
                       "import sys; sys.stdout.write('evidence-ok')", operation]
            receipt, _exit = er.execute_and_sign(
                Path(run_root), receipt_id, operation, command)
            assert receipt["status"] == "PASS", (
                f"夹具执行器签发失败：{operation} -> {receipt}")
            command_receipts.append(receipt)
        else:
            # 退化分支（不依赖执行器的纯单元场景）：v3.4.14 风格手写 argv/output。
            command_receipts.append({
                "receipt_id": receipt_id,
                "operation": operation,
                "status": "PASS",
                "argv": ["tushare", operation, "--ts_code", "000651.SZ"],
                "output": f"{operation} 实际执行输出：000651.SZ 数据已落盘",
            })
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

        # 本 canary 跑完整链路 start→lease 13 单元→各写报告→submit→register-summary→
    # render-html。但 submit 走 Gate 的 ingest-result → admit_bundle(check_artifacts=True)
    # → _substance_errors，而后者仍按 contract `sections` 计数（lean 已移除），
    # 导致 min_substantive_sections 永远无法满足、任何 PASS 报告都 ingest 失败
    # （tools/full_analysis_gate.py:971）。impl 修复后移除本装饰器即可转绿。
    def test_single_company_canary_closes_all_thirteen_units(self):
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
             ev_receipts, ev_capabilities) = build_compliant_evidence(
                REGISTRY, skill_id, self.run_root)
            bundle = {
                "schema_version": "result-schema/v1", "run_id": run_id,
                "work_unit_id": lease["work_unit_id"], "attempt_id": lease["attempt_id"],
                "agent_job_id": f"job-{lease['attempt_id']}", "lease_nonce": lease["lease_nonce"],
                "skill_id": skill_id, "role_id": None, "status": "PASS",
                "artifact_records": [{"artifact_id": by_id[skill_id]["artifact"].get("artifact_id", f"artifact.{skill_id}"),
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
                "evidence_digest": brief["evidence_sha256"],
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
        html_path = (
            self.run_root / final_manifest["delivery"]["summary"]["path"]
        ).with_suffix(".html")
        self.assertTrue(html_path.is_file())
        html = html_path.read_text(encoding="utf-8")
        self.assertIn("格力电器", html)
        self.assertIn("000651.SZ", html)
        self.assertIn("2026-07-23", html)
        company_base = self.run_root.parent.parent
        index_path = company_base / "index.html"
        self.assertTrue(index_path.is_file())
        index_html = index_path.read_text(encoding="utf-8")
        self.assertIn("格力电器", index_html)
        self.assertIn("000651.SZ", index_html)


if __name__ == "__main__":
    unittest.main()
