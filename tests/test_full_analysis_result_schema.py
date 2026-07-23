import json
import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO / "tools" / "full_analysis_result_schema.json"


def valid_bundle():
    return {
        "schema_version": "result-schema/v1",
        "run_id": "run-20260723-0001",
        "work_unit_id": "wu-ashare-data",
        "attempt_id": "attempt-01",
        "agent_job_id": "job-01",
        "lease_nonce": "lease-01",
        "skill_id": "ashare-data",
        "role_id": None,
        "status": "PASS_WITH_LIMITATIONS",
        "artifact_records": [{
            "artifact_id": "artifact.ashare-data",
            "path": "01-数据与快筛/01-ashare-data.md",
            "bytes": 1024,
            "sha256": "a" * 64,
            "formal": True,
            "accepted": True,
        }],
        "fact_updates": [{
            "fact_id": "fact.price",
            "field": "price",
            "value": "100.00",
            "source_ids": ["source.eastmoney"],
        }],
        "source_records": [{
            "source_id": "source.eastmoney",
            "url": "https://example.invalid/source",
            "retrieved_at": "2026-07-23T10:00:00+08:00",
            "source_type": "web",
        }],
        "calculation_requests": [{
            "calculation_id": "calculation.market-cap",
            "operation": "verify-market-cap",
            "args": {"price": "100.00"},
        }],
        "judgments": [],
        "limitations": [{"code": "tushare_unavailable", "detail": "not configured"}],
        "pwl_candidates": ["tushare_unavailable"],
        "started_at": "2026-07-23T10:00:00+08:00",
        "completed_at": "2026-07-23T10:01:00+08:00",
        "error": None,
    }


class ResultSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_schema_declares_result_bundle_v1_and_closed_top_level(self):
        self.assertEqual(self.schema["schema_version"], "result-schema/v1")
        self.assertEqual(self.schema["type"], "object")
        self.assertFalse(self.schema["additionalProperties"])
        self.assertEqual(set(self.schema["required"]), {
            "schema_version", "run_id", "work_unit_id", "attempt_id",
            "agent_job_id", "lease_nonce", "skill_id", "role_id", "status",
            "artifact_records", "fact_updates", "source_records",
            "calculation_requests", "judgments", "limitations", "pwl_candidates",
            "started_at", "completed_at", "error",
        })

    def test_status_and_pwl_enums_are_closed(self):
        self.assertEqual(set(self.schema["properties"]["status"]["enum"]), {
            "PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE", "FAIL",
        })
        self.assertEqual(set(self.schema["properties"]["pwl_candidates"]["items"]["enum"]), {
            "tushare_unavailable", "web_bandwidth_degraded", "ephemeral_source",
        })

    def test_artifact_record_requires_hash_and_acceptance_fields(self):
        artifact = self.schema["properties"]["artifact_records"]["items"]
        self.assertFalse(artifact["additionalProperties"])
        self.assertEqual(set(artifact["required"]), {
            "artifact_id", "path", "bytes", "sha256", "formal", "accepted",
        })
        self.assertEqual(artifact["properties"]["sha256"]["pattern"], r"^[0-9a-f]{64}$")
        self.assertEqual(artifact["properties"]["artifact_id"]["pattern"], r"^artifact\.[a-z0-9._-]+$")

    def test_bundle_shape_can_be_checked_without_external_jsonschema(self):
        bundle = valid_bundle()
        self.assertEqual(bundle["schema_version"], self.schema["properties"]["schema_version"]["const"])
        self.assertIn(bundle["status"], self.schema["properties"]["status"]["enum"])
        artifact = bundle["artifact_records"][0]
        self.assertRegex(artifact["artifact_id"], self.schema["properties"]["artifact_records"]["items"]["properties"]["artifact_id"]["pattern"])
        self.assertRegex(artifact["sha256"], self.schema["properties"]["artifact_records"]["items"]["properties"]["sha256"]["pattern"])

    def test_fail_result_requires_error_and_success_result_forbids_it(self):
        status = self.schema["properties"]["status"]
        self.assertEqual(status["enum"], ["PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE", "FAIL"])
        self.assertEqual(self.schema["x_rules"]["error_required_when_status"], "FAIL")
        self.assertEqual(self.schema["x_rules"]["error_null_when_status"], ["PASS", "PASS_WITH_LIMITATIONS", "NOT_APPLICABLE"])

    def test_calculation_request_cannot_carry_agent_expected_result(self):
        calculation = self.schema["properties"]["calculation_requests"]["items"]
        self.assertEqual(set(calculation["required"]), {"calculation_id", "operation", "args"})
        self.assertNotIn("expected", calculation["properties"])


if __name__ == "__main__":
    unittest.main()
