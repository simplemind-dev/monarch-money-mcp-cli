# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Unofficial, **read-only by default** CLI (`monarch`) and optional MCP server for Monarch Money, distributed as `monarch-money-mcp-cli` (repo `simplemind-dev/monarch-money-mcp-cli`; Python module still `monarch_money_cli`). Calls Monarch's private GraphQL API at `api.monarch.com`, which may change without notice. Session token lives in the macOS Keychain. Python 3.11+.

## Commands

```bash
uv venv && uv pip install -e '.[mcp]'                      # dev install incl. MCP extra

uv run --no-project -- python -m unittest discover -s tests -v                 # full suite (what CI runs)
uv run --no-project -- python -m unittest tests.test_cli -v                    # one module
uv run --no-project -- python -m unittest tests.test_cli.Parser.test_command_shapes   # one test

uv run --no-project -- python -m monarch_money_cli --help                      # run without installing the script
```

No linter/formatter config; tests use `unittest`, not pytest. CI (`.github/workflows/ci.yml`) runs on Ubuntu and macOS, Python 3.11–3.13, and checks `pip install .` pulls **zero** runtime dependencies.

## Hard rules (from CONTRIBUTING.md / SECURITY.md, several enforced by tests)

1. **Stdlib only in the core.** `tests/test_security.py` scans core-module imports; only `mcp_server.py` may import `mcp`/`pydantic`, and `cli.cmd_mcp` imports it lazily with an install hint on `ImportError`. Never add dependencies.
2. **Read-only except `client.ALLOWED_MUTATIONS`.** Never add an entry without its `does`/`why`, a SECURITY.md row, and tests.
3. **Fixed host.** `client.BASE_URL` is hard-coded HTTPS; `client.py` must not read `os.environ`/`getenv` (test-enforced). Don't add proxy settings or a configurable host/URL.
4. **Network hardening (`client._OPENER`):** an empty `ProxyHandler` ignores proxy env vars, `_NoRedirect` stops the auth header being forwarded, and `MAX_RESPONSE_BYTES` caps responses at 5 MB.
5. **Keychain:** the token is written via `/usr/bin/security -i` through **stdin**, never argv, and validated against `_TOKEN_RE`. No plaintext fallback off macOS. `keychain.SERVICE` must never change: it would orphan stored tokens (test-pinned).
6. **Data trimming:** never return transaction `notes` or account `mask`; treat merchant and category names as untrusted text.

## Architecture

Four layers in `src/monarch_money_cli/`, data flowing `client.py` → `service.py` → `cli.py`/`mcp_server.py`:

- `client.py`: transport. Sync `_post` (urllib), wrapped by async `MonarchClient._gql` via `asyncio.to_thread`. Maps 401/403 to `AuthRequired`, other failures to `MonarchError`; `login()` raises `MFARequired`/`CaptchaRequired`. GraphQL query strings (`Q_*`) live here. Mutations (`M_*`) go only through `_mutate`, which needs `allow_writes=True` and checks name, query text, and input keys before any request; `_gql` refuses them.
- `service.py`: shared by CLI and MCP. Loads the token from Keychain, resolves date ranges and `--entity` scopes, returns trimmed dicts paginated at `MAX_PAGE = 100`. `resolve_entities` turns `--entity` values (id, name/prefix, or `household`) into a scope; `entity_set()` builds `BusinessEntitySetInput`, where `includeUnassigned` is required so it's always sent.
- `cli.py`: argparse subcommands calling service functions via `asyncio.run`. `cmd_doctor` never imports `mcp`; it checks a live MCP handshake against tool names parsed from `mcp_server.py` source. `_csv_cell` guards CSV output against formula injection.
- `mcp_server.py`: `MCPServer` tools, read-only annotations, pydantic-validated args, stdio only. Never write to stdout here: it's the MCP transport; log to stderr. Write tools are added by `enable_writes()` (`monarch mcp --allow-writes`) via `mcp.add_tool`, so doctor's `@mcp.tool` scan sees only reads.

