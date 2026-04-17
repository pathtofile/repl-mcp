# REPL-MCP — Detailed Implementation Plan

A Python TUI application and MCP server that lets AI agents start, interact with, and manage interactive REPL programs (Python, shells, gdb, etc.), while giving humans full visibility and control via a Textual-based TUI.

---

## Architecture Overview

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
│  │  (AI I/O)    │  port 2222 (configurable)      │
│  └──────────────┘                                │
└─────────────────────────────────────────────────┘
         │
         ▼
   ┌───────────┐  ┌───────────┐  ┌───────────┐
   │  PTY: gdb │  │ PTY: bash │  │PTY: python│
   └───────────┘  └───────────┘  └───────────┘
```

- **Single process**: TUI and HTTP MCP server run together in the same async event loop.
- **Streamable HTTP transport**: Modern MCP transport over HTTP with optional SSE streaming.
- **Full PTY**: Each managed program gets a pseudo-terminal for correct interactive behavior (prompts, colors, Ctrl+C, etc.).
- **Default port 2222**, overridable via `--port` CLI flag.

---

## MCP Tools

### 1. `start_program`
Start a new interactive program in a PTY.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `command` | string | yes | Program to run (e.g., `"python"`, `"gdb ./mybin"`) |
| `args` | list[string] | no | Additional arguments |
| `cwd` | string | no | Working directory (default: server's cwd) |
| `env` | dict[string, string] | no | Additional environment variables |

**Returns:** `{ "id": "<uuid>", "pid": <int>, "command": "<resolved command>" }`

**Allowlist enforcement:** Before starting, resolve the executable to its canonical absolute path via `shutil.which()` + `os.path.realpath()`. If an allowlist is configured and the resolved path doesn't match any allowed executable, return an error.

### 2. `send_input`
Send text/stdin to a running program.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `id` | string (uuid) | yes | Program ID |
| `input` | string | yes | Text to send (newline appended if not present) |

**Returns:** `{ "success": true }`

### 3. `send_signal`
Send a signal to a running program.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `id` | string (uuid) | yes | Program ID |
| `signal` | string | yes | Signal name: `"SIGINT"`, `"SIGTERM"`, `"SIGKILL"`, etc. |

**Returns:** `{ "success": true }`

### 4. `read_output`
Read new output from a program since the caller's last read.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `id` | string (uuid) | yes | Program ID |
| `timeout` | float | no | Max seconds to wait for new output (default: 0, instant return). Useful to avoid busy-polling — blocks up to this duration if no output is available yet. |

**Returns:** `{ "output": "<new text>", "is_running": true/false }`

Output is a single merged stream (stdout + stderr interleaved, as the PTY naturally provides). Delta-based: only returns output since the caller's last read for this program. Each agent tracks its own read cursor independently.

### 5. `list_programs`
List all managed programs.

**Parameters:** None.

**Returns:**
```json
[
  {
    "id": "<uuid>",
    "command": "python",
    "pid": 12345,
    "is_running": true,
    "owner_agent": "agent-1",
    "started_at": "2025-01-01T00:00:00Z"
  }
]
```

### 6. `kill_program`
Terminate a running program.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `id` | string (uuid) | yes | Program ID |

**Returns:** `{ "success": true }`

Sends SIGTERM, waits briefly, then SIGKILL if needed.

---

## TUI Design (Textual)

### Layout
- **Tab bar** at top: one tab per managed program, showing program name + status indicator (running/stopped).
- **Main area**: terminal output of the selected program's PTY. Scrollable with configurable scrollback (default 10,000 lines, `--scrollback` CLI flag).
- **Input bar** at bottom: text input for sending commands to the selected program. Human can type and press Enter.
- **Status bar**: server port, auth token (if enabled), connected agent count, program count.

### Color Coding
- **AI-sent commands**: displayed in a distinct color (e.g., cyan/blue) in the terminal output.
- **Human-sent commands**: displayed in a different color (e.g., green).
- **Program output**: default terminal colors (ANSI pass-through from PTY).

### Multi-Agent Display
- Each connected agent gets an auto-assigned label (e.g., `agent-1`, `agent-2`).
- Program tabs show the owning agent's label.
- Status bar shows count of connected agents.

### Idle Warning
- Programs with no I/O for a configurable duration show a warning indicator in their tab (e.g., amber/yellow icon).
- No auto-kill — human decides what to do.

---

## Program Manager

### Core Responsibilities
- Maintain a registry of all programs: UUID, PTY file descriptor, process handle, owner agent, output buffer, per-agent read cursors.
- Start programs in PTYs with proper environment.
- Async read loop per PTY: continuously read output, append to buffer, notify TUI.
- Enforce scrollback limit (default 10,000 lines, configurable).
- Track agent read cursors: each agent has its own position in each program's output buffer for delta reads.
- **Programs persist** after agent disconnect. Human can continue via TUI. Another agent can interact with them.

### Allowlist
- CLI: `--allow python gdb bash` (list of executable names).
- Resolution: `shutil.which(name)` -> `os.path.realpath(path)` -> store canonical paths.
- On `start_program`: resolve the requested command the same way, compare canonical path against allowlist.
- If no allowlist specified, all programs are allowed.

---

## Authentication

- **Optional**, controlled by `--token <value>` or `--generate-token` CLI flag.
- When enabled: agents must send `Authorization: Bearer <token>` header with all MCP requests.
- `--generate-token`: auto-generate a random token, display it in TUI and print to stdout on startup.
- When disabled (default): no auth, any localhost connection is accepted.

---

## CLI Interface

```
repl-mcp [OPTIONS]

