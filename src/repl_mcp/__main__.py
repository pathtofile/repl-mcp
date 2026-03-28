"""Entry point for repl-mcp."""

import argparse
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Main entry point."""
    args = parse_args(argv)

    # Handle token
    token = args.token
    if args.generate_token:
        token = generate_token()
        print(f"Generated token: {token}")
        sys.exit(0)

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
    )

    try:
        app.run()
    finally:
        # Ensure all managed programs are killed on exit, even if the TUI
        # crashed or was terminated without going through action_quit.
        manager.kill_all_sync()


if __name__ == "__main__":
    main()
