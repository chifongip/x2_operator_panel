"""Exercise profile discovery against an isolated ROS parameter service."""

import time
import unittest

import rclpy
from rcl_interfaces.srv import GetParameters
from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node

from x2_operator_panel.table_profiles import TableProfileMonitor


class TableProfilesRosTest(unittest.TestCase):
    def test_parameter_service_discovery_disconnect_and_restart(self):
        context = Context()
        rclpy.init(context=context, domain_id=197)
        executor = SingleThreadedExecutor(context=context)
        reader = Node("test_table_profile_reader", context=context)
        executor.add_node(reader)
        server = None
        client = reader.create_client(GetParameters, "/test_pick_place_server/get_parameters")
        monitor = TableProfileMonitor(client, 1.0)

        def start_server(default):
            node = Node("test_pick_place_server", context=context)
            from test.test_table_profiles import table_client
            parameters = table_client().parameters
            parameters["default_table_profile"] = default
            node.declare_parameters("", list(parameters.items()))
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
            self.assertEqual(monitor.snapshot()["profiles"][1]["place_offset"], [0.0, -0.1])
            executor.remove_node(server)
            server.destroy_node()
            server = None
            spin_until(lambda: not client.service_is_ready())
            self.assertFalse(monitor.snapshot()["available"])
            self.assertEqual(monitor.snapshot()["profiles"], [])
            server = start_server("second")
            spin_until(lambda: monitor.snapshot()["available"])
            self.assertEqual(monitor.snapshot()["default_profile"], "second")
        finally:
            if server is not None:
                executor.remove_node(server)
                server.destroy_node()
            executor.remove_node(reader)
            reader.destroy_node()
            executor.shutdown()
            context.shutdown()
