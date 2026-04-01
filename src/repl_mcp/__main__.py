"""Entry point for iterm2-mcp."""

import argparse
import asyncio
import logging
import sys

from .auth import generate_token
from .manager import ITermManager
from .server import ITermMCPServer


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="iterm2-mcp",
        description="MCP server for controlling iTerm2 tabs via the Python API",
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
    token_group = parser.add_mutually_exclusive_group()
    token_group.add_argument(
        "--token",
        type=str,
        default=None,
        help="Authentication token for MCP clients",
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
        help="Max output buffer lines per tab (default: 10000)",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Discover and track all existing iTerm2 sessions on startup",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> None:
    """Async main: connect to iTerm2 and start the MCP server."""
    manager = ITermManager()
    manager.scrollback_limit = args.scrollback

    server = ITermMCPServer(
        manager=manager,
        host=args.host,
        port=args.port,
        token=args.token,
    )

    # Connect to iTerm2 early so we fail fast if it's not available
    await manager.connect()

    if args.discover:
        tabs = await manager.discover_tabs()
        logging.getLogger(__name__).info("Discovered %d existing sessions", len(tabs))

    # Start serving (blocks until shutdown)
    await server.start()


def main(argv: list[str] | None = None) -> None:
    """Main entry point."""
    args = parse_args(argv)

    if args.generate_token:
        token = generate_token()
        print(f"Generated token: {token}")
        sys.exit(0)

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
