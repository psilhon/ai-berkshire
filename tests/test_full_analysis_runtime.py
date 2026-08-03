import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tests.test_full_analysis_e2e import build_compliant_evidence, build_compliant_report


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "full_analysis.py"
GATE = REPO / "tools" / "full_analysis_gate.py"
REGISTRY = REPO / "tools" / "full_analysis_contract.json"
sys.path.insert(0, str(REPO / "tools"))
import full_analysis_runtime as rt  # noqa: E402


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.run_root = self.root / "local/company/000651.SZ-格力电器/20260723-120000-ab12"

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(CLI), *map(str, args)], cwd=self.root,
            capture_output=True, text=True,
        )

    def start(self):
        result = self.cli(
            "start", "--registry", REGISTRY, "--repo-root", self.root,
            "--company", "格力电器", "--code", "000651.SZ", "--as-of", "2026-07-23",
            "--run-root", self.run_root,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def state(self):
        return json.loads((self.run_root / "evidence/runtime-state.json").read_text())

    def lease_and_start(self):
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        started = self.cli(
            "job-started", "--run-root", self.run_root,
            "--work-unit-id", leased["work_unit_id"],
            "--attempt-id", leased["attempt_id"],
            "--lease-nonce", leased["lease_nonce"],
            "--agent-job-id", f"job-{leased['attempt_id']}",
        )
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        return leased

    def write_result(self, leased, *, attempt_id=None, lease_nonce=None, agent_job_id=None):
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        skill = next(s for s in registry["skills"] if s["skill_id"] == leased["skill_id"])
        attempt_id = attempt_id or leased["attempt_id"]
        attempt_dir = self.run_root / "evidence/attempts" / leased["skill_id"] / attempt_id
        attempt_dir.mkdir(parents=True, exist_ok=True)
        artifact = attempt_dir / "report.md"
        artifact.write_text(build_compliant_report(REGISTRY, leased["skill_id"]), encoding="utf-8")
        (facts, sources, calculations, judgments, role_runs,
         command_receipts, capability_records) = build_compliant_evidence(
            REGISTRY, leased["skill_id"])
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        bundle = {
            "schema_version": "result-schema/v1",
            "run_id": manifest["run"]["run_id"],
            "work_unit_id": leased["work_unit_id"],
            "attempt_id": attempt_id,
            "agent_job_id": agent_job_id or f"job-{leased['attempt_id']}",
            "lease_nonce": lease_nonce or leased["lease_nonce"],
            "skill_id": leased["skill_id"],
            "role_id": None,
            "status": "PASS",
            "artifact_records": [{
                "artifact_id": skill["artifact"]["artifact_id"],
                "path": str(artifact.relative_to(self.run_root)),
                "bytes": artifact.stat().st_size,
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                "formal": False,
                "accepted": False,
            }],
            "fact_updates": facts,
            "source_records": sources,
            "calculation_requests": calculations,
            "judgments": judgments,
            "role_runs": role_runs,
            "command_receipts": command_receipts,
            "capability_records": capability_records,
            "limitations": [],
            "pwl_candidates": [],
            "started_at": "2026-07-25T12:00:00+08:00",
            "completed_at": "2026-07-25T12:01:00+08:00",
            "error": None,
        }
        path = attempt_dir / "result.json"
        path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        return path

    def test_start_initializes_budget_and_counts_preflight_once(self):
        self.start()
        state = self.state()
        self.assertEqual(state["budget"]["hard_max"], 33)
        self.assertEqual(state["budget"]["stop_dispatch_at"], 30)
        # v3.4.9：normal_target 机器派生 = 2 × 契约单元数（13 → 26），非魔数
        self.assertEqual(state["budget"]["normal_target"], 2 * len(state["work_units"]))
        self.assertEqual(state["budget"]["used"], 1)
        self.assertEqual(state["budget"]["preflight_count"], 1)
        self.assertEqual(len(state["work_units"]), 13)
        authorization = state["authorization"]
        self.assertEqual(
            authorization["profile"],
            "full-analysis-internal/v1",
        )
        self.assertIn("read_only_external_research",
                      authorization["granted"])
        self.assertIn("external_publish", authorization["denied"])

    def test_next_work_injects_bounded_unattended_authorization(self):
        self.start()

        leased = self.cli("next-work", "--run-root", self.run_root)

        self.assertEqual(leased.returncode, 0, leased.stdout + leased.stderr)
        payload = json.loads(leased.stdout)
        self.assertEqual(
            payload["authorization"]["profile"],
            "full-analysis-internal/v1",
        )
        methodology = payload["methodology_text"]
        self.assertIn("本次 run 的启动请求已满足", methodology)
        self.assertIn("只读外部研究", methodology)
        self.assertIn("run_root", methodology)
        self.assertIn("不得据此执行 push、PR、publish、send", methodology)

    def test_next_work_injects_complete_result_bundle_template(self):
        self.start()
        payload = json.loads(
            self.cli("next-work", "--run-root", self.run_root).stdout)
        methodology = payload["methodology_text"]
        self.assertIn('"capability_records": []', methodology)
        self.assertIn('"not_applicable": null', methodology)
        self.assertIn('"accepted": false', methodology)
        self.assertNotIn(
            '"formal": false,\n      "accepted": true', methodology)
        self.assertIn("是否非空以 evidence_rules 为准", methodology)

    def test_next_work_and_job_started_enforce_four_concurrent_leases(self):
        # v3.3.10：依赖门禁下，完成 ashare 后 W2 四个单元就绪，
        # 并发上限 4 允许四个租约，第五个触发 CONCURRENCY_LIMIT。
        self.start()
        self._set_unit_done("ashare-data")
        leases = [self.cli("next-work", "--run-root", self.run_root) for _ in range(4)]
        fifth = self.cli("next-work", "--run-root", self.run_root)
        for lease in leases:
            self.assertEqual(lease.returncode, 0)
            self.assertEqual(json.loads(lease.stdout)["status"], "LEASED")
        self.assertEqual(json.loads(fifth.stdout)["status"], "NO_WORK")
        self.assertEqual(json.loads(fifth.stdout)["reason"], "CONCURRENCY_LIMIT")
        a = json.loads(leases[0].stdout)
        started = self.cli("job-started", "--run-root", self.run_root,
                           "--work-unit-id", a["work_unit_id"], "--attempt-id", a["attempt_id"],
                           "--lease-nonce", a["lease_nonce"], "--agent-job-id", "job-1")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertEqual(self.state()["budget"]["used"], 2)

    def _set_unit_done(self, skill_id):
        """直接把某单元置为 DONE（模拟上游完成），隔离测试依赖门禁逻辑。"""
        path = self.run_root / "evidence/runtime-state.json"
        state = self.state()
        for unit in state["work_units"]:
            if unit["skill_id"] == skill_id:
                unit["status"] = "DONE"
                unit["lease"] = None
        path.write_text(json.dumps(state), encoding="utf-8")

    def test_dependency_gate_blocks_units_until_deps_done(self):
        # v3.3.10 T5：依赖未完成的单元不得派发（波次调度的正确性根基）
        self.start()
        first = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(first["skill_id"], "ashare-data")  # W1 根节点先发
        # ashare 未完成时，其余单元依赖未就绪，应无可派发
        second = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(second["status"], "NO_WORK")
        self.assertEqual(second["reason"], "DEPENDENCIES_PENDING")
        # 完成 ashare 后，W2 单元解锁
        self._set_unit_done("ashare-data")
        third = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(third["status"], "LEASED")
        self.assertIn(third["skill_id"],
                      {"financial-data", "quality-screen",
                       "investment-checklist", "investment-research"})

    def test_dependency_gate_fills_wave_in_parallel_up_to_concurrency(self):
        # 完成 ashare 后，编排器循环 next-work 可在并发上限内填满整个 W2（4 个），
        # 第 5 个因 CONCURRENCY_LIMIT 停（不是 DEPENDENCIES_PENDING）
        self.start()
        self._set_unit_done("ashare-data")
        leased, last = [], None
        for _ in range(6):
            result = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
            last = result
            if result["status"] == "LEASED":
                leased.append(result["skill_id"])
            else:
                break
        self.assertEqual(len(leased), 4)
        self.assertEqual(set(leased),
                         {"financial-data", "quality-screen",
                          "investment-checklist", "investment-research"})
        self.assertEqual(last["status"], "NO_WORK")
        self.assertEqual(last["reason"], "CONCURRENCY_LIMIT")

    def test_dependency_gate_holds_downstream_until_full_wave_done(self):
        # W3（investment-team 依赖 ashare+financial+quality）须等依赖全 DONE 才解锁；
        # 仅完成部分依赖时不得越级派发。
        self.start()
        self._set_unit_done("ashare-data")
        self._set_unit_done("financial-data")
        # 只完成 ashare+financial，quality 未 DONE → 派发的仍是 W2 剩余单元，绝不到 W3
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(leased["status"], "LEASED")
        self.assertIn(leased["skill_id"],
                      {"quality-screen", "investment-checklist", "investment-research"})
        self.assertNotIn(leased["skill_id"],
                         {"investment-team", "management-deep-dive",
                          "earnings-review", "industry-research"})
        # 补齐 W2 全部 DONE 后，W3 才解锁
        for skill in ("quality-screen", "investment-checklist", "investment-research"):
            self._set_unit_done(skill)
        result = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(result["status"], "LEASED")
        self.assertIn(result["skill_id"],
                      {"investment-team", "management-deep-dive",
                       "earnings-review", "industry-research"})

    def test_rate_limit_failure_enters_global_cooldown_and_retry_backoff(self):
        self.start()
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.cli("job-started", "--run-root", self.run_root,
                 "--work-unit-id", leased["work_unit_id"], "--attempt-id", leased["attempt_id"],
                 "--lease-nonce", leased["lease_nonce"], "--agent-job-id", "job-1")
        failed = self.cli("record-failure", "--run-root", self.run_root,
                          "--work-unit-id", leased["work_unit_id"], "--attempt-id", leased["attempt_id"],
                          "--reason", "rate_limit")
        self.assertEqual(failed.returncode, 0, failed.stdout + failed.stderr)
        state = self.state()
        self.assertEqual(state["concurrency"]["max"], 1)
        self.assertTrue(state["concurrency"]["cooldown_until"])
        unit = next(x for x in state["work_units"] if x["work_unit_id"] == leased["work_unit_id"])
        self.assertEqual(unit["status"], "RETRY_WAIT")
        self.assertEqual(unit["attempts"], 1)

    def test_next_work_allowlist_enforces_w3_stagger(self):
        # v3.4.2 fix（HIGH）：W3 错峰——契约顺序 investment-team(5) → mgmt(6) →
        # earnings(7) → industry-research(8)，若无 allowlist，要领 earnings-review
        # 必须先租出 management-deep-dive，错峰纪律在 Runtime 层不可实现。
        # 本测试验证：--allowlist 只派发白名单内的就绪单元，白名单外单元被挡。
        self.start()
        # 完成 W1+W2 全部依赖，使 W3 四单元就绪
        for skill in ("ashare-data", "financial-data", "quality-screen",
                      "investment-checklist", "investment-research"):
            self._set_unit_done(skill)
        # 错峰第一批：只领 investment-team + earnings-review
        leased = []
        for _ in range(5):
            result = json.loads(self.cli(
                "next-work", "--run-root", self.run_root,
                "--allowlist", "investment-team,earnings-review").stdout)
            if result["status"] == "LEASED":
                leased.append(result["skill_id"])
            else:
                break
        self.assertEqual(set(leased), {"investment-team", "earnings-review"})
        # management-deep-dive / industry-research 不得被白名单提前派发
        state = self.state()
        blocked = {u["skill_id"] for u in state["work_units"]
                   if u["skill_id"] in {"management-deep-dive", "industry-research"}}
        self.assertTrue(blocked)
        for sid in blocked:
            unit = next(u for u in state["work_units"] if u["skill_id"] == sid)
            self.assertEqual(unit["status"], "PENDING", f"{sid} 不得在 W3a 阶段被派发")
        # 白名单为空 allowlist（错误输入）退化为不过滤：可派发白名单外单元
        result = json.loads(self.cli(
            "next-work", "--run-root", self.run_root,
            "--allowlist", "management-deep-dive").stdout)
        self.assertEqual(result["status"], "LEASED")
        self.assertEqual(result["skill_id"], "management-deep-dive")

    def test_w3b_not_leased_until_w3a_done(self):
        # v3.4.4 fix（HIGH）：W3 错峰屏障——W3b（management-deep-dive+industry-research）
        # 只能在 W3a（investment-team+earnings-review）全部 DONE 后领取。
        # allowlist 只限制本轮可领集合，不越过依赖；W3b 单元依赖（W2）已满足时就绪，
        # 因此「W3a 全 DONE 前不得领 W3b」必须由编排纪律执行——本测试钉住该语义：
        # ① W3a 未 DONE 时 W3b 单元确实就绪（证明屏障只能靠编排，不能靠 runtime）；
        # ② W3a 全部 DONE 后，W3b allowlist 可正常收齐。
        self.start()
        for skill in ("ashare-data", "financial-data", "quality-screen",
                      "investment-checklist", "investment-research"):
            self._set_unit_done(skill)
        # 领出 W3a 两单元（不 DONE）
        w3a = []
        for _ in range(5):
            result = json.loads(self.cli(
                "next-work", "--run-root", self.run_root,
                "--allowlist", "investment-team,earnings-review").stdout)
            if result["status"] == "LEASED":
                w3a.append(result["skill_id"])
            else:
                break
        self.assertEqual(set(w3a), {"investment-team", "earnings-review"})
        # ① 编排器若误用 W3b allowlist（W3a 未 DONE），W3b 单元因依赖已满足而就绪——
        #    证明屏障必须由编排纪律执行（文档「W3a 全 DONE 后领 W3b」是硬前置条件）
        leaked = json.loads(self.cli(
            "next-work", "--run-root", self.run_root,
            "--allowlist", "management-deep-dive,industry-research").stdout)
        self.assertEqual(leaked["status"], "LEASED")
        self.assertIn(leaked["skill_id"],
                      {"management-deep-dive", "industry-research"})
        # ② 正确顺序：W3a 全部 DONE 后，W3b allowlist 收齐剩余单元
        for sid in w3a:
            self._set_unit_done(sid)
        # 上面误领了一个 W3b 单元，先把它也 DONE，再验证 W3b 可完整收齐
        self._set_unit_done(leaked["skill_id"])
        w3b = []
        for _ in range(5):
            result = json.loads(self.cli(
                "next-work", "--run-root", self.run_root,
                "--allowlist", "management-deep-dive,industry-research").stdout)
            if result["status"] == "LEASED":
                w3b.append(result["skill_id"])
            else:
                break
        self.assertEqual(len(w3b), 1)  # 剩余 1 个 W3b 单元
        self.assertIn(w3b[0], {"management-deep-dive", "industry-research"})
        self.assertNotEqual(w3b[0], leaked["skill_id"])

    def test_budget_adjust_only_raises_and_logs_event(self):
        # v3.4.2 fix（MEDIUM）：budget 触顶 CHECKPOINT 的「调高预算继续」需要 CLI 闭环。
        self.start()
        state = self.state()
        old_stop = state["budget"]["stop_dispatch_at"]
        old_hard = state["budget"]["hard_max"]
        # 先模拟触顶产生的 PARTIAL 残留（v3.4.4：budget-adjust 成功后须清除）
        (self.run_root / "PARTIAL_REPORT.md").write_text("stale")
        (self.run_root / "SUMMARY.md").write_text("stale")
        # 上调 hard_max + stop_dispatch_at
        result = self.cli("budget-adjust", "--run-root", self.run_root,
                          "--stop-dispatch-at", str(old_stop + 10),
                          "--hard-max", str(old_hard + 10),
                          "--reason", "人工调高预算继续")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["status"], "OK")
        # v3.4.4：PARTIAL 残留已清除
        self.assertIn("cleared_partial", body)
        self.assertFalse((self.run_root / "PARTIAL_REPORT.md").exists())
        self.assertFalse((self.run_root / "SUMMARY.md").exists())
        state = self.state()
        self.assertEqual(state["budget"]["stop_dispatch_at"], old_stop + 10)
        self.assertEqual(state["budget"]["hard_max"], old_hard + 10)
        # 事件已写入 events.jsonl
        evs = (self.run_root / "evidence/events.jsonl").read_text()
        self.assertIn("budget_adjusted", evs)
        # 倒置配置被拒（v3.4.4）：stop_dispatch_at >= hard_max 时拒绝
        inverted = self.cli("budget-adjust", "--run-root", self.run_root,
                            "--stop-dispatch-at", "133", "--hard-max", "33")
        self.assertNotEqual(inverted.returncode, 0)
        self.assertIn("倒置", inverted.stdout + inverted.stderr)
        self.assertIn("人工调高预算继续", evs)
        # 下调被拒（防静默降标）——old_hard=33 < old_stop+10=40，先命中倒置校验
        down = self.cli("budget-adjust", "--run-root", self.run_root,
                        "--hard-max", str(old_hard))
        self.assertNotEqual(down.returncode, 0)
        self.assertIn("倒置", down.stdout + down.stderr)

    def test_event_log_writes_whitelisted_kind(self):
        # v3.4.2 fix（MEDIUM）：doctor CHECKPOINT 人工结论需要受支持的 events.jsonl 写入入口。
        self.start()
        ok = self.cli("event-log", "--run-root", self.run_root,
                      "--kind", "doctor_checkpoint", "--note", "复核结论：确属坍塌，返工")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        body = json.loads(ok.stdout)
        self.assertEqual(body["status"], "OK")
        evs = (self.run_root / "evidence/events.jsonl").read_text()
        self.assertIn("doctor_checkpoint", evs)
        self.assertIn("复核结论：确属坍塌，返工", evs)
        # 非白名单类型被拒（argparse choices 在 CLI 层强制白名单）
        bad = self.cli("event-log", "--run-root", self.run_root,
                       "--kind", "arbitrary_injection", "--note", "x")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("invalid choice", bad.stderr)
        # 空 note 被拒（v3.4.4）：复核结论不可留空
        empty = self.cli("event-log", "--run-root", self.run_root,
                         "--kind", "doctor_checkpoint", "--note", "")
        self.assertNotEqual(empty.returncode, 0)
        self.assertIn("必填", empty.stdout + empty.stderr)

    def test_hard_budget_blocks_new_job_at_fifty(self):
        self.start()
        path = self.run_root / "evidence/runtime-state.json"
        state = self.state()
        state["budget"]["used"] = state["budget"]["hard_max"]
        path.write_text(json.dumps(state), encoding="utf-8")
        result = self.cli("next-work", "--run-root", self.run_root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(state["budget"]["hard_max"]), result.stdout + result.stderr)
        self.assertTrue((self.run_root / "PARTIAL_REPORT.md").is_file())
        self.assertTrue((self.run_root / "SUMMARY.md").is_file())

    def test_cleanup_requires_dry_run_and_resume_marks_old_attempt_abandoned(self):
        self.start()
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.cli("job-started", "--run-root", self.run_root,
                 "--work-unit-id", leased["work_unit_id"], "--attempt-id", leased["attempt_id"],
                 "--lease-nonce", leased["lease_nonce"], "--agent-job-id", "job-1")
        denied = self.cli("cleanup", "--run-root", self.run_root)
        self.assertNotEqual(denied.returncode, 0)
        preview = self.cli("cleanup", "--run-root", self.run_root, "--dry-run")
        self.assertEqual(preview.returncode, 0)
        resumed = self.cli("resume", "--run-root", self.run_root)
        self.assertEqual(resumed.returncode, 0)
        state = self.state()
        unit = next(x for x in state["work_units"] if x["work_unit_id"] == leased["work_unit_id"])
        self.assertIn(leased["attempt_id"], unit["abandoned_attempts"])

    def test_resume_recovers_valid_orphan_before_abandoning_live_lease(self):
        self.start()
        leased = self.lease_and_start()
        self.write_result(leased)

        resumed = self.cli("resume", "--run-root", self.run_root)

        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        payload = json.loads(resumed.stdout)
        self.assertIn(leased["work_unit_id"], payload["recovered"])
        unit = next(
            item for item in self.state()["work_units"]
            if item["work_unit_id"] == leased["work_unit_id"]
        )
        self.assertEqual(unit["status"], "DONE")
        self.assertNotIn(
            leased["attempt_id"], unit.get("abandoned_attempts", []))
        manifest = json.loads(
            (self.run_root / "evidence/00-analysis-manifest.json").read_text())
        skill = next(
            item for item in manifest["skills"]
            if item["skill_id"] == leased["skill_id"]
        )
        self.assertEqual(skill["status"], "PASS")

    def test_resume_recovers_orphan_from_never_job_started_leased_unit(self):
        """E15 回归：Agent 完成产物但从未 job-started（编排器拿到空返回）→
        lease.agent_job_id 为 None → 孤儿恢复不得因 agent_job_id 强比对被拒。
        历史事故：五粮液 run W4 三单元（industry-funnel/bottleneck/news-pulse）
        产物齐全但提交链路断裂，resume 全部被拒，租约过期卡死整个 run。"""
        self.start()
        # 只 next-work 不 job-started：模拟 Agent 领取租约后返回为空、从未登记
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(leased["status"], "LEASED")
        # Agent 静默完成：bundle 里 agent_job_id 是 Agent 自造值（非租约登记值）
        result_path = self.write_result(
            leased, agent_job_id="agent-fake-job-id-from-agent")

        resumed = self.cli("resume", "--run-root", self.run_root)

        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        payload = json.loads(resumed.stdout)
        self.assertIn(leased["work_unit_id"], payload["recovered"])
        unit = next(
            item for item in self.state()["work_units"]
            if item["work_unit_id"] == leased["work_unit_id"]
        )
        self.assertEqual(unit["status"], "DONE")
        manifest = json.loads(
            (self.run_root / "evidence/00-analysis-manifest.json").read_text())
        skill = next(
            item for item in manifest["skills"]
            if item["skill_id"] == leased["skill_id"]
        )
        self.assertEqual(skill["status"], "PASS")

    def test_submit_result_accepts_never_job_started_bundle_with_matching_ids(self):
        """E15 回归：从未 job-started 的 LEASED 租约直接 submit-result（Agent 自提交
        路径：Agent 完成分析后自己 job-started 失败/跳过，直接提交），只要
        attempt_id + lease_nonce 匹配即可接受，agent_job_id 不作强校验。"""
        self.start()
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(leased["status"], "LEASED")
        result_path = self.write_result(
            leased, agent_job_id="agent-self-attested-job-id")

        submitted = self.cli(
            "submit-result", "--run-root", self.run_root,
            "--registry", REGISTRY, "--result", result_path,
        )

        self.assertEqual(submitted.returncode, 0, submitted.stdout + submitted.stderr)
        self.assertEqual(json.loads(submitted.stdout)["status"], "DONE")
        unit = next(
            item for item in self.state()["work_units"]
            if item["work_unit_id"] == leased["work_unit_id"]
        )
        self.assertEqual(unit["status"], "DONE")

    def test_concurrent_job_started_updates_are_serialized_without_lost_budget(self):
        self.start()
        # v3.3.10：依赖门禁下先完成 ashare，W2 四个单元就绪供并发 job-started 租用
        self._set_unit_done("ashare-data")
        leases = [
            json.loads(
                self.cli("next-work", "--run-root", self.run_root).stdout)
            for _ in range(4)
        ]
        processes = []
        for index, leased in enumerate(leases):
            processes.append(subprocess.Popen(
                [
                    sys.executable, str(CLI), "job-started",
                    "--run-root", str(self.run_root),
                    "--work-unit-id", leased["work_unit_id"],
                    "--attempt-id", leased["attempt_id"],
                    "--lease-nonce", leased["lease_nonce"],
                    "--agent-job-id", f"job-concurrent-{index}",
                ],
                cwd=self.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ))
        completed = [process.communicate(timeout=20) for process in processes]

        for process, (stdout, stderr) in zip(processes, completed):
            self.assertEqual(process.returncode, 0, stdout + stderr)
        state = self.state()
        self.assertEqual(state["budget"]["used"], 5)
        self.assertEqual(
            sum(unit["status"] == "RUNNING" for unit in state["work_units"]),
            4,
        )
        self.assertTrue(
            (self.run_root / "evidence/locks/runtime-state.lock").is_file())

    def test_submit_result_rejects_bundle_not_bound_to_current_lease_before_gate(self):
        self.start()
        leased = self.lease_and_start()
        result_path = self.write_result(
            leased,
            attempt_id="attempt-stale",
            lease_nonce="foreign-nonce",
            agent_job_id="foreign-job",
        )

        submitted = self.cli(
            "submit-result", "--run-root", self.run_root,
            "--registry", REGISTRY, "--result", result_path,
        )

        self.assertNotEqual(submitted.returncode, 0)
        unit = next(x for x in self.state()["work_units"] if x["work_unit_id"] == leased["work_unit_id"])
        self.assertEqual(unit["status"], "RUNNING")
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        item = next(x for x in manifest["skills"] if x["skill_id"] == leased["skill_id"])
        self.assertEqual(item["status"], "PENDING")
        self.assertFalse((self.run_root / "01-数据与快筛/01-ashare-data.md").exists())

    def test_fail_result_closes_runtime_unit_and_run_as_failed(self):
        self.start()
        leased = self.lease_and_start()
        result_path = self.write_result(leased)
        bundle = json.loads(result_path.read_text(encoding="utf-8"))
        bundle.update({
            "status": "FAIL",
            "artifact_records": [],
            "error": {
                "code": "research_failed",
                "detail": "无法完成研究",
                "retryable": False,
            },
        })
        result_path.write_text(
            json.dumps(bundle, ensure_ascii=False), encoding="utf-8")

        submitted = self.cli(
            "submit-result",
            "--run-root", self.run_root,
            "--registry", REGISTRY,
            "--result", result_path,
        )

        self.assertEqual(
            submitted.returncode, 0, submitted.stdout + submitted.stderr)
        self.assertEqual(json.loads(submitted.stdout)["status"], "FAILED")
        unit = next(
            item for item in self.state()["work_units"]
            if item["work_unit_id"] == leased["work_unit_id"]
        )
        self.assertEqual(unit["status"], "FAILED")

        manifest_path = self.run_root / "evidence/00-analysis-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        failed_skill = next(
            item for item in manifest["skills"]
            if item["skill_id"] == leased["skill_id"]
        )
        self.assertEqual(failed_skill["status"], "FAIL")
        for item in manifest["skills"]:
            if item["skill_id"] != leased["skill_id"]:
                item["status"] = "NOT_APPLICABLE"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        finalized = subprocess.run(
            [
                sys.executable,
                str(GATE),
                "finalize",
                "--run-root", str(self.run_root),
                "--registry", str(REGISTRY),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(finalized.returncode, 0)
        final_manifest = json.loads(
            manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(final_manifest["run"]["status"], "FAILED")

    def test_expired_orphan_result_is_validated_instead_of_marked_done_from_status_only(self):
        self.start()
        leased = self.lease_and_start()
        attempt_dir = self.run_root / "evidence/attempts" / leased["skill_id"] / leased["attempt_id"]
        (attempt_dir / "result.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        state_path = self.run_root / "evidence/runtime-state.json"
        state = self.state()
        unit = next(x for x in state["work_units"] if x["work_unit_id"] == leased["work_unit_id"])
        unit["lease"]["expires_at"] = "2000-01-01T00:00:00+08:00"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        advanced = self.cli("next-work", "--run-root", self.run_root)

        self.assertEqual(advanced.returncode, 0, advanced.stdout + advanced.stderr)
        unit = next(x for x in self.state()["work_units"] if x["work_unit_id"] == leased["work_unit_id"])
        self.assertEqual(unit["status"], "RETRY_WAIT")
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        item = next(x for x in manifest["skills"] if x["skill_id"] == leased["skill_id"])
        self.assertEqual(item["status"], "PENDING")


    def test_methodology_ref_mode_omits_full_text(self):
        # Task 4: ref 模式返回稳定 hash/path，payload 不含完整 skill 文本；full 模式保持兼容
        self.start()
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root,
                                     "--methodology-mode", "ref").stdout)
        self.assertEqual(leased["status"], "LEASED")
        self.assertEqual(leased["methodology_mode"], "ref")
        self.assertIsNotNone(leased["methodology_ref"])
        self.assertEqual(len(leased["methodology_sha256"]), 64)
        ref_text = leased.get("methodology_text") or ""
        spec = Path(REPO) / leased["methodology_ref"]
        self.assertTrue(spec.is_file(), f"methodology_ref 应指向真实 spec: {spec}")
        full = spec.read_text(encoding="utf-8")
        # ref 模式 methodology_text 只含授权信封（不含 skill 正文长文）
        self.assertLess(len(ref_text), len(full) / 2,
                        f"ref payload 不应内嵌完整 skill 文本({len(ref_text)}>={len(full)}/2)")
        # hash 稳定性：等于 spec 文件内容 sha256（确定性，与派发次数无关）
        expected = hashlib.sha256(full.encode("utf-8")).hexdigest()
        self.assertEqual(leased["methodology_sha256"], expected)

    def test_methodology_full_mode_keeps_embedded_text(self):
        self.start()
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(leased["status"], "LEASED")
        self.assertEqual(leased["methodology_mode"], "full")
        self.assertGreater(len(leased.get("methodology_text") or ""), 1000,
                           "full 模式应内嵌完整方法论（含授权信封+skill 正文+指令）")
        self.assertIsNone(leased.get("methodology_ref"))


class DependencyGraphTests(unittest.TestCase):
    """v3.3.10 T4：contract depends_on 依赖图 + 拓扑分层 + 环检测。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.run_root = self.root / "local/company/000651.SZ-格力电器/20260723-120000-ab12"

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(CLI), *map(str, args)], cwd=self.root,
            capture_output=True, text=True,
        )

    # ---- 纯函数：依赖图构建 ----

    def test_build_graph_default_depends_on_ashare(self):
        # 缺省 depends_on 视为仅依赖 ashare（向后兼容）；ashare 自身为根（无依赖）
        skills = [
            {"skill_id": "ashare-data"},
            {"skill_id": "financial-data"},
            {"skill_id": "quality-screen", "depends_on": ["ashare-data"]},
        ]
        graph = rt.build_dependency_graph(skills)
        self.assertEqual(graph["ashare-data"], [])
        self.assertEqual(graph["financial-data"], ["ashare-data"])
        self.assertEqual(graph["quality-screen"], ["ashare-data"])

    def test_build_graph_explicit_deps_preserved(self):
        skills = [
            {"skill_id": "ashare-data"},
            {"skill_id": "a", "depends_on": ["ashare-data"]},
            {"skill_id": "b", "depends_on": ["ashare-data", "a"]},
        ]
        graph = rt.build_dependency_graph(skills)
        self.assertEqual(graph["b"], ["ashare-data", "a"])

    def test_build_graph_unknown_dep_raises(self):
        skills = [
            {"skill_id": "ashare-data"},
            {"skill_id": "a", "depends_on": ["nonexistent"]},
        ]
        with self.assertRaises(ValueError):
            rt.build_dependency_graph(skills)

    def test_detect_cycle_finds_ring(self):
        graph = {"ashare-data": [], "a": ["b"], "b": ["a"]}
        cycle = rt.detect_dependency_cycle(graph)
        self.assertIsNotNone(cycle)
        self.assertTrue(set(cycle).issubset({"a", "b"}))

    def test_detect_cycle_none_for_dag(self):
        graph = {"ashare-data": [], "a": ["ashare-data"], "b": ["ashare-data", "a"]}
        self.assertIsNone(rt.detect_dependency_cycle(graph))

    def test_compute_waves_matches_expected_six_waves(self):
        # v3.4.8: bottleneck-hunter + news-pulse 依赖 industry-funnel → W4 拆为 W4a+W4b，共 6 波
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        graph = rt.build_dependency_graph(registry["skills"])
        waves = rt.compute_dependency_waves(graph)
        self.assertEqual(len(waves), 6, f"应为 6 波，实际 {waves}")
        wave_sets = [set(w) for w in waves]
        self.assertEqual(wave_sets[0], {"ashare-data"})
        self.assertEqual(wave_sets[1],
                         {"financial-data", "quality-screen", "investment-checklist",
                          "investment-research"})
        self.assertEqual(wave_sets[2],
                         {"investment-team", "management-deep-dive",
                          "earnings-review", "industry-research"})
        self.assertEqual(wave_sets[3], {"industry-funnel"})
        self.assertEqual(wave_sets[4], {"bottleneck-hunter", "news-pulse"})
        self.assertEqual(wave_sets[5], {"thesis-tracker"})

    # ---- 端到端：init 持久化依赖图、拒绝有环契约 ----

    def test_init_persists_dependency_waves_into_state(self):
        result = self.cli(
            "start", "--registry", REGISTRY, "--repo-root", self.root,
            "--company", "格力电器", "--code", "000651.SZ", "--as-of", "2026-07-23",
            "--run-root", self.run_root,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = json.loads((self.run_root / "evidence/runtime-state.json").read_text())
        self.assertIn("dependency_graph", state)
        self.assertIn("dependency_waves", state)
        self.assertEqual(len(state["dependency_waves"]), 6)  # v3.4.8: W4 拆为 W4a+W4b
        # 每个 work_unit 带 depends_on
        by_id = {u["work_unit_id"]: u for u in state["work_units"]}
        self.assertEqual(by_id["wu-financial-data"].get("depends_on"), ["ashare-data"])

    def test_start_rejects_contract_with_dependency_cycle(self):
        # 构造含环的临时契约：financial-data 依赖 quality-screen，quality-screen 又依赖 financial-data
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for skill in registry["skills"]:
            if skill["skill_id"] == "financial-data":
                skill["depends_on"] = ["quality-screen"]
            elif skill["skill_id"] == "quality-screen":
                skill["depends_on"] = ["financial-data"]
        bad_registry = self.root / "bad-contract.json"
        bad_registry.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
        result = self.cli(
            "start", "--registry", bad_registry, "--repo-root", self.root,
            "--company", "格力电器", "--code", "000651.SZ", "--as-of", "2026-07-23",
            "--run-root", self.run_root,
        )
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("依赖", combined)
        self.assertIn("环", combined)

    # ---- v3.3.10 T7：fanout 租约 TTL 倍增 + per-lease 续期 ----

    @staticmethod
    def _parse(iso_str):
        return datetime.fromisoformat(iso_str)

    def _set_status(self, skill_id, status):
        path = self.run_root / "evidence/runtime-state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        for unit in state["work_units"]:
            if unit["skill_id"] == skill_id:
                unit["status"] = status
                unit["lease"] = None
        path.write_text(json.dumps(state), encoding="utf-8")

    def _lease_duration_minutes(self, leased):
        return (self._parse(leased["expires_at"])
                - self._parse(leased["leased_at"])).total_seconds() / 60

    def test_non_fanout_unit_gets_40min_lease_ttl(self):
        # 非扇出单元 TTL = NON_FANOUT_LEASE_MINUTES = 40 分（宏景 run 实证：
        # mgmt ~35min、ind-research ~25min，旧 TTL=20 频繁被 sweep 误回收）。
        result = self.cli(
            "start", "--registry", REGISTRY, "--repo-root", self.root,
            "--company", "格力电器", "--code", "000651.SZ", "--as-of", "2026-07-23",
            "--run-root", self.run_root,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # ashare-data 是首个就绪单元（非扇出），应获得 TTL=40
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(leased["skill_id"], "ashare-data")
        self.assertEqual(leased.get("lease_ttl_minutes"), 40)
        self.assertEqual(self._lease_duration_minutes(leased), 40)

    def test_fanout_unit_gets_multiplied_lease_ttl(self):
        # investment-team 扇出 4 角色（integrator 不计）→ TTL = LEASE_MINUTES×4 = 80 分，
        # lease 存 lease_ttl_minutes，防止多角色串行超时被 sweep 误回收。
        result = self.cli(
            "start", "--registry", REGISTRY, "--repo-root", self.root,
            "--company", "格力电器", "--code", "000651.SZ", "--as-of", "2026-07-23",
            "--run-root", self.run_root,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for skill in ("ashare-data", "financial-data", "quality-screen",
                      "investment-checklist", "investment-research"):
            self._set_status(skill, "DONE")
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(leased["skill_id"], "investment-team")
        self.assertEqual(leased.get("lease_ttl_minutes"), 80)
        self.assertEqual(self._lease_duration_minutes(leased), 80)

    def test_heartbeat_renews_by_stored_lease_ttl(self):
        # fanout 单元的 heartbeat 按存储的 lease_ttl_minutes（80 分）续期，而非默认 40 分。
        result = self.cli(
            "start", "--registry", REGISTRY, "--repo-root", self.root,
            "--company", "格力电器", "--code", "000651.SZ", "--as-of", "2026-07-23",
            "--run-root", self.run_root,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for skill in ("ashare-data", "financial-data", "quality-screen",
                      "investment-checklist", "investment-research"):
            self._set_status(skill, "DONE")
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(leased["skill_id"], "investment-team")
        beat = json.loads(self.cli(
            "heartbeat", "--run-root", self.run_root,
            "--work-unit-id", leased["work_unit_id"],
            "--attempt-id", leased["attempt_id"],
            "--lease-nonce", leased["lease_nonce"]).stdout)
        renewed = self._parse(beat["expires_at"]) - self._parse(leased["leased_at"])
        # 续期后距初始 leased_at 应 ≥80 分（heartbeat 时刻 + 80 分 TTL）
        self.assertGreaterEqual(renewed.total_seconds() / 60, 80)


if __name__ == "__main__":
    unittest.main()
