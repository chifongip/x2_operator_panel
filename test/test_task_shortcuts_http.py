from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from x2_operator_panel.panel_server import _make_request_handler
from x2_operator_panel.task_shortcuts import TaskShortcutStore
from x2_operator_panel.navigation_destinations import NavigationDestinationStore
from test.test_task_shortcuts import pick_shortcut


@pytest.fixture
def shortcut_http(tmp_path):
    application = SimpleNamespace(
        node=SimpleNamespace(http_request_timeout_sec=2.0, get_logger=Mock(return_value=Mock()),
                             navigation_destinations=NavigationDestinationStore(tmp_path / "destinations.json")),
        configuration_lock=threading.RLock(),
        source_address_is_allowed=lambda address: address == "127.0.0.1",
        request_is_authenticated=lambda headers: headers.get("Cookie") == "session=test",
        unsafe_request_has_same_origin=lambda headers: headers.get("Origin") == "http://panel.test",
        audit=Mock(), task_shortcuts=TaskShortcutStore(tmp_path / "shortcuts.json"),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_request_handler(application))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, payload=None, authenticated=True, same_origin=True):
        connection = HTTPConnection(*server.server_address, timeout=3.0)
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Cookie"] = "session=test"
        if same_origin:
            headers["Origin"] = "http://panel.test"
        connection.request(method, path, json.dumps(payload) if payload is not None else None, headers)
        response = connection.getresponse()
        status, body = response.status, json.loads(response.read())
        connection.close()
        return status, body

    try:
        yield application, request
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3.0)


def test_shortcut_http_crud_and_edit_conflicts(shortcut_http):
    application, request = shortcut_http
    assert request("GET", "/api/task-shortcuts")[1]["shortcuts"] == []
    status, body = request("POST", "/api/task-shortcuts/save", pick_shortcut())
    assert status == 200
    original = body["shortcut"]
    status, body = request("POST", "/api/task-shortcuts/save", dict(original, name="Changed"))
    assert status == 200
    updated = body["shortcut"]
    assert request("POST", "/api/task-shortcuts/save", original)[0] == 409
    assert request("POST", "/api/task-shortcuts/delete", original)[0] == 409
    assert request("GET", "/api/task-shortcuts")[1]["shortcuts"] == [updated]
    assert request("POST", "/api/task-shortcuts/delete", updated)[1]["shortcuts"] == []
    assert application.audit.add.call_count == 3


def test_shortcut_storage_permission_error_returns_json(shortcut_http):
    application, request = shortcut_http
    with patch("x2_operator_panel.task_shortcuts.Path.exists", side_effect=PermissionError("Access denied")):
        status, body = request("GET", "/api/task-shortcuts")
        assert status == 200
        assert not body["available"]
        assert "Access denied" in body["detail"]
        status, body = request("POST", "/api/task-shortcuts/save", pick_shortcut())
        assert status == 400
        assert "Access denied" in body["error"]
    application.audit.add.assert_not_called()


def test_shortcut_routes_require_authentication_and_same_origin(shortcut_http):
    application, request = shortcut_http
    assert request("GET", "/api/task-shortcuts", authenticated=False)[0] == 401
    for path in ("/api/task-shortcuts/save", "/api/task-shortcuts/delete"):
        assert request("POST", path, pick_shortcut(), authenticated=False)[0] == 401
        assert request("POST", path, pick_shortcut(), same_origin=False)[0] == 403
    assert request("POST", "/api/task-shortcuts/save", {})[0] == 400
    assert not application.task_shortcuts.path.exists()
    application.audit.add.assert_not_called()
