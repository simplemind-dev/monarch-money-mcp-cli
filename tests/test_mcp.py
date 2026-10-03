import asyncio
import unittest
from unittest import mock

try:
    from mcp import Client, types
    from mcp.server.mcpserver.exceptions import ToolError

    from monarch_money_cli import mcp_server
except ImportError:  # [mcp] extra not installed
    mcp_server = None

from monarch_money_cli import keychain
from tests._mock import TOKEN, MockMonarch

WRITE_TOOLS = {"monarch_set_transaction_category", "monarch_update_transaction_tags"}


@unittest.skipIf(mcp_server is None, "mcp extra not installed")
class McpServer(unittest.TestCase):
    def test_all_tools_read_only(self):
        tools = asyncio.run(mcp_server.mcp.list_tools())
        self.assertEqual({t.name for t in tools}, {"monarch_list_accounts", "monarch_list_transactions",
                                                  "monarch_cashflow_summary", "monarch_spending_by_category",
                                                  "monarch_income_by_category", "monarch_list_entities",
                                                  "monarch_cashflow_by_entity", "monarch_budget_summary",
                                                  "monarch_list_goals", "monarch_list_recurring",
                                                  "monarch_list_holdings", "monarch_net_worth_history",
                                                  "monarch_list_categories"})
        for t in tools:
            self.assertTrue(t.annotations.read_only_hint, t.name)
            self.assertFalse(t.annotations.destructive_hint, t.name)

    def test_tool_call(self):
        with MockMonarch(), mock.patch.object(keychain, "load", return_value=TOKEN):
            r = asyncio.run(mcp_server.mcp.call_tool("monarch_cashflow_summary",
                                                     {"start_date": "2026-09-01", "end_date": "2026-09-30"}))
            self.assertIn("4700", str(r))
            self.assertIn("expense_categories", str(r))

    def test_planning_and_investment_tools(self):
        args = {"start_date": "2026-09-01", "end_date": "2026-09-30"}
        with MockMonarch(), mock.patch.object(keychain, "load", return_value=TOKEN):
            def call(tool, a):
                return str(asyncio.run(mcp_server.mcp.call_tool(tool, a)))
            self.assertIn("Groceries", call("monarch_budget_summary", args))
            goals = call("monarch_list_goals", args)
            self.assertIn("Emergency fund", goals)
            self.assertNotIn("secret-image-id", goals)
            self.assertIn("Old car", call("monarch_list_goals", {**args, "include_archived": True}))
            self.assertIn("Landlord", call("monarch_list_recurring", args))
            self.assertIn("VTI", call("monarch_list_holdings", {"account_ids": ["3"]}))
            self.assertIn("104500.5", call("monarch_net_worth_history", args))
            with self.assertRaises(ToolError):
                call("monarch_list_holdings", {"account_ids": ["3; drop"]})

    def test_category_tools_split_income_and_expenses(self):
        args = {"start_date": "2026-09-01", "end_date": "2026-09-30"}
        with MockMonarch(), mock.patch.object(keychain, "load", return_value=TOKEN):
            spending = str(asyncio.run(mcp_server.mcp.call_tool("monarch_spending_by_category", args)))
            income = str(asyncio.run(mcp_server.mcp.call_tool("monarch_income_by_category", args)))
        self.assertIn("Rent", spending)
        self.assertNotIn("Paycheck", spending)
        self.assertNotIn("Credit Card Payment", spending)
        self.assertIn("Paycheck", income)
        self.assertNotIn("Groceries", income)

    def test_entity_scope_and_by_entity(self):
        args = {"start_date": "2026-09-01", "end_date": "2026-09-30"}
        with MockMonarch() as m, mock.patch.object(keychain, "load", return_value=TOKEN):
            self.assertIn("Acme LLC", str(asyncio.run(mcp_server.mcp.call_tool("monarch_list_entities", {}))))
            for tool, extra in (("monarch_list_accounts", {}), ("monarch_list_transactions", args),
                                ("monarch_cashflow_summary", args), ("monarch_spending_by_category", args),
                                ("monarch_income_by_category", args)):
                m.requests.clear()
                asyncio.run(mcp_server.mcp.call_tool(tool, {**extra, "entity_ids": ["101"],
                                                           "include_household": True}))
                self.assertEqual(m.requests[-1]["body"]["variables"]["filters"]["businessEntitySet"],
                                 {"businessEntityIds": ["101"], "includeUnassigned": True}, tool)
            r = str(asyncio.run(mcp_server.mcp.call_tool("monarch_cashflow_by_entity", args)))
            self.assertIn("household", r)
            self.assertIn("4700", r)
            with self.assertRaises(ToolError):  # unknown id is rejected before any data call
                asyncio.run(mcp_server.mcp.call_tool("monarch_cashflow_summary", {**args, "entity_ids": ["999"]}))

    def test_unavailable_and_unknown_entity_surface_real_messages(self):
        args = {"start_date": "2026-09-01", "end_date": "2026-09-30"}
        with MockMonarch() as m, mock.patch.object(keychain, "load", return_value=TOKEN):
            m.force_status = 502
            with self.assertRaises(ToolError) as ctx:
                asyncio.run(mcp_server.mcp.call_tool("monarch_list_transactions", args))
            msg = str(ctx.exception)
            self.assertNotEqual(msg.strip(), "Error executing tool monarch_list_transactions")
            self.assertIn("temporarily unavailable", msg)
            self.assertIn("retry the same call shortly", msg)

        with MockMonarch(), mock.patch.object(keychain, "load", return_value=TOKEN):
            with self.assertRaises(ToolError) as ctx:
                asyncio.run(mcp_server.mcp.call_tool("monarch_list_transactions",
                                                     {**args, "entity_ids": ["999"]}))
            msg = str(ctx.exception)
            self.assertNotEqual(msg.strip(), "Error executing tool monarch_list_transactions")
            self.assertIn("Unknown entity", msg)

    def test_input_validation(self):
        with mock.patch.object(keychain, "load", return_value=TOKEN):
            with self.assertRaises(ToolError):
                asyncio.run(mcp_server.mcp.call_tool("monarch_list_transactions", {
                    "start_date": "2026-09-01", "end_date": "2026-09-30", "account_ids": ["1; drop"]}))
            with self.assertRaises(ToolError):
                asyncio.run(mcp_server.mcp.call_tool("monarch_cashflow_summary", {
                    "start_date": "2026-09-01", "end_date": "2026-09-30", "entity_ids": ["Acme LLC"]}))

    def test_list_categories(self):
        with MockMonarch(), mock.patch.object(keychain, "load", return_value=TOKEN):
            r = str(asyncio.run(mcp_server.mcp.call_tool("monarch_list_categories", {})))
        self.assertIn("Restaurants", r)


