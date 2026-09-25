import inspect
import os
import re
import unittest
from unittest import mock

from monarch_money_cli import client, keychain
from tests._mock import TOKEN, MockMonarch


class ClientHardening(unittest.TestCase):
    def test_host_is_fixed_https(self):
        self.assertEqual(client.BASE_URL, "https://api.monarch.com")

    def test_client_has_no_mutations_or_env_access(self):
        src = inspect.getsource(client)
        self.assertNotRegex(src, r"\bmutation\b")
        self.assertNotIn("os.environ", src)
        self.assertNotIn("getenv", src)

    def test_production_opener_ignores_proxies_and_refuses_redirects(self):
        handlers = client._OPENER.handlers
        # An empty ProxyHandler is dropped by build_opener and suppresses the default,
        # env-reading one; so no handler with configured proxies may be present.
        proxies = [h for h in handlers if getattr(h, "proxies", None)]
        self.assertEqual(proxies, [])
        self.assertTrue(any(isinstance(h, client._NoRedirect) for h in handlers))

    def test_proxy_env_ignored(self):
        with MockMonarch() as m, mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9",
                                                             "HTTPS_PROXY": "http://127.0.0.1:9"}):
            status, _ = client._post("/graphql", {"operationName": "GetAccounts"}, TOKEN, 5)
            self.assertEqual(status, 200)
            self.assertEqual(len(m.requests), 1)

    def test_redirect_not_followed(self):
        with MockMonarch():
            status, _ = client._post("/redirect", {}, TOKEN, 5)
            self.assertEqual(status, 302)

    def test_response_size_capped(self):
        with MockMonarch(), self.assertRaises(client.MonarchError):
            client._post("/huge", {}, TOKEN, 5)


class KeychainSafety(unittest.TestCase):
    def test_token_validation(self):
        self.assertTrue(keychain.valid_token(TOKEN))
        for bad in ['abc"; rm -rf /', "short", "a" * 64 + "\n", "a" * 300, "tok en" * 10]:
            self.assertFalse(keychain.valid_token(bad), bad)

    def test_store_sends_token_via_stdin_not_argv(self):
        with mock.patch.object(keychain.sys, "platform", "darwin"), \
             mock.patch.object(keychain.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            keychain.store(TOKEN)
            argv = run.call_args.args[0]
            self.assertEqual(argv, ["/usr/bin/security", "-i"])
            self.assertNotIn(TOKEN, " ".join(argv))
            self.assertIn(TOKEN, run.call_args.kwargs["input"])

    def test_store_refuses_bad_token(self):
        with mock.patch.object(keychain.sys, "platform", "darwin"), \
             mock.patch.object(keychain.subprocess, "run") as run, \
             self.assertRaises(keychain.KeychainError):
            keychain.store('x" ; delete-keychain login.keychain')
        run.assert_not_called()

    def test_load_rejects_garbage_from_keychain(self):
        with mock.patch.object(keychain.sys, "platform", "darwin"), \
             mock.patch.object(keychain.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="not a token!\n")
            self.assertIsNone(keychain.load())

    def test_token_state(self):
        with mock.patch.object(keychain.sys, "platform", "darwin"), \
             mock.patch.object(keychain.subprocess, "run") as run:
            for rc, stdout, want in [(0, TOKEN + "\n", "ok"), (44, "", "missing"), (0, "not a token!\n", "invalid")]:
                run.return_value = mock.Mock(returncode=rc, stdout=stdout)
                self.assertEqual(keychain.token_state(), want)
        with mock.patch.object(keychain.sys, "platform", "linux"):
            self.assertEqual(keychain.token_state(), "unsupported")

    def test_no_plaintext_fallback_off_macos(self):
        with mock.patch.object(keychain.sys, "platform", "linux"):
            self.assertIsNone(keychain.load())
            with self.assertRaises(keychain.KeychainError):
                keychain.store(TOKEN)

    @unittest.skipUnless(os.environ.get("MONARCH_KEYCHAIN_TEST") == "1" and keychain.sys.platform == "darwin",
                         "set MONARCH_KEYCHAIN_TEST=1 on macOS to test the real Keychain")
    def test_real_keychain_roundtrip(self):
        with mock.patch.object(keychain, "SERVICE", "monarch-money-cli-test"):
            keychain.store(TOKEN)
            self.assertEqual(keychain.load(), TOKEN)
            self.assertTrue(keychain.delete())
            self.assertIsNone(keychain.load())


    def test_service_name_is_frozen(self):
        # Stored tokens live under this service name; changing it would log every user out.
        self.assertEqual(keychain.SERVICE, "monarch-money-cli")
        self.assertEqual(keychain.ACCOUNT, "session_token")


class NoForbiddenImports(unittest.TestCase):
    def test_core_uses_stdlib_only(self):
        import sys
        from pathlib import Path
        pkg = Path(client.__file__).parent
        stdlib = sys.stdlib_module_names
        for f in pkg.glob("*.py"):
            if f.name == "mcp_server.py":
                continue
            for mod in re.findall(r"^\s*(?:from|import)\s+([a-zA-Z_][\w]*)", f.read_text(), re.M):
                self.assertTrue(mod in stdlib or mod in ("monarch_money_cli", "__future__"),
                                f"{f.name} imports non-stdlib module {mod!r}")
