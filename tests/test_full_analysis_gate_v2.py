import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "tools" / "full_analysis_gate.py"
AUDIT = REPO / "tools" / "full_analysis_audit.py"
REGISTRY = REPO / "tools" / "full_analysis_contract.json"
sys.path.insert(0, str(REPO / "tools"))
import full_analysis_gate as gate_module  # noqa: E402


def run_gate(root, *args):
    return subprocess.run(
        [sys.executable, str(GATE), *map(str, args)],
        cwd=root,
        capture_output=True,
        text=True,
    )


ROLE_CN = {
    "duan": "段永平", "buffett": "巴菲特", "munger": "芒格", "li": "李录",
    "editor": "编辑", "reader": "读者", "company": "公司", "regulatory": "监管",
    "industry": "行业", "sentiment": "情绪", "governance": "治理", "business": "业务",
    "technology": "技术", "finance": "财务", "alternative-data": "另类",
}
BOILERPLATE = {"研究免责", "仅供学习研究", "数据截止日", "命令执行记录", "下游证据", "契约计算"}


def build_compliant_report(registry_path, skill_id):
    """按 contract 必需要素小节生成能通过实质校验的达标报告（真回归测试用）。"""
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
    return "".join(lines)


class GateV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.run_root = self.root / "local/company/000651.SZ-格力电器/20260723-120000-ab12"

    def tearDown(self):
        self.temp.cleanup()

    def init(self):
        result = run_gate(
            self.root, "init", "--registry", REGISTRY, "--repo-root", self.root,
            "--company", "格力电器", "--code", "000651.SZ", "--as-of", "2026-07-23",
            "--platform", "workbuddy",
            "--run-root", self.run_root,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_init_creates_canonical_manifest_and_uniform_intermediate_dirs(self):
        self.init()
        manifest_path = self.run_root / "evidence/00-analysis-manifest.json"
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_schema_version"], "full-analysis-manifest/v2")
        self.assertEqual(manifest["run"]["status"], "RUNNING")
        self.assertEqual(manifest["company"]["code"], "000651.SZ")
        self.assertEqual(len(manifest["skills"]), 13)
        self.assertTrue((self.run_root / "evidence/attempts").is_dir())
        self.assertTrue((self.run_root / "evidence/work-packets").is_dir())
        self.assertTrue((self.run_root / "04-论文与组合").is_dir())
        self.assertFalse((self.run_root / "manifest.json").exists())

    def test_ingest_promotes_attempt_artifact_and_updates_skill_atomically(self):
        self.init()
        attempt_dir = self.run_root / "evidence/attempts/ashare-data/attempt-01"
        attempt_dir.mkdir(parents=True)
        source = attempt_dir / "result.md"
        source.write_text(build_compliant_report(REGISTRY, "ashare-data"), encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        result_bundle = attempt_dir / "result.json"
        result_bundle.write_text(json.dumps({
            "schema_version": "result-schema/v1",
            "run_id": json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())["run"]["run_id"],
            "work_unit_id": "wu-ashare-data",
            "attempt_id": "attempt-01",
            "agent_job_id": "job-01",
            "lease_nonce": "lease-01",
            "skill_id": "ashare-data",
            "role_id": None,
            "status": "PASS",
            "artifact_records": [{
                "artifact_id": "artifact.ashare-data",
                "path": str(source.relative_to(self.run_root)),
                "bytes": source.stat().st_size,
                "sha256": digest,
                "formal": False,
                "accepted": False,
            }],
            "fact_updates": [], "source_records": [], "calculation_requests": [],
            "judgments": [], "limitations": [], "pwl_candidates": [],
            "started_at": "2026-07-23T12:00:00+08:00",
            "completed_at": "2026-07-23T12:01:00+08:00", "error": None,
        }, ensure_ascii=False), encoding="utf-8")
        ingested = run_gate(self.root, "ingest-result", "--run-root", self.run_root,
                            "--registry", REGISTRY, "--result", result_bundle)
        self.assertEqual(ingested.returncode, 0, ingested.stdout + ingested.stderr)
        formal = self.run_root / "01-数据与快筛/01-ashare-data.md"
        self.assertTrue(formal.is_file())
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        skill = next(item for item in manifest["skills"] if item["skill_id"] == "ashare-data")
        self.assertEqual(skill["status"], "PASS")
        self.assertEqual(skill["artifact_records"][0]["sha256"], digest)

    def _bundle(self, *, skill_id, attempt_id, artifact_id, rel, size, digest,
                status="PASS", error=None, not_applicable=None):
        run_id = json.loads((self.run_root / "evidence/00-analysis-manifest.json")
                            .read_text())["run"]["run_id"]
        return {
            "schema_version": "result-schema/v1", "run_id": run_id,
            "work_unit_id": f"wu-{skill_id}", "attempt_id": attempt_id,
            "agent_job_id": f"job-{attempt_id}", "lease_nonce": f"lease-{attempt_id}",
            "skill_id": skill_id, "role_id": None, "status": status,
            "artifact_records": [{"artifact_id": artifact_id, "path": rel,
                                  "bytes": size, "sha256": digest,
                                  "formal": False, "accepted": False}],
            "fact_updates": [], "source_records": [], "calculation_requests": [],
            "judgments": [], "limitations": [], "pwl_candidates": [],
            "started_at": "2026-07-23T12:00:00+08:00",
            "completed_at": "2026-07-23T12:01:00+08:00", "error": error,
            "not_applicable": not_applicable,
        }

    def _write_attempt(self, skill_id, attempt_id, body):
        attempt_dir = self.run_root / "evidence/attempts" / skill_id / attempt_id
        attempt_dir.mkdir(parents=True, exist_ok=True)
        source = attempt_dir / "result.md"
        source.write_text(body, encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        bundle_path = attempt_dir / "result.json"
        return bundle_path, str(source.relative_to(self.run_root)), source.stat().st_size, digest

    def _ingest(self, bundle_path, bundle):
        bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        return run_gate(self.root, "ingest-result", "--run-root", self.run_root,
                        "--registry", REGISTRY, "--result", bundle_path)

    def test_rejected_second_attempt_does_not_overwrite_formal_artifact(self):
        """P0：被拒的第二次 attempt 不得覆盖已晋级的正式文件（ingest 事务性）。"""
        self.init()
        skill_id = "ashare-data"
        formal = self.run_root / "01-数据与快筛/01-ashare-data.md"
        good_body = build_compliant_report(REGISTRY, skill_id)

        # 第一次：合规 attempt → 晋级
        bp, rel, size, digest = self._write_attempt(skill_id, "attempt-01", good_body)
        r1 = self._ingest(bp, self._bundle(
            skill_id=skill_id, attempt_id="attempt-01", artifact_id="artifact.ashare-data",
            rel=rel, size=size, digest=digest))
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        self.assertTrue(formal.is_file())
        self.assertEqual(hashlib.sha256(formal.read_bytes()).hexdigest(), digest)
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        entry = next(s for s in manifest["skills"] if s["skill_id"] == skill_id)
        self.assertEqual(entry["artifact_records"][0]["sha256"], digest)

        # 第二次：过低 attempt（触发 min_bytes 拒收）
        low_body = "# ashare-data\n过浅\n"
        bp2, rel2, size2, digest2 = self._write_attempt(skill_id, "attempt-02", low_body)
        r2 = self._ingest(bp2, self._bundle(
            skill_id=skill_id, attempt_id="attempt-02", artifact_id="artifact.ashare-data",
            rel=rel2, size=size2, digest=digest2))
        self.assertNotEqual(r2.returncode, 0, "低质量 attempt 应被拒收")
        self.assertIn("防坍塌下限", r2.stdout + r2.stderr)

        # 关键断言：正式文件仍是第一次的合规内容，manifest 哈希不变
        self.assertEqual(hashlib.sha256(formal.read_bytes()).hexdigest(), digest,
                         "被拒 attempt 不得覆盖正式文件")
        self.assertNotEqual(hashlib.sha256(formal.read_bytes()).hexdigest(), digest2)
        manifest2 = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        entry2 = next(s for s in manifest2["skills"] if s["skill_id"] == skill_id)
        self.assertEqual(entry2["artifact_records"][0]["sha256"], digest)

    def test_malformed_nested_bundle_is_rejected_before_formal_copy(self):
        """嵌套非法 Result Bundle 必须在写正式文件前拒绝。"""
        self.init()
        skill_id = "ashare-data"
        bundle_path, rel, size, digest = self._write_attempt(
            skill_id, "attempt-bad", build_compliant_report(REGISTRY, skill_id))
        bundle = self._bundle(
            skill_id=skill_id,
            attempt_id="attempt-bad",
            artifact_id="artifact.ashare-data",
            rel=rel,
            size=size,
            digest=digest,
        )
        bundle["fact_updates"] = ["not-an-object"]

        result = self._ingest(bundle_path, bundle)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("$.fact_updates[0]", result.stdout + result.stderr)
        self.assertFalse(
            (self.run_root / "01-数据与快筛/01-ashare-data.md").exists())
        manifest = json.loads(
            (self.run_root / "evidence/00-analysis-manifest.json").read_text())
        entry = next(
            item for item in manifest["skills"]
            if item["skill_id"] == skill_id
        )
        self.assertEqual(entry["status"], "PENDING")

    def test_finalize_rejects_when_formal_artifact_tampered(self):
        """P0：finalize 必须复核正式文件哈希，被篡改/覆盖即拒绝准出。"""
        self.init()
        skill_id = "ashare-data"
        bp, rel, size, digest = self._write_attempt(
            skill_id, "attempt-01", build_compliant_report(REGISTRY, skill_id))
        r1 = self._ingest(bp, self._bundle(
            skill_id=skill_id, attempt_id="attempt-01", artifact_id="artifact.ashare-data",
            rel=rel, size=size, digest=digest))
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        formal = self.run_root / "01-数据与快筛/01-ashare-data.md"
        # 模拟外部污染：直接篡改正式文件
        formal.write_text("被外部篡改的内容\n", encoding="utf-8")
        result = run_gate(self.root, "finalize", "--run-root", self.run_root,
                          "--registry", REGISTRY)
        self.assertNotEqual(result.returncode, 0, "哈希不一致时 finalize 必须拒绝")
        self.assertIn("哈希不一致", result.stdout + result.stderr)

    def test_finalize_rejects_incomplete_run_loudly(self):
        self.init()
        result = run_gate(self.root, "finalize", "--run-root", self.run_root,
                          "--registry", REGISTRY)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PENDING", result.stdout + result.stderr)

    def test_finalize_closes_completed_failed_run_as_failed(self):
        self.init()
        manifest_path = self.run_root / "evidence/00-analysis-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["skills"]:
            item["status"] = "NOT_APPLICABLE"
        manifest["skills"][0]["status"] = "FAIL"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        result = run_gate(
            self.root, "finalize", "--run-root", self.run_root,
            "--registry", REGISTRY,
        )

        self.assertNotEqual(result.returncode, 0)
        final = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(final["run"]["status"], "FAILED")
        self.assertIn("ashare-data", result.stdout + result.stderr)

    def test_register_summary_binds_human_delivery_to_manifest(self):
        self.init()
        manifest_path = self.run_root / "evidence/00-analysis-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["skills"]:
            item["status"] = "NOT_APPLICABLE"
            formal = self.run_root / f"06-负向验收/{item['skill_id']}.md"
            formal.write_text("负向验收测试产物", encoding="utf-8")
            item["artifact_records"] = [{
                "artifact_id": f"artifact.na.{item['skill_id']}",
                "path": str(formal.relative_to(self.run_root)),
                "bytes": formal.stat().st_size,
                "sha256": hashlib.sha256(formal.read_bytes()).hexdigest(),
                "formal": True,
                "accepted": True,
            }]
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False),
                                 encoding="utf-8")
        summary_dir = self.run_root / "evidence/attempts/summary"
        summary_dir.mkdir(parents=True)
        summary = summary_dir / "summary.md"
        body = "# 核心结论速览\n" + "\n".join(
            f"## {heading}\n{heading}内容均来自正式产物。" * 100
            for heading in (
                "主干①·投资分析", "主干②·财报研读", "主干③·行业分析",
                "补充与参考", "产物索引", "数据截止日", "仅供学习研究",
            )
        ) + "\n" + "\n".join(
            record["path"]
            for item in manifest["skills"]
            for record in item["artifact_records"]
        )
        summary.write_text(body, encoding="utf-8")

        registered = run_gate(
            self.root, "register-summary", "--run-root", self.run_root,
            "--registry", REGISTRY, "--summary", summary,
        )

        self.assertEqual(registered.returncode, 0,
                         registered.stdout + registered.stderr)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = manifest["delivery"]["summary"]
        self.assertTrue(record["accepted"])
        self.assertEqual(record["sha256"],
                         hashlib.sha256(summary.read_bytes()).hexdigest())
        self.assertTrue((self.run_root / record["path"]).is_file())

    def test_finalize_rejects_terminal_run_without_registered_summary(self):
        self.init()
        manifest_path = self.run_root / "evidence/00-analysis-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["skills"]:
            item["status"] = "NOT_APPLICABLE"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False),
                                 encoding="utf-8")

        result = run_gate(
            self.root, "finalize", "--run-root", self.run_root,
            "--registry", REGISTRY,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("总结报告", result.stdout + result.stderr)

    def test_review_gate_rejects_custom_scope_that_omits_required_targets(self):
        self.init()
        review_dir = self.run_root / "evidence/review"
        review_dir.mkdir(parents=True)
        (review_dir / "review-index.json").write_text(json.dumps({
            "run_id": "test-run",
            "scope": [],
            "briefs": {},
        }), encoding="utf-8")
        (review_dir / "review-result-placeholder.json").write_text(
            "{}", encoding="utf-8")

        result = gate_module._run_review_gate(self.run_root, REGISTRY)

        self.assertEqual(result["status"], "incomplete")
        self.assertIn("delivery-summary", result["missing_scope"])
        self.assertIn("investment-research", result["missing_scope"])

    def test_ingest_rejects_not_applicable_without_gate_verifiable_proof(self):
        self.init()
        skill_id = "quality-screen"
        bp, rel, size, digest = self._write_attempt(
            skill_id, "attempt-na-empty", "# 不适用结论\n无\n")
        bundle = self._bundle(
            skill_id=skill_id, attempt_id="attempt-na-empty",
            artifact_id=f"artifact.na.{skill_id}", rel=rel, size=size,
            digest=digest, status="NOT_APPLICABLE",
        )

        result = self._ingest(bp, bundle)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not_applicable", result.stdout + result.stderr)

    def test_ingest_rejects_not_applicable_for_always_applicable_skill(self):
        self.init()
        skill_id = "investment-research"
        body = "\n".join([
            "# 不适用结论", "无法执行。" * 80,
            "## 判定事实", "事实。" * 80,
            "## 证据来源", "来源。" * 80,
            "## 替代路径", "替代。" * 80,
            "## 限制", "限制。" * 80,
        ])
        bp, rel, size, digest = self._write_attempt(
            skill_id, "attempt-na-always", body)
        bundle = self._bundle(
            skill_id=skill_id, attempt_id="attempt-na-always",
            artifact_id=f"artifact.na.{skill_id}", rel=rel, size=size,
            digest=digest, status="NOT_APPLICABLE",
            not_applicable={
                "predicate": "always_applicable",
                "fact_id": "fact.investment-research.applicable",
                "alternative": None,
            },
        )
        bundle["fact_updates"] = [{
            "fact_id": "fact.investment-research.applicable",
            "field": "always_applicable", "value": False,
            "source_ids": ["src.fake"],
        }]
        bundle["source_records"] = [{
            "source_id": "src.fake", "url": "https://example.invalid/fake",
            "retrieved_at": "2026-07-25", "source_type": "other",
        }]
        bundle["limitations"] = [{"code": "not_applicable", "detail": "测试"}]

        result = self._ingest(bp, bundle)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("始终适用", result.stdout + result.stderr)

    def test_ingest_accepts_gate_verified_not_applicable_and_promotes_negative_report(self):
        self.init()
        skill_id = "quality-screen"
        body = "\n".join([
            "# 不适用结论", "缺少可比财务历史，无法执行质量筛选。" * 60,
            "## 判定事实", "可比财务历史不可得。" * 60,
            "## 证据来源", "交易所披露记录仅覆盖当前期间。" * 60,
            "## 替代路径", "转入限制清单并保留后续复核。" * 60,
            "## 限制", "结论只说明适用性，不代表公司质量。" * 60,
        ])
        bp, rel, size, digest = self._write_attempt(
            skill_id, "attempt-na-valid", body)
        bundle = self._bundle(
            skill_id=skill_id, attempt_id="attempt-na-valid",
            artifact_id=f"artifact.na.{skill_id}", rel=rel, size=size,
            digest=digest, status="NOT_APPLICABLE",
            not_applicable={
                "predicate": "has_comparable_financial_history",
                "fact_id": "fact.quality-screen.comparable-history",
                "alternative": None,
            },
        )
        bundle["fact_updates"] = [{
            "fact_id": "fact.quality-screen.comparable-history",
            "field": "has_comparable_financial_history", "value": False,
            "source_ids": ["src.quality-screen.filing-index"],
        }]
        bundle["source_records"] = [{
            "source_id": "src.quality-screen.filing-index",
            "url": "https://example.invalid/filing-index",
            "retrieved_at": "2026-07-25", "source_type": "filing",
        }]
        bundle["limitations"] = [{
            "code": "not_applicable",
            "detail": "可比财务历史不可得，已使用负向验收。",
        }]

        result = self._ingest(bp, bundle)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        formal = self.run_root / "06-负向验收/quality-screen.md"
        self.assertTrue(formal.is_file())
        manifest = json.loads(
            (self.run_root / "evidence/00-analysis-manifest.json").read_text())
        entry = next(
            item for item in manifest["skills"]
            if item["skill_id"] == skill_id
        )
        self.assertEqual(entry["status"], "NOT_APPLICABLE")
        self.assertEqual(entry["not_applicable"]["predicate"],
                         "has_comparable_financial_history")
        self.assertEqual(entry["artifact_records"][0]["path"],
                         "06-负向验收/quality-screen.md")

    def test_ingest_rejects_single_padded_section_when_contract_requires_structure(self):
        self.init()
        skill_id = "quality-screen"
        body = "# 伪合格报告\n\n## 唯一正文\n" + ("风险 数据详实 " * 2500)
        bp, rel, size, digest = self._write_attempt(skill_id, "attempt-padding", body)
        result = self._ingest(bp, self._bundle(
            skill_id=skill_id,
            attempt_id="attempt-padding",
            artifact_id="artifact.quality-screen",
            rel=rel,
            size=size,
            digest=digest,
        ))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("必需章节", result.stdout + result.stderr)

    def test_ingest_derives_role_runs_from_verified_memos(self):
        self.init()
        skill_id = "investment-team"
        attempt_id = "attempt-roles"
        bp, rel, size, digest = self._write_attempt(
            skill_id, attempt_id, build_compliant_report(REGISTRY, skill_id))
        attempt_dir = bp.parent
        required_roles = ["duan", "buffett", "munger", "li"]
        memo_digests = {}
        for role in required_roles:
            memo = attempt_dir / f"role-{role}.md"
            memo.write_text((f"{role} 独立研究备忘录，包含证据、反证与结论。" * 30),
                            encoding="utf-8")
            memo_digests[role] = hashlib.sha256(memo.read_bytes()).hexdigest()
        bundle = self._bundle(
            skill_id=skill_id, attempt_id=attempt_id,
            artifact_id="artifact.investment-team",
            rel=rel, size=size, digest=digest,
        )
        bundle["role_runs"] = [
            {"role_id": "fabricated-role", "status": "PASS"},
        ]

        result = self._ingest(bp, bundle)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = json.loads(
            (self.run_root / "evidence/00-analysis-manifest.json").read_text())
        role_runs = [
            record for record in manifest["role_runs"]
            if record.get("skill_id") == skill_id
        ]
        self.assertEqual({record["role_id"] for record in role_runs},
                         set(required_roles))
        for record in role_runs:
            self.assertEqual(record["sha256"], memo_digests[record["role_id"]])
            self.assertTrue((self.run_root / record["artifact_path"]).is_file())
            self.assertTrue(record["verified_by_gate"])

    def test_finalize_rejects_audit_created_before_later_ingest(self):
        self.init()
        manifest_path = self.run_root / "evidence/00-analysis-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        for item in manifest["skills"]:
            item["status"] = "NOT_APPLICABLE"
        summary = self.run_root / "格力电器-全量分析-总结报告.md"
        summary.write_text("测试总结", encoding="utf-8")
        manifest["delivery"] = {"summary": {
            "artifact_id": "artifact.delivery-summary",
            "path": summary.name,
            "bytes": summary.stat().st_size,
            "sha256": hashlib.sha256(summary.read_bytes()).hexdigest(),
            "formal": True,
            "accepted": True,
        }}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        audited = subprocess.run(
            [sys.executable, str(AUDIT), "--run-root", str(self.run_root),
             "--registry", str(REGISTRY)],
            cwd=self.root, capture_output=True, text=True,
        )
        self.assertEqual(audited.returncode, 0, audited.stdout + audited.stderr)

        skill_id = "ashare-data"
        bp, rel, size, digest = self._write_attempt(
            skill_id, "attempt-after-audit", build_compliant_report(REGISTRY, skill_id))
        ingested = self._ingest(bp, self._bundle(
            skill_id=skill_id,
            attempt_id="attempt-after-audit",
            artifact_id="artifact.ashare-data",
            rel=rel,
            size=size,
            digest=digest,
        ))
        self.assertEqual(ingested.returncode, 0, ingested.stdout + ingested.stderr)

        finalized = run_gate(
            self.root, "finalize", "--run-root", self.run_root,
            "--registry", REGISTRY,
        )

        self.assertNotEqual(finalized.returncode, 0)
        self.assertIn("Audit 快照", finalized.stdout + finalized.stderr)


if __name__ == "__main__":
    unittest.main()
