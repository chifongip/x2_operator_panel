"""Exercise camera subscription churn under traffic without robot interfaces."""

import threading
import time
import unittest

import rclpy
from rclpy.context import Context
from rclpy.node import Node
from sensor_msgs.msg import Image

from x2_operator_panel.panel_server import SingleThreadedExecutor
from x2_operator_panel.ros_gateway import OperatorPanelNode, _display_telemetry_qos


class CameraMonitor(Node):
    _sync_camera_subscriptions = OperatorPanelNode._sync_camera_subscriptions
    _camera_requested_locked = OperatorPanelNode._camera_requested_locked

    def __init__(self, context):
        super().__init__("camera_lifecycle_monitor", context=context)
        self._lock = threading.RLock()
        self._camera_requested_monotonic = {}
        self._camera_subscriptions = {}
        self._camera_frames = {}
        self._camera_last_encoded_monotonic = {}
        self.camera_display_rate_hz = 1.0
        self.front_center_camera_topic = "/test/front_camera"
        self.throttled_camera_topic = "/test/throttled_camera"
        self.received = 0
        self.transitions = 0
        self.create_timer(0.02, self.toggle_demand)

    def toggle_demand(self):
        # Expire demand immediately so many destroy/recreate cycles fit in a
        # short test, exercising the production timer's subscription lifecycle.
        self._camera_requested_monotonic["front_center"] = (
            time.monotonic() if self.transitions % 2 == 0 else 0.0
        )
        self._sync_camera_subscriptions()
        self.transitions += 1

    def _on_camera_image(self, name, message):
        self.received += 1


class SubscriptionLifecycleRosTest(unittest.TestCase):
    def test_camera_demand_churn_while_images_are_ready(self):
        context = Context()
        rclpy.init(context=context, domain_id=194)
        executor = SingleThreadedExecutor(context=context)
        monitor = CameraMonitor(context)
        source = Node("fake_camera_source", context=context)
        publisher = source.create_publisher(
            Image, monitor.front_center_camera_topic, _display_telemetry_qos()
        )
        source.create_timer(0.001, lambda: publisher.publish(Image()))
        executor.add_node(source)
        executor.add_node(monitor)
        try:
            deadline = time.monotonic() + 8.0
            while monitor.transitions < 100 and time.monotonic() < deadline:
                executor.spin_once(timeout_sec=0.02)
            self.assertGreaterEqual(monitor.transitions, 100)
            self.assertGreater(monitor.received, 5)
            self.assertEqual(monitor._camera_subscriptions, {})
        finally:
            executor.shutdown()
            monitor.destroy_node()
            source.destroy_node()
            context.shutdown()
