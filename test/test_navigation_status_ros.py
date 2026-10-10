"""Exercise the gateway callbacks with fake action servers; no robot motion."""

import threading
import time
import unittest
from unittest.mock import Mock

import rclpy
from agibot_x2_manipulation_msgs.action import Pick
from nav2_msgs.action import NavigateThroughPoses, NavigateToPose
from rclpy.action import ActionClient, ActionServer
from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from x2_navigation.action import FineAlign

from x2_operator_panel.navigation_status import ActionGoalStatus, NavigationGoalStatus
from x2_operator_panel.ros_gateway import OperatorPanelNode, PanelCommandError, _TASK_ACTION_NAMES


class StatusMonitor(Node):
    _refresh_navigation_servers_locked = OperatorPanelNode._refresh_navigation_servers_locked
    _navigation_status_callback = OperatorPanelNode._navigation_status_callback
    _nav_goal_status_locked = OperatorPanelNode._nav_goal_status_locked
    _table_catalog = OperatorPanelNode._table_catalog
    _table_profile_id = OperatorPanelNode._table_profile_id
    _docking_catalog = OperatorPanelNode._docking_catalog
    _docking_profile_id = OperatorPanelNode._docking_profile_id
    _submit_fine_align = OperatorPanelNode._submit_fine_align
    _submit_manipulation = OperatorPanelNode._submit_manipulation
    _submit_navigation = OperatorPanelNode._submit_navigation
    _refresh_goal_servers_locked = OperatorPanelNode._refresh_goal_servers_locked
    _refresh_task_servers_locked = OperatorPanelNode._refresh_task_servers_locked
    _goal_status_callback = OperatorPanelNode._goal_status_callback
    _task_admission_blocker_locked = OperatorPanelNode._task_admission_blocker_locked
    _assert_task_idle_locked = OperatorPanelNode._assert_task_idle_locked
    _assert_task_idle = OperatorPanelNode._assert_task_idle
    _optional_boolean = staticmethod(OperatorPanelNode._optional_boolean)
    _parse_map_target = staticmethod(OperatorPanelNode._parse_map_target)
    _pose_from_values = staticmethod(OperatorPanelNode._pose_from_values)

    def __init__(self, context, name):
        super().__init__(name, context=context)
        self._lock = threading.RLock()
        self._manipulation_state = {"state": "EMPTY"}
        self._manipulation_task = {"status": "idle"}
        self._operations = {}
        self._task_goal_status = ActionGoalStatus(tuple(_TASK_ACTION_NAMES.values()))
        self._task_status_clients = {
            "pick_box": ActionClient(self, Pick, "/pick_box"),
            "fine_align": ActionClient(self, FineAlign, "/fine_align"),
        }
        self._task_status_subscriptions = {}
        self._navigation_goal_status = NavigationGoalStatus()
        self._navigation_status_subscriptions = {}
        self._navigation_status_qos = QoSProfile(
            depth=1, reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._navigation_status_clients = {
            "navigate_to_pose": ActionClient(self, NavigateToPose, "/navigate_to_pose"),
            "navigate_through_poses": ActionClient(
                self, NavigateThroughPoses, "/navigate_through_poses"
            ),
        }


class NavigationStatusRosTest(unittest.TestCase):
    def test_fake_servers_silence_retained_status_and_restart(self):
        context = Context()
        # Keep fake action servers separate from the robot's ROS graph.
        rclpy.init(context=context, domain_id=193)
        executor = SingleThreadedExecutor(context=context)
        server_node = Node("fake_navigation_servers", context=context)
        monitor = StatusMonitor(context, "navigation_status_monitor")
        executor.add_node(server_node)
        executor.add_node(monitor)
        servers = []
        late_monitor = None

        completed_goals = []

        def finish_goal(handle):
            completed_goals.append(handle)
            handle.succeed()
            return NavigateToPose.Result()

        def finish_secondary_goal(handle):
            handle.succeed()
            return NavigateThroughPoses.Result()

        def spin_until(predicate, timeout=5.0):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                executor.spin_once(timeout_sec=0.02)
                with monitor._lock:
                    monitor._nav_goal_status_locked()
                    monitor._refresh_task_servers_locked()
                if predicate():
                    return
            raise AssertionError("Timed out waiting for fake navigation state")

        def state():
            with monitor._lock:
                return monitor._nav_goal_status_locked()

        def hold_state(active):
            deadline = time.monotonic() + 3.2
            while time.monotonic() < deadline:
                executor.spin_once(timeout_sec=0.02)
                assert state()["available"] is True
                assert state()["active"] is active

        try:
            primary = ActionServer(server_node, NavigateToPose, "/navigate_to_pose", finish_goal)
            servers.append(primary)
            spin_until(lambda: state()["actions"]["navigate_to_pose"]["connected"])
            assert state()["available"] is False  # Readiness does not imply Idle.
            accepted = []
            secondary = ActionServer(
                server_node, NavigateThroughPoses, "/navigate_through_poses",
                finish_secondary_goal,
                handle_accepted_callback=accepted.append,
            )
            servers.append(secondary)
            spin_until(lambda: state()["actions"]["navigate_through_poses"]["connected"])
            sent = monitor._navigation_status_clients["navigate_to_pose"].send_goal_async(
                NavigateToPose.Goal()
            )
            spin_until(lambda: sent.done() and state()["active"] is False)
            hold_state(False)

            # Both servers exist, but only the primary has received a goal.
            # Exercise real navigation admission without an idle override; mock
            # operation bookkeeping while the fake server owns goal status.
            assert state()["actions"]["navigate_through_poses"]["available"] is False
            monitor._map_pose = {"available": True, "fresh": True}
            monitor._initial_pose_status_locked = Mock(return_value={"state": "NOT_REQUESTED"})
            monitor._action_clients = {
                "navigate": monitor._navigation_status_clients["navigate_to_pose"],
            }
            monitor.goal_admission_timeout_sec = 5.0
            monitor._register_operation = Mock()
            monitor._on_goal_response = Mock()
            monitor._audit = Mock()
            operation = monitor._submit_navigation({
                "confirmed": True, "goal": {"x": 1.0, "y": 0.0, "yaw": 0.0},
            })
            assert operation.kind == "navigate"
            spin_until(lambda: len(completed_goals) == 2 and state()["active"] is False)
            monitor._audit.assert_called_with(
                "navigate", "submitted", "map target; Nav2 action status reports idle")

            # A new subscription must load the server's retained terminal status.
            late_monitor = StatusMonitor(context, "late_navigation_status_monitor")
            executor.add_node(late_monitor)
            spin_until(lambda: late_monitor._nav_goal_status_locked()["active"] is False)

            # Tasks accepted outside the panel must block both navigation and
            # manipulation through the same admission check.
            for name, action_type, new_task in (
                ("pick_box", Pick, lambda: monitor._submit_navigation({})),
                ("fine_align", FineAlign, lambda: monitor._submit_manipulation("place", {})),
            ):
                handles = []

                def finish_task(handle):
                    handle.succeed()
                    return action_type.Result()

                task_server = ActionServer(
                    server_node, action_type, f"/{name}", finish_task,
                    handle_accepted_callback=handles.append,
                )
                servers.append(task_server)
                spin_until(lambda: monitor._task_status_clients[name].server_is_ready())
                monitor._task_status_clients[name].send_goal_async(action_type.Goal())
                spin_until(lambda: monitor._task_goal_status.snapshot(time.monotonic())[name]["active"] is True)
                with self.assertRaisesRegex(PanelCommandError, f"active {name} task"):
                    new_task()
                handles[0].execute()
                spin_until(lambda: monitor._task_goal_status.snapshot(time.monotonic())[name]["active"] is False)
                monitor._assert_task_idle()
                task_server.destroy()
                servers.remove(task_server)

            assert state()["available"] is True
            assert state()["actions"]["navigate_through_poses"]["available"] is False
            monitor._navigation_status_clients["navigate_through_poses"].send_goal_async(
                NavigateThroughPoses.Goal()
            )
            spin_until(lambda: bool(accepted) and state()["active"] is True)
            hold_state(True)
            with self.assertRaisesRegex(PanelCommandError, "active navigation task"):
                monitor._submit_manipulation("place", {"plan_only": True, "confirm_nav2_idle": True})
            try:
                monitor._submit_fine_align({"confirm_nav2_idle": True})
            except PanelCommandError as error:
                assert "Nav2 must be idle" in str(error)
            else:
                raise AssertionError("Fine Align admitted while navigation was active")

            accepted[0].execute()
            spin_until(lambda: state()["actions"]["navigate_through_poses"]["active"] is False)
            assert state()["active"] is False
            monitor._assert_task_idle()

            secondary.destroy()
            servers.remove(secondary)
            spin_until(lambda: not state()["actions"]["navigate_through_poses"]["connected"])
            assert state()["available"] is True

            primary.destroy()
            servers.remove(primary)
            # Replace it immediately: the publisher GID detects the restart even
            # when service readiness never appears to go away.
            primary = ActionServer(server_node, NavigateToPose, "/navigate_to_pose", finish_goal)
            servers.append(primary)
            spin_until(lambda: not state()["actions"]["navigate_to_pose"]["available"])
            spin_until(lambda: state()["actions"]["navigate_to_pose"]["connected"])
            assert state()["actions"]["navigate_to_pose"]["available"] is False
            monitor._navigation_status_clients["navigate_to_pose"].send_goal_async(
                NavigateToPose.Goal()
            )
            spin_until(lambda: state()["actions"]["navigate_to_pose"]["active"] is False)
        finally:
            for server in servers:
                server.destroy()
            executor.shutdown()
            if late_monitor is not None:
                late_monitor.destroy_node()
            monitor.destroy_node()
            server_node.destroy_node()
            context.shutdown()
