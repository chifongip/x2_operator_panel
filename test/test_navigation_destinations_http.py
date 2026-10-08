from unittest.mock import patch
from uuid import UUID

from test.test_task_shortcuts_http import shortcut_http  # noqa: F401
from test.test_task_shortcuts import pick_shortcut
from test.test_navigation_destinations import destination


def test_destination_http_crud_authentication_and_reference_protection(shortcut_http):
    application, request = shortcut_http
    assert request("GET", "/api/presets", authenticated=False)[0] == 401
    assert request("GET", "/api/presets")[1]["presets"] == []
    for path in ("/api/presets/save", "/api/presets/delete"):
        assert request("POST", path, destination(), authenticated=False)[0] == 401
        assert request("POST", path, destination(), same_origin=False)[0] == 403
    status, body = request("POST", "/api/presets/save", destination())
    assert status == 200
    first = body["preset"]
    assert str(UUID(first["id"])) == first["id"]
    status, body = request("POST", "/api/presets/save", dict(first, label="Updated"))
    assert status == 200
    updated = body["preset"]
    assert updated["id"] == first["id"]
    assert request("POST", "/api/presets/save", first)[0] == 409
    assert request("POST", "/api/presets/delete", first)[0] == 409
    shortcut = dict(pick_shortcut(), navigate_start={"enabled": True, "preset_id": first["id"]})
    status, body = request("POST", "/api/task-shortcuts/save", shortcut)
    assert status == 200
    saved_shortcut = body["shortcut"]
    status, body = request("POST", "/api/presets/delete", updated)
    assert status == 400
    assert "Pick grey box" in body["error"]
    assert request("GET", "/api/presets")[1]["presets"] == [updated]
    assert request("POST", "/api/task-shortcuts/delete", saved_shortcut)[0] == 200
    assert request("POST", "/api/presets/delete", updated)[1]["presets"] == []
    assert request("POST", "/api/task-shortcuts/save", shortcut)[0] == 400
    assert not hasattr(application.node, "request"), "Destination editing must not submit robot commands"


def test_destination_storage_errors_are_reported_as_json(shortcut_http):
    application, request = shortcut_http
    assert request("POST", "/api/presets/save", {})[0] == 400
    with patch("x2_operator_panel.navigation_destinations.Path.exists", side_effect=PermissionError("denied")):
        status, body = request("GET", "/api/presets")
        assert status == 200 and not body["available"]
        assert "denied" in body["detail"]
        assert request("POST", "/api/presets/save", destination())[0] == 400
    preset = request("POST", "/api/presets/save", destination())[1]["preset"]
    with patch.object(application.task_shortcuts, "snapshot", return_value={"available": False, "detail": "denied"}):
        assert request("POST", "/api/presets/delete", preset)[0] == 400
