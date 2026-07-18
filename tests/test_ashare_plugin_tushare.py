import os
import unittest
from unittest import mock

from tools.ashare_plugin.tushare import TushareClient


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class TestTushareClient(unittest.TestCase):
    @mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_token_returns_not_configured_without_transport_call(self):
        transport = FakeTransport({"code": 0, "data": {"fields": [], "items": []}})

        result = TushareClient(transport=transport).query("daily_basic")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "not_configured")
        self.assertEqual(transport.calls, [])

    def test_success_maps_fields_and_items_to_dict_rows(self):
        transport = FakeTransport({
            "code": 0,
            "msg": None,
            "data": {
                "fields": ["ts_code", "trade_date", "pb"],
                "items": [["600036.SH", "20260718", 1.2]],
            },
        })

        result = TushareClient(token="test-token", transport=transport).query(
            "daily_basic",
            params={"ts_code": "600036.SH"},
            fields=("ts_code", "trade_date", "pb"),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "tushare.daily_basic")
        self.assertEqual(result["data"][0]["pb"], 1.2)
        body = transport.calls[0][1]["data"]
        self.assertEqual(body["api_name"], "daily_basic")
        self.assertEqual(body["fields"], "ts_code,trade_date,pb")

    def test_permission_rate_empty_and_schema_are_distinct(self):
        cases = [
            ({"code": 2002, "msg": "无权限", "data": None}, "permission_denied"),
            ({"code": -2001, "msg": "每分钟最多访问 50 次", "data": None}, "rate_limited"),
            ({"code": 0, "msg": None, "data": {"fields": ["x"], "items": []}}, "empty_data"),
            ({"code": 0, "msg": None, "data": {"fields": ["x"], "items": [[1, 2]]}}, "schema_error"),
        ]

        for response, expected in cases:
            with self.subTest(expected=expected):
                result = TushareClient(
                    token="test-token", transport=FakeTransport(response)
                ).query("sample")
                self.assertFalse(result["ok"])
                self.assertEqual(result["error_type"], expected)


if __name__ == "__main__":
    unittest.main()
