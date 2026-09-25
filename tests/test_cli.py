import contextlib
import csv
import io
import json
import os
import re
import unittest
from datetime import date
from unittest import mock

from monarch_money_cli import cli, keychain, service
from tests._mock import TOKEN, MockMonarch


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class Parser(unittest.TestCase):
    def parse(self, *argv):
        return cli.build_parser().parse_args(list(argv))

    def test_command_shapes(self):
        self.assertIs(self.parse("auth").func, cli.cmd_auth_login)
        self.assertIs(self.parse("auth", "status").func, cli.cmd_auth_status)
        self.assertIs(self.parse("account", "list").func, cli.cmd_accounts_list)
        self.assertIs(self.parse("accounts").func, cli.cmd_accounts_list)
        a = self.parse("transactions", "2026-09-01", "2026-09-30", "--search", "costco")
        self.assertEqual((a.start, a.end, a.search), ("2026-09-01", "2026-09-30", "costco"))
        self.assertIs(self.parse("tx").func, cli.cmd_transactions)
        self.assertIs(self.parse("income").func, cli.cmd_income)
        self.assertIs(self.parse("doctor").func, cli.cmd_doctor)
        self.assertEqual(self.parse("spending").output, "table")
        self.assertEqual(self.parse("accounts").output, "table")
        self.assertEqual(self.parse("cashflow", "--json").output, "json")
        self.assertIs(self.parse("entities").func, cli.cmd_entities)
        self.assertEqual(self.parse("accounts").entity, [])
        self.assertEqual(self.parse("tx", "--entity", "1", "--entity", "household").entity, ["1", "household"])
        self.assertTrue(self.parse("cashflow", "--by-entity").by_entity)


