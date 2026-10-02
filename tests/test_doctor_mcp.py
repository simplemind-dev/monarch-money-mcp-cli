import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from monarch_money_cli import cli

try:
    import mcp.server.mcpserver  # noqa: F401
    HAVE_MCP = True
except ImportError:  # [mcp] extra not installed
    HAVE_MCP = False

TOOLS = {"monarch_list_accounts", "monarch_list_transactions", "monarch_cashflow_summary",
         "monarch_spending_by_category", "monarch_income_by_category", "monarch_list_entities",
         "monarch_cashflow_by_entity", "monarch_budget_summary", "monarch_list_goals", "monarch_list_recurring",
         "monarch_list_holdings", "monarch_net_worth_history", "monarch_list_categories"}


class RegisteredTools(unittest.TestCase):
    def test_read_from_source_without_importing_mcp(self):
        self.assertEqual(cli._registered_mcp_tools(), TOOLS)


@unittest.skipUnless(HAVE_MCP, "mcp extra not installed")
class McpServerHandshake(unittest.TestCase):
    def test_real_subprocess_lists_every_tool(self):
        status, detail = cli._check_mcp_server()
        self.assertEqual(status, "ok", detail)
        self.assertIn("13/13 tools listed", detail)

    def test_missing_tool_fails(self):
        with mock.patch.object(cli, "_registered_mcp_tools", return_value=TOOLS | {"monarch_extra"}):
            status, detail = cli._check_mcp_server()
        self.assertEqual(status, "fail")
        self.assertIn("monarch_extra", detail)


class McpServerFailures(unittest.TestCase):
    def fake_server(self, script):
        """Run `script` instead of `python -m monarch_money_cli mcp`."""
        real = cli.subprocess.Popen
        return mock.patch.object(cli.subprocess, "Popen",
                                 side_effect=lambda _argv, **kw: real([sys.executable, "-c", script], **kw))

    def test_timeout_kills_child(self):
        with self.fake_server("import time; time.sleep(60)"):
            status, detail = cli._check_mcp_server(timeout=0.5)
        self.assertEqual(status, "fail")
        self.assertIn("TimeoutError", detail)

    def test_early_exit_reports_stderr(self):
        with self.fake_server("import sys; sys.stderr.write('boom happened\\n'); sys.exit(1)"):
            status, detail = cli._check_mcp_server(timeout=5)
        self.assertEqual(status, "fail")
        self.assertIn("EOFError", detail)
        self.assertIn("boom happened", detail)


