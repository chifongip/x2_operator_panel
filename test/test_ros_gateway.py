from collections import deque
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from math import inf, nan, pi
from queue import Queue
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from builtin_interfaces.msg import Time
from x2_operator_panel.ros_gateway import (
    Operation,
    OperatorPanelNode,
    PanelCommandError,
)


class FakeGoalHandle:
    def __init__(self, fail=False):
        self.cancel_calls = 0
        self.fail = fail
        self.accepted = True
        self.goal_id = type("GoalId", (), {"uuid": bytes(range(16))})()
        self.result_future = Future()

    def cancel_goal_async(self):
        self.cancel_calls += 1
        if self.fail:
            raise RuntimeError("cancel transport failed")
        response = Future()
        response.set_result(type("Response", (), {"goals_canceling": [object()]})())
        return response

    def get_result_async(self):
        return self.result_future


class RosGatewayTest(unittest.TestCase):
    @staticmethod
    def _map_transform(stamp_sec):
        return SimpleNamespace(
            header=SimpleNamespace(
                stamp=SimpleNamespace(sec=stamp_sec, nanosec=0)
            ),
            transform=SimpleNamespace(
                translation=SimpleNamespace(x=1.0, y=2.0, z=0.0),
                rotation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
            ),
        )

    def test_map_pose_freshness_uses_transform_updates_not_source_clock(self):
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        node._last_map_transform_stamp = None
        node._last_map_transform_update_monotonic = None
        node._last_tf_error = ""
        node._map_pose = {}
        node.tf_freshness_sec = 1.0
        node._tf_buffer = SimpleNamespace(
            lookup_transform=lambda *_args, **_kwargs: self._map_transform(42)
        )
        node.get_clock = lambda: SimpleNamespace(
            now=lambda: SimpleNamespace(nanoseconds=9_000_000_000)
        )

        with patch(
            "x2_operator_panel.ros_gateway.time.monotonic",
            side_effect=[100.0, 101.1],
        ):
            node._poll_map_pose()
            self.assertTrue(node._map_pose["fresh"])
            self.assertEqual(node._map_pose["source_age_sec"], -33.0)
            node._poll_map_pose()

        self.assertFalse(node._map_pose["fresh"])
        self.assertIn("has not updated", node._map_pose["detail"])

        node._tf_buffer = SimpleNamespace(
            lookup_transform=lambda *_args, **_kwargs: self._map_transform(43)
        )
        with patch(
            "x2_operator_panel.ros_gateway.time.monotonic", return_value=101.2
        ):
            node._poll_map_pose()

        self.assertTrue(node._map_pose["fresh"])

    def test_box_pose_in_base_link_projects_into_the_map(self):
        node = object.__new__(OperatorPanelNode)
        node._box_pose = {
            "frame_id": "base_link",
            "x": 1.0,
            "y": 0.0,
            "z": 0.2,
        }
        node._box_pose_received_monotonic = 100.0
        node.box_pose_freshness_sec = 0.5
        node._map_pose = {
            "available": True,
            "fresh": True,
            "x": 3.0,
            "y": 4.0,
            "yaw": pi / 2.0,
        }

        with patch(
            "x2_operator_panel.ros_gateway.time.monotonic", return_value=100.1
        ):
            box_pose = node._box_pose_in_map_locked()

        self.assertTrue(box_pose["available"])
        self.assertTrue(box_pose["fresh"])
        self.assertAlmostEqual(box_pose["x"], 3.0)
        self.assertAlmostEqual(box_pose["y"], 5.0)
        self.assertEqual(box_pose["source_frame_id"], "base_link")

    def test_box_pose_becomes_stale_without_new_detection(self):
        node = object.__new__(OperatorPanelNode)
        node._box_pose = {"frame_id": "map", "x": 1.0, "y": 2.0, "z": 0.2}
        node._box_pose_received_monotonic = 100.0
        node.box_pose_freshness_sec = 0.5
        node._map_pose = {"available": False, "fresh": False}

        with patch(
            "x2_operator_panel.ros_gateway.time.monotonic", return_value=100.6
        ):
            box_pose = node._box_pose_in_map_locked()

        self.assertTrue(box_pose["available"])
        self.assertFalse(box_pose["fresh"])
        self.assertIn("/box_pose has not updated", box_pose["detail"])

    def test_place_commands_leave_place_pose_empty(self):
        node = object.__new__(OperatorPanelNode)

        for kind in ("place", "pick_place"):
            goal = node._build_manipulation_goal(kind, {}, True)

            self.assertTrue(goal.plan_only)
            self.assertEqual(goal.place_pose.header.frame_id, "")

    def test_manual_place_pose_is_added_to_the_goal(self):
        node = object.__new__(OperatorPanelNode)
        manual_pose = {
            "frame_id": "base_link",
            "x": 0.35,
            "y": 0.0,
            "z": 0.29,
            "yaw": 0.0,
        }

        for kind in ("place", "pick_place"):
            goal = node._build_manipulation_goal(kind, {"place_pose": manual_pose}, True)

            self.assertTrue(goal.plan_only)
            self.assertEqual(goal.place_pose.header.frame_id, "base_link")
            self.assertAlmostEqual(goal.place_pose.pose.position.x, 0.35)
            self.assertAlmostEqual(goal.place_pose.pose.position.y, 0.0)
            self.assertAlmostEqual(goal.place_pose.pose.position.z, 0.29)
            self.assertAlmostEqual(goal.place_pose.pose.orientation.w, 1.0)

    def test_timed_out_queued_command_is_not_executed_later(self):
        node = object.__new__(OperatorPanelNode)
        node._commands = Queue()
        node._shutting_down = False
        called = []
        node._unlock_execution = lambda: called.append(True) or {}

        with self.assertRaises(FutureTimeoutError):
            node.request("unlock_execution", {}, timeout_sec=0.001)
        node._drain_commands()

        self.assertEqual(called, [])

    def test_successful_operation_normalizes_progress_to_complete(self):
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        node._audit_sink = None
        operation = Operation("completed", "pick", time.time(), progress=0.15)
        node._operations = {operation.identifier: operation}

        node._finish_operation(
            operation.identifier,
            "SUCCEEDED",
            {"success": True, "message": "plan is feasible"},
        )

        self.assertEqual(operation.progress, 1.0)
        self.assertEqual(operation.status, "SUCCEEDED")

    def test_unsuccessful_operation_retains_last_reported_progress(self):
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        node._audit_sink = None
        operation = Operation("failed", "pick", time.time(), progress=0.15)
        node._operations = {operation.identifier: operation}

        node._finish_operation(
            operation.identifier,
            "ABORTED",
            {"success": False, "message": "planning failed"},
        )

        self.assertEqual(operation.progress, 0.15)

    def test_operation_history_evicts_old_records(self):
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        node._operations = {}
        node._operation_history = deque(maxlen=2)
        for identifier in ("first", "second", "third"):
            node._register_operation(Operation(identifier, "pick", time.time()))

        self.assertEqual(list(node._operations), ["second", "third"])

    def test_navigation_requires_fresh_map_pose(self):
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        node._manipulation_state = {"state": "EMPTY"}
        node._map_pose = {"available": True, "fresh": False}

        with self.assertRaisesRegex(PanelCommandError, "fresh map"):
            node._submit_navigation({"confirmed": True, "preset_id": "dock"})

    def test_initial_pose_is_rejected_until_nav2_reports_idle(self):
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        node._operations = {}
        node._nav_goal_status = {"available": True, "active": True}

        with self.assertRaisesRegex(PanelCommandError, "Nav2 must be idle"):
            node._set_initial_pose({"x": 1.0, "y": 2.0, "yaw": 0.0, "confirmed": True})

    def test_initial_pose_without_status_requires_operator_idle_confirmation(self):
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        node._operations = {}
        node._nav_goal_status = {"available": False, "active": None}
        node._action_clients = {
            "navigate": SimpleNamespace(server_is_ready=lambda: True)
        }

        with self.assertRaisesRegex(PanelCommandError, "verify Nav2 is idle"):
            node._set_initial_pose({"x": 1.0, "y": 2.0, "yaw": 0.0, "confirmed": True})

    def test_initial_pose_publishes_map_pose_after_idle_nav2(self):
        published = []
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        node._operations = {}
        node._nav_goal_status = {"available": True, "active": False}
        node._action_clients = {
            "navigate": SimpleNamespace(server_is_ready=lambda: True)
        }
        node._initial_pose_publisher = SimpleNamespace(publish=published.append)
        node._initial_pose_status = {
            "state": "NOT_REQUESTED",
            "detail": "No initial pose sent from this panel",
        }
        node._initial_pose_requested_monotonic = None
        node._last_map_transform_stamp = (1, 0)
        node.initial_pose_settle_timeout_sec = 10.0
        node._audit_sink = None
        node.get_clock = lambda: SimpleNamespace(
            now=lambda: SimpleNamespace(to_msg=lambda: Time(sec=9, nanosec=0))
        )

        response = node._set_initial_pose(
            {"x": 1.5, "y": -0.5, "yaw": pi / 2.0, "confirmed": True}
        )

        self.assertEqual(response["initial_pose"]["state"], "PENDING")
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0].header.frame_id, "map")
        self.assertAlmostEqual(published[0].pose.pose.position.x, 1.5)
        self.assertAlmostEqual(published[0].pose.pose.orientation.z, 2 ** -0.5)

    def test_map_navigation_goal_is_serialized_with_selected_coordinates(self):
        handle = FakeGoalHandle()
        sent = Future()
        sent.set_result(handle)
        goals = []
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        node._manipulation_state = {"state": "EMPTY"}
        node._map_pose = {"available": True, "fresh": True}
        node._nav_goal_status = {"available": True, "active": False}
        node._initial_pose_status = {"state": "NOT_REQUESTED", "detail": ""}
        node._initial_pose_requested_monotonic = None
        node._presets = {}
        node._operations = {}
        node._operation_history = deque(maxlen=10)
        node.goal_admission_timeout_sec = 5.0
        node._audit_sink = None
        node.get_clock = lambda: SimpleNamespace(
            now=lambda: SimpleNamespace(to_msg=lambda: Time(sec=9, nanosec=0))
        )
        node._action_clients = {
            "navigate": SimpleNamespace(
                send_goal_async=lambda goal, feedback_callback: goals.append(goal) or sent
            )
        }

        operation = node._submit_navigation(
            {"confirmed": True, "goal": {"x": 2.0, "y": -1.0, "yaw": pi / 2.0}}
        )

        self.assertIsNone(operation.preset_id)
        self.assertEqual(operation.target_pose, {"x": 2.0, "y": -1.0, "yaw": pi / 2.0})
        self.assertEqual(goals[0].pose.header.frame_id, "map")
        self.assertAlmostEqual(goals[0].pose.pose.position.x, 2.0)
        self.assertAlmostEqual(goals[0].pose.pose.orientation.z, 2 ** -0.5)

    def test_navigation_without_status_requires_operator_idle_confirmation(self):
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        node._manipulation_state = {"state": "EMPTY"}
        node._map_pose = {"available": True, "fresh": True}
        node._nav_goal_status = {"available": False, "active": None}
        node._initial_pose_status = {"state": "NOT_REQUESTED", "detail": ""}
        node._initial_pose_requested_monotonic = None
        node._action_clients = {
            "navigate": SimpleNamespace(server_is_ready=lambda: True)
        }

        with self.assertRaisesRegex(PanelCommandError, "verify Nav2 is idle"):
            node._submit_navigation(
                {"confirmed": True, "goal": {"x": 2.0, "y": -1.0, "yaw": 0.0}}
            )

    def test_scan_points_are_projected_into_map_frame(self):
        scan = SimpleNamespace(
            ranges=[1.0, inf, 2.0],
            range_min=0.2,
            range_max=5.0,
            angle_min=0.0,
            angle_increment=pi / 2.0,
        )
        transform = SimpleNamespace(
            transform=SimpleNamespace(
                translation=SimpleNamespace(x=3.0, y=4.0),
                rotation=SimpleNamespace(x=0.0, y=0.0, z=2 ** -0.5, w=2 ** -0.5),
            )
        )

        points = OperatorPanelNode._scan_points_in_map(scan, transform, 10)

        self.assertEqual(len(points), 2)
        self.assertAlmostEqual(points[0][0], 3.0)
        self.assertAlmostEqual(points[0][1], 5.0)
        self.assertAlmostEqual(points[1][0], 3.0)
        self.assertAlmostEqual(points[1][1], 2.0)

    def test_global_path_points_are_bounded_and_keep_the_destination(self):
        def pose(x, y):
            return SimpleNamespace(pose=SimpleNamespace(position=SimpleNamespace(x=x, y=y)))

        path = SimpleNamespace(poses=[pose(0.0, 0.0), pose(nan, 1.0), pose(2.0, 2.0)])

        points = OperatorPanelNode._bounded_path_points(path, 2)

        self.assertEqual(points, [[0.0, 0.0], [2.0, 2.0]])

    def test_initial_pose_settles_only_after_matching_post_publish_transform(self):
        node = object.__new__(OperatorPanelNode)
        node._initial_pose_status = {
            "state": "PENDING",
            "x": 1.0,
            "y": 2.0,
            "yaw": 0.0,
        }
        node._initial_pose_request_stamp_ns = 1_000
        node.initial_pose_position_tolerance_m = 0.5
        node.initial_pose_yaw_tolerance_rad = 0.35

        node._update_initial_pose_settling_locked(1_000, 1.0, 2.0, 0.0)
        self.assertEqual(node._initial_pose_status["state"], "PENDING")
        node._update_initial_pose_settling_locked(1_001, 1.7, 2.0, 0.0)
        self.assertEqual(node._initial_pose_status["state"], "PENDING")
        node._update_initial_pose_settling_locked(1_002, 1.2, 2.1, 0.1)
        self.assertEqual(node._initial_pose_status["state"], "SETTLED")

    def test_nav2_status_expires_and_requires_the_idle_fallback(self):
        node = object.__new__(OperatorPanelNode)
        node._nav_goal_status = {"available": True, "active": False, "detail": "Nav2 is idle"}
        node._nav_goal_status_received_monotonic = 100.0
        node.nav_goal_status_freshness_sec = 3.0

        with patch("x2_operator_panel.ros_gateway.time.monotonic", return_value=103.1):
            status = node._nav_goal_status_locked()

        self.assertFalse(status["available"])
        self.assertIsNone(status["active"])
        self.assertIn("not updated", status["detail"])

    def test_stale_global_path_is_removed_from_the_map_snapshot(self):
        node = object.__new__(OperatorPanelNode)
        node._global_path = {
            "available": True,
            "fresh": True,
            "points": [[0.0, 0.0], [1.0, 1.0]],
            "point_count": 2,
            "detail": "",
        }
        node._global_path_received_monotonic = 100.0
        node.global_path_freshness_sec = 3.0
        node.global_path_topic = "/plan"

        with patch("x2_operator_panel.ros_gateway.time.monotonic", return_value=103.1):
            path = node._global_path_in_map_locked()

        self.assertFalse(path["fresh"])
        self.assertEqual(path["points"], [])
        self.assertEqual(path["point_count"], 0)

    def test_global_path_rejects_non_map_frame(self):
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        node.global_path_topic = "/plan"
        node.global_path_max_points = 500
        node._global_path_received_monotonic = None
        path = SimpleNamespace(header=SimpleNamespace(frame_id="odom"), poses=[])

        node._on_global_path(path)

        self.assertFalse(node._global_path["available"])
        self.assertIn("'odom'", node._global_path["detail"])

    def test_lifecycle_probe_retries_after_an_unfulfilled_request(self):
        pending = Future()
        calls = []
        client = SimpleNamespace(
            service_is_ready=lambda: True,
            call_async=lambda _request: calls.append(True) or pending,
        )
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        node._nav_lifecycle_clients = {"planner_server": client}
        node._nav_lifecycle_status = {"planner_server": {}}
        node._nav_lifecycle_requests = {}
        node.service_timeout_sec = 5.0

        with patch("x2_operator_panel.ros_gateway.time.monotonic", side_effect=[100.0, 105.1]):
            node._poll_navigation_lifecycle()
            node._poll_navigation_lifecycle()

        self.assertEqual(len(calls), 2)
        self.assertIn("retrying", node._nav_lifecycle_status["planner_server"]["detail"])

    def test_recovery_operation_is_reported_as_non_cancelable(self):
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        operation = Operation("recover", "recover_state", time.time(), cancelable=False)
        node._operations = {operation.identifier: operation}

        result = node._cancel_active()

        self.assertEqual(result["operation_ids"], [])
        self.assertEqual(result["non_cancelable_operation_ids"], ["recover"])
        self.assertEqual(operation.status, "SUBMITTING")

    def test_cancel_dispatch_failure_does_not_claim_cancellation(self):
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        node._audit_sink = None
        handle = FakeGoalHandle(fail=True)
        operation = Operation("active", "pick", time.time(), status="ACTIVE")
        operation.goal_handle = handle
        node._operations = {operation.identifier: operation}

        node._cancel_active()

        self.assertEqual(handle.cancel_calls, 1)
        self.assertEqual(operation.status, "ACTIVE")
        self.assertIn("transport failed", operation.detail)

    def test_admission_timeout_requests_late_goal_cancellation(self):
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        node._audit_sink = None
        operation = Operation(
            "pending",
            "navigate",
            time.time(),
            admission_deadline=time.monotonic() - 1.0,
        )
        node._operations = {operation.identifier: operation}

        node._expire_pending_operations()

        self.assertEqual(operation.status, "CANCEL_REQUESTED")
        self.assertIn("unknown", operation.detail)

    def test_late_goal_handle_is_canceled_after_admission_timeout(self):
        node = object.__new__(OperatorPanelNode)
        node._lock = threading.RLock()
        node._audit_sink = None
        operation = Operation(
            "pending", "navigate", time.time(), status="CANCEL_REQUESTED"
        )
        node._operations = {operation.identifier: operation}
        handle = FakeGoalHandle()
        response = Future()
        response.set_result(handle)

        node._on_goal_response(operation.identifier, response)

        self.assertEqual(handle.cancel_calls, 1)
        self.assertEqual(operation.status, "CANCEL_REQUESTED")
        self.assertEqual(operation.goal_uuid, bytes(range(16)).hex())


if __name__ == "__main__":
    unittest.main()
