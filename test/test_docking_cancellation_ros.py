"""Cancel invisible-target docking through the panel and admit a new task."""

from concurrent.futures import ThreadPoolExecutor
import json
from math import isnan
import os
from pathlib import Path
import signal
import subprocess
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from agibot_x2_manipulation_msgs.msg import ManipulationState
from ament_index_python.packages import get_package_prefix
from geometry_msgs.msg import Twist
from lifecycle_msgs.srv import GetState
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.qos import DurabilityPolicy, QoSProfile

from x2_operator_panel.panel_server import _json_dumps
from x2_operator_panel.ros_gateway import OperatorPanelNode


class DockingCancellationRosTest(unittest.TestCase):
    def test_cancel_invisible_target_then_assign_another_task(self):
        executable = (Path(get_package_prefix("x2_navigation")) /
                      "lib/x2_navigation/fine_align_server")
        self.assertTrue(executable.is_file(), "Build x2_navigation before this integration test")
        domain = 207
        with TemporaryDirectory(prefix="x2-panel-docking-cancel-") as directory:
            environment = dict(os.environ, ROS_DOMAIN_ID=str(domain), ROS_LOCALHOST_ONLY="1",
                               ROS_LOG_DIR=directory)
            environment.pop("FASTRTPS_DEFAULT_PROFILES_FILE", None)
            with open(Path(directory) / "server.log", "w+") as log:
                server = subprocess.Popen([
                    str(executable), "--ros-args",
                    "-p", "acquisition_timeout:=0.5",
                    "-p", "retry_delay:=0.2",
                    "-p", "maximum_retries:=2",
                ], env=environment, stdout=log, stderr=subprocess.STDOUT)
                try:
                    with patch.dict(os.environ, {"ROS_LOCALHOST_ONLY": "1"}):
                        self.exercise_panel(domain)
                except Exception:
                    log.seek(0)
                    print(log.read())
                    raise
                finally:
                    server.send_signal(signal.SIGINT)
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=5)

    def exercise_panel(self, domain):
        rclpy.init(domain_id=domain)
        panel = None
        fixtures = None
        executor = SingleThreadedExecutor()
        with ThreadPoolExecutor(max_workers=1) as requests:
            try:
                panel = OperatorPanelNode()
                fixtures = rclpy.create_node("collision_monitor")
                executor.add_node(panel)
                executor.add_node(fixtures)

                def active_collision_monitor(request, response):
                    response.current_state.id = 3
                    response.current_state.label = "active"
                    return response

                lifecycle = fixtures.create_service(
                    GetState, "/collision_monitor/get_state", active_collision_monitor)
                states = fixtures.create_publisher(
                    ManipulationState, "/manipulation_state",
                    QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
                commands = []
                subscription = fixtures.create_subscription(Twist, "/cmd_vel_raw", commands.append, 10)

                def spin_until(predicate, timeout=10):
                    deadline = time.monotonic() + timeout
                    while not predicate() and time.monotonic() < deadline:
                        message = ManipulationState()
                        message.state = ManipulationState.EMPTY
                        states.publish(message)
                        executor.spin_once(timeout_sec=0.02)
                    self.assertTrue(predicate(), panel.snapshot())

                def request(name, payload):
                    response = requests.submit(panel.request, name, payload)
                    spin_until(response.done)
                    return response.result()

                spin_until(lambda: panel._action_clients["fine_align"].server_is_ready()
                           and panel._manipulation_state["state"] == "EMPTY"
                           and panel._nav_lifecycle_status.get("collision_monitor", {}).get("state_id") == 3)

                # No tag publisher exists. Exercise Stop sequence's shared cancel
                # endpoint while the physical docking action reacquires its tag.
                request("unlock_execution", {})
                submitted = request("submit", {
                    "kind": "fine_align", "execute": True, "confirmed": True,
                    "confirm_nav2_idle": True,
                })
                operation = panel._operations[submitted["operation"]["id"]]
                spin_until(lambda: operation.status == "ACTIVE" and operation.stage == "Reacquiring target")
                self.assertTrue(panel.snapshot()["task_admission"]["blocked"])
                canceled = request("cancel_active", {})
                self.assertIn(operation.identifier, canceled["operation_ids"])
                spin_until(lambda: operation.status == "CANCELED")
                spin_until(lambda: not panel.snapshot()["task_admission"]["blocked"])
                self.assertTrue(isnan(operation.result["final_error"]["x"]))
                status = json.loads(_json_dumps(panel.snapshot()),
                                    parse_constant=lambda value: self.fail(f"Invalid JSON: {value}"))
                stopped = next(item for item in status["operations"] if item["id"] == operation.identifier)
                self.assertEqual(stopped["status"], "CANCELED")
                self.assertEqual(stopped["result"]["final_error"], {"x": None, "y": None, "yaw": None})
                self.assertFalse(status["task_admission"]["blocked"])

                # Use the ordinary submission path again: stale cancellation must
                # not block admission. This second, plan-only action times out.
                submitted = request("submit", {
                    "kind": "fine_align", "execute": False, "confirm_nav2_idle": True,
                })
                following = panel._operations[submitted["operation"]["id"]]
                spin_until(lambda: following.status == "ABORTED")
                self.assertIn("no stable", following.detail)
                spin_until(lambda: not panel.snapshot()["task_admission"]["blocked"])
                json.loads(_json_dumps(panel.snapshot()),
                           parse_constant=lambda value: self.fail(f"Invalid JSON: {value}"))
                self.assertTrue(commands, "Observe the server's zero velocity output")
                self.assertTrue(all(command == Twist() for command in commands))
            finally:
                executor.shutdown()
                if fixtures is not None:
                    fixtures.destroy_node()
                if panel is not None:
                    panel.destroy_node()
                rclpy.shutdown()
