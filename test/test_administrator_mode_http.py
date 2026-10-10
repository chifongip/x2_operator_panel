"""Exercise administrator HTTP routes against real session and dispatch checks."""

from concurrent.futures import Future
from queue import Queue
from unittest.mock import Mock

import pytest

from test import test_ros_gateway as gateway_fixtures
from test.test_task_shortcuts_http import shortcut_http  # noqa: F401
from x2_operator_panel.auth import SessionStore, create_password_hash
from x2_operator_panel.panel_server import AuditLog, PanelApplication
from x2_operator_panel.ros_gateway import QueuedCommand


@pytest.fixture
def administrator_http(shortcut_http):
    application, request = shortcut_http
    node = gateway_fixtures.RosGatewayTest().rotation_node()
    node.http_request_timeout_sec = 2.0
    node.get_logger = Mock(return_value=Mock())
    node._shutting_down = False
    node._commands = Queue()
    node.session_store = application.sessions = SessionStore(create_password_hash("operator"), 60)
    session = application.sessions.login("operator")
    application.audit = AuditLog()
    node._audit_sink = application.audit.add
    node.snapshot = lambda: {"execution_unlock_remaining_sec": 0}
    application.node = node
    application.status = lambda: PanelApplication.status(application)
    application.authenticated_session_token = lambda headers: (
        session.token if headers.get("Cookie") == "session=test"
        and application.sessions.valid(session.token) else None)

    def dispatch(name, payload, *, session_token=None):
        response = Future()
        node._commands.put(QueuedCommand(name, payload, response, session_token))
        node._drain_commands()
        return response.result()

    node.request = dispatch
    return application, request


def test_administrator_http_enable_status_disable(administrator_http):
    application, request = administrator_http
    assert request("GET", "/api/status")[1]["administrator_mode_enabled"] is False
    assert request("POST", "/api/administrator-mode", {"enabled": True, "confirmed": True}) == (
        202, {"administrator_mode_enabled": True})
    for _ in range(2):
        assert request("GET", "/api/status")[1]["administrator_mode_enabled"] is True
    assert request("POST", "/api/administrator-mode", {"enabled": False, "confirmed": True}) == (
        202, {"administrator_mode_enabled": False})
    assert [entry["outcome"] for entry in application.audit.entries()] == ["disabled", "enabled"]


def test_administrator_http_rejects_invalid_requests(administrator_http):
    application, request = administrator_http
    payload = {"enabled": True, "confirmed": True}
    assert request("POST", "/api/administrator-mode", payload, authenticated=False)[0] == 401
    assert request("POST", "/api/administrator-mode", payload, same_origin=False)[0] == 403
    for invalid in ({}, {"enabled": True}, {"enabled": "true", "confirmed": True}):
        assert request("POST", "/api/administrator-mode", invalid)[0] == 400
    assert application.audit.entries() == []
    assert not application.sessions.administrator_mode_enabled()
    application.sessions.login("operator")
    assert request("POST", "/api/administrator-mode", payload)[0] == 401
