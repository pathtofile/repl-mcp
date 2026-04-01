# iterm2-mcp

A headless MCP server that lets AI agents control iTerm2 via its Python API. Agents connect over Streamable HTTP to create tabs, run commands, read output, and send control characters — across multiple iTerm2 sessions simultaneously.

```
┌──────────────────────────────────────────────────┐
│           iterm2-mcp (headless process)           │
│                                                   │
│  ┌──────────────────┐    ┌─────────────────────┐ │
│  │  FastMCP Server   │◄──►│   iTerm2 Manager    │ │
│  │  (Streamable HTTP)│    │  (connection, tabs,  │ │
│  │  port 8780        │    │   output buffers)    │ │
│  └──────────────────┘    └──────────┬──────────┘ │
│                                     │             │
│         MCP tools                   │ iterm2      │
│         (9 tools)                   │ Python API  │
└─────────────────────────────────────┼─────────────┘
          ▲                           │
          │ Streamable HTTP           │ Unix socket
          │                           ▼
   ┌──────┴──────┐           ┌──────────────┐
   │  AI Agents  │           │    iTerm2    │
   │ (Claude, etc)│          │ ┌────┬────┐  │
   └─────────────┘           │ │tab1│tab2│  │
                             │ ├────┼────┤  │
                             │ │tab3│tab4│  │
                             │ └────┴────┘  │
                             └──────────────┘
```

## How It Works

iterm2-mcp runs as a standalone process that bridges AI agents to iTerm2:

1. **Connects externally** to a running iTerm2 instance over its Unix domain socket (the same mechanism iTerm2 scripts use).
2. **Exposes MCP tools** via a Streamable HTTP server that any MCP-compatible client can connect to.
3. **Monitors output** using iTerm2's `ScreenStreamer` API — a background task per tracked session watches for screen changes, diffs consecutive snapshots, and accumulates new output into a buffer.
4. **Per-agent read cursors** let multiple agents read the same tab independently. Agent A reading output doesn't consume it for Agent B.
5. **Human-readable IDs** — tabs get memorable names like `bewildered-spectacles` instead of iTerm2's internal `w0t0p0` identifiers.

### Output Detection (Screen Diffing)

Since iTerm2 exposes screen contents (not a raw byte stream), iterm2-mcp detects new output by diffing consecutive screen snapshots:

- When `ScreenStreamer` fires, the monitor captures all visible lines.
- It finds the overlap between the previous and current screen (detecting scroll).
- Lines after the overlap are treated as new output and appended to the buffer.
- If the screen jumped entirely (e.g., `clear` was run), new non-empty lines that weren't in the previous screen are captured.

This means the output buffer contains the **logical history** of what appeared in the terminal, not a character-by-character stream.

### Write + Wait

When an agent calls `write_to_terminal`, the server sends the text to iTerm2 and then waits for output to settle (no new output for 300ms, up to a 5s timeout). This lets the agent know how many lines a command produced before deciding what to do next.

## Features

- **Multi-tab control** — create, close, and interact with any number of iTerm2 tabs concurrently
- **Any-tab targeting** — every I/O tool takes a tab ID, so agents aren't limited to a single "active" tab
- **Tab discovery** — auto-discover and adopt existing iTerm2 sessions, not just ones the agent created
- **Per-agent read cursors** — multiple agents can independently track output from the same tab
- **Screen snapshots** — `get_screen` returns exactly what's visible in a tab right now, including cursor position
- **Control characters** — send Ctrl+C, Ctrl+D, Ctrl+Z, Escape, etc. to any tab
- **Bearer token auth** — optional authentication for the MCP endpoint
- **Headless** — no TUI; iTerm2 itself is the UI, so you see everything the agent does in real time
- **Human-readable IDs** — tabs get names like `bewildered-spectacles` via `unique-namer`
- **Scrollback limit** — output buffer capped at 10,000 lines (configurable) to prevent unbounded memory growth

## Prerequisites

- **macOS** (iTerm2 is macOS-only)
- **iTerm2** installed and running
- **iTerm2 Python API enabled**: Preferences → General → Magic → ✅ Enable Python API
- **Python 3.11+**

## Installation

