# repl-mcp

A TUI application and MCP server that lets AI agents start, interact with, and manage interactive REPL programs (Python, shells, gdb, etc.), while giving humans full visibility and control through a terminal UI.

```
┌─────────────────────────────────────────────────┐
│              repl-mcp (single process)           │
│                                                  │
│  ┌──────────────┐       ┌─────────────────────┐ │
│  │  Textual TUI │◄─────►│   Program Manager   │ │
│  │  (human I/O) │       │  (PTY pool, state)  │ │
│  └──────────────┘       └──────────┬──────────┘ │
│                                    │             │
│  ┌──────────────┐                  │             │
│  │  MCP HTTP    │◄─────────────────┘             │
│  │  Server      │  Streamable HTTP               │
│  │  (AI I/O)    │  port 8780 (configurable)      │
│  └──────────────┘                                │
└─────────────────────────────────────────────────┘
         │
         ▼
   ┌───────────┐  ┌───────────┐  ┌───────────┐
   │  PTY: gdb │  │ PTY: bash │  │PTY: python│
   └───────────┘  └───────────┘  └───────────┘
```

## Features

- **Full PTY support** — each managed program gets a real pseudo-terminal for correct interactive behavior (prompts, line editing, Ctrl+C, colors)
- **MCP Streamable HTTP server** — AI agents connect over HTTP and use standard MCP tools to start programs, send input, read output, and send signals
- **Textual TUI** — tabbed interface showing all managed programs with scrollable output, human input bar, and live status
- **Color-coded I/O** — AI input shown in cyan, human input in green, program output in default terminal color
- **Per-agent read cursors** — multiple agents can independently read output from the same program without missing data
- **Program allowlist** — optionally restrict which executables agents can launch
- **Bearer token auth** — optional authentication for the MCP endpoint
- **Idle detection** — programs with no I/O for 60+ seconds show a yellow idle indicator
- **Human-readable IDs** — programs get memorable names like `bewildered-spectacles` instead of UUIDs
- **Human-created programs** — start programs from the TUI with `Ctrl+N` (with optional working directory and environment variables); any agent can interact with any program by ID

## Installation

Requires Python 3.11+.

```bash
# Clone and install
git clone https://github.com/youruser/repl-mcp.git
cd repl-mcp
pip install -e .

# Or install with dev dependencies
pip install -e ".[dev]"
```

## Usage

### Basic

```bash
# Start with defaults (port 8780, no auth)
repl-mcp

# Custom port
repl-mcp --port 9000

# With authentication
repl-mcp --token my-secret-token

# Generate a random token and start
repl-mcp --token $(repl-mcp --generate-token 2>/dev/null)

# Restrict allowed programs
repl-mcp --allow python gdb bash

# All options
repl-mcp --host 0.0.0.0 --port 9000 --token secret --allow python bash --scrollback 50000
```

### CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--port PORT` | `8780` | Port for the MCP HTTP server |
| `--host HOST` | `127.0.0.1` | Host to bind to |
| `--token TOKEN` | none | Bearer token for authentication |
| `--generate-token` | — | Print a random token and exit |
| `--allow PROGRAM...` | all allowed | Restrict which programs agents can start |
| `--scrollback LINES` | `10000` | Max output lines kept per program |
| `--startup-procs FILE` | none | YAML file listing programs to launch at startup |

#### Startup procs file

The `--startup-procs` file is a YAML list of programs to launch when repl-mcp starts. Each entry has a `command` (a full command line, split with shell rules) and optional `cwd`, `env`, and `initial_input` fields:

```yaml
- command: python -i
  cwd: /path/to/project
  env:
    PYTHONDONTWRITEBYTECODE: "1"

- command: ssh user@host
  initial_input: my-password

- command: node server/server.js 192.168.1.180 4455 --log-file
  cwd: /path/to/nodeshell
```

### TUI Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+N` | Start a new program (with optional working dir, env vars, and initial input) |
| `Ctrl+T` | Focus the input bar |
| `Ctrl+C` | Copy active program's unique ID to clipboard |
| `Ctrl+Q` | Quit |
| Tab click | Switch between managed programs |

> **Tip:** Use `Ctrl+C` (or `Cmd+C` on macOS, depending on your terminal) while a program's tab is selected to copy its unique name (e.g. `noxious-penny`) to your clipboard. You can then paste it directly into a Claude Code conversation to reference that program by ID.

### Connecting Claude Code

