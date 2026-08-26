"""ROS interfaces behind the operator panel's HTTP and WebSocket boundary."""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from math import atan2, cos, hypot, isfinite, sin
from pathlib import Path
from queue import Empty, Queue
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from action_msgs.msg import GoalStatus, GoalStatusArray
from agibot_x2_manipulation_msgs.action import Pick, PickPlace, Place, ResetManipulation
from agibot_x2_manipulation_msgs.msg import ManipulationState
from agibot_x2_manipulation_msgs.srv import RecoverManipulationState
from ament_index_python.packages import get_package_share_directory
from diagnostic_msgs.msg import DiagnosticArray
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from lifecycle_msgs.srv import GetState
from moveit_msgs.action import MoveGroup
from moveit_msgs.srv import GetPlanningScene
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import Odometry, Path as NavPath
from rclpy.action import ActionClient
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.time import Time
from sensor_msgs.msg import JointState, LaserScan
from std_msgs.msg import Float32
from tf2_ros import Buffer, TransformException, TransformListener
import yaml


_ACTIVE_STATUSES = {"SUBMITTING", "ACTIVE", "CANCEL_REQUESTED"}
_GOAL_STATUS_NAMES = {
    GoalStatus.STATUS_UNKNOWN: "UNKNOWN",
    GoalStatus.STATUS_ACCEPTED: "ACCEPTED",
    GoalStatus.STATUS_EXECUTING: "EXECUTING",
    GoalStatus.STATUS_CANCELING: "CANCELING",
    GoalStatus.STATUS_SUCCEEDED: "SUCCEEDED",
    GoalStatus.STATUS_CANCELED: "CANCELED",
    GoalStatus.STATUS_ABORTED: "ABORTED",
}
_MANIPULATION_STATE_NAMES = {
    ManipulationState.UNKNOWN: "UNKNOWN",
    ManipulationState.EMPTY: "EMPTY",
    ManipulationState.HOLDING: "HOLDING",
    ManipulationState.RECOVERY_REQUIRED: "RECOVERY_REQUIRED",
}


class PanelCommandError(ValueError):
    """Expected invalid or unsafe API requests."""


@dataclass(frozen=True)
class NavigationPreset:
    identifier: str
    label: str
    x: float
    y: float
    yaw: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.identifier,
            "label": self.label,
            "pose": {"x": self.x, "y": self.y, "yaw": self.yaw},
        }


@dataclass
class Operation:
    identifier: str
    kind: str
    requested_at: float
    plan_only: bool | None = None
    preset_id: str | None = None
    target_pose: dict[str, float] | None = None
    status: str = "SUBMITTING"
    goal_uuid: str | None = None
    stage: str = "Submitting"
    progress: float | None = None
    feedback: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    detail: str = ""
    cancelable: bool = True
    admission_deadline: float | None = field(default=None, repr=False)
    service_deadline: float | None = field(default=None, repr=False)
    goal_handle: Any = field(default=None, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.identifier,
            "kind": self.kind,
            "requested_at": self.requested_at,
            "plan_only": self.plan_only,
            "preset_id": self.preset_id,
            "target_pose": self.target_pose,
            "status": self.status,
            "goal_uuid": self.goal_uuid,
            "stage": self.stage,
            "progress": self.progress,
            "feedback": self.feedback,
            "result": self.result,
            "detail": self.detail,
            "cancelable": self.cancelable,
        }


@dataclass
class QueuedCommand:
    name: str
    payload: dict[str, Any]
    response: Future[dict[str, Any]]


