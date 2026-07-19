import unittest

from tools.ashare_plugin import success_result, with_verification
from tools.ashare_plugin.tushare_verification import (
    COMMAND_APIS,
    apply_market_precedence,
    compare_decimal,
    compare_text,
    finalize_verification,
    not_configured_verification,
    verify_command,
)


class TestVerificationCore(unittest.TestCase):
    def test_not_configured_has_closed_top_level_shape(self):
        result = not_configured_verification()

        self.assertEqual(set(result), {
            "provider", "configured", "status", "as_of",
            "warnings", "fields", "endpoints",
        })
        self.assertEqual(result["status"], "NOT_CONFIGURED")

    def test_decimal_threshold_is_inclusive(self):
        field = compare_decimal(
            "pb", "100", "101",
            primary_source="eastmoney",
            verification_source="tushare.daily_basic",
            primary_period="2026-07-18",
            verification_period="2026-07-18",
            primary_unit="multiple",
            verification_unit="multiple",
        )

        self.assertEqual(field["status"], "MATCH")
        self.assertEqual(field["deviation_pct"], "1.00")

    def test_period_or_unit_mismatch_is_insufficient(self):
        period = compare_decimal(
            "pb", "1", "1",
            primary_source="eastmoney",
            verification_source="tushare.daily_basic",
            primary_period="2026-07-18",
            verification_period="2026-07-17",
            primary_unit="multiple",
            verification_unit="multiple",
        )
        unit = compare_decimal(
            "market_cap", "1", "1",
            primary_source="tencent",
            verification_source="tushare.daily_basic",
            primary_period="2026-07-18",
            verification_period="2026-07-18",
            primary_unit="CNY",
            verification_unit="CNY_10K",
        )

        self.assertEqual(period["status"], "INSUFFICIENT")
        self.assertEqual(period["error_type"], "period_mismatch")
        self.assertEqual(unit["error_type"], "unit_mismatch")

    def test_conflict_precedes_match_in_aggregate(self):
        matching = compare_decimal(
            "pb", "1", "1",
            primary_source="a",
            verification_source="b",
            primary_period="2026",
            verification_period="2026",
            primary_unit="multiple",
            verification_unit="multiple",
        )
        conflict = compare_decimal(
            "pe", "10", "12",
            primary_source="a",
            verification_source="b",
            primary_period="2026",
            verification_period="2026",
            primary_unit="multiple",
            verification_unit="multiple",
        )

        result = finalize_verification([matching, conflict], [])

        self.assertEqual(result["status"], "CONFLICT")

    def test_text_difference_is_conflict(self):
        field = compare_text(
            "security_name", "招商银行", "招商银航",
            primary_source="eastmoney",
            verification_source="tushare.stock_basic",
            period="current",
        )

        self.assertEqual(field["status"], "CONFLICT")

    def test_with_verification_does_not_mutate_primary_result(self):
        primary = success_result({"pb": "1.2"}, "eastmoney")
        verification = not_configured_verification()

        combined = with_verification(primary, verification)

        self.assertNotIn("verification", primary)
        self.assertIs(combined["verification"], verification)

    def test_market_conflict_uses_tushare_as_effective_value(self):
        conflict = compare_decimal(
            "pb", "0.87", "0.8468",
            primary_source="tencent",
            verification_source="tushare.daily_basic",
            primary_period="20260717",
            verification_period="20260717",
            primary_unit="multiple",
            verification_unit="multiple",
        )

        result = apply_market_precedence("valuation", {
            "provider": "tushare", "fields": [conflict],
        })

        field = result["fields"][0]
        self.assertEqual(field["effective_value"], "0.8468")
        self.assertEqual(field["effective_source"], "tushare.daily_basic")
        self.assertTrue(field["precedence_applied"])
        self.assertEqual(conflict["primary_value"], "0.87")

    def test_financial_or_noncomparable_conflict_keeps_primary_effective_value(self):
        financial = compare_decimal(
            "roe", "13.44", "12.0198",
            primary_source="eastmoney",
            verification_source="tushare.fina_indicator",
            primary_period="20251231",
            verification_period="20251231",
            primary_unit="percent",
            verification_unit="percent",
        )
        period_mismatch = compare_decimal(
            "pb", "0.87", "0.8468",
            primary_source="tencent",
            verification_source="tushare.daily_basic",
            primary_period="20260717",
            verification_period="20260716",
            primary_unit="multiple",
            verification_unit="multiple",
        )

        financial_result = apply_market_precedence("financials", {
            "provider": "tushare", "fields": [financial],
        })
        mismatch_result = apply_market_precedence("valuation", {
            "provider": "tushare", "fields": [period_mismatch],
        })

        self.assertEqual(financial_result["fields"][0]["effective_value"], "13.44")
        self.assertFalse(financial_result["fields"][0]["precedence_applied"])
        self.assertEqual(mismatch_result["fields"][0]["effective_value"], "0.87")
        self.assertFalse(mismatch_result["fields"][0]["precedence_applied"])


