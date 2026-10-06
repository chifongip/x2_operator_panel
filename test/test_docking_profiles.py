"""Check docking discovery and API selection without commanding hardware."""

from collections import deque
from concurrent.futures import Future
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from rclpy.parameter import Parameter
from x2_navigation.action import FineAlign, Undock

from x2_operator_panel.docking_profiles import DockingProfileMonitor
from x2_operator_panel.ros_gateway import OperatorPanelNode, PanelCommandError


def configured_parameters():
    return {
        "docking_profile_names": ["offset"], "default_docking_profile": "default",
        "tag_id": 9, "tag_frame": "tag9", "standoff": 0.5,
        "lateral_offset": 0.0, "yaw_offset": 0.0,
        "docking_profiles.offset.tag_id": 10,
        "docking_profiles.offset.tag_frame": "tag10",
        "docking_profiles.offset.standoff": 0.7,
        "docking_profiles.offset.lateral_offset": -0.1,
        "docking_profiles.offset.yaw_offset": 0.2,
    }


def response(parameters, request):
    values = []
    for name in request.names:
        value = parameters.get(name)
        parameter_type = Parameter.Type.STRING_ARRAY if value == [] else None
        values.append(Parameter(name, type_=parameter_type, value=value).get_parameter_value())
    return SimpleNamespace(values=values)


class LateFuture(Future):
    def cancel(self):
        return False  # Simulate a remote response already in flight.


class ParameterClient:
    def __init__(self, deferred=False):
        self.parameters = configured_parameters()
        self.ready = True
        self.deferred = deferred
        self.requests = []
        self.removed = []

    def service_is_ready(self):
        return self.ready

    def call_async(self, request):
        future = LateFuture()
        self.requests.append((request, future))
        if not self.deferred:
            future.set_result(response(self.parameters, request))
        return future

    def remove_pending_request(self, future):
        self.removed.append(future)


class DockingProfileMonitorTest(unittest.TestCase):
    def test_discovers_offsets_and_returns_independent_snapshots(self):
        client = ParameterClient()
        monitor = DockingProfileMonitor(client, 1.0)
        monitor.poll()
        catalog = monitor.snapshot()
        self.assertTrue(catalog["available"])
        self.assertEqual(catalog["default_profile"], "default")
        self.assertEqual([item["id"] for item in catalog["profiles"]], ["default", "offset"])
        self.assertEqual(catalog["profiles"][1]["tag_id"], 10)
        self.assertEqual(catalog["profiles"][1]["lateral_offset"], -0.1)
        catalog["profiles"].clear()
        self.assertEqual(len(monitor.snapshot()["profiles"]), 2)
        monitor.poll()
        self.assertEqual(len(client.requests), 2, "Do not poll parameters on every status tick")

    def test_default_only_server_and_alternate_configured_default(self):
        client = ParameterClient()
        client.parameters["docking_profile_names"] = []
        monitor = DockingProfileMonitor(client, 1.0)
        monitor.poll()
        self.assertEqual(len(monitor.snapshot()["profiles"]), 1)
        client = ParameterClient()
        client.parameters["default_docking_profile"] = "offset"
        monitor = DockingProfileMonitor(client, 1.0)
        monitor.poll()
        self.assertEqual(monitor.snapshot()["default_profile"], "offset")

    def test_timeout_discards_late_response_and_retries(self):
        client = ParameterClient(deferred=True)
        now = [0.0]
        monitor = DockingProfileMonitor(client, 1.0, clock=lambda: now[0])
        monitor.poll()
        old_request, old_future = client.requests[0]
        monitor.poll()
        self.assertEqual(len(client.requests), 1)
        now[0] = 1.1
        monitor.poll()
        self.assertFalse(monitor.snapshot()["available"])
        self.assertIn("timed out", monitor.snapshot()["detail"])
        self.assertEqual(client.removed, [old_future])
        now[0] = 5.1
        monitor.poll()
        old_future.set_result(response(client.parameters, old_request))
        self.assertEqual(len(client.requests), 2, "An old response must not launch a second stage")
        request, future = client.requests[-1]
        future.set_result(response(client.parameters, request))
        monitor.poll()
        self.assertEqual(len(client.requests), 3)
        request, future = client.requests[-1]
        future.set_result(response(client.parameters, request))
        self.assertTrue(monitor.snapshot()["available"])

    def test_disconnect_clears_catalog_and_restart_refreshes_profiles(self):
        client = ParameterClient()
        monitor = DockingProfileMonitor(client, 1.0)
        monitor.poll()
        client.ready = False
        monitor.poll()
        self.assertFalse(monitor.snapshot()["available"])
        self.assertEqual(monitor.snapshot()["profiles"], [])
        client.parameters["default_docking_profile"] = "offset"
        client.ready = True
        monitor.poll()
        self.assertEqual(monitor.snapshot()["default_profile"], "offset")

    def test_response_after_deadline_is_rejected_before_next_poll(self):
        for stage in (0, 1):
            with self.subTest(stage=stage):
                client = ParameterClient(deferred=True)
                now = [0.0]
                monitor = DockingProfileMonitor(client, 1.0, clock=lambda: now[0])
                monitor.poll()
                if stage:
                    request, future = client.requests[-1]
                    future.set_result(response(client.parameters, request))
                request, future = client.requests[-1]
                now[0] = 1.1
                future.set_result(response(client.parameters, request))
                self.assertFalse(monitor.snapshot()["available"])
                self.assertIn("timed out", monitor.snapshot()["detail"])
                self.assertEqual(len(client.requests), stage + 1)

    def test_incomplete_or_invalid_catalog_is_not_exposed(self):
        for changes in (
            {"docking_profile_names": None},
            {"docking_profile_names": ["default"]},
            {"docking_profile_names": ["bad.name"]},
            {"default_docking_profile": "missing"},
            {"docking_profiles.offset.tag_id": -1},
            {"docking_profiles.offset.standoff": 0.0},
            {"docking_profiles.offset.lateral_offset": None},
        ):
            with self.subTest(changes=changes):
                client = ParameterClient()
                client.parameters.update(changes)
                monitor = DockingProfileMonitor(client, 1.0)
                monitor.poll()
                self.assertFalse(monitor.snapshot()["available"])
                self.assertEqual(monitor.snapshot()["profiles"], [])


