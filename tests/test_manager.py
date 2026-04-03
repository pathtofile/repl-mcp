"""Tests for the REPL process manager."""

import asyncio

import pytest


async def _wait_for_output(manager, prog_id, agent_id="waiter", timeout=3.0, interval=0.1):
    """Wait until read_output returns non-empty output or raise on timeout."""
    elapsed = 0.0
    while elapsed < timeout:
        try:
            result = await manager.read_output(prog_id, agent_id=agent_id, timeout=0)
        except KeyError:
            # Program was already cleaned up — output was consumed by the read loop
            await asyncio.sleep(interval)
            elapsed += interval
            continue
        if result["output"]:
            return result["output"]
        await asyncio.sleep(interval)
        elapsed += interval
    pytest.fail(f"Timed out after {timeout}s waiting for output on {prog_id}")


class TestStartProgram:
    async def test_start_echo(self, manager):
        result = await manager.start_program("echo", args=["hello"])
        assert "id" in result
        assert "pid" in result
        assert result["pid"] > 0

    async def test_id_is_human_readable_name(self, manager):
        result = await manager.start_program("echo", args=["hi"])
        prog_id = result["id"]
        # Should be a hyphenated word pair, not a UUID
        assert "-" in prog_id
        assert len(prog_id.split("-")) >= 2
        # Should not look like a UUID (no hex groups)
        assert len(prog_id) < 36

    async def test_start_nonexistent(self, manager):
        with pytest.raises(FileNotFoundError):
            await manager.start_program("nonexistent_program_xyz")

    async def test_start_returns_resolved_command(self, manager):
        result = await manager.start_program("echo", args=["test"])
        assert result["command"].startswith("/")  # Should be absolute path

    async def test_start_with_cwd(self, manager):
        result = await manager.start_program("sleep", args=["30"], cwd="/tmp")
        prog_id = result["id"]
        prog = manager._programs[prog_id]
        assert prog.cwd == "/tmp"

    async def test_start_with_env(self, manager):
        result = await manager.start_program("sleep", args=["30"], env={"FOO": "bar"})
        prog_id = result["id"]
        prog = manager._programs[prog_id]
        assert prog.env == {"FOO": "bar"}

    async def test_start_registers_program(self, manager):
        result = await manager.start_program("sleep", args=["30"])
        assert result["id"] in manager._programs



class TestSendInput:
    async def test_send_to_nonexistent_program(self, manager):
        with pytest.raises(KeyError):
            await manager.send_input("nonexistent-id", "text")

    async def test_send_to_dead_program(self, manager):
        result = await manager.start_program("echo", args=["done"])
        prog_id = result["id"]
        await asyncio.sleep(1.0)  # Wait for echo to finish and be cleaned up
        # Program is cleaned up after exit — raises KeyError
        with pytest.raises(KeyError):
            await manager.send_input(prog_id, "more input")

    async def test_send_input_echoed_by_pty(self, manager):
        """Verify send_input text appears in output buffer via PTY echo."""
        result = await manager.start_program("cat")
        prog_id = result["id"]
        await asyncio.sleep(0.3)  # Let cat start
        await manager.send_input(prog_id, "hello from agent", source="ai", agent_id="a1")
        # Wait for PTY echo to arrive in the output buffer
        output = await _wait_for_output(manager, prog_id, agent_id="echo-check")
        assert "hello from agent" in output


class TestReadOutput:
    async def test_read_echo_output(self, manager):
        result = await manager.start_program("cat")
        prog_id = result["id"]
        await asyncio.sleep(0.3)
        await manager.send_input(prog_id, "hello\n")
        output = await _wait_for_output(manager, prog_id, agent_id="test-echo")
        assert "hello" in output

    async def test_read_returns_is_running(self, manager):
        result = await manager.start_program("cat")
        prog_id = result["id"]
        await asyncio.sleep(0.3)
        output = await manager.read_output(prog_id, agent_id="test-agent")
        assert "is_running" in output

    async def test_read_with_timeout_returns_quickly(self, manager):
        result = await manager.start_program("cat")
        prog_id = result["id"]
        await asyncio.sleep(0.3)
        # Read all initial output
        await manager.read_output(prog_id, agent_id="test-agent", timeout=0)
        # Read again with timeout - no new output expected, should return quickly
        output = await manager.read_output(prog_id, agent_id="test-agent", timeout=0.1)
        assert "output" in output

    async def test_independent_cursors(self, manager):
        result = await manager.start_program("cat")
        prog_id = result["id"]
        await asyncio.sleep(0.3)
        await manager.send_input(prog_id, "test\n")
        output_text = await _wait_for_output(manager, prog_id, agent_id="agent-1")
        # Agent 2 reads same output independently
        out2 = await manager.read_output(prog_id, agent_id="agent-2")
        assert "test" in output_text
        assert "test" in out2["output"]

    async def test_cursor_advances_after_read(self, manager):
        result = await manager.start_program("cat")
        prog_id = result["id"]
        await asyncio.sleep(0.3)
        # Send some data so there's output to read
        await manager.send_input(prog_id, "data\n", source="ai", agent_id="agent-a")
        await asyncio.sleep(0.5)
        # First read gets all output
        out1 = await manager.read_output(prog_id, agent_id="agent-a")
        assert len(out1["output"]) > 0
        # Second read should get nothing new (cursor advanced past existing data)
        out2 = await manager.read_output(prog_id, agent_id="agent-a")
        assert out2["output"] == ""

    async def test_read_nonexistent_program(self, manager):
        with pytest.raises(KeyError):
            await manager.read_output("nonexistent-id", agent_id="test")

    async def test_read_multiline_output(self, manager):
        result = await manager.start_program("cat")
        prog_id = result["id"]
        await asyncio.sleep(0.3)
        await manager.send_input(prog_id, "line1\nline2\n")
        output = await _wait_for_output(manager, prog_id, agent_id="test-multi")
        assert "line1" in output
        assert "line2" in output


