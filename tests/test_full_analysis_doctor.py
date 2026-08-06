"""full_analysis_doctor 回归测试：锁定"执行完整性体检"行为，防回退。

关键不变式：
1. 合规 run（产物充足余量 + 全覆盖提交事件）→ verdict=PASS，退出码 0；
2. 坍塌 run（全部分析单元压到下限 + 零提交事件）→ verdict=WARN，含"零提交事件"告警；
3. doctor 默认 advisory（恒退出 0），仅 --strict 时 WARN 抬升为退出码 3；
4. N/A / 缺产物 / 未完成单元不计入贴线统计（分别报告）；
5. 事件日志缺失/损坏 → 显式 WARN（不允许静默 PASS）；
6. 深度分化指纹按标准化 margin 的 CV 计算（而非原始 bytes）；
7. 尾部连续缺口指纹捕捉"后半程绕过 submit-result"；
8. DoctorError 是 Exception 子类（确保被 finalize 捕获，不致 SystemExit 逃逸）。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / "tools/full_analysis_contract.json"

sys.path.insert(0, str(REPO / "tools"))
import full_analysis_doctor as doctor  # noqa: E402

REPORTABLE = {"PASS", "PASS_WITH_LIMITATIONS"}


def _floors() -> dict:
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return {s["skill_id"]: (s.get("artifact") or {}).get("min_bytes") or 0
            for s in reg["skills"]}


def _build_run(root: Path, floors: dict, *, size_overrides=None,
               statuses=None, submit_skills=None, skip_events=False,
               corrupt_events=False, partial_bad_lines=0,
               attempt_submits=None, accepted_attempt_ids=None) -> Path:
    """构造最小可诊断 run。

    - size_overrides: {skill_id: size_mult}，控制每单元字节= floor*mult
    - statuses: {skill_id: status}，默认全部 PASS；NOT_APPLICABLE 单元不产物
    - submit_skills: 发提交事件的 skill_id 集合（每单元一条 skill 级 result_ingested，无 attempt_id）
    - attempt_submits: [(skill_id, attempt_id)] 精确到 attempt 的提交事件（result_submitted）
    - accepted_attempt_ids: {skill_id: attempt_id} 写入 artifact_records[].attempt_id
    - skip_events: 不写 events.jsonl（模拟日志缺失）
    - corrupt_events: 写全坏行（模拟日志损坏）
    - partial_bad_lines: 混入 N 条坏行（模拟日志部分损坏）
    """
    size_overrides = size_overrides or {}
    statuses = statuses or {}
    submit_skills = submit_skills or []
    attempt_submits = attempt_submits or []
    accepted_attempt_ids = accepted_attempt_ids or {}
    skills = []
    for sid, floor in floors.items():
        status = statuses.get(sid, "PASS")
        if status == "NOT_APPLICABLE":
            skills.append({"skill_id": sid, "status": status, "artifact_records": []})
            continue
        mult = size_overrides.get(sid, 2.0)
        size = max(1, int(floor * mult))
        rel = f"evidence/attempts/{sid}/attempt-x/report.md"
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"a" * size)
        rec = {"path": rel}
        if sid in accepted_attempt_ids:
            rec["attempt_id"] = accepted_attempt_ids[sid]
        skills.append({"skill_id": sid, "status": status, "artifact_records": [rec]})
    manifest = {"run": {"run_id": "test-run", "status": "APPROVED"}, "skills": skills}
    (root / "evidence").mkdir(parents=True, exist_ok=True)
    (root / "evidence/00-analysis-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    if skip_events:
        return root
    events = []
    if corrupt_events:
        (root / "evidence/events.jsonl").write_text(
            "not-json\n{broken\n", encoding="utf-8")
        return root
    for sid in submit_skills:
        events.append({"type": "result_ingested", "skill_id": sid})
    for sid, aid in attempt_submits:
        events.append({"type": "result_submitted", "work_unit_id": f"wu-{sid}", "attempt_id": aid})
    lines = [json.dumps(e, ensure_ascii=False) for e in events]
    lines += ["not-json-line"] * partial_bad_lines
    (root / "evidence/events.jsonl").write_text(
        "".join(l + "\n" for l in lines), encoding="utf-8")
    return root


def _analytic_ids(floors) -> list:
    return [sid for sid in floors
            if sid not in (doctor.DATA_SKILLS | doctor.LIGHT_SKILLS)]


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "rr"
        self.root.mkdir(parents=True)
        self.floors = _floors()

    def tearDown(self):
        self.temp.cleanup()

    def test_compliant_run_verdict_pass(self):
        ids = list(self.floors)
        _build_run(self.root, self.floors, submit_skills=ids)  # 全覆盖提交事件 + 2x 余量
        report = doctor.diagnose(self.root, REGISTRY)
        self.assertEqual(report["verdict"], "PASS", report["warnings"])
        self.assertEqual(report["thin_units"], [])
        self.assertGreaterEqual(report["submit_coverage"], doctor.COVERAGE_FLOOR)

    def test_cliff_run_verdict_warn(self):
        size_ov = {sid: 1.0 for sid in self.floors}  # 全部压线
        _build_run(self.root, self.floors, size_overrides=size_ov, submit_skills=[])
        report = doctor.diagnose(self.root, REGISTRY)
        self.assertEqual(report["verdict"], "WARN")
        self.assertTrue(any("零提交事件" in w for w in report["warnings"]), report["warnings"])
        self.assertGreaterEqual(report["thin_share_analytic"], 0.40)

    def test_na_units_excluded_from_thin(self):
        # 7 个分析单元 N/A，其余 2x 余量 + 全覆盖提交事件 → 应 PASS（N/A 不算贴线）
        analytic = _analytic_ids(self.floors)
        na = dict.fromkeys(analytic[:7], "NOT_APPLICABLE")
        rest = [sid for sid in self.floors if sid not in na]
        _build_run(self.root, self.floors, statuses=na, submit_skills=rest)
        report = doctor.diagnose(self.root, REGISTRY)
        self.assertEqual(len(report["na_units"]), 7)
        self.assertEqual(report["verdict"], "PASS", report["warnings"])

    def test_events_missing_warns(self):
        ids = list(self.floors)
        _build_run(self.root, self.floors, submit_skills=ids, skip_events=True)
        report = doctor.diagnose(self.root, REGISTRY)
        self.assertEqual(report["events_status"], "missing")
        self.assertEqual(report["verdict"], "WARN")
        self.assertTrue(any("事件日志" in w for w in report["warnings"]))

    def test_events_corrupt_warns(self):
        ids = list(self.floors)
        _build_run(self.root, self.floors, submit_skills=ids, corrupt_events=True)
        report = doctor.diagnose(self.root, REGISTRY)
        self.assertEqual(report["events_status"], "corrupt")
        self.assertEqual(report["verdict"], "WARN")

    def test_events_partial_warns(self):
        """P0：部分损坏的事件日志不能静默 PASS，应产生 WARN。"""
        ids = list(self.floors)
        _build_run(self.root, self.floors, submit_skills=ids, partial_bad_lines=2)
        report = doctor.diagnose(self.root, REGISTRY)
        self.assertEqual(report["events_status"], "partial")
        self.assertEqual(report["verdict"], "WARN")
        self.assertTrue(any("部分损坏" in w for w in report["warnings"]))

    def test_stale_attempt_submit_does_not_cover_accepted(self):
        """P0：旧失败 attempt 的提交事件不得替零提交的 accepted attempt 背书。"""
        ids = list(self.floors)
        # accepted attempt 统一为 attempt-final；但提交事件全部打在旧 attempt-old 上
        accepted = {sid: "attempt-final" for sid in ids}
        stale_submit = [(sid, "attempt-old") for sid in ids]
        _build_run(self.root, self.floors, accepted_attempt_ids=accepted,
                   attempt_submits=stale_submit)
        report = doctor.diagnose(self.root, REGISTRY)
        # 虽然 submit_total>0（有旧提交），但 accepted attempt 覆盖应为 0 → 全程零提交告警
        self.assertEqual(report["submit_coverage"], 0.0)
        self.assertTrue(any("零提交事件" in w for w in report["warnings"]),
                        report["warnings"])
        self.assertEqual(report["verdict"], "WARN")

    def test_matching_attempt_submit_covers_accepted(self):
        """对照组：提交事件打在 accepted attempt 上 → 覆盖成立 → PASS。"""
        ids = list(self.floors)
        accepted = {sid: "attempt-final" for sid in ids}
        good_submit = [(sid, "attempt-final") for sid in ids]
        _build_run(self.root, self.floors, accepted_attempt_ids=accepted,
                   attempt_submits=good_submit)
        report = doctor.diagnose(self.root, REGISTRY)
        self.assertEqual(report["submit_coverage"], 1.0)
        self.assertEqual(report["verdict"], "PASS", report["warnings"])

    def test_margin_cv_divergence_fires(self):
        # 指纹4 关键判别：原始 bytes CV 高（floor 差异大）但标准化 margin CV 低。
        # 4 个分析单元 margin 1.05（贴线），其余 1.30（不贴线）→ thin_share≈0.36<0.40（指纹1不触发）
        analytic = _analytic_ids(self.floors)
        size_ov = {}
        for i, sid in enumerate(analytic):
            size_ov[sid] = 1.05 if i < 4 else 1.30
        ids = list(self.floors)
        _build_run(self.root, self.floors, size_overrides=size_ov, submit_skills=ids)
        report = doctor.diagnose(self.root, REGISTRY)
        self.assertTrue(any("深度分化不足" in w for w in report["warnings"]), report["warnings"])
        # 指纹1 不应触发（thin_share<0.40）
        self.assertFalse(any("分布坍塌" in w for w in report["warnings"]))
        self.assertEqual(report["verdict"], "WARN")

    def test_tail_gap_warns(self):
        # 前半程有提交事件、尾部连续 ≥8 单元无提交事件 → 后半程绕过提交告警
        ran = [sid for sid, st in [(s, "PASS") for s in self.floors]]
        # 前 5 个单元发提交事件，尾部 8 个无 → tail_gap=8
        submit = ran[:5]
        _build_run(self.root, self.floors, submit_skills=submit)
        report = doctor.diagnose(self.root, REGISTRY)
        self.assertGreaterEqual(report["tail_gap"], doctor.TAIL_GAP_THRESHOLD)
        self.assertTrue(any("尾部连续" in w for w in report["warnings"]), report["warnings"])

    def test_doctor_error_is_exception_subclass(self):
        # 保证 finalize 的 `except Exception` 能捕获，SystemExit 不会逃逸
        self.assertTrue(issubclass(doctor.DoctorError, Exception))
        self.assertFalse(issubclass(doctor.DoctorError, SystemExit))

    def test_advisory_exit_code_zero_by_default(self):
        _build_run(self.root, self.floors,
                   size_overrides={s: 1.0 for s in self.floors}, submit_skills=[])
        self.assertEqual(doctor.main(["--run-root", str(self.root),
                                      "--registry", str(REGISTRY)]), 0)

    def test_strict_exit_code_three_on_warn(self):
        _build_run(self.root, self.floors,
                   size_overrides={s: 1.0 for s in self.floors}, submit_skills=[])
        self.assertEqual(doctor.main(["--run-root", str(self.root),
                                      "--registry", str(REGISTRY), "--strict"]), 3)

    def test_strict_exit_code_zero_on_pass(self):
        _build_run(self.root, self.floors, submit_skills=list(self.floors))
        self.assertEqual(doctor.main(["--run-root", str(self.root),
                                      "--registry", str(REGISTRY), "--strict"]), 0)


if __name__ == "__main__":
    unittest.main()
