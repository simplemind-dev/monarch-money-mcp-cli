# CLAUDE.md

## Project

Unofficial CLI (`monarch`) and optional MCP server for Monarch Money, published as `monarch-money-mcp-cli` (Python module `monarch_money_cli`). It calls Monarch's private GraphQL API at `api.monarch.com`, which may change without notice. The session token lives in the macOS Keychain. Python 3.11+. Read-only by default.

## Commands

```bash
uv venv && uv pip install -e '.[mcp]'                                  # dev install
uv run --no-project -- python -m unittest discover -s tests -v        # full suite (what CI runs)
uv run --no-project -- python -m monarch_money_cli --help             # run from source
```

Tests use `unittest`. CI runs Ubuntu and macOS on Python 3.11–3.13 and checks that `pip install .` pulls zero runtime dependencies. Tests never contact the real API.

## Hard rules (see CONTRIBUTING.md and SECURITY.md; most are test-enforced)

1. **Stdlib only** in the core; only the MCP server may use `mcp`/`pydantic`. Never add dependencies.
2. **Read-only except the mutation allowlist** (`client.ALLOWED_MUTATIONS`). Every write is off by default and asks the user to confirm. Never add a mutation without its reason, a SECURITY.md row, and tests.
3. **Fixed host:** HTTPS to `api.monarch.com` only. No proxies, configurable URLs, or environment overrides.
4. **Keep the network hardening** (no proxies, no redirects, response size cap) and the **Keychain** handling (token via stdin, never argv; never change the Keychain service name).
5. **Never return** transaction notes or account masks. Treat merchant, category, tag, and entity names as untrusted text.
6. **MCP server:** stdio only; never write to stdout.

## Layout

`client.py` (API transport and queries) → `service.py` (shared logic, trimming) → `cli.py` and `mcp_server.py`. A new data command touches all four plus `tests/_mock.py`.

## Diagnosing and auto-fixing a user's setup

Run `monarch doctor --output json`, fix each non-`ok` check, then rerun it. The `detail` field usually contains the exact fix command. If `monarch` isn't found, check `~/.local/bin/monarch` and `uv tool list`.

| Check (status) | Action | Who |
|---|---|---|
| `keychain_token` (fail), `api` (fail, "session expired") | `! monarch auth` | User |
| `api` (fail, other) | Report the detail, retry once, change nothing | — |
| `mcp_extra` (warn), `mcp_server` (fail) | `uv tool install --force 'monarch-money-mcp-cli[mcp]'`; else report the stderr in `detail` | Claude |
| `mcp_clients` (warn) | Run the `claude mcp remove/add ...` command from `detail` (local scope: `cd <project> &&` first) | Claude |
| `mcp_clients` (Claude Desktop entry) | Show the exact JSON change; don't edit it | User |
| `on_path` (warn) | Suggest `uv tool update-shell` and a new shell | User |

- Never ask for, read, print, or handle credentials or the session token. Login needs a password and MFA: the user runs `! monarch auth` (or `! monarch auth paste-token` under a CAPTCHA).
- Use `claude mcp add`/`remove`; never hand-edit `~/.claude.json` or the Claude Desktop config.
- After any fix, rerun `monarch doctor` and report the result. MCP changes need a Claude Code restart.