```bash
# Clone and install
git clone https://github.com/pathtofile/repl-mcp.git
cd repl-mcp
pip install -e .

# Or with uv
uv pip install -e .

# Or install dev dependencies too
pip install -e ".[dev]"
```

## Usage

### Starting the Server

```bash
# Start with defaults (port 8780, localhost only)
iterm2-mcp

# Discover and track all existing iTerm2 sessions on startup
iterm2-mcp --discover

# With authentication
iterm2-mcp --token my-secret-token

# Verbose logging
iterm2-mcp -v

# All options
iterm2-mcp --host 127.0.0.1 --port 9000 --token secret --scrollback 50000 --discover -v
```

### CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--port PORT` | `8780` | Port for the MCP HTTP server |
| `--host HOST` | `127.0.0.1` | Host to bind to |
| `--token TOKEN` | none | Bearer token for authentication |
| `--generate-token` | — | Print a random token and exit |
| `--scrollback LINES` | `10000` | Max output lines buffered per tab |
| `--discover` | off | Track all existing iTerm2 sessions on startup |
| `-v, --verbose` | off | Enable debug-level logging |

### Connecting Claude Code

Add iterm2-mcp to your Claude Code MCP settings (`.claude/settings.json` or `~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "iterm2": {
      "type": "url",
      "url": "http://127.0.0.1:8780/mcp"
    }
  }
}
```

With authentication:

```json
{
  "mcpServers": {
    "iterm2": {
      "type": "url",
      "url": "http://127.0.0.1:8780/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN_HERE"
      }
    }
  }
}
```

Or via the CLI:

```bash
claude mcp add iterm2 --transport http http://127.0.0.1:8780/mcp
```

### Connecting Other MCP Clients

Any MCP-compatible client can connect using Streamable HTTP transport:

- **URL**: `http://<host>:<port>/mcp`
- **Transport**: Streamable HTTP
- **Auth** (if enabled): `Authorization: Bearer <token>` header

## MCP Tools

### Tab Management

#### `create_tab`

Create a new iTerm2 tab, optionally running a command.

```json
{
  "command": "python3 -i",
  "profile": "Default",
  "window_id": null
}
```

Returns: `{ "id": "bewildered-spectacles", "session_id": "w0t1p0", "name": "python3 -i" }`

#### `close_tab`

Close an iTerm2 tab and stop tracking it.

```json
{ "id": "bewildered-spectacles" }
```

#### `list_tabs`

List all tracked tabs with their IDs, names, and status. No parameters.

Returns: `[{ "id": "bewildered-spectacles", "session_id": "w0t1p0", "name": "python3 -i", "is_alive": true, "owner_agent": "agent-1", "started_at": "..." }]`

#### `discover_tabs`

Find and start tracking all existing iTerm2 sessions across all windows. Already-tracked sessions are left untouched. No parameters.

#### `adopt_tab`

Claim ownership of an unowned tab (e.g., one discovered from an existing session). Fails if another agent already owns it.

```json
{ "id": "bewildered-spectacles" }
```

### Terminal I/O

#### `write_to_terminal`

Write text to a tab's terminal. By default appends a newline and waits for output to settle before returning.

```json
{
  "id": "bewildered-spectacles",
  "text": "ls -la",
  "newline": true,
  "wait_for_output": true
}
```

Returns: `{ "success": true, "output_lines": 12 }`

The `output_lines` count tells the agent how much output the command produced, so it can decide whether to read it.

#### `read_terminal_output`

Read output from a tab. Two modes:

- **Incremental** (default): returns all new output since this agent's last read.
- **Last N lines**: returns the most recent N lines from the buffer.

```json
{ "id": "bewildered-spectacles", "lines": null }
```

Returns: `{ "output": "total 42\ndrwxr-xr-x ...", "num_lines": 12, "is_alive": true }`

#### `send_control_character`

Send a control character to a tab.

```json
{ "id": "bewildered-spectacles", "character": "c" }
```

Accepts: single letters (`"c"` for Ctrl+C), `"ctrl-c"` format, or special names (`"esc"`, `"tab"`, `"enter"`).

| Character | Meaning |
|-----------|---------|
| `c` | Ctrl+C (interrupt) |
| `d` | Ctrl+D (EOF) |
| `z` | Ctrl+Z (suspend) |
| `l` | Ctrl+L (clear) |
| `\\` | Ctrl+\\ (quit) |
| `esc` | Escape key |

#### `get_screen`

Snapshot the current visible screen of a tab, independent of the output buffer.

```json
{ "id": "bewildered-spectacles" }
```

Returns: `{ "screen": "user@mac ~ % ls\nfile1.txt  file2.txt\nuser@mac ~ % ", "num_lines": 24, "cursor_x": 15, "cursor_y": 2 }`

## Limitations

- **macOS only** — iTerm2 is a macOS terminal emulator. This project cannot work on Linux or Windows.
- **Requires iTerm2 running** — the server connects to a live iTerm2 instance. If iTerm2 isn't running or the Python API isn't enabled, it will fail to start.
- **Screen-based output, not byte-stream** — output is captured via screen snapshots rather than a raw PTY stream. This means:
  - Very fast output that scrolls past the screen between snapshots may be partially missed.
  - Programs that redraw the screen (e.g., `top`, `vim`, `htop`) will produce noisy diffs.
  - The output buffer contains logical lines, not raw terminal escape sequences.
- **No scrollback capture** — only the visible screen area is diffed. Lines that scroll into iTerm2's scrollback buffer between screen updates are captured via the diff, but extremely rapid output may outpace the streamer.
- **Single iTerm2 instance** — connects to whatever iTerm2 instance is running. Cannot target a specific instance if multiple are running (rare edge case).
- **Tab close is forceful** — `close_tab` sends a force-close to the iTerm2 session. Running processes in that tab will be terminated.
- **No file transfer** — the server provides terminal I/O, not file system access. Use other MCP tools for file operations.

## Security Decisions

### Localhost-only by default

The server binds to `127.0.0.1` by default, so it is not accessible from the network. Binding to `0.0.0.0` is supported but **not recommended** — anyone who can reach the port can control your terminal.

### Optional bearer token authentication

When `--token` is set, every MCP request must include an `Authorization: Bearer <token>` header. The token is compared using `secrets.compare_digest` to prevent timing attacks. Without `--token`, the server is unauthenticated (acceptable for localhost-only use).

### No program allowlist

Unlike the PTY-based predecessor, this server does not restrict which commands can be run — it sends text to iTerm2 tabs, which are full shell sessions. The agent can type anything the human could type. **The security boundary is iTerm2 itself and the user's shell permissions.**

### No environment variable injection

The server does not pass environment variables to iTerm2 sessions. Sessions inherit iTerm2's environment, which the user controls via their shell profile and iTerm2 preferences.

### Per-agent isolation

Each MCP session gets a stable agent label (`agent-1`, `agent-2`, etc.) with independent read cursors. Agents can only adopt unowned tabs — they cannot take ownership of another agent's tab. However, any agent can read from or write to any tracked tab (ownership is for bookkeeping, not access control).

### No automatic tab cleanup

When the server shuts down, it cancels its output monitors but **does not close iTerm2 tabs**. This is intentional — the user's terminal sessions should not be destroyed just because the MCP server stopped.

## Architecture

```
src/repl_mcp/
├── __init__.py      # Package version
├── __main__.py      # Headless CLI entry point
├── auth.py          # Bearer token middleware (Starlette)
├── manager.py       # ITermManager: connection, tabs, output monitoring
├── models.py        # Tab dataclass
└── server.py        # ITermMCPServer: FastMCP tool registration, ASGI app
```

### Data Flow

1. Agent calls `write_to_terminal(id="bewildered-spectacles", text="ls")`
2. `server.py` resolves the agent label, delegates to `manager.py`
3. `manager.py` looks up the iTerm2 session ID, calls `session.async_send_text("ls\n")`
4. iTerm2 executes the command in the tab — the user sees it happen in real time
5. `ScreenStreamer` fires in the background monitor task
6. Monitor diffs the screen, appends new lines to `tab.output_buffer`
7. `write_to_terminal` waits for output to settle, returns `{"output_lines": 12}`
8. Agent calls `read_terminal_output(id="bewildered-spectacles")` to get the actual output
9. Manager returns buffered lines since the agent's last read cursor position

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest -v

# Format code
black src/ tests/

# Lint
pylint src/repl_mcp/
```

## License

MIT
