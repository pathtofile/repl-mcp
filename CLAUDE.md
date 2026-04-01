# CLAUDE.md

## Project Overview

iterm2-mcp is a headless MCP server that lets AI agents control iTerm2 via its Python API. Agents connect over Streamable HTTP to create/close tabs, send input, read output, and send control characters across multiple iTerm2 sessions simultaneously.

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

- **macOS** with iTerm2 installed
- iTerm2 Python API enabled: Preferences > General > Magic > Enable Python API

## Architecture

Headless async process running a FastMCP server via uvicorn. Connects to iTerm2 externally over its Unix domain socket using the `iterm2` Python package.

- **`manager.py`** is the core — manages the iTerm2 connection, tracks sessions, monitors output via `ScreenStreamer`, and maintains per-agent read cursors. Uses screen diffing to extract new output from consecutive screen snapshots.
- **`server.py`** wraps manager methods as MCP tools via `FastMCP`. Uses `streamable_http_app()` served by uvicorn.
- **`models.py`** defines the `Tab` dataclass representing a tracked iTerm2 session.
- **`auth.py`** is optional Starlette middleware for bearer token auth.
- **`__main__.py`** is the headless entry point — no TUI, since iTerm2 itself is the UI.

## MCP Tools

| Tool | Description |
|------|-------------|
| `list_tabs` | List all tracked iTerm2 sessions |
| `discover_tabs` | Find and track all existing iTerm2 sessions |
| `create_tab` | Create a new iTerm2 tab (optionally with a command) |
| `close_tab` | Close a tab and stop tracking it |
| `adopt_tab` | Adopt an unowned tab |
| `write_to_terminal` | Write text/commands to any tab, returns output line count |
| `read_terminal_output` | Read N lines or all new output since last read from any tab |
| `send_control_character` | Send Ctrl+C, Ctrl+D, etc. to any tab |
| `get_screen` | Snapshot the current visible screen of any tab |

## Code Conventions

- Python 3.11+, src layout (`src/repl_mcp/`)
- Black formatter, line length 100
- pytest with `asyncio_mode = "auto"` — no need for `@pytest.mark.asyncio`

## Key Design Decisions

- **External connection** — connects to iTerm2 via Unix socket, runs as standalone process
- **Headless** — no TUI; iTerm2 is the UI
- **Per-agent read cursors** — multiple agents reading the same tab get independent views via `read_cursors` dict on each `Tab`
- **Screen diffing** — uses `ScreenStreamer` for change notifications, then diffs consecutive screen snapshots to extract new output lines
- **Scrollback limit** — output buffer capped (default 10000 lines) to prevent unbounded memory
- **Human-readable tab IDs** — uses `unique-namer` to generate names like `bewildered-spectacles`
- **Any-tab targeting** — all I/O tools take a tab ID, so agents can control multiple tabs concurrently
- **Write + wait** — `write_to_terminal` waits for output to settle before returning, giving agents the output line count
