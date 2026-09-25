"""`monarch` command line interface. Standard library only."""
from __future__ import annotations

import argparse
import ast
import asyncio
import csv
import getpass
import importlib
import importlib.util
import json
import os
import queue
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from monarch_money_cli import __version__, keychain, service
from monarch_money_cli.client import (
    AuthRequired,
    CaptchaRequired,
    MFARequired,
    MonarchClient,
    MonarchError,
    login,
)

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_AUTH = 0, 1, 2, 3
# Shown when the [mcp] extra is missing. Use the installer that installed monarch (uv tool or pipx).
MCP_REINSTALL = ("uv tool install --force "
                 "'monarch-money-mcp-cli[mcp] @ git+https://github.com/simplemind-dev/monarch-money-mcp-cli'")
CLI_MAX_TRANSACTIONS = 1000


# ---------- output ----------

def _money(v: Any) -> str:
    return "" if v is None else f"{v:,.2f}"


def _table(rows: list[dict[str, Any]], cols: list[tuple[str, str]], right: set[str] = frozenset(),
           style: Callable[[str, str], str] | None = None) -> str:
    """Aligned text table. `style(key, value)` decorates data cells after padding, so ANSI codes
    never affect column widths."""
    if not rows:
        return "(none)"
    cells = [[("" if r.get(k) is None else str(r.get(k))) for k, _ in cols] for r in rows]
    widths = [max(len(h), *(len(c[i]) for c in cells)) for i, (_, h) in enumerate(cols)]

    def line(vals: list[str], styled: bool = False) -> str:
        out = []
        for i, (v, w) in enumerate(zip(vals, widths, strict=True)):
            pad = " " * (w - len(v))
            text = style(cols[i][0], v) if styled and style and v else v
            out.append(pad + text if cols[i][0] in right else text + pad)
        return "  ".join(out).rstrip()

    return "\n".join([line([h for _, h in cols]), line(["-" * w for w in widths]),
                      *(line(c, styled=True) for c in cells)])


STATUS_COLORS = {"ok": "32", "warn": "38;5;214", "fail": "31"}  # green, amber (256-color), red


def _use_color(args: argparse.Namespace) -> bool:
    """Color only human tables on a terminal, and never when NO_COLOR is set (no-color.org)."""
    return args.output == "table" and sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _status_style(key: str, value: str) -> str:
    code = STATUS_COLORS.get(value) if key == "status" else None
    return f"\033[{code}m{value}\033[0m" if code else value


def _csv_cell(v: Any) -> Any:
    # Merchant and category names are untrusted: stop spreadsheets reading them as formulas.
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _emit(args: argparse.Namespace, payload: dict[str, Any], human: str,
          records: list[dict[str, Any]], fields: list[str]) -> None:
    """Print `payload` as JSON, `records` as CSV (header + one row each), or the `human` table."""
    if args.output == "json":
        print(json.dumps(payload, indent=2))
    elif args.output == "csv":
        w = csv.writer(sys.stdout, lineterminator="\n")
        w.writerow(fields)
        w.writerows([_csv_cell(r.get(f)) for f in fields] for r in records)
    else:
        print(human)


def _category_table(cats: list[dict[str, Any]], total: float) -> str:
    if not cats:
        return "(none)"
    rows = [{**r, "total": _money(r["total"])} for r in cats]
    rows.append({"category": "TOTAL", "total": _money(total)})
    return _table(rows, [("category", "CATEGORY"), ("total", "TOTAL")], {"total"})


# ---------- auth ----------

def _verify_and_store(token: str) -> None:
    if not keychain.valid_token(token):
        raise MonarchError("Token has an unexpected format; not storing it.")
    asyncio.run(MonarchClient(token).accounts())  # fails loudly if the token is bad
    keychain.store(token)
    print("Logged in. Session token stored in the macOS Keychain.")


def cmd_auth_login(args: argparse.Namespace) -> int:
    email = input("Monarch email: ").strip()
    password = getpass.getpass("Monarch password: ")
    try:
        try:
            token = login(email, password)
        except MFARequired:
            token = login(email, password, mfa_code=getpass.getpass("MFA code: ").strip())
    finally:
        del password
    _verify_and_store(token)
    return EXIT_OK


