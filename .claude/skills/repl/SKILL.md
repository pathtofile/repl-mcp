---
name: repl
description: Use this skill when you need to control iTerm2 terminals via the iterm2-mcp server. Provides guidance on creating tabs, running commands, reading output, sending control characters, and asking the human operator for help when stuck.
argument-hint: "[task description]"
---

# Using the iterm2-mcp Server

You have access to an iterm2-mcp server that controls iTerm2 tabs via its Python API. A human operator can see everything you do in real time in their iTerm2 windows.

The MCP server is running at `http://127.0.0.1:8780/mcp` (default) and exposes the tools below. All tools target tabs by their human-readable ID (e.g. `bewildered-spectacles`).

## Available MCP Tools

### Creating a Tab

Use `create_tab` to open a new iTerm2 tab:

```json
{ "command": "python3 -i", "profile": "Default" }
```

Returns a tab `id` (a human-readable name like `bewildered-spectacles`) you'll use for all subsequent interactions. If `command` is provided, it runs immediately in the new tab.

### Writing to the Terminal

Use `write_to_terminal` to send text (usually a command) to a tab:

```json
{ "id": "<tab-id>", "text": "ls -la", "newline": true, "wait_for_output": true }
```

- By default, a newline is appended and the server waits for output to settle before returning.
- Returns `output_lines` — how many lines of output the command produced.
- Set `wait_for_output: false` if you don't need to wait (e.g. for long-running commands).

### Reading Output

Use `read_terminal_output` to get output from a tab:

```json
{ "id": "<tab-id>", "lines": null }
```

Two modes:
- **Incremental** (default, `lines: null`): Returns all new output since your last read. Each agent tracks its own cursor independently.
- **Last N lines** (`lines: 20`): Returns the most recent N lines from the buffer regardless of cursor position.

Returns `output`, `num_lines`, and `is_alive`.

### Sending Control Characters

Use `send_control_character` to send Ctrl+C, Ctrl+D, etc.:

```json
{ "id": "<tab-id>", "character": "c" }
```

Common characters:
| Input | Effect |
|-------|--------|
| `"c"` | Ctrl+C (interrupt) |
| `"d"` | Ctrl+D (EOF) |
| `"z"` | Ctrl+Z (suspend) |
| `"l"` | Ctrl+L (clear screen) |
| `"\\"` | Ctrl+\\ (quit) |
| `"esc"` | Escape key |

Also accepts `"ctrl-c"`, `"ctrl+d"` format.

### Getting a Screen Snapshot

Use `get_screen` to see exactly what's visible in a tab right now:

```json
{ "id": "<tab-id>" }
```

Returns the full visible screen text, line count, and cursor position (x, y). This is independent of the output buffer — it's a live snapshot.

### Listing and Discovering Tabs

- `list_tabs` — list all tracked tabs with their IDs, names, and status.
- `discover_tabs` — find all existing iTerm2 sessions and start tracking untracked ones. Useful when the server starts after iTerm2 already has sessions open.

### Adopting a Tab

Use `adopt_tab` to claim ownership of an unowned tab (e.g. one found via `discover_tabs`):

```json
{ "id": "<tab-id>" }
```

Fails if another agent already owns it. Check `list_tabs` for tabs with empty `owner_agent`.

### Closing a Tab

Use `close_tab` to close an iTerm2 tab and stop tracking it:

```json
{ "id": "<tab-id>" }
```

This force-closes the session — any running process in that tab will be terminated.

## Recommended Workflow

1. **Create** a tab for your task (`create_tab` with optional command)
2. **Read** initial output to see the prompt (`read_terminal_output`)
3. **Write** your command (`write_to_terminal`)
4. **Read** the result (`read_terminal_output`)
5. Repeat steps 3-4 as needed
6. **Close** when done (`close_tab`)

### Example: Python REPL Session

```
1. create_tab(command="python3 -i")              → id = "bewildered-spectacles"
2. read_terminal_output(id=..., lines=5)          → "Python 3.x.x ...\n>>>"
3. write_to_terminal(id=..., text="2 + 2")        → output_lines = 2
4. read_terminal_output(id=...)                   → "4\n>>>"
5. write_to_terminal(id=..., text="exit()")
6. close_tab(id=...)
```

### Example: Discovering Existing Sessions

```
1. discover_tabs()                                → tracks all existing sessions
2. list_tabs()                                    → find tab with empty owner_agent
3. adopt_tab(id="bewildered-spectacles")          → you now own it
4. get_screen(id="bewildered-spectacles")         → see current screen state
5. write_to_terminal(id=..., text="whoami")
6. read_terminal_output(id=...)                   → "username"
```

### Example: Running a Build and Checking Output

```
1. create_tab(command="make build")               → starts build
2. write_to_terminal(id=..., text="make build", wait_for_output=false)
   ... later ...
3. read_terminal_output(id=..., lines=50)         → last 50 lines of build output
4. get_screen(id=...)                             → check if build finished
```

### Example: Interrupting a Stuck Process

```
1. write_to_terminal(id=..., text="./long_running_script.sh")
   ... output_lines=0 after wait, or process seems stuck ...
2. send_control_character(id=..., character="c")  → Ctrl+C
3. read_terminal_output(id=...)                   → "^C\nInterrupted"
```

## When You Get Stuck — Ask the Human

The human can see everything you do in iTerm2 in real time. If you encounter a situation you cannot resolve, **ask the human for help**.

### When to escalate:

- **Authentication prompts** — if a program asks for a password, SSH passphrase, or 2FA code: *"The program is asking for authentication. Please type your credentials directly in the iTerm2 tab."*
- **Interactive TUI programs** — if a program shows a menu, confirmation dialog, or fullscreen UI (like vim, top) that you can't navigate: ask the human to handle it in iTerm2.
- **Ambiguous errors** — if output is confusing after 2-3 attempts, describe what you've tried and ask the human to look at the iTerm2 tab.
- **Destructive operations** — before running commands that delete data, modify production systems, or are irreversible, ask the human to confirm.
- **Program hangs** — if output isn't appearing and the tab is still alive: *"The program appears to be hanging. Could you check the iTerm2 tab and see if there's a prompt I'm missing?"*

### How to ask:

Be specific about which tab and what you need:

- "The Python debugger is asking for input I can't determine. Please type your response directly in the iTerm2 tab named 'python3 -i' and let me know when done."
- "I need your sudo password. Please enter it directly in the iTerm2 tab."
- "The command seems stuck. Could you check the iTerm2 tab and press Ctrl+C if needed?"

After the human acts, call `read_terminal_output` or `get_screen` to see the new state.

## Tips

- **Read before writing.** Always read pending output before sending the next command so you know the program is ready for input.
- **Use `get_screen` for orientation.** When you're not sure what state the terminal is in, `get_screen` gives you a live snapshot independent of the buffer.
- **Check `is_alive`.** If a tab's session has ended, `is_alive` will be `false`. Don't keep writing to a dead session.
- **Use `lines` for context.** When you need the last N lines regardless of your cursor position, pass `lines=N` to `read_terminal_output`.
- **Omit `lines` for incremental reads.** When following a running process, omit `lines` to get just the new output since your last read.
- **Use control characters, not tab closures.** If a command is hung, try `send_control_character("c")` before resorting to `close_tab`.
- **One command at a time.** Send a single command, read the output, then decide. Don't batch commands.
- **Clean up.** Close tabs when done to keep the iTerm2 workspace tidy.
