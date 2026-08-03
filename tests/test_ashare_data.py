#!/usr/bin/env python3
"""Unit tests for tools/ashare_data.py."""

import argparse
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ashare_data

TOOL = str(ROOT / "tools" / "ashare_data.py")


def run_cli(*args):
    return subprocess.run(
        [sys.executable, TOOL, *args],
        capture_output=True,
        text=True,
        timeout=10,
    )


def _quote_raw():
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


class OfflineAshareDataTestCase(unittest.TestCase):
    def setUp(self):
        environment = mock.patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)


class TestSecurityCode(OfflineAshareDataTestCase):
    def test_normalizes_shenzhen_shanghai_and_beijing(self):
        self.assertEqual(ashare_data._em_secu_code("600036"), "600036.SH")
        self.assertEqual(ashare_data._em_secu_code("000001.SZ"), "000001.SZ")
        self.assertEqual(ashare_data._em_secu_code("430047"), "430047.BJ")
        self.assertEqual(ashare_data._em_secu_code("920002"), "920002.BJ")
        self.assertEqual(ashare_data._em_secu_code("900901"), "900901.SH")

    def test_rejects_invalid_code(self):
        with self.assertRaises(ValueError):
            ashare_data._em_secu_code("ABC")
        with self.assertRaises(ValueError):
            ashare_data._em_secu_code("600036.HK")


