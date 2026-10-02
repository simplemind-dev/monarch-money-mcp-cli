# Contributing

1. **No new runtime dependencies.** The only allowed extra is the official `mcp` SDK.
2. **Read-only except `client.ALLOWED_MUTATIONS`.** Never add an entry without its `does`/`why`, a SECURITY.md row, and tests.
3. **No configurable API host or proxy.**
4. **Tests pass:** `uv run --no-project -- python -m unittest discover -s tests -v`.
5. New data commands go into both the CLI and the MCP server (see [CLAUDE.md](CLAUDE.md)). Update the README and CHANGELOG too.

Contributions are licensed under the MIT License.
