"""Textual TUI application for repl-mcp."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone

import shlex

from textual.app import App, ComposeResult
from textual.events import Paste
from textual.screen import ModalScreen
from textual.widgets import (
    Header,
    Footer,
    Static,
    Input,
    TabbedContent,
    TabPane,
)
from textual.reactive import reactive
from textual.css.query import NoMatches
from rich.text import Text

from .manager import ProgramManager
from .models import Program
from .terminal_widget import TerminalPane

# Tab status icons
ICON_RUNNING = "\u25cf"  # filled circle
ICON_IDLE = "\u26a0"  # warning sign
ICON_STOPPED = "\u23f9"  # stop button

# Timing constants
IDLE_CHECK_INTERVAL = 15  # seconds between idle checks
IDLE_THRESHOLD_SECONDS = 60  # seconds before a program is considered idle


def _program_display_name(command: str) -> str:
    """Extract a short display name from a command path."""
    return command.split("/")[-1].split()[0]


class ProgramTab(TabPane):
    """A tab pane for a single managed program."""

    def __init__(self, program_id: str, title: str, scrollback: int = 10000, **kwargs):
        super().__init__(title, id=f"tab-{program_id}", **kwargs)
        self.program_id = program_id
        self._scrollback = scrollback

    def compose(self) -> ComposeResult:
        yield TerminalPane(
            program_id=self.program_id,
            scrollback=self._scrollback,
            id=f"terminal-{self.program_id}",
        )


class StatusBar(Static):
    """Bottom status bar showing server info."""

    port: reactive[int] = reactive(8780)
    agent_count: reactive[int] = reactive(0)
    program_count: reactive[int] = reactive(0)
    token_display: reactive[str] = reactive("")

    def render(self) -> str:
        token_info = (
            f" | Token: {self.token_display}" if self.token_display else " | No auth"
        )
        return (
            f"  Port: {self.port}{token_info}"
            f" | Agents: {self.agent_count}"
            f" | Programs: {self.program_count}"
        )


class NewProgramScreen(ModalScreen[dict | None]):
    """Modal dialog for entering a command to start a new program."""

    CSS = """
    NewProgramScreen {
        align: center middle;
    }

    #new-program-dialog {
        width: 70;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $accent;
    }

    .field-label {
        margin-top: 1;
        margin-bottom: 0;
        color: $text-muted;
    }

    .field-label-first {
        margin-bottom: 0;
    }

    .field-input {
        width: 100%;
    }
    """

    def compose(self) -> ComposeResult:
        with Static(id="new-program-dialog"):
            yield Static(
                "Command (e.g. python3, gdb ./a.out):", classes="field-label-first"
            )
            yield Input(
                placeholder="command [args...]",
                id="new-program-command",
                classes="field-input",
            )
            yield Static("Working directory (optional):", classes="field-label")
            yield Input(
                placeholder="/path/to/dir", id="new-program-cwd", classes="field-input"
            )
            yield Static(
                "Environment variables (optional, KEY=VAL KEY2=VAL2):",
                classes="field-label",
            )
            yield Input(
                placeholder="FOO=bar BAZ=qux",
                id="new-program-env",
                classes="field-input",
            )
            yield Static(
                "Initial input (optional, sent on startup e.g. password):",
                classes="field-label",
            )
            yield Input(
                placeholder="text to type into program",
                id="new-program-initial-input",
                classes="field-input",
            )

    def on_mount(self) -> None:
        self.query_one("#new-program-command", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        command = self.query_one("#new-program-command", Input).value.strip()
        if not command:
            self.dismiss(None)
            return
        cwd = self.query_one("#new-program-cwd", Input).value.strip() or None
        env_str = self.query_one("#new-program-env", Input).value.strip()
        env = None
        if env_str:
            env = {}
            for part in shlex.split(env_str):
                if "=" in part:
                    key, _, val = part.partition("=")
                    env[key] = val
        initial_input = (
            self.query_one("#new-program-initial-input", Input).value.strip() or None
        )
        self.dismiss({"command": command, "cwd": cwd, "env": env, "initial_input": initial_input})

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class ReplMCPApp(App):
    """TUI for repl-mcp -- manage interactive REPL programs."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #main-content {
        height: 1fr;
    }

    #no-programs {
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text-muted;
    }

    TabbedContent {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+n", "new_program", "New Program"),
        ("ctrl+c", "copy_program_id", "Copy Program ID"),
    ]

    def __init__(
        self,
        manager: ProgramManager | None = None,
        server=None,
        port: int = 8780,
        token: str | None = None,
        scrollback: int = 10000,
        startup_procs: list[dict] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.manager = manager
        self.server_instance = server
        self._port = port
        self._token = token
        self._scrollback = scrollback
        self._startup_procs = startup_procs or []
        self._active_program_id: str | None = None
        self._idle_check_task: asyncio.Task | None = None
        self._server_task: asyncio.Task | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield TabbedContent(id="main-content")
        yield Static(
            "No programs running. Press Ctrl+N to start one, or wait for an agent.",
            id="no-programs",
        )
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        status = self.query_one("#status-bar", StatusBar)
        status.port = self._port
        if self._token:
            # Show only first 4 chars to limit exposure
            status.token_display = self._token[:4] + "..."

        if self.manager:
            self.manager.on_output = self._on_program_output
            self.manager.on_program_started = self._on_program_started
            self.manager.on_program_exited = self._on_program_exited

        self._idle_check_task = asyncio.create_task(self._check_idle_programs())

        # Start the MCP HTTP server as a background task in the same event loop
        if self.server_instance:
            self._server_task = asyncio.create_task(self.server_instance.start())

        # Launch any startup programs requested via CLI
        if self._startup_procs and self.manager:
            asyncio.create_task(self._launch_startup_procs())

    async def _launch_startup_procs(self) -> None:
        """Launch programs specified via --startup-procs or -- command line args."""

        async def _start_one(proc: dict) -> None:
            try:
                await self.manager.start_program(
                    command=proc["command"],
                    args=proc.get("args"),
                    cwd=proc.get("cwd"),
                    env=proc.get("env"),
                    initial_input=proc.get("initial_input"),
                )
            except Exception as exc:
                self.notify(
                    f"Failed to start {proc['command']}: {exc}", severity="error"
                )

        await asyncio.gather(*[_start_one(p) for p in self._startup_procs])

    async def _check_idle_programs(self) -> None:
        """Periodically check for idle programs and update tab indicators."""
        while True:
            await asyncio.sleep(IDLE_CHECK_INTERVAL)
            if not self.manager:
                continue
            now = datetime.now(timezone.utc)
            try:
                tabs = self.query_one("#main-content", TabbedContent)
            except NoMatches:
                continue
            for prog_id, prog in self.manager.programs.items():
                idle_seconds = (now - prog.last_io_time).total_seconds()
                tab_id = f"tab-{prog_id}"
                try:
                    tab = tabs.get_tab(tab_id)
                except NoMatches:
                    continue

                name = _program_display_name(prog.command)
                agent_info = f" ({prog.owner_agent})" if prog.owner_agent else ""

                if not prog.is_running:
                    tab.label = Text(f"{ICON_STOPPED} {name} [{prog_id}]{agent_info}")
                elif idle_seconds > IDLE_THRESHOLD_SECONDS:
                    tab.label = Text(f"{ICON_IDLE} {name} [{prog_id}]{agent_info}")
                else:
                    tab.label = Text(f"{ICON_RUNNING} {name} [{prog_id}]{agent_info}")

    def _on_program_started(self, program: Program) -> None:
        """Called when a new program is started or adopted (from async tasks on the main thread)."""
        self.call_later(self._add_program_tab, program)

    def _add_program_tab(self, program: Program) -> None:
        """Add a new tab for a program, or update an existing one (runs on the main/UI thread)."""
        name = _program_display_name(program.command)
        agent_info = f" ({program.owner_agent})" if program.owner_agent else ""
        title = f"{ICON_RUNNING} {name} [{program.id}]{agent_info}"

        tabs = self.query_one("#main-content", TabbedContent)
        tab_id = f"tab-{program.id}"

        # If the tab already exists (e.g. adoption), just update its label
        try:
            existing_tab = tabs.get_tab(tab_id)
            existing_tab.label = Text(title)
            self._update_status()
            return
        except NoMatches:
            pass

        new_tab = ProgramTab(program.id, title, scrollback=self._scrollback)
        tabs.add_pane(new_tab)

        # Set the PTY fd on the terminal pane after it's mounted
        def _set_pty_fd():
            try:
                terminal = self.query_one(f"#terminal-{program.id}", TerminalPane)
                terminal._pty_fd = program.pty_fd
                terminal.focus()
            except NoMatches:
                pass

        self.call_later(_set_pty_fd)

        # Hide the "no programs" placeholder
        try:
            no_progs = self.query_one("#no-programs")
            no_progs.display = False
        except NoMatches:
            pass

        self._active_program_id = program.id

        self._update_status()

    def _on_program_exited(self, program: Program) -> None:
        """Called when a program exits (from async tasks on the main thread)."""
        self.call_later(self._update_exited_tab, program)

    def _update_exited_tab(self, program: Program) -> None:
        """Remove the tab for an exited program (runs on main thread)."""
        try:
            tabs = self.query_one("#main-content", TabbedContent)
            tabs.remove_pane(f"tab-{program.id}")
        except NoMatches:
            pass

        self._update_status()

    def _on_program_output(
        self, program_id: str, text: str, source: str = "program"
    ) -> None:
        """Called when new output is available (from async tasks on the main thread)."""
        self.call_later(self._append_output, program_id, text, source)

    def _append_output(
        self, program_id: str, text: str, source: str = "program"
    ) -> None:
        """Feed output to the program's terminal emulator (runs on main thread)."""
        try:
            terminal = self.query_one(f"#terminal-{program_id}", TerminalPane)
        except NoMatches:
            return

        terminal.feed(text)

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        """Track which program tab is currently active and focus its terminal."""
        tab_id = event.pane.id or ""
        if tab_id.startswith("tab-"):
            self._active_program_id = tab_id[4:]
            try:
                terminal = self.query_one(
                    f"#terminal-{self._active_program_id}", TerminalPane
                )
                terminal.focus()
            except NoMatches:
                pass

    async def on_paste(self, event: Paste) -> None:
        """Handle paste events — send pasted text directly to the active program.

        Multi-line paste is sent line-by-line so the REPL processes each line.
        Single-line paste is also forwarded to keep the behavior consistent
        with a real terminal (paste goes straight to the program).
        """
        if not self._active_program_id or not self.manager:
            return

        # Only intercept paste when the main screen is active (not a modal)
        if self.screen is not self.screen_stack[0]:
            return

        prog = self.manager.programs.get(self._active_program_id)
        if not prog or not prog.is_running:
            return

        event.prevent_default()
        event.stop()

        text = event.text.rstrip("\n")
        if not text:
            return

        # Send each line to the program, preserving multi-line paste behavior
        lines = text.split("\n")
        for line in lines:
            try:
                await self.manager.send_input(
                    self._active_program_id,
                    line,
                    source="human",
                    agent_id="human",
                )
            except Exception as e:
                self.notify(f"Error sending pasted input: {e}", severity="error")
                break

    def _update_status(self) -> None:
        """Update the status bar counts."""
        try:
            status = self.query_one("#status-bar", StatusBar)
            status.program_count = len(self.manager.programs) if self.manager else 0
            if self.server_instance and hasattr(self.server_instance, "agent_count"):
                status.agent_count = self.server_instance.agent_count
        except NoMatches:
            pass

    def action_new_program(self) -> None:
        """Open dialog to start a new program from the TUI."""

        async def _on_result(result_dict: dict | None) -> None:
            if not result_dict or not self.manager:
                return
            try:
                parts = shlex.split(result_dict["command"])
            except ValueError as e:
                self.notify(f"Invalid command: {e}", severity="error")
                return
            command = parts[0]
            args = parts[1:]
            try:
                result = await self.manager.start_program(
                    command=command,
                    args=args,
                    cwd=result_dict.get("cwd"),
                    env=result_dict.get("env"),
                    owner_agent="",
                    initial_input=result_dict.get("initial_input"),
                )
                self.notify(f"Started {command} as [{result['id']}]")
            except Exception as e:
                self.notify(f"Error: {e}", severity="error")

        self.push_screen(NewProgramScreen(), callback=_on_result)

    def action_focus_input(self) -> None:
        """Focus the input bar."""
        self.query_one("#input-bar", Input).focus()

    def action_copy_program_id(self) -> None:
        """Copy the active program's unique ID to the system clipboard."""
        if self._active_program_id:
            self.copy_to_clipboard(self._active_program_id)
            self.notify(f"Copied: {self._active_program_id}")
        else:
            self.notify("No active program", severity="warning")

    async def action_quit(self) -> None:
        """Quit the application, killing all managed programs and cancelling background tasks."""
        if self.manager:
            await self.manager.shutdown()
        for task in (self._idle_check_task, self._server_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self.exit()
