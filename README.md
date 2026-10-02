# Monarch Money MCP server + CLI

An MCP server and command line tool for [Monarch Money](https://www.monarch.com). Use it to give
Claude (or any MCP client) access to your accounts, transactions, cash flow, spending, income, budgets,
goals, recurring bills, investment holdings, and net worth. It's read-only unless you enable writes, which
can only change a transaction's category or tags.
There are no third-party dependencies beyond the MCP SDK, and your session token stays in the macOS Keychain.

> **Unofficial.** This project isn't affiliated with Monarch Money, Inc. It uses Monarch's private web API,
> which may change without notice.

## Quick start

Requires macOS, Python 3.11+, and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install 'monarch-money-mcp-cli[mcp]'
monarch auth                                              # email, password, MFA code
monarch doctor                                            # everything should be ok
claude mcp add monarch -s user -- "$(which monarch)" mcp  # then restart Claude Code
```

Or with pipx: `pipx install 'monarch-money-mcp-cli[mcp]'`.

To upgrade: `uv tool upgrade monarch-money-mcp-cli` (or `pipx upgrade monarch-money-mcp-cli`), then restart Claude Code.

## Commands

```bash
monarch auth [login|status|logout|paste-token]
monarch doctor
monarch entities
monarch accounts list [--all]
monarch tx [--search TEXT] [--account ID] [--tag NAME] [--limit N]
monarch cashflow [--by-entity]
monarch spending
monarch income
monarch budgets
monarch goals [--all]
monarch recurring
monarch holdings [--account ID]
monarch networth [--daily]
monarch categories
monarch tx set-category TXN_ID --category NAME|ID [--yes]
monarch tx tag TXN_ID [--add NAME]... [--remove NAME]... [--yes]
```

- **Dates:** the default is the current month. Give `START END` (`YYYY-MM-DD`), or one of `--month YYYY-MM`,
  `--year YYYY`, `--ytd`, `--last-month`, `--days N`, or `--from DATE [--to DATE]`.
  `networth` defaults to the last 12 months; `holdings` shows current positions and takes no dates.
- **Business entities:** add `--entity <id|name|household>` to limit results to an entity. You can repeat it.
  It works on `accounts`, `tx`, `cashflow`, `spending`, and `income`.
- **Output:** `--output table|csv|json` (`--json` for short).
- **Changing a transaction:** `tx set-category` and `tx tag` show the change and send nothing unless you add
  `--yes`. Find the id with `monarch tx --json`. `tx tag` keeps the transaction's other tags.

```bash
monarch spending --last-month
monarch cashflow --ytd --entity "Acme LLC"
monarch tx --from 2026-01-01 --output csv > transactions.csv
```

Run `monarch <command> --help` for all options.

## MCP server

**Claude Desktop:** add this to `~/Library/Application Support/Claude/claude_desktop_config.json`, using
the path that `which monarch` prints, then restart the app:

```json
{ "mcpServers": { "monarch": { "command": "/Users/you/.local/bin/monarch", "args": ["mcp"] } } }
```

The read-only tools: `monarch_list_entities`, `monarch_list_accounts`, `monarch_list_transactions`,
`monarch_cashflow_summary`, `monarch_cashflow_by_entity`, `monarch_spending_by_category`,
`monarch_income_by_category`, `monarch_budget_summary`, `monarch_list_goals`, `monarch_list_recurring`,
`monarch_list_holdings`, `monarch_net_worth_history`, and `monarch_list_categories`.

**Writes (opt-in):** start the server with `--allow-writes` to add `monarch_set_transaction_category` and
`monarch_update_transaction_tags`. They preview by default and change data only when called with `apply=true`.

```bash
claude mcp add monarch -s user -- "$(which monarch)" mcp --allow-writes
```

## Troubleshooting

Run `monarch doctor`. Every row that isn't `ok` explains the problem, and most include the fix command.

- **Not logged in or session expired:** run `monarch auth`. If login asks for a CAPTCHA, use `monarch auth paste-token`.
- **Monarch is temporarily unavailable:** Monarch is down or overloaded. Wait a minute and try again.
- **`/mcp` doesn't show `monarch`:** restart Claude Code after adding the server.
- **Stale `monarch` entry pointing at `api.monarch.com/mcp`:** run the `claude mcp remove` command that `doctor` shows.

## Security

Your password is never stored. The tool only talks to `api.monarch.com`. It changes data only through the
mutation allowlist in [SECURITY.md](SECURITY.md), and only when you pass `--yes` or enable MCP writes.

## Development

```bash
git clone https://github.com/simplemind-dev/monarch-money-mcp-cli && cd monarch-money-mcp-cli
uv venv && uv pip install -e '.[mcp]'
uv run --no-project -- python -m unittest discover -s tests -v
```

## License

[MIT](LICENSE)
