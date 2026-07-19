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


if __name__ == "__main__":
    unittest.main()
