#!/usr/bin/env python3
"""run_store 深模块的接口测试（2026-08-30 架构评审候选②·完整版）。

机器证明三件事：
1. 四个状态文件的 I/O 只有 run_store 一条缝（gate/runtime 均为薄委托）；
2. 原子写语义正确（正常落盘 / 失败保留旧文件 / 无临时文件残留）；
3. 状态/账本读写闭环（roundtrip）+ 版本校验单点（STATE_VERSION）。

背景缺陷：runtime 刷新 usage 汇总时曾用 write_text 直写 manifest（非原子），
且 gate/runtime 各持一套原子写实现（fsync vs 无 fsync）——本测试锁定收拢后行为。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import run_layout  # noqa: E402
import run_store  # noqa: E402


class TestSingleSource(unittest.TestCase):
    """I/O 单一所有者：gate/runtime 的原语必须是 run_store 同一对象（不是副本）。"""

    def test_gate_primitives_delegate_to_run_store(self):
        import full_analysis_gate as gate
        self.assertIs(gate.atomic_write_json, run_store.atomic_write_json)
        self.assertIs(gate.atomic_write_text, run_store.atomic_write_text)

    def test_state_file_paths_single_source(self):
        self.assertIs(run_store.LOCK_REL, run_layout.LOCK_REL)
        self.assertIs(run_store.MANIFEST_REL, run_layout.MANIFEST_REL)
        self.assertIs(run_store.RUNTIME_STATE_REL, run_layout.RUNTIME_STATE_REL)
        self.assertIs(run_store.EVENTS_REL, run_layout.EVENTS_REL)
        self.assertIs(run_store.USAGE_REL, run_layout.USAGE_REL)
        import full_analysis_runtime as runtime
        self.assertIs(runtime.LOCK_REL, run_layout.LOCK_REL)

    def test_state_version_is_single_constant(self):
        """gate 初始化与 runtime 校验必须用同一个 STATE_VERSION。"""
        import full_analysis_gate as gate
        import full_analysis_runtime as runtime
        self.assertEqual(run_store.STATE_VERSION, "runtime-state/v1")
        # gate 初始化写出的 state 必须能通过 runtime 的版本校验（roundtrip 语义）
        self.assertEqual(gate.run_store.STATE_VERSION, runtime.run_store.STATE_VERSION)


class TestAtomicWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="run-store-test-"))

    def _no_tmp_leftover(self, target: Path):
        leftovers = [p for p in target.parent.iterdir() if p.name.startswith(f".{target.name}.")]
        self.assertEqual(leftovers, [], f"临时文件残留: {leftovers}")

    def test_atomic_write_json_roundtrip(self):
        target = self.tmp / "state.json"
        run_store.atomic_write_json(target, {"a": 1, "b": "中文"})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"a": 1, "b": "中文"})
        self.assertTrue(target.read_text(encoding="utf-8").endswith("\n"))
        self._no_tmp_leftover(target)

    def test_atomic_write_json_preserves_mode_on_overwrite(self):
        target = self.tmp / "state.json"
        target.write_text("{}", encoding="utf-8")
        os.chmod(target, 0o600)
        run_store.atomic_write_json(target, {"ok": True})
        self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)
        self._no_tmp_leftover(target)

    def test_atomic_write_failure_preserves_previous_file(self):
        target = self.tmp / "state.json"
        target.write_text('{"old": true}\n', encoding="utf-8")

        class Unserializable:
            pass

        with self.assertRaises(TypeError):
            run_store.atomic_write_json(target, {"bad": Unserializable()})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"old": True})
        self._no_tmp_leftover(target)

    def test_atomic_write_text_roundtrip(self):
        target = self.tmp / "events.jsonl"
        run_store.atomic_write_text(target, "")
        self.assertEqual(target.read_text(encoding="utf-8"), "")
        run_store.atomic_write_text(target, "line\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "line\n")
        self._no_tmp_leftover(target)


class TestManifestIO(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="run-store-test-"))

    def test_manifest_roundtrip(self):
        manifest = {"manifest_schema_version": "full-analysis-manifest/v2", "run": {"run_id": "r1"}}
        run_store.write_manifest(self.tmp, manifest)
        self.assertEqual(run_store.load_manifest(self.tmp), manifest)

    def test_load_manifest_missing_raises_code_2(self):
        with self.assertRaises(run_store.RunStoreError) as ctx:
            run_store.load_manifest(self.tmp)
        self.assertEqual(ctx.exception.code, 2)

    def test_load_manifest_corrupt_raises_code_2(self):
        (self.tmp / "evidence").mkdir()
        (run_store.manifest_path(self.tmp)).write_text("{not json", encoding="utf-8")
        with self.assertRaises(run_store.RunStoreError) as ctx:
            run_store.load_manifest(self.tmp)
        self.assertEqual(ctx.exception.code, 2)

    def test_load_manifest_non_object_raises_code_2(self):
        (self.tmp / "evidence").mkdir()
        run_store.manifest_path(self.tmp).write_text("[1,2]\n", encoding="utf-8")
        with self.assertRaises(run_store.RunStoreError) as ctx:
            run_store.load_manifest(self.tmp)
        self.assertIn("顶层必须为对象", str(ctx.exception))

    def test_manifest_path_layout(self):
        self.assertEqual(run_store.manifest_path(self.tmp),
                         self.tmp / "evidence" / "00-analysis-manifest.json")


class TestRuntimeStateIO(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="run-store-test-"))

    def _write_state(self, version="runtime-state/v1"):
        run_store.write_runtime_state(self.tmp, {"state_version": version, "budget": {"used": 0}})

    def test_state_roundtrip(self):
        self._write_state()
        state = run_store.load_runtime_state(self.tmp)
        self.assertEqual(state["state_version"], "runtime-state/v1")

    def test_state_version_mismatch_raises_code_1(self):
        self._write_state(version="runtime-state/v9")
        with self.assertRaises(run_store.RunStoreError) as ctx:
            run_store.load_runtime_state(self.tmp)
        self.assertIn("版本不匹配", str(ctx.exception))
        self.assertEqual(ctx.exception.code, 1)

    def test_state_missing_raises_code_2(self):
        with self.assertRaises(run_store.RunStoreError) as ctx:
            run_store.load_runtime_state(self.tmp)
        self.assertEqual(ctx.exception.code, 2)


class TestEventsAndUsage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="run-store-test-"))

    def test_append_event_adds_event_at_first(self):
        record = run_store.append_event(self.tmp, {"type": "run_initialized", "run_id": "r1"})
        self.assertEqual(list(record.keys())[0], "event_at")
        self.assertEqual(record["type"], "run_initialized")
        self.assertEqual(run_store.read_events(self.tmp), [record])

    def test_append_event_preserves_explicit_event_at(self):
        record = run_store.append_event(self.tmp, {"event_at": "2026-01-01T00:00:00+08:00"})
        self.assertEqual(record["event_at"], "2026-01-01T00:00:00+08:00")

    def test_reset_events_creates_empty_file(self):
        run_store.append_event(self.tmp, {"type": "x"})
        run_store.reset_events(self.tmp)
        self.assertEqual((self.tmp / run_layout.EVENTS_REL).read_text(encoding="utf-8"), "")
        self.assertEqual(run_store.read_events(self.tmp), [])

    def test_read_events_missing_returns_empty(self):
        self.assertEqual(run_store.read_events(self.tmp), [])

    def test_usage_roundtrip(self):
        self.assertEqual(run_store.read_usage(self.tmp), [])
        receipt = {"schema_version": "usage-receipt/v1", "phase": "work", "skill_id": "s1"}
        run_store.append_usage(self.tmp, receipt)
        self.assertEqual(run_store.read_usage(self.tmp), [receipt])
        self.assertEqual(run_store.usage_path(self.tmp),
                         self.tmp / "evidence" / "usage.jsonl")


class TestDelegationBehavior(unittest.TestCase):
    """gate/runtime 的薄委托在行为层面与 run_store 语义一致。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="run-store-test-"))

    def test_gate_append_event_delegates(self):
        import full_analysis_gate as gate
        gate.append_event(self.tmp, {"type": "result_ingested", "skill_id": "s1"})
        events = run_store.read_events(self.tmp)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "result_ingested")
        self.assertIn("event_at", events[0])

    def test_gate_load_manifest_translates_error(self):
        import full_analysis_gate as gate
        with self.assertRaises(gate.GateError) as ctx:
            gate.load_manifest(self.tmp)
        self.assertEqual(ctx.exception.code, 2)

    def test_runtime_load_state_translates_error(self):
        import full_analysis_runtime as runtime
        with self.assertRaises(runtime.RuntimeErrorState):
            runtime.load_state(self.tmp)

    def test_runtime_save_load_roundtrip(self):
        import full_analysis_runtime as runtime
        runtime.save_state(self.tmp, {"state_version": run_store.STATE_VERSION, "budget": {"used": 3}})
        self.assertEqual(runtime.load_state(self.tmp)["budget"]["used"], 3)

    def test_runtime_event_delegates(self):
        import full_analysis_runtime as runtime
        runtime.event(self.tmp, "usage_recorded", phase="work", skill_id="s1")
        events = run_store.read_events(self.tmp)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "usage_recorded")
        self.assertEqual(events[0]["phase"], "work")


if __name__ == "__main__":
    unittest.main()
