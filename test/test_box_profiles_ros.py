"""Exercise box-catalog discovery through a real isolated ROS service."""

import time
import unittest

from agibot_x2_manipulation_msgs.srv import GetBoxProfiles
import rclpy
from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node

from x2_operator_panel.box_profiles import BoxProfileMonitor


class BoxProfilesRosTest(unittest.TestCase):
    def test_discovers_unseen_profiles_reload_disconnect_and_restart(self):
        context = Context()
        rclpy.init(context=context, domain_id=198)
        executor = SingleThreadedExecutor(context=context)
        reader = Node("test_box_catalog_reader", context=context)
        executor.add_node(reader)
        server = None
        names, version, clock = ["box-a", "grey_box"], [0], [0.0]
        client = reader.create_client(GetBoxProfiles, "/test_get_box_profiles")
        monitor = BoxProfileMonitor(client, 1.0, clock=lambda: clock[0])

        def start_server():
            node = Node("test_box_catalog_server", context=context)

            def catalog(request, response):
                response.profile_ids = names
                response.profile_version = version[0]
                return response

            node.create_service(GetBoxProfiles, "/test_get_box_profiles", catalog)
            executor.add_node(node)
            return node

        def spin_until(predicate):
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                executor.spin_once(timeout_sec=0.02)
                monitor.poll()
                if predicate():
                    return
            self.fail(f"Box catalog discovery timed out: {monitor.snapshot()}")

        try:
            server = start_server()
            spin_until(lambda: monitor.snapshot()["available"])
            self.assertEqual(monitor.snapshot()["profiles"], [{"id": "box-a"}, {"id": "grey_box"}])
            names[:] = ["reloaded_box"]
            version[0], clock[0] = 1, 5.1
            spin_until(lambda: monitor.snapshot().get("profile_version") == 1)
            self.assertEqual(monitor.snapshot()["profiles"], [{"id": "reloaded_box"}])
            executor.remove_node(server)
            server.destroy_node()
            server = None
            spin_until(lambda: not client.service_is_ready())
            self.assertFalse(monitor.snapshot()["available"])
            names[:] = ["restarted_box"]
            version[0] = 0
            server = start_server()
            spin_until(lambda: monitor.snapshot()["available"])
            self.assertEqual(monitor.snapshot()["profiles"], [{"id": "restarted_box"}])
        finally:
            if server is not None:
                executor.remove_node(server)
                server.destroy_node()
            executor.remove_node(reader)
            reader.destroy_node()
            executor.shutdown()
            context.shutdown()
