"""ROS interfaces behind the operator panel's HTTP and WebSocket boundary."""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from math import atan2, cos, isfinite, sin
from pathlib import Path
from queue import Empty, Queue
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from action_msgs.msg import GoalStatus
from agibot_x2_manipulation_msgs.action import Pick, PickPlace, Place, ResetManipulation
from agibot_x2_manipulation_msgs.msg import ManipulationState
from agibot_x2_manipulation_msgs.srv import RecoverManipulationState
from ament_index_python.packages import get_package_share_directory
from diagnostic_msgs.msg import DiagnosticArray
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import Odometry
from rclpy.action import ActionClient
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.time import Time
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
        self._recovery_client = self.create_client(
            RecoverManipulationState, "/recover_manipulation_state"
        )
        state_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
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
        self.create_subscription(
            DiagnosticArray, "/pick_place/planning_diagnostics", self._on_diagnostics, 10
        )
        self.create_timer(0.05, self._drain_commands)
        self.create_timer(0.20, self._poll_map_pose)
        self.create_timer(0.20, self._expire_pending_operations)
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
        if manipulation_state not in {"EMPTY", "HOLDING"}:
            raise PanelCommandError(
                "Navigation requires a known EMPTY or HOLDING manipulation state"
            )
        if not map_pose.get("available") or not map_pose.get("fresh"):
            raise PanelCommandError("Navigation requires a fresh map -> base_link transform")
        preset_id = payload.get("preset_id")
        preset = self._presets.get(preset_id)
        if preset is None:
            raise PanelCommandError("Unknown navigation preset")
        goal = NavigateToPose.Goal()
        goal.pose = self._pose_from_values("map", preset.x, preset.y, 0.0, preset.yaw)
        operation = Operation(
            identifier=str(uuid4()),
            kind="navigate",
            requested_at=time.time(),
            preset_id=preset.identifier,
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
        self._audit("navigate", "submitted", preset.identifier)
        return operation

    def _build_manipulation_goal(
        self, kind: str, payload: dict[str, Any], plan_only: bool | None
    ) -> Any:
        if kind == "pick":
            goal = Pick.Goal()
            goal.plan_only = bool(plan_only)
            return goal
        if kind in {"place", "pick_place"}:
            pose = self._parse_place_pose(payload.get("place_pose"))
            goal = Place.Goal() if kind == "place" else PickPlace.Goal()
            goal.place_pose = pose
            goal.plan_only = bool(plan_only)
            return goal
        if kind == "reset":
            goal = ResetManipulation.Goal()
            goal.confirm_empty = True
            return goal
        raise PanelCommandError("Unsupported manipulation action")

    def _parse_place_pose(self, value: object) -> PoseStamped:
        if not isinstance(value, dict):
            raise PanelCommandError("Place pose is required")
        frame_id = value.get("frame_id")
        if frame_id not in {"base_link", "map"}:
            raise PanelCommandError("Place frame must be base_link or map")
        return self._pose_from_values(
            frame_id,
            _finite_number(value.get("x"), "place x"),
            _finite_number(value.get("y"), "place y"),
            _finite_number(value.get("z"), "place z"),
            _finite_number(value.get("yaw"), "place yaw"),
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
        return

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
            with self._lock:
                if stamp_key != self._last_map_transform_stamp:
                    self._last_map_transform_stamp = stamp_key
                    self._last_map_transform_update_monotonic = observed_at
                last_update = self._last_map_transform_update_monotonic
            update_age = observed_at - last_update if last_update is not None else float("inf")
            source_now = self.get_clock().now().nanoseconds / 1_000_000_000
            translation = transform.transform.translation
            rotation = transform.transform.rotation
            yaw = atan2(
                2.0 * (rotation.w * rotation.z + rotation.x * rotation.y),
                1.0 - 2.0 * (rotation.y * rotation.y + rotation.z * rotation.z),
            )
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
