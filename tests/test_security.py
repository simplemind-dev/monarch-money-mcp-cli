import asyncio
import inspect
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from monarch_money_cli import client, keychain
from monarch_money_cli.client import MonarchClient, MonarchError
from tests._mock import TOKEN, MockMonarch


class ClientHardening(unittest.TestCase):
    def test_host_is_fixed_https(self):
        self.assertEqual(client.BASE_URL, "https://api.monarch.com")

    def test_client_has_no_env_access(self):
        src = inspect.getsource(client)
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


SET_CATEGORY = "Web_TransactionDrawerUpdateTransaction"
SET_TAGS = "Web_SetTransactionTags"


class MutationAllowlist(unittest.TestCase):
    def test_every_mutation_in_client_is_allowlisted_and_every_entry_is_used(self):
        src = inspect.getsource(client)
        in_source = set(re.findall(r"\bmutation\s+([A-Za-z_]\w*)\s*[({]", src))
        self.assertEqual(in_source, set(client.ALLOWED_MUTATIONS))
        texts = [v for v in vars(client).values() if isinstance(v, str) and v.lstrip().startswith("mutation")]
        self.assertEqual({re.match(r"\s*mutation\s+(\w+)", t).group(1) for t in texts}, set(client.ALLOWED_MUTATIONS))
        for name in client.ALLOWED_MUTATIONS:
            self.assertRegex(src, rf'_mutate\(\s*"{name}"', name)

    def test_entries_explain_themselves(self):
        self.assertTrue(client.ALLOWED_MUTATIONS)
        for name, m in client.ALLOWED_MUTATIONS.items():
            self.assertTrue(m.does.strip(), name)
            self.assertTrue(m.why.strip(), name)
            self.assertIsInstance(m.input_keys, frozenset, name)
            self.assertTrue(m.input_keys, name)

    def test_every_entry_is_in_security_md(self):
        doc = (Path(__file__).resolve().parents[1] / "SECURITY.md").read_text()
        for name in client.ALLOWED_MUTATIONS:
            self.assertIn(name, doc)

    def assertRefusedWithoutRequest(self, call):
        with MockMonarch() as m:
            with self.assertRaises(MonarchError):
                asyncio.run(call())
            self.assertEqual(m.requests, [])

    def test_unlisted_mutation_refused(self):
        c = MonarchClient(TOKEN, allow_writes=True)
        q = "mutation Common_DeleteTransactionMutation($input: DeleteTransactionMutationInput!) { deleteTransaction(input: $input) { deleted } }"
        self.assertRefusedWithoutRequest(lambda: c._mutate("Common_DeleteTransactionMutation", q, {"input": {"transactionId": "1"}}))

    def test_wrong_input_keys_refused(self):
        c = MonarchClient(TOKEN, allow_writes=True)
        for variables in ({"input": {"id": "1", "category": "2", "notes": "x"}},  # extra key
                          {"input": {"id": "1"}},  # missing key
                          {"input": {"id": "1", "category": "2"}, "extra": 1},  # extra variable
                          {"input": None}, {}):
            self.assertRefusedWithoutRequest(lambda v=variables: c._mutate(SET_CATEGORY, client.M_SET_CATEGORY, v))
        self.assertRefusedWithoutRequest(lambda: c._mutate(
            SET_TAGS, client.M_SET_TAGS, {"input": {"transactionId": "1", "tagIds": [], "id": "1"}}))

    def test_query_text_must_be_that_single_mutation(self):
        c = MonarchClient(TOKEN, allow_writes=True)
        tags_input = {"input": {"transactionId": "1", "tagIds": []}}
        # Allowed name, but the text is a different mutation.
        self.assertRefusedWithoutRequest(lambda: c._mutate(SET_TAGS, client.M_SET_CATEGORY, tags_input))
        # A second operation smuggled into the document.
        smuggled = client.M_SET_TAGS + "\nmutation Other($input: X!) { deleteTransaction(input: $input) { deleted } }"
        self.assertRefusedWithoutRequest(lambda: c._mutate(SET_TAGS, smuggled, tags_input))
        self.assertRefusedWithoutRequest(lambda: c._mutate(SET_TAGS, "query GetAccounts { accounts { id } }", tags_input))

    def test_reads_refuse_mutations(self):
        c = MonarchClient(TOKEN, allow_writes=True)
        self.assertRefusedWithoutRequest(lambda: c._gql(SET_CATEGORY, client.M_SET_CATEGORY,
                                                        {"input": {"id": "1", "category": "2"}}))
        self.assertRefusedWithoutRequest(lambda: c._gql("GetAccounts", "{ accounts { id } } mutation X { y }"))

    def test_writes_off_by_default(self):
        c = MonarchClient(TOKEN)
        self.assertRefusedWithoutRequest(lambda: c.set_transaction_category("1", "2"))
        self.assertRefusedWithoutRequest(lambda: c.set_transaction_tags("1", ["g1"]))

    def test_allowed_mutation_sends_one_request(self):
        with MockMonarch() as m:
            asyncio.run(MonarchClient(TOKEN, allow_writes=True).set_transaction_category("301", "203"))
            asyncio.run(MonarchClient(TOKEN, allow_writes=True).set_transaction_tags("301", ["g2"]))
            self.assertEqual([(b["operationName"], b["variables"]) for b in m.mutations()],
                             [(SET_CATEGORY, {"input": {"id": "301", "category": "203"}}),
                              (SET_TAGS, {"input": {"transactionId": "301", "tagIds": ["g2"]}})])
            self.assertEqual(len(m.requests), 2)

    def test_payload_errors_are_failures(self):
        with MockMonarch() as m:
            m.payload_errors = {"message": "Invalid category", "code": "BAD_INPUT",
                                "fieldErrors": [{"field": "category", "messages": ["does not exist"]}]}
            with self.assertRaises(MonarchError) as ctx:
                asyncio.run(MonarchClient(TOKEN, allow_writes=True).set_transaction_category("301", "999"))
        self.assertIn("Invalid category", str(ctx.exception))
        self.assertIn("does not exist", str(ctx.exception))

    def test_empty_payload_errors_are_not_failures(self):
        self.assertIsNone(client._payload_errors({"x": {"errors": None}}))
        self.assertIsNone(client._payload_errors({"x": {"errors": []}}))
        self.assertIsNone(client._payload_errors({"x": {"errors": {"message": None, "code": None, "fieldErrors": []}}}))
        self.assertEqual(client._payload_errors({"x": {"errors": [{"message": "nope"}]}}), "nope")


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
