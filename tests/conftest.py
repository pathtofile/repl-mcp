"""Shared test fixtures."""

import pytest

from repl_mcp.manager import ProgramManager


@pytest.fixture
async def manager():
    """Create a ProgramManager and clean up all programs after the test."""
    m = ProgramManager()
    yield m
    for prog_id in list(m._programs.keys()):
        try:
            await m.kill_program(prog_id)
        except Exception:
            pass
