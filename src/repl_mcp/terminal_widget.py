"""Terminal emulator widget using pyte for authentic REPL display."""

from __future__ import annotations

import fcntl
import os
import struct
import termios
from collections import deque

import pyte
from rich.style import Style
from rich.text import Text
from textual.events import Key, Resize
from textual.timer import Timer
from textual.widgets import RichLog, Static
from textual.widget import Widget
from textual.app import ComposeResult

# Shared default style — avoids constructing a new Style per default cell
_DEFAULT_STYLE = Style()

# App-level key bindings that should NOT be sent to the PTY
_APP_BINDINGS = frozenset({"ctrl+q", "ctrl+n"})

# Default terminal dimensions
DEFAULT_COLS = 80
DEFAULT_ROWS = 24

# How often to refresh the screen at most (seconds). ~60 fps.
_REFRESH_INTERVAL = 1 / 60


def _char_style(char) -> Style:
    """Build a Rich Style from a pyte Char, returning the shared default when possible."""
    if (
        char.fg == "default"
        and char.bg == "default"
        and not char.bold
        and not char.italics
        and not char.underscore
        and not char.strikethrough
        and not char.reverse
    ):
        return _DEFAULT_STYLE
    return Style(
        color=_pyte_color_to_rich(char.fg),
        bgcolor=_pyte_color_to_rich(char.bg),
        bold=char.bold,
        italic=char.italics,
        underline=char.underscore,
        strike=char.strikethrough,
        reverse=char.reverse,
    )


def _pyte_color_to_rich(color: str) -> str | None:
    """Convert a pyte color value to a Rich color string."""
    if color == "default":
        return None
    # Named colors
    _MAP = {
        "black": "black",
        "red": "red",
        "green": "green",
        "brown": "yellow",
        "blue": "blue",
        "magenta": "magenta",
        "cyan": "cyan",
        "white": "white",
    }
    if color in _MAP:
        return _MAP[color]
    # 256-color or hex
    if len(color) == 6:
        try:
            int(color, 16)
            return f"#{color}"
        except ValueError:
            pass
    return None


def _key_to_bytes(event: Key, decckm: bool = False) -> bytes | None:
    """Convert a Textual Key event to terminal byte sequence.

    When *decckm* is True (application cursor key mode), arrow keys use
    ``\\x1bO`` prefix instead of ``\\x1b[``.  Programs like Python's readline
    enable this mode so they can distinguish arrow keys from other sequences.
    """
    key = event.key

    # App bindings — do not capture
    if key in _APP_BINDINGS:
        return None

    # Regular printable character
    if event.character and len(event.character) == 1 and ord(event.character) >= 32:
        return event.character.encode("utf-8")

    # Arrow key prefix depends on DECCKM mode
    _arrow_prefix = b"\x1bO" if decckm else b"\x1b["

    match key:
        case "enter":
            return b"\r"
        case "tab":
            return b"\t"
        case "backspace":
            return b"\x7f"
        case "delete":
            return b"\x1b[3~"
        case "escape":
            return b"\x1b"
        case "up":
            return _arrow_prefix + b"A"
        case "down":
            return _arrow_prefix + b"B"
        case "right":
            return _arrow_prefix + b"C"
        case "left":
            return _arrow_prefix + b"D"
        case "home":
            return _arrow_prefix + b"H" if decckm else b"\x1b[H"
        case "end":
            return _arrow_prefix + b"F" if decckm else b"\x1b[F"
        case "pageup":
            return b"\x1b[5~"
        case "pagedown":
            return b"\x1b[6~"
        case "insert":
            return b"\x1b[2~"
        case "space":
            return b" "

    # Ctrl+letter combinations
    if key.startswith("ctrl+"):
        letter = key[5:]
        if len(letter) == 1 and letter.isalpha():
            return bytes([ord(letter.lower()) - ord("a") + 1])

    return None


def _render_row(screen, row: int) -> Text:
    """Render a single pyte screen row to a Rich Text, batching same-styled runs."""
    line = Text()
    buf = screen.buffer[row]
    cols = screen.columns

    # Walk columns, coalescing runs of identical style
    run_start = 0
    run_style = _char_style(buf[0])
    run_chars: list[str] = [buf[0].data]

    for col in range(1, cols):
        char = buf[col]
        style = _char_style(char)
        if style == run_style:
            run_chars.append(char.data)
        else:
            line.append("".join(run_chars), style=run_style)
            run_style = style
            run_chars = [char.data]

    # Flush last run
    line.append("".join(run_chars), style=run_style)
    return line


