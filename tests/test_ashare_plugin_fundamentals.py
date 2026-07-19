import unittest
from unittest import mock

from tools.ashare_plugin.fundamentals import fetch_history


class FakeClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append((url, params, headers))
        return self.pages.pop(0)


class TestFundamentals(unittest.TestCase):
    def test_fetch_history_reads_all_pages_and_preserves_rows(self):
        client = FakeClient([
            {"success": True, "result": {"pages": 2, "data": [{"REPORT_YEAR": "2025"}]}},
            {"success": True, "result": {"pages": 2, "data": [{"REPORT_YEAR": "2024"}]}},
        ])

        result = fetch_history("600036", years=10, client=client)

        self.assertTrue(result["ok"])
        self.assertEqual([r["REPORT_YEAR"] for r in result["data"]], ["2025", "2024"])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0][1]["p"], "1")
        self.assertEqual(client.calls[1][1]["p"], "2")

    def test_empty_history_is_explicit_failure(self):
        client = FakeClient([
            {"success": True, "result": {"pages": 1, "data": []}},
        ])
        result = fetch_history("600036", client=client)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "empty_data")

    def test_successful_result_includes_verification(self):
        client = FakeClient([
            {"success": True, "result": {"pages": 1, "data": [{"REPORT_YEAR": "2025"}]}},
        ])
        fake_v = {"provider": "tushare", "configured": True, "status": "MATCH",
                   "as_of": None, "warnings": [], "fields": [], "endpoints": []}
        with mock.patch(
            "tools.ashare_plugin.fundamentals.safe_verify_command",
            return_value=fake_v,
        ):
            result = fetch_history("600036", years=10, client=client)
        self.assertTrue(result["ok"])
        self.assertIn("verification", result)
        self.assertIs(result["verification"], fake_v)


if __name__ == "__main__":
    unittest.main()
