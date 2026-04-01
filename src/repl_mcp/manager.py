"""iTerm2 session manager — core logic for controlling iTerm2 tabs/panes."""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Callable

import iterm2

from .models import Tab

logger = logging.getLogger(__name__)

# How long to wait for output after writing to a terminal
OUTPUT_SETTLE_DELAY = 0.3
# Max time to wait for output to stabilize (stop changing)
OUTPUT_SETTLE_TIMEOUT = 5.0
# Polling interval when waiting for output to settle
OUTPUT_POLL_INTERVAL = 0.15
# Default scrollback buffer limit (lines)
DEFAULT_SCROLLBACK_LIMIT = 10000


class ITermManager:
    """Manages iTerm2 sessions via the Python API.

    Connects to a running iTerm2 instance over its Unix socket and provides
    methods to create/close tabs, send input, read output, and send control
    characters.  Supports multiple concurrent tabs and per-agent read cursors.
    """

    def __init__(self) -> None:
        self._connection: iterm2.Connection | None = None
        self._app: iterm2.App | None = None
        self._tabs: dict[str, Tab] = {}  # our_id -> Tab
        self._session_to_tab: dict[str, str] = {}  # iterm2 session_id -> our_id
        self._output_events: dict[str, asyncio.Event] = {}
        self._monitor_tasks: dict[str, asyncio.Task] = {}
        self.scrollback_limit: int = DEFAULT_SCROLLBACK_LIMIT

        # Callbacks (optional, for extensibility)
        self.on_output: Callable[[str, str], None] | None = None
        self.on_tab_created: Callable[[Tab], None] | None = None
        self.on_tab_closed: Callable[[Tab], None] | None = None

    # ------------------------------------------------------------------ #
    #  Connection lifecycle
    # ------------------------------------------------------------------ #

    async def connect(self) -> None:
        """Connect to iTerm2.  Requires iTerm2 to be running with the
        Python API enabled (Preferences > General > Magic > Enable Python API).
        """
        self._connection = await iterm2.Connection.async_create()
        self._app = await iterm2.async_get_app(self._connection)
        logger.info("Connected to iTerm2")

    async def disconnect(self) -> None:
        """Cancel all monitors and disconnect."""
        for task in self._monitor_tasks.values():
            task.cancel()
        self._monitor_tasks.clear()
        self._connection = None
        self._app = None
        logger.info("Disconnected from iTerm2")

    @property
    def connected(self) -> bool:
        return self._connection is not None and self._app is not None

    def _ensure_connected(self) -> None:
        if not self.connected:
            raise RuntimeError("Not connected to iTerm2. Call connect() first.")

    def _get_iterm_session(self, session_id: str) -> iterm2.Session:
        """Look up an iTerm2 Session object by its ID."""
        self._ensure_connected()
        session = self._app.get_session_by_id(session_id)
        if session is None:
            raise KeyError(f"iTerm2 session '{session_id}' no longer exists")
        return session

    # ------------------------------------------------------------------ #
    #  Tab lookup
    # ------------------------------------------------------------------ #

    def _get_tab(self, tab_id: str) -> Tab:
        """Get a tracked Tab by our human-readable ID."""
        try:
            return self._tabs[tab_id]
        except KeyError:
            raise KeyError(f"No tracked tab with id '{tab_id}'") from None

    def _ensure_unique_id(self, tab: Tab) -> None:
        """Ensure the tab's generated name doesn't collide."""
        while tab.id in self._tabs:
            from .models import _generate_unique_name

            tab.id = _generate_unique_name()

    # ------------------------------------------------------------------ #
    #  Tab management
    # ------------------------------------------------------------------ #

    async def create_tab(
        self,
        command: str | None = None,
        profile: str | None = None,
        window_id: str | None = None,
        owner_agent: str = "",
    ) -> dict:
        """Create a new iTerm2 tab and start tracking it.

        Args:
            command: Optional command to run immediately in the new tab.
            profile: iTerm2 profile name to use (default profile if None).
            window_id: Create tab in this window (current window if None).
            owner_agent: Agent label that owns this tab.

        Returns:
            Dict with id, session_id, and name.
        """
        self._ensure_connected()

        # Find the target window
        window = None
        if window_id:
            for w in self._app.windows:
                if w.window_id == window_id:
                    window = w
                    break
            if window is None:
                raise KeyError(f"No iTerm2 window with id '{window_id}'")
        else:
            window = self._app.current_terminal_window
            if window is None:
                raise RuntimeError("No iTerm2 window available. Open iTerm2 first.")

        # Create the tab
        tab_obj = await window.async_create_tab(profile=profile)
        session = tab_obj.current_session

        # Build our Tab model
        tab = Tab(
            session_id=session.session_id,
            tab_id=tab_obj.tab_id,
            window_id=window.window_id,
            name=command or session.name or "shell",
            is_alive=True,
            owner_agent=owner_agent,
        )
        self._ensure_unique_id(tab)

        self._tabs[tab.id] = tab
        self._session_to_tab[session.session_id] = tab.id
        self._output_events[tab.id] = asyncio.Event()

        # Start output monitor
        self._monitor_tasks[tab.id] = asyncio.create_task(self._output_monitor(tab))

        logger.info("Created tab %s (session=%s)", tab.id, session.session_id)

        # Run initial command if provided
        if command:
            await session.async_send_text(command + "\n")

        if self.on_tab_created:
            try:
                self.on_tab_created(tab)
            except Exception:
                logger.exception("on_tab_created callback error")

        return {"id": tab.id, "session_id": session.session_id, "name": tab.name}

    async def close_tab(self, tab_id: str) -> dict:
        """Close an iTerm2 tab and stop tracking it."""
        tab = self._get_tab(tab_id)

        try:
            session = self._get_iterm_session(tab.session_id)
            await session.async_close(force=True)
        except (KeyError, iterm2.RPCException):
            logger.warning("Session %s already closed", tab.session_id)

        return self._cleanup_tab(tab_id)

    def _cleanup_tab(self, tab_id: str) -> dict:
        """Remove a tab from tracking."""
        tab = self._tabs.pop(tab_id, None)
        if tab is None:
            return {"success": True}

        tab.is_alive = False
        self._session_to_tab.pop(tab.session_id, None)
        self._output_events.pop(tab_id, None)

        task = self._monitor_tasks.pop(tab_id, None)
        if task and not task.done():
            task.cancel()

        if self.on_tab_closed:
            try:
                self.on_tab_closed(tab)
            except Exception:
                logger.exception("on_tab_closed callback error")

        logger.info("Closed tab %s", tab_id)
        return {"success": True}

    async def list_tabs(self) -> list[dict]:
        """List all tracked tabs."""
        # Refresh aliveness from iTerm2
        for tab in list(self._tabs.values()):
            try:
                self._get_iterm_session(tab.session_id)
            except KeyError:
                tab.is_alive = False
        return [tab.to_dict() for tab in self._tabs.values()]

    async def discover_tabs(self, owner_agent: str = "") -> list[dict]:
        """Discover all existing iTerm2 sessions and start tracking untracked ones.

        Returns list of all tracked tabs (newly discovered + already tracked).
        """
        self._ensure_connected()
        newly_tracked = []

        for window in self._app.windows:
            for itab in window.tabs:
                for session in itab.sessions:
                    if session.session_id not in self._session_to_tab:
                        tab = Tab(
                            session_id=session.session_id,
                            tab_id=itab.tab_id,
                            window_id=window.window_id,
                            name=session.name or "shell",
                            is_alive=True,
                            owner_agent=owner_agent,
                        )
                        self._ensure_unique_id(tab)
                        self._tabs[tab.id] = tab
                        self._session_to_tab[session.session_id] = tab.id
                        self._output_events[tab.id] = asyncio.Event()
                        self._monitor_tasks[tab.id] = asyncio.create_task(
                            self._output_monitor(tab)
                        )
                        newly_tracked.append(tab)

        return [tab.to_dict() for tab in self._tabs.values()]

    async def adopt_tab(self, tab_id: str, agent_id: str) -> dict:
        """Adopt an unowned tab (or one you already own)."""
        tab = self._get_tab(tab_id)
        if tab.owner_agent and tab.owner_agent != agent_id:
            raise RuntimeError(f"Tab {tab_id} is already owned by '{tab.owner_agent}'")
        tab.owner_agent = agent_id
        logger.info("Agent %s adopted tab %s", agent_id, tab_id)
        return {"success": True, "id": tab.id, "owner_agent": agent_id}

    # ------------------------------------------------------------------ #
    #  Terminal I/O
    # ------------------------------------------------------------------ #

    async def write_to_terminal(
        self,
        tab_id: str,
        text: str,
        newline: bool = True,
        wait_for_output: bool = True,
        agent_id: str = "",
    ) -> dict:
        """Write text to an iTerm2 session (typically to run a command).

        Args:
            tab_id: Our tab ID.
            text: Text to send (a command, input, etc.).
            newline: Whether to append a newline (default True).
            wait_for_output: Wait briefly for output to settle (default True).
            agent_id: Agent making the write (for cursor tracking).

        Returns:
            Dict with success status and number of new output lines produced.
        """
        tab = self._get_tab(tab_id)
        session = self._get_iterm_session(tab.session_id)

        # Record buffer position before write
        pre_write_len = len(tab.output_buffer)

        # Send text
        payload = text + "\n" if newline and not text.endswith("\n") else text
        await session.async_send_text(payload)

        tab.last_activity = datetime.now(timezone.utc)

        if wait_for_output:
            # Wait for output to settle: keep polling until no new output
            # arrives for OUTPUT_SETTLE_DELAY, up to OUTPUT_SETTLE_TIMEOUT
            elapsed = 0.0
            last_len = len(tab.output_buffer)
            # Initial wait for first output to appear
            await asyncio.sleep(OUTPUT_SETTLE_DELAY)
            while elapsed < OUTPUT_SETTLE_TIMEOUT:
                current_len = len(tab.output_buffer)
                if current_len > last_len:
                    # New output arrived, reset settle timer
                    last_len = current_len
                    elapsed = 0.0
                else:
                    elapsed += OUTPUT_POLL_INTERVAL
                await asyncio.sleep(OUTPUT_POLL_INTERVAL)

        new_lines = len(tab.output_buffer) - pre_write_len

        return {"success": True, "output_lines": new_lines}

    async def read_terminal_output(
        self,
        tab_id: str,
        lines: int | None = None,
        agent_id: str = "",
    ) -> dict:
        """Read lines from a tab's output buffer.

        If `lines` is None, returns all new output since this agent's last read.
        If `lines` is set, returns the last N lines from the buffer.

        Args:
            tab_id: Our tab ID.
            lines: Number of lines to read (None = all new since last read).
            agent_id: Agent making the read.

        Returns:
            Dict with output text, line count, and is_alive status.
        """
        tab = self._get_tab(tab_id)

        if lines is not None:
            # Return the last N lines
            start = max(0, len(tab.output_buffer) - lines)
            result_lines = tab.output_buffer[start:]
            # Update cursor to current position
            tab.read_cursors[agent_id] = len(tab.output_buffer)
        else:
            # Return everything since last read
            cursor = tab.read_cursors.get(agent_id, 0)
            result_lines = tab.output_buffer[cursor:]
            tab.read_cursors[agent_id] = len(tab.output_buffer)

        output_text = "\n".join(result_lines)
        return {
            "output": output_text,
            "num_lines": len(result_lines),
            "is_alive": tab.is_alive,
        }

    async def send_control_character(self, tab_id: str, character: str) -> dict:
        """Send a control character to an iTerm2 session.

        Args:
            tab_id: Our tab ID.
            character: Control character name like "c" for Ctrl+C, "d" for Ctrl+D,
                       "z" for Ctrl+Z, "l" for Ctrl+L, "\\" for Ctrl+\\, etc.
                       Can also be a raw control character or "ctrl-X" format.

        Returns:
            Dict with success status.
        """
        tab = self._get_tab(tab_id)
        session = self._get_iterm_session(tab.session_id)

        ctrl_char = self._resolve_control_character(character)
        await session.async_send_text(ctrl_char)

        tab.last_activity = datetime.now(timezone.utc)
        logger.info("Sent control character '%s' to tab %s", character, tab_id)
        return {"success": True}

    async def get_screen(self, tab_id: str) -> dict:
        """Get the current visible screen contents of a tab.

        Returns a snapshot of what's currently visible in the terminal,
        independent of the output buffer / read cursors.
        """
        tab = self._get_tab(tab_id)
        session = self._get_iterm_session(tab.session_id)

        contents = await session.async_get_screen_contents()
        screen_lines = []
        for i in range(contents.number_of_lines):
            line = contents.line(i)
            screen_lines.append(line.string)

        return {
            "screen": "\n".join(screen_lines),
            "num_lines": contents.number_of_lines,
            "cursor_x": contents.cursor_coord.x,
            "cursor_y": contents.cursor_coord.y,
        }

    # ------------------------------------------------------------------ #
    #  Output monitoring
    # ------------------------------------------------------------------ #

    async def _output_monitor(self, tab: Tab) -> None:
        """Background task that monitors an iTerm2 session for screen changes
        and accumulates new output into the tab's buffer.

        Uses ScreenStreamer for change notifications, then diffs consecutive
        screen snapshots to extract new lines.
        """
        try:
            async with iterm2.ScreenStreamer(
                self._connection, tab.session_id
            ) as streamer:
                while True:
                    contents = await streamer.async_get()
                    current_lines = []
                    for i in range(contents.number_of_lines):
                        current_lines.append(contents.line(i).string)

                    # Diff against previous screen to find new output
                    new_lines = self._diff_screen(tab.last_screen_lines, current_lines)

                    if new_lines:
                        tab.output_buffer.extend(new_lines)
                        self._enforce_scrollback(tab)
                        tab.last_activity = datetime.now(timezone.utc)

                        # Wake any read waiters
                        event = self._output_events.get(tab.id)
                        if event:
                            event.set()
                            event.clear()

                        if self.on_output:
                            try:
                                self.on_output(tab.id, "\n".join(new_lines))
                            except Exception:
                                logger.exception("on_output callback error")

                    tab.last_screen_lines = current_lines

        except asyncio.CancelledError:
            return
        except iterm2.RPCException:
            logger.info("Session %s disconnected, stopping monitor", tab.session_id)
            tab.is_alive = False
        except Exception:
            logger.exception("Output monitor error for tab %s", tab.id)
            tab.is_alive = False

    @staticmethod
    def _diff_screen(prev_lines: list[str], curr_lines: list[str]) -> list[str]:
        """Extract new lines by comparing consecutive screen snapshots.

        Finds the overlap between the end of the previous screen and the
        start of the current screen (indicating scroll), then returns the
        lines that are genuinely new.

        If no overlap is found and the screen changed, returns all current
        lines (fresh screen / large jump).
        """
        if not prev_lines:
            # First snapshot — treat all non-empty lines as new
            return [line for line in curr_lines if line.strip()]

        if prev_lines == curr_lines:
            return []

        # Try to find overlap: the tail of prev matching the head of curr.
        # This detects how many lines scrolled off.
        best_overlap = 0
        max_check = min(len(prev_lines), len(curr_lines))
        for k in range(1, max_check + 1):
            if prev_lines[-k:] == curr_lines[:k]:
                best_overlap = k

        if best_overlap > 0:
            # New lines are everything after the overlapping prefix
            new = curr_lines[best_overlap:]
            return [line for line in new if line.strip()]

        # No overlap found — the screen jumped. Return lines that differ
        # from the previous screen (conservative: only lines that are new
        # and non-empty).
        prev_set = set(prev_lines)
        new = [line for line in curr_lines if line.strip() and line not in prev_set]
        return new

    def _enforce_scrollback(self, tab: Tab) -> None:
        """Trim output buffer and adjust read cursors."""
        if len(tab.output_buffer) > self.scrollback_limit:
            excess = len(tab.output_buffer) - self.scrollback_limit
            del tab.output_buffer[:excess]
            for agent_id in tab.read_cursors:
                tab.read_cursors[agent_id] = max(0, tab.read_cursors[agent_id] - excess)

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_control_character(char: str) -> str:
        """Convert a control character name to its actual character.

        Accepts formats:
          - Single letter: "c" -> Ctrl+C (\\x03)
          - "ctrl-c" or "ctrl+c" format
          - Raw control character passed through
          - Special names: "esc", "tab", "enter", "return"
        """
        # Normalize
        normalized = char.strip().lower()

        # Handle "ctrl-X" / "ctrl+X" format
        for prefix in ("ctrl-", "ctrl+", "ctrl "):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
                break

        # Special names
        specials = {
            "esc": "\x1b",
            "escape": "\x1b",
            "tab": "\t",
            "enter": "\r",
            "return": "\r",
            "backspace": "\x7f",
            "delete": "\x7f",
        }
        if normalized in specials:
            return specials[normalized]

        # Single letter -> control character (a=0x01, c=0x03, d=0x04, etc.)
        if len(normalized) == 1 and normalized.isalpha():
            return chr(ord(normalized) - ord("a") + 1)

        # Backslash -> Ctrl+\ (0x1c)
        if normalized == "\\":
            return "\x1c"

        # If it's already a raw control character, pass through
        if len(char) == 1 and ord(char) < 32:
            return char

        raise ValueError(
            f"Unknown control character: '{char}'. "
            "Use a letter (e.g. 'c' for Ctrl+C), 'ctrl-c' format, "
            "or a special name (esc, tab, enter)."
        )

    async def shutdown(self) -> None:
        """Cancel all monitors. Does NOT close iTerm2 tabs."""
        for task in self._monitor_tasks.values():
            if not task.done():
                task.cancel()
        self._monitor_tasks.clear()
        logger.info("Shutdown complete — monitors cancelled")