class TerminalPane(Widget):
    """A terminal emulator widget backed by pyte.

    Displays program output in a terminal-accurate way and forwards
    keystrokes to the PTY for authentic REPL interaction.
    """

    DEFAULT_CSS = """
    TerminalPane {
        height: 1fr;
        layout: vertical;
    }

    TerminalPane RichLog {
        height: auto;
        max-height: 50%;
        scrollbar-size: 1 1;
        display: none;
    }

    TerminalPane RichLog.has-content {
        display: block;
    }

    TerminalPane .terminal-screen {
        height: 1fr;
    }
    """

    can_focus = True

    def __init__(
        self,
        program_id: str,
        scrollback: int = 10000,
        rows: int = DEFAULT_ROWS,
        cols: int = DEFAULT_COLS,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.program_id = program_id
        self._scrollback = scrollback
        self._pty_fd: int = -1
        self._is_alive: bool = True

        # pyte terminal emulator
        self._screen = pyte.HistoryScreen(cols, rows, history=1000)
        self._screen.set_mode(pyte.modes.LNM)
        self._stream = pyte.Stream(self._screen)

        # Row render cache: row index -> rendered Rich Text
        self._row_cache: dict[int, Text] = {}

        # Track previous cursor position so we can re-render the old row
        self._prev_cursor: tuple[int, int] = (0, 0)

        # Debounce: coalesce rapid feed() calls into one render per frame
        self._refresh_needed: bool = False
        self._refresh_timer: Timer | None = None

    def on_mount(self) -> None:
        """Start the refresh timer once the widget is mounted."""
        self._refresh_timer = self.set_interval(_REFRESH_INTERVAL, self._tick_refresh)

    def compose(self) -> ComposeResult:
        yield RichLog(
            id=f"scrollback-{self.program_id}",
            wrap=False,
            max_lines=self._scrollback,
        )
        yield Static("", id=f"screen-{self.program_id}", classes="terminal-screen")

    def feed(self, data: str) -> None:
        """Feed raw PTY output into the terminal emulator. Rendering is debounced."""
        self._stream.feed(data)
        self._drain_scrollback()
        self._refresh_needed = True

    def write_to_pty(self, data: bytes) -> None:
        """Write raw bytes to the PTY fd."""
        if not self._is_alive or self._pty_fd < 0:
            return
        try:
            os.write(self._pty_fd, data)
        except OSError:
            pass  # PTY may have been closed

    def on_key(self, event: Key) -> None:
        """Convert key events to terminal bytes and send to PTY."""
        decckm = 32 in self._screen.mode  # DECCKM: private mode 1, stored as 1 << 5
        data = _key_to_bytes(event, decckm=decckm)
        if data is not None:
            self.write_to_pty(data)
            event.stop()
            event.prevent_default()

    def on_resize(self, event: Resize) -> None:
        """Resize the pyte screen and inform the PTY of new dimensions."""
        new_cols = max(event.size.width, 10)
        new_rows = max(event.size.height - 2, 4)

        self._screen.resize(new_rows, new_cols)
        self._row_cache.clear()  # dimensions changed, full re-render needed

        # Tell the child process about the new terminal size
        if self._pty_fd >= 0:
            try:
                winsize = struct.pack("HHHH", new_rows, new_cols, 0, 0)
                fcntl.ioctl(self._pty_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass

        self._do_refresh_screen(force_full=True)

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _tick_refresh(self) -> None:
        """Timer callback: render if there's pending output."""
        if self._refresh_needed:
            self._refresh_needed = False
            self._do_refresh_screen()

    def _drain_scrollback(self) -> None:
        """Move lines from pyte's history buffer into the RichLog scrollback."""
        history_top: deque = self._screen.history.top
        if not history_top:
            return

        try:
            scrollback_log = self.query_one(f"#scrollback-{self.program_id}", RichLog)
        except Exception:
            return

        while history_top:
            line_chars = history_top.popleft()
            rich_line = self._chars_to_rich_text(line_chars)
            scrollback_log.write(rich_line)

        # Show the scrollback widget now that it has content
        scrollback_log.add_class("has-content")

        # Scrollback means rows shifted — invalidate cache
        self._row_cache.clear()

    def _do_refresh_screen(self, force_full: bool = False) -> None:
        """Render changed pyte screen rows to the Static widget."""
        try:
            screen_widget = self.query_one(f"#screen-{self.program_id}", Static)
        except Exception:
            return

        screen = self._screen
        dirty = screen.dirty
        num_rows = screen.lines

        # Always re-render rows affected by cursor movement so the cursor
        # block appears in the correct position and disappears from the old one.
        prev_y, _ = self._prev_cursor
        cur_y = screen.cursor.y
        if 0 <= prev_y < num_rows:
            dirty.add(prev_y)
        if 0 <= cur_y < num_rows:
            dirty.add(cur_y)
        self._prev_cursor = (cur_y, screen.cursor.x)

        if force_full or not self._row_cache:
            # Full render — build all rows
            for row in range(num_rows):
                self._row_cache[row] = _render_row(screen, row)
        else:
            # Incremental — only re-render dirty rows
            for row in dirty:
                if 0 <= row < num_rows:
                    self._row_cache[row] = _render_row(screen, row)

        dirty.clear()

        # Assemble the full screen from cache
        lines = [self._row_cache.get(r, Text("")).copy() for r in range(num_rows)]

        # Draw cursor: apply reverse style at cursor position
        cursor = screen.cursor
        if self._is_alive and 0 <= cursor.y < num_rows:
            cursor_line = lines[cursor.y]
            col = cursor.x
            line_len = len(cursor_line.plain)
            if col < line_len:
                cursor_line.stylize("reverse", col, col + 1)
            else:
                # Cursor is past end of content — append a visible block
                cursor_line.append(" ", style="reverse")

        combined = Text("\n").join(lines)
        screen_widget.update(combined)

    @staticmethod
    def _chars_to_rich_text(line_chars: dict) -> Text:
        """Convert a pyte history line (dict of col -> Char) to Rich Text."""
        if not line_chars:
            return Text("")

        max_col = max(line_chars.keys()) if line_chars else 0
        result = Text()
        run_chars: list[str] = []
        run_style: Style = _DEFAULT_STYLE

        for col in range(max_col + 1):
            char = line_chars.get(col)
            if char is None:
                style = _DEFAULT_STYLE
                ch = " "
            else:
                style = _char_style(char)
                ch = char.data

            if style == run_style:
                run_chars.append(ch)
            else:
                if run_chars:
                    result.append("".join(run_chars), style=run_style)
                run_style = style
                run_chars = [ch]

        if run_chars:
            result.append("".join(run_chars), style=run_style)
        return result
