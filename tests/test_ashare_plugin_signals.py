import unittest

from tools.ashare_plugin.market_signals import (
    fetch_dragon_tiger,
    fetch_fund_flow,
    fetch_lockup,
    fetch_margin,
    fetch_signals,
)


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


class TestMarketSignals(unittest.TestCase):
    def test_fund_flow_normalizes_kline_units(self):
        client = FakeClient({
            "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get": {
                "data": {"klines": ["2026-07-16,1,2,3,4,5"]}
            }
        })
        result = fetch_fund_flow("600519", client=client)
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "eastmoney")
        self.assertEqual(result["data"][0]["date"], "2026-07-16")
        self.assertEqual(result["data"][0]["main_net"], 1.0)

    def test_dragon_tiger_falls_back_to_official_source(self):
        client = FakeClient({
            "https://datacenter.eastmoney.com/api/data/v1/get": RuntimeError("blocked"),
            "https://www.szse.cn/api/report/ShowReport/data": {
                "data": [{"zqdm": "300750", "zqjc": "宁德时代", "cjje": "100", "plyy": "涨幅偏离"}]
            },
        })
        result = fetch_dragon_tiger("300750", trade_date="2026-07-16", client=client)
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "szse")
        self.assertTrue(result["fallback_used"])
        self.assertEqual(result["data"][0]["code"], "300750")

    def test_lockup_and_margin_are_explicit_when_empty(self):
        client = FakeClient({
            "https://datacenter.eastmoney.com/api/data/v1/get": {
                "success": True,
                "result": {"data": []}
            }
        })
        self.assertEqual(fetch_lockup("600519", client=client)["error_type"], "empty_data")
        self.assertEqual(fetch_margin("600519", client=client)["error_type"], "empty_data")

    def test_datacenter_error_response_is_not_masked_as_empty(self):
        client = FakeClient({
            "https://datacenter.eastmoney.com/api/data/v1/get": {
                "success": False,
                "result": None,
                "message": "报表配置不存在,RPT_BAD",
                "code": 9501,
            }
        })
        for result in (fetch_lockup("600519", client=client), fetch_margin("600519", client=client)):
            self.assertFalse(result["ok"])
            self.assertEqual(result["error_type"], "http_error")
            self.assertIn("报表配置不存在", result["message"])

    def test_datacenter_no_record_code_means_empty_data(self):
        client = FakeClient({
            "https://datacenter.eastmoney.com/api/data/v1/get": {
                "success": False,
                "result": None,
                "message": "返回数据为空",
                "code": 9201,
            }
        })
        self.assertEqual(fetch_lockup("600519", client=client)["error_type"], "empty_data")
        self.assertEqual(fetch_margin("600519", client=client)["error_type"], "empty_data")

    def test_margin_queries_real_report_with_scode_filter(self):
        client = FakeClient({
            "https://datacenter.eastmoney.com/api/data/v1/get": {
                "success": True,
                "result": {"data": [{"SCODE": "600519", "DATE": "2026-07-15 00:00:00", "RZYE": 1}]}
            }
        })
        result = fetch_margin("600519", client=client)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"][0]["SCODE"], "600519")
        _, params, _ = client.calls[0]
        self.assertEqual(params["reportName"], "RPTA_WEB_RZRQ_GGMX")
        self.assertEqual(params["filter"], '(SCODE="600519")')
        self.assertEqual(params["sortColumns"], "DATE")

    def test_fetch_signals_returns_each_research_evidence_block(self):
        client = FakeClient({
            "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get": {
                "data": {"klines": ["2026-07-16,1,2,3,4,5"]}
            },
            "https://datacenter.eastmoney.com/api/data/v1/get": {
                "success": True,
                "result": {"data": [{"SECURITY_CODE": "600519"}]}
            },
        })
        result = fetch_signals("600519", client=client)
        self.assertTrue(result["ok"])
        self.assertIn("fund_flow", result["data"])
        self.assertIn("lockup", result["data"])
        self.assertIn("margin", result["data"])


if __name__ == "__main__":
    unittest.main()
