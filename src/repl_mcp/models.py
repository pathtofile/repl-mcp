"""Data models for iterm2-mcp."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

import namer


def _generate_unique_name() -> str:
    """Generate a unique human-readable name like 'bewildered-spectacles'."""
    return namer.generate(style="lowercase")


@dataclass
class Tab:
    """Represents a tracked iTerm2 session (tab or pane)."""

    id: str = field(default_factory=_generate_unique_name)
    session_id: str = ""  # iTerm2 session ID (e.g. "w0t0p0")
    tab_id: str = ""  # iTerm2 tab ID
    window_id: str = ""  # iTerm2 window ID
    name: str = ""  # Tab title
    output_buffer: list[str] = field(default_factory=list)  # Accumulated output lines
    is_alive: bool = True
    owner_agent: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    read_cursors: dict[str, int] = field(
        default_factory=dict
    )  # agent_id -> position in output_buffer
    last_screen_lines: list[str] = field(default_factory=list)  # Previous screen snapshot
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        """Return dict for list_tabs response."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "name": self.name,
            "is_alive": self.is_alive,
            "owner_agent": self.owner_agent,
            "started_at": self.started_at.isoformat(),
        }
