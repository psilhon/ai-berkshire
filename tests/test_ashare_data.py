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


if __name__ == "__main__":
    unittest.main()
