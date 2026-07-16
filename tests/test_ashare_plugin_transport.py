import json
import subprocess
import unittest
from unittest import mock

from tools.ashare_plugin.transport import FallbackChain, TransportClient


class TestTransportClient(unittest.TestCase):
    def test_transport_builds_query_and_uses_curl_without_proxy(self):
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, stdout=b'{"ok": true}', stderr=b"")

        data = TransportClient(runner=runner, sleep=lambda _: None).get_json(
            "https://example.test/api", {"code": "600519"}
        )

        self.assertEqual(data, {"ok": True})
        args, kwargs = calls[0]
        self.assertIn("--noproxy", args)
        self.assertIn("*", args)
        self.assertIn("code=600519", args[-1])
        self.assertEqual(kwargs["timeout"], 15)

    def test_transport_classifies_timeout(self):
        def runner(args, **kwargs):
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])

        with self.assertRaisesRegex(Exception, "timeout"):
            TransportClient(runner=runner, sleep=lambda _: None).get_json(
                "https://example.test/api"
            )

    def test_transport_retries_transient_http_status(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"HTTP 503")
            return subprocess.CompletedProcess(args, 0, stdout=b'{"ok": true}', stderr=b"")

        data = TransportClient(runner=runner, retries=1, sleep=lambda _: None).get_json(
            "https://example.test/api"
        )
        self.assertEqual(data, {"ok": True})
        self.assertEqual(len(calls), 2)


class TestFallbackChain(unittest.TestCase):
    def test_fallback_chain_returns_source_and_warning(self):
        def failed():
            return {
                "ok": False,
                "data": None,
                "source": "primary",
                "warnings": [],
                "error_type": "rate_limited",
                "message": "blocked",
            }

        def backup():
            return {
                "ok": True,
                "data": {"value": 1},
                "source": "backup",
                "fallback_used": False,
                "as_of": "2026-07-16T00:00:00+00:00",
                "warnings": [],
            }

        result = FallbackChain([failed, backup]).run()
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "backup")
        self.assertTrue(result["fallback_used"])
        self.assertEqual(len(result["warnings"]), 1)

    def test_fallback_chain_returns_explicit_failure(self):
        result = FallbackChain(
            [lambda: {"ok": False, "source": "a", "message": "bad", "warnings": []}]
        ).run()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "all_sources_failed")


if __name__ == "__main__":
    unittest.main()
