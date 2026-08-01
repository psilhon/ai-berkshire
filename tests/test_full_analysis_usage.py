"""Task 1：record-usage 真实 Token/字节/重试计量（TDD 失败测试先行）。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_full_analysis_e2e import build_compliant_evidence, build_compliant_report

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "full_analysis.py"
REGISTRY = REPO / "tools" / "full_analysis_contract.json"


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.run_root = self.root / "local/company/000651.SZ-格力电器/20260723-120000-ab12"
        # start 一个最小 run
        result = self.cli("start", "--registry", REGISTRY, "--repo-root", self.root,
                          "--company", "格力电器", "--code", "000651.SZ",
                          "--as-of", "2026-07-23", "--run-root", self.run_root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(CLI), *map(str, args)], cwd=self.root,
            capture_output=True, text=True,
        )

    def usage_file(self):
        return self.run_root / "evidence/usage.jsonl"

    def _record(self, **over):
        base = {
            "run-root": self.run_root,
            "phase": "work",
            "attempt-id": "attempt-usage1",
            "skill-id": "ashare-data",
            "input-tokens": 100,
            "output-tokens": 50,
            "input-bytes": 2048,
            "output-bytes": 1024,
            "duration-ms": 1200,
        }
        args = []
        for k, v in {**base, **over}.items():
            if v is None:
                continue  # 未提供（含 store_true 关闭态）
            if isinstance(v, bool):
                if v:
                    args.append(f"--{k}")
                continue
            args.append(f"--{k}")
            args.append(str(v))
        return self.cli("record-usage", *args)

    def test_usage_writes_jsonl_record(self):
        proc = self._record()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        lines = self.usage_file().read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["schema_version"], "usage-receipt/v1")
        self.assertEqual(rec["phase"], "work")
        self.assertEqual(rec["skill_id"], "ashare-data")
        self.assertEqual(rec["attempt_id"], "attempt-usage1")
        self.assertEqual(rec["input_tokens"], 100)
        self.assertEqual(rec["output_tokens"], 50)
        self.assertEqual(rec["input_bytes"], 2048)
        self.assertEqual(rec["output_bytes"], 1024)
        self.assertEqual(rec["duration_ms"], 1200)
        self.assertIn("run_id", rec)

    def test_negative_tokens_rejected(self):
        proc = self._record(**{"input-tokens": -1})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("负数", proc.stdout + proc.stderr)
        self.assertFalse(self.usage_file().exists())

    def test_missing_tokens_recorded_as_null(self):
        proc = self._record(**{"input-tokens": None, "output-tokens": None})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rec = json.loads(self.usage_file().read_text().strip().splitlines()[0])
        self.assertIsNone(rec["input_tokens"])
        self.assertIsNone(rec["output_tokens"])
        self.assertIsNotNone(rec["input_bytes"])

    def test_duplicate_attempt_phase_rejected(self):
        self.assertEqual(self._record().returncode, 0)
        proc = self._record()  # 同 attempt + phase 第二次
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("重复", proc.stdout + proc.stderr)
        lines = self.usage_file().read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)

    def test_missing_bytes_rejected(self):
        proc = self._record(**{"input-bytes": None, "output-bytes": None})
        self.assertNotEqual(proc.returncode, 0)

    def test_cache_hit_flag_optional(self):
        proc = self._record(**{"cache-hit": None})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rec = json.loads(self.usage_file().read_text().strip().splitlines()[0])
        self.assertIs(rec.get("cache_hit", False), False)
        proc2 = self._record(**{"attempt-id": "attempt-usage2", "cache-hit": True})
        self.assertEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)
        rec2 = json.loads(self.usage_file().read_text().strip().splitlines()[-1])
        self.assertIs(rec2["cache_hit"], True)

    def test_usage_summary_aggregated_in_manifest(self):
        self._record(**{"attempt-id": "attempt-a1", "skill-id": "ashare-data"})
        self._record(**{"attempt-id": "attempt-a2", "skill-id": "financial-data"})
        self._record(**{"attempt-id": "attempt-a2", "phase": "summary", "skill-id": "delivery-summary"})
        manifest = json.loads((self.run_root / "evidence/00-analysis-manifest.json").read_text())
        summary = manifest.get("usage_summary")
        self.assertIsNotNone(summary, "manifest 应含 usage_summary")
        by_phase = {p["phase"]: p for p in summary.get("by_phase", [])}
        self.assertEqual(by_phase["work"]["records"], 2)
        self.assertEqual(by_phase["work"]["input_tokens"], 200)
        self.assertEqual(by_phase["summary"]["records"], 1)
        by_skill = {s["skill_id"]: s for s in summary.get("by_skill", [])}
        self.assertEqual(by_skill["ashare-data"]["records"], 1)
        self.assertEqual(summary.get("total_tokens"), 450)  # 3 条 × (100+50)


if __name__ == "__main__":
    unittest.main()
