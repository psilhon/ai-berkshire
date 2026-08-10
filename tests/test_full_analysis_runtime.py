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

    def lease_only(self):
        # lean（v3.7+）：next-work 直接返回就绪单元，无 job-started / 无租约登记。
        return json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)

    def write_result(self, leased, *, attempt_id=None, agent_job_id=None):
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        skill = next(s for s in registry["skills"] if s["skill_id"] == leased["skill_id"])
        attempt_id = attempt_id or leased["attempt_id"]
        attempt_dir = self.run_root / "evidence/attempts" / leased["skill_id"] / attempt_id
        attempt_dir.mkdir(parents=True, exist_ok=True)
        artifact = attempt_dir / "report.md"
        artifact.write_text(build_compliant_report(REGISTRY, leased["skill_id"]), encoding="utf-8")
        (facts, sources, calculations, judgments, role_runs,
         command_receipts, capability_records) = build_compliant_evidence(
            REGISTRY, leased["skill_id"], self.run_root)
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        bundle = {
            "schema_version": "result-schema/v1",
            "run_id": manifest["run"]["run_id"],
            "work_unit_id": leased["work_unit_id"],
            "attempt_id": attempt_id,
            "agent_job_id": agent_job_id or f"job-{leased['attempt_id']}",
            "lease_nonce": None,
            "skill_id": leased["skill_id"],
            "role_id": None,
            "status": "PASS",
            "artifact_records": [{
                "artifact_id": skill["artifact"].get(
                    "artifact_id", f"artifact.{leased['skill_id']}"),
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
        # v3.4.10：normal_target 机器派生 = 2 × 契约单元数 + 1（13 → 27，+1 = preflight 计入 used），非魔数
        self.assertEqual(state["budget"]["normal_target"], 2 * len(state["work_units"]) + 1)
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
        # lean-v1：证据指令注入的是"数组可为空、绝不合成 PLACEHOLDER"口径，
        # 不再引导 Agent 依赖契约已移除的 evidence_rules 键。
        self.assertIn("lean-v1 下数组可为空", methodology)
        self.assertNotIn("evidence_rules 为准", methodology)

    def _set_unit_done(self, skill_id):
        """直接把某单元置为 DONE（模拟上游完成），隔离测试依赖门禁逻辑。"""
        path = self.run_root / "evidence/runtime-state.json"
        state = self.state()
        for unit in state["work_units"]:
            if unit["skill_id"] == skill_id:
                unit["status"] = "DONE"
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
        # v3.5.1：完成 ashare 后，编排器循环 next-work 至多租出 2 个（并发上限 2，
        # 用户指令从 4 收紧），第 3 个因 CONCURRENCY_LIMIT 停（不是 DEPENDENCIES_PENDING）
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
        self.assertEqual(len(leased), 2)
        self.assertLessEqual(set(leased),
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

    def test_lease_binding_accepts_never_job_started_bundle_with_matching_ids(self):
        """lean（v3.7+）：已无租约身份机。submit-result 仅校验 Result Bundle 与当前
        单元的强身份一致：run_id / work_unit_id / skill_id / status + 由
        `attempt-{work_unit_id}-{attempts}` 派生的 attempt_id。lease_nonce /
        agent_job_id 不再参与绑定（仅为 bundle 溯源字段，可空）。

        直接测 runtime 的身份绑定原语而非走 CLI：submit-result 会继续调用 Gate 的
        完整 ingest，而 ingest 的实质校验当前有独立缺陷（见文件末尾说明），
        会掩盖本测试要钉住的语义。
        """
        self.start()
        leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(leased["status"], "LEASED")
        state = self.state()
        bundle = {
            "run_id": state["run_id"],
            "work_unit_id": leased["work_unit_id"],
            "skill_id": leased["skill_id"],
            "attempt_id": leased["attempt_id"],
            "lease_nonce": None,
            "agent_job_id": "agent-self-attested-job-id",
        }

        unit = rt._validate_result_lease(state, bundle)

        self.assertEqual(unit["work_unit_id"], leased["work_unit_id"])
        # 强身份字段不可伪造：attempt_id / work_unit_id 任一不匹配都必须被拒
        with self.assertRaises(rt.RuntimeErrorState):
            rt._validate_result_lease(state, {**bundle, "attempt_id": "attempt-stale"})
        with self.assertRaises(rt.RuntimeErrorState):
            rt._validate_result_lease(state, {**bundle, "work_unit_id": "wu-foreign"})
        # lease_nonce 不再参与绑定：伪造 nonce 也不影响（绑定只看 attempt_id 等）
        self.assertEqual(
            rt._validate_result_lease(state, {**bundle, "lease_nonce": "foreign-nonce"})["work_unit_id"],
            leased["work_unit_id"],
        )

    def test_submit_result_rejects_bundle_with_wrong_attempt_id(self):
        """lean：submit-result 在调用 Gate 前先绑定 Result Bundle 到当前活动单元，
        只接受派生的 attempt_id；伪造 attempt_id 必须被拒（不进入 Gate）。"""
        self.start()
        leased = self.lease_only()
        result_path = self.write_result(leased, attempt_id="attempt-stale")

        submitted = self.cli(
            "submit-result", "--run-root", self.run_root,
            "--registry", REGISTRY, "--result", result_path,
        )

        self.assertNotEqual(submitted.returncode, 0)
        unit = next(x for x in self.state()["work_units"] if x["work_unit_id"] == leased["work_unit_id"])
        self.assertEqual(unit["status"], "LEASED")  # 未进 Gate，状态不变
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        item = next(x for x in manifest["skills"] if x["skill_id"] == leased["skill_id"])
        self.assertEqual(item["status"], "PENDING")
        self.assertFalse((self.run_root / "01-数据与快筛/01-ashare-data.md").exists())

    def test_fail_result_closes_runtime_unit_and_run_as_failed(self):
        self.start()
        leased = self.lease_only()
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

    # ---- lean 失败声明：mark-failed 是唯一的失败判定入口（sweep 看门狗已移除） ----

    def test_mark_failed_declares_unit_failed_with_reason(self):
        # lean 模式：租约不会被自动回收，卡死/失败必须由编排器显式声明，
        # 失败原因随单元落盘，供终稿如实标注缺口。
        self.start()
        leased = self.lease_only()

        marked = self.cli("mark-failed", "--run-root", self.run_root,
                          "--skill-id", leased["skill_id"],
                          "--reason", "数据源连续三次超时，判定不可完成")

        self.assertEqual(marked.returncode, 0, marked.stdout + marked.stderr)
        payload = json.loads(marked.stdout)
        self.assertEqual(payload["status"], "FAILED")
        self.assertEqual(payload["skill_id"], leased["skill_id"])
        unit = next(x for x in self.state()["work_units"]
                    if x["work_unit_id"] == leased["work_unit_id"])
        self.assertEqual(unit["status"], "FAILED")
        self.assertEqual(unit["failure"]["reason"], "数据源连续三次超时，判定不可完成")
        self.assertTrue(unit["failure"]["declared_at"])
        self.assertNotIn("lease", unit)  # lean：单元不携带租约字段
        events = (self.run_root / "evidence/events.jsonl").read_text(encoding="utf-8")
        self.assertIn("unit_failed", events)

    def test_next_work_reflects_mark_failed_unit(self):
        # FAILED 单元不再被派发；依赖它的下游保持阻塞（不放行、不静默跳过）。
        self.start()
        leased = self.lease_only()
        self.assertEqual(leased["skill_id"], "ashare-data")  # 根节点，全体下游依赖它
        self.cli("mark-failed", "--run-root", self.run_root,
                 "--skill-id", leased["skill_id"], "--reason", "数据源不可用")

        nothing = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)

        self.assertEqual(nothing["status"], "NO_WORK")
        self.assertEqual(nothing["reason"], "DEPENDENCIES_PENDING")

    def test_mark_failed_with_retry_requeues_unit_for_redispatch(self):
        # --retry 是显式重排一次（不置 FAILED），attempts 自增以保留重试痕迹。
        self.start()
        leased = self.lease_only()

        retried = self.cli("mark-failed", "--run-root", self.run_root,
                           "--skill-id", leased["skill_id"],
                           "--reason", "Agent 空返回", "--retry")

        self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)
        self.assertEqual(json.loads(retried.stdout)["status"], "RETRIED")
        unit = next(x for x in self.state()["work_units"]
                    if x["work_unit_id"] == leased["work_unit_id"])
        self.assertEqual(unit["status"], "PENDING")
        self.assertEqual(unit["attempts"], 2)
        self.assertNotIn("lease", unit)
        re_leased = json.loads(self.cli("next-work", "--run-root", self.run_root).stdout)
        self.assertEqual(re_leased["status"], "LEASED")
        self.assertEqual(re_leased["work_unit_id"], leased["work_unit_id"])
        self.assertNotEqual(re_leased["attempt_id"], leased["attempt_id"])

    def test_mark_failed_rejects_unknown_skill(self):
        self.start()
        bad = self.cli("mark-failed", "--run-root", self.run_root,
                       "--skill-id", "no-such-skill", "--reason", "x")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("未知 skill_id", bad.stdout + bad.stderr)

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

    def test_sweep_command_is_removed(self):
        # lean（v3.7）：sweep 看门狗已移除——租约不再被自动回收，失败一律走 mark-failed。
        # 钉住"命令不存在"，防止 watchdog 机制被悄悄复活。
        self.start()
        result = self.cli("sweep", "--run-root", self.run_root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid choice", result.stderr)


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
        path.write_text(json.dumps(state), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