@unittest.skipIf(mcp_server is None, "mcp extra not installed")
class McpWrites(unittest.TestCase):
    def setUp(self):
        mcp_server.enable_writes()
        self.addCleanup(mcp_server.disable_writes)

    def call(self, tool, args):
        return asyncio.run(mcp_server.mcp.call_tool(tool, args))

    def test_absent_by_default_present_when_enabled(self):
        mcp_server.disable_writes()
        names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
        self.assertFalse(names & WRITE_TOOLS)
        self.assertIn("monarch_list_categories", names)
        mcp_server.enable_writes()
        mcp_server.enable_writes()  # idempotent
        tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
        self.assertLessEqual(WRITE_TOOLS, set(tools))
        self.assertIn("monarch_list_categories", tools)
        for name in WRITE_TOOLS:
            a = tools[name].annotations
            self.assertEqual((a.read_only_hint, a.destructive_hint, a.idempotent_hint), (False, True, True), name)

    def test_run_registers_writes_only_when_asked(self):
        mcp_server.disable_writes()
        with mock.patch.object(mcp_server.mcp, "run"):
            mcp_server.run()
            self.assertFalse({t.name for t in asyncio.run(mcp_server.mcp.list_tools())} & WRITE_TOOLS)
            mcp_server.run(allow_writes=True)
        self.assertLessEqual(WRITE_TOOLS, {t.name for t in asyncio.run(mcp_server.mcp.list_tools())})

    def client_call(self, tool, args, answer=None, mode="auto"):
        """Call `tool` through an in-process MCP client. `answer` is the user's elicitation reply;
        None means the client doesn't support elicitation. Returns (result dict, questions asked)."""
        asked = []

        async def elicit(_ctx, params):
            asked.append(params.message)
            return answer

        async def go():
            kw = {} if answer is None else {"elicitation_callback": elicit}
            async with Client(mcp_server.mcp, mode=mode, **kw) as c:
                return await c.call_tool(tool, args)

        r = asyncio.run(go())
        self.assertFalse(r.is_error, r)
        return r.structured_content, asked

    def test_preview_never_asks(self):
        accept = types.ElicitResult(action="accept", content={})
        with MockMonarch() as m, mock.patch.object(keychain, "load", return_value=TOKEN):
            d, asked = self.client_call("monarch_set_transaction_category",
                                        {"transaction_id": "301", "category": "Restaurants"}, accept)
            self.assertEqual((asked, d["applied"]), ([], False))
            self.assertIn("Preview only", d["message"])
            # No change: nothing to confirm or send, even with apply=true.
            d, asked = self.client_call("monarch_set_transaction_category",
                                        {"transaction_id": "301", "category": "Groceries", "apply": True}, accept)
            self.assertEqual((asked, d["changed"]), ([], False))
            self.assertEqual(m.mutations(), [])

    def test_accept_applies_once(self):
        accept = types.ElicitResult(action="accept", content={})
        for mode in ("auto", "legacy"):  # 2026-07-28 input-required rounds, and mid-call elicitation
            with MockMonarch() as m, mock.patch.object(keychain, "load", return_value=TOKEN):
                d, asked = self.client_call("monarch_set_transaction_category",
                                            {"transaction_id": "301", "category": "Restaurants", "apply": True},
                                            accept, mode)
                self.assertTrue(d["applied"], mode)
                self.assertEqual(len(asked), 1, mode)
                for part in ("2026-09-02", "King Soopers", "-42.10", "Groceries -> Restaurants"):
                    self.assertIn(part, asked[0])
                d, asked = self.client_call("monarch_update_transaction_tags",
                                            {"transaction_id": "301", "add": ["Supplies"], "apply": True},
                                            accept, mode)
                self.assertTrue(d["applied"], mode)
                self.assertIn("Business -> Business, Supplies", asked[0])
                self.assertEqual([b["variables"] for b in m.mutations()],
                                 [{"input": {"id": "301", "category": "203"}},
                                  {"input": {"transactionId": "301", "tagIds": ["g1", "g2"]}}], mode)

    def test_decline_cancel_or_unticked_do_not_apply(self):
        for answer in (types.ElicitResult(action="decline"), types.ElicitResult(action="cancel"),
                       types.ElicitResult(action="decline")):
            with MockMonarch() as m, mock.patch.object(keychain, "load", return_value=TOKEN):
                d, asked = self.client_call("monarch_update_transaction_tags",
                                            {"transaction_id": "301", "remove": ["Business"], "apply": True},
                                            answer)
                self.assertEqual(len(asked), 1)
                self.assertEqual((d["applied"], d["message"]), (False, mcp_server.NOT_CONFIRMED))
                self.assertEqual(m.mutations(), [], answer)

    def test_client_without_elicitation_does_not_apply(self):
        with MockMonarch() as m, mock.patch.object(keychain, "load", return_value=TOKEN):
            d, _ = self.client_call("monarch_set_transaction_category",
                                    {"transaction_id": "301", "category": "Restaurants", "apply": True})
            self.assertFalse(d["applied"])
            self.assertIn("can't show a confirmation", d["message"])
            self.assertIn("monarch tx set-category 301", d["message"])
            # A direct in-process call has no client to ask either.
            self.call("monarch_set_transaction_category",
                      {"transaction_id": "301", "category": "Restaurants", "apply": True})
            self.assertEqual(m.mutations(), [])

    def test_write_input_validation(self):
        with MockMonarch() as m, mock.patch.object(keychain, "load", return_value=TOKEN):
            for args in ({"transaction_id": "30x", "category": "Restaurants", "apply": True},
                         {"transaction_id": "301", "category": "Nope", "apply": True}):
                with self.assertRaises(ToolError):
                    self.call("monarch_set_transaction_category", args)
            with self.assertRaises(ToolError):
                self.call("monarch_update_transaction_tags", {"transaction_id": "301", "apply": True})
            self.assertEqual(m.mutations(), [])