class McpClients(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.monarch = self.executable("bin/monarch")
        patcher = mock.patch.object(cli, "_current_monarch",
                                    return_value={os.path.realpath(self.monarch)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def executable(self, rel):
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#!/bin/sh\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
        return str(p)

    def config(self, name, data):
        p = self.dir / name
        p.write_text(data if isinstance(data, str) else json.dumps(data))
        return p

    def check(self, code=None, desktop=None):
        configs = []
        if code is not None:
            configs.append(("claude_code", self.config("claude.json", code)))
        if desktop is not None:
            configs.append(("desktop", self.config("desktop.json", desktop)))
        return cli._check_mcp_clients(configs)

    def stdio(self, command=None, **extra):
        return {"type": "stdio", "command": command or self.monarch, "args": ["mcp"], **extra}

    def test_healthy(self):
        rows = self.check({"mcpServers": {"monarch": self.stdio()}})
        self.assertEqual(rows, [("ok", "1 valid registration")])

    def test_no_files_hints_add(self):
        rows = cli._check_mcp_clients([("claude_code", self.dir / "nope.json"),
                                       ("desktop", self.dir / "nope2.json")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "warn")
        self.assertIn("claude mcp add monarch -s user --", rows[0][1])

    def test_malformed_files_are_skipped(self):
        rows = self.check("{not json", desktop="[]")
        self.assertEqual([s for s, _ in rows], ["warn"])
        self.assertIn("no working monarch registration", rows[0][1])

    def test_discontinued_connector_in_project(self):
        rows = self.check({"mcpServers": {"monarch": self.stdio()},
                           "projects": {"/p/My Project": {"mcpServers": {
                               "monarch": {"type": "http", "url": "https://api.monarch.com/mcp?k=secret"}}}}})
        details = [d for s, d in rows if s == "warn"]
        self.assertTrue(any("discontinued Monarch connector" in d and
                            "cd '/p/My Project' && claude mcp remove monarch -s local" in d
                            for d in details), details)
        # One problem, one row: the cross-scope conflict is folded into the stale-connector row.
        self.assertFalse(any("registered differently across scopes" in d for d in details), details)
        stale = [d for d in details if "discontinued Monarch connector" in d]
        self.assertEqual(len(stale), 1, details)
        self.assertIn(f"also registered as {self.monarch} in user scope", stale[0])
        self.assertTrue(stale[0].endswith("claude mcp remove monarch -s local"), stale[0])
        self.assertNotIn("secret", " ".join(details))

    def test_two_stale_urls_have_no_separate_conflict_row(self):
        rows = self.check({"mcpServers": {"monarch": {"type": "http", "url": "https://api.monarch.com/mcp"}},
                           "projects": {"/p": {"mcpServers": {"monarch": {
                               "type": "http", "url": "https://api.monarch.com/mcp/v2"}}}}})
        details = [d for _, d in rows]
        self.assertEqual(sum("discontinued Monarch connector" in d for d in details), 2, details)
        self.assertFalse(any("registered differently" in d or "also registered" in d for d in details), details)

    def test_discontinued_connector_user_and_desktop(self):
        http = {"type": "http", "url": "https://api.monarch.com/mcp"}
        rows = self.check({"mcpServers": {"Monarch": http}}, desktop={"mcpServers": {"monarch": {
            "url": "https://api.monarch.com/mcp"}}})
        details = " | ".join(d for _, d in rows)
        self.assertIn("claude mcp remove Monarch -s user", details)
        self.assertIn("delete 'monarch' from mcpServers in", details)
        self.assertIn("no working monarch registration", details)

    def test_command_not_found(self):
        rows = self.check({"mcpServers": {"monarch": self.stdio("definitely-not-a-cmd-xyz")}})
        self.assertTrue(any("not found or not executable" in d for _, d in rows), rows)
        rows = self.check({"mcpServers": {"monarch": self.stdio(str(self.dir / "missing/monarch"))}})
        self.assertTrue(any("not found or not executable" in d for _, d in rows), rows)

    def test_relative_command(self):
        rows = self.check({"projects": {"/p": {"mcpServers": {"monarch": self.stdio(".venv/bin/monarch")}}}})
        self.assertTrue(any("relative command" in d for _, d in rows), rows)

    def test_different_install(self):
        other = self.executable("other/bin/monarch")
        rows = self.check({"mcpServers": {"monarch": self.stdio(other)}})
        self.assertTrue(any("different install" in d for _, d in rows), rows)

    def test_different_python(self):
        py = self.executable("otherpy/bin/python")
        rows = self.check({"mcpServers": {"mm": {"command": py, "args": ["-m", "monarch_money_cli", "mcp"]}}})
        self.assertTrue(any("different Python" in d for _, d in rows), rows)
        rows = self.check({"mcpServers": {"mm": {"command": sys.executable,
                                                 "args": ["-m", "monarch_money_cli", "mcp"]}}})
        self.assertEqual(rows[0][0], "ok", rows)

    def test_duplicate_names_with_different_commands(self):
        other = self.executable("bin2/monarch")
        with mock.patch.object(cli, "_current_monarch",
                               return_value={os.path.realpath(self.monarch), os.path.realpath(other)}):
            rows = self.check({"mcpServers": {"monarch": self.stdio()},
                               "projects": {"/p": {"mcpServers": {"monarch": self.stdio(other)}}}})
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("registered differently across scopes", rows[0][1])

    def test_unrelated_entries_and_env_never_printed(self):
        rows = self.check({"mcpServers": {
            "monarch": self.stdio(env={"MONARCH_TOKEN": "s3cr3t-value"}),
            "github": {"command": "definitely-not-a-cmd-xyz", "env": {"GH": "ghp_x"}}}})
        self.assertEqual(rows, [("ok", "1 valid registration")])
        self.assertNotIn("s3cr3t-value", str(rows))
