#!/usr/bin/env python3
"""Unit tests for tools/ashare_data.py."""

import argparse
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
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


if __name__ == "__main__":
    unittest.main()
