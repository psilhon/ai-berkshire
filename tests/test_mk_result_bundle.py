"""mk_result_bundle.py 转正回归测试：验证其生成的 Result Bundle 对全部 13 个
契约 skill 都满足 result-schema/v1 结构约束（E16：bundle 生成必须走确定性工具，
禁止子 Agent 手写易错 JSON——五粮液 run 的 schema 返工根因）。"""
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

    def test_evidence_ledger_covers_every_contract_skill(self):
        """对全部 13 个 skill，build_evidence_ledger 都必须产出满足
        evidence_rules 最低条数的证据（无任何 skill 抛错或产出不足）。"""
        for skill in self.registry["skills"]:
            with self.subTest(skill=skill["skill_id"]):
                rules = {r.get("kind"): r for r in skill.get("evidence_rules", [])}
                facts, sources, calcs, judgments, role_runs, receipts, caps = (
                    mkb.build_evidence_ledger(skill, [], []))

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


    def test_real_evidence_leaves_no_placeholder_floor(self):
        """v3.4.12 修复验证：提供真实事实/来源时，生成器不得残留任何 PLACEHOLDER 地板。

        修复前占位事实始终生成并与真实证据按 id 并列，导致「提供 3 条真实事实仍残留
        3 条占位事实 + 1 条占位来源」，Gate 占位预检直接拒收整包。本测试直接守护该回归。
        """
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
        for fact in facts:
            self.assertNotIn("PLACEHOLDER", str(fact.get("value")))
        for src in sources:
            self.assertNotIn("PLACEHOLDER", str(src.get("publisher")))
            self.assertNotIn("PLACEHOLDER", str(src.get("title")))
        # 真实证据必须与占位地板互斥：提供真实证据后事实/来源应恰好是传入的真实内容
        self.assertEqual([f["fact_id"] for f in facts], ["fact-ashare-data-price"])
        self.assertEqual([s["source_id"] for s in sources], ["src-real"])

    def test_floor_still_emitted_when_no_real_evidence(self):
        """未提供真实证据时，地板仍全部带水印（供本地调结构与 Gate 拒收用）。"""
        skill = next(s for s in self.registry["skills"] if s["skill_id"] == "ashare-data")
        facts, sources, *_ = mkb.build_evidence_ledger(skill, [], [])
        for fact in facts:
            self.assertIn("PLACEHOLDER", str(fact.get("value")))
        for src in sources:
            self.assertIn("PLACEHOLDER", str(src.get("publisher")))

    def test_floor_never_fabricates_success_proof(self):
        """v3.4.13 P0 自证红线：生成器**不得为未发生的事签发成功证明**。

        修复前 ashare-data 的地板会一口气产出 51 条 status=PASS 的"命令已成功执行"
        回执（实际一条命令都没跑），Gate 却接受为 DONE。本测试对全部 13 个 skill
        断言：地板回执一律 UNAVAILABLE + 水印 reason，能力一律 available=false，
        计算/判断地板全部自报占位。
        """
        for skill in self.registry["skills"]:
            with self.subTest(skill=skill["skill_id"]):
                _, _, calcs, judgments, _, receipts, caps = (
                    mkb.build_evidence_ledger(skill, [], []))
                for r in receipts:
                    self.assertNotEqual(
                        r.get("status"), "PASS",
                        f"{skill['skill_id']} 地板回执伪造 PASS: {r.get('receipt_id')}")
                    self.assertEqual(r.get("status"), "UNAVAILABLE")
                    self.assertIn("PLACEHOLDER", str(r.get("reason")),
                                  f"{skill['skill_id']} 地板回执缺水印")
                for c in calcs:
                    self.assertIn("PLACEHOLDER", str(c.get("calculation_id")))
                for j in judgments:
                    self.assertIn("PLACEHOLDER", str(j.get("judgment_id")))
                    self.assertIn("PLACEHOLDER", str(j.get("conclusion")))
                for cap in caps:
                    self.assertIs(cap.get("available"), False,
                                  f"{skill['skill_id']} 地板 capability 自称可用")

    def test_real_evidence_per_category_replaces_floor(self):
        """每一类 --extra-* 真实输入都必须**独立**顶掉本类地板（不再只有 facts/sources
        两类可替换）。否则真实调研成果仍会与占位混排、被 Gate 整包拒收。"""
        skill = next(s for s in self.registry["skills"] if s["skill_id"] == "ashare-data")
        real_receipts = [{
            "receipt_id": "rcpt-ashare-data-quote",
            "operation": "quote",
            "status": "PASS",
            "argv": ["tushare", "quote", "--ts_code", "000001.SZ"],
            "output": "quote 实际输出：000001.SZ 日线数据已落盘",
            "detail": "tencent quote 000001 已执行",
        }]
        real_caps = [{"capability": "tushare_configured", "available": True}]
        _, _, _, _, _, receipts, caps = mkb.build_evidence_ledger(
            skill, [], [], extra_receipts=real_receipts, extra_capabilities=real_caps)
        self.assertEqual([r["receipt_id"] for r in receipts],
                         ["rcpt-ashare-data-quote"])
        self.assertEqual(caps, real_caps)
        for r in receipts:
            self.assertNotIn("PLACEHOLDER", json.dumps(r, ensure_ascii=False))

        # 计算/判断同理（用带这两类规则的 skill 验证，ashare-data 无此规则）
        calc_skill = next(s for s in self.registry["skills"]
                          if any(r.get("kind") == "min_calculations" and r.get("n", 0) > 0
                                 for r in s.get("evidence_rules", [])))
        real_calcs = [{"calculation_id": "calculation-real-1", "operation": "calc",
                       "args": {"expr": "1+1"}}]
        real_judgments = [{"judgment_id": "judgment-real-1", "rule_id": "r1",
                           "conclusion": "真实结论", "falsification": ["真实反证"],
                           "fact_ids": ["fact-x"]}]
        _, _, calcs, judgments, *_ = mkb.build_evidence_ledger(
            calc_skill, [], [], extra_calcs=real_calcs, extra_judgments=real_judgments)
        self.assertEqual(calcs, real_calcs)
        self.assertEqual(judgments, real_judgments)

    def test_placeholder_offenders_detects_every_category(self):
        """placeholder_offenders 必须与 Gate 预检同口径覆盖五类账本，
        任何一类漏检都会让"含占位的 bundle"以退出码 0 蒙混过关。"""
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
    """端到端跑真实 CLI，守护 v3.4.13 的两条不变量：

    1. 退出码 0 ⟺ bundle 零占位、可提交（全地板=3，单边真实证据=2）；
    2. macOS 上 /var → /private/var 符号链接不得让合法的绝对 report 路径被误判为
       "不在 run_root 内"（run_root 已 resolve，report 必须同样 resolve）。
    """

    SKILL = "ashare-data"
    WU = "wu-ashare-data"
    ATTEMPT = "attempt-cli-test"
    NONCE = "nonce-cli-test"
    JOB = "job-cli-test"

    def _make_run_root(self, td: Path) -> Path:
        run_root = td / "run"
        attempt_dir = run_root / "evidence" / "attempts" / self.SKILL / self.ATTEMPT
        attempt_dir.mkdir(parents=True)
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        skill = next(s for s in registry["skills"] if s["skill_id"] == self.SKILL)
        headings = [s["heading"] for s in skill["sections"] if s.get("required")]
        body = "\n\n".join(f"## {h}\n\n{'正文内容占位。' * 30}" for h in headings)
        (attempt_dir / "report.md").write_text(body, encoding="utf-8")
        (run_root / "evidence" / "runtime-state.json").write_text(
            json.dumps({
                "run_id": "run-cli-test",
                "work_units": [{
                    "work_unit_id": self.WU,
                    "skill_id": self.SKILL,
                    "status": "LEASED",
                    "lease": {"attempt_id": self.ATTEMPT, "lease_nonce": self.NONCE,
                              "agent_job_id": self.JOB},
                }],
            }, ensure_ascii=False), encoding="utf-8")
        return run_root

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

    # v3.4.14 覆盖 7 个 required + 3 个 conditional；沿用该组合。
    REAL_OPS = ("quote", "financials", "valuation", "history",
                "equity-history", "announcements", "signals",
                "pe-band", "ratios", "mainbz")

    def _real_receipts(self, run_root: Path, td_path: Path):
        """用**真实执行器**签发回执（v3.4.15）。

        v3.4.14 这里返回的是手写字典（argv/output 两个自述字符串）——而那正是
        Gate 当时全部的检查内容，于是测试与被测代码共享同一个错误假设：只要
        字段非空就算"真实执行"。这类 fixture 本身就是 false oracle。
        现在 fixture 必须真的跑一遍执行器：它会 subprocess 执行命令、落盘输出、
        写 journal、签名。任何一条绑定退化，这些测试都会随之变红。
        """
        cmd_stub = td_path / "op_cmd.py"
        if not cmd_stub.exists():
            cmd_stub.write_text(
                "import sys\nprint(f'{sys.argv[1]} 000651.SZ rows=42')\n",
                encoding="utf-8")
        receipts = []
        for op in self.REAL_OPS:
            proc = subprocess.run(
                [sys.executable, str(REPO / "scripts" / "run_evidence_command.py"),
                 "--run-root", str(run_root),
                 "--receipt-id", f"rcpt-ashare-data-{op}",
                 "--operation", op, "--",
                 sys.executable, str(cmd_stub), op],
                capture_output=True, text=True)
            assert proc.returncode == 0, f"{op}: {proc.stdout}\n{proc.stderr}"
            receipts.append(json.loads(proc.stdout))
        return receipts

    def test_placeholder_floor_bundle_exits_nonzero(self):
        """不传任何真实证据时，生成器必须以非 0 退出并自报占位条数——
        修复前它返回 0，等于给"未做调研"发了成功信号。"""
        with tempfile.TemporaryDirectory() as td:
            run_root = self._make_run_root(Path(td))
            proc = self._run(run_root)
            self.assertEqual(proc.returncode, mkb.EXIT_PLACEHOLDER,
                             f"stdout={proc.stdout}\nstderr={proc.stderr}")
            summary = json.loads(proc.stdout)
            self.assertFalse(summary["submittable"])
            self.assertGreater(summary["placeholder_entries"], 0)
            self.assertIn("PLACEHOLDER", proc.stderr)

    def _write_manifest(self, run_root: Path, run_id: str = "run-cli-test") -> None:
        """Gate.validate_result_bundle 要求 manifest 与 bundle 的 run_id 一致，
        且版本为 full-analysis-manifest/v2——否则 Gate 直接报'只接受 v2 manifest'。"""
        (run_root / "evidence" / "00-analysis-manifest.json").write_text(
            json.dumps({
                "manifest_schema_version": "full-analysis-manifest/v2",
                "run": {"run_id": run_id},
                "sources": [],
            }, ensure_ascii=False), encoding="utf-8")

    def _make_run_root_for(self, td: Path, skill_id: str) -> Path:
        """与 _make_run_root 同构，但支持任意 skill（NA/always-applicable 测试用）。"""
        run_root = td / "run"
        attempt_dir = run_root / "evidence" / "attempts" / skill_id / self.ATTEMPT
        attempt_dir.mkdir(parents=True)
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        skill = next(s for s in registry["skills"] if s["skill_id"] == skill_id)
        headings = [s["heading"] for s in skill["sections"] if s.get("required")]
        body = "\n\n".join(f"## {h}\n\n{'正文内容占位。' * 30}" for h in headings)
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

    def test_illegal_json_extra_evidence_exits_invalid(self):
        """v3.4.14 P1：非法 JSON 文件必须以退出码 2 收场（与 CHANGELOG/Skill 声明一致），
        不得抛 traceback 以退出码 1 结束。"""
        with tempfile.TemporaryDirectory() as td:
            run_root = self._make_run_root(Path(td))
            bad = Path(td) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            proc = self._run(run_root, ["--extra-evidence", str(bad),
                                        "--extra-sources", str(bad)])
            self.assertEqual(proc.returncode, mkb.EXIT_INVALID,
                             f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("不是合法 JSON", proc.stderr)

    def test_pass_receipt_missing_binding_rejected(self):
        """v3.4.14 回执执行绑定：PASS 回执必须携带 argv+output，否则生成器拒收（exit 2）。
        此前 Agent 可自报 PASS 并写 detail: TEST_FIXTURE::未连接真实命令日志 蒙混过关。"""
        facts, sources = self._real_facts_sources()
        no_argv = [{"receipt_id": "rcpt-ashare-data-quote", "operation": "quote",
                    "status": "PASS", "output": "quote 实际输出已落盘",
                    "detail": "quote 已执行"}]
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            run_root = self._make_run_root(td_path)
            proc = self._run(run_root, [
                "--extra-evidence", self._write(td_path, "f.json", facts),
                "--extra-sources", self._write(td_path, "s.json", sources),
                "--extra-receipts", self._write(td_path, "r.json", no_argv),
                "--extra-capabilities",
                self._write(td_path, "c.json",
                            [{"capability": "tushare_configured", "available": True}])])
            self.assertEqual(proc.returncode, mkb.EXIT_INVALID, proc.stderr)
            self.assertIn("回执无执行绑定", proc.stderr)
            self.assertIn("argv", proc.stderr)

    def test_pass_receipt_forgery_token_rejected(self):
        """含伪造标记（PLACEHOLDER/TEST_FIXTURE/未连接真实命令日志/mock）的 PASS 回执
        必须被生成器拒收（exit 2），与 Gate 双向独立校验。"""
        facts, sources = self._real_facts_sources()
        forged = [{"receipt_id": "rcpt-ashare-data-quote", "operation": "quote",
                   "status": "PASS", "argv": ["tushare", "quote", "--ts_code", "000651.SZ"],
                   "output": "quote 输出", "detail": "TEST_FIXTURE::未连接真实命令日志"}]
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            run_root = self._make_run_root(td_path)
            proc = self._run(run_root, [
                "--extra-evidence", self._write(td_path, "f.json", facts),
                "--extra-sources", self._write(td_path, "s.json", sources),
                "--extra-receipts", self._write(td_path, "r.json", forged),
                "--extra-capabilities",
                self._write(td_path, "c.json",
                            [{"capability": "tushare_configured", "available": True}])])
            self.assertEqual(proc.returncode, mkb.EXIT_INVALID, proc.stderr)
            self.assertIn("伪造标记", proc.stderr)

    def test_fail_status_requires_error_object(self):
        """--status FAIL 必须带 --error（Gate 强制 FAIL bundle 携带 error 对象，否则整包拒收）；
        有 error → 退出码 4（如实上报失败，非成功信号）；无 error → 退出码 2。"""
        facts, sources = self._real_facts_sources()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            run_root = self._make_run_root(td_path)
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

    def test_missing_required_heading_exits_invalid(self):
        """报告缺必需章节时退出码 2（BLOCK:: 硬拒收项），不再静默返回 0
        （此前缺章节只打印警告却仍 exit 0，等于给 Gate 必拒的 bundle 发成功信号）。"""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            run_root = self._make_run_root(td_path)
            # 仅保留首个必需章节，故意缺其余
            skill = next(s for s in json.loads(REGISTRY.read_text(encoding="utf-8"))["skills"]
                         if s["skill_id"] == self.SKILL)
            first = next(h["heading"] for h in skill["sections"] if h.get("required"))
            report = (run_root / "evidence" / "attempts" / self.SKILL
                      / self.ATTEMPT / "report.md")
            report.write_text(f"## {first}\n\n正文内容占位。" * 10, encoding="utf-8")
            facts, sources = self._real_facts_sources()
            proc = self._run(run_root, [
                "--extra-evidence", self._write(td_path, "f.json", facts),
                "--extra-sources", self._write(td_path, "s.json", sources),
                "--extra-receipts", self._write(td_path, "r.json", self._real_receipts(run_root, td_path)),
                "--extra-capabilities",
                self._write(td_path, "c.json",
                            [{"capability": "tushare_configured", "available": True}])])
            self.assertEqual(proc.returncode, mkb.EXIT_INVALID,
                             f"stdout={proc.stdout}\nstderr={proc.stderr}")
            blockers = json.loads(proc.stdout)["report_blockers"]
            self.assertTrue(any("缺必需章节" in b for b in blockers))

    def test_not_applicable_real_support_exits_zero_and_gate_accepts(self):
        """v3.4.14：CLI 现在能生成 Gate 可接受的 NA bundle（此前无法生成，逼得 NA 路径
        手写 result.json 与 E16 冲突）。quality-screen 谓词 has_comparable_financial_history
        证伪 → exit 0 且 Gate 接受；始终适用的 skill（news-pulse）标 NA → exit 2 拒收。"""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            run_root = self._make_run_root_for(td_path, "quality-screen")
            (run_root / "evidence" / "attempts" / "quality-screen"
             / self.ATTEMPT / "report.md").write_text(NA_REPORT_BODY, encoding="utf-8")
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
            self.assertEqual(proc.returncode, mkb.EXIT_OK, proc.stderr)
            summary = json.loads(proc.stdout)
            self.assertTrue(summary["submittable"])
            self.assertEqual(summary["not_applicable"]["predicate"],
                             "has_comparable_financial_history")
            registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
            bundle = json.loads(Path(summary["result_path"]).read_text(encoding="utf-8"))
            # Gate 必须接受该 NA bundle（双向校验）
            gate_module.validate_result_bundle(bundle, run_root, registry)

        # 始终适用的 skill 不得标 NA
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            run_root = self._make_run_root_for(td_path, "news-pulse")
            (run_root / "evidence" / "attempts" / "news-pulse"
             / self.ATTEMPT / "report.md").write_text(NA_REPORT_BODY, encoding="utf-8")
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

    def test_pass_bundle_true_oracle_accepted_and_fault_injection_red(self):
        """true-oracle（v3.4.14 P1）：生成器产出的 PASS bundle 必须被 Gate
        validate_result_bundle 端到端接受（此前'full real evidence'测试只跑生成器不跑
        Gate，是 false oracle）；并对该 bundle 做故障注入（伪造标记/删 argv/清 output/
        白名单外 op），证明 oracle 会变红——否则同 false oracle。"""
        import copy

        facts, sources = self._real_facts_sources()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            run_root = self._make_run_root(td_path)
            self._write_manifest(run_root)
            proc = self._run(run_root, [
                "--extra-evidence", self._write(td_path, "f.json", facts),
                "--extra-sources", self._write(td_path, "s.json", sources),
                "--extra-receipts", self._write(td_path, "r.json", self._real_receipts(run_root, td_path)),
                "--extra-capabilities",
                self._write(td_path, "c.json",
                            [{"capability": "tushare_configured", "available": True}])])
            self.assertEqual(proc.returncode, mkb.EXIT_OK, proc.stderr)
            registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
            bundle = json.loads(Path(
                json.loads(proc.stdout)["result_path"]).read_text(encoding="utf-8"))
            # 绿：真实 bundle 被 Gate 接受
            gate_module.validate_result_bundle(bundle, run_root, registry)
            # 红：故障注入必须让 Gate 拒收
            cases = {
                "伪造标记": lambda b: b["command_receipts"][0].update(
                    {"detail": "TEST_FIXTURE::未连接真实命令日志"}),
                "删除 argv": lambda b: b["command_receipts"][0].pop("argv", None),
                "清空 output": lambda b: b["command_receipts"][0].update({"output": "   "}),
                "白名单外 op": lambda b: b["command_receipts"][0].update(
                    {"operation": "made-up"}),
            }
            for name, mutate in cases.items():
                with self.subTest(case=name):
                    bad = copy.deepcopy(bundle)
                    mutate(bad)
                    with self.assertRaises(gate_module.GateError,
                                           msg=f"oracle 失效：{name} 未被 Gate 拒收"):
                        gate_module.validate_result_bundle(bad, run_root, registry)

    def test_single_sided_real_evidence_fails_loudly(self):
        """只传 facts 或只传 sources 必须显式失败（exit 2），
        不得静默把另一类退化成占位地板后返回成功。"""
        facts, sources = self._real_facts_sources()
        for flag, payload in (("--extra-evidence", facts), ("--extra-sources", sources)):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as td:
                run_root = self._make_run_root(Path(td))
                path = self._write(Path(td), "extra.json", payload)
                proc = self._run(run_root, [flag, path])
                self.assertEqual(proc.returncode, mkb.EXIT_INVALID,
                                 f"stdout={proc.stdout}\nstderr={proc.stderr}")
                self.assertIn("单边真实证据", proc.stderr)

    def test_partial_real_evidence_still_flags_receipt_floor(self):
        """守护"退出码 0 ⟺ 零占位"：只补 facts+sources 时回执仍是地板，
        必须继续非 0，不能因为"两边都传了"就放行。"""
        facts, sources = self._real_facts_sources()
        with tempfile.TemporaryDirectory() as td:
            run_root = self._make_run_root(Path(td))
            proc = self._run(run_root, [
                "--extra-evidence", self._write(Path(td), "f.json", facts),
                "--extra-sources", self._write(Path(td), "s.json", sources)])
            self.assertEqual(proc.returncode, mkb.EXIT_PLACEHOLDER,
                             f"stdout={proc.stdout}\nstderr={proc.stderr}")
            summary = json.loads(proc.stdout)
            self.assertFalse(summary["submittable"])

    def test_full_real_evidence_exits_zero_and_accepts_symlinked_abs_path(self):
        """五类证据齐全时退出码 0 且 bundle 零占位；同时守护 macOS 路径回归——
        tempfile 在 macOS 下天然给出 /var/... 未解析路径（真实为 /private/var/...），
        修复前会在 relative_to 处误报 "report 必须位于 run_root 内"。

        v3.4.14 P1 修正：本测试升级为**真 oracle**——生成器产出的 bundle 还会直接交给
        Gate.validate_result_bundle 端到端接受（不再只跑生成器自证）。此前"full real
        evidence"测试只跑生成器、不跑 Gate，是 false oracle。"""
        facts, sources = self._real_facts_sources()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            run_root = self._make_run_root(td_path)
            self._write_manifest(run_root)
            # 记录是否真的踩到符号链接场景（macOS 为真，Linux 下两者相等仍可跑）
            symlinked = td_path.resolve() != td_path
            proc = self._run(run_root, [
                "--extra-evidence", self._write(td_path, "f.json", facts),
                "--extra-sources", self._write(td_path, "s.json", sources),
                "--extra-receipts", self._write(td_path, "r.json", self._real_receipts(run_root, td_path)),
                "--extra-capabilities", self._write(
                    td_path, "c.json",
                    [{"capability": "tushare_configured", "available": True}])])
            self.assertNotIn("必须位于 run_root 内", proc.stderr,
                             f"符号链接路径被误判（symlinked={symlinked}）")
            self.assertEqual(proc.returncode, mkb.EXIT_OK,
                             f"stdout={proc.stdout}\nstderr={proc.stderr}")
            summary = json.loads(proc.stdout)
            self.assertTrue(summary["submittable"])
            self.assertEqual(summary["placeholder_entries"], 0)
            bundle = json.loads(Path(summary["result_path"]).read_text(encoding="utf-8"))
            self.assertEqual(mkb.placeholder_offenders(bundle), [])
            self.assertTrue(bundle["artifact_records"][0]["path"].startswith(
                "evidence/attempts/"))
            # 真 oracle：交给 Gate 端到端接受（生成器放行 ≠ Gate 放行）
            registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
            gate_module.validate_result_bundle(bundle, run_root, registry)


if __name__ == "__main__":
    unittest.main()
