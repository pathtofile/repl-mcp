"""Entry point for repl-mcp."""

import argparse
import json
import sys

from .auth import generate_token
from .manager import ProgramManager
from .server import ReplMCPServer
from .app import ReplMCPApp


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="repl-mcp",
        description="A TUI application and MCP server for managing interactive REPL programs",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8780,
        help="Port to listen on (default: 8780)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--allow",
        nargs="*",
        default=[],
        metavar="PROGRAM",
        help="List of allowed programs",
    )
    token_group = parser.add_mutually_exclusive_group()
    token_group.add_argument(
        "--token",
        type=str,
        default=None,
        help="Authentication token",
    )
    token_group.add_argument(
        "--generate-token",
        action="store_true",
        help="Generate a new authentication token and exit",
    )
    parser.add_argument(
        "--scrollback",
        type=int,
        default=10000,
        help="Number of scrollback lines to keep (default: 10000)",
    )
    parser.add_argument(
        "--startup-procs",
        type=str,
        default=None,
        metavar="FILE",
        help="Path to a JSON file listing programs to launch at startup",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Program and args to launch at startup (after --)",
    )
    return parser.parse_args(argv)


def _load_startup_procs(path: str) -> list[dict]:
    """Load and validate startup procs from a JSON file.

    Expected format: a JSON array of objects, each with at least a "command" key,
    and optional "args", "cwd", and "env" keys.
    """
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"--startup-procs file must contain a JSON array, got {type(data).__name__}")
    for i, entry in enumerate(data):
        if not isinstance(entry, dict) or "command" not in entry:
            raise ValueError(
                f"Entry {i} in --startup-procs must be an object with a \"command\" key"
            )
    return data


def main(argv: list[str] | None = None) -> None:
    """Main entry point."""
    args = parse_args(argv)

    # Handle token
    token = args.token
    if args.generate_token:
        token = generate_token()
        print(f"Generated token: {token}")
        sys.exit(0)

    # Build list of programs to start at launch
    startup_procs: list[dict] = []

    if args.startup_procs:
        try:
            startup_procs.extend(_load_startup_procs(args.startup_procs))
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            print(f"Error loading --startup-procs: {exc}", file=sys.stderr)
            sys.exit(1)

    # Handle trailing `-- <command> [args...]`
    cmd_remainder = args.command
    # argparse REMAINDER includes the leading '--' if present; strip it
    if cmd_remainder and cmd_remainder[0] == "--":
        cmd_remainder = cmd_remainder[1:]
    if cmd_remainder:
        startup_procs.append({"command": cmd_remainder[0], "args": cmd_remainder[1:]})

    # Create the program manager
    manager = ProgramManager()
    manager.scrollback_limit = args.scrollback
    if args.allow:
        manager.set_allowlist(args.allow)

    # Create MCP server
    server = ReplMCPServer(
        manager=manager,
        host=args.host,
        port=args.port,
        token=token,
    )

    # Create TUI app
    app = ReplMCPApp(
        manager=manager,
        server=server,
        port=args.port,
        token=token,
        scrollback=args.scrollback,
        startup_procs=startup_procs,
    )

    try:
        app.run()
    finally:
        # Ensure all managed programs are killed on exit, even if the TUI
        # crashed or was terminated without going through action_quit.
        manager.kill_all_sync()


if __name__ == "__main__":
    main()
