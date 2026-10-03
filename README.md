# Monarch Money MCP server + CLI

An MCP server and command line tool for [Monarch Money](https://www.monarch.com). It gives Claude (or any MCP
client) access to your accounts, transactions, cash flow, spending, income, budgets, goals, recurring bills,
investments, and net worth. It's read-only unless you enable writes, which can only change a transaction's
category or tags after you confirm.

> **Unofficial.** This project isn't affiliated with Monarch Money, Inc. It uses Monarch's private web API,
> which may change without notice.

## Installation

Requires macOS, Python 3.11+, and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install 'monarch-money-mcp-cli[mcp]'   # or: pipx install 'monarch-money-mcp-cli[mcp]'
monarch auth                                   # email, password, MFA code
monarch doctor                                 # everything should be ok
```

Add it to Claude Code, then restart Claude Code:

```bash
claude mcp add monarch -s user -- "$(which monarch)" mcp
```

To let Claude change transaction categories and tags (it always asks you to confirm), add `--allow-writes`
after `mcp`.

## Command line

```
$ monarch --help
  auth                log in, check status, or log out
  accounts            account commands
  entities            list business entities
  transactions (tx)   list transactions, or change one (set-category, tag)
  categories          list categories
  cashflow            income, expenses, savings rate, and totals by category
  spending            expense totals by category, largest first
  income              income totals by category, largest first
  budgets             budgeted vs actual per category, by month
  goals               savings goals: planned vs contributed
  recurring           recurring bills and income due
  holdings            investment holdings with value and gain
  networth            net worth over time
  doctor              check login, API access, the MCP server, and PATH
  mcp                 run the MCP server over stdio
```

```bash
monarch spending --last-month
monarch cashflow --ytd --entity "Acme LLC"
monarch tx --from 2026-01-01 --output csv > transactions.csv
monarch tx set-category 123456789 --category Subsidy
```

Run `monarch <command> --help` for all options.

## Troubleshooting

Run `monarch doctor`. Every row that isn't `ok` explains the problem, and most include the fix command.

- **Not logged in or session expired:** run `monarch auth`. If login asks for a CAPTCHA, use `monarch auth paste-token`.
- **Monarch is temporarily unavailable:** wait a minute and try again.
- **`/mcp` doesn't show `monarch`:** restart Claude Code after adding the server.

## Security

Your password is never stored, and your session token stays in the macOS Keychain. The tool only talks to
`api.monarch.com`. It changes data only through the allowlist in [SECURITY.md](SECURITY.md), and only after you
confirm.

## License

[MIT](LICENSE)
