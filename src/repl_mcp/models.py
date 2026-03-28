"""Data models for repl-mcp."""

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone

import namer


def _generate_unique_name() -> str:
    """Generate a unique human-readable name like 'bewildered-spectacles'."""
    return namer.generate(style="lowercase")


@dataclass
class Program:
    """Represents a managed interactive program running in a PTY."""

    id: str = field(default_factory=_generate_unique_name)
    command: str = ""
    args: list[str] = field(default_factory=list)
    pid: int = 0
    pty_fd: int = -1
    output_buffer: list[str] = field(default_factory=list)
    is_running: bool = True
    owner_agent: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    read_cursors: dict[str, int] = field(
        default_factory=dict
    )  # agent_id -> position in output_buffer
    last_io_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    process: subprocess.Popen | None = None
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)

    def to_list_dict(self) -> dict:
        """Return dict for list_programs response."""
        return {
            "id": self.id,
            "command": self.command,
            "pid": self.pid,
            "is_running": self.is_running,
            "owner_agent": self.owner_agent,
            "started_at": self.started_at.isoformat(),
        }
