import asyncio
import ssl
import unittest
import urllib.error
from unittest import mock

from monarch_money_cli import client
from monarch_money_cli.client import MonarchClient, MonarchError, MonarchUnavailable
from tests._mock import TOKEN, MockMonarch


class Unavailable(unittest.TestCase):
    """client._post maps upstream outages to a distinct, non-retrying MonarchUnavailable."""

    def test_502_503_504_429_via_mock_server(self):
        for status in (502, 503, 504, 429):
            with MockMonarch() as m, self.assertRaises(MonarchUnavailable) as ctx:
                m.force_status = status
                asyncio.run(MonarchClient(TOKEN).entities())
            self.assertIn(f"HTTP {status}", str(ctx.exception))
            self.assertIn("temporarily unavailable", str(ctx.exception))
            self.assertIn("Try again in a minute", str(ctx.exception))

    def test_other_http_status_keeps_existing_behaviour(self):
        # 400 is not in the outage set: the generic "Monarch returned HTTP 400." path still applies.
        with MockMonarch() as m, self.assertRaises(MonarchError) as ctx:
            m.force_status = 400
            asyncio.run(MonarchClient(TOKEN).entities())
        self.assertNotIsInstance(ctx.exception, MonarchUnavailable)
        self.assertIn("HTTP 400", str(ctx.exception))

    def test_connection_drop_or_timeout(self):
        with mock.patch.object(client._OPENER, "open",
                               side_effect=urllib.error.URLError(ConnectionResetError())):
            with self.assertRaises(MonarchUnavailable) as ctx:
                asyncio.run(MonarchClient(TOKEN).entities())
        self.assertIn("temporarily unavailable", str(ctx.exception))
        self.assertIn("Try again in a minute", str(ctx.exception))

    def test_no_retry_is_attempted(self):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.URLError(TimeoutError())
        with mock.patch.object(client, "_OPENER", opener):
            with self.assertRaises(MonarchUnavailable):
                asyncio.run(MonarchClient(TOKEN).entities())
        self.assertEqual(opener.open.call_count, 1)

    def test_read_timeout_after_connect_is_unavailable(self):
        # TimeoutError raised from resp.read() isn't wrapped as URLError by urllib.
        resp = mock.MagicMock()
        resp.read.side_effect = TimeoutError()
        resp.__enter__.return_value = resp
        with mock.patch.object(client._OPENER, "open", return_value=resp):
            with self.assertRaises(MonarchUnavailable) as ctx:
                asyncio.run(MonarchClient(TOKEN).entities())
        self.assertIn("temporarily unavailable", str(ctx.exception))
        self.assertIn("Try again in a minute", str(ctx.exception))

    def test_connection_reset_while_reading_is_unavailable(self):
        resp = mock.MagicMock()
        resp.read.side_effect = ConnectionResetError()
        resp.__enter__.return_value = resp
        with mock.patch.object(client._OPENER, "open", return_value=resp):
            with self.assertRaises(MonarchUnavailable) as ctx:
                asyncio.run(MonarchClient(TOKEN).entities())
        self.assertIn("temporarily unavailable", str(ctx.exception))


class TlsVerification(unittest.TestCase):
    """A cert failure is a security-relevant condition, not a transient outage: never suggest retry."""

    def test_cert_verification_failure_is_not_marked_retryable(self):
        reason = ssl.SSLCertVerificationError(1, "certificate verify failed: self-signed certificate")
        with mock.patch.object(client._OPENER, "open", side_effect=urllib.error.URLError(reason)):
            with self.assertRaises(MonarchError) as ctx:
                asyncio.run(MonarchClient(TOKEN).entities())
        self.assertNotIsInstance(ctx.exception, MonarchUnavailable)
        self.assertEqual(
            str(ctx.exception),
            "Could not verify api.monarch.com's TLS certificate, so nothing was sent. "
            "Check for a proxy, VPN, or captive portal on this network.",
        )

    def test_generic_ssl_error_is_not_marked_retryable(self):
        reason = ssl.SSLError("[SSL] wrong version number")
        with mock.patch.object(client._OPENER, "open", side_effect=urllib.error.URLError(reason)):
            with self.assertRaises(MonarchError) as ctx:
                asyncio.run(MonarchClient(TOKEN).entities())
        self.assertNotIsInstance(ctx.exception, MonarchUnavailable)
        self.assertIn("Could not verify api.monarch.com's TLS certificate", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
