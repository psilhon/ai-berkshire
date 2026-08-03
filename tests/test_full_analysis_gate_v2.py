import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


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
        # tempdir 无 git 环境 → _git_stale_check 返回 stale=None；
        # v3.4.9 起 None 也拒绝（fail-close），故测试 helper 显式 --allow-stale。
        result = run_gate(
            self.root, "init", "--registry", REGISTRY, "--repo-root", self.root,
            "--company", "格力电器", "--code", "000651.SZ", "--as-of", "2026-07-23",
            "--platform", "workbuddy",
            "--run-root", self.run_root, "--allow-stale",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_init_fail_close_on_stale_none_and_true(self):
        # v3.4.9：E1 fail-close——stale=None（不可判定）与 stale=True（落后）均拒绝，
        # 拒绝发生在任何落盘之前；唯一放行路径是显式 --allow-stale。
        # 注：_git_stale_check 检查 Gate 脚本所在仓库，故用 mock 注入三态。
        for stale_value, detail in ((None, "mock 不可判定"), (True, "mock 落后于最新 tag")):
            with mock.patch.object(
                gate_module, "_git_stale_check",
                return_value={"stale": stale_value, "head": "x", "head_tag": None,
                              "latest_tag": None, "detail": detail},
            ):
                rc = gate_module.main([
                    "init", "--registry", str(REGISTRY), "--repo-root", str(self.root),
                    "--company", "格力电器", "--code", "000651.SZ", "--as-of", "2026-07-23",
                    "--platform", "workbuddy", "--run-root", str(self.run_root),
                ])
            self.assertEqual(rc, 2, f"stale={stale_value} 应拒绝")
            self.assertFalse(self.run_root.exists(), f"stale={stale_value} 不得落盘")
        # --allow-stale 覆盖后放行
        with mock.patch.object(
            gate_module, "_git_stale_check",
            return_value={"stale": None, "head": "x", "head_tag": None,
                          "latest_tag": None, "detail": "mock 不可判定"},
        ):
            rc = gate_module.main([
                "init", "--registry", str(REGISTRY), "--repo-root", str(self.root),
                "--company", "格力电器", "--code", "000651.SZ", "--as-of", "2026-07-23",
                "--platform", "workbuddy", "--run-root", str(self.run_root),
                "--allow-stale",
            ])
        self.assertEqual(rc, 0)
        self.assertTrue((self.run_root / "evidence/00-analysis-manifest.json").is_file())

    def test_build_run_root_uses_canonical_company_directory(self):
        root = gate_module.build_run_root(
            self.root, "600001.SH", "测试公司")
        self.assertEqual(root.parts[-4:-2], ("local", "Company"))

    def test_atomic_write_text_failure_preserves_previous_file(self):
        target = self.root / "summary.html"
        target.write_text("previous", encoding="utf-8")

        with mock.patch.object(
            gate_module.os, "replace", side_effect=OSError("replace failed")
        ):
            with self.assertRaises(OSError):
                gate_module.atomic_write_text(target, "new")

        self.assertEqual(target.read_text(encoding="utf-8"), "previous")

    @unittest.skipIf(os.name == "nt", "POSIX 文件权限断言")
    def test_atomic_replacements_preserve_mode_and_use_readable_default(self):
        existing = self.root / "summary.html"
        existing.write_text("previous", encoding="utf-8")
        existing.chmod(0o640)
        gate_module.atomic_write_text(existing, "new")

        created = self.root / "state.json"
        gate_module.atomic_write_json(created, {"ok": True})

        source = self.root / "source.md"
        source.write_text("source", encoding="utf-8")
        copied = self.root / "copied.md"
        gate_module.atomic_copy(source, copied)

        self.assertEqual(stat.S_IMODE(existing.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(created.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(copied.stat().st_mode), 0o644)

    def test_rebuild_company_index_writes_to_run_owned_base(self):
        source_repo = self.root / "source"
        source_tools = source_repo / "tools"
        source_scripts = source_repo / "scripts"
        source_tools.mkdir(parents=True)
        source_scripts.mkdir(parents=True)
        (source_scripts / "build_company_index.py").write_bytes(
            (REPO / "scripts/build_company_index.py").read_bytes())

        company_base = self.root / "target/local/Company"
        run_root = (
            company_base
            / "600001.SH-测试公司"
            / "20260727-120000-aaaaaa"
        )
        run_root.mkdir(parents=True)
        (run_root / "测试公司-全量分析-总结报告.md").write_text(
            "# 测试公司\n\n一句话总结：建议持有。\n\n"
            "数据截止日：2026-07-27。\n",
            encoding="utf-8",
        )

        with mock.patch.object(gate_module, "TOOLS_DIR", source_tools):
            rebuilt = gate_module._rebuild_company_index(run_root)

        self.assertTrue(rebuilt)
        self.assertTrue((company_base / "index.html").is_file())

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

    def test_provenance_prepare_error_does_not_copy_formal_artifact(self):
        self.init()
        skill_id = "ashare-data"
        good_path, good_rel, good_size, good_digest = self._write_attempt(
            skill_id,
            "attempt-good",
            build_compliant_report(REGISTRY, skill_id),
        )
        accepted = self._ingest(good_path, self._bundle(
            skill_id=skill_id,
            attempt_id="attempt-good",
            artifact_id="artifact.ashare-data",
            rel=good_rel,
            size=good_size,
            digest=good_digest,
        ))
        self.assertEqual(
            accepted.returncode, 0, accepted.stdout + accepted.stderr)
        formal = self.run_root / "01-数据与快筛/01-ashare-data.md"

        bundle_path, rel, size, digest = self._write_attempt(
            skill_id,
            "attempt-provenance-error",
            build_compliant_report(REGISTRY, skill_id) + "\n第二次尝试",
        )
        bundle = self._bundle(
            skill_id=skill_id,
            attempt_id="attempt-provenance-error",
            artifact_id="artifact.ashare-data",
            rel=rel,
            size=size,
            digest=digest,
        )
        bundle_path.write_text(
            json.dumps(bundle, ensure_ascii=False), encoding="utf-8")

        with mock.patch.object(
            gate_module,
            "_merge_provenance",
            side_effect=RuntimeError("provenance prepare failed"),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "provenance prepare failed"):
                gate_module.cmd_ingest(SimpleNamespace(
                    run_root=self.run_root,
                    registry=REGISTRY,
                    result=bundle_path,
                ))

        self.assertEqual(
            hashlib.sha256(formal.read_bytes()).hexdigest(), good_digest)
        manifest = json.loads(
            (self.run_root / "evidence/00-analysis-manifest.json").read_text())
        entry = next(
            item for item in manifest["skills"]
            if item["skill_id"] == skill_id
        )
        self.assertEqual(entry["status"], "PASS")
        self.assertEqual(entry["attempts"], ["attempt-good"])
        self.assertEqual(
            entry["artifact_records"][0]["sha256"], good_digest)

    def test_atomic_copy_rejects_content_changed_after_validation(self):
        source = self.root / "source.md"
        target = self.root / "formal.md"
        source.write_text("validated content", encoding="utf-8")
        target.write_text("existing formal content", encoding="utf-8")
        existing = target.read_bytes()
        expected_bytes = source.stat().st_size
        expected_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()

        def copy_changed_content(_source_handle, target_handle):
            target_handle.write(b"changed after validation")

        with mock.patch.object(
            gate_module.shutil,
            "copyfileobj",
            side_effect=copy_changed_content,
        ):
            with self.assertRaises(gate_module.GateError):
                gate_module.atomic_copy(
                    source,
                    target,
                    expected_bytes=expected_bytes,
                    expected_sha256=expected_sha256,
                )

        self.assertEqual(target.read_bytes(), existing)

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

    def test_substance_diagnostic_skips_blank_lines_before_h3(self):
        skill = {
            "sections": [{
                "section_id": "core",
                "heading": "核心结论",
                "required": True,
                "min_content_chars": 150,
            }],
            "min_substantive_sections": 1,
        }
        errors = gate_module._substance_errors(
            skill, "## 核心结论\n\n### 子标题\n正文")
        self.assertTrue(any("后紧跟 ###" in item for item in errors))

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

    # ---- E4/E6/E7：前置账本校验 / 跨 skill 覆盖告警 / schema 报错友好化 ----

    def _mk_bundle(self, skill_id, attempt, facts=None, judgments=None, caps=None,
                   receipts=None, calcs=None):
        """构造最小可 ingest 的 PASS bundle（可覆盖账本字段）。"""
        self.init()
        attempt_dir = self.run_root / f"evidence/attempts/{skill_id}/{attempt}"
        attempt_dir.mkdir(parents=True)
        source = attempt_dir / "report.md"
        source.write_text(build_compliant_report(REGISTRY, skill_id), encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        run_id = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())["run"]["run_id"]
        bundle = {
            "schema_version": "result-schema/v1", "run_id": run_id,
            "work_unit_id": f"wu-{skill_id}", "attempt_id": attempt,
            "agent_job_id": f"job-{attempt}", "lease_nonce": "lease-x",
            "skill_id": skill_id, "role_id": None, "status": "PASS",
            "artifact_records": [{
                "artifact_id": f"artifact.{skill_id}", "path": str(source.relative_to(self.run_root)),
                "bytes": source.stat().st_size, "sha256": digest, "formal": False, "accepted": False,
            }],
            "fact_updates": facts if facts is not None else [],
            "source_records": [], "calculation_requests": calcs if calcs is not None else [],
            "judgments": judgments if judgments is not None else [],
            "capability_records": caps if caps is not None else [],
            "command_receipts": receipts if receipts is not None else [],
            "limitations": [], "pwl_candidates": [],
            "started_at": "2026-07-23T12:00:00+08:00",
            "completed_at": "2026-07-23T12:01:00+08:00", "error": None,
        }
        result_path = attempt_dir / "result.json"
        result_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        return result_path

    def test_e4_precheck_rejects_missing_required_fact_fields(self):
        # ashare-data 契约 required_fact_fields=[price, market_cap, revenue]，
        # 缺 revenue 时应在 submit 前置校验被拒（而非等 audit）
        rp = self._mk_bundle("ashare-data", "attempt-e4a", facts=[
            {"fact_id": "fact.ashare.price", "field": "price", "value": 1.0, "source_ids": ["s1"]},
            {"fact_id": "fact.ashare.market-cap", "field": "market_cap", "value": 2.0, "source_ids": ["s1"]},
        ], caps=[{"capability": "tushare_configured", "available": True}])
        ingested = run_gate(self.root, "ingest-result", "--run-root", self.run_root,
                            "--registry", REGISTRY, "--result", rp)
        self.assertNotEqual(ingested.returncode, 0)
        self.assertIn("缺必需字段", ingested.stdout + ingested.stderr)
        self.assertIn("revenue", ingested.stdout + ingested.stderr)

    def test_e4_precheck_rejects_missing_capability_attestation(self):
        # capability 名错配（tushare 而非契约值 tushare_configured）应在提交时被拒
        rp = self._mk_bundle("ashare-data", "attempt-e4b", facts=[
            {"fact_id": "fact.ashare.price", "field": "price", "value": 1.0, "source_ids": ["s1"]},
            {"fact_id": "fact.ashare.market-cap", "field": "market_cap", "value": 2.0, "source_ids": ["s1"]},
            {"fact_id": "fact.ashare.revenue", "field": "revenue", "value": 3.0, "source_ids": ["s1"]},
        ], caps=[{"capability": "tushare", "available": True}])
        ingested = run_gate(self.root, "ingest-result", "--run-root", self.run_root,
                            "--registry", REGISTRY, "--result", rp)
        self.assertNotEqual(ingested.returncode, 0)
        self.assertIn("capability", ingested.stdout + ingested.stderr)

    def test_e4_precheck_passes_when_accounting_aligned(self):
        rp = self._mk_bundle("ashare-data", "attempt-e4c", facts=[
            {"fact_id": "fact.ashare.price", "field": "price", "value": 1.0, "source_ids": ["s1"]},
            {"fact_id": "fact.ashare.market-cap", "field": "market_cap", "value": 2.0, "source_ids": ["s1"]},
            {"fact_id": "fact.ashare.revenue", "field": "revenue", "value": 3.0, "source_ids": ["s1"]},
        ], caps=[{"capability": "tushare_configured", "available": True}])
        ingested = run_gate(self.root, "ingest-result", "--run-root", self.run_root,
                            "--registry", REGISTRY, "--result", rp)
        self.assertEqual(ingested.returncode, 0, ingested.stdout + ingested.stderr)

    def test_e6_cross_skill_override_writes_warning_event(self):
        rp1 = self._mk_bundle("ashare-data", "attempt-e6a", facts=[
            {"fact_id": "fact.ashare.price", "field": "price", "value": 1.0, "source_ids": ["s1"]},
            {"fact_id": "fact.ashare.market-cap", "field": "market_cap", "value": 2.0, "source_ids": ["s1"]},
            {"fact_id": "fact.ashare.revenue", "field": "revenue", "value": 3.0, "source_ids": ["s1"]},
        ], caps=[{"capability": "tushare_configured", "available": True}])
        self.assertEqual(run_gate(self.root, "ingest-result", "--run-root", self.run_root,
                                  "--registry", REGISTRY, "--result", rp1).returncode, 0)
        # 第二个 run_root 需复用同一 root：bundle 的 run_id 绑定 manifest；此处改造成本高，
        # 直接验证 _merge_provenance 层的事件写入（同 manifest 两次合并跨 skill 覆盖）
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        from types import SimpleNamespace as _NS
        bundle2 = json.loads(rp1.read_text(encoding="utf-8"))
        bundle2["skill_id"] = "financial-data"
        bundle2["work_unit_id"] = "wu-financial-data"
        gate_module._merge_provenance(manifest, bundle2, run_root=self.run_root)
        events = (self.run_root / "evidence/events.jsonl").read_text(encoding="utf-8")
        self.assertIn("fact_overridden", events)
        self.assertIn("from_skill", events)
        self.assertIn("ashare-data", events)

    def test_e7_schema_error_lists_allowed_keys(self):
        # command_receipts 含未知键 skill_id → 报错应列出允许键
        rp = self._mk_bundle("ashare-data", "attempt-e7", facts=[
            {"fact_id": "fact.ashare.price", "field": "price", "value": 1.0, "source_ids": ["s1"]},
            {"fact_id": "fact.ashare.market-cap", "field": "market_cap", "value": 2.0, "source_ids": ["s1"]},
            {"fact_id": "fact.ashare.revenue", "field": "revenue", "value": 3.0, "source_ids": ["s1"]},
        ], caps=[{"capability": "tushare_configured", "available": True}],
            receipts=[{"receipt_id": "r1", "operation": "quote", "status": "PASS", "skill_id": "x"}])
        ingested = run_gate(self.root, "ingest-result", "--run-root", self.run_root,
                            "--registry", REGISTRY, "--result", rp)
        self.assertNotEqual(ingested.returncode, 0)
        self.assertIn("允许键", ingested.stdout + ingested.stderr)

    def test_e10_start_pins_contract_digest_and_commit(self):
        self.init()
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        self.assertIn("registry_sha256", manifest["contract"])
        self.assertIn("contract_commit", manifest["run"])
        self.assertTrue(manifest["run"]["contract_commit"], "contract_commit 应为非空 HEAD commit")

    def test_e10_finalize_rejects_contract_mismatch(self):
        self.init()
        # 复制注册表并改动一个不影响加载的字段（min_bytes+1，schema_version 不动）
        # → finalize 必须拒绝（CONTRACT_VERSION_MISMATCH）
        tampered = self.root / "tampered-contract.json"
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        data["skills"][0]["artifact"]["min_bytes"] = data["skills"][0]["artifact"]["min_bytes"] + 1
        tampered.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        finalized = run_gate(self.root, "finalize", "--run-root", self.run_root,
                             "--registry", tampered)
        self.assertNotEqual(finalized.returncode, 0)
        self.assertIn("CONTRACT_VERSION_MISMATCH", finalized.stdout + finalized.stderr)

    def test_cost_budget_check_flags_missing_usage_and_repeated_attempts(self):
        # Task 6: 缺 usage summary / 同一 skill 重复 attempt → cost_budget 告警（非阻断）
        self.init()
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        # 缺 usage_summary + 第一个 skill 记 2 次 attempt
        self.assertNotIn("usage_summary", manifest)
        manifest["skills"][0]["attempts"] = ["attempt-a", "attempt-b"]
        gate_module.atomic_write_json(self.run_root / "evidence/00-analysis-manifest.json", manifest)
        check = gate_module._cost_budget_check(self.run_root, manifest)
        exceeded = {item["code"] for item in check.get("exceeded", [])}
        self.assertIn("missing_usage_summary", exceeded)
        self.assertIn("excessive_attempts", exceeded)
        self.assertIn("COST_BUDGET_EXCEEDED", check.get("verdict", ""))

    # ---- v3.3.9 T1：派发前参数预校验（financial_rigor dry-run） ----

    def test_calc_param_preflight_rejects_bad_argparse_params(self):
        # three-scenario 缺必需 --growth / --pe → argparse rc=2，应在 submit 前置拦截，
        # 不进 audit、不耗 attempt（根治「一个笔误毁全量」）
        rp = self._mk_bundle("financial-data", "attempt-t1a", calcs=[
            {"calculation_id": "calculation.t1.bad", "operation": "three-scenario",
             "args": {"price": 10, "eps": 1.0, "shares": 9.11}},
        ])
        ingested = run_gate(self.root, "ingest-result", "--run-root", self.run_root,
                            "--registry", REGISTRY, "--result", rp)
        self.assertNotEqual(ingested.returncode, 0)
        combined = ingested.stdout + ingested.stderr
        self.assertIn("参数", combined)
        self.assertIn("three-scenario", combined)

    def test_calc_param_preflight_allows_valid_and_business_failure(self):
        # 合法参数（含业务不通过 rc=1）应放行到 audit，预校验只拦 rc=2 参数错
        rp = self._mk_bundle("financial-data", "attempt-t1b", calcs=[
            {"calculation_id": "calculation.t1.ok", "operation": "calc", "args": {"expr": "1+1"}},
            {"calculation_id": "calculation.t1.biz", "operation": "verify-market-cap",
             "args": {"price": 10, "shares": 1, "reported": 999999}},
        ])
        ingested = run_gate(self.root, "ingest-result", "--run-root", self.run_root,
                            "--registry", REGISTRY, "--result", rp)
        self.assertEqual(ingested.returncode, 0, ingested.stdout + ingested.stderr)

    def test_calc_param_preflight_report_includes_argv_and_expected_flags(self):
        # 回传须含真实 argv + 必需参数清单，让 Agent 一次改对（T3 基础）
        rp = self._mk_bundle("financial-data", "attempt-t1c", calcs=[
            {"calculation_id": "calculation.t1.msg", "operation": "three-scenario",
             "args": {"price": 10}},
        ])
        ingested = run_gate(self.root, "ingest-result", "--run-root", self.run_root,
                            "--registry", REGISTRY, "--result", rp)
        combined = ingested.stdout + ingested.stderr
        self.assertIn("calculation.t1.msg", combined)
        self.assertIn("--growth", combined)

    # ---- v3.3.9 T2：ashare 回执完整性门禁（白名单外 PASS 操作拦截） ----

    ASHARE_FACTS = [
        {"fact_id": "fact.ashare.price", "field": "price", "value": 1.0, "source_ids": ["s1"]},
        {"fact_id": "fact.ashare.market-cap", "field": "market_cap", "value": 2.0, "source_ids": ["s1"]},
        {"fact_id": "fact.ashare.revenue", "field": "revenue", "value": 3.0, "source_ids": ["s1"]},
    ]
    ASHARE_CAPS = [{"capability": "tushare_configured", "available": True}]
    ASHARE_REQUIRED_OPS = ["quote", "financials", "valuation", "history",
                           "equity-history", "announcements", "signals"]

    def _ashare_receipts(self, extra=()):
        receipts = [
            {"receipt_id": f"receipt.{op}", "operation": op, "status": "PASS"}
            for op in self.ASHARE_REQUIRED_OPS
        ]
        return receipts + list(extra)

    def test_receipt_gate_rejects_pass_operation_outside_whitelist(self):
        # 白名单外的 PASS 操作（虚构成功/自定义操作）应在 submit 前置拦截，
        # 根治沪电 run「自定义操作不可重放」导致的整轮返工
        rp = self._mk_bundle("ashare-data", "attempt-t2a", facts=self.ASHARE_FACTS,
                             caps=self.ASHARE_CAPS, receipts=self._ashare_receipts(extra=[
                                 {"receipt_id": "receipt.custom", "operation": "custom-download",
                                  "status": "PASS"}]))
        ingested = run_gate(self.root, "ingest-result", "--run-root", self.run_root,
                            "--registry", REGISTRY, "--result", rp)
        self.assertNotEqual(ingested.returncode, 0)
        combined = ingested.stdout + ingested.stderr
        self.assertIn("custom-download", combined)
        self.assertIn("白名单", combined)

    def test_receipt_gate_ignores_non_pass_operation_outside_whitelist(self):
        # 白名单外的 FAIL/UNAVAILABLE 不构成「虚构成功」，门禁不拦（放行）
        rp = self._mk_bundle("ashare-data", "attempt-t2b", facts=self.ASHARE_FACTS,
                             caps=self.ASHARE_CAPS, receipts=self._ashare_receipts(extra=[
                                 {"receipt_id": "receipt.custom2", "operation": "custom-download",
                                  "status": "FAIL", "reason": "empty_data: 无数据"}]))
        ingested = run_gate(self.root, "ingest-result", "--run-root", self.run_root,
                            "--registry", REGISTRY, "--result", rp)
        self.assertEqual(ingested.returncode, 0, ingested.stdout + ingested.stderr)

    def test_receipt_gate_passes_all_whitelisted_operations(self):
        # required + conditional 全在白名单内 → 门禁放行
        rp = self._mk_bundle("ashare-data", "attempt-t2c", facts=self.ASHARE_FACTS,
                             caps=self.ASHARE_CAPS, receipts=self._ashare_receipts(extra=[
                                 {"receipt_id": "receipt.peband", "operation": "pe-band",
                                  "status": "PASS"}]))
        ingested = run_gate(self.root, "ingest-result", "--run-root", self.run_root,
                            "--registry", REGISTRY, "--result", rp)
        self.assertEqual(ingested.returncode, 0, ingested.stdout + ingested.stderr)

    # ---- v3.3.9 T3：门禁聚合回传（一次看全、原地改完） ----

    def test_preflight_aggregates_calc_and_receipt_errors_in_single_report(self):
        # calc 参数笔误 + 回执虚构成功同时出现时，单次回传须含两类错误 + 聚合计数，
        # 让 Agent 一轮修完（而非先拒 calc、修后再拒 receipt 的两轮）
        rp = self._mk_bundle("ashare-data", "attempt-t3a",
                             facts=self.ASHARE_FACTS, caps=self.ASHARE_CAPS,
                             calcs=[{"calculation_id": "calculation.t3.bad",
                                     "operation": "three-scenario", "args": {"price": 10}}],
                             receipts=self._ashare_receipts(extra=[
                                 {"receipt_id": "receipt.custom", "operation": "custom-download",
                                  "status": "PASS"}]))
        ingested = run_gate(self.root, "ingest-result", "--run-root", self.run_root,
                            "--registry", REGISTRY, "--result", rp)
        self.assertNotEqual(ingested.returncode, 0)
        combined = ingested.stdout + ingested.stderr
        self.assertIn("calculation.t3.bad", combined)   # 参数错条目
        self.assertIn("--growth", combined)             # 参数修复提示
        self.assertIn("custom-download", combined)      # 回执越界条目
        self.assertIn("2 处问题", combined)             # 聚合计数


class StaleCheckTests(unittest.TestCase):
    """v3.4.4：E1 机器门禁 _git_stale_check 的四种场景。"""

    @staticmethod
    def _mock_git(results):
        calls = iter(results)

        def fake_run(cmd, **kw):
            rc, out = next(calls)
            return mock.Mock(returncode=rc, stdout=out)

        return mock.patch("full_analysis_gate.subprocess.run", side_effect=fake_run)

    def test_head_is_latest_tag_not_stale(self):
        from full_analysis_gate import _git_stale_check
        with self._mock_git([
            (0, "abc123\n"),                                # rev-parse HEAD
            (0, "v3.4.3\n"),                                # describe exact-match
            (0, "v3.3.1\nv3.4.1\nv3.4.2\nv3.4.3\n"),        # tag list
        ]):
            r = _git_stale_check()
        self.assertFalse(r["stale"])
        self.assertEqual(r["head_tag"], "v3.4.3")
        self.assertEqual(r["latest_tag"], "v3.4.3")

    def test_head_ahead_of_latest_not_stale(self):
        from full_analysis_gate import _git_stale_check
        with self._mock_git([
            (0, "def456\n"),                                # rev-parse
            (1, ""),                                        # describe 失败（非 tag）
            (0, "v3.4.3\n"),                                # tag list
            (0, "0"),                                       # merge-base ancestor OK
        ]):
            r = _git_stale_check()
        self.assertFalse(r["stale"])
        self.assertEqual(r["head_tag"], None)
        self.assertEqual(r["latest_tag"], "v3.4.3")

    def test_head_behind_latest_is_stale(self):
        # 核心场景（review 问题 2）：干净的过期 checkout（HEAD=v3.4.2，最新=v3.4.3）
        from full_analysis_gate import _git_stale_check
        with self._mock_git([
            (0, "abc123\n"),                                # rev-parse
            (0, "v3.4.2\n"),                                # describe → 旧 tag
            (0, "v3.3.1\nv3.4.1\nv3.4.2\nv3.4.3\n"),        # tag list
            (1, ""),                                        # merge-base: v3.4.3 非 HEAD 祖先
        ]):
            r = _git_stale_check()
        self.assertTrue(r["stale"])
        self.assertEqual(r["head_tag"], "v3.4.2")
        self.assertEqual(r["latest_tag"], "v3.4.3")
        self.assertIn("落后于", r["detail"])

    def test_no_git_env_returns_none(self):
        # v3.4.7: git rev-parse 失败 → stale=None（WARN），不再静默放行
        from full_analysis_gate import _git_stale_check
        with self._mock_git([(1, "")]):
            r = _git_stale_check()
        self.assertIsNone(r["stale"])
        self.assertIn("git rev-parse", r["detail"])

    def test_git_error_returns_none_not_false(self):
        # 检测异常不得静默放行（stale=None 由 cmd_init 转 WARN）
        from full_analysis_gate import _git_stale_check
        with mock.patch("full_analysis_gate.subprocess.run",
                        side_effect=RuntimeError("git broken")):
            r = _git_stale_check()
        self.assertIsNone(r["stale"])
        self.assertIn("异常", r["detail"])


if __name__ == "__main__":
    unittest.main()