class DockingProfileGatewayTest(unittest.TestCase):
    def panel(self):
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        node._assert_task_idle = Mock()
        node._assert_task_idle_locked = Mock()
        node._operations = {}
        node._operation_history = deque(maxlen=10)
        node._manipulation_state = {"state": "EMPTY"}
        node._nav_goal_status_locked = Mock(return_value={"available": True, "active": False})
        node._nav_lifecycle_status = {"collision_monitor": {"state_id": 3}}
        node._execution_unlocked_until = time.monotonic() + 30.0
        node.goal_admission_timeout_sec = 5.0
        node._audit_sink = None
        node._on_goal_response = Mock()
        node._docking_profile_monitor = DockingProfileMonitor(ParameterClient(), 1.0)
        node._docking_profile_monitor.poll()
        node._action_clients = {kind: Mock() for kind in ("fine_align", "undock")}
        return node

    def test_named_profiles_reach_both_actions_and_operation_history(self):
        for kind in ("fine_align", "undock"):
            node = self.panel()
            submit = node._submit_fine_align if kind == "fine_align" else node._submit_undock
            operation = submit({"profile_id": "offset", "execute": True, "confirmed": True})
            goal = node._action_clients[kind].send_goal_async.call_args.args[0]
            self.assertEqual(goal.profile_id, "offset")
            self.assertEqual(operation.as_dict()["profile_id"], "offset")
            self.assertEqual(node._execution_unlocked_until, 0.0)

    def test_invalid_selection_does_not_consume_unlock_or_submit_goal(self):
        for kind in ("fine_align", "undock"):
            for profile in (None, 9, "missing", "bad.name", " offset", "é"):
                node = self.panel()
                unlocked_until = node._execution_unlocked_until
                submit = node._submit_fine_align if kind == "fine_align" else node._submit_undock
                with self.assertRaises(PanelCommandError):
                    submit({"profile_id": profile, "execute": True, "confirmed": True})
                self.assertEqual(node._execution_unlocked_until, unlocked_until)
                node._action_clients[kind].send_goal_async.assert_not_called()

    def test_automatic_selection_survives_discovery_unavailability(self):
        for kind in ("fine_align", "undock"):
            node = self.panel()
            node._docking_profile_monitor.client.ready = False
            node._docking_profile_monitor.poll()
            submit = node._submit_fine_align if kind == "fine_align" else node._submit_undock
            with self.assertRaisesRegex(PanelCommandError, "configuration is unavailable"):
                submit({"profile_id": "offset", "confirmed": True})
            operation = submit({"confirmed": True})
            goal = node._action_clients[kind].send_goal_async.call_args.args[0]
            self.assertEqual(goal.profile_id, "")
            self.assertEqual(operation.profile_id, "")

    def test_resolved_profiles_are_serialized_from_feedback_and_results(self):
        node = self.panel()
        operation = node._submit_fine_align({})
        feedback = FineAlign.Feedback()
        feedback.profile_id = "offset"
        node._on_feedback(operation.identifier, SimpleNamespace(feedback=feedback))
        self.assertEqual(operation.feedback["profile_id"], "offset")
        for result in (FineAlign.Result(), Undock.Result()):
            result.profile_id = "offset"
            self.assertEqual(node._result_as_dict(result)["profile_id"], "offset")
