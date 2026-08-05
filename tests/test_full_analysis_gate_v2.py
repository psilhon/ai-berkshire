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
AS_OF = "2026-07-23"
# lean 契约下 Gate 的「实质地板」三锚（见 full_analysis_gate._substance_errors）：
# 数据截止日（YYYY-MM-DD）、数据来源、仅供学习研究/免责。三者缺一即拒收。
SUBSTANCE_ANCHORS = (
    f"数据截止日 {AS_OF}。数据来源：Tushare 行情接口与巨潮资讯网公开披露文件。"
    "本报告仅供学习研究，不构成投资建议。\n"
)
_BODY_UNIT = (
    "该判断由公开披露数据逐项交叉核对得出，覆盖营业收入、毛利率与经营性现金流三条线索，"
    "并与同业可比公司做横向对照；同时列出反面证据与主要风险点，避免单向叙事。"
)


def build_compliant_report(registry_path, skill_id, *, omit=()):
    """生成一份能通过 lean 实质地板的达标报告（真回归测试用）。

    lean 契约已移除 sections/evidence_rules/artifact_id，Gate 不再校验固定标题，
    改为校验「实质地板」：数据截止日 + 数据来源 + 免责声明 + 足量实质章节 + 字节下限，
    扇出类另需具名分歧（>=2 角色交锋）。

    omit 用于负例构造，可取 "as_of" / "sources" / "disclaimer"。
    """
    reg = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    skill = next(s for s in reg["skills"] if s["skill_id"] == skill_id)

    anchors = ""
    if "as_of" not in omit:
        anchors += f"数据截止日 {AS_OF}。"
    if "sources" not in omit:
        anchors += "数据来源：Tushare 行情接口与巨潮资讯网公开披露文件。"
    if "disclaimer" not in omit:
        anchors += "本报告仅供学习研究，不构成投资建议。"

    lines = [f"# {skill_id} 分析报告\n", anchors + "\n"]
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

    def _ensure_init(self):
        # 幂等：仅当本 run_root 尚未初始化（无 manifest）时才 init。
        if not (self.run_root / "evidence" / "00-analysis-manifest.json").is_file():
            self.init()

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

        # _substance_errors counts contract `sections` (removed in lean) →
    # `min_substantive_sections` never satisfiable → no PASS report can ingest.
    # Remove this decorator once the gate counts real report `##` sections
    # (tools/full_analysis_gate.py:971).
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

        # _substance_errors counts contract `sections` (removed in lean) →
    # `min_substantive_sections` never satisfiable → no PASS report can ingest.
    # Remove this decorator once the gate counts real report `##` sections
    # (tools/full_analysis_gate.py:971).
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
        # v3.4.15：统一 admit_bundle 口径后，min_bytes 拒收由 _admit_artifact_checks 输出
        self.assertIn(f"artifact 字节数 {size2} < 下限", r2.stdout + r2.stderr)

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

        # _substance_errors counts contract `sections` (removed in lean) →
    # `min_substantive_sections` never satisfiable → no PASS report can ingest.
    # Remove this decorator once the gate counts real report `##` sections
    # (tools/full_analysis_gate.py:971).
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

        # _substance_errors counts contract `sections` (removed in lean) →
    # `min_substantive_sections` never satisfiable → no PASS report can ingest,
    # so the prerequisite PASS promotion this test relies on cannot succeed.
    # Remove this decorator once the gate counts real report `##` sections
    # (tools/full_analysis_gate.py:971).
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
            "skill_id": "demo", "skill_type": "analysis",
            "min_substantive_sections": 1,
            "substance": {"require_as_of": True, "require_sources": True,
                          "require_disclaimer": True},
        }
        errors = gate_module._substance_errors(
            skill, "## 核心结论\n\n### 子标题\n正文")
        self.assertTrue(any("后紧跟 ###" in item for item in errors))

    def test_substance_floor_reports_missing_credibility_anchors(self):
        """lean 实质地板：缺数据截止日/来源/免责三锚各自给出确定性拦截消息。"""
        skill = {
            "skill_id": "demo", "skill_type": "analysis",
            "min_substantive_sections": 1,
            "substance": {"require_as_of": True, "require_sources": True,
                          "require_disclaimer": True},
        }
        errors = gate_module._substance_errors(skill, "## 主题一\n" + "正文。" * 200)
        self.assertIn("缺数据截止日声明（需含 YYYY-MM-DD 形式日期）", errors)
        self.assertIn("缺数据来源声明", errors)
        self.assertIn("缺仅供学习研究/免责声明", errors)

        # _substance_errors counts contract `sections` (removed in lean) so
    # `min_substantive_sections` is never satisfiable → no PASS report can ingest.
    # Remove this decorator once the gate counts real report `##` sections
    # (see tools/full_analysis_gate.py:971).
    def test_substance_floor_passes_when_anchors_and_sections_present(self):
        """三锚齐备 + 足量实质章节的报告不得被实质地板拦截。"""
        skill = {
            "skill_id": "demo", "skill_type": "analysis",
            "min_substantive_sections": 2,
            "substance": {"require_as_of": True, "require_sources": True,
                          "require_disclaimer": True},
        }
        text = (
            f"# demo\n{SUBSTANCE_ANCHORS}"
            f"## 主题1\n{_BODY_UNIT * 6}（其一）\n"
            f"## 主题2\n{_BODY_UNIT * 6}（其二）\n"
        )
        self.assertEqual(gate_module._substance_errors(skill, text), [])

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

    def test_ingest_accepts_gate_verified_not_applicable_and_keeps_attempt_path(self):
        """lean：NA 报告不晋级到负向验收目录，证据保留在 attempt 目录并登记为未接受。"""
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
        # lean：不晋级到负向验收目录，证据保留在 attempt 目录。
        attempt_file = self.run_root / rel
        self.assertTrue(attempt_file.is_file())
        self.assertFalse((self.run_root / "06-负向验收/quality-screen.md").is_file())
        manifest = json.loads(
            (self.run_root / "evidence/00-analysis-manifest.json").read_text())
        entry = next(
            item for item in manifest["skills"]
            if item["skill_id"] == skill_id
        )
        self.assertEqual(entry["status"], "NOT_APPLICABLE")
        self.assertEqual(entry["not_applicable"]["predicate"],
                         "has_comparable_financial_history")
        # 路径必须是 attempt 路径（不晋级），且登记为未接受。
        self.assertEqual(entry["artifact_records"][0]["path"], rel)
        self.assertFalse(entry["artifact_records"][0]["formal"])
        self.assertFalse(entry["artifact_records"][0]["accepted"])

    def _reject_by_omitted_anchor(self, omit, attempt_id, expected):
        """公共夹具：除指定实质锚点外全部达标的报告，必须只因该锚点缺失被拒。"""
        self.init()
        skill_id = "quality-screen"
        body = build_compliant_report(REGISTRY, skill_id, omit=(omit,))
        bp, rel, size, digest = self._write_attempt(skill_id, attempt_id, body)
        result = self._ingest(bp, self._bundle(
            skill_id=skill_id, attempt_id=attempt_id,
            artifact_id="artifact.quality-screen",
            rel=rel, size=size, digest=digest,
        ))
        combined = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, combined)
        self.assertIn(expected, combined)

    def test_ingest_rejects_report_without_as_of_declaration(self):
        # lean 实质地板：报告没有 YYYY-MM-DD 数据截止日 → 不可发布
        self._reject_by_omitted_anchor(
            "as_of", "attempt-no-asof", "缺数据截止日声明")

    def test_ingest_rejects_report_without_source_declaration(self):
        self._reject_by_omitted_anchor(
            "sources", "attempt-no-source", "缺数据来源声明")

    def test_ingest_rejects_report_without_disclaimer(self):
        self._reject_by_omitted_anchor(
            "disclaimer", "attempt-no-disclaimer", "缺仅供学习研究/免责声明")

        # _substance_errors counts contract `sections` (removed in lean) →
    # `min_substantive_sections` never satisfiable → no PASS report can ingest,
    # so the multi-role PASS promotion this test relies on cannot succeed.
    # Remove this decorator once the gate counts real report `##` sections
    # (tools/full_analysis_gate.py:971).
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

        # _substance_errors counts contract `sections` (removed in lean) →
    # `min_substantive_sections` never satisfiable → no PASS report can ingest,
    # so the later PASS ingest this test relies on cannot succeed.
    # Remove this decorator once the gate counts real report `##` sections
    # (tools/full_analysis_gate.py:971).
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

    # ---- lean 账本自由 / 跨 skill 覆盖告警 / schema 报错友好化 ----

    def _mk_bundle(self, skill_id, attempt, facts=None, judgments=None, caps=None,
                   receipts=None, calcs=None):
        """构造最小可 ingest 的 PASS bundle（可覆盖账本字段）。"""
        self._ensure_init()
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

    def test_lean_bundle_with_empty_evidence_ledger_is_accepted(self):
        # lean 契约已移除 evidence_rules：账本形状不再被强制，空账本 bundle 在
        # 逻辑准入层（check_artifacts=False，跳过仅 PASS 触发的「实质地板」）即放行。
        # 报告才是唯一交付物——本报告独立承担可信度三锚与实质章节。
        reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
        bundle = json.loads(
            self._mk_bundle("ashare-data", "attempt-lean-empty").read_text(encoding="utf-8"))
        errs = gate_module.admit_bundle(bundle, self.run_root, reg, check_artifacts=False)
        self.assertEqual(errs, [], "\n".join(errs))

    def test_lean_bundle_keeps_agent_provided_evidence(self):
        # 账本自由不等于账本被丢弃：Agent 真实提供的 fact/source 必须经 _merge_provenance
        # 写进 manifest（lean 下不再由 evidence_rules 强制，但合并逻辑照常生效）。
        self.init()
        manifest = json.loads(
            (self.run_root / "evidence/00-analysis-manifest.json").read_text())
        bundle = {"skill_id": "ashare-data", "fact_updates": [
            {"fact_id": "fact.ashare.price", "field": "price", "value": 41.2,
             "source_ids": ["src.ashare.quote"]},
        ], "source_records": [{
            "source_id": "src.ashare.quote", "url": "https://example.invalid/quote",
            "retrieved_at": AS_OF, "source_type": "web",
        }]}
        gate_module._merge_provenance(manifest, bundle, run_root=self.run_root)
        self.assertIn("fact.ashare.price",
                      {f["fact_id"] for f in manifest["facts"]})
        self.assertIn("src.ashare.quote",
                      {s["source_id"] for s in manifest["sources"]})

    def test_cross_skill_fact_override_writes_warning_event(self):
        # 跨 skill 同 fact_id 覆盖（last-write-wins 抢归因）须留 warning 事件。
        # 直接驱动 _merge_provenance，不依赖 ingest 全链路。
        self.init()
        manifest = json.loads(
            (self.run_root / "evidence/00-analysis-manifest.json").read_text())
        fact = {"fact_id": "fact.shared.price", "field": "price", "value": 1.0,
                "source_ids": ["src.x"]}
        gate_module._merge_provenance(
            manifest, {"skill_id": "ashare-data", "fact_updates": [fact]},
            run_root=self.run_root)
        gate_module._merge_provenance(
            manifest, {"skill_id": "financial-data", "fact_updates": [fact]},
            run_root=self.run_root)
        events = (self.run_root / "evidence/events.jsonl").read_text(encoding="utf-8")
        self.assertIn("fact_overridden", events)
        self.assertIn("from_skill", events)
        self.assertIn("ashare-data", events)
        merged = next(f for f in manifest["facts"]
                      if f["fact_id"] == "fact.shared.price")
        self.assertEqual(merged["skill_id"], "financial-data")

    def test_e7_schema_error_lists_allowed_keys(self):
        # command_receipts 含未知键 skill_id → 报错应列出允许键
        rp = self._mk_bundle(
            "ashare-data", "attempt-e7",
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
        # 合法参数（含业务不通过 rc=1）在准入层应放行（参数预校验只拦 rc=2 参数错）。
        # 用 admit_bundle(check_artifacts=False) 绕过「实质地板」（仅 PASS 触发，与本测试无关），
        # 直接验证 calc 参数预校验对合法/业务失败的放行。
        reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
        bundle = self._receipt_bundle([
            {"receipt_id": "receipt.quote", "operation": "quote", "status": "PASS",
             "argv": ["tushare", "quote"], "output": "ok"}], skill_id="financial-data")
        bundle["calculation_requests"] = [
            {"calculation_id": "calculation.t1.ok", "operation": "calc", "args": {"expr": "1+1"}},
            {"calculation_id": "calculation.t1.biz", "operation": "verify-market-cap",
             "args": {"price": 10, "shares": 1, "reported": 999999}},
        ]
        errs = gate_module.admit_bundle(bundle, self.run_root, reg, check_artifacts=False)
        self.assertEqual(errs, [], "\n".join(errs))

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

    # ---- lean 契约：命令回执门禁现状 ----
    # v3 lean 重构移除了 evidence_rules，契约不再声明 required_command_operations，
    # 因此 _precheck_command_receipts 对 lean 技能直接以空 whitelist 早退（不做越界/
    # 绑定/伪造校验）。下列测试既记录「lean 技能当前不强制回执白名单」这一现状，
    # 也保留对底层回执校验函数（给定声明了操作白名单的技能）的回归覆盖。

    ASHARE_OPS = ["quote", "financials", "valuation", "history",
                  "equity-history", "announcements", "signals"]

    def _receipt_skill(self):
        """合成一个声明了命令操作白名单的技能（沿用 ashare-data 的语义），
        以便直接驱动 _precheck_command_receipts 的越界/绑定/伪造校验分支——
        这些分支在 lean 契约（无 evidence_rules）下被白名单早退跳过，但函数本身
        仍是当前 Gate 的回执校验入口，需要保留回归。"""
        return {
            "skill_id": "ashare-data",
            "skill_type": "analysis",
            "evidence_rules": [
                {"kind": "required_command_operations", "values": self.ASHARE_OPS},
            ],
            "artifact": {"artifact_id": "artifact.ashare-data",
                          "formal_path": "evidence/artifacts/ashare-data.md",
                          "min_bytes": 0},
            "substance": {},
        }

    def _receipt_bundle(self, receipts, *, calcs=None, skill_id="ashare-data"):
        return {
            "schema_version": "result-schema/v1",
            "run_id": "run-x", "work_unit_id": "wu", "attempt_id": "a",
            "agent_job_id": "j", "lease_nonce": "l",
            "skill_id": skill_id, "role_id": None, "status": "PASS",
            "artifact_records": [{
                "artifact_id": f"artifact.{skill_id}",
                "path": f"evidence/attempts/{skill_id}/a/report.md",
                "bytes": 10, "sha256": "0" * 64, "formal": False, "accepted": False,
            }],
            "fact_updates": [], "source_records": [],
            "calculation_requests": calcs if calcs is not None else [],
            "judgments": [], "role_runs": [],
            "command_receipts": receipts, "capability_records": [],
            "limitations": [], "pwl_candidates": [],
            "started_at": "2026-07-23T12:00:00+08:00",
            "completed_at": "2026-07-23T12:01:00+08:00", "error": None,
        }

    def test_receipt_precheck_is_noop_for_lean_skill_without_evidence_rules(self):
        # lean 契约 ashare-data 无 evidence_rules → 白名单为空 → _precheck_command_receipts
        # 早退返回 []；缺失 argv/output 的回执也不会被拦截（这是当前 lean 行为，需留痕）。
        lean_skill = {"skill_id": "ashare-data", "artifact": {}, "substance": {}}
        bad = [{"receipt_id": "r", "operation": "quote", "status": "PASS"}]
        self.assertEqual(
            gate_module._precheck_command_receipts(
                self._receipt_bundle(bad), lean_skill, self.run_root), [])

    # ---- lean 契约下回执绑定/伪造校验（直接驱动 _precheck_command_receipts） ----

    def test_receipt_precheck_rejects_pass_missing_argv(self):
        # 给定声明了白名单的技能，PASS 回执缺 argv（无真实执行痕迹）→ 绑定校验报错。
        receipts = [{"receipt_id": "receipt.quote", "operation": "quote",
                     "status": "PASS", "output": "out"}]
        errs = gate_module._precheck_command_receipts(
            self._receipt_bundle(receipts), self._receipt_skill(), self.run_root)
        self.assertTrue(errs)
        self.assertIn("回执无执行绑定", errs[0])

    def test_receipt_precheck_rejects_pass_missing_output(self):
        receipts = [{"receipt_id": "receipt.quote", "operation": "quote",
                     "status": "PASS", "argv": ["tushare", "quote"]}]
        errs = gate_module._precheck_command_receipts(
            self._receipt_bundle(receipts), self._receipt_skill(), self.run_root)
        self.assertTrue(errs)
        self.assertIn("回执无执行绑定", errs[0])

    def test_receipt_precheck_rejects_pass_forgery_token(self):
        # 回执正文含伪造标记（PLACEHOLDER/TEST_FIXTURE 等）→ 即便 argv/output 齐备也拦截。
        receipts = [{"receipt_id": "receipt.quote", "operation": "quote",
                     "status": "PASS", "argv": ["tushare", "quote"],
                     "output": "ok", "detail": "PLACEHOLDER::未连接真实命令"}]
        errs = gate_module._precheck_command_receipts(
            self._receipt_bundle(receipts), self._receipt_skill(), self.run_root)
        self.assertTrue(errs)
        self.assertIn("回执伪造痕迹", errs[0])

    def test_receipt_precheck_accepts_pass_with_real_binding(self):
        # argv + output 齐备且无伪造标记 → 绑定校验通过（绿）。
        receipts = [{"receipt_id": "receipt.quote", "operation": "quote",
                     "status": "PASS", "argv": ["tushare", "quote", "--ts_code", "000651.SZ"],
                     "output": "quote 实际输出已落盘"}]
        self.assertEqual(
            gate_module._precheck_command_receipts(
                self._receipt_bundle(receipts), self._receipt_skill(), self.run_root), [])

    # ---- v3.3.9 T3：门禁聚合回传（一次看全、原地改完） ----

    def test_preflight_aggregates_calc_and_receipt_errors_in_single_report(self):
        # calc 参数笔误 + 占位证据同时出现时，单次准入须聚合两类错误，
        # 让 Agent 一轮修完（而非先拒 calc、修后再拒证据的两轮）。
        # 用 check_artifacts=False 绕过「实质地板」（仅 PASS 触发，与本测试无关），
        # 直接验证 admit_bundle 的聚合逻辑。
        reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
        bundle = self._receipt_bundle([], calcs=[{
            "calculation_id": "calculation.t3.bad",
            "operation": "three-scenario", "args": {"price": 10}}])
        bundle["fact_updates"] = [{
            "fact_id": "fact.x.f1", "field": "price",
            "value": "PLACEHOLDER::x::price", "source_ids": ["src.x.primary"]}]
        errs = gate_module.admit_bundle(
            bundle, self.run_root, reg, check_artifacts=False)
        combined = "\n".join(errs)
        self.assertIn("calculation.t3.bad", combined)   # 参数错条目
        self.assertIn("--growth", combined)             # 参数修复提示
        self.assertIn("占位证据", combined)              # 占位证据条目
        self.assertGreaterEqual(len(errs), 2)           # 两类错误聚合

    def test_fail_short_report_accepted_with_relaxed_min_bytes(self):
        # Task #48：FAIL 短报告——如实上报失败时字节下限放宽到 FAIL_MIN_BYTES(200)，
        # 不得套用 PASS 的 min_bytes 拒绝（否则「生成器 rc4 但 ingest 拒收」断路重现）。
        # 直接用 admit_bundle(check_artifacts=True) 验证 FAIL 档字节下限放宽，
        # 绕过 cmd_ingest 对 negative_acceptance_dir 的引用（lean 下该键已移除，属独立 impl 问题）。
        self.init()
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        skill = next(s for s in registry["skills"] if s["skill_id"] == "ashare-data")
        attempt_dir = self.run_root / "evidence/attempts/ashare-data/attempt-fail"
        attempt_dir.mkdir(parents=True)
        source = attempt_dir / "report.md"
        source.write_text("# ashare-data 失败报告\n" + "接口连续超时，本次无法完成。" * 20,
                          encoding="utf-8")
        actual = source.stat().st_size
        self.assertGreaterEqual(actual, 200, "报告须 ≥ FAIL_MIN_BYTES")
        self.assertLess(actual, skill["artifact"]["min_bytes"],
                        "报告须 < 正常 min_bytes 才能证明 FAIL 档确实放宽")
        run_id = json.loads(
            (self.run_root / "evidence/00-analysis-manifest.json").read_text())["run"]["run_id"]
        bundle = {
            "schema_version": "result-schema/v1", "run_id": run_id,
            "work_unit_id": "wu-ashare-data", "attempt_id": "attempt-fail",
            "agent_job_id": "job-fail", "lease_nonce": "lease-x",
            "skill_id": "ashare-data", "role_id": None, "status": "FAIL",
            "artifact_records": [{
                "artifact_id": skill["artifact"].get("artifact_id", f"artifact.{skill['skill_id']}"),
                "path": str(source.relative_to(self.run_root)),
                "bytes": actual, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "formal": False, "accepted": False,
            }],
            "fact_updates": [], "source_records": [], "calculation_requests": [],
            "judgments": [], "role_runs": [], "command_receipts": [],
            "capability_records": [], "limitations": [], "pwl_candidates": [],
            "started_at": "2026-07-23T12:00:00+08:00",
            "completed_at": "2026-07-23T12:01:00+08:00",
            "error": {"code": "tushare_down", "detail": "接口连续超时", "retryable": True},
        }
        errs = gate_module.admit_bundle(bundle, self.run_root, registry, check_artifacts=True)
        self.assertEqual(errs, [], "\n".join(errs))


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


class PlaceholderEvidenceTests(unittest.TestCase):
    """v3.4.10：PLACEHOLDER 水印证据必须被预提交门禁拒收（防占位证据污染正式账本）。"""

    def test_watermarked_fact_rejected(self):
        from full_analysis_gate import _precheck_placeholder_evidence
        bundle = {"fact_updates": [{
            "fact_id": "fact.x.f1", "field": "price",
            "value": "PLACEHOLDER::x::price", "source_ids": ["src.x.primary"],
        }], "source_records": []}
        errors = _precheck_placeholder_evidence(bundle)
        self.assertEqual(len(errors), 1)
        self.assertIn("占位证据", errors[0])

    def test_watermarked_source_rejected(self):
        from full_analysis_gate import _precheck_placeholder_evidence
        bundle = {"fact_updates": [], "source_records": [{
            "source_id": "src.x.primary",
            "url": "https://example.invalid/x/placeholder-primary",
            "publisher": "PLACEHOLDER 占位一手来源（x，未核实）",
            "title": "x 结构地板占位来源——非真实检索",
        }]}
        errors = _precheck_placeholder_evidence(bundle)
        self.assertEqual(len(errors), 1)

    def test_real_evidence_passes(self):
        from full_analysis_gate import _precheck_placeholder_evidence
        bundle = {"fact_updates": [{
            "fact_id": "fact.x.f1", "field": "price",
            "value": 12.34, "source_ids": ["src.real"],
        }], "source_records": [{
            "source_id": "src.real", "url": "https://www.cninfo.com.cn/x",
            "publisher": "巨潮资讯网", "title": "x 2025 年报",
        }]}
        self.assertEqual(_precheck_placeholder_evidence(bundle), [])

    def test_generator_floor_is_empty_in_lean(self):
        # lean 契约：mk_result_bundle.build_evidence_ledger 不再合成 PLACEHOLDER 占位账本
        #（报告才是唯一交付物）。对全部 13 个技能，地板 ledger 必须为空——既不伪造证据，
        # 也不代签成功证明。占位证据的拦截仍由 _precheck_placeholder_evidence 负责
        #（见 test_watermarked_* 系列），与「是否生成占位账本」解耦。
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "mkb", str(REPO / "scripts" / "mk_result_bundle.py"))
        mkb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mkb)
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for skill in registry["skills"]:
            with self.subTest(skill=skill["skill_id"]):
                facts, sources, calcs, judgments, _roles, receipts, caps = (
                    mkb.build_evidence_ledger(skill, [], []))
                self.assertEqual(facts, [], f"{skill['skill_id']} 地板不应生成 fact")
                self.assertEqual(sources, [], f"{skill['skill_id']} 地板不应生成 source")
                self.assertEqual(calcs, [], f"{skill['skill_id']} 地板不应生成 calc")
                self.assertEqual(judgments, [], f"{skill['skill_id']} 地板不应生成 judgment")
                self.assertEqual(receipts, [], f"{skill['skill_id']} 地板不应生成回执")
                self.assertEqual(caps, [], f"{skill['skill_id']} 地板不应生成能力记录")

    def test_precheck_rejects_each_evidence_category(self):
        """v3.4.13：占位预检必须覆盖五类账本，缺任一类即留下自证通道。"""
        from full_analysis_gate import _precheck_placeholder_evidence
        cases = {
            "fact": {"fact_updates": [
                {"fact_id": "fact-x-1", "value": "PLACEHOLDER::x"}]},
            "source": {"source_records": [
                {"source_id": "src-x-1", "publisher": "PLACEHOLDER 占位"}]},
            "calculation": {"calculation_requests": [
                {"calculation_id": "calculation-x-PLACEHOLDER-1"}]},
            "judgment": {"judgments": [
                {"judgment_id": "judgment-x-PLACEHOLDER-1", "conclusion": "x"}]},
            "receipt": {"command_receipts": [
                {"receipt_id": "rcpt-x-PLACEHOLDER-1", "operation": "quote",
                 "status": "UNAVAILABLE", "reason": "PLACEHOLDER::未执行"}]},
        }
        for name, bundle in cases.items():
            with self.subTest(category=name):
                self.assertTrue(
                    _precheck_placeholder_evidence(bundle),
                    f"{name} 类占位证据未被拒收——自证通道仍然存在")


if __name__ == "__main__":
    unittest.main()