class TestPositiveYears(OfflineAshareDataTestCase):
    def test_accepts_range_boundaries(self):
        self.assertEqual(ashare_data._positive_years("1"), 1)
        self.assertEqual(ashare_data._positive_years("50"), 50)

    def test_rejects_values_outside_range(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            ashare_data._positive_years("0")
        with self.assertRaises(argparse.ArgumentTypeError):
            ashare_data._positive_years("51")


class TestDatacenterPagination(OfflineAshareDataTestCase):
    @mock.patch.object(ashare_data, "_curl_json")
    def test_reads_all_pages_without_silent_truncation(self, curl_json):
        curl_json.side_effect = [
            {"success": True, "result": {"pages": 2, "data": [{"id": 1}]}},
            {"success": True, "result": {"pages": 2, "data": [{"id": 2}]}},
        ]

        rows = ashare_data._fetch_datacenter_rows(
            "REPORT", "600036.SH", sort_column="END_DATE"
        )

        self.assertEqual(rows, [{"id": 1}, {"id": 2}])
        self.assertEqual(curl_json.call_count, 2)
        self.assertEqual(curl_json.call_args_list[0].args[1]["p"], "1")
        self.assertEqual(curl_json.call_args_list[1].args[1]["p"], "2")

    @mock.patch.object(ashare_data, "_curl_json")
    def test_limit_stops_after_enough_rows(self, curl_json):
        curl_json.return_value = {
            "success": True,
            "result": {"pages": 3, "data": [{"id": 1}, {"id": 2}]},
        }

        rows = ashare_data._fetch_datacenter_rows(
            "REPORT", "600036.SH", sort_column="REPORT_DATE", limit=1
        )

        self.assertEqual(rows, [{"id": 1}])
        self.assertEqual(curl_json.call_count, 1)

    @mock.patch.object(ashare_data, "_curl_json")
    def test_api_failure_is_loud(self, curl_json):
        curl_json.return_value = {"success": False, "message": "bad field"}

        with self.assertRaisesRegex(ConnectionError, "bad field"):
            ashare_data._fetch_datacenter_rows(
                "REPORT", "600036.SH", sort_column="END_DATE"
            )


class TestHistoryCommand(OfflineAshareDataTestCase):
    @mock.patch.object(ashare_data, "_fetch_datacenter_rows")
    def test_outputs_auditable_metrics_without_total_share(self, fetch):
        fetch.return_value = [{
            "REPORT_YEAR": "2025",
            "SECURITY_NAME_ABBR": "样本公司",
            "ROEJQ": 12.3,
            "XSMLL": 45.6,
            "XSJLL": 18.9,
            "NCO_NETPROFIT": 1.2,
            "INTSTCOVRATE": 8.5,
            "NETCASH_OPERATE_PK": 1230000000,
            "TOTAL_SHARE": 999999999,
        }]

        with redirect_stdout(StringIO()) as output:
            ok = ashare_data.cmd_history("600036", 10)

        text = output.getvalue()
        self.assertTrue(ok)
        self.assertIn("2025", text)
        self.assertIn("ROE", text)
        self.assertIn("12.30%", text)
        self.assertIn("经营现金流", text)
        self.assertIn("12.30亿", text)
        self.assertNotIn("999999999", text)
        self.assertEqual(fetch.call_args.args[1], "600036.SH")
        self.assertEqual(fetch.call_args.kwargs["sort_column"], "REPORT_DATE")
        self.assertEqual(fetch.call_args.kwargs["limit"], 10)
        self.assertIn('(REPORT_TYPE="年报")',
                      fetch.call_args.kwargs["extra_filter"])

    @mock.patch.object(ashare_data, "_fetch_datacenter_rows", return_value=[])
    def test_no_annual_reports_returns_failure(self, _fetch):
        with redirect_stderr(StringIO()) as error:
            ok = ashare_data.cmd_history("600036", 10)

        self.assertFalse(ok)
        self.assertIn("年度财务数据", error.getvalue())

    def test_history_is_discoverable(self):
        proc = run_cli("--help")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("history", proc.stdout)

    def test_history_rejects_years_outside_range(self):
        proc = run_cli("history", "600036", "--years", "0")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--years 必须在 1 到 50 之间", proc.stderr)


class TestEquityHistoryCommand(OfflineAshareDataTestCase):
    @mock.patch.object(ashare_data, "_fetch_datacenter_rows")
    def test_outputs_date_shares_change_and_reason(self, fetch):
        fetch.return_value = [{
            "END_DATE": "2025-06-30 00:00:00",
            "SECURITY_NAME_ABBR": "样本公司",
            "TOTAL_SHARES": 1200000000,
            "TOTAL_SHARES_CHANGE": -10000000,
            "CHANGE_REASON": "股份回购",
        }]

        with redirect_stdout(StringIO()) as output:
            ok = ashare_data.cmd_equity_history("430047")

        text = output.getvalue()
        self.assertTrue(ok)
        self.assertIn("2025-06-30", text)
        self.assertIn("12.00亿", text)
        self.assertIn("-1000.00万", text)
        self.assertIn("股份回购", text)
        self.assertEqual(fetch.call_args.args[1], "430047.BJ")
        self.assertEqual(fetch.call_args.kwargs["sort_column"], "END_DATE")
        self.assertEqual(fetch.call_args.kwargs["sort_order"], "-1")
        self.assertIsNone(fetch.call_args.kwargs.get("limit"))

    @mock.patch.object(ashare_data, "_fetch_datacenter_rows", return_value=[])
    def test_no_equity_history_returns_failure(self, _fetch):
        with redirect_stderr(StringIO()) as error:
            ok = ashare_data.cmd_equity_history("600036")

        self.assertFalse(ok)
        self.assertIn("历史股本", error.getvalue())

    def test_equity_history_is_discoverable(self):
        proc = run_cli("--help")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("equity-history", proc.stdout)


class TestLegacyCommandExitSemantics(OfflineAshareDataTestCase):
    @mock.patch.object(ashare_data, "_curl", return_value='v_none="";')
    def test_quote_and_valuation_return_false_without_quote(self, _curl):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertIs(ashare_data.cmd_quote("INVALID"), False)
            self.assertIs(ashare_data.cmd_valuation("INVALID"), False)

    @mock.patch.object(ashare_data, "_curl_json")
    @mock.patch.object(ashare_data, "_curl", return_value='v_none="";')
    def test_financials_returns_false_without_reports(self, _curl, curl_json):
        curl_json.return_value = {"success": True, "result": {"data": []}}

        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertIs(ashare_data.cmd_financials("600036"), False)

        self.assertEqual(curl_json.call_count, 2)

    @mock.patch.object(ashare_data, "_curl_json")
    def test_search_returns_false_without_results(self, curl_json):
        curl_json.return_value = {"QuotationCodeTable": {"Data": []}}

        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertIs(ashare_data.cmd_search("不存在"), False)

    @mock.patch.object(ashare_data, "_fetch_52w", return_value=("12", "8"))
    @mock.patch.object(ashare_data, "_curl", return_value=_quote_raw())
    def test_quote_and_valuation_return_true_with_quote(self, _curl, _fetch):
        with redirect_stdout(StringIO()):
            self.assertIs(ashare_data.cmd_quote("600036"), True)
            self.assertIs(ashare_data.cmd_valuation("600036"), True)

    @mock.patch.object(ashare_data, "_curl_json")
    @mock.patch.object(ashare_data, "_curl", return_value='v_none="";')
    def test_financials_returns_true_with_report(self, _curl, curl_json):
        curl_json.return_value = {
            "success": True,
            "result": {"data": [{
                "REPORT_DATE": "2025-12-31",
                "REPORT_DATE_NAME": "2025年报",
                "TOTALOPERATEREVE": 100000000,
                "PARENTNETPROFIT": 10000000,
                "EPSJB": 1.0,
                "BPS": 5.0,
                "ROEJQ": 10.0,
            }]},
        }

        with redirect_stdout(StringIO()):
            self.assertIs(ashare_data.cmd_financials("600036"), True)

    @mock.patch.object(ashare_data, "_curl_json")
    def test_search_returns_true_with_results(self, curl_json):
        curl_json.return_value = {
            "QuotationCodeTable": {"Data": [{
                "Code": "600036",
                "Name": "招商银行",
                "MktNum": "1",
            }]},
        }

        with redirect_stdout(StringIO()):
            self.assertIs(ashare_data.cmd_search("招商银行"), True)

    @mock.patch.object(
        ashare_data, "_curl", side_effect=ConnectionError("offline")
    )
    def test_quote_and_valuation_request_errors_return_false(self, _curl):
        with redirect_stderr(StringIO()) as error:
            self.assertIs(ashare_data.cmd_quote("600036"), False)
            self.assertIs(ashare_data.cmd_valuation("600036"), False)

        self.assertIn("offline", error.getvalue())

    @mock.patch.object(
        ashare_data, "_curl_json", side_effect=ConnectionError("offline")
    )
    @mock.patch.object(
        ashare_data, "_curl", side_effect=ConnectionError("offline")
    )
    def test_financials_request_errors_return_false(self, _curl, _curl_json):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as error:
            self.assertIs(ashare_data.cmd_financials("600036"), False)

        self.assertIn("财务数据", error.getvalue())

    @mock.patch.object(
        ashare_data, "_curl_json", side_effect=ConnectionError("offline")
    )
    def test_search_request_error_returns_false(self, _curl_json):
        with redirect_stderr(StringIO()) as error:
            self.assertIs(ashare_data.cmd_search("招商银行"), False)

        self.assertIn("offline", error.getvalue())

    def test_main_maps_false_to_exit_one(self):
        with mock.patch.object(sys, "argv", [TOOL, "quote", "600036"]), \
                mock.patch.object(ashare_data, "cmd_quote", return_value=False):
            with self.assertRaises(SystemExit) as raised:
                ashare_data.main()

        self.assertEqual(raised.exception.code, 1)

    def test_main_keeps_success_at_zero(self):
        with mock.patch.object(sys, "argv", [TOOL, "quote", "600036"]), \
                mock.patch.object(ashare_data, "cmd_quote", return_value=True):
            ashare_data.main()


class TestBeijingExchangeRouting(OfflineAshareDataTestCase):
    def test_qq_code_covers_bj_segments(self):
        self.assertEqual(ashare_data._qq_code("430047"), "bj430047")
        self.assertEqual(ashare_data._qq_code("920002"), "bj920002")
        self.assertEqual(ashare_data._qq_code("900901"), "sh900901")

    def test_em_secid_covers_bj_segments(self):
        self.assertEqual(ashare_data._em_secid("430047"), "0.430047")
        self.assertEqual(ashare_data._em_secid("920002"), "0.920002")
        self.assertEqual(ashare_data._em_secid("900901"), "1.900901")

    @mock.patch.object(ashare_data, "_curl_json")
    @mock.patch.object(ashare_data, "_curl", return_value='v_none="";')
    def test_financials_queries_bj_secucode(self, _curl, curl_json):
        curl_json.return_value = {
            "success": True,
            "result": {"data": [{
                "REPORT_DATE": "2025-12-31",
                "REPORT_DATE_NAME": "2025年报",
                "TOTALOPERATEREVE": 100000000,
            }]},
        }

        with redirect_stdout(StringIO()):
            ok = ashare_data.cmd_financials("430047")

        self.assertTrue(ok)
        self.assertIn('(SECUCODE="430047.BJ")',
                      curl_json.call_args.args[1]["filter"])

    def test_financials_rejects_invalid_code_exit_two(self):
        proc = run_cli("financials", "600036.HK")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("参数错误", proc.stderr)


class TestPluginCommands(OfflineAshareDataTestCase):
    @mock.patch.object(ashare_data, "fetch_signals")
    def test_signals_prints_source_and_returns_success(self, fetch):
        fetch.return_value = {
            "ok": True,
            "source": "multiple",
            "fallback_used": False,
            "as_of": "2026-07-16T00:00:00+00:00",
            "warnings": [],
            "data": {"fund_flow": {"ok": True, "data": []}},
        }
        with redirect_stdout(StringIO()) as output:
            ok = ashare_data.cmd_signals("600036")
        self.assertTrue(ok)
        self.assertIn("multiple", output.getvalue())

    @mock.patch.object(ashare_data, "fetch_announcements")
    def test_announcements_failure_is_non_success(self, fetch):
        fetch.return_value = {
            "ok": False,
            "source": "cninfo",
            "error_type": "all_sources_failed",
            "message": "offline",
            "warnings": ["cninfo: offline"],
        }
        with redirect_stderr(StringIO()) as error:
            ok = ashare_data.cmd_announcements("600036", 20)
        self.assertFalse(ok)
        self.assertIn("offline", error.getvalue())

    def test_new_commands_are_discoverable(self):
        proc = run_cli("--help")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("signals", proc.stdout)
        self.assertIn("announcements", proc.stdout)


class TestTushareCliVerification(OfflineAshareDataTestCase):
    @mock.patch.object(ashare_data, "safe_verify_command")
    def test_valuation_conflict_prints_tushare_effective_value(self, verify):
        verify.return_value = {
            "provider": "tushare",
            "configured": True,
            "status": "CONFLICT",
            "as_of": None,
            "warnings": [],
            "endpoints": [],
            "fields": [{
                "field": "pb",
                "status": "CONFLICT",
                "primary_value": "0.87",
                "verification_value": "0.8468",
                "primary_source": "tencent",
                "verification_source": "tushare.daily_basic",
                "period": "20260717",
                "unit": "multiple",
                "deviation_pct": "2.67",
            }],
        }

        with mock.patch.object(ashare_data, "_curl", return_value=_quote_raw()), \
                mock.patch.object(ashare_data, "_fetch_52w", return_value=("12", "8")), \
                redirect_stdout(StringIO()) as output:
            ok = ashare_data.cmd_valuation("600036")

        self.assertTrue(ok)
        self.assertIn("PB:         0.8468", output.getvalue())
        self.assertIn("Tushare 覆盖: pb 0.87 -> 0.8468", output.getvalue())

    @mock.patch.object(ashare_data, "safe_verify_command")
    def test_successful_quote_prints_verification_without_changing_success(self, verify):
        verify.return_value = {
            "provider": "tushare",
            "configured": True,
            "status": "MATCH",
            "as_of": "2026-07-19T00:00:00+00:00",
            "warnings": [],
            "fields": [{"field": "pb", "status": "MATCH"}],
            "endpoints": [],
        }

        with mock.patch.object(ashare_data, "_curl", return_value=_quote_raw()), \
                mock.patch.object(ashare_data, "_fetch_52w", return_value=("12", "8")), \
                redirect_stdout(StringIO()) as output:
            ok = ashare_data.cmd_quote("600036")

        self.assertTrue(ok)
        self.assertIn("Tushare 验证: MATCH", output.getvalue())

    @mock.patch.object(
        ashare_data, "safe_verify_command", side_effect=RuntimeError("hidden")
    )
    def test_verification_exception_does_not_fail_primary(self, _verify):
        with mock.patch.object(ashare_data, "_curl", return_value=_quote_raw()), \
                mock.patch.object(ashare_data, "_fetch_52w", return_value=("12", "8")), \
                redirect_stdout(StringIO()) as output:
            ok = ashare_data.cmd_quote("600036")

        self.assertTrue(ok)
        self.assertIn("Tushare 验证: INSUFFICIENT", output.getvalue())
        self.assertNotIn("hidden", output.getvalue())

    @mock.patch.object(ashare_data, "safe_verify_command")
    def test_not_configured_is_explicit(self, verify):
        verify.return_value = {
            "provider": "tushare",
            "configured": False,
            "status": "NOT_CONFIGURED",
            "as_of": None,
            "warnings": ["未配置 TUSHARE_TOKEN；未发起 Tushare 请求"],
            "fields": [],
            "endpoints": [],
        }

        with mock.patch.object(ashare_data, "_curl", return_value=_quote_raw()), \
                mock.patch.object(ashare_data, "_fetch_52w", return_value=("12", "8")), \
                redirect_stdout(StringIO()) as output:
            ok = ashare_data.cmd_quote("600036")

        self.assertTrue(ok)
        self.assertIn("NOT_CONFIGURED", output.getvalue())


class _FakeTsClient:
    configured = True

    def __init__(self, rows):
        # rows: a list (same data for every api) OR a dict {api_name: rows}
        self._rows = rows
        self.calls = []

    def query(self, api_name, *, params=None, fields=()):
        self.calls.append((api_name, dict(params or {})))
        data = self._rows[api_name] if isinstance(self._rows, dict) else list(self._rows)
        return {"ok": True, "data": list(data), "source": f"tushare.{api_name}"}


class TestMarginCommand(unittest.TestCase):
    @mock.patch.object(ashare_data, "_get_tushare_client")
    def test_without_code_queries_market_summary(self, get_client):
        client = _FakeTsClient([])
        get_client.return_value = client

        with redirect_stdout(StringIO()) as output:
            ok = ashare_data.cmd_margin(None, "20260725")

        self.assertTrue(ok)
        self.assertIn("融资融券汇总", output.getvalue())
        self.assertEqual(client.calls, [("margin", {"trade_date": "20260725"})])


class TestManagersCommand(unittest.TestCase):
    @mock.patch.object(ashare_data, "_get_tushare_client")
    def test_shows_current_manager_bios(self, get_client):
        get_client.return_value = _FakeTsClient([
            {"name": "万敏", "title": "董事长", "gender": "M",
             "birthday": "1965", "edu": "硕士", "national": "中国",
             "begin_date": "20260701", "end_date": None, "resume": "曾任..."},
            {"name": "旧董事", "title": "董事", "gender": "M",
             "birthday": "1960", "edu": "本科", "national": "中国",
             "begin_date": "20200101", "end_date": "20260630", "resume": "已离任"},
        ])
        with redirect_stdout(StringIO()) as out:
            ok = ashare_data.cmd_managers("601919")
        text = out.getvalue()
        self.assertTrue(ok)
        self.assertIn("万敏", text)
        self.assertIn("董事长", text)
        self.assertIn("stk_managers", text)
        self.assertIn("1965", text)  # 出生年/履历补齐

    @mock.patch.object(ashare_data, "_get_tushare_client", return_value=None)
    def test_requires_token(self, _gc):
        self.assertFalse(ashare_data.cmd_managers("601919"))

    def test_discoverable(self):
        proc = run_cli("--help")
        self.assertIn("managers", proc.stdout)


class TestMainbzCommand(unittest.TestCase):
    @mock.patch.object(ashare_data, "_get_tushare_client")
    def test_shows_product_and_region_segments(self, get_client):
        client = _FakeTsClient([
            {"end_date": "20251231", "bz_item": "集装箱航运业务",
             "bz_sales": 210731000000, "bz_profit": 40964000000,
             "bz_cost": None, "curr_type": "CNY"},
            {"end_date": "20251231", "bz_item": "码头业务",
             "bz_sales": 12041000000, "bz_profit": 3120000000,
             "bz_cost": None, "curr_type": "CNY"},
        ])
        get_client.return_value = client
        with redirect_stdout(StringIO()) as out:
            ok = ashare_data.cmd_mainbz("601919")
        text = out.getvalue()
        self.assertTrue(ok)
        self.assertIn("集装箱航运业务", text)
        self.assertIn("fina_mainbz", text)
        types = [c[1].get("type") for c in client.calls]
        self.assertIn("P", types)  # 分产品
        self.assertIn("D", types)  # 分地区

    @mock.patch.object(ashare_data, "_get_tushare_client", return_value=None)
    def test_requires_token(self, _gc):
        self.assertFalse(ashare_data.cmd_mainbz("601919"))

    def test_discoverable(self):
        proc = run_cli("--help")
        self.assertIn("mainbz", proc.stdout)


class TestRepurchaseCommand(unittest.TestCase):
    @mock.patch.object(ashare_data, "_get_tushare_client")
    def test_shows_buyback_events(self, get_client):
        get_client.return_value = _FakeTsClient([
            {"ann_date": "20260707", "end_date": "20261006", "proc": "实施",
             "vol": 3000000, "amount": 41361744, "high_limit": 15.40,
             "low_limit": None},
            {"ann_date": "20260706", "end_date": "20261006", "proc": "董事会预案",
             "vol": 100000000, "amount": 1540000000, "high_limit": 15.40,
             "low_limit": None},
        ])
        with redirect_stdout(StringIO()) as out:
            ok = ashare_data.cmd_repurchase("601919")
        text = out.getvalue()
        self.assertTrue(ok)
        self.assertIn("2026-07-07", text)
        self.assertIn("实施", text)
        self.assertIn("repurchase", text)

    @mock.patch.object(ashare_data, "_get_tushare_client", return_value=None)
    def test_requires_token(self, _gc):
        self.assertFalse(ashare_data.cmd_repurchase("601919"))

    def test_discoverable(self):
        proc = run_cli("--help")
        self.assertIn("repurchase", proc.stdout)


class TestPledgeCommand(unittest.TestCase):
    @mock.patch.object(ashare_data, "_get_tushare_client")
    def test_shows_pledge_ratio_trend(self, get_client):
        get_client.return_value = _FakeTsClient([
            {"end_date": "20260630", "pledge_count": 0, "pledge_ratio": 0.0,
             "rest_pledge": 0, "unrest_pledge": 0, "total_share": 15268.12},
            {"end_date": "20251231", "pledge_count": 2, "pledge_ratio": 1.5,
             "rest_pledge": 100, "unrest_pledge": 50, "total_share": 15489.88},
        ])
        with redirect_stdout(StringIO()) as out:
            ok = ashare_data.cmd_pledge("601919")
        text = out.getvalue()
        self.assertTrue(ok)
        self.assertIn("2026-06-30", text)
        self.assertIn("pledge_stat", text)
        self.assertIn("质押", text)

    @mock.patch.object(ashare_data, "_get_tushare_client", return_value=None)
    def test_requires_token(self, _gc):
        self.assertFalse(ashare_data.cmd_pledge("601919"))

    def test_discoverable(self):
        proc = run_cli("--help")
        self.assertIn("pledge", proc.stdout)


class TestExpressCommand(unittest.TestCase):
    @mock.patch.object(ashare_data, "_get_tushare_client")
    def test_shows_earnings_flash(self, get_client):
        get_client.return_value = _FakeTsClient([
            {"ann_date": "20220311", "end_date": "20211231",
             "revenue": 333694000000.0, "n_income": 89296000000.0,
             "diluted_eps": 5.59, "diluted_roe": 101.15,
             "yoy_net_profit": 9927000000.0, "yoy_sales": 94.85,
             "bps": 8.31, "perf_summary": "运价大涨"},
        ])
        with redirect_stdout(StringIO()) as out:
            ok = ashare_data.cmd_express("601919")
        text = out.getvalue()
        self.assertTrue(ok)
        self.assertIn("2021-12-31", text)
        self.assertIn("express", text)

    @mock.patch.object(ashare_data, "_get_tushare_client", return_value=None)
    def test_requires_token(self, _gc):
        self.assertFalse(ashare_data.cmd_express("601919"))

    def test_discoverable(self):
        proc = run_cli("--help")
        self.assertIn("express", proc.stdout)


class TestKlineCommand(unittest.TestCase):
    @mock.patch.object(ashare_data, "_get_tushare_client")
    def test_computes_forward_adjusted_series(self, get_client):
        get_client.return_value = _FakeTsClient({
            "daily": [
                {"trade_date": "20260101", "open": 9, "high": 11, "low": 8,
                 "close": 10, "pct_chg": 1.0},
                {"trade_date": "20260717", "open": 19, "high": 21, "low": 18,
                 "close": 20, "pct_chg": 2.0},
            ],
            "adj_factor": [
                {"trade_date": "20260101", "adj_factor": 1.0},
                {"trade_date": "20260717", "adj_factor": 2.0},
            ],
        })
        with redirect_stdout(StringIO()) as out:
            ok = ashare_data.cmd_kline("601919")
        text = out.getvalue()
        self.assertTrue(ok)
        # 前复权: 20260101 close 10 * 1.0/2.0 = 5.00 ; 20260717 stays 20.00
        self.assertIn("5.00", text)
        self.assertIn("20.00", text)
        self.assertIn("kline", text)  # 数据来源标注

    @mock.patch.object(ashare_data, "_get_tushare_client", return_value=None)
    def test_requires_token(self, _gc):
        self.assertFalse(ashare_data.cmd_kline("601919"))

    def test_discoverable(self):
        proc = run_cli("--help")
        self.assertIn("kline", proc.stdout)


class TestAuditCommand(unittest.TestCase):
    @mock.patch.object(ashare_data, "_get_tushare_client")
    def test_shows_audit_opinion(self, get_client):
        get_client.return_value = _FakeTsClient([
            {"end_date": "20251231", "ann_date": "20260320",
             "audit_result": "标准无保留意见",
             "audit_agency": "信永中和会计师事务所", "audit_fees": 11587000.0},
        ])
        with redirect_stdout(StringIO()) as out:
            ok = ashare_data.cmd_audit("601919")
        text = out.getvalue()
        self.assertTrue(ok)
        self.assertIn("标准无保留意见", text)
        self.assertIn("fina_audit", text)

    @mock.patch.object(ashare_data, "_get_tushare_client")
    def test_flags_non_standard_opinion(self, get_client):
        get_client.return_value = _FakeTsClient([
            {"end_date": "20251231", "ann_date": "20260320",
             "audit_result": "保留意见", "audit_agency": "某所"},
        ])
        with redirect_stdout(StringIO()) as out:
            ashare_data.cmd_audit("601919")
        self.assertIn("⚠️", out.getvalue())  # 非标意见须告警

    @mock.patch.object(ashare_data, "_get_tushare_client", return_value=None)
    def test_requires_token(self, _gc):
        self.assertFalse(ashare_data.cmd_audit("601919"))

    def test_discoverable(self):
        self.assertIn("audit", run_cli("--help").stdout)


class TestHolderNumCommand(unittest.TestCase):
    @mock.patch.object(ashare_data, "_get_tushare_client")
    def test_shows_holder_count_trend(self, get_client):
        get_client.return_value = _FakeTsClient([
            {"end_date": "20260331", "ann_date": "20260430", "holder_num": 397399},
            {"end_date": "20251231", "ann_date": "20260320", "holder_num": 410000},
        ])
        with redirect_stdout(StringIO()) as out:
            ok = ashare_data.cmd_holder_num("601919")
        text = out.getvalue()
        self.assertTrue(ok)
        self.assertIn("397,399", text)  # 千分位格式
        self.assertIn("stk_holdernumber", text)

    @mock.patch.object(ashare_data, "_get_tushare_client", return_value=None)
    def test_requires_token(self, _gc):
        self.assertFalse(ashare_data.cmd_holder_num("601919"))

    def test_discoverable(self):
        self.assertIn("holder-num", run_cli("--help").stdout)


class TestRatiosCommand(unittest.TestCase):
    @mock.patch.object(ashare_data, "_get_tushare_client")
    def test_shows_ratio_series(self, get_client):
        get_client.return_value = _FakeTsClient([
            {"end_date": "20251231", "update_flag": "1", "roe": 13.22,
             "roe_dt": 13.16, "roa": 8.08, "roic": 9.23,
             "grossprofit_margin": 20.05, "netprofit_margin": 16.05,
             "debt_to_assets": 41.42, "current_ratio": 1.51,
             "quick_ratio": 1.45, "ocf_to_or": 0.21, "bps": 14.99, "eps": 1.99},
            {"end_date": "20241231", "update_flag": "1", "roe": 22.60,
             "roa": 12.0, "grossprofit_margin": 25.0},
        ])
        with redirect_stdout(StringIO()) as out:
            ok = ashare_data.cmd_ratios("601919")
        text = out.getvalue()
        self.assertTrue(ok)
        self.assertIn("2025-12-31", text)
        self.assertIn("13.22", text)  # ROE
        self.assertIn("fina_indicator", text)

    @mock.patch.object(ashare_data, "_get_tushare_client", return_value=None)
    def test_requires_token(self, _gc):
        self.assertFalse(ashare_data.cmd_ratios("601919"))

    def test_discoverable(self):
        self.assertIn("ratios", run_cli("--help").stdout)


class TestPeersCommand(unittest.TestCase):
    @mock.patch.object(ashare_data, "_get_tushare_client")
    def test_lists_industry_member_pool(self, get_client):
        members = [
            {"l1_name": "交通运输", "l2_name": "航运港口", "l3_name": "航运",
             "l1_code": "801170.SI", "l2_code": "801992.SI", "l3_code": "851761.SI",
             "ts_code": "601919.SH", "name": "中远海控"},
            {"l1_name": "交通运输", "l2_name": "航运港口", "l3_name": "航运",
             "l1_code": "801170.SI", "l2_code": "801992.SI", "l3_code": "851761.SI",
             "ts_code": "601872.SH", "name": "招商轮船"},
            {"l1_name": "交通运输", "l2_name": "航运港口", "l3_name": "航运",
             "l1_code": "801170.SI", "l2_code": "801992.SI", "l3_code": "851761.SI",
             "ts_code": "600026.SH", "name": "中远海能"},
        ]
        client = _FakeTsClient(members)
        get_client.return_value = client
        with redirect_stdout(StringIO()) as out:
            ok = ashare_data.cmd_peers("601919")
        text = out.getvalue()
        self.assertTrue(ok)
        self.assertIn("航运", text)           # 申万行业归属
        self.assertIn("招商轮船", text)         # 候选池成员
        self.assertIn("index_member_all", text)
        # target 自身应被标出
        self.assertIn("中远海控", text)
        # 两次调用：反查行业 + 查成员
        self.assertGreaterEqual(
            sum(1 for c in client.calls if c[0] == "index_member_all"), 2
        )

    @mock.patch.object(ashare_data, "_get_tushare_client")
    def test_level_l2_uses_l2_code(self, get_client):
        members = [{"l1_name": "交通运输", "l2_name": "航运港口", "l3_name": "航运",
                    "l1_code": "801170.SI", "l2_code": "801992.SI", "l3_code": "851761.SI",
                    "ts_code": "601919.SH", "name": "中远海控"}]
        client = _FakeTsClient(members)
        get_client.return_value = client
        with redirect_stdout(StringIO()):
            ashare_data.cmd_peers("601919", level="l2")
        # 成员查询用了 l2_code 参数
        member_calls = [c for c in client.calls if "l2_code" in c[1]]
        self.assertTrue(member_calls)

    @mock.patch.object(ashare_data, "_get_tushare_client", return_value=None)
    def test_requires_token(self, _gc):
        self.assertFalse(ashare_data.cmd_peers("601919"))

    def test_discoverable(self):
        self.assertIn("peers", run_cli("--help").stdout)


class TestNorthHoldCommand(unittest.TestCase):
    @mock.patch.object(ashare_data, "_get_tushare_client")
    def test_shows_northbound_holding_trend(self, get_client):
        get_client.return_value = _FakeTsClient([
            {"trade_date": "20260630", "ts_code": "601919.SH",
             "name": "中远海控", "vol": 268178679, "ratio": 2.13},
            {"trade_date": "20260531", "ts_code": "601919.SH",
             "name": "中远海控", "vol": 250000000, "ratio": 2.00},
        ])
        with redirect_stdout(StringIO()) as out:
            ok = ashare_data.cmd_north_hold("601919")
        text = out.getvalue()
        self.assertTrue(ok)
        self.assertIn("2.13", text)      # 北向持股占比
        self.assertIn("hk_hold", text)

    @mock.patch.object(ashare_data, "_get_tushare_client", return_value=None)
    def test_requires_token(self, _gc):
        self.assertFalse(ashare_data.cmd_north_hold("601919"))

    def test_discoverable(self):
        self.assertIn("north-hold", run_cli("--help").stdout)


class TestIndexValCommand(unittest.TestCase):
    @mock.patch.object(ashare_data, "_get_tushare_client")
    def test_shows_market_valuation_percentile(self, get_client):
        rows = [
            {"trade_date": "20200101", "pe": 12.0, "pe_ttm": 11.5, "pb": 1.2},
            {"trade_date": "20260717", "pe": 14.21, "pe_ttm": 13.98, "pb": 1.42},
        ]
        client = _FakeTsClient(rows)
        get_client.return_value = client
        with redirect_stdout(StringIO()) as out:
            ok = ashare_data.cmd_index_val("hs300")
        text = out.getvalue()
        self.assertTrue(ok)
        self.assertIn("13.98", text)   # 当前 PE(TTM)
        self.assertIn("分位", text)
        # 别名 hs300 -> 000300.SH
        codes = [c[1].get("ts_code") for c in client.calls]
        self.assertIn("000300.SH", codes)

    @mock.patch.object(ashare_data, "_get_tushare_client", return_value=None)
    def test_requires_token(self, _gc):
        self.assertFalse(ashare_data.cmd_index_val("hs300"))

    def test_discoverable(self):
        self.assertIn("index-val", run_cli("--help").stdout)


class TestRunLevelCommand(OfflineAshareDataTestCase):
    """run-level：取数级别串跑（standalone 快查专用）。

    守两条架构底线（见 docs/ashare-data-tiered-upgrade-plan.md ADR-001/003）：
    不提供 core 级别；逐条执行逐条呈现，不聚合成统一报告。
    """

    def _patch_runners(self, quote=True, valuation=True, financials=True):
        """把三条已就位命令替换为可观测桩，返回调用序列。"""
        calls = []

        def make(name, outcome):
            def runner(code):
                calls.append((name, code))
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            return runner

        for name, outcome in (("cmd_quote", quote),
                              ("cmd_valuation", valuation),
                              ("cmd_financials", financials)):
            patcher = mock.patch.object(
                ashare_data, name, make(name.replace("cmd_", ""), outcome)
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        return calls

    # --- 架构底线：core 不可用 -------------------------------------------

    def test_core_level_is_rejected_with_guidance(self):
        with self.assertRaises(ValueError) as ctx:
            ashare_data.cmd_run_level("600519", "core")
        message = str(ctx.exception)
        self.assertIn("不提供 --level core", message)
        self.assertIn("run-ashare-command", message)
        self.assertIn("feeds", message)

    def test_core_level_rejected_case_insensitively(self):
        with self.assertRaises(ValueError):
            ashare_data.cmd_run_level("600519", " CORE ")

    def test_level_registry_never_contains_core(self):
        self.assertNotIn("core", ashare_data.LEVEL_COMMANDS)
        self.assertEqual(
            set(ashare_data.LEVEL_COMMANDS),
            set(ashare_data.LEVEL_PENDING_LAYERS),
        )

    def test_unknown_level_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            ashare_data.cmd_run_level("600519", "deep")
        self.assertIn("未知取数级别", str(ctx.exception))

    # --- L0 静态清单 ------------------------------------------------------

    def test_quick_level_is_the_overview_triplet(self):
        self.assertEqual(
            ashare_data.LEVEL_COMMANDS["quick"],
            ("quote", "valuation", "financials"),
        )

    def test_quick_runs_each_command_once_in_order(self):
        calls = self._patch_runners()
        with redirect_stdout(StringIO()) as out:
            result = ashare_data.cmd_run_level("600519", "quick")
        self.assertIs(result, True)
        self.assertEqual(
            calls,
            [("quote", "600519"), ("valuation", "600519"),
             ("financials", "600519")],
        )
        text = out.getvalue()
        for name in ("quote", "valuation", "financials"):
            self.assertIn(f"✅ {name}", text)

    def test_standalone_boundary_is_stated_in_output(self):
        self._patch_runners()
        with redirect_stdout(StringIO()) as out:
            ashare_data.cmd_run_level("600519", "quick")
        text = out.getvalue()
        self.assertIn("不用于 full-company-analysis-workbuddy 主管线", text)
        self.assertIn("run-ashare-command", text)

    # --- 逐条呈现，不聚合 --------------------------------------------------

    def test_failure_is_reported_per_command_without_aborting(self):
        calls = self._patch_runners(valuation=False)
        with redirect_stdout(StringIO()) as out:
            result = ashare_data.cmd_run_level("600519", "quick")
        self.assertIs(result, False)
        # 中间一条失败不得中断后续命令
        self.assertEqual([name for name, _ in calls],
                         ["quote", "valuation", "financials"])
        text = out.getvalue()
        self.assertIn("✅ quote", text)
        self.assertIn("❌ valuation", text)
        self.assertIn("✅ financials", text)
        self.assertIn("数据不足", text)

    def test_command_exception_does_not_abort_remaining(self):
        calls = self._patch_runners(quote=ConnectionError("offline"))
        with redirect_stdout(StringIO()) as out, redirect_stderr(StringIO()):
            result = ashare_data.cmd_run_level("600519", "quick")
        self.assertIs(result, False)
        self.assertEqual([name for name, _ in calls],
                         ["quote", "valuation", "financials"])
        self.assertIn("❌ quote", out.getvalue())

    # --- 候选层如实告知 ----------------------------------------------------

    def test_enhanced_declares_no_pending_layers_after_l2_complete(self):
        # L2 热度层已就位（ths-hot 交付），enhanced 无待建候选层
        self._patch_runners()
        with redirect_stdout(StringIO()) as out:
            self.assertIs(ashare_data.cmd_run_level("600519", "enhanced"), True)
        text = out.getvalue()
        self.assertNotIn("尚未就位的候选层", text)
        self.assertNotIn("L2 候选", text)

    def test_full_declares_no_pending_layers_after_l3_complete(self):
        # 打板三件套 + 热度层(L2) + 互动易/财联社/研报(L3) 全部交付为独立子命令，
        # LEVEL_PENDING_LAYERS 三级均清空；run-level 仍仅跑 L1 快查不代跑 L2/L3
        self._patch_runners()
        with redirect_stdout(StringIO()) as out:
            ashare_data.cmd_run_level("600519", "full")
        text = out.getvalue()
        self.assertNotIn("尚未就位的候选层", text)
        self.assertNotIn("L3 候选", text)
        self.assertNotIn("L2 候选", text)

    def test_quick_has_no_pending_layer_notice(self):
        self._patch_runners()
        with redirect_stdout(StringIO()) as out:
            ashare_data.cmd_run_level("600519", "quick")
        self.assertNotIn("尚未就位的候选层", out.getvalue())

    # --- 跨级输入归一化（search 定码）--------------------------------------

    def test_company_name_resolves_via_single_search_hit(self):
        calls = self._patch_runners()
        with mock.patch.object(
            ashare_data, "_search_candidates",
            return_value=[{"Code": "600036", "Name": "招商银行",
                           "MktNum": "1"}],
        ):
            with redirect_stdout(StringIO()):
                self.assertIs(
                    ashare_data.cmd_run_level("招商银行", "quick"), True
                )
        self.assertEqual([code for _, code in calls],
                         ["600036", "600036", "600036"])

    def test_multiple_search_hits_refuse_to_pick(self):
        calls = self._patch_runners()
        with mock.patch.object(
            ashare_data, "_search_candidates",
            return_value=[{"Code": "600036", "Name": "招商银行",
                           "MktNum": "1"},
                          {"Code": "001227", "Name": "招商积余",
                           "MktNum": "2"}],
        ):
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as err:
                self.assertIs(
                    ashare_data.cmd_run_level("招商", "quick"), False
                )
        self.assertEqual(calls, [])  # 未代为选择，任何取数命令都不执行
        self.assertIn("请指定六位代码", err.getvalue())

    def test_unresolvable_name_returns_false(self):
        calls = self._patch_runners()
        with mock.patch.object(
            ashare_data, "_search_candidates", return_value=[]
        ):
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertIs(
                    ashare_data.cmd_run_level("不存在的公司", "quick"), False
                )
        self.assertEqual(calls, [])

    # --- CLI 集成 ----------------------------------------------------------

    def test_discoverable(self):
        self.assertIn("run-level", run_cli("--help").stdout)

    def test_cli_core_level_exits_two(self):
        completed = run_cli("run-level", "600519", "--level", "core")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("不提供 --level core", completed.stderr)


# ---------------------------------------------------------------------------
# 打板三件套（limit-pool / monitor-pool / anomaly-pool）— 需求拉动闭环单测
# 全市场级（--date 非 --code）；mock 点统一为模块级 _curl_json。
# ---------------------------------------------------------------------------


class TestLimitPoolCommand(OfflineAshareDataTestCase):
    """limit-pool：东财 push2ex 涨停/炸板/跌停/昨涨停四维生态。"""

    @mock.patch.object(ashare_data, "_curl_json")
    def test_returns_true_when_any_block_has_rows(self, curl_json):
        # 四维各调 1 次；首个（涨停）给 1 条即 total>0
        curl_json.side_effect = [
            {"data": {"pool": [{"c": "600519", "n": "贵州茅台", "lbc": 2,
                                 "fund": 1e8, "zbc": 0, "hybk": "白酒",
                                 "fbt": 92500}]}},
            {"data": {"pool": []}},
            {"data": {"pool": []}},
            {"data": {"pool": []}},
        ]
        with redirect_stdout(StringIO()):
            result = ashare_data.cmd_limit_pool("20260731")
        self.assertIs(result, True)
        self.assertEqual(curl_json.call_count, 4)

    @mock.patch.object(ashare_data, "_curl_json")
    def test_returns_false_when_all_blocks_empty(self, curl_json):
        curl_json.side_effect = [{"data": {"pool": []}}] * 4
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            result = ashare_data.cmd_limit_pool("20260731")
        self.assertIs(result, False)

    @mock.patch.object(ashare_data, "_curl_json")
    def test_returns_false_on_request_error(self, curl_json):
        # TransportError 被 _zt_pool 捕获 → 各维返回 [] → 合计 0
        curl_json.side_effect = ashare_data.TransportError("offline")
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            result = ashare_data.cmd_limit_pool("20260731")
        self.assertIs(result, False)

    @mock.patch.object(ashare_data, "_curl_json")
    def test_passes_trade_date_and_referer_to_api(self, curl_json):
        curl_json.side_effect = [{"data": {"pool": []}}] * 4
        with redirect_stdout(StringIO()):
            ashare_data.cmd_limit_pool("20260731")
        first = curl_json.call_args_list[0]
        params = first.kwargs["params"]
        self.assertEqual(params["date"], "20260731")
        self.assertIn("Referer", first.kwargs["headers"])


class TestMonitorPoolCommand(OfflineAshareDataTestCase):
    """monitor-pool：东财 mobappconfig 重点监控名单 + 生效窗口。"""

    def _rows(self):
        return [
            {"STKCODE": "501018", "STKNAME": "南方原油", "MARKET": "1",
             "VALIDATESTARTDATE": "2020-01-01", "VALIDATEENDDATE": "2099-12-31"},
            {"STKCODE": "920002", "STKNAME": "测试BJ", "MARKET": "B",
             "VALIDATESTARTDATE": "2020-01-01", "VALIDATEENDDATE": "2099-12-31"},
            {"STKCODE": "600000", "STKNAME": "已过窗口", "MARKET": "1",
             "VALIDATESTARTDATE": "2000-01-01", "VALIDATEENDDATE": "2020-01-01"},
        ]

    @mock.patch.object(ashare_data, "_curl_json")
    def test_maps_market_and_filters_window(self, curl_json):
        curl_json.return_value = self._rows()
        with redirect_stdout(StringIO()) as out:
            result = ashare_data.cmd_monitor_pool("20260731")
        text = out.getvalue()
        self.assertIs(result, True)
        self.assertIn("501018", text)
        self.assertIn("(SH)", text)
        self.assertIn("920002", text)
        self.assertIn("(BJ)", text)        # 北交所 MARKET="B" 三值原样保留
        self.assertNotIn("已过窗口", text)  # 超出窗口被过滤

    @mock.patch.object(ashare_data, "_curl_json")
    def test_returns_true_when_no_active_entries(self, curl_json):
        curl_json.return_value = [{"STKCODE": "600000", "STKNAME": "X",
                                    "MARKET": "1", "VALIDATESTARTDATE": "2000-01-01",
                                    "VALIDATEENDDATE": "2020-01-01"}]
        with redirect_stdout(StringIO()):
            result = ashare_data.cmd_monitor_pool("20260731")
        self.assertIs(result, True)

    @mock.patch.object(ashare_data, "_curl_json")
    def test_returns_false_on_request_error(self, curl_json):
        curl_json.side_effect = ashare_data.TransportError("offline")
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            result = ashare_data.cmd_monitor_pool("20260731")
        self.assertIs(result, False)


class TestAnomalyPoolCommand(OfflineAshareDataTestCase):
    """anomaly-pool：东财 dycalchis 严重异常波动明细。"""

    @mock.patch.object(ashare_data, "_curl_json")
    def test_returns_true_with_parsed_items(self, curl_json):
        curl_json.return_value = {
            "result": 0, "date": "20260731",
            "data": [{"c": "300688", "n": "创业黑马", "m": 0, "s": 6,
                      "e": 7, "a": 50.0, "x": 70.11, "d": 9, "o": 1}],
        }
        with redirect_stdout(StringIO()) as out:
            result = ashare_data.cmd_anomaly_pool("20260731")
        text = out.getvalue()
        self.assertIs(result, True)
        self.assertIn("300688", text)
        self.assertIn("(SZ)", text)
        self.assertIn("70.11", text)
        # team=h5 固定参数须随请求发出，否则 unknow team
        self.assertEqual(curl_json.call_args.kwargs["params"]["team"], "h5")

    @mock.patch.object(ashare_data, "_curl_json")
    def test_returns_false_when_result_not_zero(self, curl_json):
        curl_json.return_value = {"result": 1, "msg": "unknow team"}
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            result = ashare_data.cmd_anomaly_pool("20260731")
        self.assertIs(result, False)

    @mock.patch.object(ashare_data, "_curl_json")
    def test_returns_false_on_request_error(self, curl_json):
        curl_json.side_effect = ashare_data.TransportError("offline")
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            result = ashare_data.cmd_anomaly_pool("20260731")
        self.assertIs(result, False)

    @mock.patch.object(ashare_data, "_curl_json")
    def test_rule_code_multiplied_by_ten_when_s_is_six(self, curl_json):
        # s==6 且 e∈{4,5,6,7} → 规则码 e*10；其余保持 e
        curl_json.return_value = {
            "result": 0, "date": "20260731",
            "data": [
                {"c": "300001", "n": "A", "m": 0, "s": 6, "e": 4,
                 "a": 1, "x": 1, "d": 1, "o": 1},
                {"c": "300002", "n": "B", "m": 0, "s": 1, "e": 1,
                 "a": 1, "x": 1, "d": 1, "o": 1},
            ],
        }
        with redirect_stdout(StringIO()) as out:
            ashare_data.cmd_anomaly_pool("20260731")
        text = out.getvalue()
        self.assertIn("连续十个交易日内日收盘价涨跌幅偏离值累计+150%", text)  # 规则码 40
        self.assertIn("主板连续10个交易日内4次同向异常波动", text)          # 规则码 1


class TestAnomalyMarketHelper(OfflineAshareDataTestCase):
    """_anomaly_market / _fmt_zt_time：北交所号段优先 + 时间格式化。"""

    def test_beijing_by_920_prefix(self):
        self.assertEqual(ashare_data._anomaly_market("920002", 0), "BJ")

    def test_beijing_by_old_prefix(self):
        for code in ("430047", "830799", "870001"):
            self.assertEqual(ashare_data._anomaly_market(code, 0), "BJ", code)

    def test_shanghai_when_m_is_one(self):
        self.assertEqual(ashare_data._anomaly_market("600519", 1), "SH")

    def test_shenzhen_when_m_is_zero(self):
        self.assertEqual(ashare_data._anomaly_market("000001", 0), "SZ")

    def test_300_code_with_board_six_stays_shenzhen(self):
        # 北交所与深市同为 m=0；按代码号段优先，300 不是 BJ 号段 → SZ
        self.assertEqual(ashare_data._anomaly_market("300688", 0, 6), "SZ")

    def test_board_eight_forces_beijing(self):
        self.assertEqual(ashare_data._anomaly_market("830799", 0, 8), "BJ")

    def test_fmt_zt_time_pads_to_hhmmss(self):
        self.assertEqual(ashare_data._fmt_zt_time(92500), "09:25:00")
        self.assertEqual(ashare_data._fmt_zt_time(93000), "09:30:00")
        self.assertEqual(ashare_data._fmt_zt_time(145500), "14:55:00")


class TestZtTriadCli(OfflineAshareDataTestCase):
    """打板三件套在 CLI --help 中可发现。"""

    def test_help_lists_all_three(self):
        help_text = run_cli("--help").stdout
        for name in ("limit-pool", "monitor-pool", "anomaly-pool"):
            self.assertIn(name, help_text)


class TestThsHotCommand(OfflineAshareDataTestCase):
    """ths-hot：L2 热度层，同花顺热榜(GET) → 东财人气榜(POST) → Tushare 回退。"""

    @mock.patch.object(ashare_data, "_curl_json")
    def test_ths_success_returns_true_with_concepts(self, curl_json):
        curl_json.return_value = {"data": {"stock_list": [{
            "order": 1, "code": "600519", "name": "贵州茅台", "rate": "100",
            "rise_and_fall": 2.08, "hot_rank_chg": 0,
            "tag": {"concept_tag": ["白酒", "奢侈品"], "popularity_tag": "持续上榜"},
        }]}}
        with redirect_stdout(StringIO()) as out:
            result = ashare_data.cmd_ths_hot("hour", None, 50)
        text = out.getvalue()
        self.assertIs(result, True)
        self.assertIn("同花顺热榜", text)
        self.assertIn("600519", text)
        self.assertIn("白酒", text)
        self.assertIn("持续上榜", text)

    @mock.patch.object(ashare_data, "_curl_json_post")
    @mock.patch.object(ashare_data, "_curl_json")
    def test_ths_empty_falls_back_to_em(self, curl_json, curl_json_post):
        # 同花顺(GET) 空 → 东财(POST) 给数据 → ulist(GET) 补名称
        curl_json.side_effect = [
            {"data": {"stock_list": []}},  # 同花顺
            {"data": {"diff": [  # ulist 补名称/价格
                {"f12": "600519", "f14": "贵州茅台", "f2": 1700.0, "f3": 2.08}]}},
        ]
        curl_json_post.return_value = {"data": [{"sc": "SH600519", "rk": 1, "hisRc": 0}]}
        with redirect_stdout(StringIO()) as out:
            result = ashare_data.cmd_ths_hot("hour", None, 50)
        text = out.getvalue()
        self.assertIs(result, True)
        self.assertIn("东财人气榜", text)
        self.assertIn("600519", text)
        self.assertIn("贵州茅台", text)
        # top 透传给东财 POST 的 pageSize
        self.assertEqual(curl_json_post.call_args.kwargs["data"]["pageSize"], 50)

    @mock.patch.object(ashare_data, "_curl_json_post")
    @mock.patch.object(ashare_data, "_get_tushare_client")
    @mock.patch.object(ashare_data, "_curl_json")
    def test_both_curl_fail_no_token_returns_false(self, curl_json, get_client, curl_json_post):
        curl_json.return_value = {"data": {"stock_list": []}}  # 同花顺空
        curl_json_post.return_value = {"data": []}             # 东财空
        get_client.return_value = None                         # 无 TUSHARE_TOKEN
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            result = ashare_data.cmd_ths_hot("hour", None, 50)
        self.assertIs(result, False)

    @mock.patch.object(ashare_data, "_curl_json_post")
    @mock.patch.object(ashare_data, "_get_tushare_client")
    @mock.patch.object(ashare_data, "_curl_json")
    def test_both_curl_fail_falls_back_to_tushare(self, curl_json, get_client, curl_json_post):
        curl_json.return_value = {"data": {"stock_list": []}}
        curl_json_post.return_value = {"data": []}
        fake = mock.MagicMock()
        fake.query.return_value = {"ok": True, "data": [{
            "ts_code": "600519.SH", "name": "贵州茅台", "rank": 1,
            "pct_change": 2.08, "hot_value": 100}]}
        get_client.return_value = fake
        with redirect_stdout(StringIO()) as out:
            result = ashare_data.cmd_ths_hot("hour", "20260731", 50)
        text = out.getvalue()
        self.assertIs(result, True)
        self.assertIn("Tushare ths_hot", text)
        fake.query.assert_called_once_with(
            "ths_hot", params={"trade_date": "20260731"}, fields=[])

    @mock.patch.object(ashare_data, "_curl_json_post")
    @mock.patch.object(ashare_data, "_curl_json")
    def test_passes_period_to_api(self, curl_json, curl_json_post):
        # 同花顺成功即返回，东财 POST 不触发；仅验证 period 透传
        curl_json.return_value = {"data": {"stock_list": [{
            "order": 1, "code": "600519", "name": "贵州茅台", "rate": "100",
            "rise_and_fall": 2.08, "hot_rank_chg": 0,
            "tag": {"concept_tag": ["白酒"], "popularity_tag": "持续上榜"},
        }]}}
        with redirect_stdout(StringIO()) as out:
            ashare_data.cmd_ths_hot("day", None, 30)
        self.assertEqual(curl_json.call_args.kwargs["params"]["type"], "day")
        self.assertIsNone(curl_json_post.call_args)  # 东财回退未触发
        self.assertIn("白酒", out.getvalue())


class TestEmHotRankHelper(OfflineAshareDataTestCase):
    """_em_hot_rank：东财人气榜(POST) + ulist.np(GET) 补名称/价格。"""

    @mock.patch.object(ashare_data, "_curl_json")
    @mock.patch.object(ashare_data, "_curl_json_post")
    def test_parses_prefixed_code_and_enriches(self, curl_json_post, curl_json):
        curl_json_post.return_value = {"data": [{"sc": "SH600519", "rk": 1, "hisRc": 3}]}
        curl_json.return_value = {"data": {"diff": [
            {"f12": "600519", "f14": "贵州茅台", "f2": 1700.0, "f3": 2.08}]}}
        rows = ashare_data._em_hot_rank(50)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "600519")
        self.assertEqual(rows[0]["name"], "贵州茅台")
        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(rows[0]["rank_chg"], 3)
        self.assertEqual(curl_json_post.call_args.kwargs["data"]["pageSize"], 50)

    @mock.patch.object(ashare_data, "_curl_json")
    @mock.patch.object(ashare_data, "_curl_json_post")
    def test_diff_dict_normalized(self, curl_json_post, curl_json):
        # push2 ulist.np 的 diff 偶有 dict（按序号为键），须 list(values()) 归一化
        curl_json_post.return_value = {"data": [{"sc": "SZ000001", "rk": 2, "hisRc": -1}]}
        curl_json.return_value = {"data": {"diff": {
            "0": {"f12": "000001", "f14": "平安银行", "f2": 11.0, "f3": 0.5}}}}
        rows = ashare_data._em_hot_rank(50)
        self.assertEqual(rows[0]["code"], "000001")
        self.assertEqual(rows[0]["name"], "平安银行")

    @mock.patch.object(ashare_data, "_curl_json")
    @mock.patch.object(ashare_data, "_curl_json_post")
    def test_ulist_failure_yields_unnamed_rows(self, curl_json_post, curl_json):
        curl_json_post.return_value = {"data": [{"sc": "SH600519", "rk": 1, "hisRc": 0}]}
        curl_json.side_effect = ashare_data.TransportError("offline")  # ulist 失败
        rows = ashare_data._em_hot_rank(50)
        self.assertEqual(rows[0]["code"], "600519")
        self.assertEqual(rows[0]["name"], "")  # 名称缺失

    @mock.patch.object(ashare_data, "_curl_json_post")
    def test_empty_when_post_returns_no_data(self, curl_json_post):
        curl_json_post.return_value = {"data": []}
        self.assertEqual(ashare_data._em_hot_rank(50), [])


class TestThsHotCli(OfflineAshareDataTestCase):
    """ths-hot 在 CLI 中可发现，--period/--top 参数可见。"""

    def test_help_lists_ths_hot(self):
        help_text = run_cli("--help").stdout
        self.assertIn("ths-hot", help_text)

    def test_period_and_top_in_help(self):
        help_text = run_cli("ths-hot", "--help").stdout
        self.assertIn("hour", help_text)
        self.assertIn("day", help_text)
        self.assertIn("--top", help_text)


class TestIrdInteractCommand(OfflineAshareDataTestCase):
    """ird-interact：L3 一手定性，巨潮互动易两步 POST。"""

    @mock.patch.object(ashare_data, "_curl_json_post")
    def test_success_returns_true_with_answers(self, post):
        # 第一步定 orgId；第二步拉问答（参数放 query string）
        post.side_effect = [
            {"data": [{"secid": "9900014448", "stockCode": "002475"}]},
            {"total": 1, "rows": [{
                "stockCode": "002475", "companyShortName": "立讯精密",
                "mainContent": "公司回购进度如何？", "attachedContent": "已回购 1766 万股",
                "attachedAuthor": "立讯精密", "pubDate": 1785463007000,
            }]},
        ]
        with redirect_stdout(StringIO()) as out:
            result = ashare_data.cmd_ird_interact("002475", 20)
        text = out.getvalue()
        self.assertIs(result, True)
        self.assertIn("互动易问答", text)
        self.assertIn("立讯精密", text)
        self.assertIn("公司回购进度如何？", text)
        self.assertIn("已回购 1766 万股", text)
        # 第二步是 GET 式 query string（非 body），data=None
        self.assertIsNone(post.call_args_list[1].kwargs.get("data"))

    @mock.patch.object(ashare_data, "_curl_json_post")
    def test_empty_when_no_ir_subject(self, post):
        post.return_value = {"data": []}  # 第一步未检索到主体
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            result = ashare_data.cmd_ird_interact("600519", 20)
        self.assertIs(result, False)

    @mock.patch.object(ashare_data, "_curl_json_post")
    def test_empty_when_no_qa_rows(self, post):
        post.side_effect = [
            {"data": [{"secid": "gssh0600519", "stockCode": "600519"}]},
            {"total": 0, "rows": []},  # 有主体但无问答（回复率极低属正常）
        ]
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            result = ashare_data.cmd_ird_interact("600519", 20)
        self.assertIs(result, False)

    @mock.patch.object(ashare_data, "_curl_json_post")
    def test_transport_error_returns_false(self, post):
        post.side_effect = ashare_data.TransportError("offline")
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            result = ashare_data.cmd_ird_interact("002475", 20)
        self.assertIs(result, False)

    def test_invalid_code_returns_false(self):
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            result = ashare_data.cmd_ird_interact("not-a-code", 20)
        self.assertIs(result, False)


class TestClsTelegraphCommand(OfflineAshareDataTestCase):
    """cls-telegraph：L3 快讯，财联社 v1 + 本地签名零 key。"""

    @mock.patch.object(ashare_data, "_curl_json")
    def test_success_returns_true(self, curl_json):
        curl_json.return_value = {"errno": 0, "msg": "", "data": {"roll_data": [
            {"ctime": 1785463600, "title": "美联储巴尔金讲话", "content": "利率是否足够高难断言"},
            {"ctime": 1785463700, "title": "墨西哥地震", "brief": "恰帕斯州 5.0 级"},
        ]}}
        with redirect_stdout(StringIO()) as out:
            result = ashare_data.cmd_cls_telegraph(50)
        text = out.getvalue()
        self.assertIs(result, True)
        self.assertIn("财联社实时电报", text)
        self.assertIn("美联储巴尔金讲话", text)
        self.assertIn("墨西哥地震", text)
        # sign 必带
        self.assertIn("sign=", curl_json.call_args.args[0])

    @mock.patch.object(ashare_data, "_curl_json")
    def test_error_no_returns_false(self, curl_json):
        curl_json.return_value = {"errno": 1, "msg": "bad sign"}
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            result = ashare_data.cmd_cls_telegraph(50)
        self.assertIs(result, False)

    @mock.patch.object(ashare_data, "_curl_json")
    def test_empty_returns_false(self, curl_json):
        curl_json.return_value = {"errno": 0, "msg": "", "data": {"roll_data": []}}
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            result = ashare_data.cmd_cls_telegraph(50)
        self.assertIs(result, False)

    @mock.patch.object(ashare_data, "_curl_json")
    def test_transport_error_returns_false(self, curl_json):
        curl_json.side_effect = ashare_data.TransportError("offline")
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            result = ashare_data.cmd_cls_telegraph(50)
        self.assertIs(result, False)

    def test_sign_is_deterministic(self):
        params = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5",
                  "last_time": "", "refresh_type": "1", "rn": "50"}
        sign = ashare_data._cls_sign(params)
        self.assertEqual(sign, ashare_data._cls_sign(dict(params)))  # 同输入同签名
        self.assertEqual(len(sign), 32)  # md5


