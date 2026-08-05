"""mk_result_bundle.py 回归测试（lean 契约口径）。

lean 契约 `full-analysis-contract/lean-v1` 已移除 `sections` / `evidence_rules` /
`artifact.artifact_id`，随之而来的行为变化：

1. `build_evidence_ledger` **不再合成任何 PLACEHOLDER 结构地板**——未提供 `--extra-*`
   时七类账本一律为空（报告才是唯一交付物，空账本合法）；
2. 报告不再核对固定章节标题，改由 Gate 的 `_substance_errors` 校验「实质地板」
   （数据截止日 + 数据来源 + 仅供学习研究/免责）；
3. 契约不再声明命令操作白名单，`_precheck_command_receipts` 对全部 13 个 skill
   直接放行（whitelist 为空即 return []），故旧的「回执执行绑定」用例已无被测行为。

已知 impl 缺陷（**不得在本次修改中动实现**）：`_substance_errors` 仍从
`skill["sections"]` 统计实质章节（tools/full_analysis_gate.py:971），lean 契约无
sections → `min_substantive_sections` 永远不满足 → 任何 PASS bundle 都无法通过
`admit_bundle(check_artifacts=True)`。因此本文件的 PASS 路径只断言与该缺陷无关的
不变量（零占位 / 路径解析 / 实质锚点拦截），完整准入的绿灯用例走 NA 路径与
`validate_result_bundle(check_artifacts=False)` 轻量准入。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / "tools" / "full_analysis_contract.json"
SCHEMA = REPO / "tools" / "full_analysis_result_schema.json"

sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tools"))  # 供 true-oracle 测试直接调用 Gate 预检
import full_analysis_gate as gate_module  # noqa: E402
import mk_result_bundle as mkb  # noqa: E402

# 负向验收（NOT_APPLICABLE）报告必须含 Gate 约定的五章且 >= NA_MIN_BYTES，
# 否则即便谓词证伪成功也会被 Gate 报告硬门槛拒收。
NA_REPORT_BODY = "\n\n".join(
    f"## {h}\n\n{'此谓词经判定不成立，已附真实证据与替代路径并登记限制。' * 20}"
    for h in ("不适用结论", "判定事实", "证据来源", "替代路径", "限制"))

# lean 实质地板三锚：数据截止日（YYYY-MM-DD）+ 数据来源 + 仅供学习研究/免责。
LEAN_ANCHORS = ("数据截止日 2026-08-03。数据来源：巨潮资讯网公开披露文件与 Tushare 行情。"
                "本报告仅供学习研究，不构成投资建议。")


def lean_report(min_bytes: int = 0) -> str:
    """构造满足 lean 实质三锚且不低于 min_bytes 的报告正文。"""
    body = (f"# 分析报告\n\n{LEAN_ANCHORS}\n\n"
            f"## 核心结论\n\n{'扎实论证：把关键数据与推演逐条落到可核验口径上。' * 8}\n\n"
            f"## 关键数据\n\n{'关键数据表与口径说明，逐项标注取数路径与时间。' * 8}\n")
    filler = "补充论证：把上述数据与推演进一步展开，逐条落到可核验的口径上。\n"
    while len(body.encode("utf-8")) < min_bytes:
        body += filler
    return body


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


def artifact_id_of(skill: dict) -> str:
    """lean 契约不再声明 artifact_id，与 Gate 同口径回退为 artifact.<skill_id>。"""
    return skill["artifact"].get("artifact_id", f"artifact.{skill['skill_id']}")


class MkResultBundleTests(unittest.TestCase):
    def setUp(self):
        self.registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    def test_evidence_ledger_is_empty_for_every_contract_skill(self):
        """lean：无 --extra-* 输入时，全部 13 个 skill 的七类账本一律为空。

        契约已无 evidence_rules，生成器不得再按「最低条数」合成任何证据；
        报告是唯一交付物，空账本合法。"""
        self.assertEqual(len(self.registry["skills"]), 13)
        for skill in self.registry["skills"]:
            with self.subTest(skill=skill["skill_id"]):
                self.assertNotIn("evidence_rules", skill,
                                 "lean 契约不应再声明 evidence_rules")
                ledger = mkb.build_evidence_ledger(skill, [], [])
                facts, sources, calcs, judgments, role_runs, receipts, caps = ledger
                self.assertEqual(
                    [facts, sources, calcs, judgments, role_runs, receipts, caps],
                    [[], [], [], [], [], [], []],
                    f"{skill['skill_id']} 应产出空账本")

    def test_evidence_ledger_never_synthesizes_placeholder(self):
        """自证红线（lean 版）：空账本里不可能出现 PLACEHOLDER 水印，
        更不会出现「命令已成功执行」这类为未发生的事签发的成功证明。"""
        for skill in self.registry["skills"]:
            with self.subTest(skill=skill["skill_id"]):
                ledger = mkb.build_evidence_ledger(skill, [], [])
                blob = json.dumps(ledger, ensure_ascii=False)
                self.assertNotIn(mkb.PLACEHOLDER, blob)
                self.assertNotIn("PASS", blob)

    def test_generated_bundle_satisfies_result_schema(self):
        """空账本 bundle 仍必须通过 result-schema/v1 结构校验
        （lean 契约无 artifact_id，回退为 artifact.<skill_id>）。"""
        skill = next(s for s in self.registry["skills"] if s["skill_id"] == "ashare-data")
        facts, sources, calcs, judgments, role_runs, receipts, caps = (
            mkb.build_evidence_ledger(skill, [], []))
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
                "artifact_id": artifact_id_of(skill),
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
        _validate_schema_value(bundle, self.schema, "$")
        for key in ("fact_updates", "source_records", "calculation_requests",
                    "judgments", "role_runs", "command_receipts",
                    "capability_records", "limitations"):
            _validate_schema_value(
                bundle[key],
                self.schema["properties"][key],
                f"$.{key}")

    def test_check_report_no_longer_requires_fixed_headings(self):
        """lean：契约无 sections，check_report 不再产出「缺必需章节标题」，
        只保留 min_bytes 防坍塌下限。"""
        skill = next(s for s in self.registry["skills"] if s["skill_id"] == "ashare-data")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "report.md"
            p.write_text("# 标题\n\n正文内容\n", encoding="utf-8")
            warnings = mkb.check_report(skill, p)
            self.assertFalse(any("缺必需章节标题" in w for w in warnings), warnings)
            self.assertTrue(any("字节数" in w for w in warnings), warnings)
            # 字节达标后无任何阻断项
            p.write_text(lean_report(skill["artifact"]["min_bytes"] + 200),
                         encoding="utf-8")
            self.assertEqual(mkb.check_report(skill, p), [])

    def test_report_substance_floor_is_the_lean_gate(self):
        """lean 报告底线由 Gate `_substance_errors` 三锚判定：
        数据截止日（YYYY-MM-DD）/ 数据来源 / 仅供学习研究·免责。"""
        skill = next(s for s in self.registry["skills"] if s["skill_id"] == "ashare-data")
        bare = "# 标题\n\n" + ("这是一段普通的占位分析正文，没有时间标注，没有引用，也没有风险说明。" * 20)
        errors = gate_module._substance_errors(skill, bare)
        self.assertIn("缺数据截止日声明（需含 YYYY-MM-DD 形式日期）", errors)
        self.assertIn("缺数据来源声明", errors)
        self.assertIn("缺仅供学习研究/免责声明", errors)
        # 三锚齐备后，这三条实质错误全部消失
        ok_errors = gate_module._substance_errors(
            skill, lean_report(skill["artifact"]["min_bytes"]))
        for msg in ("缺数据截止日声明（需含 YYYY-MM-DD 形式日期）",
                    "缺数据来源声明", "缺仅供学习研究/免责声明"):
            self.assertNotIn(msg, ok_errors)

    def test_real_evidence_leaves_no_placeholder_floor(self):
        """提供真实事实/来源时，账本恰好是传入内容，无任何占位混排。"""
        skill = next(s for s in self.registry["skills"] if s["skill_id"] == "ashare-data")
        real_facts = [{
            "fact_id": "fact-ashare-data-price",
            "field": "price",
            "value": 12.34,
            "source_ids": ["src-real"],
            "confidence": "high",
        }]
        real_sources = [{
            "source_id": "src-real",
            "url": "https://www.cninfo.com.cn/x",
            "publisher": "巨潮资讯网",
            "title": "x 2025 年报",
        }]
        facts, sources, *_ = mkb.build_evidence_ledger(skill, real_facts, real_sources)
        self.assertEqual(facts, real_facts)
        self.assertEqual(sources, real_sources)
        self.assertNotIn(mkb.PLACEHOLDER,
                         json.dumps([facts, sources], ensure_ascii=False))

    def test_every_extra_category_passes_through_unchanged(self):
        """六类 --extra-* 输入必须原样进入账本（role_runs 恒为空：Gate 自行从
        磁盘 role-<role>.md 备忘录派生，不信任 bundle 自述）。"""
        skill = next(s for s in self.registry["skills"] if s["skill_id"] == "ashare-data")
        real_facts = [{"fact_id": "fact-1", "field": "price", "value": 1.0,
                       "source_ids": ["src-1"], "confidence": "high"}]
        real_sources = [{"source_id": "src-1", "url": "https://www.cninfo.com.cn/x",
                         "publisher": "巨潮资讯网", "title": "年报"}]
        real_calcs = [{"calculation_id": "calculation-real-1", "operation": "calc",
                       "args": {"expr": "1+1"}}]
        real_judgments = [{"judgment_id": "judgment-real-1", "rule_id": "r1",
                           "conclusion": "真实结论", "falsification": ["真实反证"],
                           "fact_ids": ["fact-1"]}]
        real_receipts = [{"receipt_id": "rcpt-1", "operation": "quote", "status": "PASS",
                          "argv": ["tushare", "quote"], "output": "已落盘"}]
        real_caps = [{"capability": "tushare_configured", "available": True}]
        facts, sources, calcs, judgments, role_runs, receipts, caps = (
            mkb.build_evidence_ledger(skill, real_facts, real_sources,
                                      real_calcs, real_judgments,
                                      real_receipts, real_caps))
        self.assertEqual(facts, real_facts)
        self.assertEqual(sources, real_sources)
        self.assertEqual(calcs, real_calcs)
        self.assertEqual(judgments, real_judgments)
        self.assertEqual(receipts, real_receipts)
        self.assertEqual(caps, real_caps)
        self.assertEqual(role_runs, [])

    def test_ledger_entries_are_copies_not_aliases(self):
        """账本条目必须是副本：调用方后续修改传入对象不得污染已生成的 bundle。"""
        skill = next(s for s in self.registry["skills"] if s["skill_id"] == "ashare-data")
        src = {"source_id": "src-1", "publisher": "巨潮资讯网", "title": "年报"}
        fact = {"fact_id": "fact-1", "field": "price", "value": 1.0,
                "source_ids": ["src-1"]}
        facts, sources, *_ = mkb.build_evidence_ledger(skill, [fact], [src])
        fact["value"] = 999
        src["publisher"] = "改过的出版方"
        self.assertEqual(facts[0]["value"], 1.0)
        self.assertEqual(sources[0]["publisher"], "巨潮资讯网")

    def test_placeholder_offenders_detects_every_category(self):
        """placeholder_offenders 必须与 Gate 预检同口径覆盖五类账本——lean 下生成器
        不再合成占位，但 Agent 手写占位仍须在提交前被抓出。"""
        bundle = {
            "fact_updates": [{"fact_id": "f1", "value": "PLACEHOLDER::x"}],
            "source_records": [{"source_id": "s1", "publisher": "PLACEHOLDER 占位",
                                "title": "t"}],
            "calculation_requests": [{"calculation_id": "calculation-x-PLACEHOLDER-1"}],
            "judgments": [{"judgment_id": "judgment-x-PLACEHOLDER-1",
                           "conclusion": "PLACEHOLDER::未判断"}],
            "command_receipts": [{"receipt_id": "rcpt-x-PLACEHOLDER-1",
                                  "reason": "PLACEHOLDER::未执行"}],
        }
        offenders = mkb.placeholder_offenders(bundle)
        self.assertEqual(len(offenders), 5, offenders)
        for prefix in ("fact ", "source ", "calculation ", "judgment ", "receipt "):
            self.assertTrue(any(o.startswith(prefix) for o in offenders),
                            f"未覆盖 {prefix.strip()} 类占位: {offenders}")
        # 零占位 bundle 必须返回空（否则退出码不变量会永远为非 0）
        self.assertEqual(mkb.placeholder_offenders({
            "fact_updates": [{"fact_id": "f1", "value": 12.34}],
            "source_records": [{"source_id": "s1", "publisher": "巨潮资讯网", "title": "t"}],
            "command_receipts": [{"receipt_id": "rcpt-1", "status": "PASS"}],
        }), [])


class MkResultBundleCliTests(unittest.TestCase):
    """端到端跑真实 CLI，守护 lean 下仍然成立的不变量：

    1. 空账本 **不再**被判为 PLACEHOLDER 地板（退出码 3 不应再出现）；
    2. macOS 上 /var → /private/var 符号链接不得让合法的绝对 report 路径被误判为
       "不在 run_root 内"；
    3. 输入非法 / 单边证据 / FAIL 缺 error 等确定性错误仍按既定退出码收场。
    """

    SKILL = "ashare-data"
    WU = "wu-ashare-data"
    ATTEMPT = "attempt-cli-test"
    NONCE = "nonce-cli-test"
    JOB = "job-cli-test"

    def _make_run_root(self, td: Path, skill_id: str | None = None,
                       body: str | None = None) -> Path:
        """构造最小 run_root：attempt 目录 + lean 报告 + 租约身份。"""
        skill_id = skill_id or self.SKILL
        run_root = td / "run"
        attempt_dir = run_root / "evidence" / "attempts" / skill_id / self.ATTEMPT
        attempt_dir.mkdir(parents=True)
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        skill = next(s for s in registry["skills"] if s["skill_id"] == skill_id)
        if body is None:
            body = lean_report(skill["artifact"].get("min_bytes", 0) + 200)
        (attempt_dir / "report.md").write_text(body, encoding="utf-8")
        (run_root / "evidence" / "runtime-state.json").write_text(
            json.dumps({
                "run_id": "run-cli-test",
                "work_units": [{
                    "work_unit_id": f"wu-{skill_id}",
                    "skill_id": skill_id,
                    "status": "LEASED",
                    "lease": {"attempt_id": self.ATTEMPT, "lease_nonce": self.NONCE,
                              "agent_job_id": self.JOB},
                }],
            }, ensure_ascii=False), encoding="utf-8")
        return run_root

    def _write_manifest(self, run_root: Path, run_id: str = "run-cli-test") -> None:
        """Gate 准入要求 manifest 与 bundle 的 run_id 一致，且版本为
        full-analysis-manifest/v2。"""
        (run_root / "evidence" / "00-analysis-manifest.json").write_text(
            json.dumps({
                "manifest_schema_version": "full-analysis-manifest/v2",
                "run": {"run_id": run_id},
                "sources": [],
            }, ensure_ascii=False), encoding="utf-8")

    def _run(self, run_root: Path, extra=None, skill_id=None):
        skill_id = skill_id or self.SKILL
        report = (run_root / "evidence" / "attempts" / skill_id
                  / self.ATTEMPT / "report.md")
        argv = [sys.executable, str(REPO / "scripts" / "mk_result_bundle.py"),
                "--run-root", str(run_root), "--skill-id", skill_id,
                "--work-unit-id", f"wu-{skill_id}", "--attempt-id", self.ATTEMPT,
                "--lease-nonce", self.NONCE, "--agent-job-id", self.JOB,
                "--report", str(report)]
        argv.extend(extra or [])
        return subprocess.run(argv, capture_output=True, text=True)

    @staticmethod
    def _write(td: Path, name: str, payload) -> str:
        p = td / name
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return str(p)

    @staticmethod
    def _real_facts_sources():
        facts = [{"fact_id": f"fact-ashare-data-{f}", "field": f, "value": 1.0 + i,
                  "source_ids": ["src-ashare-real"], "confidence": "high"}
                 for i, f in enumerate(("price", "market_cap", "revenue"))]
        sources = [{"source_id": "src-ashare-real",
                    "url": "https://www.cninfo.com.cn/new/disclosure",
                    "retrieved_at": "2026-08-03", "source_type": "filing",
                    "publisher": "巨潮资讯网", "title": "2025 年年度报告"}]
        return facts, sources

    def test_empty_ledger_is_not_a_placeholder_floor(self):
        """lean 核心行为：不传 --extra-* 时账本为空且零占位，
        绝不再走「PLACEHOLDER 地板」退出码 3。"""
        with tempfile.TemporaryDirectory() as td:
            run_root = self._make_run_root(Path(td))
            self._write_manifest(run_root)
            proc = self._run(run_root)
            self.assertNotEqual(proc.returncode, mkb.EXIT_PLACEHOLDER,
                                f"空账本被误判为占位地板\n{proc.stdout}\n{proc.stderr}")
            self.assertNotIn("PLACEHOLDER", proc.stderr)
            summary = json.loads(proc.stdout)
            self.assertEqual(summary["placeholder_entries"], 0)
            self.assertEqual(
                [summary["facts"], summary["sources"], summary["calculations"],
                 summary["judgments"], summary["role_runs"], summary["receipts"]],
                [0, 0, 0, 0, 0, 0])
            bundle = json.loads(Path(summary["result_path"]).read_text(encoding="utf-8"))
            for key in ("fact_updates", "source_records", "calculation_requests",
                        "judgments", "role_runs", "command_receipts",
                        "capability_records"):
                self.assertEqual(bundle[key], [], key)

    def test_illegal_json_extra_evidence_exits_invalid(self):
        """非法 JSON 文件必须以退出码 2 收场，不得抛 traceback 以退出码 1 结束。"""
        with tempfile.TemporaryDirectory() as td:
            run_root = self._make_run_root(Path(td))
            bad = Path(td) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            proc = self._run(run_root, ["--extra-evidence", str(bad),
                                        "--extra-sources", str(bad)])
            self.assertEqual(proc.returncode, mkb.EXIT_INVALID,
                             f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("不是合法 JSON", proc.stderr)

    def test_single_sided_real_evidence_fails_loudly(self):
        """只传 facts 或只传 sources 必须显式失败（exit 2）：
        fact.source_ids 必须指向真实 source_records，二者同真同假。"""
        facts, sources = self._real_facts_sources()
        for flag, payload in (("--extra-evidence", facts), ("--extra-sources", sources)):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as td:
                run_root = self._make_run_root(Path(td))
                path = self._write(Path(td), "extra.json", payload)
                proc = self._run(run_root, [flag, path])
                self.assertEqual(proc.returncode, mkb.EXIT_INVALID,
                                 f"stdout={proc.stdout}\nstderr={proc.stderr}")
                self.assertIn("单边真实证据", proc.stderr)

    def test_fail_status_requires_error_object(self):
        """--status FAIL 必须带 --error；有 error → 退出码 4（如实上报失败，
        非成功信号）；无 error → 退出码 2。"""
        facts, sources = self._real_facts_sources()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            run_root = self._make_run_root(td_path)
            self._write_manifest(run_root)
            proc = self._run(run_root, [
                "--status", "FAIL",
                "--extra-evidence", self._write(td_path, "f.json", facts),
                "--extra-sources", self._write(td_path, "s.json", sources)])
            self.assertEqual(proc.returncode, mkb.EXIT_INVALID, proc.stderr)
            self.assertIn("--error", proc.stderr)

            proc = self._run(run_root, [
                "--status", "FAIL", "--error", "tushare_down|接口连续超时",
                "--error-retryable", "false",
                "--extra-evidence", self._write(td_path, "f2.json", facts),
                "--extra-sources", self._write(td_path, "s2.json", sources)])
            self.assertEqual(proc.returncode, mkb.EXIT_NOT_SUCCESS, proc.stderr)
            summary = json.loads(proc.stdout)
            bundle = json.loads(Path(summary["result_path"]).read_text(encoding="utf-8"))
            self.assertIsNotNone(bundle["error"])
            self.assertEqual(bundle["error"]["code"], "tushare_down")
            self.assertFalse(bundle["error"]["retryable"])

    def test_report_missing_substance_anchors_is_blocked(self):
        """lean 报告底线：缺数据截止日 / 来源 / 免责声明的报告必须被准入拦截
        （exit 2），拦截文案与 Gate `_substance_errors` 一致。"""
        facts, sources = self._real_facts_sources()
        bare = "# 报告\n\n" + ("这是一段普通的占位分析正文，没有时间标注，没有引用，也没有风险说明。" * 200)
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            run_root = self._make_run_root(td_path, body=bare)
            self._write_manifest(run_root)
            proc = self._run(run_root, [
                "--extra-evidence", self._write(td_path, "f.json", facts),
                "--extra-sources", self._write(td_path, "s.json", sources)])
            self.assertEqual(proc.returncode, mkb.EXIT_INVALID,
                             f"stdout={proc.stdout}\nstderr={proc.stderr}")
            blockers = json.loads(proc.stdout)["admission_blockers"]
            for msg in ("缺数据截止日声明（需含 YYYY-MM-DD 形式日期）",
                        "缺数据来源声明", "缺仅供学习研究/免责声明"):
                self.assertIn(msg, blockers)

    def test_abs_report_path_under_symlinked_tmp_is_resolved(self):
        """macOS 路径回归：tempfile 给出的 /var/... 未解析路径（真实为 /private/var/...）
        不得在 relative_to 处被误判为「report 必须位于 run_root 内」。"""
        facts, sources = self._real_facts_sources()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            run_root = self._make_run_root(td_path)
            self._write_manifest(run_root)
            symlinked = td_path.resolve() != td_path
            proc = self._run(run_root, [
                "--extra-evidence", self._write(td_path, "f.json", facts),
                "--extra-sources", self._write(td_path, "s.json", sources)])
            self.assertNotIn("必须位于 run_root 内", proc.stderr,
                             f"符号链接路径被误判（symlinked={symlinked}）")
            summary = json.loads(proc.stdout)
            bundle = json.loads(Path(summary["result_path"]).read_text(encoding="utf-8"))
            self.assertTrue(bundle["artifact_records"][0]["path"].startswith(
                "evidence/attempts/"))
            self.assertEqual(summary["placeholder_entries"], 0)

    def test_lean_bundle_passes_light_admission_and_fault_injection_is_red(self):
        """oracle：lean 空账本 PASS bundle 必须通过 Gate 轻量准入
        （`validate_result_bundle`，不触碰 artifact 文件）；并对同一 bundle 做故障注入
        （artifact_id 错 / 手写占位证据 / FAIL 缺 error / 未注册 PWL），证明 oracle 会变红。
        """
        import copy

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            run_root = self._make_run_root(td_path)
            self._write_manifest(run_root)
            proc = self._run(run_root)
            summary = json.loads(proc.stdout)
            bundle = json.loads(Path(summary["result_path"]).read_text(encoding="utf-8"))
            registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
            # 绿：空账本 bundle 通过轻量准入（lean 不再强制账本形状）
            gate_module.validate_result_bundle(bundle, run_root, registry)
            # 红：故障注入必须被 Gate 拒收
            cases = {
                "artifact_id 不匹配": lambda b: b["artifact_records"][0].update(
                    {"artifact_id": "artifact.made-up"}),
                "手写占位事实": lambda b: b["fact_updates"].append(
                    {"fact_id": "f-x", "field": "price", "value": "PLACEHOLDER::未取数",
                     "source_ids": ["s-x"], "confidence": "low"}),
                "FAIL 缺 error": lambda b: b.update({"status": "FAIL"}),
                "未注册 PWL": lambda b: b.update({"pwl_candidates": ["made_up_reason"]}),
            }
            for name, mutate in cases.items():
                with self.subTest(case=name):
                    bad = copy.deepcopy(bundle)
                    mutate(bad)
                    with self.assertRaises(gate_module.GateError,
                                           msg=f"oracle 失效：{name} 未被 Gate 拒收"):
                        gate_module.validate_result_bundle(bad, run_root, registry)

    def test_not_applicable_real_support_exits_zero_and_gate_accepts(self):
        """负向验收：quality-screen 谓词 has_comparable_financial_history 被真实证伪
        → exit 0 且 Gate 接受；始终适用的 skill（news-pulse）标 NA → exit 2 拒收。"""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            run_root = self._make_run_root(td_path, skill_id="quality-screen",
                                           body=NA_REPORT_BODY)
            self._write_manifest(run_root)
            facts = [{"fact_id": "fact-quality-screen-na",
                      "field": "has_comparable_financial_history", "value": False,
                      "source_ids": ["src-na"], "confidence": "high"}]
            sources = [{"source_id": "src-na", "url": "https://www.cninfo.com.cn/x",
                        "retrieved_at": "2026-08-04", "source_type": "filing",
                        "publisher": "巨潮资讯网", "title": "上市不足三年公告"}]
            proc = self._run(run_root, [
                "--status", "NOT_APPLICABLE",
                "--extra-evidence", self._write(td_path, "f.json", facts),
                "--extra-sources", self._write(td_path, "s.json", sources),
                "--na-fact-id", "fact-quality-screen-na",
                "--limitation", "insufficient_history|上市不足三年，无可比财务历史"],
                skill_id="quality-screen")
            self.assertEqual(proc.returncode, mkb.EXIT_OK,
                             f"stdout={proc.stdout}\nstderr={proc.stderr}")
            summary = json.loads(proc.stdout)
            self.assertTrue(summary["submittable"])
            self.assertEqual(summary["not_applicable"]["predicate"],
                             "has_comparable_financial_history")
            registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
            bundle = json.loads(Path(summary["result_path"]).read_text(encoding="utf-8"))
            self.assertEqual(bundle["artifact_records"][0]["artifact_id"],
                             "artifact.na.quality-screen")
            gate_module.validate_result_bundle(bundle, run_root, registry)

        # 始终适用的 skill 不得标 NA
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            run_root = self._make_run_root(td_path, skill_id="news-pulse",
                                           body=NA_REPORT_BODY)
            self._write_manifest(run_root)
            facts = [{"fact_id": "fact-quality-screen-na",
                      "field": "has_comparable_financial_history", "value": False,
                      "source_ids": ["src-na"], "confidence": "high"}]
            sources = [{"source_id": "src-na", "url": "https://www.cninfo.com.cn/x",
                        "retrieved_at": "2026-08-04", "source_type": "filing",
                        "publisher": "巨潮资讯网", "title": "上市不足三年公告"}]
            proc = self._run(run_root, [
                "--status", "NOT_APPLICABLE",
                "--extra-evidence", self._write(td_path, "f2.json", facts),
                "--extra-sources", self._write(td_path, "s2.json", sources),
                "--na-fact-id", "fact-quality-screen-na",
                "--limitation", "x|y"], skill_id="news-pulse")
            self.assertEqual(proc.returncode, mkb.EXIT_INVALID, proc.stderr)
            self.assertIn("始终适用", proc.stderr)


if __name__ == "__main__":
    unittest.main()
