# Monarch Money MCP server + CLI

A read-only MCP server and command line tool for [Monarch Money](https://www.monarch.com). Use it to give
Claude (or any MCP client) access to your accounts, transactions, cash flow, spending, and income.
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
```

- **Dates:** the default is the current month. Give `START END` (`YYYY-MM-DD`), or one of `--month YYYY-MM`,
  `--year YYYY`, `--ytd`, `--last-month`, `--days N`, or `--from DATE [--to DATE]`.
- **Business entities:** add `--entity <id|name|household>` to limit results to an entity. You can repeat it.
- **Output:** `--output table|csv|json` (`--json` for short).

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

The tools are all read-only: `monarch_list_entities`, `monarch_list_accounts`, `monarch_list_transactions`,
`monarch_cashflow_summary`, `monarch_cashflow_by_entity`, `monarch_spending_by_category`, and
`monarch_income_by_category`.

## Troubleshooting

Run `monarch doctor`. Every row that isn't `ok` explains the problem, and most include the fix command.

- **Not logged in or session expired:** run `monarch auth`. If login asks for a CAPTCHA, use `monarch auth paste-token`.
- **Monarch is temporarily unavailable:** Monarch is down or overloaded. Wait a minute and try again.
- **`/mcp` doesn't show `monarch`:** restart Claude Code after adding the server.
- **Stale `monarch` entry pointing at `api.monarch.com/mcp`:** run the `claude mcp remove` command that `doctor` shows.

## Security

Your password is never stored. The tool only talks to `api.monarch.com` and never changes your data.
See [SECURITY.md](SECURITY.md).

## Development

```bash
git clone https://github.com/simplemind-dev/monarch-money-mcp-cli && cd monarch-money-mcp-cli
uv venv && uv pip install -e '.[mcp]'
uv run --no-project -- python -m unittest discover -s tests -v
```

## License

[MIT](LICENSE)
