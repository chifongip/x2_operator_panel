from collections import deque
from concurrent.futures import Future
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from x2_operator_panel.ros_gateway import Operation, PanelCommandError, _TASK_ACTION_NAMES
from .test_ros_gateway import FakeGoalHandle, _new_panel_node


ACTION_KINDS = ("navigate", *_TASK_ACTION_NAMES)


def admission_node():
    node = _new_panel_node()
    node._operation_history = deque(maxlen=10)
    node._audit_sink = None
    node._shutting_down = False
    node._execution_unlocked_until = time.monotonic() + 60.0
    node._action_clients = {
        kind: SimpleNamespace(server_is_ready=lambda: True, send_goal_async=Mock())
        for kind in ACTION_KINDS
    }
    return node


class TaskAdmissionTest(unittest.TestCase):
    def test_every_active_panel_task_blocks_every_new_action(self):
        for active_kind in ACTION_KINDS:
            for status in ("SUBMITTING", "ACTIVE", "CANCEL_REQUESTED"):
                for requested_kind in ACTION_KINDS:
                    with self.subTest(active=active_kind, status=status, requested=requested_kind):
                        node = admission_node()
                        node._operations["existing"] = Operation(
                            "existing", active_kind, time.time(), status=status
                        )
                        unlock = node._execution_unlocked_until
                        with self.assertRaisesRegex(PanelCommandError, "active operation"):
                            node._submit({"kind": requested_kind, "confirm_nav2_idle": True})
                        self.assertEqual(node._execution_unlocked_until, unlock)
                        for client in node._action_clients.values():
                            client.send_goal_async.assert_not_called()

    def test_external_navigation_blocks_all_actions_including_plan_only(self):
        for name in ("navigate_to_pose", "navigate_through_poses"):
            for requested_kind in ACTION_KINDS:
                with self.subTest(active=name, requested=requested_kind):
                    node = admission_node()
                    node._navigation_goal_status.observe_server(name, frozenset({b"nav"}), True)
                    node._navigation_goal_status.receive(name, b"nav", [2], 100.0)
                    with self.assertRaisesRegex(PanelCommandError, "active navigation task"):
                        node._submit({
                            "kind": requested_kind, "plan_only": True, "confirm_nav2_idle": True,
                        })

    def test_external_manipulation_task_blocks_all_actions_while_paused_or_retrying(self):
        for status in ("running", "retrying", "paused"):
            for requested_kind in ACTION_KINDS:
                with self.subTest(status=status, requested=requested_kind):
                    node = admission_node()
                    node._manipulation_task = {"status": status}
                    with self.assertRaisesRegex(PanelCommandError, "active manipulation task"):
                        node._submit({"kind": requested_kind, "confirm_nav2_idle": True})

    def test_external_action_status_blocks_every_new_action(self):
        for name in _TASK_ACTION_NAMES.values():
            for status in (1, 2, 3):
                for requested_kind in ACTION_KINDS:
                    with self.subTest(active=name, status=status, requested=requested_kind):
                        node = admission_node()
                        node._task_goal_status.observe_server(name, frozenset({b"task"}), True)
                        node._task_goal_status.receive(name, b"task", [status], 100.0)
                        with self.assertRaisesRegex(PanelCommandError, f"active {name} task"):
                            node._submit({"kind": requested_kind, "confirm_nav2_idle": True})

    def test_direct_action_and_service_entry_points_cannot_bypass_admission(self):
        node = admission_node()
        node._operations["existing"] = Operation("existing", "pick", time.time())
        payload = {"confirmed": True, "x": 0.0, "y": 0.0, "yaw": 0.0}
        commands = [
            lambda: node._submit_manipulation("place", {}),
            lambda: node._submit_navigation({}),
            lambda: node._submit_fine_align({}),
            lambda: node._submit_undock({}),
            lambda: node._set_initial_pose(payload),
            lambda: node._recover_state({}),
            lambda: node._reload_box_profiles({}),
            lambda: node._set_locomanipulation_posture({}),
            lambda: node._release_locomanipulation_posture({}),
            lambda: node._clear_costmaps({"confirmed": True}),
        ]
        for command in commands:
            with self.assertRaisesRegex(PanelCommandError, "active operation"):
                command()

    def test_concurrent_registrations_reserve_only_one_task_slot(self):
        node = admission_node()
        start = threading.Barrier(2)
        errors = []

        def register(identifier, kind):
            start.wait(timeout=2.0)
            try:
                node._register_operation(Operation(identifier, kind, time.time()))
            except PanelCommandError as error:
                errors.append(error)

        threads = [
            threading.Thread(target=register, args=("nav", "navigate")),
            threading.Thread(target=register, args=("pick", "pick")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2.0)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(node._operations), 1)
        self.assertEqual(len(errors), 1)

    def test_cancel_acceptance_does_not_release_slot_before_terminal_result(self):
        node = admission_node()
        operation = Operation("existing", "pick", time.time(), status="ACTIVE")
        operation.goal_handle = FakeGoalHandle()
        node._register_operation(operation)
        node._cancel_active()
        with self.assertRaises(PanelCommandError):
            node._register_operation(Operation("next", "navigate", time.time()))
        result = Future()
        result.set_result(SimpleNamespace(status=5, result=SimpleNamespace(success=False)))
        node._on_action_result("existing", result)
        node._register_operation(Operation("next", "navigate", time.time()))
        self.assertEqual(node._operations["next"].status, "SUBMITTING")

    def test_service_timeout_blocks_until_late_response_establishes_outcome(self):
        node = admission_node()
        operation = Operation(
            "existing", "recover_state", time.time(), cancelable=False,
            service_deadline=time.monotonic() - 1.0,
        )
        node._register_operation(operation)
        node._expire_pending_operations()
        self.assertEqual(operation.status, "OUTCOME_UNKNOWN")
        with self.assertRaisesRegex(PanelCommandError, "outcome to be established"):
            node._register_operation(Operation("next", "navigate", time.time()))
        result = Future()
        result.set_result(SimpleNamespace(success=True, message="Recovered"))
        node._on_recovery_result("existing", result)
        node._register_operation(Operation("next", "navigate", time.time()))

    def test_late_costmap_responses_resolve_unknown_outcome(self):
        node = admission_node()
        node._costmap_clear_clients = {"global": object(), "local": object()}
        operation = Operation(
            "existing", "clear_costmaps", time.time(), cancelable=False,
            service_deadline=time.monotonic() - 1.0,
        )
        node._register_operation(operation)
        node._expire_pending_operations()
        result = Future()
        result.set_result(object())
        node._on_costmap_clear_result("existing", "global", result)
        self.assertEqual(operation.status, "OUTCOME_UNKNOWN")
        node._on_costmap_clear_result("existing", "local", result)
        self.assertEqual(operation.status, "SUCCEEDED")
        node._register_operation(Operation("next", "navigate", time.time()))

    def test_action_result_error_does_not_claim_task_has_finished(self):
        node = admission_node()
        operation = Operation("existing", "pick", time.time(), status="ACTIVE")
        operation.goal_handle = FakeGoalHandle()
        node._register_operation(operation)
        result = Future()
        result.set_exception(RuntimeError("result transport failed"))
        node._on_action_result("existing", result)
        self.assertEqual(operation.status, "ACTIVE")
        self.assertIsNotNone(operation.goal_handle)
        with self.assertRaises(PanelCommandError):
            node._register_operation(Operation("next", "navigate", time.time()))
        operation.result_retry_deadline = time.monotonic() - 1.0
        operation.goal_handle.result_future.set_result(
            SimpleNamespace(status=4, result=SimpleNamespace(success=True))
        )
        node._expire_pending_operations()
        self.assertEqual(operation.status, "SUCCEEDED")
        node._register_operation(Operation("next", "navigate", time.time()))

    def test_goal_acceptance_error_does_not_release_task_slot(self):
        node = admission_node()
        operation = Operation("existing", "pick", time.time())
        node._register_operation(operation)
        response = Future()
        response.set_exception(RuntimeError("goal response transport failed"))
        node._on_goal_response("existing", response)
        self.assertEqual(operation.status, "OUTCOME_UNKNOWN")
        with self.assertRaises(PanelCommandError):
            node._register_operation(Operation("next", "navigate", time.time()))

    def test_cancel_is_sent_even_if_result_tracking_initially_fails(self):
        node = admission_node()
        operation = Operation("existing", "pick", time.time(), status="CANCEL_REQUESTED")
        node._register_operation(operation)
        handle = FakeGoalHandle()
        handle.get_result_async = Mock(side_effect=RuntimeError("result transport failed"))
        response = Future()
        response.set_result(handle)
        node._on_goal_response("existing", response)
        self.assertEqual(handle.cancel_calls, 1)
        self.assertEqual(operation.status, "CANCEL_REQUESTED")
        self.assertIsNotNone(operation.result_retry_deadline)
