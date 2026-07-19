import unittest
from unittest import mock

from tools.ashare_plugin.disclosures import fetch_announcements


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append(("GET", url, params, headers, False))
        response = self.responses.get(url)
        if isinstance(response, Exception):
            raise response
        return response

    def post_json(self, url, data=None, headers=None, json_body=False):
        self.calls.append(("POST", url, data, headers, json_body))
        response = self.responses.get(url)
        if isinstance(response, Exception):
            raise response
        return response


class TestDisclosures(unittest.TestCase):
    def test_cninfo_rows_are_normalized_with_source(self):
        client = FakeClient({
            "https://www.cninfo.com.cn/new/data/szse_stock.json": {
                "stockList": [{
                    "code": "300750",
                    "orgId": "GD165627",
                }],
            },
            "https://www.cninfo.com.cn/new/hisAnnouncement/query": {
                "announcements": [{
                    "announcementTitle": "年度报告",
                    "announcementTime": 1760000000000,
                    "columnName": "定期报告",
                    "adjunctUrl": "doc.pdf",
                }]
            }
        })

        result = fetch_announcements("300750", client=client)
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "cninfo")
        self.assertEqual(result["data"][0]["title"], "年度报告")
        self.assertEqual(result["data"][0]["type"], "定期报告")
        self.assertIn("doc.pdf", result["data"][0]["pdf"])
        query_call = [call for call in client.calls
                      if call[1].endswith("hisAnnouncement/query")][0]
        self.assertEqual(query_call[2]["stock"], "300750,GD165627")
        self.assertEqual(query_call[2]["column"], "szse")
        self.assertEqual(query_call[2]["plate"], "sz")

    def test_cninfo_uses_shanghai_market_parameters(self):
        client = FakeClient({
            "https://www.cninfo.com.cn/new/data/szse_stock.json": {
                "stockList": [{
                    "code": "600036",
                    "orgId": "gssh0600036",
                }],
            },
            "https://www.cninfo.com.cn/new/hisAnnouncement/query": {
                "announcements": [{
                    "announcementTitle": "年度报告",
                    "announcementTime": 1760000000000,
                    "adjunctUrl": "doc.pdf",
                }],
            },
        })

        result = fetch_announcements("600036", client=client)

        self.assertTrue(result["ok"])
        query_call = [call for call in client.calls
                      if call[1].endswith("hisAnnouncement/query")][0]
        self.assertEqual(query_call[2]["stock"], "600036,gssh0600036")
        self.assertEqual(query_call[2]["column"], "sse")
        self.assertEqual(query_call[2]["plate"], "sh")

    def test_shenzhen_primary_failure_uses_szse_backup(self):
        client = FakeClient({
            "https://www.cninfo.com.cn/new/data/szse_stock.json": {
                "stockList": [{
                    "code": "300750",
                    "orgId": "GD165627",
                }],
            },
            "https://www.cninfo.com.cn/new/hisAnnouncement/query": ValueError("blocked"),
            "https://www.szse.cn/api/disc/announcement/annList": {
                "data": [{
                    "title": "公告",
                    "publishTime": "2026-07-16 15:00:00",
                    "attachPath": "/doc.pdf",
                }]
            },
        })

        result = fetch_announcements("300750", client=client)
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "szse")
        self.assertTrue(result["fallback_used"])
        self.assertEqual(result["data"][0]["title"], "公告")
        backup_call = [call for call in client.calls
                       if call[1].endswith("announcement/annList")][0]
        self.assertTrue(backup_call[4])
        self.assertEqual(backup_call[2]["stock"], ["300750"])

    def test_all_sources_failed_is_explicit(self):
        client = FakeClient({
            "https://www.cninfo.com.cn/new/data/szse_stock.json": {
                "stockList": [{
                    "code": "300750",
                    "orgId": "GD165627",
                }],
            },
            "https://www.cninfo.com.cn/new/hisAnnouncement/query": ValueError("blocked"),
            "https://www.szse.cn/api/disc/announcement/annList": ValueError("offline"),
        })

        result = fetch_announcements("300750", client=client)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "all_sources_failed")
        self.assertGreaterEqual(len(result["warnings"]), 2)

    def test_successful_result_includes_verification(self):
        client = FakeClient({
            "https://www.cninfo.com.cn/new/data/szse_stock.json": {
                "stockList": [{"code": "300750", "orgId": "GD165627"}],
            },
            "https://www.cninfo.com.cn/new/hisAnnouncement/query": {
                "announcements": [{
                    "announcementTitle": "年度报告",
                    "announcementTime": 1760000000000,
                    "adjunctUrl": "doc.pdf",
                }]
            },
        })
        fake_v = {"provider": "tushare", "configured": True, "status": "MATCH",
                   "as_of": None, "warnings": [], "fields": [], "endpoints": []}
        with mock.patch(
            "tools.ashare_plugin.disclosures.safe_verify_command",
            return_value=fake_v,
        ):
            result = fetch_announcements("300750", client=client)
        self.assertTrue(result["ok"])
        self.assertIn("verification", result)
        self.assertIs(result["verification"], fake_v)


if __name__ == "__main__":
    unittest.main()
