"""Tests for the MCP server."""

from unittest.mock import MagicMock

import pytest

from repl_mcp.manager import ProgramManager
from repl_mcp.server import ReplMCPServer


def _make_ctx(session=None):
    """Create a mock Context with a given session object."""
    ctx = MagicMock()
    ctx.session = session if session is not None else object()
    return ctx


class TestServerInit:
    def test_create_server(self):
        manager = ProgramManager()
        server = ReplMCPServer(manager=manager)
        assert server.mcp is not None
        assert server.agent_count == 0

    def test_default_host_port(self):
        manager = ProgramManager()
        server = ReplMCPServer(manager=manager)
        assert server.host == "127.0.0.1"
        assert server.port == 8780

    def test_custom_host_port(self):
        manager = ProgramManager()
        server = ReplMCPServer(manager=manager, host="0.0.0.0", port=9999)
        assert server.host == "0.0.0.0"
        assert server.port == 9999

    def test_server_with_token(self):
        manager = ProgramManager()
        server = ReplMCPServer(manager=manager, token="test-token")
        assert server.token == "test-token"

    def test_server_without_token(self):
        manager = ProgramManager()
        server = ReplMCPServer(manager=manager)
        assert server.token is None


class TestAgentLabels:
    def test_same_session_returns_same_label(self):
        manager = ProgramManager()
        server = ReplMCPServer(manager=manager)
        session = object()
        ctx = _make_ctx(session)
        label1 = server._get_agent_label(ctx)
        label2 = server._get_agent_label(ctx)
        assert label1 == "agent-1"
        assert label2 == "agent-1"
        assert server.agent_count == 1

    def test_different_sessions_get_different_labels(self):
        manager = ProgramManager()
        server = ReplMCPServer(manager=manager)
        ctx1 = _make_ctx(object())
        ctx2 = _make_ctx(object())
        label1 = server._get_agent_label(ctx1)
        label2 = server._get_agent_label(ctx2)
        assert label1 == "agent-1"
        assert label2 == "agent-2"
        assert server.agent_count == 2

    def test_agent_labels_sequential(self):
        manager = ProgramManager()
        server = ReplMCPServer(manager=manager)
        sessions = [object() for _ in range(5)]
        labels = [server._get_agent_label(_make_ctx(s)) for s in sessions]
        assert labels == ["agent-1", "agent-2", "agent-3", "agent-4", "agent-5"]
        assert server.agent_count == 5

    def test_many_calls_same_session_stable(self):
        manager = ProgramManager()
        server = ReplMCPServer(manager=manager)
        session = object()
        ctx = _make_ctx(session)
        labels = [server._get_agent_label(ctx) for _ in range(10)]
        assert all(label == "agent-1" for label in labels)
        assert server.agent_count == 1


class TestServerManager:
    def test_server_holds_manager_reference(self):
        manager = ProgramManager()
        server = ReplMCPServer(manager=manager)
        assert server.manager is manager
