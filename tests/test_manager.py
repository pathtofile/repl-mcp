"""Tests for the REPL process manager."""

import asyncio

import pytest


async def _wait_for_buffer(manager, prog_id, timeout=3.0, interval=0.1):
    """Wait until the program's output buffer has content or raise on timeout."""
    prog = manager._programs[prog_id]
    elapsed = 0.0
    while elapsed < timeout:
        if prog.output_buffer:
            return
        await asyncio.sleep(interval)
        elapsed += interval
    pytest.fail(f"Timed out after {timeout}s waiting for output buffer on {prog_id}")


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
        result = await manager.start_program("echo", args=["hi"], cwd="/tmp")
        prog = manager._programs[result["id"]]
        assert prog.cwd == "/tmp"

    async def test_start_with_env(self, manager):
        result = await manager.start_program("echo", args=["hi"], env={"FOO": "bar"})
        prog = manager._programs[result["id"]]
        assert prog.env == {"FOO": "bar"}

    async def test_start_registers_program(self, manager):
        result = await manager.start_program("echo", args=["hi"])
        assert result["id"] in manager._programs

    async def test_start_with_owner_agent(self, manager):
        result = await manager.start_program("echo", args=["hi"], owner_agent="agent-1")
        prog = manager._programs[result["id"]]
        assert prog.owner_agent == "agent-1"


class TestSendInput:
    async def test_send_to_nonexistent_program(self, manager):
        with pytest.raises(KeyError):
            await manager.send_input("nonexistent-id", "text")

    async def test_send_to_dead_program(self, manager):
        result = await manager.start_program("echo", args=["done"])
        prog_id = result["id"]
        await asyncio.sleep(1.0)  # Wait for echo to finish
        with pytest.raises(RuntimeError):
            await manager.send_input(prog_id, "more input")

    async def test_send_tracks_input_in_output_buffer(self, manager):
        """Verify send_input records attributed input in the output buffer."""
        result = await manager.start_program("echo", args=["hi"])
        prog_id = result["id"]
        prog = manager._programs[prog_id]
        # Force is_running so we can test the input tracking
        prog.is_running = True
        try:
            await manager.send_input(prog_id, "test input", source="ai", agent_id="a1")
        except (RuntimeError, OSError):
            pass  # PTY may be closed, but buffer should still be updated
        # Check that input was tracked in the buffer (contains attribution marker)
        buffer_text = "".join(prog.output_buffer)
        assert "test input" in buffer_text


class TestReadOutput:
    async def test_read_echo_output(self, manager):
        result = await manager.start_program("echo", args=["hello"])
        prog_id = result["id"]
        await _wait_for_buffer(manager, prog_id)
        output = await manager.read_output(prog_id, agent_id="test-echo")
        assert "hello" in output["output"]

    async def test_read_returns_is_running(self, manager):
        result = await manager.start_program("echo", args=["hi"])
        prog_id = result["id"]
        await asyncio.sleep(0.5)
        output = await manager.read_output(prog_id, agent_id="test-agent")
        assert "is_running" in output

    async def test_read_with_timeout_returns_quickly(self, manager):
        result = await manager.start_program("echo", args=["hi"])
        prog_id = result["id"]
        await asyncio.sleep(0.5)
        # Read all initial output
        await manager.read_output(prog_id, agent_id="test-agent", timeout=0)
        # Read again with timeout - no new output expected, should return quickly
        output = await manager.read_output(prog_id, agent_id="test-agent", timeout=0.1)
        assert "output" in output

    async def test_independent_cursors(self, manager):
        result = await manager.start_program("echo", args=["test"])
        prog_id = result["id"]
        await _wait_for_buffer(manager, prog_id)
        # Agent 1 reads
        out1 = await manager.read_output(prog_id, agent_id="agent-1")
        # Agent 2 reads same output independently
        out2 = await manager.read_output(prog_id, agent_id="agent-2")
        assert "test" in out1["output"]
        assert out1["output"] == out2["output"]

    async def test_cursor_advances_after_read(self, manager):
        result = await manager.start_program("echo", args=["data"])
        prog_id = result["id"]
        await _wait_for_buffer(manager, prog_id)
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
        result = await manager.start_program(
            "python3", args=["-u", "-c", "print('line1'); print('line2')"]
        )
        prog_id = result["id"]
        await _wait_for_buffer(manager, prog_id)
        output = await manager.read_output(prog_id, agent_id="test-multi")
        assert "line1" in output["output"]
        assert "line2" in output["output"]


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
        # Verify program is marked as not running
        programs = manager.list_programs()
        prog = [p for p in programs if p["id"] == prog_id][0]
        assert prog["is_running"] is False

    async def test_kill_already_exited(self, manager):
        result = await manager.start_program("echo", args=["done"])
        prog_id = result["id"]
        await asyncio.sleep(1.0)  # Wait for echo to finish
        kill_result = await manager.kill_program(prog_id)
        assert kill_result["success"] is True

    async def test_kill_nonexistent_program(self, manager):
        with pytest.raises(KeyError):
            await manager.kill_program("nonexistent-id")

    async def test_kill_closes_pty_fd(self, manager):
        result = await manager.start_program("sleep", args=["30"])
        prog_id = result["id"]
        await asyncio.sleep(0.2)
        await manager.kill_program(prog_id)
        prog = manager._programs[prog_id]
        assert prog.pty_fd == -1


class TestListPrograms:
    async def test_list_empty(self, manager):
        assert manager.list_programs() == []

    async def test_list_after_start(self, manager):
        await manager.start_program("echo", args=["hi"])
        programs = manager.list_programs()
        assert len(programs) == 1
        assert "id" in programs[0]
        assert "command" in programs[0]
        assert "pid" in programs[0]
        assert "is_running" in programs[0]

    async def test_list_multiple(self, manager):
        await manager.start_program("echo", args=["one"])
        await manager.start_program("echo", args=["two"])
        programs = manager.list_programs()
        assert len(programs) == 2

    async def test_list_contains_started_at(self, manager):
        await manager.start_program("echo", args=["hi"])
        programs = manager.list_programs()
        assert "started_at" in programs[0]

    async def test_list_contains_owner_agent(self, manager):
        await manager.start_program("echo", args=["hi"], owner_agent="agent-1")
        programs = manager.list_programs()
        assert programs[0]["owner_agent"] == "agent-1"


class TestAdoptProgram:
    async def test_adopt_unowned_program(self, manager):
        result = await manager.start_program("echo", args=["hi"], owner_agent="")
        prog_id = result["id"]
        adopt_result = await manager.adopt_program(prog_id, agent_id="agent-1")
        assert adopt_result["success"] is True
        assert adopt_result["owner_agent"] == "agent-1"
        assert manager._programs[prog_id].owner_agent == "agent-1"

    async def test_adopt_already_owned_by_same_agent(self, manager):
        result = await manager.start_program("echo", args=["hi"], owner_agent="agent-1")
        prog_id = result["id"]
        adopt_result = await manager.adopt_program(prog_id, agent_id="agent-1")
        assert adopt_result["success"] is True

    async def test_adopt_already_owned_by_other_agent(self, manager):
        result = await manager.start_program("echo", args=["hi"], owner_agent="agent-1")
        prog_id = result["id"]
        with pytest.raises(RuntimeError, match="already owned"):
            await manager.adopt_program(prog_id, agent_id="agent-2")

    async def test_adopt_nonexistent_program(self, manager):
        with pytest.raises(KeyError):
            await manager.adopt_program("nonexistent-id", agent_id="agent-1")
