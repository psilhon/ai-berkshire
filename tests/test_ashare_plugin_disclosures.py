import unittest

from tools.ashare_plugin.disclosures import fetch_announcements


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append((url, params, headers))
        response = self.responses.get(url)
        if isinstance(response, Exception):
            raise response
        return response


class TestDisclosures(unittest.TestCase):
    def test_cninfo_rows_are_normalized_with_source(self):
        client = FakeClient({
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

    def test_shenzhen_primary_failure_uses_szse_backup(self):
        client = FakeClient({
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

    def test_all_sources_failed_is_explicit(self):
        client = FakeClient({
            "https://www.cninfo.com.cn/new/hisAnnouncement/query": ValueError("blocked"),
            "https://www.szse.cn/api/disc/announcement/annList": ValueError("offline"),
        })

        result = fetch_announcements("300750", client=client)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "all_sources_failed")
        self.assertGreaterEqual(len(result["warnings"]), 2)


if __name__ == "__main__":
    unittest.main()