def _ok(source, rows):
    return {
        "ok": True,
        "data": rows,
        "source": source,
        "fallback_used": False,
        "as_of": "2026-07-19T00:00:00+00:00",
        "warnings": [],
    }


class FakeTushareClient:
    configured = True

    def __init__(self, results):
        self.results = results
        self.calls = []

    def query(self, api_name, *, params=None, fields=()):
        self.calls.append((api_name, dict(params or {}), tuple(fields)))
        return self.results[api_name]


class TestCommandVerification(unittest.TestCase):
    def test_market_value_converts_tencent_yi_to_tushare_ten_thousand_cny(self):
        client = FakeTushareClient({
            "daily_basic": _ok("tushare.daily_basic", [{
                "ts_code": "600036.SH",
                "trade_date": "20260718",
                "close": 10,
                "pe": 8,
                "pb": 1.1,
                "turnover_rate": 1.2,
                "total_mv": 1000000,
                "circ_mv": 800000,
            }]),
        })

        result = verify_command("valuation", "600036", {
            "quote_time": "20260718150000",
            "price": "10",
            "pe": "8",
            "pb": "1.1",
            "turnover_rate": "1.2",
            "market_cap": "100",
            "float_cap": "80",
        }, client=client)

        self.assertEqual(result["status"], "MATCH")
        market_cap = next(
            field for field in result["fields"]
            if field["field"] == "market_cap"
        )
        self.assertEqual(market_cap["primary_value"], "1000000")
        self.assertEqual(market_cap["unit"], "CNY_10K")
        self.assertEqual(client.calls[0][1]["trade_date"], "20260718")

    def test_market_date_mismatch_never_matches(self):
        client = FakeTushareClient({
            "daily_basic": _ok("tushare.daily_basic", [{
                "ts_code": "600036.SH",
                "trade_date": "20260717",
                "close": 10,
            }]),
        })

        result = verify_command("quote", "600036", {
            "quote_time": "20260718113000",
            "price": "10",
        }, client=client)

        self.assertEqual(result["status"], "INSUFFICIENT")
        self.assertEqual(result["fields"][0]["error_type"], "period_mismatch")

    def test_quote_verifies_all_displayed_market_fields(self):
        client = FakeTushareClient({
            "daily_basic": _ok("tushare.daily_basic", [{
                "trade_date": "20260718",
                "close": 10,
                "total_mv": 1000000,
                "circ_mv": 800000,
                "pe": 8,
                "pb": 1.1,
                "pe_ttm": 8,
                "dv_ratio": 6.88,
                "turnover_rate": 1.2,
            }]),
        })

        result = verify_command("quote", "600036", {
            "quote_time": "20260718150000",
            "price": "10",
            "market_cap": "100",
            "float_cap": "80",
            "pe": "8",
            "pb": "1.1",
            "turnover_rate": "1.2",
        }, client=client)

        self.assertEqual(
            {field["field"] for field in result["fields"]},
            {"close", "market_cap", "float_cap", "pe", "pb", "turnover_rate",
             "pe_ttm", "dividend_yield"},
        )

    def test_quote_surfaces_tushare_dividend_yield_without_tencent_primary(self):
        client = FakeTushareClient({
            "daily_basic": _ok("tushare.daily_basic", [{
                "trade_date": "20260718",
                "close": 10,
                "dv_ratio": 6.88,
            }]),
        })
        result = verify_command("quote", "600036", {
            "quote_time": "20260718150000",
            "price": "10",
        }, client=client)
        dy = next(f for f in result["fields"] if f["field"] == "dividend_yield")
        # Tencent quote has no dividend yield -> surfaced as INSUFFICIENT but
        # the independent Tushare value is preserved for the research layer.
        self.assertEqual(dy["verification_value"], "6.88")
        self.assertEqual(dy["verification_source"], "tushare.daily_basic")
        self.assertEqual(dy["status"], "INSUFFICIENT")

    def test_financials_selects_same_period_latest_update(self):
        client = FakeTushareClient({
            "income": _ok("tushare.income", [
                {
                    "end_date": "20251231",
                    "f_ann_date": "20260320",
                    "report_type": "1",
                    "update_flag": "0",
                    "total_revenue": 99,
                },
                {
                    "end_date": "20251231",
                    "f_ann_date": "20260401",
                    "report_type": "1",
                    "update_flag": "1",
                    "total_revenue": 100,
                },
            ]),
            "balancesheet": _ok("tushare.balancesheet", [
                {"end_date": "20251231", "update_flag": "1"},
            ]),
            "cashflow": _ok("tushare.cashflow", [
                {"end_date": "20251231", "update_flag": "1", "n_cashflow_act": 20},
            ]),
            "fina_indicator": _ok("tushare.fina_indicator", [
                {"end_date": "20251231", "update_flag": "1", "roe": 10},
            ]),
        })

        result = verify_command("financials", "600036", [{
            "REPORT_DATE": "2025-12-31",
            "REPORT_DATE_NAME": "2025年报",
            "TOTALOPERATEREVE": 100,
            "ROEJQ": 10,
        }], client=client)

        revenue = next(
            field for field in result["fields"] if field["field"] == "revenue"
        )
        self.assertEqual(revenue["verification_value"], "100")
        self.assertEqual(revenue["status"], "MATCH")

    def test_every_command_has_an_explicit_route(self):
        self.assertEqual(set(COMMAND_APIS), {
            "quote", "valuation", "financials", "history",
            "equity-history", "search", "signals", "announcements",
            # Phase 1: Tushare 10,000-point commands
            "pe-band", "research-visits", "insider-trades",
            # Phase 2: enhancement commands
            "consensus", "shareholders", "dividend", "management",
            # P1: Industry benchmark
            "industry-pe",
            "news", "disclosure-calendar", "hk-quote", "ah-cross-check",
            # Tier 1 gap fillers
            "mainbz", "managers", "repurchase", "pledge", "express", "kline",
            # Tier 2 gap fillers
            "audit", "holder-num", "ratios", "peers", "north-hold",
        })

    def test_managers_and_mainbz_route_to_tushare_primary(self):
        for command, api in (
            ("managers", "stk_managers"), ("mainbz", "fina_mainbz"),
            ("repurchase", "repurchase"), ("pledge", "pledge_stat"),
            ("express", "express"),
            ("audit", "fina_audit"), ("holder-num", "stk_holdernumber"),
            ("ratios", "fina_indicator"), ("peers", "index_member_all"),
            ("north-hold", "hk_hold"),
        ):
            client = FakeTushareClient(
                {api: _ok(f"tushare.{api}", [{"x": 1}, {"x": 2}])}
            )
            result = verify_command(command, "600036", [{}], client=client)
            field = next(
                f for f in result["fields"] if f["field"] == f"{api}:row_count"
            )
            self.assertEqual(field["status"], "MATCH")
            self.assertEqual(field["primary_value"], "2")
            # ts_code was passed (not treated as a search / market command)
            self.assertEqual(client.calls[0][0], api)

    def test_partial_endpoint_failure_keeps_successful_financial_fields(self):
        client = FakeTushareClient({
            "income": _ok("tushare.income", [{
                "end_date": "20251231",
                "report_type": "1",
                "update_flag": "1",
                "total_revenue": 100,
            }]),
            "balancesheet": {
                "ok": False,
                "data": None,
                "source": "tushare",
                "error_type": "permission_denied",
                "warnings": [],
            },
            "cashflow": {
                "ok": False,
                "data": None,
                "source": "tushare",
                "error_type": "empty_data",
                "warnings": [],
            },
            "fina_indicator": _ok("tushare.fina_indicator", [{
                "end_date": "20251231",
                "update_flag": "1",
            }]),
        })

        result = verify_command("financials", "600036", [{
            "REPORT_DATE": "2025-12-31",
            "TOTALOPERATEREVE": 100,
        }], client=client)

        self.assertEqual(result["status"], "MATCH")
        self.assertIn("balancesheet: permission_denied", result["warnings"])


