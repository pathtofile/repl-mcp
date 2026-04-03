"""REPL process manager."""

import asyncio
import errno
import fcntl
import logging
import os
import pty
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Callable

from .models import Program

logger = logging.getLogger(__name__)

# Environment variables that must not be overridden by callers
_BLOCKED_ENV_VARS = frozenset(
    {
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
    }
)

# Constants for tuning
PTY_READ_SIZE = 4096
EAGAIN_RETRY_DELAY = 0.05
KILL_POLL_INTERVAL = 0.1
KILL_POLL_ITERATIONS = 20
INITIAL_INPUT_DELAY = 1.0

# ioctl constant to set the controlling terminal.
# Required so programs like ssh can open /dev/tty for password prompts.
if sys.platform == "darwin":
    TIOCSCTTY = 0x20007461
else:
    TIOCSCTTY = 0x540E


def _set_controlling_tty() -> None:
    """preexec_fn: make stdin (the slave PTY) the controlling terminal.

    By the time subprocess calls this, setsid() and dup2(slave_fd, 0)
    have already happened, so fd 0 is the slave PTY in a new session.
    """
    fcntl.ioctl(0, TIOCSCTTY, 0)


class ProgramManager:
    """Manages interactive programs running in PTYs."""

    def __init__(self) -> None:
        self._programs: dict[str, Program] = {}
        self._output_events: dict[str, asyncio.Event] = {}
        self._read_tasks: dict[str, asyncio.Task] = {}
        self._allowlist: set[str] | None = None  # None means allow all
        self.scrollback_limit: int = 10000
        self.on_output: Callable[[str, str, str], None] | None = None
        self.on_program_started: Callable[[Program], None] | None = None
        self.on_program_exited: Callable[[Program], None] | None = None

    # ------------------------------------------------------------------ #
    #  Allowlist
    # ------------------------------------------------------------------ #

    def set_allowlist(self, programs: list[str]) -> None:
        """Set the allowlist of permitted programs (resolved to canonical paths)."""
        resolved: set[str] = set()
        for name in programs:
            path = shutil.which(name)
            if path is not None:
                resolved.add(os.path.realpath(path))
            else:
                # Allow literal paths that exist
                real = os.path.realpath(name)
                if os.path.isfile(real):
                    resolved.add(real)
                else:
                    logger.warning("Allowlist entry '%s' could not be resolved; skipping", name)
        self._allowlist = resolved

    def _check_allowlist(self, resolved_path: str) -> None:
        """Raise ValueError if the resolved path is not in the allowlist."""
        if self._allowlist is None:
            return
        if resolved_path not in self._allowlist:
            logger.warning(
                "Blocked program '%s' (allowlist: %s)",
                resolved_path,
                sorted(self._allowlist),
            )
            raise ValueError(f"Program '{resolved_path}' is not in the allowlist.")

    # ------------------------------------------------------------------ #
    #  Core operations
    # ------------------------------------------------------------------ #

    async def start_program(
        self,
        command: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        owner_agent: str = "",
        initial_input: str | None = None,
    ) -> dict:
        """Start a new interactive program in a PTY.

        Returns dict with id, pid, and command.
        """
        args = args or []

        # Resolve command to a real path
        which_path = shutil.which(command)
        if which_path is None:
            raise FileNotFoundError(f"Command not found: {command}")
        resolved_command = os.path.realpath(which_path)

        # Check allowlist
        self._check_allowlist(resolved_command)

        # Build environment, blocking dangerous overrides from callers
        proc_env = os.environ.copy()
        if env:
            for key, value in env.items():
                if key in _BLOCKED_ENV_VARS:
                    logger.warning("Blocked dangerous env var override: %s", key)
                    continue
                proc_env[key] = value

        # Create PTY pair
        master_fd, slave_fd = pty.openpty()

        try:
            process = subprocess.Popen(
                [resolved_command] + args,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                preexec_fn=_set_controlling_tty,
                cwd=cwd,
                env=proc_env,
            )
        except Exception:
            os.close(master_fd)
            os.close(slave_fd)
            raise

        # Close slave fd in parent — the child owns it now
        os.close(slave_fd)

        # Set master fd to non-blocking
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        prog = Program(
            command=resolved_command,
            args=args,
            pid=process.pid,
            pty_fd=master_fd,
            is_running=True,
            owner_agent=owner_agent,
            process=process,
            cwd=cwd or os.getcwd(),
            env=env or {},
        )

        # Ensure the generated name is unique among existing programs
        while prog.id in self._programs:
            from .models import _generate_unique_name

            prog.id = _generate_unique_name()

        self._programs[prog.id] = prog
        self._output_events[prog.id] = asyncio.Event()

        # Start async read loop
        task = asyncio.create_task(self._read_loop(prog))
        self._read_tasks[prog.id] = task

        logger.info("Started program %s (pid=%d, cmd=%s)", prog.id, prog.pid, resolved_command)

        # Notify TUI that a new program started
        if self.on_program_started is not None:
            try:
                self.on_program_started(prog)
            except Exception:
                logger.exception("on_program_started callback error")

        # Send initial input after a brief delay so the program can show its prompt
        if initial_input:
            asyncio.create_task(self._send_initial_input(prog.id, initial_input))

        return {"id": prog.id, "pid": prog.pid, "command": resolved_command}

    async def _send_initial_input(self, program_id: str, text: str) -> None:
        """Send initial input to a program after a brief startup delay."""
        await asyncio.sleep(INITIAL_INPUT_DELAY)
        try:
            await self.send_input(program_id, text, source="initial")
        except Exception:
            logger.exception("Failed to send initial input to %s", program_id)

    async def send_input(
        self,
        program_id: str,
        text: str,
        source: str = "ai",
        agent_id: str = "",
    ) -> dict:
        """Send input text to a running program's PTY.

        Returns dict with success status.
        """
        prog = self._get_program(program_id)

        if not prog.is_running:
            raise RuntimeError(f"Program {program_id} is not running")

        # Append newline if not present
        if not text.endswith("\n"):
            text += "\n"

        # Write to PTY — the terminal echo will make it visible naturally
        try:
            os.write(prog.pty_fd, text.encode("utf-8"))
        except OSError as exc:
            raise RuntimeError(f"Failed to write to PTY: {exc}") from exc

        prog.last_io_time = datetime.now(timezone.utc)

        return {"success": True}

    async def send_signal(self, program_id: str, signal_name: str) -> dict:
        """Send a signal to a running program.

        signal_name should be like 'SIGINT', 'SIGTERM', etc.
        Returns dict with success status.
        """
        prog = self._get_program(program_id)

        # Parse signal name
        sig_name = signal_name.upper()
        if not sig_name.startswith("SIG"):
            sig_name = "SIG" + sig_name
        try:
            sig = signal.Signals[sig_name]
        except KeyError:
            raise ValueError(f"Unknown signal: {signal_name}") from None

        try:
            os.kill(prog.pid, sig)
        except ProcessLookupError:
            prog.is_running = False
            raise RuntimeError(f"Process {prog.pid} no longer exists") from None

        logger.info("Sent %s to program %s (pid=%d)", sig_name, program_id, prog.pid)
        return {"success": True}

    async def read_output(
        self,
        program_id: str,
        agent_id: str = "",
        timeout: float = 0,
    ) -> dict:
        """Read new output from a program since this agent's last read.

        Returns dict with output text and is_running status.
        """
        prog = self._get_program(program_id)

        cursor = prog.read_cursors.get(agent_id, 0)

        # If no new output and timeout > 0, wait
        if cursor >= len(prog.output_buffer) and timeout > 0 and prog.is_running:
            event = self._output_events.get(program_id)
            if event is not None:
                try:
                    await asyncio.wait_for(event.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    pass

        # Collect new output
        buf = prog.output_buffer
        new_data = buf[cursor:]
        prog.read_cursors[agent_id] = len(buf)

        output_text = "".join(new_data)

        return {"output": output_text, "is_running": prog.is_running}

    async def kill_program(self, program_id: str) -> dict:
        """Kill a program: SIGTERM, wait 2s, then SIGKILL if needed.

        Returns dict with success status.
        """
        prog = self._programs.get(program_id)
        if prog is None:
            # Already exited and cleaned up
            return {"success": True}

        if prog.is_running and prog.process is not None:
            # Send SIGTERM
            try:
                os.kill(prog.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

            # Wait up to 2 seconds for graceful exit
            for _ in range(KILL_POLL_ITERATIONS):
                if prog.process.poll() is not None:
                    break
                await asyncio.sleep(KILL_POLL_INTERVAL)

            # Force kill if still running
            if prog.process.poll() is None:
                try:
                    os.kill(prog.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                # Brief wait for SIGKILL to take effect
                await asyncio.sleep(KILL_POLL_INTERVAL)

        prog.is_running = False

        self._close_pty(prog)

        # Cancel read task
        task = self._read_tasks.pop(program_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        # Remove program from tracking so MCP clients no longer see it
        self._programs.pop(program_id, None)
        self._output_events.pop(program_id, None)

        logger.info("Killed program %s (pid=%d)", program_id, prog.pid)
        return {"success": True}

    async def adopt_program(self, program_id: str, agent_id: str) -> dict:
        """Adopt an unowned program (or transfer ownership).

        Returns dict with success status and the program id.
        """
        prog = self._get_program(program_id)
        if prog.owner_agent and prog.owner_agent != agent_id:
            raise RuntimeError(f"Program {program_id} is already owned by '{prog.owner_agent}'")
        prog.owner_agent = agent_id
        logger.info("Agent %s adopted program %s", agent_id, program_id)

        # Notify TUI so it can update the tab label
        if self.on_program_started is not None:
            try:
                self.on_program_started(prog)
            except Exception:
                logger.exception("on_program_started callback error (adopt)")

        return {"success": True, "id": prog.id, "owner_agent": agent_id}

    def list_programs(self) -> list[dict]:
        """Return a list of all managed programs as dicts."""
        return [prog.to_list_dict() for prog in self._programs.values()]

    @property
    def programs(self) -> dict[str, Program]:
        """Public read-only access to managed programs."""
        return self._programs

    async def shutdown(self) -> None:
        """Kill all running programs and clean up."""
        for prog_id in list(self._programs.keys()):
            try:
                await self.kill_program(prog_id)
            except Exception:
                logger.exception("Error shutting down program %s", prog_id)

    def kill_all_sync(self) -> None:
        """Synchronously kill all running programs (for use during interpreter shutdown).

        Sends SIGTERM then SIGKILL to each running process and closes PTY fds.
        Does not await anything — safe to call when no event loop is available.
        """
        signaled = False
        for prog in self._programs.values():
            if not prog.is_running or prog.process is None:
                continue
            if prog.process.poll() is not None:
                continue
            try:
                os.kill(prog.pid, signal.SIGTERM)
                signaled = True
            except ProcessLookupError:
                continue

        if signaled:
            time.sleep(0.5)

        for prog in self._programs.values():
            if prog.process is not None and prog.process.poll() is None:
                try:
                    os.kill(prog.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self._close_pty(prog)
            prog.is_running = False

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _get_program(self, program_id: str) -> Program:
        """Look up a program by ID or raise KeyError."""
        try:
            return self._programs[program_id]
        except KeyError:
            raise KeyError(f"No program with id '{program_id}'") from None

    @staticmethod
    def _close_pty(prog: Program) -> None:
        """Safely close and invalidate a program's PTY file descriptor."""
        if prog.pty_fd >= 0:
            try:
                os.close(prog.pty_fd)
            except OSError:
                pass
            prog.pty_fd = -1

    def _enforce_scrollback(self, prog: Program) -> None:
        """Trim output buffer to scrollback_limit and adjust cursors."""
        excess = len(prog.output_buffer) - self.scrollback_limit
        if excess <= 0:
            return
        del prog.output_buffer[:excess]
        for agent_id in prog.read_cursors:
            prog.read_cursors[agent_id] = max(0, prog.read_cursors[agent_id] - excess)

    def _wake_event(self, program_id: str) -> None:
        """Notify any asyncio waiters that new output is available."""
        event = self._output_events.get(program_id)
        if event is not None:
            event.set()
            event.clear()

    def _blocking_read(self, fd: int) -> bytes:
        """Blocking read from a (possibly non-blocking) PTY fd.

        Retries on EAGAIN/EWOULDBLOCK with a short sleep so the executor
        thread does not spin-wait, and only raises on real errors (EIO, EBADF)
        which indicate the PTY has been closed.
        """
        while True:
            try:
                return os.read(fd, PTY_READ_SIZE)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    time.sleep(EAGAIN_RETRY_DELAY)
                    continue
                raise

    async def _read_loop(self, prog: Program) -> None:
        """Async task that reads from the PTY fd and appends to the output buffer."""
        loop = asyncio.get_running_loop()
        fd = prog.pty_fd

        try:
            while True:
                try:
                    data = await loop.run_in_executor(None, self._blocking_read, fd)
                except OSError:
                    # PTY closed (EIO / EBADF)
                    break

                if not data:
                    # EOF
                    break

                text = data.decode("utf-8", errors="replace")
                prog.output_buffer.append(text)
                self._enforce_scrollback(prog)
                prog.last_io_time = datetime.now(timezone.utc)

                # Notify waiters
                self._wake_event(prog.id)

                # Notify TUI callback
                if self.on_output is not None:
                    try:
                        self.on_output(prog.id, text, "program")
                    except Exception:
                        logger.exception("on_output callback error")

        except asyncio.CancelledError:
            return
        finally:
            # Mark program as not running
            if prog.process is not None:
                prog.process.poll()
            prog.is_running = False

            self._close_pty(prog)

            # Wake any waiters so they see is_running=False
            event = self._output_events.get(prog.id)
            if event is not None:
                event.set()

            # Notify TUI that program exited
            if self.on_program_exited is not None:
                try:
                    self.on_program_exited(prog)
                except Exception:
                    logger.exception("on_program_exited callback error")

            # Remove program from tracking so MCP clients no longer see it
            self._programs.pop(prog.id, None)
            self._output_events.pop(prog.id, None)
            self._read_tasks.pop(prog.id, None)

            logger.info("Read loop ended for program %s (pid=%d)", prog.id, prog.pid)
