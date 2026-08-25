from collections import deque
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from math import pi
from queue import Queue
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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