def load_navigation_presets(path: str | Path) -> list[NavigationPreset]:
    presets_path = Path(path)
    if not presets_path.is_file():
        raise ValueError(f"Navigation preset file does not exist: {presets_path}")
    try:
        document = yaml.safe_load(presets_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid navigation preset YAML: {error}") from error
    entries = document.get("presets")
    if not isinstance(entries, list):
        raise ValueError("Navigation preset YAML requires a presets list")

    presets: list[NavigationPreset] = []
    identifiers: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each navigation preset must be a mapping")
        identifier = entry.get("id")
        label = entry.get("label")
        if not isinstance(identifier, str) or not identifier.replace(
            "_", ""
        ).replace("-", "").isalnum():
            raise ValueError("Navigation preset id must contain only letters, digits, _ or -")
        if not isinstance(label, str) or not label.strip() or len(label) > 80:
            raise ValueError("Navigation preset label must be 1-80 characters")
        if identifier in identifiers:
            raise ValueError(f"Duplicate navigation preset id: {identifier}")
        identifiers.add(identifier)
        pose = entry.get("pose")
        if not isinstance(pose, dict):
            raise ValueError(f"Navigation preset {identifier} requires a pose")
        presets.append(
            NavigationPreset(
                identifier=identifier,
                label=label.strip(),
                x=_finite_number(pose.get("x"), f"preset {identifier} x"),
                y=_finite_number(pose.get("y"), f"preset {identifier} y"),
                yaw=_finite_number(pose.get("yaw"), f"preset {identifier} yaw"),
            )
        )
    return presets


def _finite_number(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise PanelCommandError(f"{field_name} must be numeric")
    converted = float(value)
    if not isfinite(converted):
        raise PanelCommandError(f"{field_name} must be finite")
    return converted


def _pose_as_dict(pose: PoseStamped) -> dict[str, Any]:
    orientation = pose.pose.orientation
    return {
        "frame_id": pose.header.frame_id,
        "x": pose.pose.position.x,
        "y": pose.pose.position.y,
        "z": pose.pose.position.z,
        "qx": orientation.x,
        "qy": orientation.y,
        "qz": orientation.z,
        "qw": orientation.w,
    }


class OperatorPanelNode(Node):
    """Single ROS owner for every browser-exposed robot interface."""

    def __init__(self) -> None:
        super().__init__("x2_operator_panel")
        navigation_share = Path(get_package_share_directory("x2_navigation"))
        package_share = Path(get_package_share_directory("x2_operator_panel"))
        self.map_yaml = self.declare_parameter(
            "map_yaml", str(navigation_share / "map" / "2026-08-18-Lab_voxel_0_05m.yaml")
        ).value
        self.presets_file = self.declare_parameter(
            "navigation_presets_file", str(package_share / "config" / "navigation_presets.yaml")
        ).value
        self.bind_address = self.declare_parameter("bind_address", "127.0.0.1").value
        self.allow_lan_access = bool(
            self.declare_parameter("allow_lan_access", False).value
        )
        self.lan_allowed_subnet = self.declare_parameter("lan_allowed_subnet", "").value
        self.tls_cert_file = self.declare_parameter("tls_cert_file", "").value
        self.tls_key_file = self.declare_parameter("tls_key_file", "").value
        self.http_port = int(self.declare_parameter("http_port", 8080).value)
        self.websocket_port = int(self.declare_parameter("websocket_port", 8081).value)
        self.websocket_url = self.declare_parameter("websocket_url", "").value
        self.allowed_origin = self.declare_parameter("allowed_origin", "").value
        self.session_ttl_sec = float(self.declare_parameter("session_ttl_sec", 1800.0).value)
        self.http_worker_limit = int(self.declare_parameter("http_worker_limit", 16).value)
        self.http_request_timeout_sec = float(
            self.declare_parameter("http_request_timeout_sec", 5.0).value
        )
        self.websocket_client_limit = int(
            self.declare_parameter("websocket_client_limit", 4).value
        )
        self.websocket_send_timeout_sec = float(
            self.declare_parameter("websocket_send_timeout_sec", 1.0).value
        )
        self.login_per_source_limit = int(
            self.declare_parameter("login_per_source_limit", 5).value
        )
        self.login_global_limit = int(
            self.declare_parameter("login_global_limit", 30).value
        )
        self.login_window_sec = float(
            self.declare_parameter("login_window_sec", 60.0).value
        )
        self.execution_unlock_sec = float(
            self.declare_parameter("execution_unlock_sec", 30.0).value
        )
        self.box_pose_freshness_sec = float(
            self.declare_parameter("box_pose_freshness_sec", 0.5).value
        )
        self.tf_freshness_sec = float(self.declare_parameter("tf_freshness_sec", 1.0).value)
        self.scan_topic = self.declare_parameter("scan_topic", "/scan_nav/laser").value
        self.scan_freshness_sec = float(
            self.declare_parameter("scan_freshness_sec", 1.0).value
        )
        self.scan_max_points = int(self.declare_parameter("scan_max_points", 360).value)
        self.global_path_topic = self.declare_parameter("global_path_topic", "/plan").value
        self.global_path_freshness_sec = float(
            self.declare_parameter("global_path_freshness_sec", 3.0).value
        )
        self.global_path_max_points = int(
            self.declare_parameter("global_path_max_points", 500).value
        )
        self.nav_goal_status_freshness_sec = float(
            self.declare_parameter("nav_goal_status_freshness_sec", 3.0).value
        )
        self.initial_pose_settle_timeout_sec = float(
            self.declare_parameter("initial_pose_settle_timeout_sec", 10.0).value
        )
        self.initial_pose_position_tolerance_m = float(
            self.declare_parameter("initial_pose_position_tolerance_m", 0.5).value
        )
        self.initial_pose_yaw_tolerance_rad = float(
            self.declare_parameter("initial_pose_yaw_tolerance_rad", 0.35).value
        )
        self.localization_confidence_topic = self.declare_parameter(
            "localization_confidence_topic", "/localization_3d_confidence"
        ).value
        self.localization_delay_topic = self.declare_parameter(
            "localization_delay_topic", "/localization_3d_delay_ms"
        ).value
        self.move_group_action_name = self.declare_parameter(
            "move_group_action_name", "/move_action"
        ).value
        self.planning_scene_service_name = self.declare_parameter(
            "planning_scene_service_name", "/get_planning_scene"
        ).value
        self.goal_admission_timeout_sec = float(
            self.declare_parameter("goal_admission_timeout_sec", 5.0).value
        )
        self.service_timeout_sec = float(
            self.declare_parameter("service_timeout_sec", 5.0).value
        )
        self.shutdown_cancel_grace_sec = float(
            self.declare_parameter("shutdown_cancel_grace_sec", 5.0).value
        )
        operation_history_limit = int(
            self.declare_parameter("operation_history_limit", 100).value
        )
        positive_values = {
            "http_worker_limit": self.http_worker_limit,
            "http_request_timeout_sec": self.http_request_timeout_sec,
            "websocket_client_limit": self.websocket_client_limit,
            "websocket_send_timeout_sec": self.websocket_send_timeout_sec,
            "login_per_source_limit": self.login_per_source_limit,
            "login_global_limit": self.login_global_limit,
            "login_window_sec": self.login_window_sec,
            "session_ttl_sec": self.session_ttl_sec,
            "execution_unlock_sec": self.execution_unlock_sec,
            "box_pose_freshness_sec": self.box_pose_freshness_sec,
            "tf_freshness_sec": self.tf_freshness_sec,
            "scan_freshness_sec": self.scan_freshness_sec,
            "scan_max_points": self.scan_max_points,
            "global_path_freshness_sec": self.global_path_freshness_sec,
            "global_path_max_points": self.global_path_max_points,
            "nav_goal_status_freshness_sec": self.nav_goal_status_freshness_sec,
            "initial_pose_settle_timeout_sec": self.initial_pose_settle_timeout_sec,
            "initial_pose_position_tolerance_m": self.initial_pose_position_tolerance_m,
            "initial_pose_yaw_tolerance_rad": self.initial_pose_yaw_tolerance_rad,
            "goal_admission_timeout_sec": self.goal_admission_timeout_sec,
            "service_timeout_sec": self.service_timeout_sec,
            "shutdown_cancel_grace_sec": self.shutdown_cancel_grace_sec,
            "operation_history_limit": operation_history_limit,
        }
        invalid = [name for name, value in positive_values.items() if value <= 0]
        if invalid:
            raise ValueError(f"Parameters must be positive: {', '.join(invalid)}")
        if not 1 <= self.http_port <= 65535 or not 1 <= self.websocket_port <= 65535:
            raise ValueError("HTTP and WebSocket ports must be between 1 and 65535")
        if self.http_port == self.websocket_port:
            raise ValueError("HTTP and WebSocket ports must be different")

        self._lock = threading.RLock()
        self._commands: Queue[QueuedCommand] = Queue()
        self._operations: dict[str, Operation] = {}
        self._operation_history: deque[str] = deque(maxlen=operation_history_limit)
        self._presets = {
            preset.identifier: preset
            for preset in load_navigation_presets(self.presets_file)
        }
        self._execution_unlocked_until = 0.0
        self._status_sink: Callable[[dict[str, Any]], None] | None = None
        self._audit_sink: Callable[[str, str, str], None] | None = None
        self._manipulation_state = {"state": "UNKNOWN", "detail": "No state received"}
        self._box_pose: dict[str, Any] | None = None
        self._box_pose_received_monotonic: float | None = None
        self._diagnostics: list[dict[str, Any]] = []
        self._map_pose: dict[str, Any] = {
            "available": False,
            "fresh": False,
            "detail": "Waiting for map -> base_link transform",
        }
        self._last_tf_error = ""
        self._last_map_transform_stamp: tuple[int, int] | None = None
        self._last_map_transform_update_monotonic: float | None = None
        self._initial_pose_status: dict[str, Any] = {
            "state": "NOT_REQUESTED",
            "detail": "No initial pose sent from this panel",
        }
        self._initial_pose_request_stamp_ns: int | None = None
        self._initial_pose_requested_monotonic: float | None = None
        self._scan: dict[str, Any] = {
            "available": False,
            "fresh": False,
            "detail": f"Waiting for {self.scan_topic}",
        }
        self._scan_received_monotonic: float | None = None
        self._global_path: dict[str, Any] = {
            "available": False,
            "fresh": False,
            "detail": f"Waiting for {self.global_path_topic}",
        }
        self._global_path_received_monotonic: float | None = None
        self._localization_metrics: dict[str, dict[str, Any]] = {
            "confidence": {
                "available": False,
                "detail": f"Waiting for {self.localization_confidence_topic}",
            },
            "delay_ms": {
                "available": False,
                "detail": f"Waiting for {self.localization_delay_topic}",
            },
        }
        self._odom_received_monotonic: float | None = None
        self._joint_states_received_monotonic: float | None = None
        self._nav_goal_status: dict[str, Any] = {
            "available": False,
            "active": None,
            "detail": "Waiting for Nav2 action status",
        }
        self._nav_goal_status_received_monotonic: float | None = None
        self._nav_lifecycle_status = {
            name: {
                "available": False,
                "state": "UNKNOWN",
                "detail": "Waiting for lifecycle service",
            }
            for name in (
                "map_server",
                "planner_server",
                "controller_server",
                "behavior_server",
                "bt_navigator",
            )
        }
        self._nav_lifecycle_requests: dict[str, float] = {}
        self._shutting_down = False

        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

        self._action_clients = {
            "pick": ActionClient(self, Pick, "/pick_box"),
            "place": ActionClient(self, Place, "/place_box"),
            "pick_place": ActionClient(self, PickPlace, "/pick_place"),
            "reset": ActionClient(self, ResetManipulation, "/reset_manipulation"),
            "navigate": ActionClient(self, NavigateToPose, "/navigate_to_pose"),
        }
        self._move_group_action_client = ActionClient(
            self, MoveGroup, self.move_group_action_name
        )
        self._planning_scene_client = self.create_client(
            GetPlanningScene, self.planning_scene_service_name
        )
        self._nav_lifecycle_clients = {
            name: self.create_client(GetState, f"/{name}/get_state")
            for name in self._nav_lifecycle_status
        }
        self._recovery_client = self.create_client(
            RecoverManipulationState, "/recover_manipulation_state"
        )
        self._initial_pose_publisher = self.create_publisher(
            PoseWithCovarianceStamped, "/initialpose", 10
        )
        state_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        scan_qos = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        self.create_subscription(
            ManipulationState, "/manipulation_state", self._on_manipulation_state, state_qos
        )
        self.create_subscription(
            PoseWithCovarianceStamped, "/box_pose", self._on_box_pose, 10
        )
        self.create_subscription(
            Odometry, "/odom", self._on_odom, 10
        )
        self.create_subscription(LaserScan, self.scan_topic, self._on_scan, scan_qos)
        self.create_subscription(NavPath, self.global_path_topic, self._on_global_path, 10)
        self.create_subscription(
            Float32,
            self.localization_confidence_topic,
            self._on_localization_confidence,
            10,
        )
        self.create_subscription(
            Float32, self.localization_delay_topic, self._on_localization_delay, 10
        )
        self.create_subscription(JointState, "/joint_states", self._on_joint_states, 10)
        self.create_subscription(
            GoalStatusArray,
            "/navigate_to_pose/_action/status",
            self._on_nav_goal_status,
            state_qos,
        )
        self.create_subscription(
            DiagnosticArray, "/pick_place/planning_diagnostics", self._on_diagnostics, 10
        )
        self.create_timer(0.05, self._drain_commands)
        self.create_timer(0.20, self._poll_map_pose)
        self.create_timer(0.20, self._expire_pending_operations)
        self.create_timer(1.0, self._poll_navigation_lifecycle)
        self.create_timer(0.25, self._publish_status)

    def set_status_sink(self, sink: Callable[[dict[str, Any]], None]) -> None:
        self._status_sink = sink

    def set_audit_sink(self, sink: Callable[[str, str, str], None]) -> None:
        self._audit_sink = sink

    def presets(self) -> list[dict[str, Any]]:
        return [preset.as_dict() for preset in self._presets.values()]

    def request(
        self, name: str, payload: dict[str, Any], timeout_sec: float = 5.0
    ) -> dict[str, Any]:
        response: Future[dict[str, Any]] = Future()
        self._commands.put(QueuedCommand(name, payload, response))
        try:
            return response.result(timeout=timeout_sec)
        except FutureTimeoutError:
            # Cancellation succeeds only while the command is still queued. Once
            # the ROS executor owns it, wait for the definitive dispatch outcome
            # instead of reporting a failure while motion may be submitted.
            if response.cancel():
                raise
            return response.result()

    def enable_execution_unlock(self) -> dict[str, Any]:
        return self.request("unlock_execution", {})

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            operations = [operation.as_dict() for operation in self._operations.values()]
            operations.sort(key=lambda item: item["requested_at"], reverse=True)
            return {
                "servers": {
                    name: client.server_is_ready()
                    for name, client in self._action_clients.items()
                },
                "recovery_service_ready": self._recovery_client.service_is_ready(),
                "manipulation_state": dict(self._manipulation_state),
                "box_pose": dict(self._box_pose) if self._box_pose is not None else None,
                "box_map_pose": self._box_pose_in_map_locked(),
                "map_pose": dict(self._map_pose),
                "initial_pose": self._initial_pose_status_locked(),
                "scan": self._scan_in_map_locked(),
                "localization_metrics": self._localization_metrics_locked(),
                "navigation": {
                    "lifecycle": {
                        name: dict(status)
                        for name, status in self._nav_lifecycle_status.items()
                    },
                    "goal_status": self._nav_goal_status_locked(),
                    "odom": self._freshness_status_locked(
                        self._odom_received_monotonic, "/odom"
                    ),
                    "global_path": self._global_path_in_map_locked(),
                },
                "moveit": {
                    "move_group_action_ready": self._move_group_action_client.server_is_ready(),
                    "planning_scene_service_ready": self._planning_scene_client.service_is_ready(),
                    "joint_states": self._freshness_status_locked(
                        self._joint_states_received_monotonic, "/joint_states"
                    ),
                },
                "diagnostics": list(self._diagnostics),
                "operations": operations,
                "execution_unlock_remaining_sec": max(
                    0.0, self._execution_unlocked_until - time.monotonic()
                ),
            }

    def _drain_commands(self) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except Empty:
                return
            if not command.response.set_running_or_notify_cancel():
                continue
            try:
                if self._shutting_down and command.name != "cancel_active":
                    raise PanelCommandError("The operator panel is shutting down")
                if command.name == "unlock_execution":
                    result = self._unlock_execution()
                elif command.name == "submit":
                    result = self._submit(command.payload)
                elif command.name == "cancel_active":
                    result = self._cancel_active()
                elif command.name == "recover_state":
                    result = self._recover_state(command.payload)
                elif command.name == "set_initial_pose":
                    result = self._set_initial_pose(command.payload)
                else:
                    raise PanelCommandError("Unknown panel command")
                command.response.set_result(result)
            except Exception as error:  # Surface a safe request failure to HTTP callers.
                command.response.set_exception(error)

    def _unlock_execution(self) -> dict[str, Any]:
        with self._lock:
            self._execution_unlocked_until = time.monotonic() + self.execution_unlock_sec
        self._audit("execution_unlock", "accepted", "Physical manipulation unlock granted")
        return {"unlocked_for_sec": self.execution_unlock_sec}

    def _set_initial_pose(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("confirmed") is not True:
            raise PanelCommandError("Setting the initial pose requires confirmation")
        x, y, yaw = self._parse_map_target(payload, "initial pose")
        with self._lock:
            nav_goal_status = self._nav_goal_status_locked()
            if self._active_operations():
                raise PanelCommandError(
                    "Wait for the active panel operation to reach a terminal state"
                )
            if not nav_goal_status.get("available"):
                if payload.get("confirm_nav2_idle") is not True:
                    raise PanelCommandError(
                        "Nav2 action status is unavailable; verify Nav2 is idle and confirm again"
                    )
                nav_status_detail = "operator-confirmed idle without action status"
            elif nav_goal_status.get("active") is not False:
                raise PanelCommandError("Nav2 must be idle before setting the initial pose")
            else:
                nav_status_detail = "Nav2 action status reports idle"

            if not self._action_clients["navigate"].server_is_ready():
                raise PanelCommandError(
                    "NavigateToPose action server is unavailable before setting pose"
                )

            pose = PoseWithCovarianceStamped()
            pose.header.frame_id = "map"
            pose.header.stamp = self.get_clock().now().to_msg()
            pose.pose.pose.position.x = x
            pose.pose.pose.position.y = y
            pose.pose.pose.orientation.z = sin(yaw / 2.0)
            pose.pose.pose.orientation.w = cos(yaw / 2.0)
            pose.pose.covariance[0] = 0.25
            pose.pose.covariance[7] = 0.25
            pose.pose.covariance[35] = 0.06853891945200942
            self._initial_pose_publisher.publish(pose)
            self._initial_pose_status = {
                "state": "PENDING",
                "detail": "Waiting for a newer map -> base_link transform",
                "x": x,
                "y": y,
                "yaw": yaw,
            }
            self._initial_pose_request_stamp_ns = self._stamp_nanoseconds(pose.header.stamp)
            self._initial_pose_requested_monotonic = time.monotonic()
        self._audit(
            "set_initial_pose",
            "submitted",
            f"x={x:.2f}, y={y:.2f}, yaw={yaw:.2f}; {nav_status_detail}",
        )
        return {"initial_pose": self._initial_pose_status_locked()}

    def _submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._shutting_down:
            raise PanelCommandError("The operator panel is shutting down")
        kind = payload.get("kind")
        if kind not in self._action_clients:
            raise PanelCommandError("Unsupported action")
        if not self._action_clients[kind].server_is_ready():
            raise PanelCommandError(f"The {kind} action server is unavailable")
        if self._active_operations():
            raise PanelCommandError("Wait for the active operation to reach a terminal state")
        if kind == "navigate":
            operation = self._submit_navigation(payload)
        else:
            operation = self._submit_manipulation(kind, payload)
        return {"operation": operation.as_dict()}

    def _submit_manipulation(self, kind: str, payload: dict[str, Any]) -> Operation:
        plan_only = self._optional_boolean(payload, "plan_only", True) if kind != "reset" else None
        if kind == "reset" and payload.get("confirm_empty") is not True:
            raise PanelCommandError("Reset requires confirmation that no box is held")
        requires_execution = kind == "reset" or plan_only is False
        goal = self._build_manipulation_goal(kind, payload, plan_only)
        if requires_execution:
            if payload.get("confirmed") is not True:
                raise PanelCommandError("Physical manipulation requires per-command confirmation")
            if time.monotonic() >= self._execution_unlocked_until:
                raise PanelCommandError("Physical manipulation unlock has expired")
            self._execution_unlocked_until = 0.0

        operation = Operation(
            identifier=str(uuid4()),
            kind=kind,
            requested_at=time.time(),
            plan_only=plan_only,
            admission_deadline=time.monotonic() + self.goal_admission_timeout_sec,
        )
        self._register_operation(operation)
        try:
            future = self._action_clients[kind].send_goal_async(
                goal,
                feedback_callback=lambda message: self._on_feedback(
                    operation.identifier, message
                ),
            )
        except Exception as error:
            self._finish_operation(operation.identifier, "ERROR", {"message": str(error)})
            raise PanelCommandError(f"Failed to submit {kind} goal: {error}") from error
        future.add_done_callback(
            lambda sent: self._on_goal_response(operation.identifier, sent)
        )
        self._audit(kind, "submitted", "plan_only" if plan_only else "execution")
        return operation

    def _submit_navigation(self, payload: dict[str, Any]) -> Operation:
        if payload.get("confirmed") is not True:
            raise PanelCommandError("Navigation requires confirmation")
        with self._lock:
            manipulation_state = self._manipulation_state["state"]
            map_pose = dict(self._map_pose)
            nav_goal_status = self._nav_goal_status_locked()
            initial_pose_status = self._initial_pose_status_locked()
        if manipulation_state not in {"EMPTY", "HOLDING"}:
            raise PanelCommandError(
                "Navigation requires a known EMPTY or HOLDING manipulation state"
            )
        if not map_pose.get("available") or not map_pose.get("fresh"):
            raise PanelCommandError("Navigation requires a fresh map -> base_link transform")
        if not nav_goal_status.get("available"):
            if not self._action_clients["navigate"].server_is_ready():
                raise PanelCommandError("The navigate action server is unavailable")
            if payload.get("confirm_nav2_idle") is not True:
                raise PanelCommandError(
                    "Nav2 action status is unavailable; verify Nav2 is idle and confirm again"
                )
            nav_status_detail = "operator-confirmed idle without action status"
        elif nav_goal_status.get("active") is not False:
            raise PanelCommandError("Nav2 already has an active navigation goal")
        else:
            nav_status_detail = "Nav2 action status reports idle"
        if initial_pose_status["state"] == "PENDING":
            raise PanelCommandError("Waiting for the requested initial pose to settle")
        if initial_pose_status["state"] == "TIMEOUT":
            raise PanelCommandError(
                "Initial-pose update timed out; verify localization before navigation"
            )
        preset_id = payload.get("preset_id")
        map_goal = payload.get("goal")
        if preset_id is not None and map_goal is not None:
            raise PanelCommandError("Choose either a navigation preset or a map goal")
        preset = self._presets.get(preset_id) if preset_id is not None else None
        if preset is not None:
            x, y, yaw = preset.x, preset.y, preset.yaw
            target_label = preset.identifier
        elif map_goal is not None:
            x, y, yaw = self._parse_map_target(map_goal, "navigation goal")
            target_label = "map target"
        else:
            raise PanelCommandError("Choose a navigation preset or map goal")
        goal = NavigateToPose.Goal()
        goal.pose = self._pose_from_values("map", x, y, 0.0, yaw)
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        operation = Operation(
            identifier=str(uuid4()),
            kind="navigate",
            requested_at=time.time(),
            preset_id=preset.identifier if preset is not None else None,
            target_pose={"x": x, "y": y, "yaw": yaw},
            admission_deadline=time.monotonic() + self.goal_admission_timeout_sec,
        )
        self._register_operation(operation)
        try:
            future = self._action_clients["navigate"].send_goal_async(
                goal,
                feedback_callback=lambda message: self._on_feedback(
                    operation.identifier, message
                ),
            )
        except Exception as error:
            self._finish_operation(operation.identifier, "ERROR", {"message": str(error)})
            raise PanelCommandError(f"Failed to submit navigation goal: {error}") from error
        future.add_done_callback(
            lambda sent: self._on_goal_response(operation.identifier, sent)
        )
        self._audit("navigate", "submitted", f"{target_label}; {nav_status_detail}")
        return operation

    @staticmethod
    def _parse_map_target(value: object, field_name: str) -> tuple[float, float, float]:
        if not isinstance(value, dict):
            raise PanelCommandError(f"{field_name} must be an object")
        return (
            _finite_number(value.get("x"), f"{field_name} x"),
            _finite_number(value.get("y"), f"{field_name} y"),
            _finite_number(value.get("yaw"), f"{field_name} yaw"),
        )

    def _build_manipulation_goal(
        self, kind: str, payload: dict[str, Any], plan_only: bool | None
    ) -> Any:
        if kind == "pick":
            goal = Pick.Goal()
            goal.plan_only = bool(plan_only)
            return goal
        if kind in {"place", "pick_place"}:
            goal = Place.Goal() if kind == "place" else PickPlace.Goal()
            if "place_pose" in payload:
                goal.place_pose = self._parse_place_pose(payload["place_pose"])
            # A default-constructed pose tells pick_place_server to use tag9.
            goal.plan_only = bool(plan_only)
            return goal
        if kind == "reset":
            goal = ResetManipulation.Goal()
            goal.confirm_empty = True
            return goal
        raise PanelCommandError("Unsupported manipulation action")

    def _parse_place_pose(self, value: object) -> PoseStamped:
        if not isinstance(value, dict):
            raise PanelCommandError("Manual place pose must be an object")
        frame_id = value.get("frame_id")
        if frame_id not in {"base_link", "map"}:
            raise PanelCommandError("Manual place frame must be base_link or map")
        return self._pose_from_values(
            frame_id,
            _finite_number(value.get("x"), "manual place x"),
            _finite_number(value.get("y"), "manual place y"),
            _finite_number(value.get("z"), "manual place z"),
            _finite_number(value.get("yaw"), "manual place yaw"),
        )

    @staticmethod
    def _pose_from_values(frame_id: str, x: float, y: float, z: float, yaw: float) -> PoseStamped:
        pose = PoseStamped()
        pose.header.frame_id = frame_id
        pose.pose.position.x = x
        pose.pose.position.y = y
        pose.pose.position.z = z
        pose.pose.orientation.z = sin(yaw / 2.0)
        pose.pose.orientation.w = cos(yaw / 2.0)
        return pose

    def _register_operation(self, operation: Operation) -> None:
        with self._lock:
            if len(self._operation_history) == self._operation_history.maxlen:
                oldest = self._operation_history.popleft()
                self._operations.pop(oldest, None)
            self._operations[operation.identifier] = operation
            self._operation_history.append(operation.identifier)

    def _on_goal_response(self, operation_id: str, sent: Any) -> None:
        try:
            handle = sent.result()
        except Exception as error:
            self._finish_operation(operation_id, "ERROR", {"message": str(error)})
            return
        with self._lock:
            operation = self._operations.get(operation_id)
            if operation is None:
                return
            operation.admission_deadline = None
            if not handle.accepted:
                operation.status = "REJECTED"
                operation.stage = "Rejected"
                operation.detail = "The ROS action server rejected this goal"
                operation.result = {"message": operation.detail}
                self._audit(operation.kind, "rejected", operation.detail)
                return
            cancel_requested = operation.status == "CANCEL_REQUESTED"
            operation.goal_handle = handle
            operation.goal_uuid = bytes(handle.goal_id.uuid).hex()
            operation.status = "CANCEL_REQUESTED" if cancel_requested else "ACTIVE"
            operation.stage = "Cancellation requested" if cancel_requested else "Executing"
        try:
            result_future = handle.get_result_async()
        except Exception as error:
            self._restore_active_after_cancel_failure(
                operation_id, f"Could not track action result: {error}"
            )
            return
        result_future.add_done_callback(
            lambda result: self._on_action_result(operation_id, result)
        )
        if cancel_requested:
            self._request_goal_cancel(operation_id, handle)

    def _on_feedback(self, operation_id: str, message: Any) -> None:
        feedback = message.feedback
        details: dict[str, Any] = {}
        if hasattr(feedback, "stage"):
            details["stage"] = feedback.stage
        if hasattr(feedback, "progress"):
            details["progress"] = float(feedback.progress)
        if hasattr(feedback, "box_pose"):
            details["box_pose"] = _pose_as_dict(feedback.box_pose)
        if hasattr(feedback, "distance_remaining"):
            details["distance_remaining"] = float(feedback.distance_remaining)
        if hasattr(feedback, "number_of_recoveries"):
            details["number_of_recoveries"] = int(feedback.number_of_recoveries)
        if hasattr(feedback, "current_pose"):
            details["current_pose"] = _pose_as_dict(feedback.current_pose)
        with self._lock:
            operation = self._operations.get(operation_id)
            if operation is None:
                return
            operation.feedback = details
            operation.stage = str(details.get("stage", "Executing"))
            operation.progress = details.get("progress")

    def _on_action_result(self, operation_id: str, completed: Any) -> None:
        try:
            wrapped = completed.result()
            result = self._result_as_dict(wrapped.result)
            status = _GOAL_STATUS_NAMES.get(wrapped.status, f"STATUS_{wrapped.status}")
        except Exception as error:
            self._finish_operation(operation_id, "ERROR", {"message": str(error)})
            return
        self._finish_operation(operation_id, status, result)

    @staticmethod
    def _result_as_dict(result: Any) -> dict[str, Any]:
        details: dict[str, Any] = {}
        for attribute in ("success", "error_code", "message", "object_held", "error_msg"):
            if hasattr(result, attribute):
                details[attribute] = getattr(result, attribute)
        if hasattr(result, "achieved_pose"):
            details["achieved_pose"] = _pose_as_dict(result.achieved_pose)
        return details

    def _finish_operation(self, operation_id: str, status: str, result: dict[str, Any]) -> None:
        with self._lock:
            operation = self._operations.get(operation_id)
            if operation is None:
                return
            operation.status = status
            operation.stage = status.title()
            if status == "SUCCEEDED":
                # ROS actions may complete without a final progress feedback.
                # The terminal action result is the authoritative completion signal.
                operation.progress = 1.0
            operation.result = result
            operation.detail = str(result.get("message", result.get("error_msg", "")))
            operation.admission_deadline = None
            operation.service_deadline = None
            operation.goal_handle = None
            self._audit(operation.kind, status.lower(), operation.detail or status)

    def _cancel_active(self) -> dict[str, Any]:
        canceled: list[str] = []
        non_cancelable: list[str] = []
        with self._lock:
            active = self._active_operations()
            for operation in active:
                if not operation.cancelable:
                    non_cancelable.append(operation.identifier)
                    continue
                operation.status = "CANCEL_REQUESTED"
                operation.stage = "Cancellation requested"
                canceled.append(operation.identifier)
                if operation.goal_handle is not None:
                    self._request_goal_cancel(
                        operation.identifier, operation.goal_handle
                    )
        if canceled:
            self._audit("cancel", "requested", ", ".join(canceled))
        return {
            "operation_ids": canceled,
            "non_cancelable_operation_ids": non_cancelable,
        }

    def _request_goal_cancel(self, operation_id: str, goal_handle: Any) -> None:
        try:
            future = goal_handle.cancel_goal_async()
        except Exception as error:
            self._restore_active_after_cancel_failure(operation_id, str(error))
            return
        future.add_done_callback(
            lambda response: self._on_cancel_response(operation_id, response)
        )

    def _on_cancel_response(self, operation_id: str, response: Any) -> None:
        try:
            cancel_response = response.result()
        except Exception as error:
            self._restore_active_after_cancel_failure(operation_id, str(error))
            return
        if not cancel_response.goals_canceling:
            self._restore_active_after_cancel_failure(
                operation_id, "ROS action server did not accept cancellation"
            )

    def _restore_active_after_cancel_failure(self, operation_id: str, detail: str) -> None:
        with self._lock:
            operation = self._operations.get(operation_id)
            if operation is not None and operation.status == "CANCEL_REQUESTED":
                operation.status = "ACTIVE"
                operation.stage = "Executing"
                operation.detail = detail
            elif operation is not None and operation.status == "ACTIVE":
                operation.detail = detail

    def _recover_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._active_operations():
            raise PanelCommandError("Wait for the active operation to reach a terminal state")
        if payload.get("confirmed") is not True:
            raise PanelCommandError("Manipulation-state recovery requires confirmation")
        requested_state = payload.get("requested_state")
        if requested_state not in {"empty", "holding"}:
            raise PanelCommandError("Recovery state must be empty or holding")
        if not self._recovery_client.service_is_ready():
            raise PanelCommandError("Manipulation recovery service is unavailable")
        operation = Operation(
            identifier=str(uuid4()),
            kind="recover_state",
            requested_at=time.time(),
            plan_only=None,
            cancelable=False,
            service_deadline=time.monotonic() + self.service_timeout_sec,
        )
        self._register_operation(operation)
        request = RecoverManipulationState.Request()
        request.requested_state = (
            RecoverManipulationState.Request.CONFIRM_EMPTY
            if requested_state == "empty"
            else RecoverManipulationState.Request.CONFIRM_HOLDING
        )
        try:
            future = self._recovery_client.call_async(request)
        except Exception as error:
            self._finish_operation(operation.identifier, "ERROR", {"message": str(error)})
            raise PanelCommandError(f"Failed to call manipulation recovery: {error}") from error
        future.add_done_callback(
            lambda response: self._on_recovery_result(operation.identifier, response)
        )
        self._audit("recover_state", "submitted", requested_state)
        return {"operation": operation.as_dict()}

    def _on_recovery_result(self, operation_id: str, completed: Any) -> None:
        try:
            result = completed.result()
            details = {"success": bool(result.success), "message": result.message}
            status = "SUCCEEDED" if result.success else "FAILED"
        except Exception as error:
            status = "ERROR"
            details = {"message": str(error)}
        self._finish_operation(operation_id, status, details)

    def _expire_pending_operations(self) -> None:
        now = time.monotonic()
        with self._lock:
            operations = list(self._operations.values())
        for operation in operations:
            if (
                operation.status == "SUBMITTING"
                and operation.admission_deadline is not None
                and now >= operation.admission_deadline
            ):
                with self._lock:
                    current = self._operations.get(operation.identifier)
                    if current is None or current.status != "SUBMITTING":
                        continue
                    current.status = "CANCEL_REQUESTED"
                    current.stage = "Admission timed out"
                    current.detail = (
                        "Goal admission outcome is unknown; cancellation will be sent "
                        "if a handle arrives"
                    )
                    current.admission_deadline = None
                self._audit(operation.kind, "admission_timeout", operation.identifier)
            elif (
                operation.status == "SUBMITTING"
                and operation.service_deadline is not None
                and now >= operation.service_deadline
            ):
                self._finish_operation(
                    operation.identifier,
                    "OUTCOME_UNKNOWN",
                    {"message": "Recovery service response timed out"},
                )

    def _active_operations(self) -> list[Operation]:
        with self._lock:
            return [
                operation
                for operation in self._operations.values()
                if operation.status in _ACTIVE_STATUSES
            ]

    @staticmethod
    def _optional_boolean(
        payload: dict[str, Any], field_name: str, default: bool
    ) -> bool:
        value = payload.get(field_name, default)
        if not isinstance(value, bool):
            raise PanelCommandError(f"{field_name} must be a boolean")
        return value

    def begin_shutdown(self) -> dict[str, Any]:
        with self._lock:
            self._shutting_down = True
            self._execution_unlocked_until = 0.0
        return self._cancel_active()

    def has_active_action_operations(self) -> bool:
        return any(
            operation.cancelable and operation.status in _ACTIVE_STATUSES
            for operation in self._active_operations()
        )

    def _on_manipulation_state(self, message: ManipulationState) -> None:
        with self._lock:
            self._manipulation_state = {
                "state": _MANIPULATION_STATE_NAMES.get(message.state, "UNKNOWN"),
                "detail": message.detail,
            }

    def _on_box_pose(self, message: PoseWithCovarianceStamped) -> None:
        with self._lock:
            pose = PoseStamped()
            pose.header = message.header
            pose.pose = message.pose.pose
            self._box_pose = _pose_as_dict(pose)
            self._box_pose["stamp"] = (
                message.header.stamp.sec + message.header.stamp.nanosec / 1_000_000_000
            )
            self._box_pose_received_monotonic = time.monotonic()

    def _box_pose_in_map_locked(self) -> dict[str, Any]:
        if self._box_pose is None or self._box_pose_received_monotonic is None:
            return {"available": False, "fresh": False, "detail": "Waiting for /box_pose"}

        source = self._box_pose
        update_age = time.monotonic() - self._box_pose_received_monotonic
        box_fresh = update_age <= self.box_pose_freshness_sec
        frame_id = source["frame_id"]
        if frame_id == "map":
            x = source["x"]
            y = source["y"]
            map_fresh = True
        elif frame_id == "base_link":
            map_pose = self._map_pose
            if not map_pose.get("available"):
                return {
                    "available": False,
                    "fresh": False,
                    "detail": "Waiting for map -> base_link to locate the box",
                }
            map_yaw = map_pose["yaw"]
            x = map_pose["x"] + cos(map_yaw) * source["x"] - sin(map_yaw) * source["y"]
            y = map_pose["y"] + sin(map_yaw) * source["x"] + cos(map_yaw) * source["y"]
            map_fresh = bool(map_pose.get("fresh"))
        else:
            return {
                "available": False,
                "fresh": False,
                "detail": f"Cannot display /box_pose frame {frame_id!r} on the map",
            }

        fresh = box_fresh and map_fresh
        if not box_fresh:
            detail = "/box_pose has not updated recently"
        elif not map_fresh:
            detail = "map -> base_link has not updated recently"
        else:
            detail = ""
        return {
            "available": True,
            "fresh": fresh,
            "x": x,
            "y": y,
            "z": source["z"],
            "source_frame_id": frame_id,
            "age_sec": update_age,
            "detail": detail,
        }

    def _on_odom(self, _: Odometry) -> None:
        # /odom confirms that the navigation state stream is alive. It is never
        # used for the map marker because only map -> base_link is globally aligned.
        with self._lock:
            self._odom_received_monotonic = time.monotonic()

    def _on_joint_states(self, _: JointState) -> None:
        with self._lock:
            self._joint_states_received_monotonic = time.monotonic()

    def _on_localization_confidence(self, message: Float32) -> None:
        self._on_localization_metric("confidence", float(message.data))

    def _on_localization_delay(self, message: Float32) -> None:
        self._on_localization_metric("delay_ms", float(message.data))

    def _on_localization_metric(self, name: str, value: float) -> None:
        with self._lock:
            self._localization_metrics[name] = {
                "available": True,
                "value": value,
                "received_monotonic": time.monotonic(),
                "detail": "",
            }

    def _on_nav_goal_status(self, message: GoalStatusArray) -> None:
        active_statuses = {
            GoalStatus.STATUS_ACCEPTED,
            GoalStatus.STATUS_EXECUTING,
            GoalStatus.STATUS_CANCELING,
        }
        active = any(item.status in active_statuses for item in message.status_list)
        with self._lock:
            self._nav_goal_status = {
                "available": True,
                "active": active,
                "detail": "Active navigation goal" if active else "Nav2 is idle",
            }
            self._nav_goal_status_received_monotonic = time.monotonic()

    @staticmethod
    def _stamp_nanoseconds(stamp: Any) -> int:
        return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)

    def _nav_goal_status_locked(self) -> dict[str, Any]:
        status = dict(
            getattr(
                self,
                "_nav_goal_status",
                {
                    "available": False,
                    "active": None,
                    "detail": "Waiting for Nav2 action status",
                },
            )
        )
        received = getattr(self, "_nav_goal_status_received_monotonic", None)
        if received is None:
            return status
        age = time.monotonic() - received
        status["age_sec"] = age
        if age > getattr(self, "nav_goal_status_freshness_sec", 3.0):
            status.update(
                {
                    "available": False,
                    "active": None,
                    "detail": "Nav2 action status has not updated recently",
                }
            )
        return status

    @staticmethod
    def _scan_points_in_map(
        scan: LaserScan, transform: Any, max_points: int
    ) -> list[list[float]]:
        if max_points <= 0:
            return []
        step = max(1, (len(scan.ranges) + max_points - 1) // max_points)
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        yaw = atan2(
            2.0 * (rotation.w * rotation.z + rotation.x * rotation.y),
            1.0 - 2.0 * (rotation.y * rotation.y + rotation.z * rotation.z),
        )
        cosine = cos(yaw)
        sine = sin(yaw)
        points: list[list[float]] = []
        for index in range(0, len(scan.ranges), step):
            distance = float(scan.ranges[index])
            if not isfinite(distance) or distance < scan.range_min or distance > scan.range_max:
                continue
            angle = scan.angle_min + index * scan.angle_increment
            local_x = distance * cos(angle)
            local_y = distance * sin(angle)
            points.append(
                [
                    translation.x + cosine * local_x - sine * local_y,
                    translation.y + sine * local_x + cosine * local_y,
                ]
            )
        return points

    def _on_scan(self, message: LaserScan) -> None:
        source_frame = message.header.frame_id or "base_link"
        try:
            transform = self._tf_buffer.lookup_transform(
                "map",
                source_frame,
                Time.from_msg(message.header.stamp),
                timeout=Duration(seconds=0.02),
            )
            points = self._scan_points_in_map(message, transform, self.scan_max_points)
            scan = {
                "available": True,
                "fresh": True,
                "points": points,
                "point_count": len(points),
                "source_frame_id": source_frame,
                "detail": "",
            }
        except TransformException:
            scan = {
                "available": False,
                "fresh": False,
                "points": [],
                "point_count": 0,
                "source_frame_id": source_frame,
                "detail": f"Waiting for map -> {source_frame} scan transform",
            }
        with self._lock:
            self._scan = scan
            self._scan_received_monotonic = time.monotonic()

    @staticmethod
    def _bounded_path_points(path: NavPath, max_points: int) -> list[list[float]]:
        if max_points <= 0:
            return []
        step = max(1, (len(path.poses) + max_points - 1) // max_points)
        points: list[list[float]] = []
        for pose_stamped in path.poses[::step]:
            position = pose_stamped.pose.position
            if isfinite(position.x) and isfinite(position.y):
                points.append([position.x, position.y])
        if path.poses and points:
            final_position = path.poses[-1].pose.position
            final_point = [final_position.x, final_position.y]
            if isfinite(final_position.x) and isfinite(final_position.y):
                if points[-1] != final_point:
                    if len(points) >= max_points:
                        points[-1] = final_point
                    else:
                        points.append(final_point)
        return points

    def _on_global_path(self, message: NavPath) -> None:
        frame_id = message.header.frame_id
        if not frame_id and message.poses:
            frame_id = message.poses[0].header.frame_id
        if frame_id != "map":
            path = {
                "available": False,
                "fresh": False,
                "points": [],
                "point_count": 0,
                "detail": f"Cannot display {self.global_path_topic} frame {frame_id!r}",
            }
        else:
            points = self._bounded_path_points(message, self.global_path_max_points)
            path = {
                "available": True,
                "fresh": True,
                "points": points,
                "point_count": len(points),
                "detail": "" if points else "Nav2 has no global path",
            }
        with self._lock:
            self._global_path = path
            self._global_path_received_monotonic = time.monotonic()

    def _on_diagnostics(self, message: DiagnosticArray) -> None:
        diagnostics = []
        for status in message.status:
            diagnostics.append(
                {
                    "name": status.name,
                    "level": int(status.level),
                    "message": status.message,
                    "values": {item.key: item.value for item in status.values},
                }
            )
        with self._lock:
            self._diagnostics = diagnostics[-10:]

    def _poll_navigation_lifecycle(self) -> None:
        now = time.monotonic()
        for name, client in self._nav_lifecycle_clients.items():
            if not client.service_is_ready():
                with self._lock:
                    self._nav_lifecycle_status[name] = {
                        "available": False,
                        "state": "UNKNOWN",
                        "detail": "Lifecycle service unavailable",
                    }
                continue
            with self._lock:
                requested_at = self._nav_lifecycle_requests.get(name)
                if requested_at is not None:
                    if now - requested_at < self.service_timeout_sec:
                        continue
                    self._nav_lifecycle_status[name] = {
                        "available": False,
                        "state": "UNKNOWN",
                        "detail": "Lifecycle state request timed out; retrying",
                    }
                self._nav_lifecycle_requests[name] = now
            try:
                response = client.call_async(GetState.Request())
            except Exception as error:
                with self._lock:
                    if self._nav_lifecycle_requests.get(name) == now:
                        self._nav_lifecycle_requests.pop(name, None)
                    self._nav_lifecycle_status[name] = {
                        "available": False,
                        "state": "UNKNOWN",
                        "detail": str(error),
                    }
                continue
            response.add_done_callback(
                lambda result, lifecycle_name=name, started_at=now: self._on_lifecycle_state(
                    lifecycle_name, started_at, result
                )
            )

    def _on_lifecycle_state(self, name: str, started_at: float, completed: Any) -> None:
        try:
            response = completed.result()
            state = response.current_state
            status = {
                "available": True,
                "state": state.label or str(state.id),
                "state_id": int(state.id),
                "detail": "" if state.id == 3 else "Node is not active",
            }
        except Exception as error:
            status = {"available": False, "state": "UNKNOWN", "detail": str(error)}
        with self._lock:
            if self._nav_lifecycle_requests.get(name) != started_at:
                return
            self._nav_lifecycle_requests.pop(name, None)
            self._nav_lifecycle_status[name] = status

    def _freshness_status_locked(
        self, received_monotonic: float | None, topic: str
    ) -> dict[str, Any]:
        if received_monotonic is None:
            return {"available": False, "fresh": False, "detail": f"Waiting for {topic}"}
        age = time.monotonic() - received_monotonic
        fresh = age <= self.tf_freshness_sec
        return {
            "available": True,
            "fresh": fresh,
            "age_sec": age,
            "detail": "" if fresh else f"{topic} has not updated recently",
        }

    def _localization_metrics_locked(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name, metric in self._localization_metrics.items():
            received = metric.get("received_monotonic")
            if received is None:
                result[name] = dict(metric)
                result[name]["fresh"] = False
                continue
            age = time.monotonic() - float(received)
            fresh = age <= self.tf_freshness_sec
            result[name] = {
                "available": True,
                "fresh": fresh,
                "value": metric["value"],
                "age_sec": age,
                "detail": "" if fresh else f"Localization {name} has not updated recently",
            }
        return result

    def _scan_in_map_locked(self) -> dict[str, Any]:
        if self._scan_received_monotonic is None:
            return dict(self._scan)
        age = time.monotonic() - self._scan_received_monotonic
        fresh = bool(self._scan.get("available")) and age <= self.scan_freshness_sec
        result = dict(self._scan)
        result["fresh"] = fresh
        result["age_sec"] = age
        if result.get("available") and not fresh:
            result["detail"] = f"{self.scan_topic} has not updated recently"
        return result

    def _global_path_in_map_locked(self) -> dict[str, Any]:
        if self._global_path_received_monotonic is None:
            return dict(self._global_path)
        age = time.monotonic() - self._global_path_received_monotonic
        fresh = bool(self._global_path.get("available")) and (
            age <= self.global_path_freshness_sec
        )
        result = dict(self._global_path)
        result["fresh"] = fresh
        result["age_sec"] = age
        if result.get("available") and not fresh:
            result["detail"] = f"{self.global_path_topic} has not updated recently"
            result["points"] = []
            result["point_count"] = 0
        return result

    def _initial_pose_status_locked(self) -> dict[str, Any]:
        status = dict(getattr(self, "_initial_pose_status", {
            "state": "NOT_REQUESTED",
            "detail": "No initial pose sent from this panel",
        }))
        requested = getattr(self, "_initial_pose_requested_monotonic", None)
        if status.get("state") == "PENDING" and requested is not None:
            elapsed = time.monotonic() - requested
            status["elapsed_sec"] = elapsed
            if elapsed >= self.initial_pose_settle_timeout_sec:
                status["state"] = "TIMEOUT"
                status["detail"] = "No matching post-publish map -> base_link transform before timeout"
                self._initial_pose_status = dict(status)
        return status

    def _update_initial_pose_settling_locked(
        self,
        transform_stamp_ns: int,
        x: float,
        y: float,
        yaw: float,
    ) -> None:
        status = getattr(self, "_initial_pose_status", {})
        if status.get("state") != "PENDING":
            return
        request_stamp_ns = getattr(self, "_initial_pose_request_stamp_ns", None)
        if request_stamp_ns is None or transform_stamp_ns <= request_stamp_ns:
            status["detail"] = "Waiting for a transform newer than the initial-pose publish"
            return
        position_error = hypot(x - float(status["x"]), y - float(status["y"]))
        yaw_error = abs(atan2(sin(yaw - float(status["yaw"])), cos(yaw - float(status["yaw"]))))
        if position_error > self.initial_pose_position_tolerance_m:
            status["detail"] = (
                "Waiting for map pose within "
                f"{self.initial_pose_position_tolerance_m:.2f} m of the requested initial pose"
            )
            return
        if yaw_error > self.initial_pose_yaw_tolerance_rad:
            status["detail"] = (
                "Waiting for map heading within "
                f"{self.initial_pose_yaw_tolerance_rad:.2f} rad of the requested initial pose"
            )
            return
        self._initial_pose_status = {
            "state": "SETTLED",
            "detail": "Observed a matching post-publish map -> base_link transform",
            "x": status["x"],
            "y": status["y"],
            "yaw": status["yaw"],
            "position_error_m": position_error,
            "yaw_error_rad": yaw_error,
        }

    def _poll_map_pose(self) -> None:
        try:
            transform = self._tf_buffer.lookup_transform(
                "map", "base_link", Time(), timeout=Duration(seconds=0.02)
            )
            stamp_key = (
                int(transform.header.stamp.sec),
                int(transform.header.stamp.nanosec),
            )
            source_stamp = stamp_key[0] + stamp_key[1] / 1_000_000_000
            observed_at = time.monotonic()
            translation = transform.transform.translation
            rotation = transform.transform.rotation
            yaw = atan2(
                2.0 * (rotation.w * rotation.z + rotation.x * rotation.y),
                1.0 - 2.0 * (rotation.y * rotation.y + rotation.z * rotation.z),
            )
            with self._lock:
                if stamp_key != self._last_map_transform_stamp:
                    self._last_map_transform_stamp = stamp_key
                    self._last_map_transform_update_monotonic = observed_at
                    self._update_initial_pose_settling_locked(
                        self._stamp_nanoseconds(transform.header.stamp),
                        translation.x,
                        translation.y,
                        yaw,
                    )
                last_update = self._last_map_transform_update_monotonic
            update_age = observed_at - last_update if last_update is not None else float("inf")
            source_now = self.get_clock().now().nanoseconds / 1_000_000_000
            pose = {
                "available": True,
                "fresh": update_age <= self.tf_freshness_sec,
                "x": translation.x,
                "y": translation.y,
                "yaw": yaw,
                "stamp": source_stamp,
                "age_sec": update_age,
                "source_age_sec": source_now - source_stamp,
                "detail": (
                    ""
                    if update_age <= self.tf_freshness_sec
                    else "map -> base_link has not updated recently"
                ),
            }
            self._last_tf_error = ""
        except TransformException as error:
            detail = str(error)
            if detail != self._last_tf_error:
                self.get_logger().warn(f"Cannot resolve map -> base_link: {detail}")
                self._last_tf_error = detail
            pose = {"available": False, "fresh": False, "detail": "map -> base_link unavailable"}
        with self._lock:
            self._map_pose = pose

    def _publish_status(self) -> None:
        if self._status_sink is not None:
            self._status_sink(self.snapshot())

    def _audit(self, action: str, outcome: str, detail: str) -> None:
        if self._audit_sink is not None:
            self._audit_sink(action, outcome, detail)
