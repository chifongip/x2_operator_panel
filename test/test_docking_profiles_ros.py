"""Exercise profile discovery against an isolated ROS parameter service."""

import time
import unittest

import rclpy
from rcl_interfaces.srv import GetParameters
from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node

from x2_operator_panel.docking_profiles import DockingProfileMonitor


class DockingProfilesRosTest(unittest.TestCase):
    def test_parameter_service_discovery_disconnect_and_restart(self):
        context = Context()
        rclpy.init(context=context, domain_id=194)
        executor = SingleThreadedExecutor(context=context)
        reader = Node("test_docking_profile_reader", context=context)
        executor.add_node(reader)
        server = None
        client = reader.create_client(GetParameters, "/test_fine_align_server/get_parameters")
        monitor = DockingProfileMonitor(client, 1.0)

        def start_server(default):
            node = Node("test_fine_align_server", context=context)
            node.declare_parameters("", [
                ("docking_profile_names", ["offset"]),
                ("default_docking_profile", default),
                ("tag_id", 9), ("tag_frame", "tag9"), ("standoff", 0.5),
                ("lateral_offset", 0.0), ("yaw_offset", 0.0),
                ("docking_profiles.offset.tag_id", 10),
                ("docking_profiles.offset.tag_frame", "tag10"),
                ("docking_profiles.offset.standoff", 0.7),
                ("docking_profiles.offset.lateral_offset", -0.1),
                ("docking_profiles.offset.yaw_offset", 0.2),
            ])
            executor.add_node(node)
            return node

        def spin_until(predicate):
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                executor.spin_once(timeout_sec=0.02)
                monitor.poll()
                if predicate():
                    return
            self.fail(f"Timed out reading profile service: {monitor.snapshot()}")

        try:
            server = start_server("default")
            spin_until(lambda: monitor.snapshot()["available"])
            self.assertEqual(monitor.snapshot()["default_profile"], "default")
            self.assertEqual(monitor.snapshot()["profiles"][1]["lateral_offset"], -0.1)
            executor.remove_node(server)
            server.destroy_node()
            server = None
            spin_until(lambda: not client.service_is_ready())
            self.assertFalse(monitor.snapshot()["available"])
            self.assertEqual(monitor.snapshot()["profiles"], [])
            server = start_server("offset")
            spin_until(lambda: monitor.snapshot()["available"])
            self.assertEqual(monitor.snapshot()["default_profile"], "offset")
        finally:
            if server is not None:
                executor.remove_node(server)
                server.destroy_node()
            executor.remove_node(reader)
            reader.destroy_node()
            executor.shutdown()
            context.shutdown()
