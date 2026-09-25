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
| Capability | Read-only. There are no GraphQL mutations. |
| Data | Transaction notes, account masks, and entity notes are never returned. |
| MCP | stdio only, with no listening port. |
| `doctor` | Reads MCP client configs without changing them and never prints their `env` values or the token. |

## Known limitations

- Any process running as your macOS user can read the token through `/usr/bin/security`.
- Merchant and category names are untrusted text. Take care when combining the MCP server with tools that can send data out.
- Monarch has no public API, so private endpoints may change without notice.
