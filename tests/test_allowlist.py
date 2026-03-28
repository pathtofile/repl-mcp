"""Tests for the program allowlist."""

import pytest


class TestAllowlist:
    async def test_no_allowlist_allows_all(self, manager):
        # No allowlist set - should work
        result = await manager.start_program("echo", args=["allowed"])
        assert "id" in result

    async def test_allowlist_permits(self, manager):
        manager.set_allowlist(["echo"])
        result = await manager.start_program("echo", args=["allowed"])
        assert "id" in result

    async def test_allowlist_blocks(self, manager):
        manager.set_allowlist(["echo"])
        with pytest.raises(ValueError, match="not in the allowlist"):
            await manager.start_program("cat")

    async def test_allowlist_multiple(self, manager):
        manager.set_allowlist(["echo", "cat"])
        r1 = await manager.start_program("echo", args=["test"])
        r2 = await manager.start_program("cat")
        assert "id" in r1
        assert "id" in r2

    async def test_allowlist_empty_blocks_all(self, manager):
        manager.set_allowlist([])
        with pytest.raises(ValueError, match="not in the allowlist"):
            await manager.start_program("echo", args=["blocked"])

    async def test_allowlist_nonexistent_entry_skipped(self, manager):
        # A non-resolvable entry should be skipped without error
        manager.set_allowlist(["echo", "totally_fake_command_xyz"])
        # echo should still be allowed
        result = await manager.start_program("echo", args=["ok"])
        assert "id" in result

    async def test_allowlist_reset_to_none(self, manager):
        manager.set_allowlist(["echo"])
        # Manually reset allowlist to None (allow all)
        manager._allowlist = None
        result = await manager.start_program("cat")
        assert "id" in result
