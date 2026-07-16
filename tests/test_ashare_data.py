#!/usr/bin/env python3
"""Unit tests for tools/ashare_data.py."""

import argparse
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


class TestSecurityCode(unittest.TestCase):
    def test_normalizes_shenzhen_shanghai_and_beijing(self):
        self.assertEqual(ashare_data._em_secu_code("600036"), "600036.SH")
        self.assertEqual(ashare_data._em_secu_code("000001.SZ"), "000001.SZ")
        self.assertEqual(ashare_data._em_secu_code("430047"), "430047.BJ")

    def test_rejects_invalid_code(self):
        with self.assertRaises(ValueError):
            ashare_data._em_secu_code("ABC")
        with self.assertRaises(ValueError):
            ashare_data._em_secu_code("600036.HK")


class TestPositiveYears(unittest.TestCase):
    def test_accepts_range_boundaries(self):
        self.assertEqual(ashare_data._positive_years("1"), 1)
        self.assertEqual(ashare_data._positive_years("50"), 50)

    def test_rejects_values_outside_range(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            ashare_data._positive_years("0")
        with self.assertRaises(argparse.ArgumentTypeError):
            ashare_data._positive_years("51")


class TestDatacenterPagination(unittest.TestCase):
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


class TestHistoryCommand(unittest.TestCase):
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


class TestEquityHistoryCommand(unittest.TestCase):
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


class TestLegacyCommandExitSemantics(unittest.TestCase):
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


class TestPluginCommands(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