Add repl-mcp to your Claude Code MCP settings. You can configure it at the project level (`.claude/settings.json`) or user level (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "repl-mcp": {
      "type": "url",
      "url": "http://127.0.0.1:8780/mcp"
    }
  }
}
```

With bearer token authentication:

```json
{
  "mcpServers": {
    "repl-mcp": {
      "type": "url",
      "url": "http://127.0.0.1:8780/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN_HERE"
      }
    }
  }
}
```

You can also add it via the CLI:

```bash
claude mcp add repl-mcp --transport http http://127.0.0.1:8780/mcp
```

### Connecting Other MCP Clients

The MCP server exposes a Streamable HTTP endpoint at `http://127.0.0.1:8780/mcp` (or your custom host/port). Any MCP-compatible client can connect using the Streamable HTTP transport.

For clients that require manual configuration, the key details are:
- **Transport**: Streamable HTTP
- **URL**: `http://<host>:<port>/mcp`
- **Auth** (if enabled): `Authorization: Bearer <token>` header

## MCP Tools

### `start_program`

Start a new interactive program in a PTY.

```json
{
  "command": "python",
  "args": ["-i"],
  "cwd": "/path/to/project",
  "env": {"PYTHONDONTWRITEBYTECODE": "1"},
  "initial_input": "import os"
}
```

The optional `initial_input` field sends text to the program immediately after it starts — useful for automatically typing passwords, setup commands, etc.

Returns: `{ "id": "bewildered-spectacles", "pid": 1234, "command": "/usr/bin/python" }`

### `send_input`

Send text to a running program's stdin. A newline is appended if not present.

```json
{ "id": "<program-id>", "input": "print('hello')" }
```

### `read_output`

Read new output since the caller's last read. Each agent has an independent cursor.

```json
{ "id": "<program-id>", "timeout": 2.0 }
```

Returns: `{ "output": ">>> hello\n", "is_running": true }`

The `timeout` parameter (seconds) enables long-polling — the call blocks until output is available or the timeout expires. Use `0` for an instant check.

### `send_signal`

Send a Unix signal to a running program.

```json
{ "id": "<program-id>", "signal": "SIGINT" }
```

### `list_programs`

List all managed programs (no parameters).

Returns an array of `{ id, command, pid, is_running, started_at }`.

### `kill_program`

Gracefully terminate a program (SIGTERM, then SIGKILL after 2s).

```json
{ "id": "<program-id>" }
```

## Claude Code Skill

This project includes a built-in [Claude Code skill](https://code.claude.com/docs/en/skills.md) at `.claude/skills/repl/SKILL.md` that teaches AI agents how to use the repl-mcp server effectively.

### What the skill provides

When an agent invokes `/repl` (or Claude auto-invokes it based on context), it gets:

- **Tool reference** — how to call each MCP tool with examples
- **Recommended workflow** — the start/read/send/read/kill loop with proper timeouts
- **Human escalation patterns** — when and how to ask the human operator for help through the TUI (auth prompts, hangs, ambiguous errors, destructive operations)
- **Best practices** — avoid busy-polling, check `is_running`, clean up programs

### Human-in-the-loop

The skill explicitly teaches agents to escalate to the human watching the TUI when they get stuck. For example:

- **Auth prompts**: *"Please enter your credentials in the TUI input bar."*
- **Program hangs**: *"Could you check the repl-mcp TUI and see if there's a prompt I'm missing?"*
- **Destructive ops**: *"Please confirm this operation in the TUI before I proceed."*

This keeps the human in control while letting agents handle routine interactions autonomously.

### Using in other projects

Copy the `.claude/skills/repl/` directory into any project where agents should have access to a running repl-mcp server. The skill will appear in Claude Code's skill menu automatically.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with verbose output
pytest -v

# Format code
black src/ tests/

# Lint
pylint src/repl_mcp/
```

## Project Structure

```
.claude/skills/repl/
└── SKILL.md         # Agent skill: how to use the repl-mcp server

src/repl_mcp/
├── __init__.py      # Package version
├── __main__.py      # CLI entry point, wires TUI + MCP server
├── app.py           # Textual TUI application
├── auth.py          # Bearer token middleware
├── manager.py       # Program manager (PTY lifecycle, I/O, cursors)
├── models.py        # Program and Agent dataclasses
└── server.py        # MCP server (tool registration, HTTP transport)

tests/
├── test_allowlist.py
├── test_manager.py
└── test_server.py
```

## License

MIT
