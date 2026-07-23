import json
import unittest
from pathlib import Path


FIXTURE = (
    Path(__file__).parent / "fixtures" / "full_analysis" / "incidents.json"
)

REQUIRED = {
    "incident_id",
    "company_code",
    "root_cause",
    "stage",
    "injected_fault",
    "expected_outcome",
    "manual_action_forbidden",
}

EXPECTED_ROOT_CAUSES = {
    "control_plane",
    "schema",
    "data_lifecycle",
    "facts",
    "sections",
    "scheduler",
    "audit",
    "delivery",
}


class FullAnalysisIncidentFixtureTests(unittest.TestCase):
    def _load_rows(self):
        self.assertTrue(FIXTURE.is_file(), f"missing incident fixture: {FIXTURE}")
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_incident_fixture_covers_observed_root_causes(self):
        rows = self._load_rows()

        self.assertGreaterEqual(len(rows), len(EXPECTED_ROOT_CAUSES))
        self.assertTrue(all(REQUIRED <= row.keys() for row in rows))
        self.assertEqual(
            {row["root_cause"] for row in rows},
            EXPECTED_ROOT_CAUSES,
        )
        self.assertTrue(all(row["manual_action_forbidden"] is True for row in rows))

    def test_incident_fixture_covers_three_historical_companies_and_faults(self):
        rows = self._load_rows()
        companies = {row["company_code"] for row in rows}
        faults = {row["injected_fault"] for row in rows}

        self.assertTrue({"600276.SH", "603501.SH", "000651.SZ"} <= companies)
        self.assertTrue(
            {
                "tushare_unavailable",
                "empty_fact",
                "artifact_type_mismatch",
                "invalid_calculation",
                "missing_section",
                "agent_timeout",
                "rate_limit_429",
            }
            <= faults
        )

    def test_incident_ids_and_expected_outcomes_are_unique_and_explicit(self):
        rows = self._load_rows()
        ids = [row["incident_id"] for row in rows]

        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(row["expected_outcome"] for row in rows))
        self.assertTrue(all(row["stage"] for row in rows))


if __name__ == "__main__":
    unittest.main()
