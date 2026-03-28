# REPL-MCP Initial ideas

This will be a python project using `uv`, that will aim to provide a single MCP server to start and interact with interactive REPLs, such as python's REPL, shells, gdb, etc.

Here is a dump of the ideas I want to incorporate

## TUI design
This program should be a nice looking TUI, to enable a human to both view and manage connections from ai agents.

## Simple MCP commands
Provide MCP commands to
 - Start a new Program (returns a unique uuid to reference, and maybe a PID?) (also provide environment variables, cwd, etc)
 - Send a command/stdin to the program
 - Read output from the program
 - Kill the program

Can do other things if you think they'd be useful, but don't go overboard.

## Program interactable by both ai and humans.
When an agent starts a new REPL program, it should be possible for both the agent and a Human in the TUI to both see the output from the program, and also the human should be able to 'manually' interact with it.
This is sometimes useful to help manually intervene when an ai agent get's confused, or to use an agent to start a process for a human to finish/confirm it.

## MCP over HTTP
Use HTTP listening on localhost for the mcp transport. This will enable this to be run both locally and remotley via SSH tunnel.

## AI can run multiple programs
An agent can use this single mcp server to run and manage multiple programs at once.

## Optional allowlist of programs to start when starting MCP server
By default the MCP server can start any program, but optionally the user can specifcy on the commandline when starting the server an allowlist of programs that it can start. It can start any number of these, but errors on anything else.
For this, allow it to ask to start e.g. just `python`, but the server will work out exactly what program this is before checking allowlist, to ensure the agent can't manupilate cwd or environment variables to launch a non-allowed program.

## Don't overcomplicate
I prefer smaller more elegant solutions over a massive project, but also ensure things are readible and logical

## Use pyproject.toml, uv black, pylint, pytest and pre-commit
For linting and formatting, use pyproject.toml, uv black, pylint, pytest and pre-commit, with sensible defaults.
Don't write too many pytests, just enough to confirm functionality.

