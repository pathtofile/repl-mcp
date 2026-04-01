# CLAUDE.md

## Project Overview

iterm2-mcp is a headless MCP server that lets AI agents control iTerm2 via its Python API. Agents connect over Streamable HTTP to create/close tabs, send input, read output, and send control characters across multiple iTerm2 sessions simultaneously. iTerm2 itself is the UI — no TUI needed.

## Quick Commands

```bash
# Run the server
uv run iterm2-mcp

# Run with auto-discovery of existing tabs
uv run iterm2-mcp --discover

# Run with verbose logging
uv run iterm2-mcp -v

# Run tests
uv run pytest

# Run tests verbose
uv run pytest -v

# Format
uv run black src/ tests/

# Lint
uv run pylint src/repl_mcp/
```

## Prerequisites

- **macOS** with iTerm2 installed and running
- iTerm2 Python API enabled: Preferences > General > Magic > Enable Python API
- Python 3.11+

## Architecture

Headless async process running a FastMCP server via uvicorn. Connects to iTerm2 externally over its Unix domain socket using the `iterm2` Python package.

```
src/repl_mcp/
├── __init__.py      # Package version (0.3.0)
├── __main__.py      # Headless CLI entry point (asyncio.run)
├── auth.py          # Optional Starlette bearer token middleware
├── manager.py       # ITermManager — connection, tab tracking, output monitoring
├── models.py        # Tab dataclass
└── server.py        # ITermMCPServer — FastMCP tool registration, ASGI app
```

### Key modules

- **`manager.py`** is the core — manages the iTerm2 connection via `iterm2.Connection.async_create()`, tracks sessions in a `dict[str, Tab]`, monitors output via `ScreenStreamer` with per-tab background tasks, and maintains per-agent read cursors. Uses screen diffing (overlap detection between consecutive snapshots) to extract new output lines.
- **`server.py`** wraps manager methods as MCP tools via `FastMCP("iterm2-mcp")`. Each MCP session gets a stable agent label (`agent-1`, `agent-2`...) with independent read cursors. Uses `streamable_http_app()` served by uvicorn.
- **`models.py`** defines the `Tab` dataclass: session_id, tab_id, window_id, output_buffer, read_cursors, last_screen_lines (for diffing).
- **`auth.py`** is optional Starlette ASGI middleware for bearer token auth using `secrets.compare_digest`.
- **`__main__.py`** is the headless entry point — connects to iTerm2, optionally discovers existing sessions, starts uvicorn.

## MCP Tools

| Tool | Description |
|------|-------------|
| `list_tabs` | List all tracked iTerm2 sessions |
| `discover_tabs` | Find and track all existing iTerm2 sessions |
| `create_tab` | Create a new iTerm2 tab (optionally with a command) |
| `close_tab` | Close a tab and stop tracking it |
| `adopt_tab` | Adopt an unowned tab |
| `write_to_terminal` | Write text/commands to any tab, waits for output to settle, returns output line count |
| `read_terminal_output` | Read N lines or all new output since last read from any tab |
| `send_control_character` | Send Ctrl+C, Ctrl+D, Ctrl+Z, etc. to any tab |
| `get_screen` | Snapshot the current visible screen of any tab (with cursor position) |

## Code Conventions

- Python 3.11+, src layout (`src/repl_mcp/`)
- Black formatter, line length 100
- pylint for linting (`C0114`, `C0115`, `C0116` disabled — no mandatory docstrings)
- pytest with `asyncio_mode = "auto"` — no need for `@pytest.mark.asyncio`

## Key Design Decisions

- **External connection** — connects to iTerm2 via Unix socket using `iterm2.Connection.async_create()`, runs as standalone process outside iTerm2
- **Headless** — no TUI; iTerm2 is the UI. Users see everything agents do in real time in their iTerm2 tabs
- **Screen diffing** — uses `ScreenStreamer` for change notifications, then diffs consecutive screen snapshots to extract new output lines. Finds overlap between end-of-previous and start-of-current screen to detect scroll
- **Per-agent read cursors** — multiple agents reading the same tab get independent views via `read_cursors` dict on each `Tab`. `read_terminal_output` without `lines` returns delta since last read
- **Write + wait** — `write_to_terminal` waits for output to settle (no new output for 300ms, up to 5s max) before returning, giving agents the output line count
- **Any-tab targeting** — all I/O tools take a tab ID, so agents can control multiple tabs concurrently
- **Human-readable tab IDs** — uses `unique-namer` to generate names like `bewildered-spectacles` instead of iTerm2's `w0t0p0` identifiers
- **Scrollback limit** — output buffer capped (default 10000 lines) to prevent unbounded memory
- **No tab cleanup on shutdown** — server cancels monitors but does NOT close iTerm2 tabs. User's sessions are preserved
- **Localhost only by default** — binds to 127.0.0.1, not 0.0.0.0

## Security Notes

- Server binds to localhost only by default. Binding to 0.0.0.0 exposes terminal control to the network.
- Bearer token auth is optional but recommended when exposing beyond localhost. Uses timing-safe comparison.
- No program allowlist — agents send text to full shell sessions. The security boundary is iTerm2 and the user's shell permissions.
- Per-agent ownership prevents one agent from adopting another's tabs, but any agent can read/write any tracked tab.
- Server does not inject environment variables into iTerm2 sessions.
