# Security Policy

## Reporting a vulnerability

Report vulnerabilities privately with GitHub's **Report a vulnerability** button under the Security tab.
Please don't open a public issue.

## Design

| Area | Guarantee |
|---|---|
| Dependencies | Standard library only. The MCP extra adds only the official `mcp` SDK. |
| Credentials | Your password and MFA codes are never stored. The token is kept only in the macOS Keychain and is passed to `/usr/bin/security` via stdin, never as an argument. There's no plaintext fallback. |
| Network | HTTPS to `api.monarch.com` only, with certificate checks. Proxies and redirects are refused, responses are capped at 5 MB, and the host can't be configured. |
| Capability | Read-only by default. The only writes are the mutations in the allowlist below; anything else is refused before any request is sent. |
| Data | Transaction notes, account masks, and entity notes are never returned. |
| MCP | stdio only, with no listening port. |
| `doctor` | Reads MCP client configs without changing them and never prints their `env` values or the token. |

## Mutation allowlist

`client.ALLOWED_MUTATIONS` is the only list of mutations the package can send. The client checks the operation name,
the query text, and the exact input keys before any request, and refuses all mutations unless writes were enabled.

| Mutation | What it does | Why it's allowed | Gate |
|---|---|---|---|
| `Web_TransactionDrawerUpdateTransaction` | Sets one transaction's category to an existing category. | Fix miscategorised transactions. | `monarch tx set-category`: the user answers `y` at the terminal prompt, or passes `--yes`. MCP: `monarch_set_transaction_category` with `apply=true` on a server started with `monarch mcp --allow-writes`, and the user accepts the confirmation. |
| `Web_SetTransactionTags` | Replaces one transaction's tags with existing tags. | Tag transactions for reporting. | `monarch tx tag`: the user answers `y` at the terminal prompt, or passes `--yes`. MCP: `monarch_update_transaction_tags` with `apply=true` on a server started with `monarch mcp --allow-writes`, and the user accepts the confirmation. |

Every write shows the change first. Without a terminal and without `--yes`, the CLI only previews. The MCP server
asks for confirmation through MCP elicitation; if the client can't show it, or the user declines, nothing is sent.

## Known limitations

- Any process running as your macOS user can read the token through `/usr/bin/security`.
- Merchant, category, and tag names are untrusted text. Take care when combining the MCP server with tools that can send data out, and when enabling its write tools.
- Monarch has no public API, so private endpoints may change without notice.
