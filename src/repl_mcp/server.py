"""MCP HTTP server for repl-mcp."""

import logging

from mcp.server.fastmcp import FastMCP, Context

from .manager import ProgramManager
from .auth import BearerAuthMiddleware, generate_token

logger = logging.getLogger(__name__)


class ReplMCPServer:
    """MCP server that exposes REPL management tools."""

    def __init__(
        self,
        manager: ProgramManager,
        host: str = "127.0.0.1",
        port: int = 8780,
        token: str | None = None,
    ):
        self.manager = manager
        self.host = host
        self.port = port
        self.token = token
        self._agent_counter = 0
        self._agents: dict[int, str] = {}  # id(session) -> label

        self.mcp = FastMCP("repl-mcp")
        self._register_tools()

    def _get_agent_label(self, ctx: Context) -> str:
        """Get or create a stable agent label from the MCP session.

        Uses id(session) as the key so all tool calls within the same
        MCP session share one label and one set of read cursors.
        """
        session_key = id(ctx.session)
        if session_key in self._agents:
            return self._agents[session_key]
        self._agent_counter += 1
        label = f"agent-{self._agent_counter}"
        self._agents[session_key] = label
        logger.info("New agent connected: %s", label)
        return label

    @property
    def agent_count(self) -> int:
        """Return the number of connected agents."""
        return len(self._agents)

    def _register_tools(self) -> None:
        """Register all MCP tool handlers."""

        @self.mcp.tool()
        async def start_program(
            command: str,
            ctx: Context,
            args: list[str] | None = None,
            cwd: str | None = None,
            env: dict[str, str] | None = None,
            initial_input: str | None = None,
        ) -> dict:
            """Start a new interactive program in a PTY.

            Args:
                command: Program to run (e.g., "python", "gdb ./mybin")
                args: Additional arguments
                cwd: Working directory (default: server's cwd)
                env: Additional environment variables
                initial_input: Text to send to the program immediately after it starts
            """
            agent_label = self._get_agent_label(ctx)
            return await self.manager.start_program(
                command=command,
                args=args or [],
                cwd=cwd,
                env=env or {},
                owner_agent=agent_label,
                initial_input=initial_input,
            )

        @self.mcp.tool()
        async def send_input(id: str, input: str, ctx: Context) -> dict:
            """Send text/stdin to a running program.

            Args:
                id: Program name
                input: Text to send (newline appended if not present)
            """
            agent_label = self._get_agent_label(ctx)
            return await self.manager.send_input(id, input, source="ai", agent_id=agent_label)

        @self.mcp.tool()
        async def send_signal(id: str, signal: str, ctx: Context) -> dict:
            """Send a signal to a running program.

            Args:
                id: Program name
                signal: Signal name (SIGINT, SIGTERM, SIGKILL, etc.)
            """
            agent_label = self._get_agent_label(ctx)
            logger.info("Agent %s sending %s to %s", agent_label, signal, id)
            return await self.manager.send_signal(id, signal)

        @self.mcp.tool()
        async def read_output(id: str, ctx: Context, timeout: float = 0) -> dict:
            """Read new output from a program since the caller's last read.

            Args:
                id: Program name
                timeout: Max seconds to wait for new output (default: 0, instant return)
            """
            agent_label = self._get_agent_label(ctx)
            return await self.manager.read_output(id, agent_id=agent_label, timeout=timeout)

        @self.mcp.tool()
        async def adopt_program(id: str, ctx: Context) -> dict:
            """Adopt an unowned program (e.g. one created by the human operator).

            This sets you as the owner so you can interact with it.

            Args:
                id: Program name
            """
            agent_label = self._get_agent_label(ctx)
            return await self.manager.adopt_program(id, agent_id=agent_label)

        @self.mcp.tool()
        async def list_programs() -> list[dict]:
            """List all managed programs."""
            return self.manager.list_programs()

        @self.mcp.tool()
        async def kill_program(id: str, ctx: Context) -> dict:
            """Terminate a running program. Sends SIGTERM, then SIGKILL if needed.

            Args:
                id: Program name
            """
            agent_label = self._get_agent_label(ctx)
            logger.info("Agent %s killing program %s", agent_label, id)
            return await self.manager.kill_program(id)

    def _build_app(self):  # -> ASGI app callable
        """Build the ASGI app, handling trailing-slash and optional auth."""
        inner = self.mcp.streamable_http_app()

        # Wrap with a lightweight ASGI middleware that strips trailing slashes
        # so /mcp/ is handled the same as /mcp (avoids 307 redirects that
        # many MCP clients don't follow).  Must pass through lifespan events
        # unchanged so the MCP session manager initializes correctly.
        async def strip_trailing_slash(scope, receive, send):
            if scope["type"] == "http" and scope["path"].endswith("/") and scope["path"] != "/":
                scope = dict(scope, path=scope["path"].rstrip("/"))
            await inner(scope, receive, send)

        app = strip_trailing_slash

        if self.token:
            app = BearerAuthMiddleware(app, self.token)

        return app

    async def start(self) -> None:
        """Start the MCP server using uvicorn."""
        import uvicorn

        app = self._build_app()

        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        try:
            await server.serve()
        except Exception:
            logger.exception("MCP server failed to start on %s:%s", self.host, self.port)
            raise

    def get_starlette_app(self):  # -> ASGI app callable
        """Get the Starlette/ASGI app for embedding in another server."""
        return self._build_app()
