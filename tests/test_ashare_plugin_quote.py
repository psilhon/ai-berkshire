import unittest
from unittest import mock

from tools.ashare_plugin.quote import (
    fetch_quote,
    parse_sina_quote,
    parse_tencent_quote,
    price_cross_check,
)


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


def sina_raw(price="10.00"):
    # name,open,prev_close,price,high,low,bid,ask,volume,amount, ... ,date,time,status
    fields = [""] * 34
    fields[0] = "样本公司"
    fields[1] = "9.95"
    fields[2] = "9.90"
    fields[3] = price
    fields[4] = "10.10"
    fields[5] = "9.80"
    fields[8] = "1000"
    fields[9] = "10000.0"
    fields[30] = "2026-07-18"
    fields[31] = "15:00:00"
    fields[32] = "00"
    return 'var hq_str_sh600036="' + ",".join(fields) + '";'


class FakeClient:
    def __init__(self, sina_price="10.00"):
        self.text_calls = []
        self.json_calls = []
        self._sina_price = sina_price

    def get_text(self, url, params=None, headers=None):
        self.text_calls.append((url, params, headers))
        if "sinajs.cn" in url:
            return sina_raw(self._sina_price)
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
        self.assertEqual(result["source"], "tencent+sina+eastmoney")
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

    def test_successful_result_includes_verification(self):
        client = FakeClient()
        fake_v = {"provider": "tushare", "configured": True, "status": "MATCH",
                   "as_of": None, "warnings": [], "fields": [], "endpoints": []}
        with mock.patch(
            "tools.ashare_plugin.quote.safe_verify_command",
            return_value=fake_v,
        ):
            result = fetch_quote("600036", client=client)
        self.assertTrue(result["ok"])
        self.assertIn("verification", result)
        self.assertIs(result["verification"], fake_v)

    def test_failure_result_has_no_verification(self):
        class EmptyClient(FakeClient):
            def get_text(self, url, params=None, headers=None):
                return 'v_none="";'

        with mock.patch(
            "tools.ashare_plugin.quote.safe_verify_command",
            return_value={"status": "MATCH"},
        ) as mock_v:
            result = fetch_quote("600036", client=EmptyClient())
        self.assertFalse(result["ok"])
        mock_v.assert_not_called()


class TestSinaSecondSource(unittest.TestCase):
    def test_parse_sina_quote_returns_price_and_ohlc(self):
        result = parse_sina_quote(sina_raw("14.54"))
        self.assertEqual(result["price"], "14.54")
        self.assertEqual(result["open"], "9.95")
        self.assertEqual(result["prev_close"], "9.90")
        self.assertEqual(result["high"], "10.10")
        self.assertEqual(result["date"], "2026-07-18")

    def test_parse_sina_quote_malformed_returns_empty(self):
        self.assertEqual(parse_sina_quote('var hq_str_sh600036="";'), {})
        self.assertEqual(parse_sina_quote("garbage"), {})

    def test_price_cross_check_match_within_tolerance(self):
        cc = price_cross_check("10.00", "10.00")
        self.assertEqual(cc["status"], "MATCH")
        self.assertEqual(cc["second_source"], "sina")
        self.assertEqual(cc["second_price"], "10.00")
        self.assertEqual(cc["deviation_pct"], "0.00")

    def test_price_cross_check_conflict_beyond_tolerance(self):
        cc = price_cross_check("10.00", "11.00")
        self.assertEqual(cc["status"], "CONFLICT")

    def test_price_cross_check_unavailable_when_no_second_price(self):
        cc = price_cross_check("10.00", None)
        self.assertEqual(cc["status"], "UNAVAILABLE")
        self.assertIsNone(cc["second_price"])

    def test_fetch_quote_adds_dual_source_price_check(self):
        client = FakeClient(sina_price="10.00")
        result = fetch_quote("600036", client=client)
        self.assertTrue(result["ok"])
        cc = result["data"]["price_cross_check"]
        self.assertEqual(cc["second_source"], "sina")
        self.assertEqual(cc["status"], "MATCH")
        # sina endpoint was actually queried (independent second chain)
        self.assertTrue(
            any("sinajs.cn" in call[0] for call in client.text_calls)
        )

    def test_fetch_quote_sina_failure_is_graceful_unavailable(self):
        class TencentOnlyClient(FakeClient):
            def get_text(self, url, params=None, headers=None):
                self.text_calls.append((url, params, headers))
                if "sinajs.cn" in url:
                    raise RuntimeError("sina down")
                return quote_raw()

        result = fetch_quote("600036", client=TencentOnlyClient())
        self.assertTrue(result["ok"])  # sina failure never breaks the quote
        self.assertEqual(
            result["data"]["price_cross_check"]["status"], "UNAVAILABLE"
        )


if __name__ == "__main__":
    unittest.main()
