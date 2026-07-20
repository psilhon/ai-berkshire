import unittest

from tools.ashare_plugin.errors import InvalidCodeError
from tools.ashare_plugin.identifiers import normalize_code
from tools.ashare_plugin import failure_result


class TestCodeIdentity(unittest.TestCase):
    def test_normalize_code_infers_provider_formats(self):
        identity = normalize_code("600519")

        self.assertEqual(identity.code, "600519")
        self.assertEqual(identity.market, "SH")
        self.assertEqual(identity.secu_code, "600519.SH")
        self.assertEqual(identity.secid, "1.600519")
        self.assertEqual(identity.quote_code, "sh600519")

    def test_normalize_code_accepts_explicit_market(self):
        identity = normalize_code("000001.SZ")
        self.assertEqual(identity.market, "SZ")
        self.assertEqual(identity.quote_code, "sz000001")

    def test_normalize_code_routes_bare_920_to_beijing_exchange(self):
        identity = normalize_code("920185")
        self.assertEqual(identity.market, "BJ")
        self.assertEqual(identity.secu_code, "920185.BJ")
        self.assertEqual(identity.secid, "0.920185")
        self.assertEqual(identity.quote_code, "bj920185")

    def test_invalid_code_raises_typed_error(self):
        with self.assertRaises(InvalidCodeError):
            normalize_code("123")


class TestDataResult(unittest.TestCase):
    def test_failure_result_is_explicit_and_serializable(self):
        result = failure_result("eastmoney", "rate_limited", "blocked")

        self.assertFalse(result["ok"])
        self.assertIsNone(result["data"])
        self.assertEqual(result["source"], "eastmoney")
        self.assertEqual(result["error_type"], "rate_limited")
        self.assertEqual(result["message"], "blocked")
        self.assertIn("warnings", result)


if __name__ == "__main__":
    unittest.main()
