"""MCP HTTP server for iterm2-mcp."""

import logging

from mcp.server.fastmcp import FastMCP, Context

from .manager import ITermManager
from .auth import BearerAuthMiddleware

logger = logging.getLogger(__name__)


class ITermMCPServer:
    """MCP server that exposes iTerm2 control tools.

    Each connected MCP session (agent) gets a stable label and independent
    read cursors, so multiple agents can interact with the same or different
    tabs without interfering with each other.
    """

    def __init__(
        self,
        manager: ITermManager,
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

        self.mcp = FastMCP("iterm2-mcp")
        self._register_tools()

    def _get_agent_label(self, ctx: Context) -> str:
        """Get or create a stable agent label for this MCP session."""
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
        return len(self._agents)

    # ------------------------------------------------------------------ #
    #  Tool registration
    # ------------------------------------------------------------------ #

    def _register_tools(self) -> None:
        """Register all MCP tool handlers."""

        # ----- Tab management -----

        @self.mcp.tool()
        async def list_tabs(ctx: Context) -> list[dict]:
            """List all tracked iTerm2 tabs/sessions.

            Returns a list of tabs with their IDs, names, and status.
            """
            self._get_agent_label(ctx)  # register agent
            return await self.manager.list_tabs()

        @self.mcp.tool()
        async def discover_tabs(ctx: Context) -> list[dict]:
            """Discover all existing iTerm2 sessions and start tracking them.

            Finds every open session across all windows and tabs in iTerm2
            and begins monitoring their output.  Already-tracked sessions
            are left untouched.

            Returns a list of all tracked tabs.
            """
            agent_label = self._get_agent_label(ctx)
            return await self.manager.discover_tabs(owner_agent=agent_label)

        @self.mcp.tool()
        async def create_tab(
            ctx: Context,
            command: str | None = None,
            profile: str | None = None,
            window_id: str | None = None,
        ) -> dict:
            """Create a new iTerm2 tab.

            Args:
                command: Optional command to run immediately in the new tab.
                profile: iTerm2 profile name (uses default if not set).
                window_id: Create the tab in a specific window (current window if not set).

            Returns:
                Dict with the tab's id, session_id, and name.
            """
            agent_label = self._get_agent_label(ctx)
            return await self.manager.create_tab(
                command=command,
                profile=profile,
                window_id=window_id,
                owner_agent=agent_label,
            )

        @self.mcp.tool()
        async def close_tab(id: str, ctx: Context) -> dict:
            """Close an iTerm2 tab and stop tracking it.

            Args:
                id: Tab ID (the human-readable name, e.g. "bewildered-spectacles").
            """
            agent_label = self._get_agent_label(ctx)
            logger.info("Agent %s closing tab %s", agent_label, id)
            return await self.manager.close_tab(id)

        @self.mcp.tool()
        async def adopt_tab(id: str, ctx: Context) -> dict:
            """Adopt an unowned tab so you can interact with it.

            Args:
                id: Tab ID.
            """
            agent_label = self._get_agent_label(ctx)
            return await self.manager.adopt_tab(id, agent_id=agent_label)

        # ----- Terminal I/O -----

        @self.mcp.tool()
        async def write_to_terminal(
            id: str,
            text: str,
            ctx: Context,
            newline: bool = True,
            wait_for_output: bool = True,
        ) -> dict:
            """Write text to an iTerm2 terminal, often used to run a command.

            Sends the text to the specified tab's session.  By default,
            appends a newline and waits briefly for output to settle.

            Returns the number of lines of output produced by the command.

            Args:
                id: Tab ID (the human-readable name).
                text: Text to write (a command, input, etc.).
                newline: Append a newline after the text (default True).
                wait_for_output: Wait for output to settle before returning (default True).
            """
            agent_label = self._get_agent_label(ctx)
            return await self.manager.write_to_terminal(
                tab_id=id,
                text=text,
                newline=newline,
                wait_for_output=wait_for_output,
                agent_id=agent_label,
            )

        @self.mcp.tool()
        async def read_terminal_output(
            id: str,
            ctx: Context,
            lines: int | None = None,
        ) -> dict:
            """Read output from an iTerm2 terminal.

            If `lines` is specified, returns the last N lines from the output
            buffer.  If `lines` is not specified, returns all new output since
            this agent's last read.

            Args:
                id: Tab ID (the human-readable name).
                lines: Number of lines to read.  Omit for "all new since last read".
            """
            agent_label = self._get_agent_label(ctx)
            return await self.manager.read_terminal_output(
                tab_id=id,
                lines=lines,
                agent_id=agent_label,
            )

        @self.mcp.tool()
        async def send_control_character(id: str, character: str, ctx: Context) -> dict:
            """Send a control character to an iTerm2 terminal.

            Common control characters:
              - "c" — Ctrl+C (interrupt / SIGINT)
              - "d" — Ctrl+D (EOF)
              - "z" — Ctrl+Z (suspend / SIGTSTP)
              - "l" — Ctrl+L (clear screen)
              - "\\\\" — Ctrl+\\\\ (SIGQUIT)
              - "esc" — Escape key

            Also accepts "ctrl-c", "ctrl+d", etc.

            Args:
                id: Tab ID (the human-readable name).
                character: The control character to send.
            """
            self._get_agent_label(ctx)
            return await self.manager.send_control_character(id, character)

        @self.mcp.tool()
        async def get_screen(id: str, ctx: Context) -> dict:
            """Get the current visible screen contents of an iTerm2 tab.

            Returns a snapshot of exactly what is visible in the terminal
            right now, including the cursor position.  This is independent
            of the read_terminal_output buffer.

            Args:
                id: Tab ID (the human-readable name).
            """
            self._get_agent_label(ctx)
            return await self.manager.get_screen(id)

    # ------------------------------------------------------------------ #
    #  ASGI app
    # ------------------------------------------------------------------ #

    def _build_app(self):
        """Build the ASGI app with trailing-slash handling and optional auth."""
        inner = self.mcp.streamable_http_app()

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

        # Connect to iTerm2 before serving
        await self.manager.connect()

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
        finally:
            await self.manager.shutdown()