class TestSafeVerifyCommand(unittest.TestCase):
    def test_delegates_to_verify_command_and_returns_result(self):
        from tools.ashare_plugin.tushare_verification import safe_verify_command
        from unittest import mock
        expected = {
            "provider": "tushare",
            "configured": True,
            "status": "MATCH",
            "as_of": None,
            "warnings": [],
            "fields": [],
            "endpoints": [],
        }
        with mock.patch(
            "tools.ashare_plugin.tushare_verification.verify_command",
            return_value=expected,
        ):
            result = safe_verify_command("search", "平安银行", [{"Code": "1"}])
        self.assertEqual(result, expected)

    def test_returns_degraded_on_exception(self):
        from tools.ashare_plugin.tushare_verification import safe_verify_command
        from unittest import mock
        with mock.patch(
            "tools.ashare_plugin.tushare_verification.verify_command",
            side_effect=RuntimeError("boom"),
        ):
            result = safe_verify_command("search", "平安银行", [])
        self.assertEqual(result["status"], "INSUFFICIENT")
        self.assertEqual(result["provider"], "tushare")
        self.assertTrue(result["configured"])
        self.assertEqual(result["fields"], [])
        self.assertEqual(result["endpoints"], [])

    def test_forwards_trade_date_and_client_kwargs(self):
        from tools.ashare_plugin.tushare_verification import safe_verify_command
        from unittest import mock
        fake_client = object()
        with mock.patch(
            "tools.ashare_plugin.tushare_verification.verify_command",
            return_value={"status": "MATCH"},
        ) as mock_verify:
            safe_verify_command(
                "signals", "600519", {},
                trade_date="2026-07-17", client=fake_client,
            )
        _, kwargs = mock_verify.call_args
        self.assertEqual(kwargs["trade_date"], "2026-07-17")
        self.assertIs(kwargs["client"], fake_client)


if __name__ == "__main__":
    unittest.main()
