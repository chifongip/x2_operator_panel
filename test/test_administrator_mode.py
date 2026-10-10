"""Session authorization and gateway dispatch without hardware motion."""

from concurrent.futures import Future
from dataclasses import replace
from queue import Queue
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from x2_operator_panel.auth import SessionStore, create_password_hash
from x2_operator_panel.ros_gateway import PanelCommandError, QueuedCommand
from test import test_ros_gateway as gateway_fixtures


@pytest.fixture
def administrator():
    node = gateway_fixtures.RosGatewayTest().rotation_node()
    node._action_clients["rotate_in_place"].server_is_ready = lambda: True
    node.session_store = SessionStore(create_password_hash("operator"), 60.0)
    session = node.session_store.login("operator")
    node._command_session_token = session.token
    node._execution_unlocked_until = 0.0
    node._shutting_down = False
    node._commands = Queue()
    node._audit_sink = Mock()
    return node, session


def dispatch(node, token, name, payload):
    response = Future()
    node._commands.put(QueuedCommand(name, payload, response, token))
    node._drain_commands()
    return response.result()


def test_administrator_lifetime_and_session_replacement(administrator):
    node, first = administrator
    store = node.session_store
    assert not store.administrator_mode_enabled(first.token)
    assert not node._execution_authorized()
    assert store.set_administrator_mode(first.token, True)
    assert store.administrator_mode_enabled(first.token)
    # Repeated reads and a new cookie reference retain the same session setting.
    assert store.valid(first.token)
    assert store.administrator_mode_enabled()
    second = store.login("operator")
    assert not store.valid(first.token)
    assert not store.administrator_mode_enabled(second.token)
    with pytest.raises(ValueError, match="session expired"):
        store.set_administrator_mode(first.token, True)
    store.set_administrator_mode(second.token, True)
    with store._lock:
        store._session = replace(store._session, expires_at=time.monotonic() - 1)
    assert not store.administrator_mode_enabled()
    assert not store.valid(second.token)
    assert not SessionStore(create_password_hash("operator"), 60).administrator_mode_enabled()


def test_mode_dispatch_clears_unlock_and_audits(administrator):
    node, session = administrator
    for enabled in (True, False):
        node._execution_unlocked_until = time.monotonic() + 30
        result = dispatch(node, session.token, "set_administrator_mode",
                          {"enabled": enabled, "confirmed": True})
        assert result == {"administrator_mode_enabled": enabled}
        assert node._execution_unlocked_until == 0
        assert node._command_session_token is None
    assert [call.args[:2] for call in node._audit_sink.call_args_list] == [
        ("administrator_mode", "enabled"), ("administrator_mode", "disabled")]
    assert session.token not in str(node._audit_sink.call_args_list)


@pytest.mark.parametrize("payload", [{}, {"enabled": True},
    {"enabled": "true", "confirmed": True}, {"enabled": 1, "confirmed": True}])
def test_invalid_mode_requests_do_not_grant_authorization(administrator, payload):
    node, session = administrator
    with pytest.raises(PanelCommandError):
        dispatch(node, session.token, "set_administrator_mode", payload)
    assert not node.session_store.administrator_mode_enabled()
    node._audit_sink.assert_not_called()


def test_repeated_physical_dispatch_keeps_admin_and_confirmations(administrator):
    node, session = administrator
    dispatch(node, session.token, "set_administrator_mode", {"enabled": True, "confirmed": True})
    payload = {"kind": "rotate_in_place", "angular_speed": 0.2, "duration": 1.0,
               "confirmed": True, "confirm_nav2_idle": True}
    for _ in range(2):
        dispatch(node, session.token, "submit", payload)
        assert node._execution_unlocked_until == 0
        # Simulate the completed action so task admission permits the next task.
        node._operations.clear()
    assert len(node.goals) == 2
    with pytest.raises(PanelCommandError, match="confirmation"):
        dispatch(node, session.token, "submit", dict(payload, confirmed=False))
    node._nav_lifecycle_status["collision_monitor"]["state_id"] = 2
    with pytest.raises(PanelCommandError, match="Collision Monitor"):
        dispatch(node, session.token, "submit", payload)
    node._nav_lifecycle_status["collision_monitor"]["state_id"] = 3
    dispatch(node, session.token, "set_administrator_mode", {"enabled": False, "confirmed": True})
    with pytest.raises(PanelCommandError, match="unlock has expired"):
        dispatch(node, session.token, "submit", dict(payload, administrator_mode_enabled=True))
    assert len(node.goals) == 2


