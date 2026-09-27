import asyncio
import unittest
from unittest import mock

try:
    from mcp.server.mcpserver.exceptions import ToolError

    from monarch_money_cli import mcp_server
except ImportError:  # [mcp] extra not installed
    mcp_server = None

from monarch_money_cli import keychain
from tests._mock import TOKEN, MockMonarch


@unittest.skipIf(mcp_server is None, "mcp extra not installed")
class McpServer(unittest.TestCase):
    def test_all_tools_read_only(self):
        tools = asyncio.run(mcp_server.mcp.list_tools())
        self.assertEqual({t.name for t in tools}, {"monarch_list_accounts", "monarch_list_transactions",
                                                  "monarch_cashflow_summary", "monarch_spending_by_category",
                                                  "monarch_income_by_category", "monarch_list_entities",
                                                  "monarch_cashflow_by_entity", "monarch_budget_summary",
                                                  "monarch_list_goals", "monarch_list_recurring",
                                                  "monarch_list_holdings", "monarch_net_worth_history"})
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