@mock.patch.object(keychain, "load", return_value=TOKEN)
class Commands(unittest.TestCase):
    def test_accounts_table_and_json(self, _):
        with MockMonarch():
            code, out, _ = run("account", "list")
            self.assertEqual(code, 0)
            self.assertIn("Checking", out)
            self.assertIn("1,200.50", out)
            code, out, _ = run("accounts", "list", "--json")
            self.assertEqual(json.loads(out)["count"], 1)

    def test_transactions(self, _):
        with MockMonarch():
            code, out, _ = run("transactions", "2026-09-01", "2026-09-30", "--limit", "2")
            self.assertEqual(code, 0)
            self.assertIn("King Soopers", out)
            self.assertNotIn("secret note", out)

    def test_cashflow_and_spending(self, _):
        with MockMonarch():
            out = run("cashflow", "2026-09-01", "2026-09-30")[1]
            self.assertIn("94.0%", out)
            self.assertIn("Income by category", out)
            self.assertIn("Expenses by category", out)
            self.assertNotIn("Credit Card Payment", out)
            self.assertEqual(json.loads(run("spending", "--json")[1])["categories"][0]["category"], "Rent")

    def test_spending_table_expenses_only_with_total(self, _):
        with MockMonarch():
            code, out, _ = run("spending")
            self.assertEqual(code, 0)
            lines = out.splitlines()
            self.assertTrue(lines[2].startswith("Rent"))
            self.assertRegex(lines[-1], r"^TOTAL\s+-1,800.00$")
            for other in ("Paycheck", "Interest", "Credit Card Payment"):
                self.assertNotIn(other, out)

    def test_income_table_with_total(self, _):
        with MockMonarch():
            code, out, _ = run("income", "2026-09-01", "2026-09-30")
            self.assertEqual(code, 0)
            lines = out.splitlines()
            self.assertTrue(lines[2].startswith("Paycheck"))
            self.assertRegex(lines[-1], r"^TOTAL\s+5,012.50$")
            self.assertNotIn("Groceries", out)

    def test_output_json_alias(self, _):
        with MockMonarch():
            for cmd in (["accounts", "list"], ["tx"], ["cashflow"], ["spending"], ["income"]):
                self.assertEqual(json.loads(run(*cmd, "--json")[1]), json.loads(run(*cmd, "--output", "json")[1]))

    def test_output_csv(self, _):
        with MockMonarch():
            rows = list(csv.DictReader(io.StringIO(run("spending", "--output", "csv")[1])))
            self.assertEqual([r["category"] for r in rows], ["Rent", "Groceries"])
            self.assertEqual(rows[0]["total"], "-1500")
            rows = list(csv.DictReader(io.StringIO(run("accounts", "list", "--output", "csv")[1])))
            self.assertEqual(rows[0]["name"], "Checking")
            self.assertNotIn("mask", rows[0])
            rows = list(csv.DictReader(io.StringIO(run("tx", "--output", "csv")[1])))
            self.assertEqual(rows[0]["merchant"], "King Soopers")
            self.assertNotIn("secret note", str(rows))
            rows = list(csv.DictReader(io.StringIO(run("cashflow", "--output", "csv")[1])))
            self.assertEqual(list(rows[0]), ["section", "name", "value"])
            self.assertEqual([r["section"] for r in rows],
                             ["summary"] * 4 + ["income", "income", "expense", "expense"])
            self.assertEqual(rows[-1]["name"], "Groceries")

    def test_csv_neutralises_formulas(self, _):
        self.assertEqual(cli._csv_cell("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(cli._csv_cell("@x"), "'@x")
        self.assertEqual(cli._csv_cell(-42.1), -42.1)
        self.assertEqual(cli._csv_cell("Rent"), "Rent")

    def test_errors(self, _):
        self.assertEqual(run("tx", "--account", "1;drop")[0], cli.EXIT_USAGE)
        self.assertEqual(run("tx", "2026-09-30", "2026-09-01")[0], cli.EXIT_ERROR)
        self.assertEqual(run("tx", "--limit", "5000")[0], cli.EXIT_ERROR)
        self.assertEqual(run("spending", "--output", "xml")[0], cli.EXIT_USAGE)

    def test_monarch_unavailable_surfaces_retry_message(self, _):
        for status in (502, 503, 504, 429):
            with MockMonarch() as m:
                m.force_status = status
                code, out, err = run("tx")
            self.assertEqual(code, cli.EXIT_ERROR, status)
            self.assertIn("temporarily unavailable", err, status)
            self.assertIn("Try again in a minute", err, status)
            self.assertIn(f"HTTP {status}", err, status)

    def test_date_options_on_every_ranged_command(self, _):
        def dates(m):
            v = m.requests[-1]["body"]["variables"]
            f = v.get("filters", v)
            return f.get("startDate"), f.get("endDate")
        cases = [(["--month", "2024-02"], ("2024-02-01", "2024-02-29")),
                 (["--year", "2025"], ("2025-01-01", "2025-12-31")),
                 (["--from", "2026-01-01", "--to", "2026-01-31"], ("2026-01-01", "2026-01-31")),
                 (["2026-03-01", "2026-03-31"], ("2026-03-01", "2026-03-31"))]
        for cmd in (["tx"], ["cashflow"], ["spending"], ["income"], ["cashflow", "--by-entity"],
                    ["spending", "--entity", "household"]):
            for opts, want in cases:
                with MockMonarch() as m:
                    code, _, err = run(*cmd, *opts)
                    self.assertEqual(code, 0, (cmd, opts, err))
                    self.assertEqual(dates(m), want, (cmd, opts))
            with MockMonarch():
                d = json.loads(run(*cmd, "--month", "2024-02", "--json")[1])
                self.assertEqual((d["start_date"], d["end_date"]), ("2024-02-01", "2024-02-29"), cmd)
        with mock.patch.object(service, "date", wraps=date) as d, MockMonarch() as m:
            d.today.return_value = date(2026, 3, 15)
            for opts, want in [(["--ytd"], ("2026-01-01", "2026-03-15")), (["--last-month"], ("2026-02-01", "2026-02-28")),
                               (["--days", "7"], ("2026-03-09", "2026-03-15")), (["--from", "2026-03-01"], ("2026-03-01", "2026-03-15"))]:
                self.assertEqual(run("spending", *opts)[0], 0, opts)
                self.assertEqual(dates(m), want, opts)

    def test_date_option_errors(self, _):
        for argv in (["spending", "--ytd", "--last-month"], ["tx", "--month", "2026-01", "--year", "2026"],
                     ["cashflow", "2026-01-01", "2026-01-31", "--days", "7"], ["income", "--to", "2026-01-31"],
                     ["cashflow", "--by-entity", "--from", "2026-01-01", "--ytd"], ["tx", "--days", "x"]):
            code, _, err = run(*argv)
            self.assertEqual(code, cli.EXIT_USAGE, argv)
            self.assertTrue(err.strip(), argv)
        self.assertIn("--ytd and --last-month", run("spending", "--ytd", "--last-month")[2])
        self.assertIn("--to needs --from", run("income", "--to", "2026-01-31")[2])
        for argv in (["spending", "--month", "2026-13"], ["tx", "--days", "0"], ["income", "--year", "26"],
                     ["cashflow", "--from", "2026-02-30"]):
            self.assertEqual(run(*argv)[0], cli.EXIT_ERROR, argv)

    def test_entities_command(self, _):
        with MockMonarch():
            code, out, _ = run("entities")
            self.assertEqual(code, 0)
            self.assertRegex(out.splitlines()[0], r"^ID\s+NAME\s+STRUCTURE\s+ACCOUNTS\s+TRANSACTIONS$")
            self.assertIn("Acme LLC", out)
            self.assertNotIn("secret entity note", out)
            rows = list(csv.DictReader(io.StringIO(run("entities", "--output", "csv")[1])))
            self.assertEqual(list(rows[0]), ["id", "name", "structure", "accounts_count", "transactions_count"])
            self.assertEqual(json.loads(run("entities", "--json")[1])["count"], 2)

    def test_entity_scope_on_every_data_command(self, _):
        want = {"businessEntityIds": ["101"], "includeUnassigned": True}
        for cmd in (["accounts", "list"], ["tx"], ["cashflow"], ["spending"], ["income"]):
            with MockMonarch() as m:
                code, out, err = run(*cmd, "--entity", "acme llc", "--entity", "household")
                self.assertEqual(code, 0, err)
                self.assertEqual(m.requests[0]["body"]["operationName"], "Common_GetBusinessEntities")
                self.assertEqual(m.requests[1]["body"]["variables"]["filters"]["businessEntitySet"], want)
                self.assertIn("Acme LLC, household", out.splitlines()[0])
                d = json.loads(run(*cmd, "--entity", "101", "--json")[1])
                self.assertEqual(d["entity_scope"]["entity_ids"], ["101"])
                self.assertNotIn("Entity scope", run(*cmd)[1])

    def test_entity_errors(self, _):
        with MockMonarch():
            code, _, err = run("spending", "--entity", "Globex")
            self.assertEqual(code, cli.EXIT_ERROR)
            self.assertIn("'Acme LLC' (id 101)", err)
            self.assertIn("ambiguous", run("tx", "--entity", "acme")[2])
        self.assertEqual(run("cashflow", "--by-entity", "--entity", "household")[0], cli.EXIT_USAGE)
        self.assertEqual(run("entities", "--entity", "101")[0], cli.EXIT_USAGE)

    def test_cashflow_by_entity(self, _):
        with MockMonarch():
            code, out, _ = run("cashflow", "2026-09-01", "2026-09-30", "--by-entity")
            self.assertEqual(code, 0)
            lines = out.splitlines()
            self.assertEqual(lines[0], "Cash flow by entity 2026-09-01 to 2026-09-30")
            self.assertTrue(lines[4].startswith("Acme LLC"))
            self.assertTrue(lines[5].startswith("household"))
            self.assertRegex(lines[-1], r"^TOTAL\s+5,000.00\s+-300.00\s+4,700.00\s+94.0%\s+10$")
            rows = list(csv.DictReader(io.StringIO(run("cashflow", "--by-entity", "--output", "csv")[1])))
            self.assertEqual([r["entity"] for r in rows], ["Acme LLC", "household", "TOTAL"])
            d = json.loads(run("cashflow", "--by-entity", "--json")[1])
            self.assertEqual(d["total"]["savings"], 4700)

    def test_mcp_without_extra_shows_reinstall_hint(self, _):
        with mock.patch.dict("sys.modules", {"monarch_money_cli.mcp_server": None}):
            code, out, err = run("mcp")
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertEqual(out, "")  # stdout is the MCP transport
        self.assertIn(cli.MCP_REINSTALL, err)
        self.assertNotIn("pip install", err)


@mock.patch.object(cli, "_check_mcp_clients", return_value=[("ok", "1 valid registration")])
@mock.patch.object(cli, "_check_mcp_server", return_value=("ok", "stdio handshake ok; 5/5 tools listed"))
@mock.patch.object(cli.shutil, "which", return_value="/usr/local/bin/monarch")
@mock.patch.object(keychain, "token_state", return_value="ok")
class Doctor(unittest.TestCase):
    def doctor(self, *argv):
        code, out, err = run("doctor", *argv)
        self.assertNotIn(TOKEN, out + err)
        return code, out

    def checks(self, out):
        return {c["check"]: c for c in json.loads(out)["checks"]}

    @mock.patch.object(keychain, "load", return_value=TOKEN)
    def test_all_ok(self, *_):
        with MockMonarch(), mock.patch.object(cli.importlib, "import_module"):
            code, out = self.doctor("--json")
        self.assertEqual(code, cli.EXIT_OK)
        checks = self.checks(out)
        self.assertEqual(set(checks), {"keychain_token", "api", "mcp_extra", "mcp_server", "mcp_clients", "on_path"})
        self.assertTrue(all(c["status"] == "ok" for c in checks.values()))
        self.assertRegex(checks["api"]["detail"], r"^2 accounts in \d+ ms$")

    @mock.patch.object(keychain, "load", return_value="b" * 64)
    def test_expired_token_is_auth_failure(self, *_):
        with MockMonarch(), mock.patch.object(cli.importlib, "import_module"):
            code, out = self.doctor()
        self.assertEqual(code, cli.EXIT_AUTH)
        self.assertIn("expired or revoked", out)

    @mock.patch.object(keychain, "load", return_value=None)
    def test_not_logged_in(self, _load, state, *_):
        state.return_value = "missing"
        with mock.patch.object(cli.importlib, "import_module"):
            code, out = self.doctor("--output", "csv")
        self.assertEqual(code, cli.EXIT_AUTH)
        rows = {r["check"]: r for r in csv.DictReader(io.StringIO(out))}
        self.assertEqual(rows["keychain_token"]["status"], "fail")
        self.assertIn("skipped", rows["api"]["detail"])

    @mock.patch.object(keychain, "load", return_value=TOKEN)
    def test_cli_only_install_warns_but_passes(self, _load, _state, which, server, clients):
        which.return_value = None
        with MockMonarch(), mock.patch.object(cli.importlib, "import_module", side_effect=ImportError):
            code, out = self.doctor("--json")
        self.assertEqual(code, cli.EXIT_OK)
        d = json.loads(out)
        self.assertTrue(d["ok"])
        checks = self.checks(out)
        self.assertEqual(checks["mcp_extra"]["status"], "warn")
        self.assertIn(cli.MCP_REINSTALL, checks["mcp_extra"]["detail"])
        self.assertNotIn("pip install", checks["mcp_extra"]["detail"])
        self.assertEqual(checks["on_path"]["status"], "warn")
        self.assertNotIn("mcp_server", checks)  # MCP checks are skipped without the extra
        server.assert_not_called()
        clients.assert_not_called()

    @mock.patch.object(keychain, "load", return_value=TOKEN)
    def test_api_error_is_general_failure(self, *_):
        with MockMonarch(), mock.patch.object(cli, "_check_api", return_value=("fail", "Network error", False)), \
             mock.patch.object(cli.importlib, "import_module"):
            code, out = self.doctor()
        self.assertEqual(code, cli.EXIT_ERROR)

    @mock.patch.object(keychain, "load", return_value=TOKEN)
    def test_api_unavailable_reports_retry_message(self, *_):
        with MockMonarch() as m, mock.patch.object(cli.importlib, "import_module"):
            m.force_status = 502
            code, out = self.doctor("--json")
        self.assertEqual(code, cli.EXIT_ERROR)
        api = self.checks(out)["api"]
        self.assertEqual(api["status"], "fail")
        self.assertIn("temporarily unavailable", api["detail"])
        self.assertIn("Try again in a minute", api["detail"])


    @mock.patch.object(keychain, "load", return_value=TOKEN)
    def test_mcp_server_failure_is_general_failure(self, _load, _state, _which, server, _clients):
        server.return_value = ("fail", "TimeoutError: no response within 10 s")
        with MockMonarch(), mock.patch.object(cli.importlib, "import_module"):
            code, out = self.doctor("--json")
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertEqual(self.checks(out)["mcp_server"]["status"], "fail")

    @mock.patch.object(keychain, "load", return_value=TOKEN)
    def test_mcp_client_problems_only_warn(self, _load, _state, _which, _server, clients):
        clients.return_value = [("warn", "problem one"), ("warn", "problem two")]
        with MockMonarch(), mock.patch.object(cli.importlib, "import_module"):
            code, out = self.doctor("--json")
        self.assertEqual(code, cli.EXIT_OK)
        rows = [c for c in json.loads(out)["checks"] if c["check"] == "mcp_clients"]
        self.assertEqual([r["detail"] for r in rows], ["problem one", "problem two"])


class _TTY(io.StringIO):
    def isatty(self):
        return True


ANSI = re.compile(r"\033\[[0-9;]*m")


@mock.patch.object(cli, "_check_mcp_clients", return_value=[("warn", "problem one")])
@mock.patch.object(cli, "_check_mcp_server", return_value=("fail", "boom"))
@mock.patch.object(cli.shutil, "which", return_value="/usr/local/bin/monarch")
@mock.patch.object(keychain, "token_state", return_value="ok")
@mock.patch.object(cli, "_check_api", return_value=("ok", "2 accounts in 5 ms", False))
class DoctorColor(unittest.TestCase):
    def doctor(self, *argv, tty=True, no_color=None):
        out = _TTY() if tty else io.StringIO()
        env = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
        if no_color is not None:
            env["NO_COLOR"] = no_color
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), \
             mock.patch.dict(os.environ, env, clear=True), mock.patch.object(cli.importlib, "import_module"):
            cli.main(["doctor", *argv])
        return out.getvalue()

    def test_status_colored_on_tty(self, *_):
        out = self.doctor()
        self.assertIn("\033[32mok\033[0m", out)
        self.assertIn("\033[38;5;214mwarn\033[0m", out)
        self.assertIn("\033[31mfail\033[0m", out)
        header = out.splitlines()[0]
        self.assertNotIn("\033", header)
        row = next(l for l in out.splitlines() if "problem one" in l)
        self.assertEqual(ANSI.findall(row), ["\033[38;5;214m", "\033[0m"])  # only the STATUS cell

    def test_alignment_identical_after_stripping(self, *_):
        self.assertEqual(ANSI.sub("", self.doctor()), self.doctor(tty=False))

    def test_plain_when_not_tty(self, *_):
        self.assertNotIn("\033", self.doctor(tty=False))

    def test_plain_with_no_color(self, *_):
        self.assertNotIn("\033", self.doctor(no_color="1"))
        self.assertIn("\033[", self.doctor(no_color=""))  # empty NO_COLOR does not disable color

    def test_plain_for_json_and_csv(self, *_):
        out = self.doctor("--json")
        self.assertNotIn("\033", out)
        self.assertEqual(json.loads(out)["checks"][0]["status"], "ok")
        self.assertNotIn("\033", self.doctor("--output", "csv"))


class NotLoggedIn(unittest.TestCase):
    def test_exit_code_3(self):
        with mock.patch.object(keychain, "load", return_value=None):
            code, _, err = run("accounts", "list")
            self.assertEqual(code, cli.EXIT_AUTH)
            self.assertIn("monarch auth login", err)