@pytest.mark.parametrize("invalidate", ["expire", "replace"])
def test_queued_admin_action_revalidates_session(administrator, invalidate):
    node, session = administrator
    node.session_store.set_administrator_mode(session.token, True)
    response = Future()
    node._commands.put(QueuedCommand("submit", {
        "kind": "rotate_in_place", "angular_speed": 0.2, "duration": 1.0,
        "confirmed": True, "confirm_nav2_idle": True}, response, session.token))
    if invalidate == "expire":
        with node.session_store._lock:
            node.session_store._session = replace(session, administrator_mode_enabled=True,
                                                   expires_at=time.monotonic() - 1)
    else:
        node.session_store.login("operator")
    node._drain_commands()
    with pytest.raises(PanelCommandError, match="session expired"):
        response.result()
    assert not node.goals
    assert node._command_session_token is None


def test_session_expiry_is_checked_again_at_submission_gate(administrator):
    node, session = administrator
    node.session_store.set_administrator_mode(session.token, True)
    # Model expiry after initial dispatch validation, before the submission gate.
    assert node._administrator_authorized()
    with node.session_store._lock:
        node.session_store._session = replace(session, expires_at=time.monotonic() - 1)
    node._execution_unlocked_until = time.monotonic() + 30
    with pytest.raises(PanelCommandError, match="session expired"):
        node._execution_authorized()


@pytest.mark.parametrize("kind,settings,state", [
    ("fine_align", {"execute": True}, "EMPTY"),
    ("undock", {}, "EMPTY"),
    ("move_carry_pose", {"plan_only": False, "target_pose": 0}, "HOLDING"),
    ("pick", {"plan_only": False, "plan_id": "saved-plan"}, "EMPTY"),
])
def test_other_physical_actions_use_session_authorization(administrator, kind, settings, state):
    node, session = administrator
    node._action_clients[kind] = node._action_clients["rotate_in_place"]
    node._manipulation_state["state"] = state
    node._table_profile_id = Mock(return_value="table-1")
    payload = dict(settings, kind=kind, confirmed=True, confirm_nav2_idle=True)
    dispatch(node, session.token, "set_administrator_mode", {"enabled": True, "confirmed": True})
    for _ in range(2):
        dispatch(node, session.token, "submit", payload)
        node._operations.clear()
    assert len(node.goals) == 2
    dispatch(node, session.token, "set_administrator_mode", {"enabled": False, "confirmed": True})
    with pytest.raises(PanelCommandError, match="unlock has expired"):
        dispatch(node, session.token, "submit", payload)
    assert len(node.goals) == 2


def test_posture_mode_and_expiry_after_operation_registration(administrator):
    node, session = administrator
    node.posture_service_timeout_sec = 15.0
    node.posture_status_freshness_sec = 3.0
    node._posture_status_received_monotonic = time.monotonic()
    node._posture_status = {
        "execution_enabled": True, "feedback_window_timeout_sec": 20.0,
        "detail": "Ready",
    }
    node._posture_client = gateway_fixtures.FakeServiceClient(
        SimpleNamespace(success=True, message="accepted"))
    payload = {"height": 0.52, "waist_yaw": 0.0, "confirmed": True}
    dispatch(node, session.token, "set_administrator_mode", {"enabled": True, "confirmed": True})
    for _ in range(2):
        dispatch(node, session.token, "set_locomanipulation_posture", payload)
    assert len(node._posture_client.calls) == 2
    register = node._register_operation

    def register_then_expire(operation):
        register(operation)
        with node.session_store._lock:
            node.session_store._session = replace(session, expires_at=time.monotonic() - 1)

    node._register_operation = register_then_expire
    with pytest.raises(PanelCommandError, match="session expired"):
        dispatch(node, session.token, "set_locomanipulation_posture", payload)
    assert len(node._posture_client.calls) == 2
    assert all(operation.status in {"SUCCEEDED", "ERROR"} for operation in node._operations.values())