class TestSendSignal:
    async def test_sigint(self, manager):
        result = await manager.start_program("sleep", args=["30"])
        prog_id = result["id"]
        await asyncio.sleep(0.2)
        sig_result = await manager.send_signal(prog_id, "SIGINT")
        assert sig_result["success"] is True

    async def test_sigterm(self, manager):
        result = await manager.start_program("sleep", args=["30"])
        prog_id = result["id"]
        await asyncio.sleep(0.2)
        sig_result = await manager.send_signal(prog_id, "SIGTERM")
        assert sig_result["success"] is True

    async def test_signal_with_short_name(self, manager):
        """Signal names without SIG prefix should also work."""
        result = await manager.start_program("sleep", args=["30"])
        prog_id = result["id"]
        await asyncio.sleep(0.2)
        sig_result = await manager.send_signal(prog_id, "INT")
        assert sig_result["success"] is True

    async def test_invalid_signal(self, manager):
        result = await manager.start_program("sleep", args=["30"])
        prog_id = result["id"]
        await asyncio.sleep(0.2)
        with pytest.raises(ValueError, match="Unknown signal"):
            await manager.send_signal(prog_id, "SIGFAKE")

    async def test_signal_nonexistent_program(self, manager):
        with pytest.raises(KeyError):
            await manager.send_signal("nonexistent-id", "SIGINT")


class TestKillProgram:
    async def test_kill(self, manager):
        result = await manager.start_program("sleep", args=["30"])
        prog_id = result["id"]
        await asyncio.sleep(0.2)
        kill_result = await manager.kill_program(prog_id)
        assert kill_result["success"] is True
        # Program should be removed from listing after kill
        programs = manager.list_programs()
        assert all(p["id"] != prog_id for p in programs)

    async def test_kill_already_exited(self, manager):
        result = await manager.start_program("echo", args=["done"])
        prog_id = result["id"]
        await asyncio.sleep(1.0)  # Wait for echo to finish and be cleaned up
        kill_result = await manager.kill_program(prog_id)
        assert kill_result["success"] is True

    async def test_kill_nonexistent_program(self, manager):
        # Killing a nonexistent program succeeds silently (idempotent)
        kill_result = await manager.kill_program("nonexistent-id")
        assert kill_result["success"] is True

    async def test_kill_removes_program(self, manager):
        result = await manager.start_program("sleep", args=["30"])
        prog_id = result["id"]
        await asyncio.sleep(0.2)
        assert prog_id in manager._programs
        await manager.kill_program(prog_id)
        assert prog_id not in manager._programs


class TestListPrograms:
    async def test_list_empty(self, manager):
        assert manager.list_programs() == []

    async def test_list_after_start(self, manager):
        await manager.start_program("sleep", args=["30"])
        programs = manager.list_programs()
        assert len(programs) == 1
        assert "id" in programs[0]
        assert "command" in programs[0]
        assert "pid" in programs[0]
        assert "is_running" in programs[0]

    async def test_list_multiple(self, manager):
        await manager.start_program("sleep", args=["30"])
        await manager.start_program("sleep", args=["30"])
        programs = manager.list_programs()
        assert len(programs) == 2

    async def test_list_contains_started_at(self, manager):
        await manager.start_program("sleep", args=["30"])
        programs = manager.list_programs()
        assert "started_at" in programs[0]

    async def test_exited_program_removed_from_list(self, manager):
        result = await manager.start_program("echo", args=["bye"])
        prog_id = result["id"]
        await asyncio.sleep(1.0)  # Wait for echo to finish and be cleaned up
        programs = manager.list_programs()
        assert all(p["id"] != prog_id for p in programs)