def cmd_auth_paste(args: argparse.Namespace) -> int:
    print("In app.monarch.com open DevTools > Network, select any /graphql request,")
    print("and copy the value after 'Authorization: Token '.")
    token = getpass.getpass("Token (hidden): ").strip().removeprefix("Token ").strip()
    if not token:
        print("No token entered.", file=sys.stderr)
        return EXIT_USAGE
    _verify_and_store(token)
    return EXIT_OK


def cmd_auth_status(args: argparse.Namespace) -> int:
    if keychain.load():
        print("Logged in (token present in Keychain).")
        return EXIT_OK
    print("Not logged in. Run: monarch auth login")
    return EXIT_AUTH


def cmd_auth_logout(args: argparse.Namespace) -> int:
    if keychain.delete():
        print("Token removed. Also sign out other sessions in Monarch settings.")
    else:
        print("No token stored.")
    return EXIT_OK


# ---------- data ----------

def _scope_line(scope: dict[str, Any] | None) -> str:
    """Table header naming the --entity scope; empty when unscoped."""
    return "" if scope is None else f"Entity scope: {service.scope_label(scope)}\n\n"


def cmd_entities(args: argparse.Namespace) -> int:
    data = asyncio.run(service.entities(service.client()))
    _emit(args, data, _table(data["entities"], [("id", "ID"), ("name", "NAME"), ("structure", "STRUCTURE"),
                                                ("accounts_count", "ACCOUNTS"),
                                                ("transactions_count", "TRANSACTIONS")],
                             {"accounts_count", "transactions_count"}),
          data["entities"], ["id", "name", "structure", "accounts_count", "transactions_count"])
    return EXIT_OK


def cmd_accounts_list(args: argparse.Namespace) -> int:
    async def fetch() -> dict[str, Any]:
        c = service.client()
        return await service.accounts(c, include_hidden=args.all,
                                      scope=await service.resolve_entities(c, args.entity))

    data = asyncio.run(fetch())
    rows = [{**a, "balance": _money(a["balance"])} for a in data["accounts"]]
    _emit(args, data, _scope_line(data.get("entity_scope"))
          + _table(rows, [("id", "ID"), ("name", "NAME"), ("type", "TYPE"),
                          ("subtype", "SUBTYPE"), ("balance", "BALANCE")], {"balance"}),
          data["accounts"], ["id", "name", "type", "subtype", "balance", "is_asset", "in_net_worth"])
    return EXIT_OK


def cmd_transactions(args: argparse.Namespace) -> int:
    start, end = _range(args)
    if not 1 <= args.limit <= CLI_MAX_TRANSACTIONS:
        raise ValueError(f"--limit must be between 1 and {CLI_MAX_TRANSACTIONS}.")

    async def fetch() -> dict[str, Any]:
        c = service.client()
        scope = await service.resolve_entities(c, args.entity)
        got: list[dict[str, Any]] = []
        offset, total = args.offset, 0
        while len(got) < args.limit:
            page = await service.transactions(c, start, end, args.search, args.account,
                                              min(service.MAX_PAGE, args.limit - len(got)), offset, scope)
            got += page["transactions"]
            total = page["total"]
            offset += page["returned"]
            if not page["has_more"] or page["returned"] == 0:
                break
        out = {"start_date": start, "end_date": end, "total": total, "offset": args.offset,
               "returned": len(got), "has_more": offset < total, "transactions": got}
        if scope is not None:
            out["entity_scope"] = scope
        return out

    data = asyncio.run(fetch())
    rows = [{**t, "amount": _money(t["amount"]), "pending": "yes" if t["pending"] else ""}
            for t in data["transactions"]]
    human = _table(rows, [("date", "DATE"), ("merchant", "MERCHANT"), ("category", "CATEGORY"),
                          ("account", "ACCOUNT"), ("amount", "AMOUNT"), ("pending", "PENDING")], {"amount"})
    human = _scope_line(data.get("entity_scope")) + human
    human += f"\n\n{data['returned']} of {data['total']} transactions, {start} to {end}"
    if data["has_more"]:
        human += " (use --limit or --offset for more)"
    _emit(args, data, human, data["transactions"],
          ["id", "date", "merchant", "category", "account", "amount", "pending"])
    return EXIT_OK