Writes show the change first, then need confirmation: the CLI asks `[y/N]` on a terminal (`cli._confirm`; `--yes` skips it, non-TTY only previews), and MCP `apply=true` asks through elicitation (resolvers `Resolve`/`Elicit`) and writes nothing if the client can't ask. `monarch tx set-category|tag` use their own parser (`build_tx_write_parser`), routed in `main()`, because `tx` takes positional dates.

To add a data command: add the query/method in `client.py`, a trimming function in `service.py`, wire both `cli.py` and `mcp_server.py`, and extend `tests/_mock.py`.

## Tests

`tests/_mock.py::MockMonarch` runs a local `HTTPServer`, monkeypatching `client.BASE_URL` and `client._OPENER` (plain HTTP, same hardening). It dispatches on the GraphQL `operationName`; a new operation needs an entry in its `data` dict (mutations: `writes`), and `m.mutations()` lists the mutations it received. `tests/test_doctor_mcp.py` spawns the real MCP server for the handshake and feeds `_check_mcp_clients` temporary config files. Tests never contact the real API; Keychain calls are mocked. The real-Keychain round-trip test is opt-in:

```bash
MONARCH_KEYCHAIN_TEST=1 uv run --no-project -- python -m unittest tests.test_security -v   # macOS only
```

## Diagnosing and auto-fixing a user's setup

When `monarch` or its MCP server isn't working, run the doctor, fix each non-`ok` row, then rerun it:

```bash
monarch doctor --output json    # {"ok": bool, "checks": [{"check", "status", "detail"}]}
```

If `monarch` isn't found, check `~/.local/bin/monarch` and `uv tool list` first; `detail` usually has the exact fix command to prefer.

| Check (status) | Cause | Action | Who |
|---|---|---|---|
| `keychain_token` (fail) | No token stored, or it's malformed | Run `! monarch auth` | User |
| `api` (fail, "session expired") | Token was rejected | Run `! monarch auth` | User |
| `api` (fail, other) | Network/API outage or change | Report the detail, retry once; change nothing | — |
| `mcp_extra` (warn) | Installed without `[mcp]` | `uv tool install --force 'monarch-money-mcp-cli[mcp]'` | Claude, after saying so |
| `mcp_server` (fail) | Server didn't start, or a tool is missing | Reinstall as above; else report `detail`'s stderr | Claude |
| `mcp_clients` (warn, "discontinued Monarch connector") | Stale `https://api.monarch.com/mcp` entry | Run the `claude mcp remove ...` from `detail` (local scope: prefix `cd <project> &&`) | Claude |
| `mcp_clients` (warn, "registered differently across scopes") | A local-scope entry shadows the user's | `cd <project> && claude mcp remove monarch -s local` | Claude |
| `mcp_clients` (warn, relative, not found, or different install) | Bad or outdated command path | `claude mcp remove monarch -s <scope>`, then `claude mcp add monarch -s user -- "$(which monarch)" mcp` | Claude |
| `mcp_clients` (warn, "no working monarch registration") | Never registered | Run the `claude mcp add ...` command from `detail` | Claude |
| `mcp_clients` (Claude Desktop entry) | Stale/wrong `claude_desktop_config.json` entry | Show the exact JSON change; don't edit it | User |
| `on_path` (warn) | uv's tool bin dir isn't on `PATH` | Suggest `uv tool update-shell` + new shell | User |

Rules:

- **Never ask for, read, print, or handle credentials or the session token.** The user runs `! monarch auth` (or `! monarch auth paste-token` under a CAPTCHA) — login needs a password and MFA.
- Use `claude mcp add`/`remove`; never hand-edit `~/.claude.json` or the Claude Desktop config.
- After any fix, rerun `monarch doctor` and report the final table; MCP changes need a Claude Code restart.