Options:
  --port PORT            HTTP port to listen on (default: 2222)
  --allow PROGRAM...     Allowlist of programs that can be started (by name)
  --token TOKEN          Require this bearer token for MCP auth
  --generate-token       Auto-generate and display a bearer token
  --scrollback LINES     Max scrollback lines per program (default: 10000)
  --host HOST            Bind address (default: 127.0.0.1)
```

Installable via: `uvx repl-mcp` or `pip install repl-mcp`

---

## Project Structure

```
repl-mcp/
├── pyproject.toml          # Project metadata, dependencies, entry point, tool config
├── .pre-commit-config.yaml # Pre-commit hooks (black, pylint)
├── src/
│   └── repl_mcp/
│       ├── __init__.py
│       ├── __main__.py     # CLI entry point (argument parsing, app startup)
│       ├── app.py          # Textual app (TUI layout, widgets, event handling)
│       ├── server.py       # MCP HTTP server (Streamable HTTP transport, tool handlers)
│       ├── manager.py      # Program manager (PTY lifecycle, output buffers, read cursors)
│       ├── models.py       # Data models (Program, Agent, etc.)
│       └── auth.py         # Auth middleware (optional bearer token checking)
├── tests/
│   ├── test_manager.py     # Program manager tests (start, send, read, kill)
│   ├── test_server.py      # MCP server integration tests
│   └── test_allowlist.py   # Allowlist resolution and enforcement tests
└── README.md
```

---

## Dependencies

- **textual** — TUI framework
- **mcp** — MCP SDK for Python (provides Streamable HTTP server support)
- **uvicorn** or **hypercorn** — ASGI server (if needed by MCP SDK transport)
- **pyte** or direct PTY reading — terminal emulation / PTY management

Dev dependencies:
- **black** — formatter
- **pylint** — linter
- **pytest** — test runner
- **pytest-asyncio** — async test support
- **pre-commit** — git hooks

---

## Implementation Steps

### Phase 1: Project Scaffolding
1. Initialize `pyproject.toml` with metadata, dependencies, `[project.scripts]` entry point, and tool configs for black/pylint/pytest.
2. Create `.pre-commit-config.yaml` with black and pylint hooks.
3. Set up `src/repl_mcp/` package structure with `__init__.py` and `__main__.py`.
4. Verify `uv run repl-mcp --help` works (just argument parsing, no functionality).

### Phase 2: Program Manager (`manager.py`, `models.py`)
5. Define data models: `Program` (id, command, pid, pty_fd, output_buffer, is_running, owner_agent, started_at, per-agent read cursors).
6. Implement `ProgramManager.start_program()`: fork PTY, exec command, register in registry, start async output reader.
7. Implement PTY async read loop: read from PTY fd, append to output buffer, enforce scrollback limit.
8. Implement `ProgramManager.send_input()`: write to PTY fd.
9. Implement `ProgramManager.send_signal()`: send signal to process.
10. Implement `ProgramManager.read_output()`: return delta since agent's last read cursor, with optional timeout/wait.
11. Implement `ProgramManager.kill_program()`: SIGTERM, wait, SIGKILL if needed, cleanup.
12. Implement `ProgramManager.list_programs()`: return program status list.
13. Implement allowlist logic: resolve executable names to canonical paths, enforce on start.
14. Write tests: `test_manager.py` (start/send/read/kill lifecycle), `test_allowlist.py`.

### Phase 3: MCP Server (`server.py`, `auth.py`)
15. Set up MCP server using the `mcp` Python SDK with Streamable HTTP transport.
16. Register MCP tools: `start_program`, `send_input`, `send_signal`, `read_output`, `list_programs`, `kill_program`.
17. Wire tool handlers to `ProgramManager` methods.
18. Implement agent tracking: assign labels to connected agents, track per-agent read cursors.
19. Implement optional bearer token auth middleware.
20. Write tests: `test_server.py` (HTTP-level integration tests for MCP tools).

### Phase 4: TUI (`app.py`)
21. Create Textual app with tab-based layout: `TabbedContent` for programs, input bar, status bar.
22. Wire program output display: async update terminal view as new output arrives from PTY.
23. Implement human input: text input widget sends to selected program's PTY.
24. Implement color-coded command attribution (AI = cyan, human = green).
25. Show connected agents and program ownership in status bar and tab labels.
26. Add idle warning indicators on tabs for programs with no recent I/O.
27. Handle program lifecycle in TUI: new tab on start, status update on exit, allow closing dead tabs.

### Phase 5: Integration & CLI (`__main__.py`)
28. Wire together: start Textual app and MCP HTTP server in the same async event loop.
29. Implement full CLI argument parsing: `--port`, `--host`, `--allow`, `--token`, `--generate-token`, `--scrollback`.
30. Ensure clean shutdown: on TUI exit, stop HTTP server, optionally kill or orphan managed programs.
31. End-to-end manual testing: connect an AI agent (e.g., Claude Code) via MCP config, start programs, interact from both AI and TUI.

### Phase 6: Polish
32. Add README with usage instructions and MCP config example.
33. Verify `uvx repl-mcp` installation works.
34. Final linting/formatting pass.