def _rate(v: float | None) -> str:
    return "" if v is None else f"{v * 100:.1f}%"


def _cmd_cashflow_by_entity(args: argparse.Namespace, start: str, end: str) -> int:
    d = asyncio.run(service.cashflow_by_entity(service.client(), start, end))
    rows = [*d["entities"], {"entity": "TOTAL", **d["total"]}]
    shown = [{**r, **{k: _money(r[k]) for k in ("income", "expenses", "savings")},
              "savings_rate": _rate(r["savings_rate"])} for r in rows]
    human = f"Cash flow by entity {start} to {end}\n\n" + _table(
        shown, [("entity", "ENTITY"), ("income", "INCOME"), ("expenses", "EXPENSES"), ("savings", "SAVINGS"),
                ("savings_rate", "RATE"), ("transactions", "TXNS")],
        {"income", "expenses", "savings", "savings_rate", "transactions"})
    _emit(args, d, human, rows, ["entity_id", "entity", "income", "expenses", "savings", "savings_rate",
                                 "transactions"])
    return EXIT_OK


def cmd_cashflow(args: argparse.Namespace) -> int:
    start, end = _range(args)
    if args.by_entity:
        return _cmd_cashflow_by_entity(args, start, end)

    async def fetch() -> dict[str, Any]:
        c = service.client()
        return await service.cashflow_summary(c, start, end, await service.resolve_entities(c, args.entity))

    d = asyncio.run(fetch())
    inc, exp = d["income_categories"], d["expense_categories"]
    rate = _rate(d["savings_rate"])
    scope = d.get("entity_scope")
    title = f"Cash flow {start} to {end}" + (f" (entities: {service.scope_label(scope)})" if scope else "")
    human = "\n".join([title,
                       f"  Income:       {_money(d['income']):>14}",
                       f"  Expenses:     {_money(d['expenses']):>14}",
                       f"  Savings:      {_money(d['savings']):>14}",
                       f"  Savings rate: {rate:>14}",
                       "", "Income by category", _category_table(inc, service.category_total(inc)),
                       "", "Expenses by category", _category_table(exp, service.category_total(exp))])
    records = [{"section": "summary", "name": k, "value": d[k]}
               for k in ("income", "expenses", "savings", "savings_rate")]
    records += [{"section": "income", "name": c["category"], "value": c["total"]} for c in inc]
    records += [{"section": "expense", "name": c["category"], "value": c["total"]} for c in exp]
    _emit(args, d, human, records, ["section", "name", "value"])
    return EXIT_OK


def _cmd_categories(args: argparse.Namespace, fn: Any) -> int:
    start, end = _range(args)

    async def fetch() -> dict[str, Any]:
        c = service.client()
        return await fn(c, start, end, await service.resolve_entities(c, args.entity))

    d = asyncio.run(fetch())
    _emit(args, d, _scope_line(d.get("entity_scope")) + _category_table(d["categories"], d["total"]),
          d["categories"], ["category", "group_type", "total"])
    return EXIT_OK


def cmd_spending(args: argparse.Namespace) -> int:
    return _cmd_categories(args, service.spending_by_category)


def cmd_income(args: argparse.Namespace) -> int:
    return _cmd_categories(args, service.income_by_category)


def _check_api() -> tuple[str, str, bool]:
    """(status, detail, auth_failed) for a live GetAccounts call with the stored token."""
    token = keychain.load()
    if not token:
        return "fail", "skipped: not logged in", True
    t0 = time.perf_counter()
    try:
        d = asyncio.run(service.accounts(MonarchClient(token), include_hidden=True))
    except AuthRequired:
        return "fail", "session expired or revoked; run: monarch auth login", True
    except MonarchError as e:
        return "fail", str(e), False
    ms = (time.perf_counter() - t0) * 1000
    return "ok", f"{d['count']} accounts in {ms:.0f} ms", False