class TestReportListCommand(OfflineAshareDataTestCase):
    """report-list：L3 研报，东财 reportapi（个股 qType=0 / 行业 qType=1）。"""

    @mock.patch.object(ashare_data, "_curl_json")
    def test_stock_reports_success(self, curl_json):
        curl_json.return_value = {"TotalPage": 1, "data": [
            {"title": "需求根基稳固", "publishDate": "2026-07-23 00:00:00.000",
             "orgSName": "中邮证券", "emRatingName": "买入", "predictThisYearEps": "67.19"},
        ]}
        with redirect_stdout(StringIO()) as out:
            result = ashare_data.cmd_report_list("600519", None, 30)
        text = out.getvalue()
        self.assertIs(result, True)
        self.assertIn("研报列表", text)
        self.assertIn("中邮证券", text)
        self.assertIn("买入", text)
        # 个股研报：qType=0，code=纯6位
        params = curl_json.call_args.kwargs["params"]
        self.assertEqual(params["qType"], "0")
        self.assertEqual(params["code"], "600519")

    @mock.patch.object(ashare_data, "_curl_json")
    def test_industry_reports_success(self, curl_json):
        curl_json.return_value = {"TotalPage": 1, "data": [
            {"title": "AI剧漫剧数据报告", "publishDate": "2026-07-30 00:00:00.000",
             "orgSName": "慧动创想", "emRatingName": "", "predictThisYearEps": ""},
        ]}
        with redirect_stdout(StringIO()) as out:
            result = ashare_data.cmd_report_list(None, "1238", 30)
        text = out.getvalue()
        self.assertIs(result, True)
        self.assertIn("行业研报", text)
        self.assertIn("AI剧漫剧数据报告", text)
        params = curl_json.call_args.kwargs["params"]
        self.assertEqual(params["qType"], "1")
        self.assertEqual(params["industryCode"], "1238")
        self.assertEqual(params["code"], "")

    @mock.patch.object(ashare_data, "_curl_json")
    def test_pagination_stops_on_empty_page(self, curl_json):
        curl_json.side_effect = [
            {"TotalPage": 3, "data": [{"title": "r1", "publishDate": "2026-01-01 00:00:00.000",
                                       "orgSName": "X", "emRatingName": "", "predictThisYearEps": ""}]},
            {"TotalPage": 3, "data": []},  # 第二页空 → 停止
        ]
        with redirect_stdout(StringIO()):
            result = ashare_data.cmd_report_list("600519", None, 30)
        self.assertIs(result, True)
        self.assertEqual(curl_json.call_count, 2)  # 不再请求第三页

    @mock.patch.object(ashare_data, "_curl_json")
    def test_empty_returns_false(self, curl_json):
        curl_json.return_value = {"TotalPage": 1, "data": []}
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            result = ashare_data.cmd_report_list("600519", None, 30)
        self.assertIs(result, False)

    def test_requires_code_or_industry(self):
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            result = ashare_data.cmd_report_list(None, None, 30)
        self.assertIs(result, False)

    def test_invalid_code_returns_false(self):
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            result = ashare_data.cmd_report_list("bad-code", None, 30)
        self.assertIs(result, False)


class TestL3TriadCli(OfflineAshareDataTestCase):
    """L3 三件套在 CLI --help 中可发现，关键参数可见。"""

    def test_help_lists_all_three(self):
        help_text = run_cli("--help").stdout
        for name in ("ird-interact", "cls-telegraph", "report-list"):
            self.assertIn(name, help_text)

    def test_ird_interact_help_shows_code_and_limit(self):
        help_text = run_cli("ird-interact", "--help").stdout
        self.assertIn("code", help_text)
        self.assertIn("--limit", help_text)

    def test_cls_telegraph_help_shows_top(self):
        help_text = run_cli("cls-telegraph", "--help").stdout
        self.assertIn("--top", help_text)

    def test_report_list_help_shows_industry(self):
        help_text = run_cli("report-list", "--help").stdout
        self.assertIn("--industry", help_text)
        self.assertIn("--limit", help_text)


if __name__ == "__main__":
    unittest.main()
