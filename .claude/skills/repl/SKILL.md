---
name: repl
description: Use this skill when you need to run interactive programs (Python REPL, shells, gdb, etc.) via the repl-mcp server. Provides guidance on starting programs, sending input, reading output, and asking the human operator for help when stuck.
argument-hint: "[task description]"
---

# Using the repl-mcp Server

You have access to a repl-mcp server that manages interactive REPL programs in real PTYs. A human operator is watching the TUI and can intervene at any time.

The MCP server is running at `http://127.0.0.1:8780/mcp` (default) and exposes the tools below.

## Available MCP Tools

### Starting a Program

Use `start_program` to launch an interactive program:

```json
{ "command": "python", "args": ["-i"], "cwd": "/path/to/project" }
```

Returns a program `id` (a human-readable name like `bewildered-spectacles`) you'll use for all subsequent interactions. The command is resolved to an absolute path and checked against the allowlist (if configured).

### Sending Input

Use `send_input` to type into the program:

```json
{ "id": "<program-id>", "input": "print('hello world')" }
```

A newline is appended automatically. Send exactly what you'd type at the keyboard.

### Reading Output

Use `read_output` to get new output since your last read:

```json
{ "id": "<program-id>", "timeout": 3.0 }
```

- Set `timeout` to wait for output (avoids busy-polling). Use 2-5 seconds for interactive sessions.
- Set `timeout` to 0 for an instant check.
- Output is delta-based: you only get what's new since your last read.
- Returns `is_running` so you know if the program has exited.

### Sending Signals

Use `send_signal` to interrupt or control the program:

```json
{ "id": "<program-id>", "signal": "SIGINT" }
```

Common signals: `SIGINT` (Ctrl+C), `SIGTERM` (graceful stop), `SIGKILL` (force kill).

### Listing Programs

Use `list_programs` (no parameters) to see all managed programs and their status.

### Adopting a Human-Created Program

The human operator can start programs directly from the TUI (Ctrl+N). These programs have no owner agent. Use `adopt_program` to claim ownership:

```json
{ "id": "<program-id>" }
```

After adopting, you can send input, read output, and manage the program normally. Use `list_programs` to discover unowned programs (those with an empty `owner_agent`).

### Killing a Program

Use `kill_program` to terminate a program when you're done:

```json
{ "id": "<program-id>" }
```

Sends SIGTERM, waits 2 seconds, then SIGKILL if needed.

## Recommended Workflow

1. **Start** the program you need (`start_program`)
2. **Read** initial output to see the prompt (`read_output` with timeout 2-3s)
3. **Send** your command (`send_input`)
4. **Read** the result (`read_output` with timeout 3-5s)
5. Repeat steps 3-4 as needed
6. **Kill** when done (`kill_program`)

### Example: Python REPL Session

```
1. start_program(command="python", args=["-i"])  →  get program_id
2. read_output(id=program_id, timeout=2)          →  "Python 3.x.x ...\n>>>"
3. send_input(id=program_id, input="2 + 2")
4. read_output(id=program_id, timeout=2)          →  "4\n>>>"
5. send_input(id=program_id, input="exit()")
6. read_output(id=program_id, timeout=1)          →  is_running=false
```

### Example: Adopting a Human-Created Program

```
1. list_programs()                                   →  find program with empty owner_agent
2. adopt_program(id="bewildered-spectacles")         →  success, you now own it
3. read_output(id="bewildered-spectacles", timeout=2) →  see current state
4. send_input(id="bewildered-spectacles", input="...")
```

### Example: Debugging with gdb

```
1. start_program(command="gdb", args=["./mybin"])
2. read_output(id=..., timeout=3)                 →  gdb banner + "(gdb)"
3. send_input(id=..., input="break main")
4. read_output(id=..., timeout=2)                 →  breakpoint confirmation
5. send_input(id=..., input="run")
6. read_output(id=..., timeout=5)                 →  breakpoint hit info
```

## When You Get Stuck — Ask the Human

A human operator is watching the TUI in real time. They can see everything you send and everything the program outputs. If you encounter a situation you cannot resolve on your own, **ask the human to help directly through the TUI**.

### When to escalate to the human:

- **Authentication prompts** — if the program asks for a password, SSH passphrase, or 2FA code, tell the user: *"The program is asking for authentication. Please enter your credentials in the TUI input bar."*
- **Unexpected interactive prompts** — if a program shows a menu, confirmation dialog, or TUI of its own that you can't navigate via text input, ask the human to handle it.
- **Ambiguous errors** — if output is confusing or you're unsure what went wrong after 2-3 attempts, describe what you've tried and ask the human to take a look at the TUI.
- **Destructive operations** — before running commands that delete data, modify production systems, or are otherwise irreversible, ask the human to confirm or do it themselves.
- **Program hangs** — if `read_output` keeps returning empty output and `is_running` is still true after several attempts with increasing timeouts, tell the user: *"The program appears to be hanging. You can check the TUI and try sending input or Ctrl+C from the input bar."*

### How to ask:

Simply tell the user in your response what you need them to do. Be specific:

- "The Python debugger is asking for input I can't determine. Please type the response in the repl-mcp TUI (the tab labeled 'python') and let me know when you're done."
- "I need you to enter your sudo password in the repl-mcp TUI. The program is waiting for input in the 'bash' tab."
- "The program seems stuck. Could you check the repl-mcp TUI and see if there's a prompt I'm missing?"

After the human acts, call `read_output` to see the new state and continue.

## Tips

- **Don't busy-poll.** Always use a `timeout` of 1-5 seconds on `read_output` rather than calling it in a tight loop with timeout=0.
- **Read before sending.** Always read pending output before sending the next command so you know the program is ready.
- **Check `is_running`.** If a program exits unexpectedly, `is_running` will be `false` in the `read_output` response. Don't keep sending input to a dead program.
- **One command at a time.** Send a single command, read the output, then decide what to do next. Don't batch multiple commands unless the program expects it.
- **Use signals when needed.** If a command is taking too long or you want to cancel, send `SIGINT` rather than killing the whole program.
- **Clean up.** Kill programs when you're done with them to free resources.
