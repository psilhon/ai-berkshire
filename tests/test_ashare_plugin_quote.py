import unittest

from tools.ashare_plugin.quote import fetch_quote, parse_tencent_quote


def quote_raw():
    fields = [""] * 50
    fields[1] = "样本公司"
    fields[2] = "600036"
    fields[3] = "10.00"
    fields[4] = "9.90"
    fields[5] = "9.95"
    fields[6] = "100"
    fields[30] = "20260718150000"
    fields[31] = "0.10"
    fields[32] = "1.01"
    fields[33] = "10.10"
    fields[34] = "9.80"
    fields[37] = "1000"
    fields[38] = "1.20"
    fields[39] = "8.00"
    fields[44] = "80.00"
    fields[45] = "100.00"
    fields[46] = "1.10"
    return 'v_sh600036="' + "~".join(fields) + '";'


class FakeClient:
    def __init__(self):
        self.text_calls = []
        self.json_calls = []

    def get_text(self, url, params=None, headers=None):
        self.text_calls.append((url, params, headers))
        return quote_raw()

    def get_json(self, url, params=None, headers=None):
        self.json_calls.append((url, params, headers))
        return {"data": {"f174": 12.5, "f175": 8.5}}


class TestTencentQuote(unittest.TestCase):
    def test_parse_tencent_quote_returns_stable_fields(self):
        result = parse_tencent_quote(quote_raw())
        self.assertEqual(result["name"], "样本公司")
        self.assertEqual(result["code"], "600036")
        self.assertEqual(result["price"], "10.00")
        self.assertEqual(result["market_cap"], "100.00")
        self.assertEqual(result["quote_time"], "20260718150000")

    def test_fetch_quote_contains_source_and_52_week_range(self):
        client = FakeClient()
        result = fetch_quote("600036", client=client)
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "tencent+eastmoney")
        self.assertEqual(result["data"]["high_52w"], 12.5)
        self.assertEqual(result["data"]["low_52w"], 8.5)
        self.assertEqual(client.text_calls[0][0], "https://qt.gtimg.cn/q=sh600036")

    def test_malformed_quote_is_explicit_failure(self):
        class EmptyClient(FakeClient):
            def get_text(self, url, params=None, headers=None):
                return 'v_none="";'

        result = fetch_quote("600036", client=EmptyClient())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "parse_error")


if __name__ == "__main__":
    unittest.main()