MCP_HANDSHAKE_TIMEOUT = 10.0
DISCONTINUED_MCP_HOST = "api.monarch.com"


def _registered_mcp_tools() -> set[str]:
    """Tool names declared in mcp_server.py, read from its source so `mcp` is never imported here."""
    spec = importlib.util.find_spec("monarch_money_cli.mcp_server")
    tree = ast.parse(Path(spec.origin).read_text())
    return {kw.value.value for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "tool"
            for kw in node.keywords if kw.arg == "name" and isinstance(kw.value, ast.Constant)}


def _check_mcp_server(timeout: float = MCP_HANDSHAKE_TIMEOUT) -> tuple[str, str]:
    """Start `monarch mcp` and run initialize + tools/list over stdio. Needs no network or token."""
    expected = _registered_mcp_tools()
    errlog = tempfile.TemporaryFile("w+")  # a file, not a pipe: a chatty server can't block on it
    proc = subprocess.Popen([sys.executable, "-m", "monarch_money_cli", "mcp"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=errlog, text=True)
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    deadline = time.monotonic() + timeout

    def send(msg: dict[str, Any]) -> None:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", **msg}) + "\n")
        proc.stdin.flush()

    def reply(msg_id: int) -> dict[str, Any]:
        while True:
            try:
                line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                raise TimeoutError(f"no response within {timeout:.0f} s") from None
            if line is None:
                raise EOFError("server exited before responding")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"].get("message", msg["error"])))
                return msg.get("result") or {}

    try:
        send({"id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "monarch-doctor", "version": __version__}}})
        reply(1)
        send({"method": "notifications/initialized"})
        send({"id": 2, "method": "tools/list"})
        listed = {t.get("name") for t in reply(2).get("tools", [])}
    except (OSError, ValueError, RuntimeError, TimeoutError, EOFError) as e:
        proc.kill()
        proc.wait()
        errlog.seek(0)
        err = errlog.read().strip().splitlines()
        return "fail", f"{type(e).__name__}: {e}" + (f" ({err[-1][:200]})" if err else "")
    finally:
        proc.kill()
        proc.wait()
        errlog.close()
    missing = expected - listed
    if missing:
        return "fail", f"tools missing from tools/list: {', '.join(sorted(missing))}"
    return "ok", f"stdio handshake ok; {len(listed)}/{len(expected)} tools listed"


def _mcp_client_configs() -> list[tuple[str, Path]]:
    home = Path.home()
    return [("claude_code", home / ".claude.json"),
            ("desktop", home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")]


def _current_monarch() -> set[str]:
    """Real paths of this install's `monarch` script (best effort)."""
    paths = {str(Path(sys.executable).parent / "monarch"), shutil.which("monarch") or ""}
    return {os.path.realpath(p) for p in paths if p and os.path.exists(p)}


def _check_mcp_clients(configs: list[tuple[str, Path]] | None = None) -> list[tuple[str, str]]:
    """(status, detail) rows for monarch entries in MCP client configs. Read-only; never prints env."""
    entries: list[tuple[str, str, str | None, dict[str, Any]]] = []  # (scope, name, project, entry)
    for kind, path in configs if configs is not None else _mcp_client_configs():
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            continue  # missing, unreadable, or malformed: not our problem to report
        if not isinstance(data, dict):
            continue
        scopes: list[tuple[str, str | None, Any]] = [("desktop" if kind == "desktop" else "user", None,
                                                      data.get("mcpServers"))]
        if kind != "desktop" and isinstance(data.get("projects"), dict):
            scopes += [("local", p, v.get("mcpServers")) for p, v in data["projects"].items()
                       if isinstance(v, dict)]
        for scope, project, servers in scopes:
            if not isinstance(servers, dict):
                continue
            for name, e in servers.items():
                if not isinstance(e, dict):
                    continue
                ref = json.dumps([e.get("command"), e.get("args"), e.get("url")]).lower()
                if name.lower() == "monarch" or "monarch" in ref:
                    entries.append((scope, name, project, e))

    def where(scope: str, project: str | None) -> str:
        return f"local scope ({project})" if scope == "local" else f"{scope} scope"

    def fix(scope: str, name: str, project: str | None) -> str:
        if scope == "desktop":
            return f"delete {name!r} from mcpServers in {_mcp_client_configs()[1][1]}"
        cmd = f"claude mcp remove {shlex.quote(name)} -s {scope}"
        return f"cd {shlex.quote(project)} && {cmd}" if project else cmd

    problems: list[str] = []
    valid = 0
    current = _current_monarch()
    signatures: dict[str, dict[str, list[str]]] = {}
    stale: dict[str, tuple[int, set[str]]] = {}  # name -> (first stale-connector problem row, its URLs)
    for scope, name, project, e in entries:
        at = f"{name!r} in {where(scope, project)}"
        url = e.get("url")
        if isinstance(url, str) or e.get("type") in ("http", "sse"):
            parsed = urllib.parse.urlsplit(url if isinstance(url, str) else "")
            shown = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            signatures.setdefault(name.lower(), {}).setdefault(shown, []).append(where(scope, project))
            if (parsed.hostname or "").lower() == DISCONTINUED_MCP_HOST:
                stale.setdefault(name.lower(), (len(problems), set()))[1].add(shown)
                problems.append(f"{at}: {shown} is the discontinued Monarch connector; fix: "
                                f"{fix(scope, name, project)}")
            continue
        command = e.get("command")
        if not isinstance(command, str) or not command:
            problems.append(f"{at}: no command")
            continue
        signatures.setdefault(name.lower(), {}).setdefault(command, []).append(where(scope, project))
        if os.sep not in command:
            resolved = shutil.which(command)
        elif not os.path.isabs(command):
            problems.append(f"{at}: relative command {command!r}; use an absolute path")
            continue
        else:
            resolved = command if os.path.isfile(command) and os.access(command, os.X_OK) else None
        if not resolved:
            problems.append(f"{at}: command {command!r} not found or not executable")
            continue
        args_ = e.get("args") if isinstance(e.get("args"), list) else []
        if Path(command).name == "monarch" and current and os.path.realpath(resolved) not in current:
            problems.append(f"{at}: {command} is a different install than this one ({sys.executable})")
        elif "monarch_money_cli" in args_ and os.path.abspath(resolved) != os.path.abspath(sys.executable):
            problems.append(f"{at}: {command} is a different Python than this one ({sys.executable})")
        valid += 1

    for name, sigs in signatures.items():
        if len(sigs) <= 1:
            continue
        if name in stale:  # one problem, one row: fold the conflict into the stale-connector row
            row, urls = stale[name]
            others = [f"{s} in {', '.join(w)}" for s, w in sigs.items() if s not in urls]
            if others:
                problems[row] = problems[row].replace(
                    "; fix: ", f" (also registered as {'; '.join(others)}); fix: ", 1)
            continue
        problems.append(f"{name!r} is registered differently across scopes: "
                        + "; ".join(f"{s} in {', '.join(w)}" for s, w in sigs.items()))
    if not valid:
        exe = shutil.which("monarch") or str(Path(sys.executable).parent / "monarch")
        problems.append(f"no working monarch registration found; add one: "
                        f"claude mcp add monarch -s user -- {shlex.quote(os.path.abspath(exe))} mcp")
    if problems:
        return [("warn", p) for p in problems]
    return [("ok", f"{valid} valid registration{'s' if valid != 1 else ''}")]


def cmd_doctor(args: argparse.Namespace) -> int:
    checks: list[dict[str, str]] = []
    auth_failed = False

    def add(check: str, status: str, detail: str) -> None:
        checks.append({"check": check, "status": status, "detail": detail})

    state = keychain.token_state()
    add("keychain_token", "ok" if state == "ok" else "fail", {
        "ok": "present, valid format",
        "missing": "not logged in; run: monarch auth login",
        "invalid": "stored value has an unexpected format; run: monarch auth login",
        "unsupported": "the macOS Keychain is only available on macOS",
    }[state])
    auth_failed |= state != "ok"

    status, detail, api_auth = _check_api() if state == "ok" else ("fail", "skipped: no valid token", True)
    add("api", status, detail)
    auth_failed |= api_auth

    try:
        importlib.import_module("mcp.server.mcpserver")
        mcp_installed = True
        add("mcp_extra", "ok", "installed")
    except ImportError:
        mcp_installed = False
        add("mcp_extra", "warn", f"not installed; reinstall with the [mcp] extra, e.g. {MCP_REINSTALL}")

    if mcp_installed:
        add("mcp_server", *_check_mcp_server())
        for status, detail in _check_mcp_clients():
            add("mcp_clients", status, detail)

    path = shutil.which("monarch")
    add("on_path", "ok" if path else "warn", path or "`monarch` is not on PATH")

    ok = all(c["status"] != "fail" for c in checks)  # mcp_extra, mcp_clients and on_path only warn
    _emit(args, {"ok": ok, "checks": checks},
          _table(checks, [("check", "CHECK"), ("status", "STATUS"), ("detail", "DETAIL")],
                 style=_status_style if _use_color(args) else None),
          checks, ["check", "status", "detail"])
    if ok:
        return EXIT_OK
    return EXIT_AUTH if auth_failed else EXIT_ERROR


def cmd_mcp(args: argparse.Namespace) -> int:
    try:
        from monarch_money_cli.mcp_server import run
    except ImportError:
        print(f"The MCP server needs the optional [mcp] extra. Reinstall with it, e.g.:\n  {MCP_REINSTALL}",
              file=sys.stderr)
        return EXIT_ERROR
    run()
    return EXIT_OK


# ---------- parser ----------

def _add_range(p: argparse.ArgumentParser) -> None:
    p.add_argument("start", nargs="?", help="start date YYYY-MM-DD (default: first of this month)")
    p.add_argument("end", nargs="?", help="end date YYYY-MM-DD (default: last of this month)")
    g = p.add_argument_group("date options (pick one; instead of START END)")
    g.add_argument("--month", metavar="YYYY-MM", help="a calendar month")
    g.add_argument("--year", metavar="YYYY", help="a calendar year")
    g.add_argument("--ytd", action="store_true", help="January 1 of this year through today")
    g.add_argument("--last-month", action="store_true", help="the previous calendar month")
    g.add_argument("--days", type=int, metavar="N", help="the last N days, including today")
    g.add_argument("--from", dest="from_", metavar="YYYY-MM-DD", help="start date (through today unless --to)")
    g.add_argument("--to", metavar="YYYY-MM-DD", help="end date (needs --from)")


def _range(args: argparse.Namespace) -> tuple[str, str]:
    return service.period_range(args.start, args.end, month=args.month, year=args.year, ytd=args.ytd,
                                last_month=args.last_month, days=args.days, from_=args.from_, to=args.to)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--output", choices=["table", "csv", "json"], default="table",
                        help="output format (default: table)")
    common.add_argument("--json", dest="output", action="store_const", const="json",
                        default=argparse.SUPPRESS, help="same as --output json")
    scoped = argparse.ArgumentParser(add_help=False)
    scoped.add_argument("--entity", action="append", default=[], metavar="ID|NAME|household",
                        help="limit to a business entity by id or name, or `household` for everything "
                             "not in one (repeatable; see `monarch entities`)")

    p = argparse.ArgumentParser(
        prog="monarch",
        description="Unofficial, read-only CLI and MCP server for Monarch Money. Not affiliated with Monarch Money.",
        epilog="examples:\n"
               "  monarch auth\n"
               "  monarch accounts list\n"
               "  monarch transactions 2026-09-01 2026-09-30 --search costco\n"
               "  monarch cashflow\n"
               "  monarch cashflow --last-month\n"
               "  monarch spending --ytd --entity household\n"
               "  monarch tx --days 7 --search costco\n"
               "  monarch spending 2026-01-01 2026-06-30 --output csv\n"
               "  monarch income --json\n"
               "  monarch cashflow --by-entity\n"
               "  monarch spending --entity household\n"
               "\n"
               "something not working? run: monarch doctor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    auth = sub.add_parser("auth", help="log in (the default), check status, or log out")
    auth_sub = auth.add_subparsers(dest="auth_command", metavar="ACTION")
    auth.set_defaults(func=cmd_auth_login)  # `monarch auth` alone = login
    auth_sub.add_parser("login", help="log in with email, password, and MFA").set_defaults(func=cmd_auth_login)
    auth_sub.add_parser("paste-token", help="store a token copied from the browser (CAPTCHA fallback)"
                        ).set_defaults(func=cmd_auth_paste)
    auth_sub.add_parser("status", help="show whether a token is stored").set_defaults(func=cmd_auth_status)
    auth_sub.add_parser("logout", help="remove the stored token").set_defaults(func=cmd_auth_logout)

    acct = sub.add_parser("accounts", aliases=["account"], help="account commands")
    acct_sub = acct.add_subparsers(dest="accounts_command", metavar="ACTION")
    al = acct_sub.add_parser("list", parents=[common, scoped], help="list accounts and balances")
    al.add_argument("--all", action="store_true", help="include hidden accounts")
    al.set_defaults(func=cmd_accounts_list)
    acct.set_defaults(func=cmd_accounts_list, all=False, entity=[], output="table")  # `monarch accounts` = list

    sub.add_parser("entities", parents=[common], help="list business entities (use with --entity)"
                   ).set_defaults(func=cmd_entities)

    tx = sub.add_parser("transactions", aliases=["tx"], parents=[common, scoped],
                        help="list transactions in a date range")
    _add_range(tx)
    tx.add_argument("--search", default="", help="filter by merchant or text")
    tx.add_argument("--account", action="append", default=[], metavar="ID",
                    help="filter by account id (repeatable; see `monarch accounts list`)")
    tx.add_argument("--limit", type=int, default=50, help=f"max rows (default 50, max {CLI_MAX_TRANSACTIONS})")
    tx.add_argument("--offset", type=int, default=0, help="skip this many rows")
    tx.set_defaults(func=cmd_transactions)

    cf = sub.add_parser("cashflow", parents=[common, scoped],
                        help="income, expenses, savings rate, and totals by category")
    _add_range(cf)
    cf.add_argument("--by-entity", action="store_true",
                    help="compare income, expenses, and savings per business entity and household")
    cf.set_defaults(func=cmd_cashflow)

    sp = sub.add_parser("spending", parents=[common, scoped], help="expense totals by category, largest first")
    _add_range(sp)
    sp.set_defaults(func=cmd_spending)

    inc = sub.add_parser("income", parents=[common, scoped], help="income totals by category, largest first")
    _add_range(inc)
    inc.set_defaults(func=cmd_income)

    sub.add_parser("doctor", parents=[common], help="check login, API access, the MCP server and client configs, and PATH"
                   ).set_defaults(func=cmd_doctor)

    sub.add_parser("mcp", help="run the MCP server over stdio (needs the [mcp] extra)").set_defaults(func=cmd_mcp)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for acct_id in getattr(args, "account", []) or []:
        if not acct_id.isdigit() or len(acct_id) > 30:
            print(f"monarch: invalid account id: {acct_id!r}", file=sys.stderr)
            return EXIT_USAGE
    if getattr(args, "by_entity", False) and args.entity:
        print("monarch: --by-entity already covers every entity; drop --entity", file=sys.stderr)
        return EXIT_USAGE
    try:
        return args.func(args)
    except AuthRequired as e:
        print(f"monarch: {e}", file=sys.stderr)
        return EXIT_AUTH
    except CaptchaRequired as e:
        print(f"monarch: {e}", file=sys.stderr)
        return EXIT_AUTH
    except service.DateUsageError as e:
        print(f"monarch: {e}", file=sys.stderr)
        return EXIT_USAGE
    except (ValueError, MonarchError, keychain.KeychainError) as e:
        print(f"monarch: {e}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
