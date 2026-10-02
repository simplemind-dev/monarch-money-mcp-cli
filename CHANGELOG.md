# Changelog

## Unreleased

- Writes behind a mutation allowlist (`client.ALLOWED_MUTATIONS`); anything not listed is refused before any request.
- `monarch tx set-category TXN_ID --category NAME|ID` and `monarch tx tag TXN_ID --add/--remove NAME`:
  preview by default, change data only with `--yes`.
- `monarch categories`, and the read-only MCP tool `monarch_list_categories`.
- `monarch mcp --allow-writes` adds `monarch_set_transaction_category` and `monarch_update_transaction_tags`
  (preview unless `apply=true`).

## 0.1.2 (2026-10-01)

- `monarch budgets`: budgeted vs actual per category and month, with income and expense totals.
- `monarch goals`: savings goals with planned vs contributed amounts (`--all` includes archived).
- `monarch recurring`: recurring bills and income due in the range, marked paid, missed, or upcoming.
- `monarch holdings`: investment positions with value, cost basis, and gain (default: every brokerage account).
- `monarch networth`: month-end (or `--daily`) net worth, defaulting to the last 12 months.
- 5 matching read-only MCP tools (12 in total).

## 0.1.1 (2026-09-25)

- Transactions include each one's business entity and tags.
- `tx --tag NAME` (repeatable) filters transactions by tag.

## 0.1.0 (2026-09-25)

- `monarch auth`: login, status, logout via macOS Keychain.
- `monarch accounts list`, `transactions`/`tx`, `cashflow`, `spending`, and `income` by category.
- Business entities: `monarch entities` and `--entity` scoping, including `cashflow --by-entity`.
- Date shortcuts: `--month`, `--year`, `--ytd`, `--last-month`, `--days`, `--from`/`--to`.
- `--output table|csv|json` on every data command and `doctor`.
- `monarch doctor`: diagnoses Keychain, API, and MCP setup.
- `monarch mcp`: read-only MCP server with 7 tools (optional `[mcp]` extra).
