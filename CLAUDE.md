# CLAUDE.md

## Project Overview

repl-mcp is a Python TUI + MCP server for managing interactive REPL programs via PTY. AI agents connect over Streamable HTTP to start programs, send input, read output, and send signals. Humans see everything in a Textual-based terminal UI with tabbed program views.

## Quick Commands

```bash
# Run the app
uv run repl-mcp

# Run tests
uv run pytest

# Run tests verbose
uv run pytest -v

# Format
uv run black src/ tests/

# Lint
uv run pylint src/repl_mcp/
```

## Architecture

Single async process running both Textual TUI and uvicorn MCP server in the same event loop. The MCP server starts as an `asyncio.create_task` inside Textual's `on_mount`.

- **`manager.py`** is the core — owns all PTY lifecycle, output buffers, and per-agent read cursors. Uses `pty.openpty()` + `subprocess.Popen` for PTY management. Read loop uses `loop.run_in_executor(None, os.read, fd, 4096)`.
- **`server.py`** wraps manager methods as MCP tools via `FastMCP`. Uses `streamable_http_app()` served by uvicorn.
- **`app.py`** is the Textual TUI. Receives callbacks from manager (`on_output`, `on_program_started`, `on_program_exited`) using `call_from_thread` since PTY reads happen in executor threads. Humans can start programs directly via `Ctrl+N`.
- **`auth.py`** is optional Starlette middleware for bearer token auth.

## Code Conventions

- Python 3.11+, src layout (`src/repl_mcp/`)
- Black formatter, line length 100
- pytest with `asyncio_mode = "auto"` — no need for `@pytest.mark.asyncio`
- Tests use real PTY programs (`echo`, `cat`) — no mocking of the PTY layer

## Key Design Decisions

- **Real PTYs, not pipes** — programs get full terminal behavior (prompts, colors, Ctrl+C)
- **Per-agent read cursors** — multiple agents reading the same program get independent views via `read_cursors` dict on each `Program`
- **Scrollback limit** — output buffer is capped (default 10000 lines) to prevent unbounded memory growth
- **Allowlist uses canonical paths** — `shutil.which()` + `os.path.realpath()` to prevent symlink/PATH tricks
- **`_blocking_read` helper** — handles EAGAIN/EWOULDBLOCK from non-blocking PTY fd in executor thread, only breaks on real errors (EIO, EBADF)
- **Human-readable program IDs** — uses `unique-namer` to generate names like `bewildered-spectacles` instead of UUIDs
- **Human-created programs + adoption** — humans can start programs from the TUI (`Ctrl+N`) and agents can adopt them via `adopt_program`
